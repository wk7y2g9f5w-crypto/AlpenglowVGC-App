import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../widgets/common.dart';
import 'round_detail_screen.dart';

/// A player's round history: completed/submitted tournament and casual
/// rounds, newest tee time first, each clearly labeled with its kind.
class RoundHistoryScreen extends StatefulWidget {
  final ApiClient api;
  final String playerKey;

  const RoundHistoryScreen({
    super.key,
    required this.api,
    required this.playerKey,
  });

  @override
  State<RoundHistoryScreen> createState() => _RoundHistoryScreenState();
}

class _RoundHistoryScreenState extends State<RoundHistoryScreen> {
  late Future<List<RoundSummary>> _future;

  @override
  void initState() {
    super.initState();
    _future = widget.api.getRoundHistory(widget.playerKey);
  }

  Future<void> _refresh() async {
    final f = widget.api.getRoundHistory(widget.playerKey);
    setState(() => _future = f);
    await f;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Round history')),
      body: FutureBuilder<List<RoundSummary>>(
        future: _future,
        builder: (context, snap) {
          if (snap.connectionState == ConnectionState.waiting) {
            return const Center(child: CircularProgressIndicator());
          }
          if (snap.hasError) {
            final err = snap.error;
            final msg = err is ApiException
                ? friendlyApiMessage(err)
                : 'Couldn\u2019t load round history.';
            return RefreshIndicator(
              onRefresh: _refresh,
              child: ListView(
                physics: const AlwaysScrollableScrollPhysics(),
                padding: const EdgeInsets.all(32),
                children: [
                  const SizedBox(height: 80),
                  Text(msg,
                      textAlign: TextAlign.center,
                      style: const TextStyle(color: Colors.grey)),
                ],
              ),
            );
          }
          final rounds = snap.data ?? const <RoundSummary>[];
          if (rounds.isEmpty) {
            return RefreshIndicator(
              onRefresh: _refresh,
              child: ListView(
                physics: const AlwaysScrollableScrollPhysics(),
                padding: const EdgeInsets.all(32),
                children: const [
                  SizedBox(height: 80),
                  Text(
                    'No completed rounds yet.\nFinished scorecards show up here.',
                    textAlign: TextAlign.center,
                    style: TextStyle(color: Colors.grey),
                  ),
                ],
              ),
            );
          }
          return RefreshIndicator(
            onRefresh: _refresh,
            child: ListView.separated(
              padding: const EdgeInsets.symmetric(vertical: 8),
              itemCount: rounds.length,
              separatorBuilder: (_, __) => const Divider(height: 1),
              itemBuilder: (context, i) => _roundTile(context, rounds[i]),
            ),
          );
        },
      ),
    );
  }

  Widget _roundTile(BuildContext context, RoundSummary r) {
    final when = r.startsAt;
    return ListTile(
      contentPadding:
          const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
      leading: _KindChip(tournament: r.isTournament),
      title: Text(r.title,
          style: const TextStyle(fontWeight: FontWeight.w600)),
      subtitle: Text(
        '${when != null && when.isNotEmpty ? formatTeeTimeWhen(when) : 'Time TBD'}\n${r.course}',
        style: const TextStyle(fontSize: 12),
      ),
      isThreeLine: true,
      trailing: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          Text('${r.total}',
              style:
                  const TextStyle(fontSize: 20, fontWeight: FontWeight.bold)),
          Text(_formatToPar(r.toPar),
              style: TextStyle(
                  fontSize: 12,
                  color: _toParColor(r.toPar),
                  fontWeight: FontWeight.w600)),
        ],
      ),
      onTap: () => Navigator.of(context).push(
        MaterialPageRoute(builder: (_) => RoundDetailScreen(round: r)),
      ),
    );
  }
}

/// Small kind badge: blue for tournament rounds, green for casual.
class _KindChip extends StatelessWidget {
  final bool tournament;

  const _KindChip({required this.tournament});

  @override
  Widget build(BuildContext context) {
    final color =
        tournament ? Colors.blue.shade700 : Colors.green.shade700;
    return Chip(
      label: Text(
        tournament ? 'TOURNAMENT' : 'CASUAL',
        style: const TextStyle(
            fontSize: 10, fontWeight: FontWeight.bold, color: Colors.white),
      ),
      backgroundColor: color,
      visualDensity: VisualDensity.compact,
      padding: EdgeInsets.zero,
      labelPadding: const EdgeInsets.symmetric(horizontal: 8),
    );
  }
}

String _formatToPar(int? toPar) {
  if (toPar == null) return '';
  if (toPar == 0) return 'E';
  return toPar > 0 ? '+$toPar' : '$toPar';
}

Color? _toParColor(int? toPar) {
  if (toPar == null || toPar == 0) return Colors.grey;
  return toPar < 0 ? Colors.green.shade700 : Colors.red.shade700;
}
