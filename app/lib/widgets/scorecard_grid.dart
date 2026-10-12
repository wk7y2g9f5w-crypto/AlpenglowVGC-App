import 'package:flutter/material.dart';

import 'common.dart';

/// One column of the scorecard grid: a single card (a player's, or a
/// team's shared card) with its per-hole scores.
class ScorecardGridCard {
  final String title;
  final List<int?> scores;

  /// View-only column (e.g. a submitted card and the viewer isn't crew).
  final bool locked;

  /// The card has been submitted (pending/verified) — shown with a check.
  final bool submitted;

  /// Admin may verify this card (pending + card exists server-side).
  final bool canVerify;

  /// A server-side card exists, so tracked shots can be reviewed.
  final bool hasCard;

  const ScorecardGridCard({
    required this.title,
    required this.scores,
    this.locked = false,
    this.submitted = false,
    this.canVerify = false,
    this.hasCard = false,
  });

  bool get hasAnyScore => scores.any((s) => s != null);

  int? get runningTotal =>
      hasAnyScore ? scores.fold<int>(0, (a, s) => a + (s ?? 0)) : null;
}

/// Paper-scorecard style entry grid: holes run down the left (with pars),
/// one column per card across the top, every score visible at once so one
/// person can keep the whole group's cards. Tapping a cell raises the
/// quick-input panel (1–10, tinted against par) like a keyboard; picking a
/// score drops it away again.
class ScorecardGrid extends StatefulWidget {
  final List<ScorecardGridCard> cards;
  final List<int>? pars;
  final int holeCount;

  /// Whether a score can be cleared back to blank (false when a card's
  /// scores are never null, e.g. the alt-shot records screen).
  final bool allowClear;

  /// Highest value the Custom… dialog accepts.
  final int customMax;

  final void Function(int card, int hole, int? score) onScore;
  final void Function(int card, int hole)? onTrackShots;
  final void Function(int card)? onReviewShots;
  final void Function(int card)? onVerifyCard;

  const ScorecardGrid({
    super.key,
    required this.cards,
    required this.pars,
    required this.holeCount,
    required this.onScore,
    this.allowClear = true,
    this.customMax = 15,
    this.onTrackShots,
    this.onReviewShots,
    this.onVerifyCard,
  });

  @override
  State<ScorecardGrid> createState() => _ScorecardGridState();
}

class _ScorecardGridState extends State<ScorecardGrid> {
  int? _selCard;
  int? _selHole;

  static const double _labelW = 54;
  static const double _rowH = 44;

  int? _par(int hole) {
    final pars = widget.pars;
    if (pars != null && hole < pars.length) return pars[hole];
    return null;
  }

  void _tapCell(int card, int hole) {
    if (widget.cards[card].locked) return;
    setState(() {
      if (_selCard == card && _selHole == hole) {
        _selCard = null;
        _selHole = null;
      } else {
        _selCard = card;
        _selHole = hole;
      }
    });
  }

  void _closePanel() => setState(() {
        _selCard = null;
        _selHole = null;
      });

  void _choose(int? score) {
    final c = _selCard, h = _selHole;
    if (c == null || h == null) return;
    _closePanel();
    widget.onScore(c, h, score);
  }

