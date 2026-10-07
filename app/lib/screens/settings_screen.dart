import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../services/api_client.dart';
import '../services/auth.dart';
import '../widgets/common.dart';
import 'archive_screen.dart';
import 'crew_management_screen.dart';

/// Settings: admin-only connection config (API base URL, Discord OAuth),
/// notification prefs, privacy/support links, logout.
/// Non-admins never see the Discord OAuth fields or the crew management row.
///
/// Also hosts the privacy-policy link, the admin-only crew management entry,
/// and the self-service delete-account flow.
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
  bool _isAdmin = false;

  ApiClient get _api => ApiClient(
      baseUrl: widget.settings.baseUrl, token: widget.auth.token ?? '');

  @override
  void initState() {
    super.initState();
    _baseUrl = TextEditingController(text: widget.settings.baseUrl);
    _clientId = TextEditingController(text: widget.settings.clientId);
    _redirectUri = TextEditingController(text: widget.settings.redirectUri);
    _loadAdminFlag();
  }

  @override
  void dispose() {
    _baseUrl.dispose();
    _clientId.dispose();
    _redirectUri.dispose();
    super.dispose();
  }

  /// Open the privacy policy in the external browser (App Store requirement).
  Future<void> _openPrivacyPolicy() async {
    const privacyUrl = 'https://alpenglow-vgc.onrender.com/privacy';
    try {
      final ok = await launchUrl(Uri.parse(privacyUrl),
          mode: LaunchMode.externalApplication);
      if (!ok && mounted) {
        showSnack(context, 'Could not open the privacy policy.',
            error: true);
      }
    } catch (_) {
      if (mounted) {
        showSnack(context, 'Could not open the privacy policy.',
            error: true);
      }
    }
  }

  Future<void> _openSupport() async {
    const supportUrl = 'https://alpenglow-vgc.onrender.com/support';
    try {
      final ok = await launchUrl(Uri.parse(supportUrl),
          mode: LaunchMode.externalApplication);
      if (!ok && mounted) {
        showSnack(context, 'Could not open the support page.',
            error: true);
      }
    } catch (_) {
      if (mounted) {
        showSnack(context, 'Could not open the support page.',
            error: true);
      }
    }
  }

  /// Two-step delete-account flow: the user must type "delete" before the red
  /// Delete button enables (copied from the End-season dialog in
  /// new_season_screen.dart). On success the server-side record is removed
  /// and the user is logged out locally.
  Future<void> _deleteAccount() async {
    final controller = TextEditingController();
    try {
      final confirm = await showDialog<bool>(
        context: context,
        builder: (ctx) => _DeleteAccountDialog(controller: controller),
      );
      if (confirm != true || !mounted) return;
      try {
        await _api.deleteAccount();
        await widget.auth.logout();
        if (mounted) {
          showSnack(context,
              'Your account and data have been deleted. Sorry to see you go.');
        }
      } on ApiException catch (e) {
        if (e.statusCode == 401 || e.statusCode == 404) {
          // Account already gone or session expired — log out locally anyway.
          await widget.auth.logout();
          if (mounted) {
            showSnack(context,
                'Account already deleted or session expired. Logged out.');
          }
        } else if (mounted) {
          showSnack(context, friendlyApiMessage(e), error: true);
        }
      }
    } finally {
      controller.dispose();
    }
  }

  /// Admin gating: fetches /api/players/me and remembers is_admin, the same
  /// pattern as tournament_detail_screen.dart. Stays non-admin on failure.
  Future<void> _loadAdminFlag() async {
    try {
      final me = await _api.getMe();
      if (mounted) setState(() => _isAdmin = me.isAdmin);
    } catch (_) {
      // Leave the admin rows hidden.
    }
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
          // Connection settings are admin-only: non-admins don't even see
          // them.
          if (_isAdmin) ...[
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
          ],
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
          ListTile(
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.privacy_tip),
            title: const Text('Privacy Policy'),
            trailing: const Icon(Icons.open_in_new),
            onTap: _openPrivacyPolicy,
          ),
          ListTile(
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.help_outline),
            title: const Text('Support'),
            trailing: const Icon(Icons.open_in_new),
            onTap: _openSupport,
          ),
          if (_isAdmin)
            ListTile(
              contentPadding: EdgeInsets.zero,
              leading: const Icon(Icons.admin_panel_settings),
              title: const Text('Crew management'),
              subtitle: const Text('Grant or revoke crew roles'),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => Navigator.of(context).push(
                MaterialPageRoute(
                  builder: (_) => CrewManagementScreen(
                    auth: widget.auth,
                    settings: widget.settings,
                  ),
                ),
              ),
            ),
          if (_isAdmin)
            ListTile(
              contentPadding: EdgeInsets.zero,
              leading: const Icon(Icons.archive),
              title: const Text('Archive'),
              subtitle: const Text('Completed tee times — solo and group'),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => Navigator.of(context).push(
                MaterialPageRoute(
                  builder: (_) => ArchiveScreen(
                    auth: widget.auth,
                    settings: widget.settings,
                  ),
                ),
              ),
            ),
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
          const SizedBox(height: 12),
          OutlinedButton.icon(
            onPressed: _deleteAccount,
            icon: const Icon(Icons.delete_forever, color: Colors.red),
            label: const Text('Delete account',
                style: TextStyle(color: Colors.red)),
            style: OutlinedButton.styleFrom(
              side: BorderSide(color: Colors.red.shade700),
            ),
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

/// Two-step "Delete account" confirmation: the user must type "delete"
/// (case-insensitive, trimmed) before the red Delete button enables.
/// Mirrors the End-season dialog in new_season_screen.dart.
class _DeleteAccountDialog extends StatefulWidget {
  final TextEditingController controller;

  const _DeleteAccountDialog({required this.controller});

  @override
  State<_DeleteAccountDialog> createState() => _DeleteAccountDialogState();
}

class _DeleteAccountDialogState extends State<_DeleteAccountDialog> {
  bool get _matches =>
      widget.controller.text.trim().toLowerCase() == 'delete';

  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_onChanged);
  }

  @override
  void dispose() {
    widget.controller.removeListener(_onChanged);
    super.dispose();
  }

  void _onChanged() => setState(() {});

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Delete account?'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'This permanently deletes your Alpenglow VGC player profile and '
            'all of your app data. This cannot be undone.',
          ),
          const SizedBox(height: 16),
          const Text(
            'Type "delete" to confirm:',
            style: TextStyle(fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 8),
          TextField(
            controller: widget.controller,
            autocorrect: false,
            decoration: const InputDecoration(
              hintText: 'delete',
              border: OutlineInputBorder(),
            ),
          ),
        ],
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(false),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: _matches ? () => Navigator.of(context).pop(true) : null,
          style:
              FilledButton.styleFrom(backgroundColor: Colors.red.shade700),
          child: const Text('Delete'),
        ),
      ],
    );
  }
}
