import 'package:flutter/material.dart';
import 'dart:async';

import '../models/models.dart';
import '../services/api_client.dart';
import '../services/auth.dart';
import '../widgets/common.dart';
import '../widgets/course_art.dart';
import '../widgets/season_standings_view.dart';
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
    final isTeamFormat = const {'best_ball', 'alt_shot', 'scramble'}
        .contains(t.format);
    final tabs = <Widget>[
      const Tab(text: 'Tee Times'),
      const Tab(text: 'Leaderboard'),
      const Tab(text: 'Players'),
      if (isTeamFormat) const Tab(text: 'Teams'),
    ];
    return DefaultTabController(
      length: tabs.length,
      child: Scaffold(
        appBar: AppBar(
          title: Text(t.name),
          actions: [if (_isCrew) _crewMenu(t)],
          bottom: TabBar(tabs: tabs),
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
                  _PlayersTab(
                    auth: widget.auth,
                    settings: widget.settings,
                    tournament: t,
                  ),
                  if (isTeamFormat)
                    _TeamsTab(
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
        if (t.isMultiRound)
          const PopupMenuItem(
              value: 'rounds', child: Text('Edit round dates & settings')),
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
      case 'rounds':
        await _editRounds();
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
          seasons: fresh.seasons,
        ));
  }

  Future<void> _editDetails() async {
    final nameCtrl = TextEditingController(text: _tournament.name);
    final courseCtrl = TextEditingController(text: _tournament.course ?? '');
    DateTime? start = _parseDate(_tournament.startDate);
    DateTime? end = _parseDate(_tournament.endDate);

    // Season correction (Cayden 2026-10-18): the pick can be changed
    // here; points move with it server-side. Options = active seasons
    // plus the current assignment when it isn't active anymore (so
    // the dialog opens showing the truth).
    List<Season>? seasonOptions;
    int? seasonPick = _tournament.season?.id;
    try {
      final all = await _api.listSeasons();
      seasonOptions = all.where((s) => s.isActive).toList();
      final current = _tournament.season;
      if (current != null &&
          !seasonOptions.any((s) => s.id == current.id)) {
        seasonOptions.add(current);
      }
    } catch (e) {
      seasonOptions = null; // picker hidden; season left untouched
    }
    if (!mounted) return;

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
                if (seasonOptions != null) ...[
                  SeasonPicker(
                    activeSeasons: seasonOptions,
                    value: seasonPick,
                    onChanged: (v) => setDlg(() => seasonPick = v),
                  ),
                  const SizedBox(height: 8),
                ],
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
    final seasonChanged =
        seasonOptions != null && seasonPick != _tournament.season?.id;
    if (fields.isEmpty && !seasonChanged) {
      showSnack(context, 'Nothing changed.');
      return;
    }

    setState(() => _busy = true);
    try {
      if (fields.isNotEmpty) {
        final updated = await _api.editTournament(_tournament.id, fields);
        if (mounted) _replace(updated);
      }
      if (seasonChanged) {
        await _api.setTournamentSeason(_tournament.id, seasonPick);
        // Refresh so the header shows the new assignment.
        final all = await _api.getTournaments();
        final fresh =
            all.where((t) => t.id == _tournament.id).firstOrNull;
        if (mounted && fresh != null) _replace(fresh);
      }
      if (mounted) showSnack(context, 'Tournament updated.');
    } on ApiException catch (e) {
      if (mounted) showSnack(context, friendlyApiMessage(e), error: true);
    } catch (e) {
      if (mounted) showSnack(context, 'Update failed: $e', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// Crew editor for each round's Golf+ settings and date window.
  Future<void> _editRounds() async {
    final rounds = _tournament.rounds;
    if (rounds.isEmpty) return;
    const tees = {'front': 'Front', 'middle': 'Middle', 'back': 'Back'};
    const pins = {'black': 'Black', 'white': 'White', 'red': 'Red'};
    const winds = {'low': 'Low', 'moderate': 'Moderate', 'severe': 'Severe'};
    final starts = rounds.map((r) => _parseDate(r.startDate)).toList();
    final ends = rounds.map((r) => _parseDate(r.endDate)).toList();
    final teeVals = rounds.map((r) => r.teePosition ?? 'middle').toList();
    final pinVals = rounds.map((r) => r.pinPosition ?? 'white').toList();
    final windVals = rounds.map((r) => r.windStrength ?? 'moderate').toList();

    final saved = await showDialog<bool>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDlg) {
          DropdownButtonFormField<String> field(String label, String value,
              Map<String, String> options, void Function(String) onChanged) {
            return DropdownButtonFormField<String>(
              initialValue: value,
              decoration: InputDecoration(
                  labelText: label,
                  border: const OutlineInputBorder(),
                  isDense: true),
              items: options.entries
                  .map((e) => DropdownMenuItem(
                      value: e.key, child: Text(e.value)))
                  .toList(),
              onChanged: (v) {
                if (v != null) setDlg(() => onChanged(v));
              },
            );
          }

          Future<void> pickDate(int i, bool isStart) async {
            final picked = await showDatePicker(
              context: ctx,
              initialDate: (isStart ? starts[i] : ends[i]) ??
                  _parseDate(_tournament.startDate) ??
                  DateTime.now(),
              firstDate: DateTime(DateTime.now().year - 1),
              lastDate: DateTime(DateTime.now().year + 2),
            );
            if (picked != null) {
              setDlg(() {
                if (isStart) {
                  starts[i] = picked;
                } else {
                  ends[i] = picked;
                }
              });
            }
          }

          return AlertDialog(
            title: const Text('Round dates & settings'),
            content: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  for (var i = 0; i < rounds.length; i++) ...[
                    if (i > 0) const SizedBox(height: 12),
                    Text('Round ${rounds[i].roundNumber}',
                        style: const TextStyle(fontWeight: FontWeight.bold)),
                    const SizedBox(height: 6),
                    Row(
                      children: [
                        Expanded(
                            child: OutlinedButton(
                          onPressed: () => pickDate(i, true),
                          child: Text(
                              starts[i] == null
                                  ? 'Start'
                                  : _iso(starts[i]!),
                              style: const TextStyle(fontSize: 12)),
                        )),
                        const SizedBox(width: 8),
                        Expanded(
                            child: OutlinedButton(
                          onPressed: () => pickDate(i, false),
                          child: Text(
                              ends[i] == null ? 'End' : _iso(ends[i]!),
                              style: const TextStyle(fontSize: 12)),
                        )),
                      ],
                    ),
                    const SizedBox(height: 6),
                    Row(
                      children: [
                        Expanded(
                            child: field('Tees', teeVals[i], tees,
                                (v) => teeVals[i] = v)),
                        const SizedBox(width: 6),
                        Expanded(
                            child: field('Pins', pinVals[i], pins,
                                (v) => pinVals[i] = v)),
                        const SizedBox(width: 6),
                        Expanded(
                            child: field('Wind', windVals[i], winds,
                                (v) => windVals[i] = v)),
                      ],
                    ),
                  ],
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
          );
        },
      ),
    );
    if (saved != true || !mounted) return;

    setState(() => _busy = true);
    try {
      Tournament? latest;
      for (var i = 0; i < rounds.length; i++) {
        final fields = <String, dynamic>{};
        if (teeVals[i] != (rounds[i].teePosition ?? 'middle')) {
          fields['tee_position'] = teeVals[i];
        }
        if (pinVals[i] != (rounds[i].pinPosition ?? 'white')) {
          fields['pin_position'] = pinVals[i];
        }
        if (windVals[i] != (rounds[i].windStrength ?? 'moderate')) {
          fields['wind_strength'] = windVals[i];
        }
        final sIso = starts[i] == null ? null : _iso(starts[i]!);
        final eIso = ends[i] == null ? null : _iso(ends[i]!);
        if (sIso != rounds[i].startDate) fields['start_date'] = sIso;
        if (eIso != rounds[i].endDate) fields['end_date'] = eIso;
        if (fields.isEmpty) continue;
        latest = await _api.editRound(
            _tournament.id, rounds[i].roundNumber, fields);
      }
      if (mounted) {
        if (latest != null) _replace(latest);
        showSnack(context, 'Rounds updated.');
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

  DateTime? _parseDate(String? iso) {    if (iso == null || iso.isEmpty) return null;
    try {
      return DateTime.parse(iso);
    } catch (_) {
      return null;
    }
  }

  String _iso(DateTime d) =>
      '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

  Widget _header(Tournament t) {
    final art = courseArtAsset(t.course);
    const overlay = LinearGradient(
      begin: Alignment.topCenter,
      end: Alignment.bottomCenter,
      colors: [
        Color.fromRGBO(0, 0, 0, 0.25),
        Color.fromRGBO(0, 0, 0, 0.72),
      ],
    );
    return Container(
      width: double.infinity,
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
                colors: [Color(0xFF1B5E20), Color(0xFF0D3311)],
              )
            : null,
      ),
      child: Container(
        decoration: const BoxDecoration(gradient: overlay),
        padding: const EdgeInsets.fromLTRB(16, 20, 16, 16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(t.name,
                      style: const TextStyle(
                          fontSize: 22,
                          fontWeight: FontWeight.bold,
                          color: Colors.white)),
                ),
                StatusChip(status: t.status),
              ],
            ),
            const SizedBox(height: 8),
            Text(t.course ?? 'Course TBD',
                style:
                    const TextStyle(color: Colors.white, fontSize: 15)),
            Text(
                '${t.format ?? 'Format TBD'}${t.holes != null ? ' · ${t.holes} holes' : ''}${t.isMultiRound ? ' · 🔁 ${t.numRounds} rounds' : ''}',
                style: const TextStyle(
                    color: Colors.white70, fontSize: 14)),
            Text(formatDateRange(t.startDate, t.endDate),
                style: const TextStyle(
                    color: Colors.white70, fontSize: 14)),
            Text(
                t.season != null
                    ? '🏆 Season: ${t.season!.name}'
                    : '🏆 No season',
                style: const TextStyle(
                    color: Colors.white70, fontSize: 14)),
            if (t.isMultiRound) ...[
              const SizedBox(height: 4),
              const Text('Follow settings below for round setup in Golf+',
                  style: TextStyle(
                      color: Colors.white70, fontSize: 13)),
            ] else if (t.settingsSummary.isNotEmpty) ...[
              const SizedBox(height: 4),
              Text(t.settingsSummary,
                  style: const TextStyle(
                      color: Colors.white70, fontSize: 13)),
            ],
            if (t.isMultiRound) ...[
              const SizedBox(height: 8),
              ...t.rounds.map((r) => Padding(
                    padding: const EdgeInsets.only(top: 2),
                    child: Text(
                      'Round ${r.roundNumber}: ${r.settingsSummary}'
                      '${r.datesSummary.isNotEmpty ? ' · ${r.datesSummary}' : ''}',
                      style: const TextStyle(
                          color: Colors.white70, fontSize: 13),
                    ),
                  )),
            ],
            const SizedBox(height: 12),
            SizedBox(
              width: double.infinity,
              child: t.registered
                  ? OutlinedButton(
                      style: OutlinedButton.styleFrom(
                        foregroundColor: Colors.white,
                        side: const BorderSide(color: Colors.white70),
                      ),
                      onPressed: _busy ? null : _unregister,
                      child: _busy
                          ? const SizedBox(
                              width: 18,
                              height: 18,
                              child: CircularProgressIndicator(
                                  strokeWidth: 2, color: Colors.white))
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
  _TeeTimesData? _lastData;

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
    final data = _TeeTimesData(me: me, teeTimes: times);
    _lastData = data;
    return data;
  }

  Future<void> _refresh() async {
    final f = _load();
    setState(() => _future = f);
    await f;
  }

  Future<void> _showCreateForm(_TeeTimesData data) async {
    final labelCtrl = TextEditingController();
    DateTime date = DateTime.now().add(const Duration(days: 1));
    TimeOfDay time = const TimeOfDay(hour: 18, minute: 0);
    int maxPlayers = 4;
    final rounds = widget.tournament.rounds;
    // Rounds this user has already finished (submitted own or team card)
    // can't be booked again, so they leave the picker. Admins keep the
    // full list — they book and fix tee times for other people.
    final bookableRounds = data.me.isAdmin
        ? rounds
        : rounds
            .where((r) => !widget.tournament.myCompletedRounds
                .contains(r.roundNumber))
            .toList();
    int roundNumber =
        bookableRounds.isNotEmpty ? bookableRounds.first.roundNumber : 1;
    final isTeamFormat = const {'best_ball', 'alt_shot', 'scramble'}
        .contains(widget.tournament.format);
    int? pickedTeamId;

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
                if (bookableRounds.isEmpty)
                  const Text(
                    "You've completed every round of this tournament.",
                    style: TextStyle(color: Colors.grey),
                  ),
                if (rounds.length > 1 && bookableRounds.isNotEmpty)
                  DropdownButtonFormField<int>(
                    initialValue: roundNumber,
                    decoration: const InputDecoration(
                      labelText: 'Round',
                      border: OutlineInputBorder(),
                      isDense: true,
                    ),
                    items: bookableRounds
                        .map((r) => DropdownMenuItem(
                              value: r.roundNumber,
                              child: Text(
                                  'Round ${r.roundNumber}${r.datesSummary.isNotEmpty ? ' · ${r.datesSummary}' : ''}'),
                            ))
                        .toList(),
                    onChanged: (v) {
                      if (v != null) setDlg(() => roundNumber = v);
                    },
                  ),
                if (rounds.length > 1) const SizedBox(height: 12),
                if (isTeamFormat)
                  FutureBuilder<List<Map<String, dynamic>>>(
                    future:
                        _api.getTeams(widget.tournament.id),
                    builder: (ctx2, snap) {
                      if (snap.connectionState ==
                          ConnectionState.waiting) {
                        return const Center(
                            child: CircularProgressIndicator());
                      }
                      // Only a team's creator reps it (Cayden 2026-10-12):
                      // non-admins book teams they created; admins may
                      // book any team (its creator reps it on the sheet).
                      final all = snap.data ?? [];
                      final pickable = data.me.isAdmin
                          ? all
                          : all
                              .where((tm) =>
                                  tm['created_by']?.toString() ==
                                  data.me.discordId)
                              .toList();
                      if (pickable.isEmpty) {
                        return Text(
                          data.me.isAdmin
                              ? 'No teams yet — form one in the Teams tab.'
                              : 'Form a team first (Teams tab) — then book a '
                                  'tee time for it. Only the team\'s creator '
                                  'can book it.',
                          style: const TextStyle(color: Colors.grey),
                        );
                      }
                      return DropdownButtonFormField<int>(
                        initialValue: pickedTeamId,
                        decoration: InputDecoration(
                          labelText:
                              data.me.isAdmin ? 'Team' : 'Your team',
                          border: const OutlineInputBorder(),
                          isDense: true,
                        ),
                        items: pickable
                            .map((tm) => DropdownMenuItem<int>(
                                  value: (tm['id'] as num).toInt(),
                                  child: Text(
                                      tm['name']?.toString() ?? 'Team',
                                      overflow:
                                          TextOverflow.ellipsis),
                                ))
                            .toList(),
                        onChanged: (v) =>
                            setDlg(() => pickedTeamId = v),
                      );
                    },
                  ),
                if (isTeamFormat) const SizedBox(height: 12),
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
                              context: ctx,
                              initialTime: time,
                              initialEntryMode: TimePickerEntryMode.input);
                          if (picked != null) setDlg(() => time = picked);
                        },
                        child: Text(time.format(ctx)),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                if (!isTeamFormat)
                  Row(
                    mainAxisAlignment:
                        MainAxisAlignment.spaceBetween,
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
                              style: const TextStyle(
                                  fontWeight: FontWeight.bold)),
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
                if (isTeamFormat)
                  const Text(
                    'One rep per team · up to 2 teams per tee time.',
                    style: TextStyle(
                        color: Colors.grey, fontSize: 12),
                  ),
              ],
            ),
          ),
          actions: [
            TextButton(
                onPressed: () => Navigator.of(ctx).pop(false),
                child: const Text('Cancel')),
            ElevatedButton(
              onPressed: bookableRounds.isEmpty
                  ? null
                  : () async {
                if (isTeamFormat && pickedTeamId == null) {
                  showSnack(ctx, 'Pick which team you\'re repping.',
                      error: true);
                  return;
                }
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
                    roundNumber: roundNumber,
                    teamId: pickedTeamId,
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
              return CourseTeeTimeCard(
                course: widget.tournament.course,
                title: tt.label,
                line1:
                    '${formatLocal(tt.startsAtUtc)} · ${tt.spotsFilled}/${tt.maxPlayers} players${tt.roundNumber > 1 ? ' · Round ${tt.roundNumber}' : ''}${isCreator ? ' · yours' : ''}',
                trailing: inIt
                    ? const Icon(Icons.check, color: Colors.green)
                    : tt.isFull
                        ? const Text('Full',
                            style: TextStyle(color: Colors.white70))
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
              );
            },
          );
        },
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () {
          final d = _lastData;
          if (d != null) _showCreateForm(d);
        },
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
  late Future<LeaderboardData> _future;

  /// 0 = tournament leaderboard, 1 = season points standings.
  int _view = 0;

  Timer? _poll;

  ApiClient get _api => ApiClient(
      baseUrl: widget.settings.baseUrl, token: widget.auth.token ?? '');

  @override
  void initState() {
    super.initState();
    _future = _api.getLeaderboard(widget.tournament.id);
    // Live leaderboard: silently re-fetch every 30s while this screen is
    // open, so newly submitted cards appear without a manual pull. Only
    // the tournament view polls; the season view refreshes on pull only.
    _poll = Timer.periodic(const Duration(seconds: 30), (_) {
      if (mounted && _view == 0) _refresh();
    });
  }

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
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
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
          child: DropdownButtonFormField<int>(
            initialValue: _view,
            decoration: const InputDecoration(
              labelText: 'Standings',
              border: OutlineInputBorder(),
              isDense: true,
            ),
            items: const [
              DropdownMenuItem(value: 0, child: Text('Tournament')),
              DropdownMenuItem(value: 1, child: Text('Season')),
            ],
            onChanged: (v) {
              if (v == null) return;
              setState(() => _view = v);
            },
          ),
        ),
        Expanded(child: _view == 0 ? _tournamentView() : _seasonView()),
      ],
    );
  }

  /// Season points standings, shared with the standalone Season
  /// Standings screen.
  Widget _seasonView() => SeasonStandingsView(api: _api);

  Widget _tournamentView() {
    return AsyncBody<LeaderboardData>(
      future: _future,
      onRefresh: _refresh,
      builder: (context, data) {
        final entries = data.entries;
        final dnf = data.dnf;
        if (entries.isEmpty && dnf.isEmpty) {
          return ListView(
            physics: const AlwaysScrollableScrollPhysics(),
            children: const [
              SizedBox(height: 120),
              Center(child: Text('No scores posted yet.')),
            ],
          );
        }
        return ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          children: [
            ...List.generate(
                entries.length, (i) => _rankedRow(entries[i], i)),
            if (dnf.isNotEmpty)
              const Padding(
                padding: EdgeInsets.fromLTRB(16, 20, 16, 4),
                child: Text('Did not finish',
                    style: TextStyle(
                        fontWeight: FontWeight.bold, fontSize: 14)),
              ),
            ...dnf.map((e) => ListTile(
                  leading: CircleAvatar(
                    backgroundColor: Colors.red.shade50,
                    child: const Text('DNF',
                        style: TextStyle(
                            fontSize: 9,
                            fontWeight: FontWeight.bold,
                            color: Colors.red)),
                  ),
                  title: Text(e.name,
                      style:
                          const TextStyle(fontWeight: FontWeight.w600)),
                  subtitle:
                      Text('Missed Round ${e['missed_round'] ?? '–'}'),
                )),
          ],
        );
      },
    );
  }

  /// One ranked leaderboard row.
  Widget _rankedRow(LeaderboardEntry e, int i) {
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
              title: Text(
                  '${e.name}${e.onCourse ? ' ⛳' : ''}${e.isPending ? ' ⏳' : ''}',
                  style: const TextStyle(fontWeight: FontWeight.w600)),
              subtitle: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  if (e.teamMembers.isNotEmpty)
                    Text(e.teamMembers.join('  &  '),
                        style: const TextStyle(
                            fontSize: 12, color: Colors.green)),
                  if (progress != null) Text(progress),
                  if (e.onCourse)
                    Text('⛳ thru ${e.thru ?? '–'}',
                        style: const TextStyle(
                            color: Colors.green,
                            fontWeight: FontWeight.w600)),
                  if (e.isPending)
                    const Text('⏳ awaiting verification',
                        style: TextStyle(
                            color: Colors.grey,
                            fontStyle: FontStyle.italic)),
                ],
              ),
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
                            final live = d['status'] == 'in_progress';
                            final thru =
                                (d['thru'] as num?)?.toInt();
                            return Row(
                              mainAxisAlignment:
                                  MainAxisAlignment.spaceBetween,
                              children: [
                                Text(
                                    'Round $rn${live ? ' ⛳ thru ${thru ?? '–'}' : ''}',
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
  }
}

/// Players tab: read-only list of every account with a Golf+ username,
/// grouped into registered vs not registered for this tournament.
class _PlayersTab extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;
  final Tournament tournament;

  const _PlayersTab({
    required this.auth,
    required this.settings,
    required this.tournament,
  });

  @override
  State<_PlayersTab> createState() => _PlayersTabState();
}

