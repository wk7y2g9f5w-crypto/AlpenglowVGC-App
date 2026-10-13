import 'dart:async';

import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../services/auth.dart';
import '../widgets/common.dart';
import '../widgets/scorecard_grid.dart';
import '../widgets/shot_tracker.dart';
import 'shot_review_screen.dart';

class ScoreEntryScreen extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;
  final Tournament tournament;
  final TeeTime teeTime;
  final String myDiscordId;
  // Team mode (alt-shot / scramble): enter the shared team card instead of
  // a player card.
  final int? teamId;
  final String? teamName;

  const ScoreEntryScreen({
    super.key,
    required this.auth,
    required this.settings,
    required this.tournament,
    required this.teeTime,
    required this.myDiscordId,
    this.teamId,
    this.teamName,
  });

  @override
  State<ScoreEntryScreen> createState() => _ScoreEntryScreenState();
}

/// One card in the grid: a player's card, or the shared team card.
class _CardState {
  final TeeTimePlayer? player; // null in shared-team mode
  final int? teamId; // the team this card files under (team formats)
  final String? teamName;
  List<int?> scores;
  String? status; // null | in_progress | pending | verified
  int? cardId;
  String witness = '';
  bool editedAfterSubmit = false;

  _CardState(this.player, int holeCount, {this.teamId, this.teamName})
      : scores = List<int?>.filled(holeCount, null);

  bool get complete => !scores.any((s) => s == null);
  bool get submitted => status != null && status != 'in_progress';
  bool get submittable => complete && (!submitted || editedAfterSubmit);
  int get entered => scores.where((s) => s != null).length;
}

class _ScoreEntryScreenState extends State<ScoreEntryScreen> {
  late List<_CardState> _cards;
  late int _roundNumber;
  bool _loading = true;
  bool _submitting = false;
  bool _isCrew = false;
  bool _isAdmin = false;
  List<String> _witnessOptions = [];
  final Map<int, Timer> _saveTimers = {};
  bool _saving = false;
  bool _saveFailed = false;
  int _loadSeq = 0;

  bool get _isTeamMode => widget.teamId != null;

  bool get _isSharedCard =>
      const {'alt_shot', 'scramble'}.contains(widget.tournament.format);

  /// Solo alt-shot: only one team on this tee time — its card needs a
  /// witness to count (Cayden 2026-10-13).
  bool get _soloAltShot {
    if (widget.tournament.format != 'alt_shot' || !_isSharedCard) {
      return false;
    }
    final teamIds = <int>{
      for (final p in widget.teeTime.players)
        if (p.teamId != null) p.teamId!,
    };
    return teamIds.length == 1;
  }

  /// Witness picker options: the tournament's registered players, labeled
  /// the same way as the team-formation picker (Golf+ handle first).
  Future<void> _loadWitnessOptions() async {
    try {
      final players = await _api.getTournamentPlayers(widget.tournament.id);
      if (!mounted) return;
      final labels = <String>{};
      for (final p in players) {
        if (!p.registered) continue;
        final label =
            (p.golfplusHandle.isNotEmpty ? p.golfplusHandle : p.displayName)
                .trim();
        if (label.isNotEmpty) labels.add(label);
      }
      setState(() => _witnessOptions = labels.toList()..sort());
    } catch (_) {
      // The picker falls back to free text when the list can't load.
    }
  }

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

  String _cardTitle(_CardState card) {
    if (_isSharedCard) {
      return card.teamName ?? widget.teamName ?? 'Team card';
    }
    final p = card.player;
    if (p == null) return 'Card';
    return golferDisplayName(p.displayName, p.golfplusHandle);
  }

  /// Card identity for API calls. Shared team cards file under the team
  /// only; best ball files player + team; stroke files the player.
  String? _cardPlayerId(_CardState card) =>
      _isTeamMode && _isSharedCard ? null : card.player?.discordId;

  int? _cardTeamId(_CardState card) => card.teamId ?? widget.teamId;

