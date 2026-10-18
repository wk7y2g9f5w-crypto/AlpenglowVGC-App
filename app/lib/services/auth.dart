import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

/// The Discord OAuth client ID for this install: the build's
/// --dart-define wins, otherwise the value from Settings. One resolver so
/// the login screen and silent session renewal can never disagree.
String discordClientId(SettingsService settings) {
  const defined = String.fromEnvironment('DISCORD_CLIENT_ID');
  return defined.isNotEmpty ? defined : settings.clientId;
}

/// Outcome of a silent session-renewal attempt.
enum RefreshOutcome {
  /// New access/refresh pair stored; callers may retry with the new token.
  refreshed,

  /// Discord rejected the refresh token (revoked, password changed):
  /// renewing is impossible, the user must log in again.
  rejected,

  /// Renewal could not be completed (server/Discord unreachable or not
  /// configured): says nothing about the session — keep it, retry later.
  unavailable,

  /// No refresh token on file (email/JWT session, or a session from
  /// before renewal existed): nothing to renew with.
  notApplicable,
}

/// Persistent, non-secret app settings (shared_preferences).
class SettingsService extends ChangeNotifier {
  static const _kBaseUrl = 'base_url';
  static const _kClientId = 'discord_client_id';
  static const _kRedirectUri = 'discord_redirect_uri';
  static const _kPushAsked = 'push_permission_asked';
  static const _kInstallPromptDismissed = 'install_prompt_dismissed';

  static const defaultBaseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://localhost:8420',
  );

  final SharedPreferences _prefs;

  SettingsService(this._prefs);

  static Future<SettingsService> load() async {
    final prefs = await SharedPreferences.getInstance();
    return SettingsService(prefs);
  }

  String get baseUrl {
    final v = (_prefs.getString(_kBaseUrl) ?? '').trim();
    return v.isEmpty ? defaultBaseUrl : v;
  }

  Future<void> setBaseUrl(String v) async {
    await _prefs.setString(_kBaseUrl, v.trim());
    notifyListeners();
  }

  String get clientId => (_prefs.getString(_kClientId) ?? '').trim();

  Future<void> setClientId(String v) async {
    await _prefs.setString(_kClientId, v.trim());
    notifyListeners();
  }

  String get redirectUri => (_prefs.getString(_kRedirectUri) ?? '').trim();

  Future<void> setRedirectUri(String v) async {
    await _prefs.setString(_kRedirectUri, v.trim());
    notifyListeners();
  }

  /// True once we've shown the iOS notification permission prompt.
  bool get pushPermissionAsked => _prefs.getBool(_kPushAsked) ?? false;

  Future<void> setPushPermissionAsked(bool v) async {
    await _prefs.setBool(_kPushAsked, v);
    notifyListeners();
  }

  /// True once the user dismissed the PWA "Add to Home Screen" hint (web).
  bool get installPromptDismissed =>
      _prefs.getBool(_kInstallPromptDismissed) ?? false;

  Future<void> setInstallPromptDismissed(bool v) async {
    await _prefs.setBool(_kInstallPromptDismissed, v);
    notifyListeners();
  }
}

/// Auth state: the API auth token, stored in the platform secure store
/// (Keychain on iOS, EncryptedSharedPreferences on Android). Either the
/// Discord OAuth access token or a JWT from the local email+password login —
/// the API accepts both as `Authorization: Bearer <token>`.
///
/// Discord sessions also store the OAuth refresh token + access-token
/// expiry. Discord access tokens die after ~a week; with the refresh
/// token kept, the session renews itself silently (see [tryRefresh]) and
/// nobody has to think about logging in again. Only when renewal is
/// *impossible* (refresh token rejected — revoked access, Discord
/// password changed — or an old session with no refresh token on file)
/// does [sessionExpired] flip: the app then shows the login screen as an
/// overlay WITHOUT tearing down the screens underneath, so a card being
/// entered is never lost.
class AuthService extends ChangeNotifier {
  static const _kToken = 'discord_access_token';
  static const _kRefresh = 'discord_refresh_token';
  static const _kExpiresAt = 'discord_token_expires_at';

  final FlutterSecureStorage _storage;
  String? _token;
  String? _refreshToken;
  DateTime? _tokenExpiresAt;

  /// True once the current session is definitively dead and renewal has
  /// failed or is impossible. The in-memory token is intentionally left
  /// in place so the widget tree (and any in-progress score entry under
  /// the login overlay) survives; the stored copies are cleared so the
  /// next app launch starts at the login screen instead.
  bool _sessionExpired = false;

  /// Latched once renewal has been rejected this app run, so repeated
  /// 401s don't hammer the refresh endpoint.
  bool _refreshRejected = false;

  Future<RefreshOutcome>? _refreshInFlight;

  AuthService(this._storage);

  String? get token => _token;
  bool get isLoggedIn => _token != null && _token!.isNotEmpty;
  bool get sessionExpired => _sessionExpired;
  bool get hasRefreshToken =>
      _refreshToken != null && _refreshToken!.isNotEmpty;
  DateTime? get tokenExpiresAt => _tokenExpiresAt;

