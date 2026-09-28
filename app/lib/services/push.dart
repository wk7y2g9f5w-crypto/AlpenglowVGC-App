import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

import 'api_client.dart';
import 'auth.dart';

/// APNs device-token plumbing.
///
/// Flow: after Discord login, [onLogin] asks iOS for notification
/// permission once, registers with APNs, and uploads the device token to
/// the API so the server can send pushes (tournament/round starts, aces,
/// albatrosses, top-3 changes). The token is also re-uploaded on every
/// app start while logged in, since APNs tokens can rotate.
class PushService {
  static const _channel = MethodChannel('com.alpenglow.vgc.app/push');

  static bool _handlerSet = false;
  static String? _pendingToken;

  static void _ensureHandler(AuthService auth, SettingsService settings) {
    if (_handlerSet) return;
    _handlerSet = true;
    _channel.setMethodCallHandler((call) async {
      if (call.method == 'onToken') {
        final token = call.arguments as String?;
        if (token != null && token.isNotEmpty) {
          _pendingToken = token;
          await _upload(auth, settings, token);
        }
      }
      // onTokenError: nothing to do — user just won't get pushes.
    });
  }

  /// Call after a successful Discord login, and on app start when a
  /// stored session exists.
  static Future<void> onLogin(AuthService auth, SettingsService settings) async {
    if (defaultTargetPlatform != TargetPlatform.iOS) return;
    _ensureHandler(auth, settings);
    try {
      if (!settings.pushPermissionAsked) {
        final granted =
            await _channel.invokeMethod<bool>('requestPermission') ?? false;
        await settings.setPushPermissionAsked(true);
        if (!granted) return;
      } else {
        await _channel.invokeMethod('registerForPush');
      }
      // The token may already be cached natively (e.g. granted earlier).
      final cached = await _channel.invokeMethod<String>('getToken');
      if (cached != null && cached.isNotEmpty) {
        _pendingToken = cached;
        await _upload(auth, settings, cached);
      }
    } catch (_) {
      // Push is best-effort; the app works fine without it.
    }
  }

  /// Call on logout so this device stops receiving the user's pushes.
  static Future<void> onLogout(
      AuthService auth, SettingsService settings) async {
    if (defaultTargetPlatform != TargetPlatform.iOS) return;
    final token = _pendingToken;
    _pendingToken = null;
    if (token == null || token.isEmpty) return;
    try {
      final api = ApiClient(baseUrl: settings.baseUrl, token: auth.token ?? '');
      await api.unregisterDevice(token);
    } catch (_) {}
  }

  static Future<void> _upload(
      AuthService auth, SettingsService settings, String token) async {
    if (!auth.isLoggedIn) return;
    try {
      final api = ApiClient(baseUrl: settings.baseUrl, token: auth.token ?? '');
      await api.registerDevice(token);
    } catch (_) {}
  }
}
