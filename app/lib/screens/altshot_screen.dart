import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../services/auth.dart';
import '../widgets/common.dart';
import '../widgets/course_art.dart';
import 'altshot_detail_screen.dart';
import 'altshot_form_screen.dart';
import 'altshot_record_card_screen.dart';

/// AltShot Records: team alternate-shot tee times plus a per-course record
/// leaderboard. Tee times hold 1-2 teams; scores are submitted once per team
/// (not live); records never send notifications.
class AltShotScreen extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;

  const AltShotScreen({super.key, required this.auth, required this.settings});

  @override
  State<AltShotScreen> createState() => _AltShotScreenState();
}

class _AltShotScreenState extends State<AltShotScreen>
    with SingleTickerProviderStateMixin {
  late final TabController _tabs;

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
        title: const Text('AltShot Records'),
        bottom: TabBar(
          controller: _tabs,
          tabs: const [
            Tab(text: 'Tee Times', icon: Icon(Icons.groups)),
            Tab(text: 'Records', icon: Icon(Icons.emoji_events)),
          ],
        ),
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () async {
          final created = await Navigator.of(context).push(
            MaterialPageRoute(
              builder: (_) => AltShotFormScreen(
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
          _AltShotTeeTimeList(
              auth: widget.auth, settings: widget.settings, key: _listKey),
          _AltShotRecordsView(auth: widget.auth, settings: widget.settings),
        ],
      ),
    );
  }

  final _listKey = GlobalKey<_AltShotTeeTimeListState>();

  void _refreshCurrent() {
    _listKey.currentState?.refresh();
  }
}

class _AltShotTeeTimeList extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;

  const _AltShotTeeTimeList(
      {super.key, required this.auth, required this.settings});

  @override
  State<_AltShotTeeTimeList> createState() => _AltShotTeeTimeListState();
}

class _AltShotTeeTimeListState extends State<_AltShotTeeTimeList> {
  late Future<List<AltShotTeeTime>> _future;

  ApiClient get _api => ApiClient(
      baseUrl: widget.settings.baseUrl, token: widget.auth.token ?? '');

  @override
  void initState() {
    super.initState();
    _future = _api.listAltShotTeeTimes();
  }

  void refresh() => _refresh();

  Future<void> _refresh() async {
    final f = _api.listAltShotTeeTimes();
    setState(() => _future = f);
    await f;
  }

