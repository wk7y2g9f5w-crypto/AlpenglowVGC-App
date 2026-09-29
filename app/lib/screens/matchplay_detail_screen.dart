import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../services/auth.dart';
import '../widgets/common.dart';
import '../widgets/course_art.dart';
import 'matchplay_form_screen.dart';
import 'matchplay_score_screen.dart';

/// Detail for one match-play tee time: settings, the two sides, join/leave,
/// live status, score entry, and creator-or-crew edit/delete.
///
/// Both sides must be full before any score can be entered. Changing a
/// completed score is mod/admin only (can_manage_scores).
class MatchPlayDetailScreen extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;
  final String teeTimeId;

  const MatchPlayDetailScreen(
      {super.key,
      required this.auth,
      required this.settings,
      required this.teeTimeId});

  @override
  State<MatchPlayDetailScreen> createState() => _MatchPlayDetailScreenState();
}

class _MatchPlayDetailScreenState extends State<MatchPlayDetailScreen> {
  late Future<_Detail> _future;

  ApiClient get _api => ApiClient(
      baseUrl: widget.settings.baseUrl, token: widget.auth.token ?? '');

  @override
  void initState() {
    super.initState();
    _future = _load();
  }

  Future<_Detail> _load() async {
    final tt = await _api.getMatchPlayTeeTime(widget.teeTimeId);
    final me = await _api.getMe();
    return _Detail(tt: tt, me: me);
  }

  Future<void> _refresh() async {
    final f = _load();
    setState(() => _future = f);
    await f;
  }

