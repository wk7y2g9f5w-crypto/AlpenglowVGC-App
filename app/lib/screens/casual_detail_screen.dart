import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../services/auth.dart';
import '../widgets/common.dart';
import '../widgets/course_art.dart';
import 'altshot_detail_screen.dart';
import 'casual_form_screen.dart';
import 'casual_score_entry_screen.dart';
import 'matchplay_detail_screen.dart';

/// Detail for one casual tee time: settings, player list, join/leave,
/// and creator-or-crew edit/delete.
///
/// The format drives the lower half of the screen:
/// - stroke / best_ball: leaderboard + "Enter scores" (per-player
///   scorecards, same as tournament cards but self-attested).
/// - match_play / alt_shot: a button into the linked game, which lives
///   entirely in the existing Match Play / Alt-Shot engines.
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
    Map<String, dynamic>? leaderboard;
    if (tt.usesScorecards) {
      try {
        leaderboard = await _api.getCasualLeaderboard(tt.id);
      } on ApiException {
        leaderboard = null;
      }
    }
    return _Detail(tt: tt, me: me, leaderboard: leaderboard);
  }

  Future<void> _refresh() async {
    final f = _load();
    setState(() => _future = f);
    await f;
  }

  Future<void> _joinLeave(_Detail d, bool join) async {
    try {
      if (join) {
        await _api.joinCasualTeeTime(d.tt.id);
      } else {
        await _api.leaveCasualTeeTime(d.tt.id);
      }
      if (mounted) {
        setState(() => _future = _load());
      }
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

  /// Leaderboard + score entry for stroke / best-ball casual rounds.
  Widget _scorecardSection(_Detail d, bool inIt) {
    final lb = d.leaderboard;
    final players = (lb?['players'] as List?) ?? [];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Text('Leaderboard',
                style: Theme.of(context).textTheme.titleSmall),
            const Spacer(),
            if (inIt)
              ElevatedButton.icon(
                onPressed: () async {
                  final saved =
                      await Navigator.of(context).push(MaterialPageRoute(
                    builder: (_) => CasualScoreEntryScreen(
                      auth: widget.auth,
                      settings: widget.settings,
                      teeTime: d.tt,
                    ),
                  ));
                  if (saved == true) _refresh();
                },
                icon: const Icon(Icons.scoreboard, size: 18),
                label: const Text('Enter scores'),
              ),
          ],
        ),
        if (!inIt)
          const Padding(
            padding: EdgeInsets.only(top: 4),
            child: Text('Join the round to enter your scores.',
                style: TextStyle(fontSize: 12, color: Colors.grey)),
          ),
        const SizedBox(height: 8),
        if (players.isEmpty)
          const Text('No scores yet.',
              style: TextStyle(color: Colors.grey)),
        for (var i = 0; i < players.length; i++)
          _leaderboardRow(i + 1, players[i] as Map<String, dynamic>),
        if (d.tt.format == 'best_ball' && lb?['best_ball'] != null)
          _bestBallRow(lb!['best_ball'] as Map<String, dynamic>),
      ],
    );
  }

  Widget _leaderboardRow(int rank, Map<String, dynamic> p) {
    final name = golferDisplayName(
        (p['display_name'] ?? '').toString(),
        p['golfplus_handle']?.toString());
    final status = (p['status'] ?? '').toString();
    final toPar = (p['to_par'] as num?)?.toInt();
    final toParLabel = toPar == null
        ? ''
        : toPar == 0
            ? 'E'
            : toPar > 0
                ? '+$toPar'
                : '$toPar';
    return ListTile(
      contentPadding: EdgeInsets.zero,
      dense: true,
      leading: Text('$rank',
          style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
      title: Text(name),
      subtitle: status == 'in_progress'
          ? const Text('Live', style: TextStyle(color: Colors.green))
          : null,
      trailing: Text(
        '${p['total']}  $toParLabel',
        style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
      ),
    );
  }

  /// Best-ball-per-hole across ALL players on the round (v1: no formal
  /// teams for casual best ball).
  Widget _bestBallRow(Map<String, dynamic> bb) {
    final total = (bb['total'] as num?)?.toInt() ?? 0;
    final thru = (bb['thru'] as num?)?.toInt() ?? 0;
    return Card(
      color: Theme.of(context).colorScheme.primaryContainer,
      child: ListTile(
        dense: true,
        leading: const Icon(Icons.star),
        title: const Text('Best ball (whole group)',
            style: TextStyle(fontWeight: FontWeight.bold)),
        trailing: Text('$total · thru $thru',
            style: const TextStyle(fontWeight: FontWeight.bold)),
      ),
    );
  }

  /// Entry point into the linked match-play / alt-shot game for casual
  /// rounds in those formats. Scoring happens entirely there.
  Widget _linkedGameSection(_Detail d) {
    final tt = d.tt;
    final isMp = tt.isMatchPlay;
    final gameId = isMp ? tt.matchplayTeeTimeId : tt.altshotTeeTimeId;
    if (gameId == null) {
      return const Text('Linked game not found.',
          style: TextStyle(color: Colors.grey));
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(tt.formatLabel,
            style: Theme.of(context).textTheme.titleSmall),
        const SizedBox(height: 4),
        Text(
          isMp
              ? 'This round is played as match play — open the linked game to manage sides and score holes.'
              : 'This round is played as alt-shot — open the linked game to manage teams and score holes.',
          style: const TextStyle(color: Colors.grey, fontSize: 13),
        ),
        const SizedBox(height: 12),
        ElevatedButton.icon(
          onPressed: () {
            Navigator.of(context).push(MaterialPageRoute(
              builder: (_) => isMp
                  ? MatchPlayDetailScreen(
                      auth: widget.auth,
                      settings: widget.settings,
                      teeTimeId: gameId,
                    )
                  : AltShotDetailScreen(
                      auth: widget.auth,
                      settings: widget.settings,
                      teeTimeId: gameId,
                    ),
            ));
          },
          icon: const Icon(Icons.open_in_new, size: 18),
          label: Text('Open ${tt.formatLabel} game'),
        ),
      ],
    );
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
            children: [
              const ScorecardDeadlineWarning(),
              CourseArtHeader(
                course: tt.course,
                children: [
                  Text(tt.label,
                      style: const TextStyle(
                          fontSize: 22,
                          fontWeight: FontWeight.bold,
                          color: Colors.white)),
                  const SizedBox(height: 4),
                  Text(tt.course,
                      style: const TextStyle(
                          fontSize: 16, color: Colors.white70)),
                  const SizedBox(height: 4),
                  Text(formatTeeTimeWhen(tt.startsAt),
                      style: const TextStyle(color: Colors.white70)),
                  const SizedBox(height: 8),
                  Text(tt.settingsSummary,
                      style: const TextStyle(color: Colors.white70)),
                  const SizedBox(height: 8),
                  Chip(
                    label: Text(tt.formatLabel),
                    visualDensity: VisualDensity.compact,
                    backgroundColor: Colors.white24,
                    labelStyle: const TextStyle(color: Colors.white),
                    side: BorderSide.none,
                  ),
                  if (tt.notes.isNotEmpty) ...[
                    const SizedBox(height: 12),
                    Text(tt.notes,
                        style: const TextStyle(color: Colors.white70)),
                  ],
                ],
              ),
              Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
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
                  title: Text(p.handleName +
                      (p.discordId == tt.creatorDiscordId
                          ? ' (organizer)'
                          : '')),
                ),
              if (tt.usesScorecards) ...[
                const SizedBox(height: 16),
                const Divider(),
                _scorecardSection(d, inIt),
              ] else ...[
                const SizedBox(height: 16),
                const Divider(),
                _linkedGameSection(d),
              ],
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
  final CasualTeeTime tt;
  final PlayerMe me;
  final Map<String, dynamic>? leaderboard;

  _Detail({required this.tt, required this.me, this.leaderboard});
}