class _PlayersTabState extends State<_PlayersTab> {
  late Future<List<TournamentPlayer>> _future;

  ApiClient get _api => ApiClient(
      baseUrl: widget.settings.baseUrl, token: widget.auth.token ?? '');

  @override
  void initState() {
    super.initState();
    _future = _api.getTournamentPlayers(widget.tournament.id);
  }

  Future<void> _refresh() async {
    final f = _api.getTournamentPlayers(widget.tournament.id);
    setState(() => _future = f);
    await f;
  }

  @override
  Widget build(BuildContext context) {
    return AsyncBody<List<TournamentPlayer>>(
      future: _future,
      onRefresh: _refresh,
      builder: (context, players) {
        if (players.isEmpty) {
          return ListView(
            physics: const AlwaysScrollableScrollPhysics(),
            children: const [
              SizedBox(height: 120),
              Text(
                'No players with a Golf+ username yet.',
                textAlign: TextAlign.center,
                style: TextStyle(color: Colors.grey),
              ),
            ],
          );
        }
        final registered = players.where((p) => p.registered).toList();
        final unregistered = players.where((p) => !p.registered).toList();
        return ListView(
          padding: const EdgeInsets.symmetric(vertical: 8),
          children: [
            _sectionHeader(
                'Registered', registered.length, Colors.green.shade700),
            for (final p in registered) _playerTile(p),
            _sectionHeader(
                'Not registered', unregistered.length, Colors.grey.shade600),
            for (final p in unregistered) _playerTile(p),
          ],
        );
      },
    );
  }

