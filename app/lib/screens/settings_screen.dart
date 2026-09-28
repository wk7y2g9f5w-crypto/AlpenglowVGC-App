import 'package:flutter/material.dart';

import '../services/api_client.dart';
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
          Text(
            'Notifications',
            style: Theme.of(context).textTheme.titleSmall,
          ),
          const SizedBox(height: 4),
          _NotificationPrefs(
              auth: widget.auth, settings: widget.settings),
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

/// Push-notification toggles, one per event kind. All default on server-side.
class _NotificationPrefs extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;

  const _NotificationPrefs({required this.auth, required this.settings});

  @override
  State<_NotificationPrefs> createState() => _NotificationPrefsState();
}

class _NotificationPrefsState extends State<_NotificationPrefs> {
  static const _labels = [
    ('tournament_starts', 'Tournament starts', 'When a tournament begins'),
    ('round_starts', 'Round starts', 'When each round opens for scoring'),
    ('ace', 'Hole-in-one', 'Someone aces a hole'),
    ('albatross', 'Albatross', 'Someone goes 3-under on a hole'),
    ('top3_changes', 'Top 3 changes', 'The leaderboard top 3 reorders'),
  ];

  ApiClient get _api => ApiClient(
      baseUrl: widget.settings.baseUrl, token: widget.auth.token ?? '');

  Map<String, bool>? _prefs;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final prefs = await _api.getNotificationPrefs();
      if (mounted) setState(() => _prefs = prefs);
    } catch (e) {
      if (mounted) setState(() => _error = 'Could not load preferences.');
    }
  }

  Future<void> _toggle(String key, bool value) async {
    final prev = Map<String, bool>.from(_prefs ?? {});
    setState(() => _prefs = {...prev, key: value});
    try {
      final updated = await _api.updateNotificationPrefs({key: value});
      if (mounted) setState(() => _prefs = {...prev, ...updated});
    } catch (_) {
      if (mounted) {
        setState(() => _prefs = prev);
        showSnack(context, 'Could not save — try again.');
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_error != null) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Text(_error!, style: const TextStyle(color: Colors.grey)),
      );
    }
    if (_prefs == null) {
      return const Padding(
        padding: EdgeInsets.symmetric(vertical: 12),
        child: Center(child: CircularProgressIndicator()),
      );
    }
    return Column(
      children: [
        for (final (key, title, subtitle) in _labels)
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: Text(title),
            subtitle: Text(subtitle),
            value: _prefs![key] ?? true,
            onChanged: (v) => _toggle(key, v),
          ),
      ],
    );
  }
}
