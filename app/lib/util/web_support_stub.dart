/// Native stub for web-only helpers: everything is a safe no-op so the
/// native app behaves exactly as before.
String? readWebOAuthToken() => null;

String? readWebOAuthError() => null;

void clearUrlFragment() {}

bool get isStandaloneDisplayMode => true;
