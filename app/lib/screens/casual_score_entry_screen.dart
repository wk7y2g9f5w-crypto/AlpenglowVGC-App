import 'dart:async';

import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../services/auth.dart';
import '../widgets/common.dart';
import '../widgets/score_badge.dart';
import '../widgets/shot_tracker.dart';
import 'shot_review_screen.dart';

/// Score entry for a casual stroke / best-ball round.
///
/// Same hole-by-hole entry UX as the tournament screen, minus rounds and
/// tee-time gates: a casual round is a single 18-hole card per player.
/// Cards are self-attested — submitting marks the card verified directly,
/// and it feeds the same optional stats + handicap as tournament cards.
class CasualScoreEntryScreen extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;
  final CasualTeeTime teeTime;

  const CasualScoreEntryScreen({
    super.key,
    required this.auth,
    required this.settings,
    required this.teeTime,
  });

  @override
  State<CasualScoreEntryScreen> createState() =>
      _CasualScoreEntryScreenState();
}

class _CasualScoreEntryScreenState extends State<CasualScoreEntryScreen> {
  static const _holeCount = 18;

  CasualPlayer? _player;
  late List<int?> _scores;
  int _hole = 0; // 0-based current hole
  bool _loading = true;
  bool _submitting = false;
  String? _existingStatus;
  final _witnessCtrl = TextEditingController();
  int _loadSeq = 0;
  Timer? _saveTimer;
  bool _saving = false;
  bool _saveFailed = false;

  /// Scorecard id from the server — the key for the shots endpoints.
  int? _cardId;

  ApiClient get _api => ApiClient(
      baseUrl: widget.settings.baseUrl, token: widget.auth.token ?? '');

  List<int>? get _pars {
    final pars = widget.teeTime.pars;
    if (pars != null && pars.length >= _holeCount) {
      return pars.sublist(0, _holeCount);
    }
    return null;
  }

  bool get _locked {
    final raw = widget.teeTime.startsAt;
    if (raw.isEmpty) return false;
    try {
      return DateTime.parse(raw).isAfter(DateTime.now());
    } catch (_) {
      return false;
    }
  }

  @override
  void initState() {
    super.initState();
    _scores = List<int?>.filled(_holeCount, null);
    _player = widget.teeTime.players.isNotEmpty
        ? widget.teeTime.players.first
        : null;
    _loadCard(null).then((_) {
      if (mounted) setState(() => _loading = false);
    });
  }

  @override
  void dispose() {
    _saveTimer?.cancel();
    _witnessCtrl.dispose();
    super.dispose();
  }

  /// Load the saved card (if any) for [playerDiscordId] (null = me).
  Future<void> _loadCard(String? playerDiscordId) async {
    final seq = ++_loadSeq;
    try {
      final card = await _api.getCasualScorecard(widget.teeTime.id,
          playerDiscordId: playerDiscordId);
      if (!mounted || seq != _loadSeq) return;
      if (card != null && card.scores.length == _holeCount) {
        setState(() {
          _scores = card.scores.map<int?>((s) => s).toList();
          _existingStatus = card.status;
          _witnessCtrl.text = card.witnessName ?? '';
          _cardId = card.id;
          _hole = 0;
          final match = widget.teeTime.players
              .where((p) => p.discordId == card.playerDiscordId);
          if (match.isNotEmpty) _player = match.first;
        });
      } else {
        _blankCard();
      }
    } on ApiException {
      if (mounted && seq == _loadSeq) _blankCard();
    }
  }

  void _blankCard() {
    setState(() {
      _scores = List<int?>.filled(_holeCount, null);
      _existingStatus = null;
      _witnessCtrl.clear();
      _cardId = null;
      _hole = 0;
    });
  }

  /// Switch to another player's scorecard. Flushes any pending debounced
  /// live save first so a quick switch can't drop a just-entered score.
  Future<void> _pickPlayer(CasualPlayer p) async {
    if (p.discordId == _player?.discordId) return;
    _saveTimer?.cancel();
    if (_player != null) await _liveSave();
    if (!mounted) return;
    setState(() {
      _player = p;
      _loading = true;
    });
    _loadCard(p.discordId).then((_) {
      if (mounted) setState(() => _loading = false);
    });
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
    _scheduleLiveSave();
  }

