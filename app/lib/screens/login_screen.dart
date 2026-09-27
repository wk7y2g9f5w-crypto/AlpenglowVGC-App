import 'dart:convert';
import 'dart:math';

import 'package:crypto/crypto.dart';
import 'package:flutter/material.dart';
import 'package:flutter_web_auth_2/flutter_web_auth_2.dart';
import 'package:http/http.dart' as http;

import '../services/auth.dart';
import '../widgets/common.dart';

/// Login screen: "Login with Discord" via the system browser.
///
/// Uses the OAuth2 authorization-code flow with PKCE (RFC 7636). Discord
/// requires PKCE when the redirect URI uses a custom URL scheme
/// (e.g. com.alpenglow.vgc.app://oauth-callback); the implicit flow
/// (`response_type=token`) is rejected with "not supported by client".
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
      final clientId = _clientId;

      // PKCE: generate a code verifier and its S256 challenge.
      final verifier = _generateCodeVerifier();
      final challenge = _codeChallenge(verifier);

      final authorizeUrl = Uri.https('discord.com', '/oauth2/authorize', {
        'client_id': clientId,
        'redirect_uri': redirectUri,
        'response_type': 'code',
        'scope': 'identify',
        'code_challenge': challenge,
        'code_challenge_method': 'S256',
      });

      final callbackScheme = Uri.parse(redirectUri).scheme;
      final result = await FlutterWebAuth2.authenticate(
        url: authorizeUrl.toString(),
        callbackUrlScheme: callbackScheme,
      );

      final code = Uri.parse(result).queryParameters['code'];
      if (code == null || code.isEmpty) {
        throw Exception('No authorization code in the OAuth response.');
      }

      final token = await _exchangeCodeForToken(
        code: code,
        clientId: clientId,
        redirectUri: redirectUri,
        verifier: verifier,
      );
      await widget.auth.saveToken(token);
    } catch (e) {
      if (mounted) showSnack(context, 'Login failed: $e', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// Exchanges the authorization code for an access token (PKCE, no secret).
  static Future<String> _exchangeCodeForToken({
    required String code,
    required String clientId,
    required String redirectUri,
    required String verifier,
  }) async {
    final resp = await http.post(
      Uri.https('discord.com', '/api/oauth2/token'),
      headers: {'Content-Type': 'application/x-www-form-urlencoded'},
      body: {
        'client_id': clientId,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': redirectUri,
        'code_verifier': verifier,
      },
    );
    if (resp.statusCode != 200) {
      throw Exception(
          'Token exchange failed (${resp.statusCode}): ${resp.body}');
    }
    final data = jsonDecode(resp.body) as Map<String, dynamic>;
    final token = data['access_token'] as String?;
    if (token == null || token.isEmpty) {
      throw Exception('No access token in the token response.');
    }
    return token;
  }

  /// Random 64-char verifier from the PKCE unreserved charset.
  static String _generateCodeVerifier() {
    const charset =
        'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~';
    final rnd = Random.secure();
    return List.generate(64, (_) => charset[rnd.nextInt(charset.length)])
        .join();
  }

  /// BASE64URL-ENCODE(SHA256(verifier)), without padding.
  static String _codeChallenge(String verifier) {
    final digest = sha256.convert(utf8.encode(verifier));
    return base64Url.encode(digest.bytes).replaceAll('=', '');
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