  Widget _sectionHeader(String label, int count, Color color) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
      child: Row(
        children: [
          Container(
            width: 10,
            height: 10,
            decoration: BoxDecoration(color: color, shape: BoxShape.circle),
          ),
          const SizedBox(width: 8),
          Text(
            '$label · $count',
            style: TextStyle(
              fontSize: 14,
              fontWeight: FontWeight.bold,
              color: color,
            ),
          ),
        ],
      ),
    );
  }

  Widget _playerTile(TournamentPlayer p) {
    // Handle-first display rule: Golf+ username only.
    return ListTile(
      contentPadding:
          const EdgeInsets.symmetric(horizontal: 16, vertical: 2),
      leading: CircleAvatar(
        backgroundColor: p.registered
            ? Colors.green.shade100
            : Colors.grey.shade200,
        child: Icon(
          Icons.person,
          color: p.registered
              ? Colors.green.shade700
              : Colors.grey.shade600,
        ),
      ),
      title: Text(
        p.golfplusHandle,
        style: const TextStyle(fontWeight: FontWeight.w600),
      ),
      trailing: p.registered
          ? Chip(
              label: const Text('Registered'),
              labelStyle: TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.bold,
                  color: Colors.green.shade800),
              backgroundColor: Colors.green.shade100,
              visualDensity: VisualDensity.compact,
            )
          : const Chip(
              label: Text('Not registered'),
              labelStyle:
                  TextStyle(fontSize: 12, color: Colors.grey),
              backgroundColor: Color(0xFFF0F0F0),
              visualDensity: VisualDensity.compact,
            ),
    );
  }
}