  Future<void> _join(_Detail d, int sideNumber) async {
    try {
      final tt = await _api.joinMatchPlayTeeTime(d.tt.id, sideNumber: sideNumber);
      if (mounted) {
        setState(() => _future = Future.value(_Detail(tt: tt, me: d.me)));
      }
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message, error: true);
    }
  }

  Future<void> _leave(_Detail d) async {
    try {
      final tt = await _api.leaveMatchPlayTeeTime(d.tt.id);
      if (mounted) {
        setState(() => _future = Future.value(_Detail(tt: tt, me: d.me)));
      }
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message, error: true);
    }
  }

  Future<void> _deleteScore(_Detail d) async {
    final confirm = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Delete score?'),
        content: const Text(
            'This clears the match score back to unplayed and removes the win/loss records.'),
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
      await _api.deleteMatchPlayScore(d.tt.id);
      if (mounted) _refresh();
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message, error: true);
    }
  }

  Future<void> _delete(_Detail d) async {
    final confirm = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Delete match?'),
        content: const Text('This removes the match and its score.'),
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
      await _api.deleteMatchPlayTeeTime(d.tt.id);
      if (mounted) Navigator.of(context).pop(true);
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message, error: true);
    }
  }

  Future<void> _openScores(_Detail d) async {
    final changed = await Navigator.of(context).push(MaterialPageRoute(
      builder: (_) => MatchPlayScoreScreen(
        auth: widget.auth,
        settings: widget.settings,
        teeTime: d.tt,
        me: d.me,
      ),
    ));
    if (changed == true) _refresh();
  }

  Widget _statusBanner(MatchPlayTeeTime tt) {
    final score = tt.score;
    final String text;
    final Color color;
    if (score == null) {
      if (!tt.bothFull) {
        text = 'Waiting for both sides to fill up';
        color = Colors.orange.shade800;
      } else {
        text = 'Both sides are in — enter scores to start';
        color = Colors.green.shade800;
      }
    } else if (score.isCompleted) {
      text = score.liveText;
      color = Colors.green.shade800;
    } else {
      final thru = score.thruLine;
      text = thru.isEmpty ? score.liveText : '${score.liveText} · $thru';
      color = Colors.blue.shade800;
    }
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: color.withValues(alpha: 0.4)),
      ),
      child: Text(
        text,
        style: TextStyle(
            fontWeight: FontWeight.bold, fontSize: 16, color: color),
        textAlign: TextAlign.center,
      ),
    );
  }

  Widget _sideCard(_Detail d, MatchPlaySide side) {
    final tt = d.tt;
    final myId = d.me.discordId;
    final mySide = tt.mySide(myId);
    final full = side.size >= tt.sideCap;
    final inThisSide = side.isMember(myId);
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(side.sideNumber == 1 ? Icons.looks_one : Icons.looks_two,
                    size: 20),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(side.displayName,
                      style: const TextStyle(
                          fontWeight: FontWeight.bold, fontSize: 16)),
                ),
                Text('${side.size}/${tt.sideCap}',
                    style: const TextStyle(color: Colors.grey)),
              ],
            ),
            const SizedBox(height: 4),
            Wrap(
              spacing: 6,
              runSpacing: 4,
              children: [
                for (final name in side.memberNames)
                  Chip(
                    label: Text(name,
                        style: const TextStyle(fontSize: 12)),
                    visualDensity: VisualDensity.compact,
                  ),
                if (inThisSide)
                  const Chip(
                    label: Text('You',
                        style: TextStyle(fontSize: 11)),
                    visualDensity: VisualDensity.compact,
                  ),
              ],
            ),
            const SizedBox(height: 8),
            if (mySide == null)
              OutlinedButton.icon(
                icon: const Icon(Icons.group_add, size: 16),
                label: Text(full ? 'Side ${side.sideNumber} full' : 'Join side ${side.sideNumber}'),
                onPressed: full ? null : () => _join(d, side.sideNumber),
              )
            else if (inThisSide)
              OutlinedButton.icon(
                icon: const Icon(Icons.exit_to_app, size: 16),
                label: const Text('Leave'),
                onPressed: () => _leave(d),
              ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Match')),
      body: AsyncBody<_Detail>(
        future: _future,
        onRefresh: _refresh,
        builder: (context, d) {
          final tt = d.tt;
          final myId = d.me.discordId;
          final mySide = tt.mySide(myId);
          final canEdit =
              tt.creatorDiscordId == myId || d.me.isCrew || d.me.isAdmin;
          final score = tt.score;
          final completed = score?.isCompleted == true;
          return ListView(
            children: [
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
                  const SizedBox(height: 4),
                  Text('${tt.formatSummary} · ${tt.settingsSummary}',
                      style: const TextStyle(color: Colors.white70)),
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
              _statusBanner(tt),
              const SizedBox(height: 16),
              if (tt.bothFull)
                SizedBox(
                  width: double.infinity,
                  child: ElevatedButton.icon(
                    onPressed: () => _openScores(d),
                    icon: Icon(completed
                        ? Icons.visibility
                        : Icons.scoreboard),
                    label: Text(completed
                        ? 'View scorecard'
                        : (score == null ? 'Enter scores' : 'Continue scoring')),
                  ),
                ),
              if (tt.bothFull) const SizedBox(height: 8),
              // Completed-score controls are mod/admin only.
              if (completed && d.me.canManageScores)
                Wrap(
                  spacing: 8,
                  children: [
                    OutlinedButton.icon(
                      icon: const Icon(Icons.edit, size: 16),
                      label: const Text('Edit score'),
                      onPressed: () => _openScores(d),
                    ),
                    OutlinedButton.icon(
                      icon: const Icon(Icons.delete,
                          size: 16, color: Colors.red),
                      label: const Text('Delete score',
                          style: TextStyle(color: Colors.red)),
                      onPressed: () => _deleteScore(d),
                    ),
                  ],
                ),
              const SizedBox(height: 16),
              Text('Sides',
                  style: Theme.of(context).textTheme.titleSmall),
              const SizedBox(height: 8),
              if (tt.sides.isNotEmpty) _sideCard(d, tt.sides[0]),
              if (tt.sides.length > 1) _sideCard(d, tt.sides[1]),
              if (mySide == null && tt.bothFull)
                Padding(
                  padding: const EdgeInsets.only(top: 4),
                  child: Text('Both sides are full.',
                      style: TextStyle(
                          fontSize: 12, color: Colors.grey.shade600)),
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
                          builder: (_) => MatchPlayFormScreen(
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
                      icon: const Icon(Icons.delete, color: Colors.red),
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
  final MatchPlayTeeTime tt;
  final PlayerMe me;

  _Detail({required this.tt, required this.me});
}
