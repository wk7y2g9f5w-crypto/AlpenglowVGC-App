/// Web implementation of the helpers in `web_support.dart`.
library;

// Only ever compiled on web (conditional import). Uses `dart:js_interop`
// directly — no extra dependencies.
import 'dart:js_interop';
import 'dart:js_interop_unsafe';

/// Reads `#discord_token=<token>` from the page URL fragment.
String? readWebOAuthToken() {
  final frag = Uri.base.fragment;
  if (frag.isEmpty) return null;
  final token = Uri.splitQueryString(frag)['discord_token'];
  return (token == null || token.isEmpty) ? null : token;
}

/// Reads `#oauth_error=<code>` from the page URL fragment.
String? readWebOAuthError() {
  final frag = Uri.base.fragment;
  if (frag.isEmpty) return null;
  final err = Uri.splitQueryString(frag)['oauth_error'];
  return (err == null || err.isEmpty) ? null : err;
}

@JS('window.history.replaceState')
external void _replaceState(JSAny? data, String title, String? url);

/// Strips the fragment from the address bar (call after storing the token).
void clearUrlFragment() {
  try {
    final clean = Uri.base.replace(fragment: '').toString();
    _replaceState(null, '', clean);
  } catch (_) {
    // Non-fatal: the token was already stored.
  }
}

@JS('window.matchMedia')
external JSAny? _matchMedia(String query);

@JS('window.navigator')
external JSAny? _navigatorRaw;

bool _isTrue(JSAny? value) =>
    value.isA<JSBoolean>() && (value as JSBoolean).toDart;

/// True when installed to the home screen (standalone display mode) or when
/// iOS Safari reports standalone mode.
bool get isStandaloneDisplayMode {
  try {
    final mql = _matchMedia('(display-mode: standalone)');
    if (mql.isA<JSObject>() && _isTrue((mql as JSObject)['matches'])) {
      return true;
    }
    final nav = _navigatorRaw;
    if (nav.isA<JSObject>() && _isTrue((nav as JSObject)['standalone'])) {
      return true;
    }
  } catch (_) {
    // Fall through to false.
  }
  return false;
}