/// Teams tab for team-format tournaments (alt-shot / best ball / scramble).
/// Teams are tournament-level and persist across rounds. Forming a team:
/// pick a registered partner from the dropdown, or type a name for a
/// partner who isn't in the app.
class _TeamsTab extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;
  final Tournament tournament;

  const _TeamsTab({
    required this.auth,
    required this.settings,
    required this.tournament,
  });

  @override
  State<_TeamsTab> createState() => _TeamsTabState();
}

class _TeamsTabState extends State<_TeamsTab> {
  Future<List<Map<String, dynamic>>>? _future;
  String? _myDiscordId;
  bool _isAdmin = false;

  ApiClient get _api => ApiClient(
      baseUrl: widget.settings.baseUrl, token: widget.auth.token ?? '');

  @override
  void initState() {
    super.initState();
    _future = _api.getTeams(widget.tournament.id);
    _loadMe();
  }

  Future<void> _loadMe() async {
    try {
      final me = await _api.getMe();
      if (mounted) {
        setState(() {
          _myDiscordId = me.discordId;
          _isAdmin = me.isAdmin;
        });
      }
    } catch (_) {}
  }

  Future<void> _refresh() async {
    final f = _api.getTeams(widget.tournament.id);
    setState(() => _future = f);
    await f;
  }

