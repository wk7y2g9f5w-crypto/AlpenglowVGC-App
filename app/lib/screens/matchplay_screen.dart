import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../services/auth.dart';
import '../widgets/common.dart';
import 'matchplay_detail_screen.dart';
import 'matchplay_form_screen.dart';

/// Matchplay: 2-sided tee times (single or best-ball) with live hole-by-hole
/// scoring, plus a per-course W-L-T record leaderboard.
class MatchplayScreen extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;

  const MatchplayScreen({super.key, required this.auth, required this.settings});

  @override
  State<MatchplayScreen> createState() => _MatchplayScreenState();
}

class _MatchplayScreenState extends State<MatchplayScreen>
    with SingleTickerProviderStateMixin {
  late final TabController _tabs;
  final _listKey = GlobalKey<_MatchPlayTeeTimeListState>();

  @override
  void initState() {
    super.initState();
    _tabs = TabController(length: 2, vsync: this);
  }

  @override
  void dispose() {
    _tabs.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Matchplay'),
        bottom: TabBar(
          controller: _tabs,
          tabs: const [
            Tab(text: 'Tee Times', icon: Icon(Icons.sports_golf)),
            Tab(text: 'Records', icon: Icon(Icons.emoji_events)),
          ],
        ),
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () async {
          final created = await Navigator.of(context).push(
            MaterialPageRoute(
              builder: (_) => MatchPlayFormScreen(
                auth: widget.auth,
                settings: widget.settings,
              ),
            ),
          );
          if (created == true && mounted) _tabs.animateTo(0);
          _refreshCurrent();
        },
        icon: const Icon(Icons.add),
        label: const Text('New'),
      ),
      body: TabBarView(
        controller: _tabs,
        children: [
          _MatchPlayTeeTimeList(
              auth: widget.auth, settings: widget.settings, key: _listKey),
          _MatchPlayRecordsView(auth: widget.auth, settings: widget.settings),
        ],
      ),
    );
  }

  void _refreshCurrent() {
    _listKey.currentState?.refresh();
  }
}

class _MatchPlayTeeTimeList extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;

  const _MatchPlayTeeTimeList(
      {super.key, required this.auth, required this.settings});

  @override
  State<_MatchPlayTeeTimeList> createState() => _MatchPlayTeeTimeListState();
}

class _MatchPlayTeeTimeListState extends State<_MatchPlayTeeTimeList> {
  late Future<List<MatchPlayTeeTime>> _future;

  ApiClient get _api => ApiClient(
      baseUrl: widget.settings.baseUrl, token: widget.auth.token ?? '');

  @override
  void initState() {
    super.initState();
    _future = _api.listMatchPlayTeeTimes();
  }

  void refresh() => _refresh();

  Future<void> _refresh() async {
    final f = _api.listMatchPlayTeeTimes();
    setState(() => _future = f);
    await f;
  }

  String _subtitle(MatchPlayTeeTime tt) {
    final s1 = tt.sides.isNotEmpty ? tt.sides[0].displayName : '';
    final s2 = tt.sides.length > 1 ? tt.sides[1].displayName : '';
    final matchup =
        s1.isNotEmpty && s2.isNotEmpty ? '$s1 vs $s2' : tt.formatSummary;
    final score = tt.score;
    final result = score == null
        ? null
        : (score.isCompleted ? score.resultText : score.liveText);
    return '$matchup${result != null && result.isNotEmpty ? ' · $result' : ''}';
  }