  /// Debounced live save: each entered hole is persisted to the server
  /// (in_progress card) shortly after entry, so the leaderboard moves
  /// hole by hole. Best-effort — the next save retries whatever failed.
  void _scheduleLiveSave() {
    if (_locked || _submitting) return;
    _saveTimer?.cancel();
    _saveTimer = Timer(const Duration(milliseconds: 800), _liveSave);
  }

  Future<void> _liveSave() async {
    final player = _player;
    if (player == null || _locked || _submitting) return;
    if (!_scores.any((s) => s != null)) return;
    setState(() {
      _saving = true;
      _saveFailed = false;
    });
    try {
      final card = await _api.submitCasualScorecard(
        widget.teeTime.id,
        player.discordId,
        List<int?>.from(_scores),
      );
      if (!mounted) return;
      setState(() {
        _saving = false;
        _existingStatus ??= card.status;
        _cardId ??= card.id;
      });
    } catch (_) {
      if (mounted) {
        setState(() {
          _saving = false;
          _saveFailed = true;
        });
      }
    }
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
    final toPar = _toParLabel();
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Submit scorecard?'),
        content: Text(
          'All scores entered are final.\n\n'
          '${golferDisplayName(_player!.displayName, _player!.golfplusHandle)}: '
          'total ${_total ?? '–'}'
          '${toPar.isNotEmpty ? ' ($toPar)' : ''}'
          '${_witnessCtrl.text.trim().isNotEmpty ? '\nWitness: ${_witnessCtrl.text.trim()}' : ''}',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('Keep editing'),
          ),
          // Opens above the dialog; the dialog is still here when the
          // review closes.
          if (_cardId != null)
            TextButton(
              onPressed: () => _openShotReview(),
              child: const Text('Review shots'),
            ),
          ElevatedButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            child: const Text('Submit'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    setState(() => _submitting = true);
    try {
      final card = await _api.submitCasualScorecard(
        widget.teeTime.id,
        _player!.discordId,
        _scores.map((s) => s!).toList(),
        complete: true,
        witnessName: _witnessCtrl.text,
      );
      if (mounted) {
        setState(() {
          _existingStatus = card.status;
          _cardId ??= card.id;
        });
        showSnack(context, 'Casual scorecard submitted.');
        Navigator.of(context).pop(true);
      }
    } on ApiException catch (e) {
      if (mounted) showSnack(context, friendlyApiMessage(e), error: true);
    } catch (e) {
      if (mounted) showSnack(context, 'Submit failed: $e', error: true);
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  /// Create the in-progress scorecard on first use (all-null scores are a
  /// valid live partial save) so shot tracking doesn't wait for a manually
  /// entered score. Returns the card id, or null on failure.
  Future<int?> _ensureCard() async {
    final player = _player;
    if (player == null || _locked || _submitting) return null;
    try {
      final card = await _api.submitCasualScorecard(
        widget.teeTime.id,
        player.discordId,
        List<int?>.from(_scores),
      );
      if (!mounted) return null;
      setState(() {
        _existingStatus ??= card.status;
        _cardId ??= card.id;
      });
      return _cardId;
    } on ApiException catch (e) {
      if (mounted) showSnack(context, friendlyApiMessage(e), error: true);
      return null;
    } catch (e) {
      if (mounted) {
        showSnack(context, 'Could not start shot tracking: $e', error: true);
      }
      return null;
    }
  }

  /// Opt-in shot tracking for the current hole. Always available — the
  /// scorecard is created on first use if needed. A finished (holed)
  /// tracking session writes its stroke count as the hole's score, so
  /// tracking doubles as score entry; an unfinished or untouched session
  /// leaves the score alone.
  Future<void> _openShotTracker() async {
    var cardId = _cardId;
    cardId ??= await _ensureCard();
    if (cardId == null || !mounted) return;
    final hole = _hole;
    final tracked = await showShotTracker(
      context: context,
      api: _api,
      cardId: cardId,
      courseName: widget.teeTime.course,
      holeNumber: hole + 1,
      par: _pars?[hole] ?? 4,
      strokes: _scores[hole],
    );
    if (tracked != null && mounted) {
      setState(() => _scores[hole] = tracked);
      _scheduleLiveSave();
    }
  }

  /// Read-only review of every hole's tracked shots, loaded fresh from
  /// the server — for checking the maps before submitting.
  Future<void> _openShotReview() async {
    final cardId = _cardId;
    if (cardId == null) return;
    await showShotReview(
      context: context,
      api: _api,
      cardId: cardId,
      courseName: widget.teeTime.course,
      pars: _pars ?? List.filled(_holeCount, 4),
      strokes: List.of(_scores),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
          title: Text('Enter scores — ${widget.teeTime.formatLabel}'),
          actions: [
            // Quiet entry to the shot-map review. Only once the scorecard
            // exists server-side (same gate as shot tracking).
            if (_cardId != null)
              IconButton(
                icon: const Icon(Icons.map_outlined),
                tooltip: 'Review tracked shots',
                onPressed: _openShotReview,
              ),
          ]),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _locked
              ? _lockedBody()
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
              'This round starts ${formatTeeTimeWhen(widget.teeTime.startsAt)}. '
              'Come back after you\'ve teed off to enter scores.',
              textAlign: TextAlign.center,
              style: const TextStyle(color: Colors.grey),
            ),
          ],
        ),
      ),
    );
  }

  Widget _entryBody() {
    final pars = _pars;
    final par = pars != null ? pars[_hole] : null;
    final complete = !_scores.any((s) => s == null);

    return Column(
      children: [
        // Header: player chips (every player in the round, always
        // visible) + running total / to-par. Tapping a chip switches to
        // that player's scorecard instantly.
        Container(
          padding: const EdgeInsets.all(12),
          color: Theme.of(context).colorScheme.surfaceContainerHighest,
          child: Row(
            children: [
              Expanded(
                child: SingleChildScrollView(
                  scrollDirection: Axis.horizontal,
                  child: Row(
                    children: widget.teeTime.players.map((p) {
                      final selected = p.discordId == _player?.discordId;
                      return Padding(
                        padding: const EdgeInsets.only(right: 8),
                        child: ChoiceChip(
                          label: Text(golferDisplayName(
                              p.displayName, p.golfplusHandle)),
                          selected: selected,
                          onSelected: (_) => _pickPlayer(p),
                        ),
                      );
                    }).toList(),
                  ),
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
        // Hole strip: hole number + score in standard golf notation.
        // Tap a hole to jump to it.
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
                Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Text(
                        'Hole ${_hole + 1}${par != null ? ' · Par $par' : ''}',
                        style: const TextStyle(
                            fontSize: 22,
                            fontWeight: FontWeight.bold)),
                    const SizedBox(width: 2),
                    Tooltip(
                      message: 'Track shots for this hole (optional)',
                      child: TextButton.icon(
                        onPressed: _openShotTracker,
                        icon: const Icon(Icons.timeline, size: 15),
                        label: const Text('Track shots',
                            style: TextStyle(fontSize: 12)),
                        style: TextButton.styleFrom(
                          foregroundColor: Colors.grey.shade600,
                          padding: const EdgeInsets.symmetric(
                              horizontal: 8),
                          minimumSize: Size.zero,
                          tapTargetSize:
                              MaterialTapTargetSize.shrinkWrap,
                        ),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 4),
                if (_saving)
                  const Text('Saving…',
                      style: TextStyle(fontSize: 12, color: Colors.grey))
                else if (_saveFailed)
                  const Text('Save failed — will retry on next hole',
                      style: TextStyle(fontSize: 12, color: Colors.red))
                else if (_existingStatus == 'in_progress' ||
                    _scores.any((s) => s != null))
                  const Text('⛳ Live — scores are on the leaderboard',
                      style: TextStyle(fontSize: 12, color: Colors.green)),
                const SizedBox(height: 16),
                if (par != null) _parRelativeButtons(par) else _numericButtons(),
                const SizedBox(height: 12),
                OutlinedButton.icon(
                  onPressed: _customScore,
                  icon: const Icon(Icons.edit),
                  label: const Text('Custom score (1–15)'),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _witnessCtrl,
                  maxLength: 80,
                  decoration: const InputDecoration(
                    labelText: 'Witness (optional)',
                    hintText: 'Who saw you play this round?',
                    border: OutlineInputBorder(),
                    isDense: true,
                    counterText: '',
                  ),
                ),
                if (_existingStatus != null) ...[
                  const SizedBox(height: 8),
                  Text(
                      'Previously saved: ${_existingStatus == 'in_progress' ? 'in progress' : _existingStatus}',
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

  /// Quick buttons relative to par: Albatross(-3), Eagle(-2), Birdie(-1),
  /// Par(0), Bogey(+1), Double(+2), Triple(+3).
  Widget _parRelativeButtons(int par) {
    const deltas = [-3, -2, -1, 0, 1, 2, 3];
    const names = {
      -3: 'Albatross',
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
