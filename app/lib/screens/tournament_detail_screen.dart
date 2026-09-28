import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../services/auth.dart';
import '../widgets/common.dart';
import 'profile_screen.dart';
import 'tee_time_detail_screen.dart';

/// Tournament detail: header info, register/unregister, tabs for
/// Tee Times and Leaderboard.
class TournamentDetailScreen extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;
  final Tournament tournament;

  const TournamentDetailScreen({
    super.key,
    required this.auth,
    required this.settings,
    required this.tournament,
  });

  @override
  State<TournamentDetailScreen> createState() => _TournamentDetailScreenState();
}

class _TournamentDetailScreenState extends State<TournamentDetailScreen> {
  late Tournament _tournament;
  bool _busy = false;
  bool _isCrew = false;
  bool _isAdmin = false;

  ApiClient get _api => ApiClient(
      baseUrl: widget.settings.baseUrl, token: widget.auth.token ?? '');

  @override
  void initState() {
    super.initState();
    _tournament = widget.tournament;
    _loadFlags();
  }

  Future<void> _loadFlags() async {
    try {
      final me = await _api.getMe();
      if (mounted) {
        setState(() {
          _isCrew = me.isCrew;
          _isAdmin = me.isAdmin;
        });
      }
    } catch (_) {
      // Flags stay false — crew actions just stay hidden.
    }
  }

  Tournament _withRegistered(bool registered) => Tournament(
        id: _tournament.id,
        name: _tournament.name,
        format: _tournament.format,
        holes: _tournament.holes,
        course: _tournament.course,
        status: _tournament.status,
        startDate: _tournament.startDate,
        endDate: _tournament.endDate,
        teePosition: _tournament.teePosition,
        pinPosition: _tournament.pinPosition,
        windStrength: _tournament.windStrength,
        greenSpeed: _tournament.greenSpeed,
        registered: registered,
        pars: _tournament.pars,
        numRounds: _tournament.numRounds,
        rounds: _tournament.rounds,
      );

