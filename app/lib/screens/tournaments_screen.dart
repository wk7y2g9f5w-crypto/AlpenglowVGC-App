import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../services/auth.dart';
import '../widgets/common.dart';
import '../widgets/course_art.dart';
import 'tournament_detail_screen.dart';
import 'create_tournament_screen.dart';
import 'season_standings_screen.dart';
import 'new_season_screen.dart';

/// Home tab: list of tournaments with pull-to-refresh.
class TournamentsScreen extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;

  const TournamentsScreen(
      {super.key, required this.auth, required this.settings});

  @override
  State<TournamentsScreen> createState() => _TournamentsScreenState();
}

class _TournamentsScreenState extends State<TournamentsScreen> {
  late Future<List<Tournament>> _future;
  bool _isCrew = false;
  bool _isAdmin = false;

  ApiClient get _api => ApiClient(
      baseUrl: widget.settings.baseUrl, token: widget.auth.token ?? '');

  @override
  void initState() {
    super.initState();
    _future = _load();
    _loadCrewFlag();
  }

  Future<List<Tournament>> _load() => _api.getTournaments();

  Future<void> _loadCrewFlag() async {
    try {
      final me = await _api.getMe();
      if (mounted) {
        setState(() {
          _isCrew = me.isCrew;
          _isAdmin = me.isAdmin;
        });
      }
    } catch (_) {
      // Not crew (or offline) — the create button just stays hidden.
    }
  }

  Future<void> _refresh() async {
    final f = _load();
    setState(() => _future = f);
    await f;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Tournaments'),
        actions: [
          IconButton(
            tooltip: 'Season standings',
            icon: const Icon(Icons.emoji_events),
            onPressed: () {
              Navigator.of(context).push(
                MaterialPageRoute(
                  builder: (_) => SeasonStandingsScreen(
                    auth: widget.auth,
                    settings: widget.settings,
                  ),
                ),
              );
            },
          ),
          if (_isAdmin)
            IconButton(
              tooltip: 'New season',
              icon: const Icon(Icons.calendar_month),
              onPressed: () {
                Navigator.of(context).push(
                  MaterialPageRoute(
                    builder: (_) => NewSeasonScreen(
                      auth: widget.auth,
                      settings: widget.settings,
                    ),
                  ),
                );
              },
            ),
        ],
      ),
      floatingActionButton: _isCrew
          ? FloatingActionButton.extended(
              onPressed: () async {
                final created = await Navigator.of(context).push(
                  MaterialPageRoute(
                    builder: (_) => CreateTournamentScreen(
                      auth: widget.auth,
                      settings: widget.settings,
                    ),
                  ),
                );
                if (created == true) _refresh();
              },
              icon: const Icon(Icons.add),
              label: const Text('New'),
            )
          : null,
      body: AsyncBody<List<Tournament>>(
        future: _future,
        onRefresh: _refresh,
        builder: (context, tournaments) {
          if (tournaments.isEmpty) {
            return ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              children: const [
                SizedBox(height: 120),
                Center(child: Text('No tournaments yet.')),
              ],
            );
          }
          return ListView.builder(
            physics: const AlwaysScrollableScrollPhysics(),
            itemCount: tournaments.length + 1,
            itemBuilder: (context, i) {
              if (i == 0) return const ProDifficultyDisclaimer();
              final t = tournaments[i - 1];
              final art = courseArtAsset(t.course);
              return Card(
                margin:
                    const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                clipBehavior: Clip.antiAlias,
                child: InkWell(
                  onTap: () async {
                    await Navigator.of(context).push(MaterialPageRoute(
                      builder: (_) => TournamentDetailScreen(
                        auth: widget.auth,
                        settings: widget.settings,
                        tournament: t,
                      ),
                    ));
                    _refresh();
                  },
                  child: Container(
                    decoration: BoxDecoration(
                      image: art != null
                          ? DecorationImage(
                              image: AssetImage(art),
                              fit: BoxFit.cover,
                              alignment: Alignment.center,
                            )
                          : null,
                      gradient: art == null
                          ? const LinearGradient(
                              begin: Alignment.topLeft,
                              end: Alignment.bottomRight,
                              colors: [
                                Color(0xFF1B5E20),
                                Color(0xFF0D3311)
                              ],
                            )
                          : null,
                    ),
                    child: Container(
                      decoration: const BoxDecoration(
                        gradient: LinearGradient(
                          begin: Alignment.topCenter,
                          end: Alignment.bottomCenter,
                          colors: [
                            Color.fromRGBO(0, 0, 0, 0.25),
                            Color.fromRGBO(0, 0, 0, 0.72),
                          ],
                        ),
                      ),
                      padding: const EdgeInsets.all(16),
                      child: Row(
                        children: [
                          Expanded(
                            child: Column(
                              crossAxisAlignment:
                                  CrossAxisAlignment.start,
                              children: [
                                Text(t.name,
                                    style: const TextStyle(
                                        fontWeight: FontWeight.bold,
                                        fontSize: 17,
                                        color: Colors.white)),
                                const SizedBox(height: 4),
                                Text(
                                    '${t.course ?? 'Course TBD'} · ${formatDateRange(t.startDate, t.endDate)}',
                                    style: const TextStyle(
                                        color: Colors.white70,
                                        fontSize: 13)),
                                const SizedBox(height: 8),
                                Row(
                                  children: [
                                    StatusChip(status: t.status),
                                    if (t.registered) ...[
                                      const SizedBox(width: 8),
                                      const Chip(
                                        label: Text('REGISTERED',
                                            style: TextStyle(
                                                fontSize: 11,
                                                fontWeight:
                                                    FontWeight.bold,
                                                color: Colors.white)),
                                        backgroundColor:
                                            Colors.transparent,
                                        side: BorderSide(
                                            color: Colors.green),
                                        visualDensity:
                                            VisualDensity.compact,
                                        padding: EdgeInsets.zero,
                                        avatar: Icon(Icons.check,
                                            size: 14,
                                            color: Colors.green),
                                      ),
                                    ],
                                  ],
                                ),
                              ],
                            ),
                          ),
                          const Icon(Icons.chevron_right,
                              color: Colors.white70),
                        ],
                      ),
                    ),
                  ),
                ),
              );
            },
          );
        },
      ),
    );
  }
}