  @override
  Widget build(BuildContext context) {
    return AsyncBody<List<MatchPlayTeeTime>>(
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
                      'No matches yet.\nTap New to set one up.',
                      textAlign: TextAlign.center)),
            ],
          );
        }
        return ListView.builder(
          physics: const AlwaysScrollableScrollPhysics(),
          itemCount: teeTimes.length,
          itemBuilder: (context, i) {
            final tt = teeTimes[i];
            final score = tt.score;
            return Card(
              margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
              child: ListTile(
                title: Text(tt.label,
                    style: const TextStyle(fontWeight: FontWeight.bold)),
                subtitle: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const SizedBox(height: 4),
                    Text('${tt.course} · ${formatTeeTimeWhen(tt.startsAt)}'),
                    const SizedBox(height: 2),
                    Text(
                      '${_subtitle(tt)} · ${tt.settingsSummary}',
                      style: const TextStyle(fontSize: 12),
                    ),
                  ],
                ),
                trailing: score != null && score.isCompleted
                    ? const Icon(Icons.emoji_events, color: Colors.amber)
                    : const Icon(Icons.chevron_right),
                onTap: () async {
                  await Navigator.of(context).push(MaterialPageRoute(
                    builder: (_) => MatchPlayDetailScreen(
                      auth: widget.auth,
                      settings: widget.settings,
                      teeTimeId: tt.id,
                    ),
                  ));
                  _refresh();
                },
              ),
            );
          },
        );
      },
    );
  }
}

class _MatchPlayRecordsView extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;

  const _MatchPlayRecordsView({required this.auth, required this.settings});

  @override
  State<_MatchPlayRecordsView> createState() => _MatchPlayRecordsViewState();
}

class _MatchPlayRecordsViewState extends State<_MatchPlayRecordsView> {
  static const _formats = {'single': '1v1 Matchplay', 'bestball': 'Best Ball'};

  String _format = 'single';
  Future<List<MatchPlayRecord>>? _future;

  ApiClient get _api => ApiClient(
      baseUrl: widget.settings.baseUrl, token: widget.auth.token ?? '');

  @override
  void initState() {
    super.initState();
    _reload();
  }

  void _reload() {
    setState(() {
      _future = _api.getMatchPlayRecords(_format);
    });
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 12, 12, 0),
          child: DropdownButtonFormField<String>(
            initialValue: _format,
            decoration: const InputDecoration(
              labelText: 'Leaderboard',
              border: OutlineInputBorder(),
              isDense: true,
            ),
            items: _formats.entries
                .map((e) =>
                    DropdownMenuItem(value: e.key, child: Text(e.value)))
                .toList(),
            onChanged: (v) {
              if (v != null) {
                _format = v;
                _reload();
              }
            },
          ),
        ),
        const SizedBox(height: 8),
        Expanded(
          child: AsyncBody<List<MatchPlayRecord>>(
            future: _future!,
            onRefresh: () async {
              final f = _api.getMatchPlayRecords(_format);
              setState(() => _future = f);
              await f;
            },
            builder: (context, records) {
              if (records.isEmpty) {
                return ListView(
                  physics: const AlwaysScrollableScrollPhysics(),
                  children: const [
                    SizedBox(height: 80),
                    Center(
                        child: Text(
                            'No records yet.\nBe the first to win one.',
                            textAlign: TextAlign.center)),
                  ],
                );
              }
              return ListView.builder(
                physics: const AlwaysScrollableScrollPhysics(),
                itemCount: records.length,
                itemBuilder: (context, i) {
                  final r = records[i];
                  return Card(
                    margin: const EdgeInsets.symmetric(
                        horizontal: 12, vertical: 6),
                    child: ListTile(
                      leading: CircleAvatar(
                        backgroundColor: i == 0
                            ? Colors.amber
                            : Colors.grey.shade300,
                        child: Text('${i + 1}',
                            style: TextStyle(
                                color: i == 0
                                    ? Colors.black
                                    : Colors.black54,
                                fontWeight: FontWeight.bold)),
                      ),
                      title: Text(r.playerName,
                          style: const TextStyle(
                              fontWeight: FontWeight.bold)),
                      subtitle: Text(
                          'W–L–T ${r.recordLine}',
                          style: const TextStyle(fontSize: 12)),
                      trailing: Text(r.winPctLine,
                          style: const TextStyle(
                              fontSize: 16,
                              fontWeight: FontWeight.bold)),
                    ),
                  );
                },
              );
            },
          ),
        ),
      ],
    );
  }
}
