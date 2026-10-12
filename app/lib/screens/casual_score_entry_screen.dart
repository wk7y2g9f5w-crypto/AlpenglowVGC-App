import 'dart:async';

import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../services/auth.dart';
import '../widgets/common.dart';
import '../widgets/scorecard_grid.dart';
import '../widgets/shot_tracker.dart';
import 'shot_review_screen.dart';

/// Score entry for a casual stroke / best-ball round.
///
/// Grid scorecard: every player's card on one tab, tap a cell to enter.
/// A casual round is a single 18-hole card per player. Cards are
/// self-attested — submitting marks the card verified directly, and it
/// feeds the same optional stats + handicap as tournament cards.
class CasualScoreEntryScreen extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;
  final CasualTeeTime teeTime;
  final String myDiscordId;

  const CasualScoreEntryScreen({
    super.key,
    required this.auth,
    required this.settings,
    required this.teeTime,
    required this.myDiscordId,
  });

  @override
  State<CasualScoreEntryScreen> createState() =>
      _CasualScoreEntryScreenState();
}

/// One player's card in the grid.
class _CardState {
  final CasualPlayer player;
  List<int?> scores;
  String? status; // null | in_progress | verified
  int? cardId;
  String witness = '';
  bool editedAfterSubmit = false;

  _CardState(this.player)
      : scores = List<int?>.filled(_CasualScoreEntryScreenState._holeCount, null);

  bool get complete => !scores.any((s) => s == null);
  bool get submitted => status == 'verified';
  bool get submittable => complete && (!submitted || editedAfterSubmit);
  int get entered => scores.where((s) => s != null).length;
}

class _CasualScoreEntryScreenState extends State<CasualScoreEntryScreen> {
  static const _holeCount = 18;

  late List<_CardState> _cards;
  bool _loading = true;
  bool _submitting = false;
  final Map<int, Timer> _saveTimers = {};
  bool _saving = false;
  bool _saveFailed = false;

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

  String _cardTitle(_CardState card) => golferDisplayName(
      card.player.displayName, card.player.golfplusHandle);

  @override
  void initState() {
    super.initState();
    _cards = [for (final p in widget.teeTime.players) _CardState(p)];
    Future.wait([
      for (var i = 0; i < _cards.length; i++) _loadCard(i)
    ]).then((_) {
      if (mounted) setState(() => _loading = false);
    });
  }

  @override
  void dispose() {
    for (final t in _saveTimers.values) {
      t.cancel();
    }
    super.dispose();
  }

  Future<void> _loadCard(int i) async {
    final card = _cards[i];
    try {
      final sc = await _api.getCasualScorecard(widget.teeTime.id,
          playerDiscordId: card.player.discordId);
      if (!mounted) return;
      if (sc != null && sc.scores.length == _holeCount) {
        setState(() {
          card.scores = sc.scores.map<int?>((s) => s).toList();
          card.status = sc.status;
          card.witness = sc.witnessName ?? '';
          card.cardId = sc.id;
          card.editedAfterSubmit = false;
        });
      }
    } on ApiException {
      // No existing card or unreadable; leave the column blank.
    }
  }

  void _onScore(int i, int hole, int? score) {
    final card = _cards[i];
    setState(() {
      card.scores[hole] = score;
      if (card.submitted) card.editedAfterSubmit = true;
    });
    _scheduleSave(i);
  }

  /// Debounced live save per card: each entered hole is persisted to the
  /// server (in_progress card) shortly after entry, so the casual
  /// leaderboard moves hole by hole. Best-effort — the next save retries.
  void _scheduleSave(int i) {
    if (_locked || _submitting) return;
    _saveTimers[i]?.cancel();
    _saveTimers[i] =
        Timer(const Duration(milliseconds: 800), () => _liveSave(i));
  }

