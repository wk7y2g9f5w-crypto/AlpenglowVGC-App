import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../services/auth.dart';
import '../widgets/common.dart';
import 'casual_form_screen.dart';
import 'casual_screen.dart';

/// Detail for one casual tee time: settings, player list, join/leave,
/// and creator-or-crew edit/delete.
class CasualDetailScreen extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;
  final String teeTimeId;

  const CasualDetailScreen(
      {super.key,
      required this.auth,
      required this.settings,
      required this.teeTimeId});

  @override
  State<CasualDetailScreen> createState() => _CasualDetailScreenState();
}

class _CasualDetailScreenState extends State<CasualDetailScreen> {
  late Future<_Detail> _future;

  ApiClient get _api => ApiClient(
      baseUrl: widget.settings.baseUrl, token: widget.auth.token ?? '');

  @override
  void initState() {
    super.initState();
    _future = _load();
  }

  Future<_Detail> _load() async {
    final tt = await _api.getCasualTeeTime(widget.teeTimeId);
    final me = await _api.getMe();
    return _Detail(tt: tt, me: me);
  }

  Future<void> _refresh() async {
    final f = _load();
    setState(() => _future = f);
    await f;
  }

  Future<void> _joinLeave(_Detail d, bool join) async {
    try {
      final tt = join
          ? await _api.joinCasualTeeTime(d.tt.id)
          : await _api.leaveCasualTeeTime(d.tt.id);
      if (mounted) setState(() => _future = Future.value(_Detail(tt: tt, me: d.me)));
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message, error: true);
    }
  }

  Future<void> _delete(_Detail d) async {
    final confirm = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Delete this round?'),
        content: Text('“${d.tt.label}” will be removed for everyone.'),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(ctx).pop(false),
              child: const Text('Cancel')),
          ElevatedButton(
              onPressed: () => Navigator.of(ctx).pop(true),
              child: const Text('Delete')),
        ],
      ),
    );
    if (confirm != true) return;
    try {
      await _api.deleteCasualTeeTime(d.tt.id);
      if (mounted) Navigator.of(context).pop();
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message, error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Casual Round')),
      body: AsyncBody<_Detail>(
        future: _future,
        onRefresh: _refresh,
        builder: (context, d) {
          final tt = d.tt;
          final myId = d.me.discordId;
          final inIt = tt.isIn(myId);
          final canEdit =
              tt.creatorDiscordId == myId || d.me.isCrew || d.me.isAdmin;
          return ListView(
            padding: const EdgeInsets.all(16),
            children: [
              Text(tt.label,
                  style: Theme.of(context).textTheme.headlineSmall),
              const SizedBox(height: 4),
              Text(tt.course,
                  style: Theme.of(context).textTheme.titleMedium),
              const SizedBox(height: 4),
              Text(formatCasualWhen(tt.startsAt)),
              const SizedBox(height: 8),
              Text(tt.settingsSummary,
                  style: const TextStyle(color: Colors.grey)),
              if (tt.notes.isNotEmpty) ...[
                const SizedBox(height: 12),
                Text(tt.notes),
              ],
              const SizedBox(height: 16),
              ElevatedButton.icon(
                onPressed: () => _joinLeave(d, !inIt),
                icon: Icon(inIt ? Icons.exit_to_app : Icons.add),
                label: Text(inIt ? 'Leave' : 'Join this round'),
              ),
              const SizedBox(height: 16),
              Text('Players (${tt.players.length}/${tt.maxPlayers})',
                  style: Theme.of(context).textTheme.titleSmall),
              const SizedBox(height: 8),
              for (final p in tt.players)
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: const Icon(Icons.person),
                  title: Text(p.displayName +
                      (p.discordId == tt.creatorDiscordId
                          ? ' (organizer)'
                          : '')),
                ),
              if (canEdit) ...[
                const SizedBox(height: 16),
                const Divider(),
                Row(
                  children: [
                    OutlinedButton.icon(
                      onPressed: () async {
                        final saved =
                            await Navigator.of(context).push(MaterialPageRoute(
                          builder: (_) => CasualFormScreen(
                            auth: widget.auth,
                            settings: widget.settings,
                            existing: tt,
                          ),
                        ));
                        if (saved == true) _refresh();
                      },
                      icon: const Icon(Icons.edit),
                      label: const Text('Edit'),
                    ),
                    const SizedBox(width: 12),
                    OutlinedButton.icon(
                      onPressed: () => _delete(d),
                      icon:
                          const Icon(Icons.delete, color: Colors.red),
                      label: const Text('Delete',
                          style: TextStyle(color: Colors.red)),
                    ),
                  ],
                ),
              ],
            ],
          );
        },
      ),
    );
  }
}

class _Detail {
  final CasualTeeTime tt;
  final PlayerMe me;

  _Detail({required this.tt, required this.me});
}
