import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../services/auth.dart';
import '../widgets/common.dart';

/// Admin-only crew management: list all players, see their crew roles, and
/// grant/revoke the Mod and Tournament Director roles behind confirmations.
class CrewManagementScreen extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;

  const CrewManagementScreen(
      {super.key, required this.auth, required this.settings});

  @override
  State<CrewManagementScreen> createState() => _CrewManagementScreenState();
}

class _CrewManagementScreenState extends State<CrewManagementScreen> {
  ApiClient get _api => ApiClient(
      baseUrl: widget.settings.baseUrl, token: widget.auth.token ?? '');

  List<CrewPlayer>? _players;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final players = await _api.getCrewPlayers();
      if (mounted) setState(() => _players = players);
    } catch (e) {
      if (!mounted) return;
      final msg = e is ApiException ? friendlyApiMessage(e) : e.toString();
      if (_players == null) {
        setState(() => _error = msg);
      } else {
        showSnack(context, 'Could not refresh: $msg', error: true);
      }
    }
  }

  Future<void> _changeRole(
      CrewPlayer player, String role, String action) async {
    try {
      await _api.setCrewRole(
          discordId: player.discordId, role: role, action: action);
      if (!mounted) return;
      showSnack(context,
          '$role ${action == 'grant' ? 'granted to' : 'revoked from'} ${player.displayName}.');
      _load();
    } catch (e) {
      if (!mounted) return;
      final msg = e is ApiException ? friendlyApiMessage(e) : e.toString();
      showSnack(context, msg, error: true);
    }
  }

  Future<void> _setLocalAdmin(CrewPlayer player, bool isAdmin) async {
    try {
      await _api.setLocalAdmin(
          playerKey: player.discordId, isAdmin: isAdmin);
      if (!mounted) return;
      showSnack(context,
          '${player.displayName} is ${isAdmin ? 'now' : 'no longer'} an admin.');
      _load();
    } catch (e) {
      if (!mounted) return;
      final msg = e is ApiException ? friendlyApiMessage(e) : e.toString();
      showSnack(context, msg, error: true);
    }
  }

  Future<void> _resetLocalPassword(CrewPlayer player) async {
    try {
      final temp = await _api.resetLocalPassword(playerKey: player.discordId);
      if (!mounted) return;
      await showDialog<void>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: const Text('Temporary password'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                  'Share this with ${player.displayName} — it is shown only once:'),
              const SizedBox(height: 12),
              SelectableText(temp,
                  style: const TextStyle(
                      fontSize: 20,
                      fontWeight: FontWeight.bold,
                      fontFamily: 'monospace')),
            ],
          ),
          actions: [
            FilledButton(
              onPressed: () => Navigator.of(ctx).pop(),
              child: const Text('Done'),
            ),
          ],
        ),
      );
    } catch (e) {
      if (!mounted) return;
      final msg = e is ApiException ? friendlyApiMessage(e) : e.toString();
      showSnack(context, msg, error: true);
    }
  }

  Future<void> _openPlayerDialog(CrewPlayer player) async {
    final isAdmin = player.hasRole('Admin');
    // Each entry: (label, disabled, destructive, run-after-confirm).
    final List<(String, bool, bool, Future<void> Function())> actions;
    if (player.isLocal) {
      actions = [
        (
          isAdmin ? 'Revoke admin' : 'Grant admin',
          false,
          isAdmin,
          () => _setLocalAdmin(player, !isAdmin),
        ),
        (
          'Reset password',
          false,
          false,
          () => _resetLocalPassword(player),
        ),
      ];
    } else {
      actions = [
        for (final spec in [
          ('Mod', 'grant', 'Grant Mod'),
          ('Mod', 'revoke', 'Revoke Mod'),
          ('Tournament Director', 'grant', 'Grant Tournament Director'),
          ('Tournament Director', 'revoke', 'Revoke Tournament Director'),
        ])
          (
            spec.$3,
            spec.$2 == 'grant'
                ? player.hasRole(spec.$1)
                : !player.hasRole(spec.$1),
            spec.$2 == 'revoke',
            () => _changeRole(player, spec.$1, spec.$2),
          ),
      ];
    }
    String detailFor(String label) {
      if (label == 'Grant admin') {
        return 'Make ${player.displayName} an admin?';
      }
      if (label == 'Revoke admin') {
        return 'Remove admin from ${player.displayName}?';
      }
      if (label == 'Reset password') {
        return 'Generate a new temporary password for ${player.displayName}? '
            'Their current password stops working immediately.';
      }
      final m = RegExp(r'^(Grant|Revoke) (.*)$').firstMatch(label);
      final verb = (m?.group(1) ?? '').toLowerCase();
      final role = m?.group(2) ?? label;
      return '$role ${verb == 'grant' ? 'for' : 'from'} ${player.displayName}?';
    }

    await showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(player.displayName),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (player.isLocal &&
                player.email != null &&
                player.email!.isNotEmpty)
              Text('Email: ${player.email}',
                  style: const TextStyle(color: Colors.grey)),
            if (player.golfplusHandle != null &&
                player.golfplusHandle!.isNotEmpty)
              Text('Golf+: ${player.golfplusHandle}',
                  style: const TextStyle(color: Colors.grey)),
            const SizedBox(height: 4),
            const Text('Choose an action — each asks for confirmation:'),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('Close'),
          ),
          for (final a in actions)
            TextButton(
              onPressed: a.$2
                  ? null
                  : () async {
                      Navigator.of(ctx).pop();
                      final confirm = await showDialog<bool>(
                        context: context,
                        builder: (c2) => AlertDialog(
                          title: Text(a.$1),
                          content: Text(detailFor(a.$1)),
                          actions: [
                            TextButton(
                              onPressed: () => Navigator.of(c2).pop(false),
                              child: const Text('Cancel'),
                            ),
                            FilledButton(
                              onPressed: () => Navigator.of(c2).pop(true),
                              style: a.$3
                                  ? FilledButton.styleFrom(
                                      backgroundColor:
                                          Colors.red.shade700)
                                  : null,
                              child: const Text('Confirm'),
                            ),
                          ],
                        ),
                      );
                      if (confirm == true && mounted) await a.$4();
                    },
              child: Text(a.$1,
                  style: TextStyle(
                      color:
                          a.$1.startsWith('Revoke') ? Colors.red.shade700 : null)),
            ),
        ],
      ),
    );
  }

  Widget _roleChips(CrewPlayer player) {
    const highlighted = {'mod', 'tournament director', 'admin'};
    final chips = player.roles
        .map((r) => r.toString())
        .where((r) => highlighted.contains(r.toLowerCase()))
        .toList();
    if (chips.isEmpty && !player.isLocal) return const SizedBox.shrink();
    return Wrap(
      spacing: 6,
      runSpacing: 4,
      children: [
        if (player.isLocal)
          const Chip(
            label: Text('local', style: TextStyle(fontSize: 12)),
            visualDensity: VisualDensity.compact,
          ),
        for (final r in chips)
          Chip(
            label: Text(r, style: const TextStyle(fontSize: 12)),
            backgroundColor: r.toLowerCase() == 'admin'
                ? Colors.amber.shade100
                : Theme.of(context).colorScheme.primaryContainer,
            visualDensity: VisualDensity.compact,
          ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Crew management')),
      body: _error != null
          ? Center(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(_error!, textAlign: TextAlign.center),
                    const SizedBox(height: 12),
                    ElevatedButton(
                        onPressed: _load, child: const Text('Retry')),
                  ],
                ),
              ),
            )
          : _players == null
              ? const Center(child: CircularProgressIndicator())
              : RefreshIndicator(
                  onRefresh: _load,
                  child: ListView.separated(
                    itemCount: _players!.length,
                    separatorBuilder: (ctx, _) => const Divider(height: 1),
                    itemBuilder: (ctx, i) {
                      final p = _players![i];
                      return ListTile(
                        title: Text(p.displayName),
                        subtitle: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            if (p.isLocal &&
                                p.email != null &&
                                p.email!.isNotEmpty)
                              Text(p.email!,
                                  style:
                                      const TextStyle(color: Colors.grey)),
                            if (p.golfplusHandle != null &&
                                p.golfplusHandle!.isNotEmpty)
                              Text('Golf+: ${p.golfplusHandle}'),
                            _roleChips(p),
                          ],
                        ),
                        trailing: const Icon(Icons.chevron_right),
                        onTap: () => _openPlayerDialog(p),
                      );
                    },
                  ),
                ),
    );
  }
}
