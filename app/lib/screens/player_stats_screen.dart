import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../widgets/shot_stats_view.dart';

/// Shot-tracking stats + handicap for another player. Honors the player's
/// privacy toggle: a private profile shows a friendly empty state instead
/// of numbers.
class PlayerStatsScreen extends StatefulWidget {
  final ApiClient api;
  final String playerKey;
  final String displayName;

  const PlayerStatsScreen({
    super.key,
    required this.api,
    required this.playerKey,
    required this.displayName,
  });

  @override
  State<PlayerStatsScreen> createState() => _PlayerStatsScreenState();
}

class _PlayerStatsScreenState extends State<PlayerStatsScreen> {
  late Future<PlayerShotStats> _future;

  @override
  void initState() {
    super.initState();
    _future = widget.api.getPlayerStats(widget.playerKey);
  }

  Future<void> _refresh() async {
    final f = widget.api.getPlayerStats(widget.playerKey);
    setState(() => _future = f);
    await f;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(widget.displayName)),
      body: FutureBuilder<PlayerShotStats>(
        future: _future,
        builder: (context, snap) {
          if (snap.connectionState == ConnectionState.waiting) {
            return const Center(child: CircularProgressIndicator());
          }
          if (snap.hasError) {
            final err = snap.error;
            if (err is StatsPrivateException) {
              return RefreshIndicator(
                onRefresh: _refresh,
                child: ListView(
                  physics: const AlwaysScrollableScrollPhysics(),
                  padding: const EdgeInsets.all(32),
                  children: [
                    const SizedBox(height: 80),
                    const Icon(Icons.visibility_off,
                        size: 56, color: Colors.grey),
                    const SizedBox(height: 16),
                    const Text(
                      'Stats are private',
                      textAlign: TextAlign.center,
                      style: TextStyle(
                          fontSize: 20,
                          fontWeight: FontWeight.bold),
                    ),
                    const SizedBox(height: 8),
                    Text(
                      '${widget.displayName} has chosen to keep '
                      'their shot stats to themselves.',
                      textAlign: TextAlign.center,
                      style: const TextStyle(color: Colors.grey),
                    ),
                  ],
                ),
              );
            }
            final msg = err is ApiException
                ? friendlyApiMessage(err)
                : err.toString();
            return Center(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Icon(Icons.cloud_off,
                        size: 48, color: Colors.grey),
                    const SizedBox(height: 12),
                    Text(msg, textAlign: TextAlign.center),
                    const SizedBox(height: 12),
                    ElevatedButton(
                        onPressed: _refresh,
                        child: const Text('Retry')),
                  ],
                ),
              ),
            );
          }
          final stats = snap.data!;
          return RefreshIndicator(
            onRefresh: _refresh,
            child: ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.all(16),
              children: [
                if (!stats.hasAnyData)
                  const Padding(
                    padding: EdgeInsets.only(bottom: 12),
                    child: Text(
                      'No tracked rounds yet — stats appear here once '
                      'this player tracks shots.',
                      textAlign: TextAlign.center,
                      style: TextStyle(color: Colors.grey),
                    ),
                  ),
                ShotStatsView(stats: stats),
              ],
            ),
          );
        },
      ),
    );
  }
}