  String _memberLabel(Map<String, dynamic> m) {
    final handle = (m['golfplus_handle']?.toString() ?? '').trim();
    if (handle.isNotEmpty) return handle;
    return (m['display_name']?.toString() ?? '?').trim();
  }

  Future<void> _formTeam() async {
    final result = await showDialog<_TeamPartner>(
      context: context,
      builder: (ctx) => _FormTeamDialog(
        api: _api,
        tournamentId: widget.tournament.id,
        myDiscordId: _myDiscordId ?? '',
        adminMode: _isAdmin,
      ),
    );
    if (result == null) return;
    try {
      await _api.createTeam(
        widget.tournament.id,
        partnerDiscordId: result.discordId,
        partnerName: result.typedName,
        playerDiscordId: result.player1Id,
      );
      if (!mounted) return;
      showSnack(context, 'Team formed — good luck!');
      await _refresh();
    } on ApiException catch (e) {
      if (!mounted) return;
      showSnack(context, friendlyApiMessage(e), error: true);
    }
  }

  Future<void> _deleteTeam(Map<String, dynamic> team) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Disband team?'),
        content: Text(
            'Remove team "${team['name']}"? This only works before the team has submitted any scorecards.'),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(ctx).pop(false),
              child: const Text('Cancel')),
          FilledButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            style: FilledButton.styleFrom(
                backgroundColor: Colors.red.shade700),
            child: const Text('Disband'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    try {
      await _api.deleteTeam(
          widget.tournament.id, (team['id'] as num).toInt());
      if (!mounted) return;
      showSnack(context, 'Team disbanded.');
      await _refresh();
    } on ApiException catch (e) {
      if (!mounted) return;
      showSnack(context, friendlyApiMessage(e), error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _formTeam,
        icon: const Icon(Icons.group_add),
        label: const Text('Form a team'),
      ),
      body: FutureBuilder<List<Map<String, dynamic>>>(
        future: _future,
        builder: (context, snap) {
          if (snap.connectionState == ConnectionState.waiting) {
            return const Center(child: CircularProgressIndicator());
          }
          if (snap.hasError) {
            return Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Text('Could not load teams.'),
                  const SizedBox(height: 8),
                  TextButton(
                      onPressed: _refresh, child: const Text('Retry')),
                ],
              ),
            );
          }
          final teams = snap.data ?? [];
          if (teams.isEmpty) {
            return const Center(
              child: Padding(
                padding: EdgeInsets.all(32),
                child: Text(
                  'No teams yet. Tap "Form a team" and pick your partner.',
                  textAlign: TextAlign.center,
                  style: TextStyle(color: Colors.grey),
                ),
              ),
            );
          }
          return RefreshIndicator(
            onRefresh: _refresh,
            child: ListView.builder(
              padding: const EdgeInsets.fromLTRB(12, 12, 12, 80),
              itemCount: teams.length,
              itemBuilder: (context, i) {
                final team = teams[i];
                final members =
                    (team['members'] as List?)?.whereType<Map<String, dynamic>>().toList() ??
                        [];
                final partnerName =
                    (team['partner_name']?.toString() ?? '').trim();
                final labels = members.map(_memberLabel).toList();
                if (partnerName.isNotEmpty) labels.add(partnerName);
                final canDelete = _isAdmin ||
                    team['created_by']?.toString() == _myDiscordId;
                return Card(
                  margin: const EdgeInsets.symmetric(vertical: 6),
                  child: ListTile(
                    leading: const Icon(Icons.groups,
                        color: Colors.green),
                    title: Text(team['name']?.toString() ?? 'Team',
                        style: const TextStyle(
                            fontWeight: FontWeight.bold)),
                    subtitle: Text(labels.join('  &  ')),
                    trailing: canDelete
                        ? IconButton(
                            tooltip: 'Disband team',
                            icon: const Icon(Icons.delete_outline,
                                color: Colors.red),
                            onPressed: () => _deleteTeam(team),
                          )
                        : null,
                  ),
                );
              },
            ),
          );
        },
      ),
    );
  }
}

