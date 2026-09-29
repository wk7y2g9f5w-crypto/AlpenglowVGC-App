import 'package:flutter/material.dart';

import '../services/api_client.dart';
import '../services/auth.dart';
import '../widgets/season_standings_view.dart';

/// Full-screen season points standings, reachable from the Tournaments
/// tab's AppBar so it is always available — even with no tournaments.
class SeasonStandingsScreen extends StatelessWidget {
  final AuthService auth;
  final SettingsService settings;

  const SeasonStandingsScreen(
      {super.key, required this.auth, required this.settings});

  @override
  Widget build(BuildContext context) {
    final api = ApiClient(
        baseUrl: settings.baseUrl, token: auth.token ?? '');
    return Scaffold(
      appBar: AppBar(title: const Text('Season Standings')),
      body: SeasonStandingsView(api: api),
    );
  }
}