  Future<void> _customScore() async {
    final c = _selCard, h = _selHole;
    if (c == null || h == null) return;
    final ctrl = TextEditingController(
        text: widget.cards[c].scores[h]?.toString() ?? '');
    final val = await showDialog<int>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('Hole ${h + 1} — custom score'),
        content: TextField(
          controller: ctrl,
          keyboardType: TextInputType.number,
          autofocus: true,
          decoration: InputDecoration(
              labelText: 'Strokes (1–${widget.customMax})',
              border: const OutlineInputBorder()),
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(ctx).pop(),
              child: const Text('Cancel')),
          ElevatedButton(
            onPressed: () {
              final n = int.tryParse(ctrl.text.trim());
              if (n == null || n < 1 || n > widget.customMax) {
                showSnack(ctx,
                    'Enter a number from 1 to ${widget.customMax}.',
                    error: true);
                return;
              }
              Navigator.of(ctx).pop(n);
            },
            child: const Text('Set'),
          ),
        ],
      ),
    );
    if (val != null) _choose(val);
  }

  /// Tint for a quick-input button: par is off-white, under-par goes green
  /// and over-par goes red, darkening with distance from par.
  static Color? scoreTint(int score, int? par) {
    if (par == null) return null;
    if (score == par) return const Color(0xFFF5F5F0); // off-white
    final dist = (score - par).abs().clamp(1, 4);
    final shade = 100 * dist; // 100, 200, 300, 400
    return score < par ? Colors.green[shade] : Colors.red[shade];
  }

  String _toParLabel(List<int?> scores) {
    final pars = widget.pars;
    if (pars == null) return '';
    var diff = 0;
    var any = false;
    for (var i = 0; i < scores.length && i < pars.length; i++) {
      final s = scores[i];
      if (s != null) {
        diff += s - pars[i];
        any = true;
      }
    }
    if (!any) return '';
    if (diff == 0) return 'E';
    return diff > 0 ? '+$diff' : '$diff';
  }

  /// Sum of a nine (start..start+8), or null until all nine are entered.
  int? _nineTotal(List<int?> scores, int start) {
    if (scores.length < start + 9) return null;
    final nine = scores.skip(start).take(9);
    if (nine.any((s) => s == null)) return null;
    return nine.fold<int>(0, (a, s) => a + (s ?? 0));
  }

  @override
  Widget build(BuildContext context) {
    // Drop a stale selection (cards reloaded, column locked, etc.).
    if (_selCard != null &&
        (_selCard! >= widget.cards.length ||
            widget.cards[_selCard!].locked)) {
      _selCard = null;
      _selHole = null;
    }
    final scheme = Theme.of(context).colorScheme;
    return LayoutBuilder(builder: (context, constraints) {
      final avail = constraints.maxWidth - _labelW;
      final colW = widget.cards.isEmpty
          ? avail
          : (avail / widget.cards.length).clamp(64.0, 200.0);
      final totalW = _labelW + colW * widget.cards.length;
      return Column(
        children: [
          Expanded(
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: SizedBox(
                width: totalW,
                child: Column(
                  children: [
                    _headerRow(colW, scheme),
                    const Divider(height: 1),
                    Expanded(
                      child: SingleChildScrollView(
                        child: Column(children: _rows(colW, scheme)),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
          AnimatedSize(
            duration: const Duration(milliseconds: 200),
            curve: Curves.easeOut,
            alignment: Alignment.bottomCenter,
            child: _selCard == null ? const SizedBox(width: double.infinity) : _panel(scheme),
          ),
        ],
      );
    });
  }

  Widget _headerRow(double colW, ColorScheme scheme) {
    return Row(
      children: [
        SizedBox(
          width: _labelW,
          child: const Padding(
            padding: EdgeInsets.symmetric(vertical: 6),
            child: Column(
              children: [
                Text('HOLE',
                    style:
                        TextStyle(fontSize: 10, fontWeight: FontWeight.bold)),
                Text('PAR',
                    style: TextStyle(fontSize: 10, color: Colors.grey)),
              ],
            ),
          ),
        ),
        for (var c = 0; c < widget.cards.length; c++)
          SizedBox(
            width: colW,
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 6, horizontal: 4),
              child: Column(
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      if (widget.cards[c].locked)
                        const Icon(Icons.lock, size: 12, color: Colors.grey),
                      if (widget.cards[c].submitted &&
                          !widget.cards[c].locked)
                        const Icon(Icons.check_circle,
                            size: 12, color: Colors.green),
                      Flexible(
                        child: Text(
                          widget.cards[c].title,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          textAlign: TextAlign.center,
                          style: const TextStyle(
                              fontSize: 13, fontWeight: FontWeight.bold),
                        ),
                      ),
                    ],
                  ),
                  Text(
                    widget.cards[c].runningTotal == null
                        ? '–'
                        : '${widget.cards[c].runningTotal}'
                            '${_toParLabel(widget.cards[c].scores).isEmpty ? '' : ' (${_toParLabel(widget.cards[c].scores)})'}',
                    style: TextStyle(
                        fontSize: 12,
                        color: scheme.onSurfaceVariant,
                        fontWeight: FontWeight.w600),
                  ),
                ],
              ),
            ),
          ),
      ],
    );
  }

  List<Widget> _rows(double colW, ColorScheme scheme) {
    final rows = <Widget>[];
    for (var h = 0; h < widget.holeCount; h++) {
      rows.add(_holeRow(h, colW, scheme));
      if (h == 8 && widget.holeCount > 9) {
        rows.add(_subtotalRow('Out', colW, scheme,
            (scores) => _nineTotal(scores, 0)));
      }
    }
    if (widget.holeCount >= 18) {
      rows.add(_subtotalRow('In', colW, scheme,
          (scores) => _nineTotal(scores, 9)));
    }
    rows.add(_subtotalRow('Total', colW, scheme,
        (scores) => scores.fold<int>(0, (a, s) => a + (s ?? 0)),
        bold: true));
    return rows;
  }

  Widget _holeRow(int hole, double colW, ColorScheme scheme) {
    final par = _par(hole);
    final alt = hole.isOdd;
    return Container(
      color: alt ? scheme.surfaceContainerLowest : null,
      child: Row(
        children: [
          SizedBox(
            width: _labelW,
            height: _rowH,
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Text('${hole + 1}',
                    style: const TextStyle(
                        fontSize: 14, fontWeight: FontWeight.bold)),
                Text(par == null ? '' : 'Par $par',
                    style:
                        const TextStyle(fontSize: 10, color: Colors.grey)),
              ],
            ),
          ),
          for (var c = 0; c < widget.cards.length; c++)
            _cell(c, hole, colW, scheme),
        ],
      ),
    );
  }

  Widget _cell(int c, int hole, double colW, ColorScheme scheme) {
    final card = widget.cards[c];
    final score = hole < card.scores.length ? card.scores[hole] : null;
    final par = _par(hole);
    final selected = _selCard == c && _selHole == hole;
    Color? bg;
    if (score != null && par != null && !card.locked) {
      if (score < par) bg = Colors.green.shade100;
      if (score > par) bg = Colors.red.shade100;
    }
    return GestureDetector(
      onTap: () => _tapCell(c, hole),
      child: Container(
        width: colW,
        height: _rowH,
        decoration: BoxDecoration(
          color: selected ? scheme.primaryContainer : bg,
          border: selected
              ? Border.all(color: scheme.primary, width: 2)
              : Border.all(color: scheme.outlineVariant, width: 0.5),
        ),
        alignment: Alignment.center,
        child: Text(
          score?.toString() ?? '–',
          style: TextStyle(
            fontSize: 17,
            fontWeight: selected ? FontWeight.bold : FontWeight.w600,
            color: score == null
                ? Colors.grey.shade400
                : card.locked
                    ? Colors.grey.shade600
                    : null,
          ),
        ),
      ),
    );
  }

  Widget _subtotalRow(String label, double colW, ColorScheme scheme,
      int? Function(List<int?>) totalOf,
      {bool bold = false}) {
    return Container(
      color: scheme.surfaceContainerHighest,
      child: Row(
        children: [
          SizedBox(
            width: _labelW,
            height: 38,
            child: Center(
              child: Text(label.toUpperCase(),
                  style: const TextStyle(
                      fontSize: 11, fontWeight: FontWeight.bold)),
            ),
          ),
          for (var c = 0; c < widget.cards.length; c++)
            SizedBox(
              width: colW,
              height: 38,
              child: Center(
                child: Text(
                  widget.cards[c].hasAnyScore
                      ? (totalOf(widget.cards[c].scores)?.toString() ?? '–')
                      : '–',
                  style: TextStyle(
                      fontSize: 14,
                      fontWeight: bold ? FontWeight.bold : FontWeight.w600),
                ),
              ),
            ),
        ],
      ),
    );
  }

  Widget _panel(ColorScheme scheme) {
    final c = _selCard!, h = _selHole!;
    final card = widget.cards[c];
    final par = _par(h);
    final current = card.scores[h];
    return Container(
      width: double.infinity,
      decoration: BoxDecoration(
        color: scheme.surface,
        border: Border(top: BorderSide(color: scheme.outlineVariant)),
        boxShadow: const [
          BoxShadow(color: Colors.black12, blurRadius: 6, offset: Offset(0, -2))
        ],
      ),
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(12, 8, 12, 10),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      'Hole ${h + 1}${par != null ? ' · Par $par' : ''} — ${card.title}',
                      style: const TextStyle(
                          fontSize: 15, fontWeight: FontWeight.bold),
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  IconButton(
                    icon: const Icon(Icons.close, size: 20),
                    onPressed: _closePanel,
                    visualDensity: VisualDensity.compact,
                  ),
                ],
              ),
              GridView.count(
                crossAxisCount: 5,
                shrinkWrap: true,
                physics: const NeverScrollableScrollPhysics(),
                mainAxisSpacing: 6,
                crossAxisSpacing: 6,
                childAspectRatio: 1.6,
                children: List.generate(10, (i) => i + 1).map((score) {
                  final selected = current == score;
                  return ElevatedButton(
                    onPressed: () => _choose(score),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: selected
                          ? scheme.primary
                          : scoreTint(score, par),
                      foregroundColor: selected ? Colors.white : Colors.black87,
                      padding: EdgeInsets.zero,
                    ),
                    child: Text('$score',
                        style: const TextStyle(
                            fontSize: 17, fontWeight: FontWeight.bold)),
                  );
                }).toList(),
              ),
              const SizedBox(height: 6),
              SingleChildScrollView(
                scrollDirection: Axis.horizontal,
                child: Row(
                  children: [
                    if (widget.allowClear && current != null)
                      TextButton.icon(
                        onPressed: () => _choose(null),
                        icon: const Icon(Icons.backspace_outlined, size: 16),
                        label: const Text('Clear'),
                      ),
                    TextButton.icon(
                      onPressed: _customScore,
                      icon: const Icon(Icons.edit, size: 16),
                      label: Text('Custom (1–${widget.customMax})'),
                    ),
                    if (widget.onTrackShots != null)
                      TextButton.icon(
                        onPressed: () {
                          _closePanel();
                          widget.onTrackShots!(c, h);
                        },
                        icon: const Icon(Icons.timeline, size: 16),
                        label: const Text('Track shots'),
                      ),
                    if (widget.onReviewShots != null && card.hasCard)
                      TextButton.icon(
                        onPressed: () {
                          _closePanel();
                          widget.onReviewShots!(c);
                        },
                        icon: const Icon(Icons.map_outlined, size: 16),
                        label: const Text('Review shots'),
                      ),
                    if (widget.onVerifyCard != null && card.canVerify)
                      TextButton.icon(
                        onPressed: () {
                          _closePanel();
                          widget.onVerifyCard!(c);
                        },
                        icon: const Icon(Icons.verified,
                            size: 16, color: Colors.green),
                        label: const Text('Verify (admin)',
                            style: TextStyle(color: Colors.green)),
                      ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