  Future<void> load() async {
    _token = await _storage.read(key: _kToken);
    _refreshToken = await _storage.read(key: _kRefresh);
    final exp = await _storage.read(key: _kExpiresAt);
    _tokenExpiresAt =
        exp == null ? null : DateTime.fromMillisecondsSinceEpoch(
            int.tryParse(exp) ?? 0);
    notifyListeners();
  }

  /// Store a plain token (email/JWT login, or a legacy Discord login with
  /// no refresh token). Clears any Discord renewal state: a JWT session
  /// must never attempt a Discord refresh.
  Future<void> saveToken(String token) async {
    _token = token;
    _refreshToken = null;
    _tokenExpiresAt = null;
    _sessionExpired = false;
    _refreshRejected = false;
    await _storage.write(key: _kToken, value: token);
    await _storage.delete(key: _kRefresh);
    await _storage.delete(key: _kExpiresAt);
    notifyListeners();
  }

  /// Store a full Discord session: access token + refresh token + the
  /// access token's lifetime, as issued by Discord at login or renewal.
  Future<void> saveDiscordSession({
    required String accessToken,
    required String refreshToken,
    int? expiresInSeconds,
  }) async {
    _token = accessToken;
    _refreshToken = refreshToken;
    _tokenExpiresAt = expiresInSeconds == null || expiresInSeconds <= 0
        ? null
        : DateTime.now().add(Duration(seconds: expiresInSeconds));
    _sessionExpired = false;
    _refreshRejected = false;
    await _storage.write(key: _kToken, value: accessToken);
    await _storage.write(key: _kRefresh, value: refreshToken);
    if (_tokenExpiresAt == null) {
      await _storage.delete(key: _kExpiresAt);
    } else {
      await _storage.write(
          key: _kExpiresAt,
          value: _tokenExpiresAt!.millisecondsSinceEpoch.toString());
    }
    notifyListeners();
  }

  /// Silently trade the stored refresh token for a fresh token pair via
  /// the backend's POST /api/oauth/refresh. Single-flight: concurrent
  /// callers (several screens 401ing at once) share one exchange.
  Future<RefreshOutcome> tryRefresh(SettingsService settings) {
    final inFlight = _refreshInFlight;
    if (inFlight != null) return inFlight;
    final fut = _doRefresh(settings);
    _refreshInFlight = fut;
    fut.whenComplete(() {
      if (identical(_refreshInFlight, fut)) _refreshInFlight = null;
    });
    return fut;
  }

  Future<RefreshOutcome> _doRefresh(SettingsService settings) async {
    final refresh = _refreshToken;
    if (refresh == null || refresh.isEmpty) {
      return RefreshOutcome.notApplicable;
    }
    if (_refreshRejected) return RefreshOutcome.rejected;
    final clientId = discordClientId(settings);
    if (clientId.isEmpty) return RefreshOutcome.unavailable;
    try {
      final resp = await http
          .post(
            Uri.parse('${settings.baseUrl}/api/oauth/refresh'),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({
              'refresh_token': refresh,
              'client_id': clientId,
            }),
          )
          .timeout(const Duration(seconds: 20));
      if (resp.statusCode == 200) {
        final data = jsonDecode(resp.body) as Map<String, dynamic>;
        final access = data['access_token']?.toString() ?? '';
        if (access.isEmpty) return RefreshOutcome.unavailable;
        await saveDiscordSession(
          accessToken: access,
          refreshToken:
              data['refresh_token']?.toString() ?? refresh,
          expiresInSeconds: (data['expires_in'] as num?)?.toInt(),
        );
        return RefreshOutcome.refreshed;
      }
      if (resp.statusCode == 401) {
        _refreshRejected = true;
        return RefreshOutcome.rejected;
      }
      return RefreshOutcome.unavailable;
    } catch (_) {
      // Network failure, timeout, malformed body: no verdict.
      return RefreshOutcome.unavailable;
    }
  }

  /// Proactive renewal: refresh in the background when the access token
  /// is within [window] of expiring, so it never dies mid-round.
  Future<void> refreshIfExpiringSoon(
      SettingsService settings, Duration window) async {
    if (!isLoggedIn || !hasRefreshToken || _sessionExpired) return;
    final exp = _tokenExpiresAt;
    if (exp == null) return;
    if (exp.difference(DateTime.now()) > window) return;
    await tryRefresh(settings);
  }

  /// Mark the session definitively dead. Deliberately does NOT clear the
  /// in-memory token or flip [isLoggedIn] — that would swap the root
  /// widget and destroy whatever is on screen (scores being entered).
  /// The root widget listens for this and pushes the login screen as a
  /// non-dismissible overlay; everything underneath survives.
  Future<void> expireSession() async {
    if (_sessionExpired) return;
    _sessionExpired = true;
    _refreshToken = null;
    _tokenExpiresAt = null;
    await _storage.delete(key: _kToken);
    await _storage.delete(key: _kRefresh);
    await _storage.delete(key: _kExpiresAt);
    notifyListeners();
  }

  Future<void> logout() async {
    _token = null;
    _refreshToken = null;
    _tokenExpiresAt = null;
    _sessionExpired = false;
    _refreshRejected = false;
    await _storage.delete(key: _kToken);
    await _storage.delete(key: _kRefresh);
    await _storage.delete(key: _kExpiresAt);
    notifyListeners();
  }
}
