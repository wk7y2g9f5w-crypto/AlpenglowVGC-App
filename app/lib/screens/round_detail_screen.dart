import 'package:flutter/material.dart';

import '../models/models.dart';
import '../widgets/common.dart';
import '../widgets/score_badge.dart';

/// Hole-by-hole detail for one round from the round history.
class RoundDetailScreen extends StatelessWidget {
  final RoundSummary round;

  const RoundDetailScreen({super.key, required this.round});

  @override
  Widget build(BuildContext context) {
    final r = round;
    return Scaffold(
      appBar: AppBar(title: Text(r.title)),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
        children: [
          _header(context),
          const SizedBox(height: 16),
          const Text('Scorecard',
              style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
          const SizedBox(height: 8),
          _scorecardGrid(),
          const SizedBox(height: 16),
          _totalsRow(),
        ],
      ),
    );
  }

  Widget _header(BuildContext context) {
    final r = round;
    final when = r.startsAt;
    final submitted = r.submittedAt;
    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Chip(
                  label: Text(
                    r.kindLabel.toUpperCase(),
                    style: const TextStyle(
                        fontSize: 10,
                        fontWeight: FontWeight.bold,
                        color: Colors.white),
                  ),
                  backgroundColor: r.isTournament
                      ? Colors.blue.shade700
                      : Colors.green.shade700,
                  visualDensity: VisualDensity.compact,
                  padding: EdgeInsets.zero,
                  labelPadding:
                      const EdgeInsets.symmetric(horizontal: 8),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(r.course,
                      style: const TextStyle(
                          fontSize: 15, fontWeight: FontWeight.w600)),
                ),
              ],
            ),
            const SizedBox(height: 8),
            _factRow('Tee time',
                when != null && when.isNotEmpty ? formatTeeTimeWhen(when) : 'Time TBD'),
            if (submitted != null && submitted.isNotEmpty)
              _factRow('Submitted', _formatSubmitted(submitted)),
            if (r.witnessName != null && r.witnessName!.isNotEmpty)
              _factRow('Witness', r.witnessName!),
            _factRow('Status',
                r.status.replaceAll('_', ' ').toUpperCase()),
          ],
        ),
      ),
    );
  }

  Widget _factRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.only(top: 4),
      child: Row(
        children: [
          SizedBox(
            width: 88,
            child: Text(label,
                style: const TextStyle(color: Colors.grey, fontSize: 13)),
          ),
          Expanded(
            child: Text(value,
                style: const TextStyle(
                    fontSize: 13, fontWeight: FontWeight.w500)),
          ),
        ],
      ),
    );
  }

  String _formatSubmitted(String iso) {
    try {
      return formatLocal(DateTime.parse(iso));
    } catch (_) {
      return iso;
    }
  }

  /// Classic scorecard grid: OUT row (1-9) and IN row (10-18) for 18-hole
  /// rounds, a single row otherwise.
  Widget _scorecardGrid() {
    final r = round;
    final n = r.scores.length;
    if (n == 0) {
      return const Text('No hole scores recorded.',
          style: TextStyle(color: Colors.grey));
    }
    if (n <= 9) return _nineRow(0, n, null);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _nineRow(0, 9, 'OUT'),
        const SizedBox(height: 12),
        _nineRow(9, n, 'IN'),
      ],
    );
  }

  Widget _nineRow(int start, int end, String? tag) {
    final r = round;
    final cells = <Widget>[];
    if (tag != null) {
      cells.add(_gridLabel(tag));
    }
    for (var i = start; i < end; i++) {
      final score = i < r.scores.length ? r.scores[i] : null;
      final par =
          r.pars != null && i < r.pars!.length ? r.pars![i] : null;
      cells.add(Expanded(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text('${i + 1}',
                style: const TextStyle(
                    fontSize: 10, color: Colors.grey)),
            const SizedBox(height: 2),
            if (score != null)
              ScoreBadge(score: score, par: par, size: 26)
            else
              const Text('\u2013',
                  style: TextStyle(color: Colors.grey)),
            if (par != null)
              Text('$par',
                  style: const TextStyle(
                      fontSize: 10, color: Colors.grey)),
          ],
        ),
      ));
    }
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: cells,
    );
  }

  Widget _gridLabel(String tag) {
    return SizedBox(
      width: 34,
      child: Padding(
        padding: const EdgeInsets.only(top: 2),
        child: Text(tag,
            style: const TextStyle(
                fontSize: 10,
                fontWeight: FontWeight.bold,
                color: Colors.grey)),
      ),
    );
  }

  Widget _totalsRow() {
    final r = round;
    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.spaceAround,
          children: [
            _totalCell('Total', '${r.total}'),
            _totalCell('To par', _formatToPar(r.toPar)),
            _totalCell('Thru', '${r.thru}'),
          ],
        ),
      ),
    );
  }

  Widget _totalCell(String label, String value) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(label,
            style: const TextStyle(fontSize: 12, color: Colors.grey)),
        const SizedBox(height: 2),
        Text(value,
            style: const TextStyle(
                fontSize: 20, fontWeight: FontWeight.bold)),
      ],
    );
  }

  String _formatToPar(int? toPar) {
    if (toPar == null) return '\u2013';
    if (toPar == 0) return 'E';
    return toPar > 0 ? '+$toPar' : '$toPar';
  }
}
