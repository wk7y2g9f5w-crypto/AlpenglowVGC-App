import 'package:flutter/material.dart';

import '../models/models.dart';

/// Shared honest-stats layout: big handicap index on top, then the
/// shot-tracking stat rows, then the tracked-rounds count. Nulls render
/// as "—". Used by Profile (own stats) and PlayerStatsScreen (others).
class ShotStatsView extends StatelessWidget {
  final PlayerShotStats stats;

  const ShotStatsView({super.key, required this.stats});

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Container(
          padding: const EdgeInsets.symmetric(vertical: 16),
          decoration: BoxDecoration(
            color: scheme.primaryContainer,
            borderRadius: BorderRadius.circular(12),
          ),
          child: Column(
            children: [
              const Text('Handicap index',
                  style: TextStyle(
                      fontSize: 13, fontWeight: FontWeight.w600)),
              const SizedBox(height: 4),
              Text(
                stats.handicapIndex == null
                    ? '—'
                    : stats.handicapIndex!.toStringAsFixed(1),
                style: const TextStyle(
                    fontSize: 40, fontWeight: FontWeight.bold),
              ),
              if (stats.handicapIndex == null)
                const Text('needs 3+ completed rounds',
                    style: TextStyle(
                        fontSize: 12, color: Colors.grey)),
            ],
          ),
        ),
        const SizedBox(height: 12),
        Card(
          margin: EdgeInsets.zero,
          child: Column(
            children: [
              _row('Fairways hit', _pct(stats.fairwaysHitPct)),
              _row('Greens in regulation', _pct(stats.girPct)),
              _row('Putts / round', _num(stats.puttsPerRound, 1)),
              _row('Putts / GIR', _num(stats.puttsPerGir, 2)),
              _row('Up & down', _pct(stats.upDownPct)),
              _row('Sand saves', _pct(stats.sandSavePct)),
            ],
          ),
        ),
        const SizedBox(height: 8),
        Text(
          '${stats.roundsTracked} round${stats.roundsTracked == 1 ? '' : 's'} tracked'
          '${stats.roundsFullyTracked > 0 ? ' · ${stats.roundsFullyTracked} fully' : ''}',
          textAlign: TextAlign.center,
          style: const TextStyle(color: Colors.grey, fontSize: 13),
        ),
      ],
    );
  }

  Widget _row(String label, String value) {
    return ListTile(
      dense: true,
      title: Text(label),
      trailing: Text(
        value,
        style:
            const TextStyle(fontWeight: FontWeight.bold, fontSize: 15),
      ),
    );
  }

  static String _pct(double? v) =>
      v == null ? '—' : '${v.toStringAsFixed(1)}%';

  static String _num(double? v, int decimals) =>
      v == null ? '—' : v.toStringAsFixed(decimals);
}
