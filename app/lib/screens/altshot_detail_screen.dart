import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../services/auth.dart';
import '../widgets/common.dart';
import 'altshot_form_screen.dart';
import 'altshot_score_screen.dart';

/// Detail for one alt-shot tee time: settings, teams, join/leave, partner
/// name, score entry, and creator-or-crew edit/delete.
class AltShotDetailScreen extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;
  final String teeTimeId;

  const AltShotDetailScreen(
      {super.key,
      required this.auth,
      required this.settings,
      required this.teeTimeId});

  @override
  State<AltShotDetailScreen> createState() => _AltShotDetailScreenState();
}

class _AltShotDetailScreenState extends State<AltShotDetailScreen> {
  late Future<_Detail> _future;

  ApiClient get _api => ApiClient(
      baseUrl: widget.settings.baseUrl, token: widget.auth.token ?? '');

  @override
  void initState() {
    super.initState();
    _future = _load();
  }

  Future<_Detail> _load() async {
    final tt = await _api.getAltShotTeeTime(widget.teeTimeId);
    final me = await _api.getMe();
    return _Detail(tt: tt, me: me);
  }

  Future<void> _refresh() async {
    final f = _load();
    setState(() => _future = f);
    await f;
  }

  Future<void> _join(_Detail d) async {
    final nameCtrl = TextEditingController();
    final p2Ctrl = TextEditingController();
    final p3Ctrl = TextEditingController();
    final p4Ctrl = TextEditingController();
    final result = await showDialog<Map<String, String>>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Join as a team'),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextField(
                controller: nameCtrl,
                decoration: const InputDecoration(
                  labelText: 'Team name (optional)',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 8),
              TextField(
                controller: p2Ctrl,
                decoration: const InputDecoration(
                  labelText: 'Player 2 name',
                  hintText: 'Teammate not in the app? Type their name',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 8),
              TextField(
                controller: p3Ctrl,
                decoration: const InputDecoration(
                  labelText: 'Player 3 name (optional)',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 8),
              TextField(
                controller: p4Ctrl,
                decoration: const InputDecoration(
                  labelText: 'Player 4 name (optional)',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 8),
              const Text(
                'Teams need 2-4 players. You can add players later too.',
                style: TextStyle(fontSize: 12, color: Colors.grey),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(ctx).pop(),
              child: const Text('Cancel')),
          ElevatedButton(
              onPressed: () => Navigator.of(ctx).pop({
                    'team_name': nameCtrl.text.trim(),
                    'player2_name': p2Ctrl.text.trim(),
                    'player3_name': p3Ctrl.text.trim(),
                    'player4_name': p4Ctrl.text.trim(),
                  }),
              child: const Text('Join')),
        ],
      ),
    );
    if (result == null) return;
    try {
      final tt = await _api.joinAltShotTeeTime(
        d.tt.id,
        teamName: result['team_name'] ?? '',
        player2Name: result['player2_name'] ?? '',
        player3Name: result['player3_name'] ?? '',
        player4Name: result['player4_name'] ?? '',
      );
      if (mounted) {
        setState(() => _future = Future.value(_Detail(tt: tt, me: d.me)));
      }
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message, error: true);
    }
  }

  Future<void> _leave(_Detail d) async {
    try {
      final tt = await _api.leaveAltShotTeeTime(d.tt.id);
      if (mounted) {
        setState(() => _future = Future.value(_Detail(tt: tt, me: d.me)));
      }
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message, error: true);
    }
  }

  Future<void> _editTeam(_Detail d, AltShotTeam team) async {
    String named(int slot) {
      final names = team.playerNames;
      return names.length > slot ? names[slot] : '';
    }

    final nameCtrl = TextEditingController(text: team.teamName);
    final p2Ctrl = TextEditingController(text: named(1));
    final p3Ctrl = TextEditingController(text: named(2));
    final p4Ctrl = TextEditingController(text: named(3));
    final result = await showDialog<Map<String, String>>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Edit team'),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextField(
                controller: nameCtrl,
                decoration: const InputDecoration(
                  labelText: 'Team name (optional)',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 8),
              TextField(
                controller: p2Ctrl,
                decoration: const InputDecoration(
                  labelText: 'Player 2 name',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 8),
              TextField(
                controller: p3Ctrl,
                decoration: const InputDecoration(
                  labelText: 'Player 3 name (optional)',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 8),
              TextField(
                controller: p4Ctrl,
                decoration: const InputDecoration(
                  labelText: 'Player 4 name (optional)',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 8),
              const Text(
                'Teams need 2-4 players to submit a record.',
                style: TextStyle(fontSize: 12, color: Colors.grey),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(ctx).pop(),
              child: const Text('Cancel')),
          ElevatedButton(
              onPressed: () => Navigator.of(ctx).pop({
                    'team_name': nameCtrl.text.trim(),
                    'player2_name': p2Ctrl.text.trim(),
                    'player3_name': p3Ctrl.text.trim(),
                    'player4_name': p4Ctrl.text.trim(),
                  }),
              child: const Text('Save')),
        ],
      ),
    );
    if (result == null) return;
    try {
      await _api.updateAltShotTeam(d.tt.id, team.id, result);
      if (mounted) _refresh();
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message, error: true);
    }
  }

  Future<void> _deleteScore(_Detail d, AltShotTeam team) async {
    final confirm = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Delete this score?'),
        content:
            Text('“${team.displayName}” will be removed from the records.'),
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
      await _api.deleteAltShotScore(d.tt.id, team.id);
      if (mounted) _refresh();
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
      await _api.deleteAltShotTeeTime(d.tt.id);
      if (mounted) Navigator.of(context).pop();
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message, error: true);
    }
  }

  String _scoreLine(AltShotTeeTime tt, AltShotScore s) {
    if (tt.pars.length == 18) {
      final toPar = s.total - tt.pars.reduce((a, b) => a + b);
      if (toPar == 0) return '${s.total} (E)';
      return '${s.total} (${toPar > 0 ? '+' : ''}$toPar)';
    }
    return '${s.total}';
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Alt-Shot Round')),
      body: AsyncBody<_Detail>(
        future: _future,
        onRefresh: _refresh,
        builder: (context, d) {
          final tt = d.tt;
          final myId = d.me.discordId;
          final myTeam = tt.myTeam(myId);
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
              Text(formatTeeTimeWhen(tt.startsAt)),
              const SizedBox(height: 8),
              Text(tt.settingsSummary,
                  style: const TextStyle(color: Colors.grey)),
              if (tt.notes.isNotEmpty) ...[
                const SizedBox(height: 12),
                Text(tt.notes),
              ],
              const SizedBox(height: 16),
              if (myTeam == null)
                ElevatedButton.icon(
                  onPressed: tt.isFull ? null : () => _join(d),
                  icon: const Icon(Icons.group_add),
                  label: Text(tt.isFull
                      ? 'Both team slots are full'
                      : 'Join with a team'),
                )
              else
                ElevatedButton.icon(
                  onPressed: () => _leave(d),
                  icon: const Icon(Icons.exit_to_app),
                  label: const Text('Leave (removes my team)'),
                ),
              const SizedBox(height: 16),
              Text('Teams (${tt.teams.length}/${tt.maxTeams})',
                  style: Theme.of(context).textTheme.titleSmall),
              const SizedBox(height: 8),
              for (final team in tt.teams)
                Card(
                  margin: const EdgeInsets.only(bottom: 8),
                  child: Padding(
                    padding: const EdgeInsets.all(12),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            const Icon(Icons.groups, size: 20),
                            const SizedBox(width: 8),
                            Expanded(
                              child: Column(
                                crossAxisAlignment:
                                    CrossAxisAlignment.start,
                                children: [
                                  Text(team.displayName,
                                      style: const TextStyle(
                                          fontWeight: FontWeight.bold)),
                                  if (team.teamName.isNotEmpty)
                                    Text(team.playersLine,
                                        style: const TextStyle(
                                            fontSize: 12,
                                            color: Colors.grey)),
                                ],
                              ),
                            ),
                            if (team.player1DiscordId ==
                                tt.creatorDiscordId)
                              const Chip(
                                label: Text('Organizer',
                                    style: TextStyle(fontSize: 11)),
                                visualDensity: VisualDensity.compact,
                              ),
                          ],
                        ),
                        if (!team.canSubmit)
                          Padding(
                            padding: const EdgeInsets.only(top: 4),
                            child: Text(
                              'Add at least one more player to submit a record.',
                              style: TextStyle(
                                  fontSize: 12,
                                  color: Colors.orange.shade800),
                            ),
                          ),
                        if (team.score != null)
                          Padding(
                            padding: const EdgeInsets.only(top: 8),
                            child: Row(
                              children: [
                                const Icon(Icons.emoji_events,
                                    size: 18, color: Colors.amber),
                                const SizedBox(width: 6),
                                Text(_scoreLine(tt, team.score!),
                                    style: const TextStyle(
                                        fontWeight: FontWeight.bold,
                                        fontSize: 16)),
                              ],
                            ),
                          ),
                        const SizedBox(height: 8),
                        Wrap(
                          spacing: 8,
                          children: [
                            if (team.player1DiscordId == myId ||
                                d.me.isCrew ||
                                d.me.isAdmin)
                              OutlinedButton.icon(
                                icon: const Icon(Icons.group, size: 16),
                                label: const Text('Edit team'),
                                onPressed: () => _editTeam(d, team),
                              ),
                            if (team.player1DiscordId == myId ||
                                d.me.isCrew ||
                                d.me.isAdmin) ...[
                              OutlinedButton.icon(
                                icon: Icon(
                                    team.score == null
                                        ? Icons.scoreboard
                                        : Icons.edit,
                                    size: 16),
                                label: Text(team.score == null
                                    ? 'Enter score'
                                    : 'Edit score'),
                                onPressed: team.canSubmit
                                    ? () async {
                                        final saved =
                                            await Navigator.of(context).push(
                                                MaterialPageRoute(
                                          builder: (_) => AltShotScoreScreen(
                                            auth: widget.auth,
                                            settings: widget.settings,
                                            teeTime: tt,
                                            team: team,
                                          ),
                                        ));
                                        if (saved == true) _refresh();
                                      }
                                    : null,
                              ),
                              if (team.score != null)
                                OutlinedButton.icon(
                                  icon: const Icon(Icons.delete,
                                      size: 16, color: Colors.red),
                                  label: const Text('Delete score',
                                      style: TextStyle(color: Colors.red)),
                                  onPressed: () => _deleteScore(d, team),
                                ),
                            ],
                          ],
                        ),
                      ],
                    ),
                  ),
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
                          builder: (_) => AltShotFormScreen(
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
  final AltShotTeeTime tt;
  final PlayerMe me;

  _Detail({required this.tt, required this.me});
}