  Future<void> _liveSave(int i) async {
    final card = _cards[i];
    if (_locked || _submitting) return;
    if (!card.scores.any((s) => s != null)) return;
    setState(() {
      _saving = true;
      _saveFailed = false;
    });
    try {
      final sc = await _api.submitCasualScorecard(
        widget.teeTime.id,
        card.player.discordId,
        List<int?>.from(card.scores),
      );
      if (!mounted) return;
      setState(() {
        _saving = false;
        card.status ??= sc.status;
        card.cardId ??= sc.id;
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

  /// Create the in-progress scorecard on first use (all-null scores are a
  /// valid live partial save) so shot tracking doesn't wait for a manually
  /// entered score. Returns the card id, or null on failure.
  Future<int?> _ensureCard(int i) async {
    final card = _cards[i];
    if (card.cardId != null) return card.cardId;
    if (_locked || _submitting) return null;
    try {
      final sc = await _api.submitCasualScorecard(
        widget.teeTime.id,
        card.player.discordId,
        List<int?>.from(card.scores),
      );
      if (!mounted) return null;
      setState(() {
        card.status ??= sc.status;
        card.cardId ??= sc.id;
      });
      return card.cardId;
    } on ApiException catch (e) {
      if (mounted) showSnack(context, friendlyApiMessage(e), error: true);
      return null;
    } catch (e) {
      if (mounted) {
        showSnack(context, 'Could not start shot tracking: $e',
            error: true);
      }
      return null;
    }
  }

  /// Opt-in shot tracking for one card + hole. A finished (holed) tracking
  /// session writes its stroke count as the hole's score, so tracking
  /// doubles as score entry.
  Future<void> _trackShots(int i, int hole) async {
    final card = _cards[i];
    var cardId = card.cardId;
    cardId ??= await _ensureCard(i);
    if (cardId == null || !mounted) return;
    final tracked = await showShotTracker(
      context: context,
      api: _api,
      cardId: cardId,
      courseName: widget.teeTime.course,
      holeNumber: hole + 1,
      par: _pars?[hole] ?? 4,
      strokes: card.scores[hole],
    );
    if (tracked != null && mounted) {
      _onScore(i, hole, tracked);
    }
  }

  /// Read-only review of a card's tracked shots.
  Future<void> _reviewShots(int i) async {
    final card = _cards[i];
    if (card.cardId == null) return;
    await showShotReview(
      context: context,
      api: _api,
      cardId: card.cardId!,
      courseName: widget.teeTime.course,
      pars: _pars ?? List.filled(_holeCount, 4),
      strokes: List.of(card.scores),
    );
  }

  int? _total(_CardState card) {
    if (!card.complete) return null;
    return card.scores.fold<int>(0, (a, b) => a + (b ?? 0));
  }

  String _toParLabel(_CardState card) {
    final pars = _pars;
    final total = _total(card);
    if (pars == null || total == null) return '';
    final tp = total - pars.fold<int>(0, (a, b) => a + b);
    if (tp == 0) return 'E';
    return tp > 0 ? '+$tp' : '$tp';
  }

  /// One submit for everyone: every complete, not-yet-submitted card (plus
  /// edited submitted cards) goes in a single confirmation.
  Future<void> _submitAll() async {
    final result = await showDialog<Map<int, String>>(
      context: context,
      builder: (_) => _SubmitAllDialog(
        cards: _cards,
        titles: [for (final c in _cards) _cardTitle(c)],
        totals: [for (final c in _cards) _total(c)],
        toParLabels: [for (final c in _cards) _toParLabel(c)],
      ),
    );
    if (result == null || result.isEmpty) return;
    setState(() => _submitting = true);
    var ok = 0;
    final failures = <String>[];
    for (final entry in result.entries) {
      final card = _cards[entry.key];
      _saveTimers[entry.key]?.cancel();
      try {
        final sc = await _api.submitCasualScorecard(
          widget.teeTime.id,
          card.player.discordId,
          card.scores.map((s) => s!).toList(),
          complete: true,
          witnessName: entry.value,
        );
        card.status = sc.status;
        card.cardId ??= sc.id;
        card.editedAfterSubmit = false;
        card.witness = entry.value;
        ok++;
      } on ApiException catch (e) {
        failures.add('${_cardTitle(card)}: ${friendlyApiMessage(e)}');
      } catch (e) {
        failures.add('${_cardTitle(card)}: $e');
      }
    }
    if (!mounted) return;
    setState(() => _submitting = false);
    if (failures.isNotEmpty) {
      showSnack(
          context,
          'Submitted $ok card${ok == 1 ? '' : 's'}. Failed: '
          '${failures.join('; ')}',
          error: true);
    } else {
      showSnack(
          context,
          ok == 1 ? 'Casual scorecard submitted.' : '$ok scorecards submitted.');
    }
    if (_cards.every((c) => c.submitted)) {
      Navigator.of(context).pop(true);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text('Enter scores — ${widget.teeTime.formatLabel}')),
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
    final readyCount = _cards.where((c) => c.submittable).length;
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.only(top: 6, bottom: 2),
          child: _saving
              ? const Text('Saving…',
                  style: TextStyle(fontSize: 12, color: Colors.grey))
              : _saveFailed
                  ? const Text('Save failed — will retry on next entry',
                      style: TextStyle(fontSize: 12, color: Colors.red))
                  : _cards.any((c) => c.entered > 0)
                      ? const Text('⛳ Live — scores save as you enter them',
                          style:
                              TextStyle(fontSize: 12, color: Colors.green))
                      : const SizedBox(height: 15),
        ),
        Expanded(
          child: ScorecardGrid(
            cards: [
              for (final card in _cards)
                ScorecardGridCard(
                  title: _cardTitle(card),
                  scores: card.scores,
                  submitted: card.submitted,
                  hasCard: card.cardId != null,
                ),
            ],
            pars: _pars,
            holeCount: _holeCount,
            onScore: _onScore,
            onTrackShots: _trackShots,
            onReviewShots: _reviewShots,
          ),
        ),
        SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: SizedBox(
              width: double.infinity,
              child: ElevatedButton(
                onPressed:
                    (readyCount > 0 && !_submitting) ? _submitAll : null,
                child: _submitting
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2))
                    : Text(readyCount == 0
                        ? 'Enter all $_holeCount holes to submit'
                        : 'Submit all cards ($readyCount ready)'),
              ),
            ),
          ),
        ),
      ],
    );
  }
}

