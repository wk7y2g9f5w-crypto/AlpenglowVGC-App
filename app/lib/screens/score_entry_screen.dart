import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../services/auth.dart';
import '../widgets/common.dart';
import '../widgets/score_badge.dart';

/// Native score entry screen mirroring the Discord bot's tap-to-enter UI.
///
/// - Player picker (anyone in the tee time)
/// - Par-relative quick buttons when hole pars are known, numeric entry otherwise
/// - Auto-advance, prev/next, custom score 1–15
/// - Running total + to-par header
/// - Submit locked until every hole is entered
/// - Whole screen locked with a friendly message while the tee time is in the future
class ScoreEntryScreen extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;
  final Tournament tournament;
  final TeeTime teeTime;

  const ScoreEntryScreen({
    super.key,
    required this.auth,
    required this.settings,
    required this.tournament,
    required this.teeTime,
  });

  @override
  State<ScoreEntryScreen> createState() => _ScoreEntryScreenState();
}

class _ScoreEntryScreenState extends State<ScoreEntryScreen> {
  TeeTimePlayer? _player;
  late List<int?> _scores;
  int _hole = 0; // 0-based current hole
  int _roundNumber = 1;
  bool _loading = true;
  bool _submitting = false;
  String? _existingStatus;
  bool _isCrew = false;

  ApiClient get _api => ApiClient(
      baseUrl: widget.settings.baseUrl, token: widget.auth.token ?? '');

  int get _holeCount {
    final pars = widget.tournament.pars;
    if (pars != null && pars.isNotEmpty) return pars.length;
    return widget.tournament.holes ?? 18;
  }

  List<int>? get _pars {
    final pars = widget.tournament.pars;
    if (pars != null && pars.length >= _holeCount) {
      return pars.sublist(0, _holeCount);
    }
    return null;
  }

  bool get _locked => widget.teeTime.startsAtUtc.isAfter(DateTime.now());

  @override
  void initState() {
    super.initState();
    _scores = List<int?>.filled(_holeCount, null);
    _player =
        widget.teeTime.players.isNotEmpty ? widget.teeTime.players.first : null;
    _loadExisting();
    _loadCrew();
  }

  Future<void> _loadCrew() async {
    try {
      final me = await _api.getMe();
      if (mounted) setState(() => _isCrew = me.isCrew);
    } catch (_) {
      // Leave as non-crew; the server still enforces the lock.
    }
  }

  Future<void> _loadExisting() async {
    await _loadCard(null, _roundNumber); // null = my own card
    if (mounted) setState(() => _loading = false);
  }

  /// Load the saved card (if any) for [playerDiscordId] (null = me) + round.
  /// The displayed card always belongs to the selected player.
  Future<void> _loadCard(String? playerDiscordId, int roundNumber) async {
    try {
      final card = await _api.getScorecard(widget.teeTime.id,
          roundNumber: roundNumber, playerDiscordId: playerDiscordId);
      if (!mounted) return;
      if (card != null && card.scores.length == _holeCount) {
        setState(() {
          _scores = card.scores.map<int?>((s) => s).toList();
          _existingStatus = card.status;
          _hole = 0;
          final match = widget.teeTime.players
              .where((p) => p.discordId == card.playerDiscordId);
          if (match.isNotEmpty) _player = match.first;
        });
      } else {
        setState(() {
          _scores = List<int?>.filled(_holeCount, null);
          _existingStatus = null;
          _hole = 0;
        });
      }
    } on ApiException {
      // No existing card or unreadable; start blank.
      if (mounted) {
        setState(() {
          _scores = List<int?>.filled(_holeCount, null);
          _existingStatus = null;
          _hole = 0;
        });
      }
    }
  }

  void _pickRound(int rn) {
    if (rn == _roundNumber) return;
    setState(() {
      _roundNumber = rn;
      _loading = true;
    });
    _loadCard(_player?.discordId, rn).then((_) {
      if (mounted) setState(() => _loading = false);
    });
  }

  String _roundLabel(int rn) {
    final rounds = widget.tournament.rounds;
    if (rounds.length >= rn) {
      final r = rounds[rn - 1];
      final summary = r.settingsSummary;
      return summary.isEmpty ? 'Round $rn' : 'Round $rn — $summary';
    }
    return 'Round $rn';
  }