/// Team choice from the form-team dialog: the partner is either a
/// registered player (discordId) or a typed name. player1Id is set only
/// in admin mode — the admin picks Player 1, who becomes the team's
/// creator and rep; everyone else forms a team as themselves.
class _TeamPartner {
  final String? discordId;
  final String? typedName;
  final String? player1Id;

  const _TeamPartner({this.discordId, this.typedName, this.player1Id});
}

/// Dialog to form a team: pick a registered partner from the dropdown, or
/// type a partner name for someone not in the app. Admins additionally
/// pick Player 1 (any registered player) instead of teaming themselves.
class _FormTeamDialog extends StatefulWidget {
  final ApiClient api;
  final String tournamentId;
  final String myDiscordId;
  final bool adminMode;

  const _FormTeamDialog({
    required this.api,
    required this.tournamentId,
    required this.myDiscordId,
    this.adminMode = false,
  });

  @override
  State<_FormTeamDialog> createState() => _FormTeamDialogState();
}

class _FormTeamDialogState extends State<_FormTeamDialog> {
  bool _typed = false;
  String? _pickedId;
  String? _player1Id;
  final _nameCtrl = TextEditingController();
  Future<List<TournamentPlayer>>? _playersFuture;

  @override
  void initState() {
    super.initState();
    _playersFuture = widget.api.getTournamentPlayers(widget.tournamentId);
  }

