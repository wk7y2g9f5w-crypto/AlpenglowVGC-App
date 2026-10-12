import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../services/auth.dart';
import '../widgets/common.dart';
import '../widgets/scorecard_grid.dart';

/// Enter (or edit) a team's 18-hole alt-shot score. Submitted once — not a
/// live scorecard. The team needs both players attached to submit.
class AltShotScoreScreen extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;
  final AltShotTeeTime teeTime;
  final AltShotTeam team;

  const AltShotScoreScreen(
      {super.key,
      required this.auth,
      required this.settings,
      required this.teeTime,
      required this.team});

  @override
  State<AltShotScoreScreen> createState() => _AltShotScoreScreenState();
}

class _AltShotScoreScreenState extends State<AltShotScoreScreen> {
  late List<int?> _holes;
  bool _saving = false;

  ApiClient get _api => ApiClient(
      baseUrl: widget.settings.baseUrl, token: widget.auth.token ?? '');

  List<int> get _pars {
    final pars = widget.teeTime.pars;
    return List<int>.generate(18, (i) => pars.length == 18 ? pars[i] : 4);
  }

  @override
  void initState() {
    super.initState();
    final existing = widget.team.score;
    if (existing != null && existing.holes.length == 18) {
      _holes = List<int?>.from(existing.holes);
    } else {
      // Fresh card starts at par on every hole (unchanged behavior).
      _holes = List<int?>.from(_pars);
    }
  }

  int get _total => _holes.fold<int>(0, (a, b) => a + (b ?? 0));

  String get _toParLine {
    final pars = widget.teeTime.pars;
    if (pars.length != 18) return 'Total $_total';
    final toPar = _total - pars.reduce((a, b) => a + b);
    if (toPar == 0) return 'Total $_total (E)';
    return 'Total $_total (${toPar > 0 ? '+' : ''}$toPar)';
  }

  Future<void> _save() async {
    final tt = widget.teeTime;
    final ready = tt.canSubmitScore(widget.team);
    if (!ready) {
      showSnack(
          context,
          tt.isOneTeam
              ? 'The roster must be full (${tt.teamSize} players) before submitting a record.'
              : tt.isFixedTwoTeam
                  ? 'Both teams must be full (${tt.teamSize} players each) before submitting a record.'
                  : 'Add at least 2 players to the team before submitting a record.',
          error: true);
      return;
    }
    setState(() => _saving = true);
    try {
      await _api.submitAltShotScore(
          widget.teeTime.id, widget.team.id, _holes.map((s) => s ?? 4).toList());
      if (mounted) Navigator.of(context).pop(true);
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message, error: true);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(widget.team.score == null
          ? 'Enter Team Score'
          : 'Edit Team Score')),
      body: Column(
        children: [
          Container(
            width: double.infinity,
            padding:
                const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
            color: Theme.of(context).colorScheme.surfaceContainerHighest,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(widget.team.displayName,
                    style:
                        Theme.of(context).textTheme.titleMedium),
                const SizedBox(height: 2),
                Text(widget.teeTime.course,
                    style: const TextStyle(color: Colors.grey)),
                const SizedBox(height: 6),
                Text(_toParLine,
                    style: const TextStyle(
                        fontSize: 18, fontWeight: FontWeight.bold)),
              ],
            ),
          ),
          Expanded(
            child: ScorecardGrid(
              cards: [
                ScorecardGridCard(
                  title: widget.team.displayName,
                  scores: _holes,
                ),
              ],
              pars: _pars,
              holeCount: 18,
              allowClear: false,
              customMax: 20,
              onScore: (card, hole, score) {
                if (score != null) setState(() => _holes[hole] = score);
              },
            ),
          ),
          SafeArea(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  onPressed: _saving ? null : _save,
                  child: _saving
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(
                              strokeWidth: 2))
                      : const Text('Submit to records'),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
