import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../services/auth.dart';
import '../widgets/common.dart';
import 'tournament_detail_screen.dart';
import 'create_tournament_screen.dart';

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
      if (mounted) setState(() => _isCrew = me.isCrew);
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
      appBar: AppBar(title: const Text('Tournaments')),
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
            itemCount: tournaments.length,
            itemBuilder: (context, i) {
              final t = tournaments[i];
              return Card(
                margin:
                    const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                child: ListTile(
                  title: Text(t.name,
                      style: const TextStyle(fontWeight: FontWeight.bold)),
                  subtitle: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const SizedBox(height: 4),
                      Text(
                          '${t.course ?? 'Course TBD'} · ${formatDateRange(t.startDate, t.endDate)}'),
                      const SizedBox(height: 6),
                      Row(
                        children: [
                          StatusChip(status: t.status),
                          if (t.registered) ...[
                            const SizedBox(width: 8),
                            const Chip(
                              label: Text('REGISTERED',
                                  style: TextStyle(
                                      fontSize: 11,
                                      fontWeight: FontWeight.bold)),
                              backgroundColor: Colors.transparent,
                              side: BorderSide(color: Colors.green),
                              visualDensity: VisualDensity.compact,
                              padding: EdgeInsets.zero,
                              avatar: Icon(Icons.check,
                                  size: 14, color: Colors.green),
                            ),
                          ],
                        ],
                      ),
                    ],
                  ),
                  trailing: const Icon(Icons.chevron_right),
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
                ),
              );
            },
          );
        },
      ),
    );
  }
}