  Future<void> _register() async {
    setState(() => _busy = true);
    try {
      final res = await _api.register(_tournament.id);
      final already = res['already'] == true;
      setState(() => _tournament = _withRegistered(true));
      if (mounted) {
        showSnack(
            context,
            already
                ? 'Already registered.'
                : 'Registered. Good luck out there!');
      }
    } on ApiException catch (e) {
      if (!mounted) return;
      if (e.code == 'timezone_required') {
        showSnack(context, friendlyApiMessage(e), error: true);
        Navigator.of(context).push(MaterialPageRoute(
          builder: (_) =>
              ProfileScreen(auth: widget.auth, settings: widget.settings),
        ));
      } else {
        showSnack(context, friendlyApiMessage(e), error: true);
      }
    } catch (e) {
      if (mounted) showSnack(context, 'Registration failed: $e', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _unregister() async {
    setState(() => _busy = true);
    try {
      await _api.unregister(_tournament.id);
      setState(() => _tournament = _withRegistered(false));
      if (mounted) showSnack(context, 'Unregistered.');
    } on ApiException catch (e) {
      if (mounted) showSnack(context, friendlyApiMessage(e), error: true);
    } catch (e) {
      if (mounted) showSnack(context, 'Failed: $e', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = _tournament;
    return DefaultTabController(
      length: 2,
      child: Scaffold(
        appBar: AppBar(
          title: Text(t.name),
          actions: [if (_isCrew) _crewMenu(t)],
          bottom: const TabBar(tabs: [
            Tab(text: 'Tee Times'),
            Tab(text: 'Leaderboard'),
          ]),
        ),
        body: Column(
          children: [
            _header(t),
            Expanded(
              child: TabBarView(
                children: [
                  _TeeTimesTab(
                    auth: widget.auth,
                    settings: widget.settings,
                    tournament: t,
                  ),
                  _LeaderboardTab(
                    auth: widget.auth,
                    settings: widget.settings,
                    tournament: t,
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _crewMenu(Tournament t) {
    final done = t.status == 'completed';
    return PopupMenuButton<String>(
      icon: const Icon(Icons.more_vert),
      onSelected: _onCrewAction,
      itemBuilder: (_) => [
        const PopupMenuItem(value: 'edit', child: Text('Edit details')),
        if (!done)
          const PopupMenuItem(
              value: 'complete', child: Text('Complete tournament')),
        if (!done)
          const PopupMenuItem(value: 'end', child: Text('End tournament')),
        if (_isAdmin)
          const PopupMenuItem(
            value: 'delete',
            child: Text('Delete tournament',
                style: TextStyle(color: Colors.red)),
          ),
      ],
    );
  }

  Future<void> _onCrewAction(String action) async {
    switch (action) {
      case 'edit':
        await _editDetails();
      case 'complete':
        await _confirmFinish(
          title: 'Complete tournament?',
          body:
              'This posts the final standings in #event-signups and awards season points.',
          run: () => _api.completeTournament(_tournament.id),
          done: 'Tournament completed — final standings posted.',
        );
      case 'end':
        await _confirmFinish(
          title: 'End tournament?',
          body:
              'This closes the tournament right now with no final standings and no season points.',
          run: () => _api.endTournament(_tournament.id),
          done: 'Tournament ended.',
        );
      case 'delete':
        await _confirmDelete();
    }
  }

  /// Swap in a fresh Tournament from the API while keeping the local
  /// registration flag (the management endpoints don't return it).
  void _replace(Tournament fresh) {
    setState(() => _tournament = Tournament(
          id: fresh.id,
          name: fresh.name,
          format: fresh.format,
          holes: fresh.holes,
          course: fresh.course,
          status: fresh.status,
          startDate: fresh.startDate,
          endDate: fresh.endDate,
          teePosition: fresh.teePosition,
          pinPosition: fresh.pinPosition,
          windStrength: fresh.windStrength,
          greenSpeed: fresh.greenSpeed,
          registered: _tournament.registered,
          pars: fresh.pars,
          numRounds: fresh.numRounds,
          rounds: fresh.rounds,
        ));
  }

  Future<void> _editDetails() async {
    final nameCtrl = TextEditingController(text: _tournament.name);
    final courseCtrl = TextEditingController(text: _tournament.course ?? '');
    DateTime? start = _parseDate(_tournament.startDate);
    DateTime? end = _parseDate(_tournament.endDate);

    final saved = await showDialog<bool>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDlg) => AlertDialog(
          title: const Text('Edit tournament'),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                TextField(
                  controller: nameCtrl,
                  decoration: const InputDecoration(labelText: 'Name'),
                ),
                TextField(
                  controller: courseCtrl,
                  decoration: const InputDecoration(labelText: 'Course'),
                ),
                const SizedBox(height: 8),
                Row(
                  children: [
                    Expanded(
                      child: OutlinedButton(
                        onPressed: () async {
                          final picked = await showDatePicker(
                            context: ctx,
                            initialDate: start ?? DateTime.now(),
                            firstDate: DateTime(DateTime.now().year - 1),
                            lastDate: DateTime(DateTime.now().year + 2),
                          );
                          if (picked != null) setDlg(() => start = picked);
                        },
                        child: Text(start == null
                            ? 'Start date'
                            : _iso(start!)),
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: OutlinedButton(
                        onPressed: () async {
                          final picked = await showDatePicker(
                            context: ctx,
                            initialDate: end ?? start ?? DateTime.now(),
                            firstDate: DateTime(DateTime.now().year - 1),
                            lastDate: DateTime(DateTime.now().year + 2),
                          );
                          if (picked != null) setDlg(() => end = picked);
                        },
                        child:
                            Text(end == null ? 'End date' : _iso(end!)),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(ctx).pop(false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.of(ctx).pop(true),
              child: const Text('Save'),
            ),
          ],
        ),
      ),
    );
    if (saved != true || !mounted) return;

    final fields = <String, dynamic>{};
    final name = nameCtrl.text.trim();
    if (name.isNotEmpty && name != _tournament.name) fields['name'] = name;
    final course = courseCtrl.text.trim();
    if (course.isNotEmpty && course != (_tournament.course ?? '')) {
      fields['course'] = course;
    }
    final startIso = start == null ? null : _iso(start!);
    if (startIso != _tournament.startDate) fields['start_date'] = startIso;
    final endIso = end == null ? null : _iso(end!);
    if (endIso != _tournament.endDate) fields['end_date'] = endIso;
    if (fields.isEmpty) {
      showSnack(context, 'Nothing changed.');
      return;
    }

    setState(() => _busy = true);
    try {
      final updated = await _api.editTournament(_tournament.id, fields);
      if (mounted) {
        _replace(updated);
        showSnack(context, 'Tournament updated.');
      }
    } on ApiException catch (e) {
      if (mounted) showSnack(context, friendlyApiMessage(e), error: true);
    } catch (e) {
      if (mounted) showSnack(context, 'Update failed: $e', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _confirmFinish({
    required String title,
    required String body,
    required Future<void> Function() run,
    required String done,
  }) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(title),
        content: Text(body),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            child: const Text('Confirm'),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    setState(() => _busy = true);
    try {
      await run();
      if (mounted) {
        _replace(_withStatus('completed'));
        showSnack(context, done);
      }
    } on ApiException catch (e) {
      if (mounted) showSnack(context, friendlyApiMessage(e), error: true);
    } catch (e) {
      if (mounted) showSnack(context, 'Failed: $e', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Tournament _withStatus(String status) => Tournament(
        id: _tournament.id,
        name: _tournament.name,
        format: _tournament.format,
        holes: _tournament.holes,
        course: _tournament.course,
        status: status,
        startDate: _tournament.startDate,
        endDate: _tournament.endDate,
        teePosition: _tournament.teePosition,
        pinPosition: _tournament.pinPosition,
        windStrength: _tournament.windStrength,
        greenSpeed: _tournament.greenSpeed,
        registered: _tournament.registered,
        pars: _tournament.pars,
        numRounds: _tournament.numRounds,
        rounds: _tournament.rounds,
      );

  Future<void> _confirmDelete() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Delete tournament?'),
        content: Text(
            'This permanently deletes "${_tournament.name}" — registrations, tee times and scores go with it. This can\'t be undone.'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('Keep it'),
          ),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: Colors.red),
            onPressed: () => Navigator.of(ctx).pop(true),
            child: const Text('Delete forever'),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    setState(() => _busy = true);
    try {
      await _api.deleteTournament(_tournament.id);
      if (mounted) {
        showSnack(context, 'Tournament deleted.');
        Navigator.of(context).pop();
      }
    } on ApiException catch (e) {
      if (mounted) showSnack(context, friendlyApiMessage(e), error: true);
    } catch (e) {
      if (mounted) showSnack(context, 'Delete failed: $e', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  DateTime? _parseDate(String? iso) {
    if (iso == null || iso.isEmpty) return null;
    try {
      return DateTime.parse(iso);
    } catch (_) {
      return null;
    }
  }

  String _iso(DateTime d) =>
      '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

  Widget _header(Tournament t) {
    return Card(
      margin: const EdgeInsets.all(12),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(t.name,
                      style: const TextStyle(
                          fontSize: 20, fontWeight: FontWeight.bold)),
                ),
                StatusChip(status: t.status),
              ],
            ),
            const SizedBox(height: 8),
            Text(t.course ?? 'Course TBD'),
            Text(
                '${t.format ?? 'Format TBD'}${t.holes != null ? ' · ${t.holes} holes' : ''}${t.isMultiRound ? ' · 🔁 ${t.numRounds} rounds' : ''}'),
            Text(formatDateRange(t.startDate, t.endDate)),
            if (t.settingsSummary.isNotEmpty) ...[
              const SizedBox(height: 4),
              Text(t.settingsSummary,
                  style: const TextStyle(color: Colors.grey, fontSize: 13)),
            ],
            if (t.isMultiRound) ...[
              const SizedBox(height: 8),
              ...t.rounds.map((r) => Padding(
                    padding: const EdgeInsets.only(top: 2),
                    child: Text(
                      'Round ${r.roundNumber}: ${r.settingsSummary}',
                      style: const TextStyle(
                          color: Colors.grey, fontSize: 13),
                    ),
                  )),
            ],
            const SizedBox(height: 12),
            SizedBox(
              width: double.infinity,
              child: t.registered
                  ? OutlinedButton(
                      onPressed: _busy ? null : _unregister,
                      child: _busy
                          ? const SizedBox(
                              width: 18,
                              height: 18,
                              child:
                                  CircularProgressIndicator(strokeWidth: 2))
                          : const Text('Unregister'),
                    )
                  : ElevatedButton(
                      onPressed: _busy ? null : _register,
                      child: _busy
                          ? const SizedBox(
                              width: 18,
                              height: 18,
                              child:
                                  CircularProgressIndicator(strokeWidth: 2))
                          : const Text('Register'),
                    ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Tee Times tab: list + create form.
class _TeeTimesTab extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;
  final Tournament tournament;

  const _TeeTimesTab({
    required this.auth,
    required this.settings,
    required this.tournament,
  });

  @override
  State<_TeeTimesTab> createState() => _TeeTimesTabState();
}

class _TeeTimesTabState extends State<_TeeTimesTab> {
  late Future<_TeeTimesData> _future;

  ApiClient get _api => ApiClient(
      baseUrl: widget.settings.baseUrl, token: widget.auth.token ?? '');

  @override
  void initState() {
    super.initState();
    _future = _load();
  }

  Future<_TeeTimesData> _load() async {
    final me = await _api.getMe();
    final times = await _api.getTeeTimes(widget.tournament.id);
    return _TeeTimesData(me: me, teeTimes: times);
  }

  Future<void> _refresh() async {
    final f = _load();
    setState(() => _future = f);
    await f;
  }

  Future<void> _showCreateForm() async {
    final labelCtrl = TextEditingController();
    DateTime date = DateTime.now().add(const Duration(days: 1));
    TimeOfDay time = const TimeOfDay(hour: 18, minute: 0);
    int maxPlayers = 4;

    final created = await showDialog<bool>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDlg) => AlertDialog(
          title: const Text('New tee time'),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                TextField(
                  controller: labelCtrl,
                  decoration: const InputDecoration(
                      labelText: 'Label',
                      hintText: 'e.g. Friday crew round'),
                ),
                const SizedBox(height: 12),
                Row(
                  children: [
                    Expanded(
                      child: OutlinedButton(
                        onPressed: () async {
                          final picked = await showDatePicker(
                            context: ctx,
                            initialDate: date,
                            firstDate: DateTime.now()
                                .subtract(const Duration(days: 1)),
                            lastDate: DateTime.now()
                                .add(const Duration(days: 365)),
                          );
                          if (picked != null) setDlg(() => date = picked);
                        },
                        child: Text(
                            '${date.year}-${date.month.toString().padLeft(2, '0')}-${date.day.toString().padLeft(2, '0')}'),
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: OutlinedButton(
                        onPressed: () async {
                          final picked = await showTimePicker(
                              context: ctx, initialTime: time);
                          if (picked != null) setDlg(() => time = picked);
                        },
                        child: Text(time.format(ctx)),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    const Text('Max players'),
                    Row(
                      children: [
                        IconButton(
                          icon: const Icon(Icons.remove),
                          onPressed: maxPlayers > 1
                              ? () => setDlg(() => maxPlayers--)
                              : null,
                        ),
                        Text('$maxPlayers',
                            style:
                                const TextStyle(fontWeight: FontWeight.bold)),
                        IconButton(
                          icon: const Icon(Icons.add),
                          onPressed: maxPlayers < 4
                              ? () => setDlg(() => maxPlayers++)
                              : null,
                        ),
                      ],
                    ),
                  ],
                ),
              ],
            ),
          ),
          actions: [
            TextButton(
                onPressed: () => Navigator.of(ctx).pop(false),
                child: const Text('Cancel')),
            ElevatedButton(
              onPressed: () async {
                try {
                  final dateStr =
                      '${date.year}-${date.month.toString().padLeft(2, '0')}-${date.day.toString().padLeft(2, '0')}';
                  final timeStr =
                      '${time.hour.toString().padLeft(2, '0')}:${time.minute.toString().padLeft(2, '0')}';
                  await _api.createTeeTime(
                    widget.tournament.id,
                    label: labelCtrl.text.trim().isEmpty
                        ? 'Tee time'
                        : labelCtrl.text.trim(),
                    date: dateStr,
                    time: timeStr,
                    maxPlayers: maxPlayers,
                  );
                  if (ctx.mounted) Navigator.of(ctx).pop(true);
                } on ApiException catch (e) {
                  if (ctx.mounted) {
                    showSnack(ctx, friendlyApiMessage(e), error: true);
                  }
                } catch (e) {
                  if (ctx.mounted) {
                    showSnack(ctx, 'Failed: $e', error: true);
                  }
                }
              },
              child: const Text('Create'),
            ),
          ],
        ),
      ),
    );
    if (created == true) _refresh();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: AsyncBody<_TeeTimesData>(
        future: _future,
        onRefresh: _refresh,
        builder: (context, data) {
          if (data.teeTimes.isEmpty) {
            return ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              children: const [
                SizedBox(height: 120),
                Center(child: Text('No tee times yet. Create one below.')),
              ],
            );
          }
          return ListView.builder(
            physics: const AlwaysScrollableScrollPhysics(),
            itemCount: data.teeTimes.length,
            itemBuilder: (context, i) {
              final tt = data.teeTimes[i];
              final inIt =
                  tt.players.any((p) => p.discordId == data.me.discordId);
              final isCreator = tt.createdBy == data.me.discordId;
              return Card(
                margin:
                    const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                child: ListTile(
                  title: Text(tt.label),
                  subtitle: Text(
                      '${formatLocal(tt.startsAtUtc)} · ${tt.spotsFilled}/${tt.maxPlayers} players${isCreator ? ' · yours' : ''}'),
                  trailing: inIt
                      ? const Icon(Icons.check, color: Colors.green)
                      : tt.isFull
                          ? const Text('Full',
                              style: TextStyle(color: Colors.grey))
                          : const Icon(Icons.chevron_right),
                  onTap: () async {
                    await Navigator.of(context).push(MaterialPageRoute(
                      builder: (_) => TeeTimeDetailScreen(
                        auth: widget.auth,
                        settings: widget.settings,
                        tournament: widget.tournament,
                        teeTimeId: tt.id,
                        myDiscordId: data.me.discordId,
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
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _showCreateForm,
        icon: const Icon(Icons.add),
        label: const Text('New tee time'),
      ),
    );
  }
}

class _TeeTimesData {
  final PlayerMe me;
  final List<TeeTime> teeTimes;
  _TeeTimesData({required this.me, required this.teeTimes});
}

/// Leaderboard tab: ranked entries rendered generically.
class _LeaderboardTab extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;
  final Tournament tournament;

  const _LeaderboardTab({
    required this.auth,
    required this.settings,
    required this.tournament,
  });

  @override
  State<_LeaderboardTab> createState() => _LeaderboardTabState();
}

class _LeaderboardTabState extends State<_LeaderboardTab> {
  late Future<List<LeaderboardEntry>> _future;

  ApiClient get _api => ApiClient(
      baseUrl: widget.settings.baseUrl, token: widget.auth.token ?? '');

  @override
  void initState() {
    super.initState();
    _future = _api.getLeaderboard(widget.tournament.id);
  }

  Future<void> _refresh() async {
    final f = _api.getLeaderboard(widget.tournament.id);
    setState(() => _future = f);
    await f;
  }

  String _formatToPar(String? toPar) {
    if (toPar == null || toPar.isEmpty || toPar == 'null') return '–';
    final n = int.tryParse(toPar);
    if (n == null) return toPar;
    if (n == 0) return 'E';
    return n > 0 ? '+$n' : '$n';
  }

  @override
  Widget build(BuildContext context) {
    return AsyncBody<List<LeaderboardEntry>>(
      future: _future,
      onRefresh: _refresh,
      builder: (context, entries) {
        if (entries.isEmpty) {
          return ListView(
            physics: const AlwaysScrollableScrollPhysics(),
            children: const [
              SizedBox(height: 120),
              Center(child: Text('No scores posted yet.')),
            ],
          );
        }
        return ListView.builder(
          physics: const AlwaysScrollableScrollPhysics(),
          itemCount: entries.length,
          itemBuilder: (context, i) {
            final e = entries[i];
            final rank = e.rank ?? '${i + 1}';
            final rp = e.roundsPlayed;
            final nr = e.numRounds;
            final progress = (rp != null && nr != null && nr > 1)
                ? '$rp/$nr rounds'
                : null;
            final breakdown = e.roundBreakdown;
            return ExpansionTile(
              leading: CircleAvatar(
                backgroundColor:
                    i == 0 ? Colors.amber.shade700 : Colors.grey.shade300,
                child: Text(rank,
                    style: TextStyle(
                        color: i == 0 ? Colors.white : Colors.black87,
                        fontWeight: FontWeight.bold)),
              ),
              title: Text(e.name,
                  style: const TextStyle(fontWeight: FontWeight.w600)),
              subtitle: progress != null ? Text(progress) : null,
              trailing: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(_formatToPar(e.toPar),
                      style: const TextStyle(
                          fontWeight: FontWeight.bold, fontSize: 16)),
                  const SizedBox(width: 12),
                  Text('Total ${e.total ?? '–'}',
                      style: const TextStyle(color: Colors.grey)),
                ],
              ),
              children: breakdown.isEmpty
                  ? []
                  : [
                      Padding(
                        padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
                        child: Column(
                          children: breakdown.map((d) {
                            final rn =
                                (d['round_number'] as num?)?.toInt() ?? 0;
                            final tot =
                                (d['total'] as num?)?.toInt();
                            final tp =
                                (d['to_par'] as num?)?.toInt();
                            return Row(
                              mainAxisAlignment:
                                  MainAxisAlignment.spaceBetween,
                              children: [
                                Text('Round $rn',
                                    style: const TextStyle(
                                        color: Colors.grey)),
                                Text(
                                    '${_formatToPar(tp?.toString())} · ${tot ?? '–'}',
                                    style: const TextStyle(
                                        color: Colors.grey)),
                              ],
                            );
                          }).toList(),
                        ),
                      ),
                    ],
            );
          },
        );
      },
    );
  }
}
