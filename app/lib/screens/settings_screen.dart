import 'package:flutter/material.dart';

import '../services/auth.dart';
import '../widgets/common.dart';

/// Settings: API base URL, Discord OAuth config, logout.
class SettingsScreen extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;

  const SettingsScreen(
      {super.key, required this.auth, required this.settings});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  late final TextEditingController _baseUrl;
  late final TextEditingController _clientId;
  late final TextEditingController _redirectUri;
  bool _saving = false;

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

  Future<void> _save() async {
    setState(() => _saving = true);
    try {
      await widget.settings.setBaseUrl(_baseUrl.text);
      await widget.settings.setClientId(_clientId.text);
      await widget.settings.setRedirectUri(_redirectUri.text);
      if (mounted) showSnack(context, 'Settings saved.');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    const definedClientId = String.fromEnvironment('DISCORD_CLIENT_ID');
    const definedRedirect = String.fromEnvironment('DISCORD_REDIRECT_URI');
    final usesDefines =
        definedClientId.isNotEmpty || definedRedirect.isNotEmpty;

    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          TextField(
            controller: _baseUrl,
            decoration: const InputDecoration(
              labelText: 'API base URL',
              hintText: 'http://localhost:8420',
              border: OutlineInputBorder(),
              helperText: 'Where the Alpenglow VGC backend lives.',
            ),
            keyboardType: TextInputType.url,
            autocorrect: false,
          ),
          const SizedBox(height: 16),
          Text(
            'Discord login',
            style: Theme.of(context).textTheme.titleSmall,
          ),
          if (usesDefines)
            const Padding(
              padding: EdgeInsets.only(top: 4, bottom: 8),
              child: Text(
                'OAuth values were provided via --dart-define and take '
                'precedence over the fields below.',
                style: TextStyle(color: Colors.grey, fontSize: 12),
              ),
            ),
          const SizedBox(height: 8),
          TextField(
            controller: _clientId,
            decoration: const InputDecoration(
              labelText: 'Discord client ID',
              border: OutlineInputBorder(),
            ),
            autocorrect: false,
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _redirectUri,
            decoration: const InputDecoration(
              labelText: 'Discord redirect URI',
              hintText: 'e.g. com.alpenglowvgc.app://oauth-callback',
              border: OutlineInputBorder(),
              helperText:
                  'Must be registered in the Discord developer portal.',
            ),
            autocorrect: false,
          ),
          const SizedBox(height: 16),
          ElevatedButton(
            onPressed: _saving ? null : _save,
            child: _saving
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2))
                : const Text('Save'),
          ),
          const SizedBox(height: 32),
          const Divider(),
          const SizedBox(height: 8),
          OutlinedButton.icon(
            onPressed: () async {
              final confirm = await showDialog<bool>(
                context: context,
                builder: (ctx) => AlertDialog(
                  title: const Text('Log out?'),
                  content: const Text(
                      'Your Discord token will be removed from this device.'),
                  actions: [
                    TextButton(
                        onPressed: () => Navigator.of(ctx).pop(false),
                        child: const Text('Cancel')),
                    ElevatedButton(
                        onPressed: () => Navigator.of(ctx).pop(true),
                        child: const Text('Log out')),
                  ],
                ),
              );
              if (confirm == true) await widget.auth.logout();
            },
            icon: const Icon(Icons.logout, color: Colors.red),
            label:
                const Text('Log out', style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
  }
}