  int? get _total {
    if (_scores.any((s) => s == null)) return null;
    return _scores.fold<int>(0, (a, b) => a + (b ?? 0));
  }

  int? get _toPar {
    final pars = _pars;
    final total = _total;
    if (pars == null || total == null) return null;
    return total - pars.fold<int>(0, (a, b) => a + b);
  }

  String _toParLabel() {
    final tp = _toPar;
    if (tp == null) return '';
    if (tp == 0) return 'E';
    return tp > 0 ? '+$tp' : '$tp';
  }

  void _enterScore(int score) {
    if (_locked) return;
    setState(() {
      _scores[_hole] = score;
      if (_hole < _holeCount - 1) _hole++;
    });
  }

  Future<void> _customScore() async {
    final ctrl =
        TextEditingController(text: _scores[_hole]?.toString() ?? '');
    final val = await showDialog<int>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('Hole ${_hole + 1} — custom score'),
        content: TextField(
          controller: ctrl,
          keyboardType: TextInputType.number,
          autofocus: true,
          decoration: const InputDecoration(
              labelText: 'Strokes (1–15)', border: OutlineInputBorder()),
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(ctx).pop(),
              child: const Text('Cancel')),
          ElevatedButton(
            onPressed: () {
              final n = int.tryParse(ctrl.text.trim());
              if (n == null || n < 1 || n > 15) {
                showSnack(ctx, 'Enter a number from 1 to 15.', error: true);
                return;
              }
              Navigator.of(ctx).pop(n);
            },
            child: const Text('Set'),
          ),
        ],
      ),
    );
    if (val != null) _enterScore(val);
  }

  Future<void> _submit() async {
    if (_player == null) return;
    // Finality warning: scores stay editable right up until this point.
    final toPar = _toParLabel();
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(_existingStatus != null
            ? 'Update submitted scorecard?'
            : 'Submit scorecard?'),
        content: Text(
          _existingStatus != null
              ? 'This will update ${_player!.displayName}\'s submitted '
                  'scorecard for round $_roundNumber.'
              : 'All scores entered are final. After submitting, only crew '
                  '(admins, mods, tournament directors) can change them.\n\n'
                  '${_player!.displayName} — round $_roundNumber: '
                  'total ${_total ?? '–'}'
                  '${toPar.isNotEmpty ? ' ($toPar)' : ''}',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('Keep editing'),
          ),
          ElevatedButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            child: Text(_existingStatus != null ? 'Update' : 'Submit'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    setState(() => _submitting = true);
    try {
      await _api.submitScorecard(
        widget.teeTime.id,
        _player!.discordId,
        _scores.map((s) => s!).toList(),
        roundNumber: _roundNumber,
      );
      if (mounted) {
        showSnack(context,
            'Round $_roundNumber scorecard submitted.');
        Navigator.of(context).pop();
      }
    } on ApiException catch (e) {
      if (mounted) showSnack(context, friendlyApiMessage(e), error: true);
    } catch (e) {
      if (mounted) showSnack(context, 'Submit failed: $e', error: true);
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Enter scores')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _locked
              ? _lockedBody()
              : (_existingStatus != null && !_isCrew)
                  ? _submittedBody()
                  : _entryBody(),
    );
  }

  Widget _lockedBody() {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.lock_clock, size: 64, color: Colors.grey),
            const SizedBox(height: 16),
            const Text('Not yet!',
                style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold)),
            const SizedBox(height: 8),
            Text(
              'This tee time starts ${formatLocal(widget.teeTime.startsAtUtc)}. '
              'Come back after you\'ve teed off to enter scores.',
              textAlign: TextAlign.center,
              style: const TextStyle(color: Colors.grey),
            ),
          ],
        ),
      ),
    );
  }

  /// Read-only view shown when a card is already submitted and the viewer
  /// isn't crew. The server rejects any edit attempt with scorecard_locked.
  Widget _submittedBody() {
    final pars = _pars;
    final total = _scores.fold<int>(0, (a, s) => a + (s ?? 0));
    final playerName = _player?.displayName ?? 'this player';
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        const Icon(Icons.lock_outline, size: 48, color: Colors.grey),
        const SizedBox(height: 12),
        Text(
          'Round $_roundNumber submitted',
          textAlign: TextAlign.center,
          style: const TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
        ),
        const SizedBox(height: 8),
        Text(
          '$playerName\'s scorecard is already submitted — only crew '
          '(admins, mods, tournament directors) can change scores. '
          'Ask a crew member if something needs fixing.',
          textAlign: TextAlign.center,
          style: const TextStyle(color: Colors.grey),
        ),
        const SizedBox(height: 16),
        ...List.generate(_holeCount, (i) {
          final s = _scores[i];
          final par = pars != null ? pars[i] : null;
          final rel = (par != null && s != null) ? s - par : null;
          final relLabel = rel == null || rel == 0
              ? ''
              : ' (${rel > 0 ? '+' : ''}$rel)';
          return ListTile(
            dense: true,
            title: Text('Hole ${i + 1}'),
            trailing: Text(
              s == null ? '–' : '$s$relLabel',
              style: const TextStyle(
                  fontSize: 16, fontWeight: FontWeight.bold),
            ),
          );
        }),
        const Divider(),
        Text(
          'Total $total',
          textAlign: TextAlign.center,
          style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
        ),
      ],
    );
  }

  Widget _entryBody() {
    final pars = _pars;
    final par = pars != null ? pars[_hole] : null;
    final complete = !_scores.any((s) => s == null);
    final multi = widget.tournament.isMultiRound;

    return Column(
      children: [
        // Round picker for multi-round tournaments.
        if (multi)
          Container(
            padding:
                const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            color: Theme.of(context).colorScheme.surfaceContainerHighest,
            child: DropdownButtonFormField<int>(
              initialValue: _roundNumber,
              decoration: const InputDecoration(
                labelText: 'Round',
                border: OutlineInputBorder(),
                isDense: true,
              ),
              items: List.generate(
                widget.tournament.numRounds,
                (i) => DropdownMenuItem(
                  value: i + 1,
                  child: Text(_roundLabel(i + 1),
                      overflow: TextOverflow.ellipsis),
                ),
              ),
              onChanged: (rn) {
                if (rn != null) _pickRound(rn);
              },
            ),
          ),
        // Header: player picker + running total / to-par.
        Container(
          padding: const EdgeInsets.all(12),
          color: Theme.of(context).colorScheme.surfaceContainerHighest,
          child: Row(
            children: [
              Expanded(
                child: DropdownButtonFormField<TeeTimePlayer>(
                  initialValue: _player,
                  decoration: const InputDecoration(
                    labelText: 'Player',
                    border: OutlineInputBorder(),
                    isDense: true,
                  ),
                  items: widget.teeTime.players
                      .map((p) => DropdownMenuItem(
                          value: p, child: Text(p.displayName)))
                      .toList(),
                  onChanged: (p) {
                    if (p == null || p.discordId == _player?.discordId) return;
                    setState(() {
                      _player = p;
                      _loading = true;
                    });
                    _loadCard(p.discordId, _roundNumber).then((_) {
                      if (mounted) setState(() => _loading = false);
                    });
                  },
                ),
              ),
              const SizedBox(width: 12),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text('Total ${_total ?? '–'}',
                      style: const TextStyle(
                          fontSize: 18, fontWeight: FontWeight.bold)),
                  Text(_toParLabel(),
                      style: TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.bold,
                          color: (_toPar ?? 0) <= 0
                              ? Colors.green.shade700
                              : Colors.red.shade700)),
                ],
              ),
            ],
          ),
        ),
        // Hole strip: hole number + score in standard golf notation
        // (circle = birdie, square = bogey, double ring/square for
        // eagle/double+). Tap a hole to jump to it.
        SizedBox(
          height: 70,
          child: ListView.builder(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
            itemCount: _holeCount,
            itemBuilder: (context, i) {
              final score = _scores[i];
              final holePar = pars != null ? pars[i] : null;
              final current = i == _hole;
              final scheme = Theme.of(context).colorScheme;
              return GestureDetector(
                onTap: () => setState(() => _hole = i),
                child: Container(
                  width: 54,
                  margin: const EdgeInsets.symmetric(horizontal: 2),
                  decoration: BoxDecoration(
                    color: current ? scheme.primaryContainer : null,
                    borderRadius: BorderRadius.circular(10),
                    border: current
                        ? Border.all(color: scheme.primary, width: 1.5)
                        : null,
                  ),
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Text(
                        '${i + 1}',
                        style: TextStyle(
                          fontSize: 11,
                          color: scheme.onSurfaceVariant,
                          fontWeight:
                              current ? FontWeight.bold : FontWeight.normal,
                        ),
                      ),
                      const SizedBox(height: 2),
                      score != null
                          ? ScoreBadge(score: score, par: holePar, size: 30)
                          : Container(
                              width: 30,
                              height: 30,
                              decoration: BoxDecoration(
                                shape: BoxShape.circle,
                                border: Border.all(
                                  color: scheme.outlineVariant,
                                  width: 1,
                                ),
                              ),
                            ),
                    ],
                  ),
                ),
              );
            },
          ),
        ),
        Expanded(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(16),
            child: Column(
              children: [
                Text('Hole ${_hole + 1}${par != null ? ' · Par $par' : ''}',
                    style: const TextStyle(
                        fontSize: 22, fontWeight: FontWeight.bold)),
                const SizedBox(height: 16),
                if (par != null) _parRelativeButtons(par) else _numericButtons(),
                const SizedBox(height: 12),
                OutlinedButton.icon(
                  onPressed: _customScore,
                  icon: const Icon(Icons.edit),
                  label: const Text('Custom score (1–15)'),
                ),
                if (_existingStatus != null) ...[
                  const SizedBox(height: 8),
                  Text(
                      'Previously saved${widget.tournament.isMultiRound ? ' (round $_roundNumber)' : ''}: $_existingStatus',
                      style:
                          const TextStyle(color: Colors.grey, fontSize: 12)),
                ],
              ],
            ),
          ),
        ),
        // Footer: prev/next + submit.
        SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: Row(
              children: [
                OutlinedButton(
                  onPressed: _hole > 0 ? () => setState(() => _hole--) : null,
                  child: const Text('Prev'),
                ),
                const SizedBox(width: 8),
                OutlinedButton(
                  onPressed: _hole < _holeCount - 1
                      ? () => setState(() => _hole++)
                      : null,
                  child: const Text('Next'),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: ElevatedButton(
                    onPressed: (complete && !_submitting && _player != null)
                        ? _submit
                        : null,
                    child: _submitting
                        ? const SizedBox(
                            width: 18,
                            height: 18,
                            child: CircularProgressIndicator(strokeWidth: 2))
                        : Text(complete
                            ? 'Submit scorecard'
                            : 'Enter all $_holeCount holes to submit'),
                  ),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }

  /// Quick buttons relative to par: Eagle(-2), Birdie(-1), Par(0),
  /// Bogey(+1), Double(+2), Triple(+3).
  Widget _parRelativeButtons(int par) {
    const deltas = [-2, -1, 0, 1, 2, 3];
    const names = {
      -2: 'Eagle',
      -1: 'Birdie',
      0: 'Par',
      1: 'Bogey',
      2: 'Double',
      3: 'Triple',
    };
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      alignment: WrapAlignment.center,
      children: deltas.map((d) {
        final score = par + d;
        final selected = _scores[_hole] == score;
        return ElevatedButton(
          onPressed: score >= 1 ? () => _enterScore(score) : null,
          style: ElevatedButton.styleFrom(
            backgroundColor: selected
                ? Theme.of(context).colorScheme.primary
                : d <= 0
                    ? Colors.green.shade100
                    : Colors.red.shade100,
            foregroundColor: selected ? Colors.white : Colors.black87,
            padding:
                const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(names[d] ?? '$d',
                  style: const TextStyle(fontWeight: FontWeight.bold)),
              Text('$score', style: const TextStyle(fontSize: 12)),
            ],
          ),
        );
      }).toList(),
    );
  }

  /// Numeric entry fallback when hole pars are unknown.
  Widget _numericButtons() {
    return GridView.count(
      crossAxisCount: 5,
      shrinkWrap: true,
      physics: const NeverScrollableScrollPhysics(),
      mainAxisSpacing: 8,
      crossAxisSpacing: 8,
      childAspectRatio: 1.2,
      children: List.generate(10, (i) => i + 1).map((score) {
        final selected = _scores[_hole] == score;
        return ElevatedButton(
          onPressed: () => _enterScore(score),
          style: ElevatedButton.styleFrom(
            backgroundColor:
                selected ? Theme.of(context).colorScheme.primary : null,
            foregroundColor: selected ? Colors.white : null,
          ),
          child: Text('$score',
              style:
                  const TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
        );
      }).toList(),
    );
  }
}
