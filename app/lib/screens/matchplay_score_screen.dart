import 'dart:async';

import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../services/auth.dart';
import '../widgets/common.dart';

/// Live hole-by-hole match-play scoring. Each hole: +1 = side 1 wins the
/// hole, −1 = side 2 wins, – = halved; tap the active pick again to clear.
/// Saves are debounced; the server recomputes the live status ("2 UP thru
/// 5", "Dormie", "All Square") and auto-completes the match when decided.
/// A completed match is locked for everyone except mods/admins.
class MatchPlayScoreScreen extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;
  final MatchPlayTeeTime teeTime;
  final PlayerMe me;

  const MatchPlayScoreScreen(
      {super.key,
      required this.auth,
      required this.settings,
      required this.teeTime,
      required this.me});

  @override
  State<MatchPlayScoreScreen> createState() => _MatchPlayScoreScreenState();
}

class _MatchPlayScoreScreenState extends State<MatchPlayScoreScreen> {
  late List<int?> _holes;
  MatchPlayScore? _serverScore;
  bool _loading = true;
  bool _saving = false;
  bool _dirty = false;
  Timer? _saveTimer;

  ApiClient get _api => ApiClient(
      baseUrl: widget.settings.baseUrl, token: widget.auth.token ?? '');

  bool get _locked =>
      _serverScore?.isCompleted == true && !widget.me.canManageScores;

  String get _side1Name =>
      widget.teeTime.sides.isNotEmpty ? widget.teeTime.sides[0].displayName : 'Side 1';
  String get _side2Name => widget.teeTime.sides.length > 1
      ? widget.teeTime.sides[1].displayName
      : 'Side 2';

  @override
  void initState() {
    super.initState();
    _holes = List<int?>.filled(18, null);
    _load();
  }

  @override
  void dispose() {
    _saveTimer?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final score = await _api.getMatchPlayScore(widget.teeTime.id);
      if (!mounted) return;
      setState(() {
        _serverScore = score;
        if (score != null && score.holeResults.length == 18) {
          _holes = List<int?>.from(score.holeResults);
        }
        _loading = false;
      });
    } catch (err) {
      if (!mounted) return;
      setState(() => _loading = false);
      showSnack(context, 'Could not load score: $err', error: true);
    }
  }

  void _setHole(int i, int? value) {
    if (_locked) return;
    setState(() {
      _holes[i] = value;
      _dirty = true;
    });
    _scheduleSave();
  }

  void _scheduleSave() {
    if (_locked) return;
    _saveTimer?.cancel();
    _saveTimer = Timer(const Duration(milliseconds: 800), _liveSave);
  }

  Future<void> _liveSave() async {
    if (_locked || _saving || !_dirty) return;
    setState(() => _saving = true);
    try {
      final score =
          await _api.saveMatchPlayScore(widget.teeTime.id, List<int?>.from(_holes));
      if (!mounted) return;
      setState(() {
        _serverScore = score;
        _dirty = false;
        _saving = false;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() => _saving = false);
      showSnack(context, e.message, error: true);
    }
  }

  Future<void> _done() async {
    _saveTimer?.cancel();
    if (_dirty && !_locked) {
      await _liveSave();
    }
    if (mounted) Navigator.of(context).pop(_dirty == false);
  }

  Widget _statusHeader() {
    final score = _serverScore;
    final String text;
    final Color color;
    if (score == null) {
      final played = _holes.where((h) => h != null).length;
      text = played == 0 ? 'No holes scored yet' : 'Scoring… $played thru';
      color = Colors.grey.shade700;
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
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      color: Theme.of(context).colorScheme.surfaceContainerHighest,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('$_side1Name  vs  $_side2Name',
              style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 4),
          Row(
            children: [
              Expanded(
                child: Text(text,
                    style: TextStyle(
                        fontSize: 20,
                        fontWeight: FontWeight.bold,
                        color: color)),
              ),
              if (_saving)
                const SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(strokeWidth: 2)),
            ],
          ),
          if (_locked)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(
                  'Match complete — only mods/admins can change it.',
                  style: TextStyle(
                      fontSize: 12, color: Colors.orange.shade800)),
            ),
        ],
      ),
    );
  }

  Widget _holeRow(int i) {
    final value = _holes[i];
    return Card(
      margin: const EdgeInsets.only(bottom: 6),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        child: Row(
          children: [
            SizedBox(
              width: 56,
              child: Text('Hole ${i + 1}',
                  style: const TextStyle(fontWeight: FontWeight.w600)),
            ),
            Expanded(
              child: SegmentedButton<int?>(
                emptySelectionAllowed: true,
                showSelectedIcon: false,
                style: SegmentedButton.styleFrom(
                  visualDensity: VisualDensity.compact,
                  padding: const EdgeInsets.symmetric(horizontal: 4),
                ),
                segments: const [
                  ButtonSegment<int?>(
                    value: 1,
                    label: Text('+1',
                        style: TextStyle(fontWeight: FontWeight.bold)),
                    tooltip: 'Side 1 wins the hole',
                  ),
                  ButtonSegment<int?>(
                    value: 0,
                    label: Text('–',
                        style: TextStyle(fontWeight: FontWeight.bold)),
                    tooltip: 'Halved',
                  ),
                  ButtonSegment<int?>(
                    value: -1,
                    label: Text('−1',
                        style: TextStyle(fontWeight: FontWeight.bold)),
                    tooltip: 'Side 2 wins the hole',
                  ),
                ],
                selected: value == null ? const <int?>{} : {value},
                onSelectionChanged: _locked
                    ? null
                    : (s) => _setHole(i, s.isEmpty ? null : s.first),
              ),
            ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Match Scoring')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : Column(
              children: [
                _statusHeader(),
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
                  child: Text(
                    '$_side1Name: +1   ·   –: halve   ·   $_side2Name: −1\nTap the active pick again to clear a hole.',
                    style:
                        TextStyle(fontSize: 12, color: Colors.grey.shade600),
                    textAlign: TextAlign.center,
                  ),
                ),
                Expanded(
                  child: ListView.builder(
                    padding: const EdgeInsets.all(12),
                    itemCount: 18,
                    itemBuilder: (context, i) => _holeRow(i),
                  ),
                ),
                SafeArea(
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: SizedBox(
                      width: double.infinity,
                      child: ElevatedButton(
                        onPressed: _saving ? null : _done,
                        child: const Text('Done'),
                      ),
                    ),
                  ),
                ),
              ],
            ),
    );
  }
}