  @override
  void initState() {
    super.initState();
    _roundNumber = widget.teeTime.roundNumber;
    if (_isSharedCard) {
      // One column per team on the sheet — the whole foursome's cards
      // are on this one grid (Cayden 2026-10-12).
      final seen = <int>{};
      _cards = [];
      for (final p in widget.teeTime.players) {
        final tid = p.teamId ?? widget.teamId;
        if (tid != null && seen.add(tid)) {
          _cards.add(_CardState(null, _holeCount,
              teamId: tid, teamName: p.teamName ?? widget.teamName));
        }
      }
      if (_cards.isEmpty && widget.teamId != null) {
        _cards = [
          _CardState(null, _holeCount,
              teamId: widget.teamId, teamName: widget.teamName)
        ];
      }
    } else {
      _cards = [
        for (final p in widget.teeTime.players)
          _CardState(p, _holeCount,
              teamId: _isTeamMode ? (p.teamId ?? widget.teamId) : null,
              teamName: p.teamName)
      ];
    }
    _loadAll(_roundNumber).then((_) {
      if (mounted) setState(() => _loading = false);
    });
    _loadCrew();
    _loadWitnessOptions();
  }

  @override
  void dispose() {
    for (final t in _saveTimers.values) {
      t.cancel();
    }
    super.dispose();
  }

  Future<void> _loadCrew() async {
    try {
      final me = await _api.getMe();
      if (mounted) {
        setState(() {
          _isCrew = me.isCrew;
          _isAdmin = me.isAdmin;
        });
        _ensureSelectableRound();
      }
    } catch (_) {
      // Leave as non-crew; the server still enforces the lock.
    }
  }

  Future<void> _loadAll(int round) async {
    final seq = ++_loadSeq;
    await Future.wait([
      for (var i = 0; i < _cards.length; i++) _loadCard(i, round, seq)
    ]);
  }

  Future<void> _loadCard(int i, int round, int seq) async {
    final card = _cards[i];
    try {
      final sc = await _api.getScorecard(
        widget.teeTime.id,
        roundNumber: round,
        playerDiscordId: _cardPlayerId(card),
        teamId: _cardTeamId(card),
      );
      if (!mounted || seq != _loadSeq) return;
      if (sc != null && sc.scores.length == _holeCount) {
        setState(() {
          card.scores = sc.scores.map<int?>((s) => s).toList();
          card.status = sc.status;
          card.witness = sc.witnessName ?? '';
          card.cardId = sc.id;
          card.editedAfterSubmit = false;
        });
      } else {
        setState(() {
          card.scores = List<int?>.filled(_holeCount, null);
          card.status = null;
          card.witness = '';
          card.cardId = null;
          card.editedAfterSubmit = false;
        });
      }
    } on ApiException {
      // No existing card or unreadable; leave the column blank.
    }
  }

  void _pickRound(int rn) {
    if (rn == _roundNumber) return;
    for (final t in _saveTimers.values) {
      t.cancel();
    }
    _saveTimers.clear();
    setState(() {
      _roundNumber = rn;
      _loading = true;
    });
    _loadAll(rn).then((_) {
      if (mounted) setState(() => _loading = false);
    });
  }

  String _roundLabel(int rn) {
    final rounds = widget.tournament.rounds;
    if (rounds.length >= rn) {
      final r = rounds[rn - 1];
      final parts = <String>['Round $rn'];
      final summary = r.settingsSummary;
      if (summary.isNotEmpty) parts.add(summary);
      if (r.datesSummary.isNotEmpty) parts.add(r.datesSummary);
      if (!r.hasStarted) {
        parts.add('not started yet');
      } else if (r.hasEnded) {
        parts.add('ended');
      }
      return parts.join(' — ');
    }
    return 'Round $rn';
  }

  /// A round can be picked when its window has opened. After the window
  /// closes, only crew can still enter cards for it.
  bool _roundSelectable(int rn) {
    final rounds = widget.tournament.rounds;
    if (rounds.length >= rn) {
      final r = rounds[rn - 1];
      if (!r.hasStarted) return false;
      if (r.hasEnded && !_isCrew) return false;
    }
    return true;
  }

