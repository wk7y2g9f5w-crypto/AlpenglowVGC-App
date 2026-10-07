import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../services/auth.dart';
import '../widgets/common.dart';
import '../widgets/course_art.dart';
import 'altshot_form_screen.dart';
import 'altshot_score_screen.dart';

/// Detail for one alt-shot tee time: settings, teams, join/leave, roster
/// editing, score entry, and creator-or-crew edit/delete.
///
/// Three modes:
/// - 1-team fixed roster: players join the single team until it hits the
///   chosen size (2-4). Text-name teammates are still allowed here.
/// - Fixed 2-team: two pre-created teams with the same roster size,
///   registered players only. The creator starts on Team 1; joiners pick
///   a team. Both teams must be full before any score is submitted.
/// - Legacy flexible 2-team (created before team_size became mandatory):
///   joining creates your own team.
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

  void _applyTeeTime(_Detail d, AltShotTeeTime tt) {
    if (mounted) {
      setState(() => _future = Future.value(_Detail(tt: tt, me: d.me)));
    }
  }

  /// Join entry point: 1-team is one tap; fixed 2-team shows a team picker;
  /// legacy flexible opens the "start your own team" dialog.
  Future<void> _join(_Detail d) async {
    final tt = d.tt;
    if (tt.isOneTeam) {
      try {
        _applyTeeTime(d, await _api.joinAltShotTeeTime(tt.id));
      } on ApiException catch (e) {
        if (mounted) showSnack(context, e.message, error: true);
      }
      return;
    }
    if (tt.isFixedTwoTeam) {
      await _joinWithTeamPicker(d);
      return;
    }
    await _joinLegacy(d);
  }

  /// Fixed 2-team join: pick which team to play for.
  Future<void> _joinWithTeamPicker(_Detail d) async {
    final tt = d.tt;
    final t1 = tt.teamByNumber(1);
    final t2 = tt.teamByNumber(2);
    if (t1 == null || t2 == null) return;
    final size = tt.teamSize ?? 2;
    final pick = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Pick your team'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            for (final t in [t1, t2])
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: SizedBox(
                  width: double.infinity,
                  child: OutlinedButton(
                    onPressed: t.teamSize >= size
                        ? null
                        : () => Navigator.of(ctx).pop(t.id),
                    child: Text(
                        '${t.displayName} (${t.teamSize}/$size)${t.teamSize >= size ? ' — full' : ''}'),
                  ),
                ),
              ),
            const Text(
              'Registered players only — no typed-in names on 2-team rounds.',
              style: TextStyle(fontSize: 12, color: Colors.grey),
            ),
          ],
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(ctx).pop(),
              child: const Text('Cancel')),
        ],
      ),
    );
    if (pick == null) return;
    try {
      _applyTeeTime(d, await _api.joinAltShotTeeTime(tt.id, teamId: pick));
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message, error: true);
    }
  }

  /// Legacy flexible 2-team join: the caller starts their own team.
  Future<void> _joinLegacy(_Detail d) async {
    final extraCtrls = List.generate(3, (_) => TextEditingController());
    final result = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Join with a team'),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              for (var i = 0; i < extraCtrls.length; i++) ...[
                TextField(
                  controller: extraCtrls[i],
                  decoration: InputDecoration(
                    labelText: i == 0
                        ? 'Player 2 name'
                        : 'Player ${i + 2} name (optional)',
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
      _applyTeeTime(
          d,
          await _api.joinAltShotTeeTime(
            d.tt.id,
            extraNames:
                (result['extra_names'] as List?)?.cast<String>() ?? [],
          ));
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message, error: true);
    }
  }

  Future<void> _switchTeam(_Detail d, AltShotTeam other) async {
    try {
      _applyTeeTime(d, await _api.switchAltShotTeam(d.tt.id, other.id));
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message, error: true);
    }
  }

  Future<void> _leave(_Detail d) async {
    try {
      _applyTeeTime(d, await _api.leaveAltShotTeeTime(d.tt.id));
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message, error: true);
    }
  }

  /// 1-team / legacy edit dialog: text-name players
  /// (teammates not in the app). Registered members are fixed.
  Future<void> _editTeamLegacy(_Detail d, AltShotTeam team) async {
    final registeredCount = team.memberDiscordIds.length;
    final cap = d.tt.isOneTeam ? (d.tt.teamSize ?? 4) : 4;
    final maxExtras = (cap - registeredCount).clamp(0, 4);
    final currentExtras = team.playerNames
        .sublist(registeredCount.clamp(0, team.playerNames.length));
    final extraCtrls =
        currentExtras.map((n) => TextEditingController(text: n)).toList();
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
                if (registeredCount > 0)
                  Align(
                    alignment: Alignment.centerLeft,
                    child: Wrap(
                      spacing: 6,
                      children: [
                        for (var i = 0;
                            i < registeredCount &&
                                i < team.handleNames.length;
                            i++)
                          Chip(
                            label: Text(team.handleNames[i],
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
                  d.tt.isOneTeam
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
        'extra_names': (result['extra_names'] as List?) ?? [],
      });
      if (mounted) _refresh();
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message, error: true);
    }
  }

  /// Fixed 2-team manage dialog (organizer/crew): move a player to the
  /// other team, or remove a player. Moves and removals are blocked once
  /// any score is submitted.
  Future<void> _manageTeam(_Detail d, AltShotTeam team) async {
    final tt = d.tt;
    final other =
        tt.teams.firstWhere((t) => t.id != team.id, orElse: () => team);
    final locked = tt.anyScoreSubmitted;

    await showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('Manage ${team.displayName}'),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('Players',
                    style: TextStyle(fontWeight: FontWeight.bold)),
                const SizedBox(height: 4),
                if (team.playerNames.isEmpty)
                  const Text('No players yet.',
                      style: TextStyle(color: Colors.grey)),
                for (var i = 0; i < team.handleNames.length; i++)
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    dense: true,
                    title: Text(team.handleNames[i]),
                    trailing: locked
                        ? null
                        : Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              if (other.id != team.id &&
                                  other.teamSize < (tt.teamSize ?? 99))
                                IconButton(
                                  tooltip: 'Move to ${other.displayName}',
                                  icon: const Icon(Icons.swap_horiz),
                                  onPressed: () async {
                                    Navigator.of(ctx).pop();
                                    try {
                                      _applyTeeTime(
                                          d,
                                          await _api.manageAltShotTeam(
                                              tt.id, other.id, {
                                            'move_discord_id':
                                                team.memberDiscordIds[i],
                                          }));
                                    } on ApiException catch (e) {
                                      if (mounted) {
                                        showSnack(context, e.message,
                                            error: true);
                                      }
                                    }
                                  },
                                ),
                              IconButton(
                                tooltip: 'Remove from team',
                                icon: const Icon(Icons.person_remove,
                                    color: Colors.red),
                                onPressed: () async {
                                  Navigator.of(ctx).pop();
                                  try {
                                    _applyTeeTime(
                                        d,
                                        await _api.manageAltShotTeam(
                                            tt.id, team.id, {
                                          'remove_discord_id':
                                              team.memberDiscordIds[i],
                                        }));
                                  } on ApiException catch (e) {
                                    if (mounted) {
                                      showSnack(context, e.message,
                                          error: true);
                                    }
                                  }
                                },
                              ),
                            ],
                          ),
                  ),
                if (locked)
                  const Padding(
                    padding: EdgeInsets.only(top: 8),
                    child: Text(
                      'Teams are locked — scoring has started.',
                      style: TextStyle(fontSize: 12, color: Colors.grey),
                    ),
                  ),
              ],
            ),
          ),
          actions: [
            TextButton(
                onPressed: () => Navigator.of(ctx).pop(),
                child: const Text('Done')),
          ],
        ),
      );
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

  Widget _header(AltShotTeeTime tt) {
    final modeLine = tt.isOneTeam
        ? '${tt.teamSize}-player team'
        : tt.isFixedTwoTeam
            ? '2 teams · ${tt.teamSize} players each'
            : 'up to ${tt.maxTeams} teams';
    return CourseArtHeader(
      course: tt.course,
      children: [
        Text(tt.label,
            style: const TextStyle(
                fontSize: 22,
                fontWeight: FontWeight.bold,
                color: Colors.white)),
        const SizedBox(height: 4),
        Text(tt.course,
            style: const TextStyle(fontSize: 16, color: Colors.white70)),
        const SizedBox(height: 4),
        Text(formatTeeTimeWhen(tt.startsAt),
            style: const TextStyle(color: Colors.white70)),
        const SizedBox(height: 8),
        Text('${tt.settingsSummary} · $modeLine',
            style: const TextStyle(color: Colors.white70)),
        if (tt.notes.isNotEmpty) ...[
          const SizedBox(height: 12),
          Text(tt.notes, style: const TextStyle(color: Colors.white70)),
        ],
      ],
    );
  }

  /// Join / switch / leave controls for fixed 2-team tee times.
  Widget _twoTeamMembership(_Detail d, AltShotTeam? myTeam) {
    final tt = d.tt;
    if (myTeam == null) {
      return ElevatedButton.icon(
        onPressed: tt.isFull ? null : () => _join(d),
        icon: const Icon(Icons.group_add),
        label: Text(tt.isFull
            ? 'Both teams are full (${tt.teamSize}/${tt.teamSize} each)'
            : 'Join — pick your team'),
      );
    }
    final other = tt.teams.firstWhere((t) => t.id != myTeam.id);
    final canSwitch =
        !tt.anyScoreSubmitted && other.teamSize < (tt.teamSize ?? 99);
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      crossAxisAlignment: WrapCrossAlignment.center,
      children: [
        Chip(
          label: Text('You play for ${myTeam.displayName}'),
          visualDensity: VisualDensity.compact,
        ),
        if (canSwitch)
          OutlinedButton.icon(
            onPressed: () => _switchTeam(d, other),
            icon: const Icon(Icons.swap_horiz, size: 16),
            label: Text('Switch to ${other.displayName}'),
          ),
        OutlinedButton.icon(
          onPressed: () => _leave(d),
          icon: const Icon(Icons.exit_to_app, size: 16),
          label: const Text('Leave'),
        ),
      ],
    );
  }

  Widget _teamCard(_Detail d, AltShotTeam team) {
    final tt = d.tt;
    final myId = d.me.discordId;
    final fixed2 = tt.isFixedTwoTeam;
    final size = tt.teamSize ?? 4;
    final canManageTeams =
        fixed2 && (tt.creatorDiscordId == myId || d.me.isCrew || d.me.isAdmin);
    final canEditLegacy = !fixed2 &&
        (team.isMember(myId) || d.me.isCrew || d.me.isAdmin);
    return Card(
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
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                          '${team.displayName}'
                          '${fixed2 ? ' (${team.teamSize}/$size)' : ''}',
                          style:
                              const TextStyle(fontWeight: FontWeight.bold)),
                      if (team.playersLine.isNotEmpty)
                        Text(team.playersLine,
                            style: const TextStyle(
                                fontSize: 12, color: Colors.grey)),
                    ],
                  ),
                ),
                if (team.player1DiscordId == tt.creatorDiscordId)
                  const Chip(
                    label:
                        Text('Organizer', style: TextStyle(fontSize: 11)),
                    visualDensity: VisualDensity.compact,
                  ),
                if (team.isMember(myId))
                  const Chip(
                    label: Text('You', style: TextStyle(fontSize: 11)),
                    visualDensity: VisualDensity.compact,
                  ),
              ],
            ),
            if (fixed2 && team.teamSize < size && !tt.anyScoreSubmitted)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  'Waiting on ${size - team.teamSize} more player(s) — both teams must be full to submit a record.',
                  style:
                      TextStyle(fontSize: 12, color: Colors.orange.shade800),
                ),
              ),
            if (tt.isOneTeam && team.teamSize < (tt.teamSize ?? 99))
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  'Waiting on ${(tt.teamSize ?? 0) - team.teamSize} more player(s) — the roster must be full to submit a record.',
                  style:
                      TextStyle(fontSize: 12, color: Colors.orange.shade800),
                ),
              ),
            if (tt.isLegacyFlexible && !team.canSubmit)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  'Add at least one more player to submit a record.',
                  style:
                      TextStyle(fontSize: 12, color: Colors.orange.shade800),
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
                            fontWeight: FontWeight.bold, fontSize: 16)),
                  ],
                ),
              ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                if (canManageTeams)
                  OutlinedButton.icon(
                    icon: const Icon(Icons.group, size: 16),
                    label: const Text('Manage team'),
                    onPressed: () => _manageTeam(d, team),
                  ),
                if (canEditLegacy)
                  OutlinedButton.icon(
                    icon: const Icon(Icons.group, size: 16),
                    label: const Text('Edit team'),
                    onPressed: () => _editTeamLegacy(d, team),
                  ),
                // First submission: the team (or crew) enters it.
                // Any change after that — edit or delete — is mod/admin
                // only.
                if (team.score == null &&
                    (team.isMember(myId) || d.me.isCrew || d.me.isAdmin))
                  OutlinedButton.icon(
                    icon: const Icon(Icons.scoreboard, size: 16),
                    label: const Text('Enter score'),
                    onPressed: tt.canSubmitScore(team)
                        ? () async {
                            final saved = await Navigator.of(context)
                                .push(MaterialPageRoute(
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
                if (team.score != null && d.me.canManageScores) ...[
                  OutlinedButton.icon(
                    icon: const Icon(Icons.edit, size: 16),
                    label: const Text('Edit score'),
                    onPressed: () async {
                      final saved =
                          await Navigator.of(context).push(MaterialPageRoute(
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
    );
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
            children: [
              const ScorecardDeadlineWarning(),
              _header(tt),
              Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    if (tt.isFixedTwoTeam)
                      _twoTeamMembership(d, myTeam)
                    else if (myTeam == null)
                      ElevatedButton.icon(
                        onPressed: tt.isFull ? null : () => _join(d),
                        icon: const Icon(Icons.group_add),
                        label: Text(tt.isFull
                            ? (tt.isOneTeam
                                ? 'Team is full (${tt.teamSize}/${tt.teamSize})'
                                : 'Both team slots are full')
                            : (tt.isOneTeam
                                ? 'Join team'
                                : 'Join with a team')),
                      )
                    else
                      ElevatedButton.icon(
                        onPressed: () => _leave(d),
                        icon: const Icon(Icons.exit_to_app),
                        label: Text(tt.isOneTeam
                            ? 'Leave team'
                            : 'Leave (removes my team)'),
                      ),
                    const SizedBox(height: 16),
                    Text(
                        tt.isOneTeam
                            ? 'Team (${tt.teams.isEmpty ? 0 : tt.teams.first.teamSize}/${tt.teamSize})'
                            : tt.isFixedTwoTeam
                                ? 'Teams'
                                : 'Teams (${tt.teams.length}/${tt.maxTeams})',
                        style: Theme.of(context).textTheme.titleSmall),
                    const SizedBox(height: 8),
                    for (final team in tt.teams) _teamCard(d, team),
                    if (canEdit) ...[
                      const SizedBox(height: 16),
                      const Divider(),
                      Row(
                        children: [
                          OutlinedButton.icon(
                            onPressed: () async {
                              final saved = await Navigator.of(context)
                                  .push(MaterialPageRoute(
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
                            icon: const Icon(Icons.delete,
                                color: Colors.red),
                            label: const Text('Delete',
                                style: TextStyle(color: Colors.red)),
                          ),
                        ],
                      ),
                    ],
                  ],
                ),
              ),
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