  @override
  void dispose() {
    _nameCtrl.dispose();
    super.dispose();
  }

  /// Player 1 is the admin in admin mode until they pick someone;
  /// otherwise it's always the person forming the team.
  String get _effectivePlayer1 =>
      widget.adminMode ? (_player1Id ?? '') : widget.myDiscordId;

  DropdownButtonFormField<String> _playerDropdown({
    required String label,
    required String? value,
    required List<TournamentPlayer> options,
    required ValueChanged<String?> onChanged,
    Key? key,
  }) {
    return DropdownButtonFormField<String>(
      key: key,
      initialValue: value,
      decoration: InputDecoration(
        labelText: label,
        border: const OutlineInputBorder(),
        isDense: true,
      ),
      items: options
          .map((p) => DropdownMenuItem(
                value: p.discordId,
                child: Text(
                  p.golfplusHandle.isNotEmpty
                      ? p.golfplusHandle
                      : p.displayName,
                  overflow: TextOverflow.ellipsis,
                ),
              ))
          .toList(),
      onChanged: onChanged,
    );
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Form a team'),
      content: SizedBox(
        width: double.maxFinite,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (widget.adminMode) ...[
              FutureBuilder<List<TournamentPlayer>>(
                future: _playersFuture,
                builder: (context, snap) {
                  if (snap.connectionState == ConnectionState.waiting) {
                    return const Center(child: CircularProgressIndicator());
                  }
                  final options = (snap.data ?? [])
                      .where((p) => p.registered)
                      .toList();
                  if (options.isEmpty) {
                    return const Text('No registered players yet.',
                        style: TextStyle(color: Colors.grey));
                  }
                  return _playerDropdown(
                    label: 'Player 1 (team creator & rep)',
                    value: _player1Id,
                    options: options,
                    onChanged: (v) => setState(() {
                      _player1Id = v;
                      if (_pickedId == v) _pickedId = null;
                    }),
                  );
                },
              ),
              const SizedBox(height: 12),
            ],
            SegmentedButton<bool>(
              segments: const [
                ButtonSegment(
                    value: false,
                    label: Text('Pick a player'),
                    icon: Icon(Icons.person_search)),
                ButtonSegment(
                    value: true,
                    label: Text('Type a name'),
                    icon: Icon(Icons.edit)),
              ],
              selected: {_typed},
              onSelectionChanged: (s) => setState(() => _typed = s.first),
            ),
            const SizedBox(height: 12),
            if (_typed)
              TextField(
                controller: _nameCtrl,
                textCapitalization: TextCapitalization.words,
                decoration: const InputDecoration(
                  labelText: "Partner's name",
                  hintText: 'As it should appear on the leaderboard',
                  border: OutlineInputBorder(),
                ),
              )
            else
              FutureBuilder<List<TournamentPlayer>>(
                future: _playersFuture,
                builder: (context, snap) {
                  if (snap.connectionState == ConnectionState.waiting) {
                    return const Center(child: CircularProgressIndicator());
                  }
                  final options = (snap.data ?? [])
                      .where((p) =>
                          p.registered &&
                          p.discordId != _effectivePlayer1)
                      .toList();
                  if (options.isEmpty) {
                    return const Text(
                        'No other registered players yet.',
                        style: TextStyle(color: Colors.grey));
                  }
                  return _playerDropdown(
                    key: ValueKey('partner-$_player1Id'),
                    label: 'Partner',
                    value: _pickedId,
                    options: options,
                    onChanged: (v) => setState(() => _pickedId = v),
                  );
                },
              ),
            if (widget.adminMode)
              const Padding(
                padding: EdgeInsets.only(top: 10),
                child: Text(
                  'Player 1 becomes the team\'s creator — they\'re the '
                  'one who reps it on tee times.',
                  style: TextStyle(color: Colors.grey, fontSize: 12),
                ),
              ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: () {
            if (widget.adminMode && _player1Id == null) return;
            final p1 = widget.adminMode ? _player1Id : null;
            if (_typed) {
              final name = _nameCtrl.text.trim();
              if (name.isEmpty) return;
              Navigator.of(context)
                  .pop(_TeamPartner(typedName: name, player1Id: p1));
            } else {
              if (_pickedId == null) return;
              Navigator.of(context)
                  .pop(_TeamPartner(discordId: _pickedId, player1Id: p1));
            }
          },
          child: const Text('Form team'),
        ),
      ],
    );
  }
}