  /// After crew status loads, move off a round the viewer can't play.
  void _ensureSelectableRound() {
    if (_roundSelectable(_roundNumber)) return;
    for (var rn = 1; rn <= widget.tournament.numRounds; rn++) {
      if (_roundSelectable(rn)) {
        _pickRound(rn);
        return;
      }
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
  /// server (in_progress card) shortly after entry, so the leaderboard
  /// moves hole by hole. Best-effort — the next save retries failures.
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
      final sc = await _api.submitScorecard(
        widget.teeTime.id,
        _cardPlayerId(card),
        List<int?>.from(card.scores),
        roundNumber: _roundNumber,
        teamId: _cardTeamId(card),
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
      final sc = await _api.submitScorecard(
        widget.teeTime.id,
        _cardPlayerId(card),
        List<int?>.from(card.scores),
        roundNumber: _roundNumber,
        teamId: _cardTeamId(card),
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
      courseName: widget.tournament.course ?? '',
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
      courseName: widget.tournament.course ?? '',
      pars: _pars ?? List.filled(_holeCount, 4),
      strokes: List.of(card.scores),
    );
  }

  Future<void> _verifyCard(int i) async {
    final card = _cards[i];
    if (card.cardId == null) return;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Verify round?'),
        content: Text(
          'This marks ${_cardTitle(card)}\'s round $_roundNumber as '
          'verified. It will count toward the leaderboard.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('Cancel'),
          ),
          ElevatedButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            child: const Text('Verify'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    try {
      await _api.verifyScorecard(card.cardId!);
      if (mounted) {
        setState(() => card.status = 'verified');
        showSnack(context, 'Round verified.');
      }
    } catch (e) {
      if (mounted) showSnack(context, 'Verify failed: $e');
    }
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
  /// crew-edited submitted cards) goes in a single confirmation.
  Future<void> _submitAll() async {
    final result = await showDialog<Map<int, String>>(
      context: context,
      builder: (_) => _SubmitAllDialog(
        cards: _cards,
        titles: [for (final c in _cards) _cardTitle(c)],
        totals: [for (final c in _cards) _total(c)],
        toParLabels: [for (final c in _cards) _toParLabel(c)],
        holeCount: _holeCount,
        roundNumber: _roundNumber,
        witnessOptions: _witnessOptions,
        witnessRequired: _soloAltShot,
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
        final sc = await _api.submitScorecard(
          widget.teeTime.id,
          _cardPlayerId(card),
          card.scores.map((s) => s!).toList(),
          roundNumber: _roundNumber,
          witnessName: entry.value,
          complete: true,
          teamId: _cardTeamId(card),
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
          ok == 1
              ? 'Round $_roundNumber scorecard submitted.'
              : '$ok scorecards submitted.');
    }
    if (_cards.every((c) => c.submitted)) {
      Navigator.of(context).pop();
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

  Widget _entryBody() {
    final multi = widget.tournament.isMultiRound;
    final readyCount = _cards.where((c) => c.submittable).length;
    final allSubmitted =
        _cards.isNotEmpty && _cards.every((c) => c.submitted);
    return Column(
      children: [
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
                  enabled: _roundSelectable(i + 1),
                  child: Text(_roundLabel(i + 1),
                      overflow: TextOverflow.ellipsis),
                ),
              ),
              onChanged: (rn) {
                if (rn != null) _pickRound(rn);
              },
            ),
          ),
        // Live-save status: scores hit the leaderboard as entered.
        Padding(
          padding: const EdgeInsets.only(top: 6, bottom: 2),
          child: _saving
              ? const Text('Saving…',
                  style: TextStyle(fontSize: 12, color: Colors.grey))
              : _saveFailed
                  ? const Text('Save failed — will retry on next entry',
                      style: TextStyle(fontSize: 12, color: Colors.red))
                  : _cards.any((c) => c.status == 'in_progress' || c.entered > 0)
                      ? const Text('⛳ Live — scores are on the leaderboard',
                          style:
                              TextStyle(fontSize: 12, color: Colors.green))
                      : const SizedBox(height: 15),
        ),
        if (allSubmitted && !_isCrew)
          const Padding(
            padding: EdgeInsets.symmetric(horizontal: 16, vertical: 4),
            child: Text(
              'All scorecards are submitted — only crew (admins, mods, '
              'tournament directors) can change scores.',
              textAlign: TextAlign.center,
              style: TextStyle(color: Colors.grey, fontSize: 13),
            ),
          ),
        Expanded(
          child: ScorecardGrid(
            key: ValueKey(_roundNumber),
            cards: [
              for (final card in _cards)
                ScorecardGridCard(
                  title: _cardTitle(card),
                  scores: card.scores,
                  locked: card.submitted && !_isCrew,
                  submitted: card.submitted,
                  canVerify: _isAdmin &&
                      card.status == 'pending' &&
                      card.cardId != null,
                  hasCard: card.cardId != null,
                ),
            ],
            pars: _pars,
            holeCount: _holeCount,
            onScore: _onScore,
            onTrackShots: _trackShots,
            onReviewShots: _reviewShots,
            onVerifyCard: _isAdmin ? _verifyCard : null,
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
/// cards get a witness picker (the tournament's registered players, or
/// type a name — the team-formation pattern), incomplete ones are noted
/// as staying in progress. Returns cardIndex → witness text for the
/// cards to submit. [witnessRequired] (a solo alt-shot team, Cayden
/// 2026-10-13) blocks submitting until every card to submit has one.
class _SubmitAllDialog extends StatefulWidget {
  final List<_CardState> cards;
  final List<String> titles;
  final List<int?> totals;
  final List<String> toParLabels;
  final int holeCount;
  final int roundNumber;
  final List<String> witnessOptions;
  final bool witnessRequired;

  const _SubmitAllDialog({
    required this.cards,
    required this.titles,
    required this.totals,
    required this.toParLabels,
    required this.holeCount,
    required this.roundNumber,
    this.witnessOptions = const [],
    this.witnessRequired = false,
  });

  @override
  State<_SubmitAllDialog> createState() => _SubmitAllDialogState();
}

class _SubmitAllDialogState extends State<_SubmitAllDialog> {
  // Controllers handed to us by each Autocomplete's field view — the
  // source of truth for the witness text at submit time (they're owned
  // by the Autocomplete, so no dispose here).
  final Map<int, TextEditingController> _witnessCtrls = {};
  bool _witnessError = false;

  String _witnessText(int i) =>
      _witnessCtrls[i]?.text ?? widget.cards[i].witness;

  void _trySubmit() {
    if (widget.witnessRequired) {
      final missing = widget.cards.asMap().entries.any(
          (e) => e.value.submittable && _witnessText(e.key).trim().isEmpty);
      if (missing) {
        setState(() => _witnessError = true);
        return;
      }
    }
    Navigator.of(context).pop({
      for (var i = 0; i < widget.cards.length; i++)
        if (widget.cards[i].submittable) i: _witnessText(i),
    });
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
              'Submitted scores are final — after this, only crew can '
              'change them.',
              style: TextStyle(fontSize: 13, color: Colors.grey),
            ),
            if (widget.witnessRequired)
              const Padding(
                padding: EdgeInsets.only(top: 8),
                child: Text(
                  'Only one team is on this tee time, so its card needs '
                  'a witness to count.',
                  style: TextStyle(fontSize: 13, color: Colors.grey),
                ),
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
                  child: Autocomplete<String>(
                    initialValue:
                        TextEditingValue(text: widget.cards[i].witness),
                    optionsBuilder: (tv) {
                      final q = tv.text.trim().toLowerCase();
                      if (q.isEmpty) return widget.witnessOptions;
                      return widget.witnessOptions
                          .where((o) => o.toLowerCase().contains(q));
                    },
                    fieldViewBuilder:
                        (context, ctrl, focusNode, onFieldSubmitted) {
                      _witnessCtrls[i] = ctrl;
                      return TextField(
                        controller: ctrl,
                        focusNode: focusNode,
                        maxLength: 80,
                        decoration: InputDecoration(
                          labelText: widget.witnessRequired
                              ? 'Witness (required)'
                              : 'Witness (optional)',
                          hintText: 'Pick a player or type a name',
                          border: const OutlineInputBorder(),
                          isDense: true,
                          counterText: '',
                          errorText: _witnessError && widget.witnessRequired
                              ? 'A witness is required for a solo team'
                              : null,
                        ),
                        onSubmitted: (_) => onFieldSubmitted(),
                      );
                    },
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
          onPressed: _trySubmit,
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
    return '${card.entered}/${widget.holeCount} holes — stays in progress';
  }
}
