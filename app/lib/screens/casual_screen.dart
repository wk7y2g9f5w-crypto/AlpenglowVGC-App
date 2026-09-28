import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../services/auth.dart';
import '../widgets/common.dart';
import '../widgets/course_art.dart';
import 'casual_detail_screen.dart';
import 'casual_form_screen.dart';

/// Casual rounds: ad-hoc tee times outside tournaments. No membership
/// limits — join as many as you like.
class CasualScreen extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;

  const CasualScreen({super.key, required this.auth, required this.settings});

  @override
  State<CasualScreen> createState() => _CasualScreenState();
}

class _CasualScreenState extends State<CasualScreen> {
  late Future<List<CasualTeeTime>> _future;

  ApiClient get _api => ApiClient(
      baseUrl: widget.settings.baseUrl, token: widget.auth.token ?? '');

  @override
  void initState() {
    super.initState();
    _future = _api.getCasualTeeTimes();
  }

  Future<void> _refresh() async {
    final f = _api.getCasualTeeTimes();
    setState(() => _future = f);
    await f;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Casual Rounds')),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () async {
          final created = await Navigator.of(context).push(
            MaterialPageRoute(
              builder: (_) => CasualFormScreen(
                auth: widget.auth,
                settings: widget.settings,
              ),
            ),
          );
          if (created == true) _refresh();
        },
        icon: const Icon(Icons.add),
        label: const Text('New'),
      ),
      body: AsyncBody<List<CasualTeeTime>>(
        future: _future,
        onRefresh: _refresh,
        builder: (context, teeTimes) {
          if (teeTimes.isEmpty) {
            return ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              children: const [
                SizedBox(height: 120),
                Center(
                    child: Text(
                        'No casual rounds yet.\nTap New to set one up.',
                        textAlign: TextAlign.center)),
              ],
            );
          }
          return ListView.builder(
            physics: const AlwaysScrollableScrollPhysics(),
            itemCount: teeTimes.length,
            itemBuilder: (context, i) {
              final tt = teeTimes[i];
              return CourseTeeTimeCard(
                course: tt.course,
                title: tt.label,
                line1: '${tt.course} · ${formatTeeTimeWhen(tt.startsAt)}',
                line2:
                    '${tt.players.length}/${tt.maxPlayers} players · ${tt.settingsSummary}',
                onTap: () async {
                  await Navigator.of(context).push(MaterialPageRoute(
                    builder: (_) => CasualDetailScreen(
                      auth: widget.auth,
                      settings: widget.settings,
                      teeTimeId: tt.id,
                    ),
                  ));
                  _refresh();
                },
              );
            },
          );
        },
      ),
    );
  }
}

