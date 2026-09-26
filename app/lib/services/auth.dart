import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Persistent, non-secret app settings (shared_preferences).
class SettingsService extends ChangeNotifier {
  static const _kBaseUrl = 'base_url';
  static const _kClientId = 'discord_client_id';
  static const _kRedirectUri = 'discord_redirect_uri';

  static const defaultBaseUrl = 'http://localhost:8420';

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
}

/// Auth state: Discord OAuth access token, stored in the platform secure
/// store (Keychain on iOS, EncryptedSharedPreferences on Android).
class AuthService extends ChangeNotifier {
  static const _kToken = 'discord_access_token';

  final FlutterSecureStorage _storage;
  String? _token;

  AuthService(this._storage);

  String? get token => _token;
  bool get isLoggedIn => _token != null && _token!.isNotEmpty;

  Future<void> load() async {
    _token = await _storage.read(key: _kToken);
    notifyListeners();
  }

  Future<void> saveToken(String token) async {
    _token = token;
    await _storage.write(key: _kToken, value: token);
    notifyListeners();
  }

  Future<void> logout() async {
    _token = null;
    await _storage.delete(key: _kToken);
    notifyListeners();
  }
}