  @override
  Widget build(BuildContext context) {
    return AsyncBody<List<AltShotTeeTime>>(
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
                      'No alt-shot rounds yet.\nTap New to set one up.',
                      textAlign: TextAlign.center)),
            ],
          );
        }
        return ListView.builder(
          physics: const AlwaysScrollableScrollPhysics(),
          itemCount: teeTimes.length,
          itemBuilder: (context, i) {
            final tt = teeTimes[i];
            final scored = tt.teams.where((t) => t.score != null).length;
            return CourseTeeTimeCard(
              course: tt.course,
              title: tt.label,
              line1: '${tt.course} · ${formatTeeTimeWhen(tt.startsAt)}',
              line2: '${tt.teams.length}/${tt.maxTeams} teams'
                  '${scored > 0 ? ' · $scored scored' : ''} · ${tt.settingsSummary}',
              onTap: () async {
                await Navigator.of(context).push(MaterialPageRoute(
                  builder: (_) => AltShotDetailScreen(
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
    );
  }
}

class _AltShotRecordsView extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;

  const _AltShotRecordsView({required this.auth, required this.settings});

  @override
  State<_AltShotRecordsView> createState() => _AltShotRecordsViewState();
}

class _AltShotRecordsViewState extends State<_AltShotRecordsView> {
  static const _tees = {'front': 'Front', 'middle': 'Middle', 'back': 'Back'};
  static const _pins = {'black': 'Black', 'white': 'White', 'red': 'Red'};
  static const _winds = {'low': 'Low', 'moderate': 'Moderate', 'severe': 'Severe'};
  static const _greens = {'veryfast': 'Very Fast', 'pro': 'Pro'};

  List<GolfCourse> _courses = [];
  GolfCourse? _course;
  int _teamSize = 2;
  String _tee = 'back';
  String _pin = 'black';
  String _wind = 'moderate';
  String _greenSpeed = 'pro';
  Future<List<AltShotRecord>>? _future;
  bool _loading = true;

  ApiClient get _api => ApiClient(
      baseUrl: widget.settings.baseUrl, token: widget.auth.token ?? '');

  @override
  void initState() {
    super.initState();
    _loadCourses();
  }

  Future<void> _loadCourses() async {
    try {
      final courses = await _api.getCourses();
      if (!mounted) return;
      setState(() {
        _courses = courses;
        _loading = false;
      });
    } catch (err) {
      if (!mounted) return;
      setState(() => _loading = false);
      showSnack(context, 'Could not load courses: $err', error: true);
    }
  }

  void _reload() {
    setState(() {
      _future = _course == null
          ? null
          : _api.getAltShotRecords(_course!.name, _teamSize,
              teePosition: _tee,
              pinPosition: _pin,
              windStrength: _wind,
              greenSpeed: _greenSpeed);
    });
  }

  void _pick(GolfCourse? c) {
    _course = c;
    _reload();
  }

  void _pickSize(int s) {
    _teamSize = s;
    _reload();
  }

  Widget _filter(String label, String value, Map<String, String> options,
      ValueChanged<String> onChanged) {
    return Expanded(
      child: DropdownButtonFormField<String>(
        initialValue: value,
        decoration: InputDecoration(
          labelText: label,
          border: const OutlineInputBorder(),
          isDense: true,
          contentPadding:
              const EdgeInsets.symmetric(horizontal: 8, vertical: 8),
        ),
        items: options.entries
            .map((e) => DropdownMenuItem(
                value: e.key,
                child: Text(e.value,
                    style: const TextStyle(fontSize: 13))))
            .toList(),
        onChanged: (v) {
          if (v != null) {
            onChanged(v);
            _reload();
          }
        },
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 12, 12, 0),
          child: DropdownButtonFormField<GolfCourse>(
            initialValue: _course,
            decoration: const InputDecoration(
              labelText: 'Course records',
              border: OutlineInputBorder(),
              isDense: true,
            ),
            items: _courses
                .map((c) =>
                    DropdownMenuItem(value: c, child: Text(c.name)))
                .toList(),
            onChanged: _pick,
          ),
        ),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          child: Row(
            children: [
              const Text('Team size'),
              const SizedBox(width: 12),
              SegmentedButton<int>(
                segments: const [
                  ButtonSegment(value: 2, label: Text('2')),
                  ButtonSegment(value: 3, label: Text('3')),
                  ButtonSegment(value: 4, label: Text('4')),
                ],
                selected: {_teamSize},
                onSelectionChanged: (s) => _pickSize(s.first),
              ),
            ],
          ),
        ),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12),
          child: Row(
            children: [
              _filter('Tees', _tee, _tees, (v) => _tee = v),
              const SizedBox(width: 8),
              _filter('Pins', _pin, _pins, (v) => _pin = v),
            ],
          ),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 8, 12, 0),
          child: Row(
            children: [
              _filter('Wind', _wind, _winds, (v) => _wind = v),
              const SizedBox(width: 8),
              _filter('Greens', _greenSpeed, _greens,
                  (v) => _greenSpeed = v),
            ],
          ),
        ),
        const SizedBox(height: 8),
        Expanded(
          child: _course == null
              ? const Center(
                  child: Text('Pick a course to see its alt-shot records.'))
              : AsyncBody<List<AltShotRecord>>(
                  future: _future!,
                  onRefresh: () async {
                    final f = _api.getAltShotRecords(
                        _course!.name, _teamSize,
                        teePosition: _tee,
                        pinPosition: _pin,
                        windStrength: _wind,
                        greenSpeed: _greenSpeed);
                    setState(() => _future = f);
                    await f;
                  },
                  builder: (context, records) {
                    if (records.isEmpty) {
                      return ListView(
                        physics:
                            const AlwaysScrollableScrollPhysics(),
                        children: const [
                          SizedBox(height: 80),
                          Center(
                              child: Text(
                                  'No records yet.\nBe the first to set one.',
                                  textAlign: TextAlign.center)),
                        ],
                      );
                    }
                    return ListView.builder(
                      physics:
                          const AlwaysScrollableScrollPhysics(),
                      itemCount: records.length,
                      itemBuilder: (context, i) {
                        final r = records[i];
                        return Card(
                          margin: const EdgeInsets.symmetric(
                              horizontal: 12, vertical: 6),
                          child: ListTile(
                            onTap: () => Navigator.of(context).push(
                              MaterialPageRoute(
                                builder: (_) =>
                                    AltShotRecordCardScreen(record: r),
                              ),
                            ),
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
                            title: Text(r.teamDisplay,
                                style: const TextStyle(
                                    fontWeight: FontWeight.bold)),
                            subtitle: Column(
                              crossAxisAlignment:
                                  CrossAxisAlignment.start,
                              children: [
                                const SizedBox(height: 2),
                                Text(r.playersLine,
                                    style:
                                        const TextStyle(fontSize: 12)),
                                Text(
                                    '${r.settingsSummary} · ${r.teeTimeLabel}',
                                    style:
                                        const TextStyle(fontSize: 12)),
                              ],
                            ),
                            trailing: Text(r.scoreLine,
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
