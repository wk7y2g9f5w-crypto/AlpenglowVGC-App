import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import 'common.dart';

/// Season points standings list, shared by the tournament detail
/// Leaderboard tab's "Season" dropdown and the standalone Season
/// Standings screen reachable from the Tournaments tab.
class SeasonStandingsView extends StatefulWidget {
  final ApiClient api;

  const SeasonStandingsView({super.key, required this.api});

  @override
  State<SeasonStandingsView> createState() => _SeasonStandingsViewState();
}

class _SeasonStandingsViewState extends State<SeasonStandingsView> {
  Future<SeasonStandings>? _future;

  @override
  void initState() {
    super.initState();
    _future = widget.api.seasonStandings();
  }

  Future<void> _refresh() async {
    final f = widget.api.seasonStandings();
    setState(() => _future = f);
    await f;
  }

  @override
  Widget build(BuildContext context) {
    return AsyncBody<SeasonStandings>(
      future: _future!,
      onRefresh: _refresh,
      builder: (context, s) {
        if (s.entries.isEmpty) {
          return ListView(
            physics: const AlwaysScrollableScrollPhysics(),
            children: const [
              SizedBox(height: 120),
              Center(child: Text('No season standings yet.')),
            ],
          );
        }
        return ListView.builder(
          physics: const AlwaysScrollableScrollPhysics(),
          itemCount: s.entries.length + 1,
          itemBuilder: (context, i) {
            if (i == 0) {
              return Padding(
                padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
                child: Text(
                  s.seasonName.isEmpty ? 'Season standings' : s.seasonName,
                  style: const TextStyle(
                      fontSize: 16, fontWeight: FontWeight.bold),
                ),
              );
            }
            final e = s.entries[i - 1];
            final handle = e.golfplusHandle;
            return ListTile(
              leading: CircleAvatar(
                backgroundColor:
                    i == 1 ? Colors.amber.shade700 : Colors.grey.shade300,
                child: Text('$i',
                    style: TextStyle(
                        color: i == 1 ? Colors.white : Colors.black87,
                        fontWeight: FontWeight.bold)),
              ),
              title: Text(
                handle != null && handle.isNotEmpty
                    ? '${e.displayName} ($handle)'
                    : e.displayName,
                style: const TextStyle(fontWeight: FontWeight.w600),
              ),
              subtitle: Text(
                  '${e.tournamentsPlayed} ${e.tournamentsPlayed == 1 ? 'tournament' : 'tournaments'}'),
              trailing: Text('${e.totalPoints} pts',
                  style: const TextStyle(
                      fontWeight: FontWeight.bold, fontSize: 16)),
            );
          },
        );
      },
    );
  }
}
