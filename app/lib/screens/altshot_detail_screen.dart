import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../services/auth.dart';
import '../widgets/common.dart';
import 'altshot_form_screen.dart';
import 'altshot_score_screen.dart';

/// Detail for one alt-shot tee time: settings, teams, join/leave, roster
/// editing, score entry, and creator-or-crew edit/delete.
///
/// Two modes: fixed-roster 1-team tee times (players join the single team
/// until it hits the chosen size) and flexible 2-team tee times (joining
/// creates your own team of 2-4 players).
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
    // Fixed roster: one tap joins the team — no dialog needed.
    if (d.tt.isFixedRoster) {
      try {
        final tt = await _api.joinAltShotTeeTime(d.tt.id);
        if (mounted) {
          setState(() => _future = Future.value(_Detail(tt: tt, me: d.me)));
        }
      } on ApiException catch (e) {
        if (mounted) showSnack(context, e.message, error: true);
      }
      return;
    }
    // Flexible: joining starts your own team.
    final nameCtrl = TextEditingController();
    final extraCtrls =
        List.generate(3, (_) => TextEditingController());
    final result = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Join with a team'),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextField(
                controller: nameCtrl,
                decoration: const InputDecoration(
                  labelText: 'Team name (optional)',
                  helperText: 'Leave blank and Golf+ usernames will show'
                      ' on the leaderboard.',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 8),
              for (var i = 0; i < extraCtrls.length; i++) ...[
                TextField(
                  controller: extraCtrls[i],
                  decoration: InputDecoration(
                    labelText:
                        i == 0 ? 'Player 2 name' : 'Player ${i + 2} name (optional)',
                    hintText: i == 0
                        ? 'Teammate not in the app? Type their name'
                        : null,
                    border: const OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 8),
              ],
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
                    'extra_names': extraCtrls
                        .map((c) => c.text.trim())
                        .where((s) => s.isNotEmpty)
                        .toList(),
                  }),
              child: const Text('Join')),
        ],
      ),
    );
    if (result == null) return;
    try {
      final tt = await _api.joinAltShotTeeTime(
        d.tt.id,
        teamName: (result['team_name'] as String?) ?? '',
        extraNames: (result['extra_names'] as List?)?.cast<String>() ?? [],
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
    // Registered members are fixed; only the team name and the text-name
    // players (teammates not in the app) are editable here.
    final registeredCount = team.memberDiscordIds.length;
    final cap = d.tt.isFixedRoster ? (d.tt.teamSize ?? 4) : 4;
    final maxExtras = (cap - registeredCount).clamp(0, 4);
    final nameCtrl = TextEditingController(text: team.teamName);
    // Text-name players = roster names beyond the registered members.
    final currentExtras =
        team.playerNames.sublist(registeredCount.clamp(0, team.playerNames.length));
    final extraCtrls = currentExtras
        .map((n) => TextEditingController(text: n))
        .toList();
    while (extraCtrls.length < maxExtras) {
      extraCtrls.add(TextEditingController());
    }

    final result = await showDialog<Map<String, dynamic>>(
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
                    helperText: 'Leave blank and Golf+ usernames will show'
                        ' on the leaderboard.',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                if (registeredCount > 0)
                  Align(
                    alignment: Alignment.centerLeft,
                    child: Wrap(
                      spacing: 6,
                      children: [
                        for (var i = 0;
                            i < registeredCount &&
                                i < team.playerNames.length;
                            i++)
                          Chip(
                            label: Text(team.playerNames[i],
                                style: const TextStyle(fontSize: 12)),
                            visualDensity: VisualDensity.compact,
                          ),
                      ],
                    ),
                  ),
                if (registeredCount > 0) const SizedBox(height: 8),
                for (var i = 0; i < extraCtrls.length; i++) ...[
                  TextField(
                    controller: extraCtrls[i],
                    decoration: InputDecoration(
                      labelText: 'Player name ${i + 1} (optional)',
                      hintText: 'Teammate not in the app',
                      border: const OutlineInputBorder(),
                    ),
                  ),
                  const SizedBox(height: 8),
                ],
                Text(
                  d.tt.isFixedRoster
                      ? 'Roster is capped at ${d.tt.teamSize} players.'
                      : 'Teams need 2-4 players to submit a record.',
                  style: const TextStyle(fontSize: 12, color: Colors.grey),
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
                      'extra_names': extraCtrls
                          .map((c) => c.text.trim())
                          .where((s) => s.isNotEmpty)
                          .toList(),
                    }),
                child: const Text('Save')),
          ],
        ),
      );
    if (result == null) return;
    try {
      await _api.updateAltShotTeam(d.tt.id, team.id, {
        'team_name': (result['team_name'] as String?) ?? '',
        'extra_names': (result['extra_names'] as List?) ?? [],
      });
      if (mounted) _refresh();
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message, error: true);
    }
  }

  Future<void> _deleteScore(_Detail d, AltShotTeam team) async {
    final confirm = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Delete score?'),
        content: const Text(
            'This removes the submitted score from the records.'),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(ctx).pop(false),
              child: const Text('Cancel')),
          ElevatedButton(
              onPressed: () => Navigator.of(ctx).pop(true),
              style: ElevatedButton.styleFrom(
                  backgroundColor: Colors.red,
                  foregroundColor: Colors.white),
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
        title: const Text('Delete tee time?'),
        content:
            const Text('This removes the tee time and all its teams.'),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(ctx).pop(false),
              child: const Text('Cancel')),
          ElevatedButton(
              onPressed: () => Navigator.of(ctx).pop(true),
              style: ElevatedButton.styleFrom(
                  backgroundColor: Colors.red,
                  foregroundColor: Colors.white),
              child: const Text('Delete')),
        ],
      ),
    );
    if (confirm != true) return;
    try {
      await _api.deleteAltShotTeeTime(d.tt.id);
      if (mounted) Navigator.of(context).pop(true);
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
          final fixed = tt.isFixedRoster;
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
              Text(
                  tt.settingsSummary +
                      (fixed ? ' · ${tt.teamSize}-player team' : ''),
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
                      ? (fixed
                          ? 'Team is full (${tt.teamSize}/${tt.teamSize})'
                          : 'Both team slots are full')
                      : (fixed ? 'Join team' : 'Join with a team')),
                )
              else
                ElevatedButton.icon(
                  onPressed: () => _leave(d),
                  icon: const Icon(Icons.exit_to_app),
                  label: Text(fixed ? 'Leave team' : 'Leave (removes my team)'),
                ),
              const SizedBox(height: 16),
              Text(
                  fixed
                      ? 'Team (${tt.teams.isEmpty ? 0 : tt.teams.first.teamSize}/${tt.teamSize})'
                      : 'Teams (${tt.teams.length}/${tt.maxTeams})',
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
                            if (team.isMember(myId))
                              const Chip(
                                label: Text('You',
                                    style: TextStyle(fontSize: 11)),
                                visualDensity: VisualDensity.compact,
                              ),
                          ],
                        ),
                        if (fixed && team.teamSize < (tt.teamSize ?? 99))
                          Padding(
                            padding: const EdgeInsets.only(top: 4),
                            child: Text(
                              'Waiting on ${(tt.teamSize ?? 0) - team.teamSize} more player(s) — the roster must be full to submit a record.',
                              style: TextStyle(
                                  fontSize: 12,
                                  color: Colors.orange.shade800),
                            ),
                          ),
                        if (!fixed && !team.canSubmit)
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
                            if (team.isMember(myId) ||
                                d.me.isCrew ||
                                d.me.isAdmin)
                              OutlinedButton.icon(
                                icon: const Icon(Icons.group, size: 16),
                                label: const Text('Edit team'),
                                onPressed: () => _editTeam(d, team),
                              ),
                            // First submission: the team (or crew) enters it.
                            // Any change after that — edit or delete — is
                            // mod/admin only.
                            if (team.score == null &&
                                (team.isMember(myId) ||
                                    d.me.isCrew ||
                                    d.me.isAdmin))
                              Builder(builder: (context) {
                                final ready = fixed
                                    ? team.teamSize == tt.teamSize
                                    : team.canSubmit;
                                return OutlinedButton.icon(
                                  icon: const Icon(Icons.scoreboard, size: 16),
                                  label: const Text('Enter score'),
                                  onPressed: ready
                                      ? () async {
                                          final saved =
                                              await Navigator.of(context)
                                                  .push(MaterialPageRoute(
                                            builder: (_) =>
                                                AltShotScoreScreen(
                                              auth: widget.auth,
                                              settings: widget.settings,
                                              teeTime: tt,
                                              team: team,
                                            ),
                                          ));
                                          if (saved == true) _refresh();
                                        }
                                      : null,
                                );
                              }),
                            if (team.score != null &&
                                d.me.canManageScores) ...[
                              OutlinedButton.icon(
                                icon: const Icon(Icons.edit, size: 16),
                                label: const Text('Edit score'),
                                onPressed: () async {
                                  final saved =
                                      await Navigator.of(context)
                                          .push(MaterialPageRoute(
                                    builder: (_) => AltShotScoreScreen(
                                      auth: widget.auth,
                                      settings: widget.settings,
                                      teeTime: tt,
                                      team: team,
                                    ),
                                  ));
                                  if (saved == true) _refresh();
                                },
                              ),
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
