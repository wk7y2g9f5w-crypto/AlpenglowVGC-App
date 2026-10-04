/// Web-only helpers (URL fragment handling, install-mode detection).
library;

// Implemented in `web_support_web.dart` on web; every function is a safe
// no-op on native via `web_support_stub.dart`, so native behavior is
// completely unchanged.
import 'web_support_stub.dart'
    if (dart.library.js_interop) 'web_support_web.dart' as impl;

/// Reads `#discord_token=<token>` from the page URL fragment (set by the
/// server's /oauth/web-callback redirect). Null when absent.
String? readWebOAuthToken() => impl.readWebOAuthToken();

/// Reads `#oauth_error=<code>` from the page URL fragment. Null when absent.
String? readWebOAuthError() => impl.readWebOAuthError();

/// Removes the URL fragment (used to strip the token after storing it).
void clearUrlFragment() => impl.clearUrlFragment();

/// True when the page runs as an installed PWA (standalone display mode).
/// Always true on native (the prompt this gates never shows there).
bool get isStandaloneDisplayMode => impl.isStandaloneDisplayMode;