/// Confirmation for submit-all: lists every card with its state; complete
/// cards get a witness field, incomplete ones are noted as staying in
/// progress. Returns cardIndex → witness text for the cards to submit.
class _SubmitAllDialog extends StatefulWidget {
  final List<_CardState> cards;
  final List<String> titles;
  final List<int?> totals;
  final List<String> toParLabels;

  const _SubmitAllDialog({
    required this.cards,
    required this.titles,
    required this.totals,
    required this.toParLabels,
  });

  @override
  State<_SubmitAllDialog> createState() => _SubmitAllDialogState();
}

class _SubmitAllDialogState extends State<_SubmitAllDialog> {
  late final Map<int, TextEditingController> _witnessCtrls = {
    for (var i = 0; i < widget.cards.length; i++)
      if (widget.cards[i].submittable)
        i: TextEditingController(text: widget.cards[i].witness),
  };

  @override
  void dispose() {
    for (final c in _witnessCtrls.values) {
      c.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Submit scorecards?'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'Casual cards are self-attested — submitting marks them '
              'verified.',
              style: TextStyle(fontSize: 13, color: Colors.grey),
            ),
            const SizedBox(height: 12),
            for (var i = 0; i < widget.cards.length; i++) ...[
              Row(
                children: [
                  Expanded(
                    child: Text(
                      widget.titles[i],
                      style: const TextStyle(fontWeight: FontWeight.bold),
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  Text(_statusLine(i),
                      style: const TextStyle(fontSize: 13)),
                ],
              ),
              if (widget.cards[i].submittable)
                Padding(
                  padding: const EdgeInsets.only(top: 4, bottom: 10),
                  child: TextField(
                    controller: _witnessCtrls[i],
                    maxLength: 80,
                    decoration: const InputDecoration(
                      labelText: 'Witness (optional)',
                      border: OutlineInputBorder(),
                      isDense: true,
                      counterText: '',
                    ),
                  ),
                )
              else
                const SizedBox(height: 10),
            ],
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Keep editing'),
        ),
        ElevatedButton(
          onPressed: () => Navigator.of(context).pop({
            for (final e in _witnessCtrls.entries) e.key: e.value.text,
          }),
          child: const Text('Submit'),
        ),
      ],
    );
  }

  String _statusLine(int i) {
    final card = widget.cards[i];
    if (card.submittable) {
      final tp = widget.toParLabels[i];
      final base = 'total ${widget.totals[i] ?? '–'}${tp.isEmpty ? '' : ' ($tp)'}';
      return card.submitted ? '$base · update' : base;
    }
    if (card.submitted) return 'already submitted ✓';
    return '${card.entered}/${_CasualScoreEntryScreenState._holeCount} holes — stays in progress';
  }
}
