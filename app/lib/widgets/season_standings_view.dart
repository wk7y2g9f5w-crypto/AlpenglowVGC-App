import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../screens/player_stats_screen.dart';
import 'common.dart';

/// Season points standings list, shared by the tournament detail
/// Leaderboard tab's "Season" dropdown and the standalone Season
/// Standings screen reachable from the Tournaments tab.
class SeasonStandingsView extends StatefulWidget {  final ApiClient api;

  const SeasonStandingsView({super.key, required this.api});

  @override
  State<SeasonStandingsView> createState() => _SeasonStandingsViewState();
}

class _SeasonStandingsViewState extends State<SeasonStandingsView> {
  Future<SeasonStandings>? _future;
  List<Season> _seasons = [];
  int? _selectedId;

  @override
  void initState() {
    super.initState();
    _future = widget.api.seasonStandings();
    _loadSeasons();
  }

  /// Load the season list for the switcher (side-by-side seasons).
  /// Default view stays the API's default (newest active season); the
  /// switcher just lets you look at any other season, active or done.
  Future<void> _loadSeasons() async {
    try {
      final seasons = await widget.api.listSeasons();
      if (!mounted) return;
      setState(() {
        _seasons = seasons;
        if (seasons.isNotEmpty) {
          final active = seasons.where((s) => s.isActive).toList();
          _selectedId =
              (active.isNotEmpty ? active.first : seasons.first).id;
        }
      });
    } catch (e) {
      // Switcher is best-effort; the default standings still show.
    }
  }

  Future<void> _refresh() async {
    final f = widget.api.seasonStandings(seasonId: _selectedId);
    setState(() => _future = f);
    await f;
  }

  void _pickSeason(int? id) {
    if (id == null || id == _selectedId) return;
    setState(() {
      _selectedId = id;
      _future = widget.api.seasonStandings(seasonId: id);
    });
  }

  @override
  Widget build(BuildContext context) {
    final standings = AsyncBody<SeasonStandings>(
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
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      s.seasonName.isEmpty ? 'Season standings' : s.seasonName,
                      style: const TextStyle(
                          fontSize: 16, fontWeight: FontWeight.bold),
                    ),
                    if (_dateRangeLabel(s) != null)
                      Padding(
                        padding: const EdgeInsets.only(top: 2),
                        child: Text(
                          _dateRangeLabel(s)!,
                          style: TextStyle(
                              fontSize: 13, color: Colors.grey.shade600),
                        ),
                      ),
                  ],
                ),
              );
            }
            final e = s.entries[i - 1];
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
                golferDisplayName(e.displayName, e.golfplusHandle),
                style: const TextStyle(fontWeight: FontWeight.w600),
              ),
              subtitle: Text(
                  '${e.tournamentsPlayed} ${e.tournamentsPlayed == 1 ? 'tournament' : 'tournaments'}'),
              trailing: Text('${e.totalPoints} pts',
                  style: const TextStyle(
                      fontWeight: FontWeight.bold, fontSize: 16)),
              onTap: () => Navigator.of(context).push(
                MaterialPageRoute(
                  builder: (_) => PlayerStatsScreen(
                    api: widget.api,
                    playerKey: e.discordId,
                    displayName: golferDisplayName(
                        e.displayName, e.golfplusHandle),
                  ),
                ),
              ),
            );
          },
        );
      },
    );
    if (_seasons.length < 2) return standings;
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
          child: DropdownButtonFormField<int>(
            initialValue: _selectedId,
            decoration: const InputDecoration(
              labelText: 'Season',
              border: OutlineInputBorder(),
              isDense: true,
            ),
            items: _seasons
                .map((s) => DropdownMenuItem(
                      value: s.id,
                      child: Text(s.isActive
                          ? s.name
                          : '${s.name} (completed)'),
                    ))
                .toList(),
            onChanged: _pickSeason,
          ),
        ),
        Expanded(child: standings),
      ],
    );
  }
}

/// "Mar 1, 2026 – Sep 30, 2026", or a single date, or null when the
/// season carries no date range.
String? _dateRangeLabel(SeasonStandings s) {
  String? fmt(String? iso) {
    if (iso == null || iso.isEmpty) return null;
    final d = DateTime.tryParse(iso);
    if (d == null) return iso;
    const months = [
      'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'
    ];
    return '${months[d.month - 1]} ${d.day}, ${d.year}';
  }

  final start = fmt(s.seasonStartDate);
  final end = fmt(s.seasonEndDate);
  if (start == null && end == null) return null;
  if (start != null && end != null) return '$start – $end';
  return start ?? end;
}
