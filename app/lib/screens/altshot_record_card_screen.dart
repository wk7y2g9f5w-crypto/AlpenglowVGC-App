import 'package:flutter/material.dart';

import '../models/models.dart';
import '../widgets/score_badge.dart';

/// Read-only view of a submitted AltShot record scorecard.
///
/// Opened by tapping a row on the Records tab. Shows the full 18-hole
/// card with standard golf notation so players can see where the record
/// team played great or poorly. No editing is possible here; scores are
/// edited (crew only) from the tee-time detail screen.
class AltShotRecordCardScreen extends StatelessWidget {
  final AltShotRecord record;

  const AltShotRecordCardScreen({super.key, required this.record});

  @override
  Widget build(BuildContext context) {
    final n = record.holes.length.clamp(0, 18);
    return Scaffold(
      appBar: AppBar(title: Text(record.teamDisplay)),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text(record.playersLine,
              style: const TextStyle(fontSize: 14, color: Colors.black87)),
          const SizedBox(height: 4),
          Text(record.settingsSummary,
              style: const TextStyle(fontSize: 12, color: Colors.grey)),
          const SizedBox(height: 4),
          Text(record.teeTimeLabel,
              style: const TextStyle(fontSize: 12, color: Colors.grey)),
          const SizedBox(height: 12),
          Row(
            children: [
              const Text('Team total',
                  style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
              const Spacer(),
              Text(record.scoreLine,
                  style: const TextStyle(
                      fontSize: 20, fontWeight: FontWeight.bold)),
            ],
          ),
          const SizedBox(height: 16),
          if (n == 0)
            const Text('No hole-by-hole scores stored for this record.')
          else
            SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: _cardTable(n),
            ),
          const SizedBox(height: 16),
          const Text(
            'Circle = birdie · double circle = eagle or better · '
            'square = bogey · double square = double bogey or worse.',
            style: TextStyle(fontSize: 11, color: Colors.grey),
          ),
        ],
      ),
    );
  }

  Widget _cardTable(int n) {
    final holes = record.holes;
    final pars = record.pars;
    int segTotal(List<int> xs, int from, int to) =>
        xs.sublist(from, to.clamp(0, xs.length)).fold(0, (a, b) => a + b);
    final frontN = n >= 18 ? 9 : (n / 2).floor();
    final front = segTotal(holes, 0, frontN);
    final back = segTotal(holes, frontN, n);
    final frontPar = segTotal(pars, 0, frontN);
    final backPar = segTotal(pars, frontN, n);

    Widget head(String s) => Container(
          width: 34,
          padding: const EdgeInsets.symmetric(vertical: 6),
          alignment: Alignment.center,
          child: Text(s,
              style:
                  const TextStyle(fontSize: 11, fontWeight: FontWeight.bold)),
        );

    Widget cell(Widget child) => Container(
          width: 34,
          padding: const EdgeInsets.symmetric(vertical: 4),
          alignment: Alignment.center,
          child: child,
        );

    List<Widget> row(String label, List<Widget> cells) => [
          Container(
            width: 44,
            padding: const EdgeInsets.symmetric(vertical: 6, horizontal: 4),
            alignment: Alignment.centerLeft,
            child: Text(label,
                style: const TextStyle(
                    fontSize: 11, fontWeight: FontWeight.bold)),
          ),
          ...cells,
        ];

    final holeCells = [
      for (var i = 0; i < n; i++) head('${i + 1}'),
      head('OUT'),
      head('IN'),
      head('TOT'),
    ];
    final scoreCells = [
      for (var i = 0; i < n; i++)
        cell(ScoreBadge(
            score: holes[i], par: i < pars.length ? pars[i] : null, size: 30)),
      cell(Text('$front',
          style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 13))),
      cell(Text('$back',
          style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 13))),
      cell(Text('${record.total}',
          style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 13))),
    ];
    final parCells = [
      for (var i = 0; i < n; i++)
        cell(Text(i < pars.length ? '${pars[i]}' : '–',
            style: const TextStyle(fontSize: 12, color: Colors.grey))),
      cell(Text('$frontPar',
          style: const TextStyle(fontSize: 12, color: Colors.grey))),
      cell(Text('$backPar',
          style: const TextStyle(fontSize: 12, color: Colors.grey))),
      cell(Text('${frontPar + backPar}',
          style: const TextStyle(fontSize: 12, color: Colors.grey))),
    ];

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(children: row('Hole', holeCells)),
        const Divider(height: 8),
        Row(children: row('Score', scoreCells)),
        const Divider(height: 8),
        Row(children: row('Par', parCells)),
      ],
    );
  }
}
