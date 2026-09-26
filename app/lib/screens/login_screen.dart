import 'package:flutter/material.dart';
import 'package:flutter_web_auth_2/flutter_web_auth_2.dart';

import '../services/auth.dart';
import '../widgets/common.dart';

/// Login screen: "Login with Discord" via the system browser (implicit flow).
///
/// The OAuth client ID and redirect URI come ONLY from --dart-define
/// (`DISCORD_CLIENT_ID` / `DISCORD_REDIRECT_URI`) or the Settings screen —
/// never hardcoded. If neither is set, a setup hint is shown.
class LoginScreen extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;

  const LoginScreen({super.key, required this.auth, required this.settings});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  bool _busy = false;

  String get _clientId {
    const defined = String.fromEnvironment('DISCORD_CLIENT_ID');
    return defined.isNotEmpty ? defined : widget.settings.clientId;
  }

  String get _redirectUri {
    const defined = String.fromEnvironment('DISCORD_REDIRECT_URI');
    return defined.isNotEmpty ? defined : widget.settings.redirectUri;
  }

  bool get _configured => _clientId.isNotEmpty && _redirectUri.isNotEmpty;

  Future<void> _login() async {
    setState(() => _busy = true);
    try {
      final redirectUri = _redirectUri;
      final authorizeUrl = Uri.https('discord.com', '/oauth2/authorize', {
        'client_id': _clientId,
        'redirect_uri': redirectUri,
        'response_type': 'token',
        'scope': 'identify',
      });

      final callbackScheme = Uri.parse(redirectUri).scheme;
      final result = await FlutterWebAuth2.authenticate(
        url: authorizeUrl.toString(),
        callbackUrlScheme: callbackScheme,
      );

      final token = _extractAccessToken(result);
      if (token == null || token.isEmpty) {
        throw Exception('No access token in the OAuth response.');
      }
      await widget.auth.saveToken(token);
    } catch (e) {
      if (mounted) showSnack(context, 'Login failed: $e', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// The implicit flow returns the token in the URL fragment, e.g.
  /// `<redirect-uri>#access_token=...&token_type=Bearer&expires_in=...`
  static String? _extractAccessToken(String resultUrl) {
    final uri = Uri.parse(resultUrl);
    final params = Uri.splitQueryString(uri.fragment);
    return params['access_token'];
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: Padding(
            padding: const EdgeInsets.all(32),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Icon(Icons.sports_golf, size: 72, color: Colors.green),
                const SizedBox(height: 16),
                const Text(
                  'Alpenglow VGC',
                  style: TextStyle(fontSize: 28, fontWeight: FontWeight.bold),
                ),
                const SizedBox(height: 8),
                const Text(
                  'Companion app for the crew\'s golf community',
                  textAlign: TextAlign.center,
                  style: TextStyle(color: Colors.grey),
                ),
                const SizedBox(height: 32),
                if (_configured)
                  SizedBox(
                    width: double.infinity,
                    child: ElevatedButton.icon(
                      onPressed: _busy ? null : _login,
                      icon: _busy
                          ? const SizedBox(
                              width: 18,
                              height: 18,
                              child:
                                  CircularProgressIndicator(strokeWidth: 2),
                            )
                          : const Icon(Icons.login),
                      label: const Text('Login with Discord'),
                      style: ElevatedButton.styleFrom(
                        padding: const EdgeInsets.symmetric(vertical: 14),
                      ),
                    ),
                  )
                else
                  _setupHint(),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _setupHint() {
    return Card(
      color: Colors.orange.shade50,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Row(
              children: [
                Icon(Icons.settings, color: Colors.orange),
                SizedBox(width: 8),
                Text('Setup needed',
                    style: TextStyle(fontWeight: FontWeight.bold)),
              ],
            ),
            const SizedBox(height: 8),
            const Text(
              'Discord login isn\'t configured yet. Provide the OAuth client ID '
              'and redirect URI via --dart-define:\n\n'
              'DISCORD_CLIENT_ID, DISCORD_REDIRECT_URI\n\n'
              'or enter them below.',
              style: TextStyle(fontSize: 13),
            ),
            const SizedBox(height: 12),
            OutlinedButton.icon(
              onPressed: () {
                // Opens settings in "setup mode" even without a token.
                Navigator.of(context).push(MaterialPageRoute(
                  builder: (_) => _SetupSettingsPage(
                      settings: widget.settings,
                      onDone: () => setState(() {})),
                ));
              },
              icon: const Icon(Icons.settings),
              label: const Text('Open Settings'),
            ),
          ],
        ),
      ),
    );
  }
}

/// Minimal settings access from the login screen (no token required).
class _SetupSettingsPage extends StatefulWidget {
  final SettingsService settings;
  final VoidCallback onDone;

  const _SetupSettingsPage({required this.settings, required this.onDone});

  @override
  State<_SetupSettingsPage> createState() => _SetupSettingsPageState();
}

class _SetupSettingsPageState extends State<_SetupSettingsPage> {
  late final TextEditingController _baseUrl;
  late final TextEditingController _clientId;
  late final TextEditingController _redirectUri;

  @override
  void initState() {
    super.initState();
    _baseUrl = TextEditingController(text: widget.settings.baseUrl);
    _clientId = TextEditingController(text: widget.settings.clientId);
    _redirectUri = TextEditingController(text: widget.settings.redirectUri);
  }

  @override
  void dispose() {
    _baseUrl.dispose();
    _clientId.dispose();
    _redirectUri.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('App settings')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          TextField(
            controller: _baseUrl,
            decoration: const InputDecoration(
              labelText: 'API base URL',
              hintText: 'http://localhost:8420',
              border: OutlineInputBorder(),
            ),
            keyboardType: TextInputType.url,
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _clientId,
            decoration: const InputDecoration(
              labelText: 'Discord client ID',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _redirectUri,
            decoration: const InputDecoration(
              labelText: 'Discord redirect URI',
              hintText: 'e.g. myapp://oauth-callback',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 20),
          ElevatedButton(
            onPressed: () async {
              await widget.settings.setBaseUrl(_baseUrl.text);
              await widget.settings.setClientId(_clientId.text);
              await widget.settings.setRedirectUri(_redirectUri.text);
              widget.onDone();
              if (context.mounted) Navigator.of(context).pop();
            },
            child: const Text('Save'),
          ),
        ],
      ),
    );
  }
}
