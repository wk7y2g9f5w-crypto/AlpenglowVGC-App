import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../services/auth.dart';
import '../widgets/common.dart';

/// Create or edit a match-play tee time. Single (1v1) or best-ball (equal
/// teams of 2-4 per side); the creator takes side 1's first spot.
class MatchPlayFormScreen extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;
  final MatchPlayTeeTime? existing;

  const MatchPlayFormScreen(
      {super.key,
      required this.auth,
      required this.settings,
      this.existing});

  @override
  State<MatchPlayFormScreen> createState() => _MatchPlayFormScreenState();
}

class _MatchPlayFormScreenState extends State<MatchPlayFormScreen> {
  static const _tees = {'front': 'Front', 'middle': 'Middle', 'back': 'Back'};
  static const _pins = {'black': 'Black', 'white': 'White', 'red': 'Red'};
  static const _winds = {
    'low': 'Low',
    'moderate': 'Moderate',
    'severe': 'Severe'
  };
  static const _greens = {'veryfast': 'Very Fast', 'pro': 'Pro'};

  late final TextEditingController _label;
  late final TextEditingController _notes;
  late final TextEditingController _side1Name;
  late final TextEditingController _side2Name;
  List<GolfCourse> _courses = [];
  GolfCourse? _course;
  String _tee = 'back';
  String _pin = 'black';
  String _wind = 'moderate';
  String _greenSpeed = 'pro';
  DateTime? _when;
  String _format = 'single';
  int _teamSize = 2;
  bool _saving = false;
  bool _loading = true;

  ApiClient get _api => ApiClient(
      baseUrl: widget.settings.baseUrl, token: widget.auth.token ?? '');

  @override
  void initState() {
    super.initState();
    final e = widget.existing;
    _label = TextEditingController(text: e?.label ?? '');
    _notes = TextEditingController(text: e?.notes ?? '');
    _side1Name = TextEditingController(
        text: e != null && e.sides.isNotEmpty ? e.sides[0].teamName : '');
    _side2Name = TextEditingController(
        text: e != null && e.sides.length > 1 ? e.sides[1].teamName : '');
    _tee = e?.teePosition ?? 'back';
    _pin = e?.pinPosition ?? 'black';
    _wind = e?.windStrength ?? 'moderate';
    _greenSpeed = e?.greenSpeed ?? 'pro';
    _format = e?.format ?? 'single';
    _teamSize = e?.teamSize ?? 2;
    if (e != null && e.startsAt.isNotEmpty) {
      try {
        _when = DateTime.parse(e.startsAt).toLocal();
      } catch (_) {}
    }
    _loadCourses();
  }

  Future<void> _loadCourses() async {
    try {
      final courses = await _api.getCourses();
      final e = widget.existing;
      setState(() {
        _courses = courses;
        if (e != null) {
          for (final c in courses) {
            if (c.name == e.course) _course = c;
          }
        }
        _loading = false;
      });
    } catch (err) {
      setState(() => _loading = false);
      if (mounted) {
        showSnack(context, 'Could not load courses: $err', error: true);
      }
    }
  }

  @override
  void dispose() {
    _label.dispose();
    _notes.dispose();
    _side1Name.dispose();
    _side2Name.dispose();
    super.dispose();
  }

  Future<void> _pickWhen() async {
    final now = DateTime.now();
    final date = await showDatePicker(
      context: context,
      initialDate: _when ?? now.add(const Duration(days: 1)),
      firstDate: now.subtract(const Duration(days: 1)),
      lastDate: now.add(const Duration(days: 365)),
    );
    if (date == null || !mounted) return;
    final time = await showTimePicker(
      context: context,
      initialTime: TimeOfDay.fromDateTime(_when ?? now),
    );
    if (time == null) return;
    setState(() {
      _when = DateTime(date.year, date.month, date.day, time.hour, time.minute);
    });
  }

  Future<void> _save() async {
    if (_label.text.trim().isEmpty) {
      showSnack(context, 'Give this match a name.', error: true);
      return;
    }
    if (_course == null) {
      showSnack(context, 'Pick a course.', error: true);
      return;
    }
    if (_when == null) {
      showSnack(context, 'Pick a start date & time.', error: true);
      return;
    }
    setState(() => _saving = true);
    try {
      final payload = <String, dynamic>{
        'label': _label.text.trim(),
        'course': _course!.name,
        'tee_position': _tee,
        'pin_position': _pin,
        'wind_strength': _wind,
        'green_speed': _greenSpeed,
        'starts_at': _when?.toUtc().toIso8601String() ?? '',
        'notes': _notes.text.trim(),
      };
      if (widget.existing == null) {
        payload['format'] = _format;
        payload['team_size'] = _format == 'bestball' ? _teamSize : 1;
        payload['side1_team_name'] = _side1Name.text.trim();
        payload['side2_team_name'] = _side2Name.text.trim();
        await _api.createMatchPlayTeeTime(payload);
      } else {
        payload['side1_team_name'] = _side1Name.text.trim();
        payload['side2_team_name'] = _side2Name.text.trim();
        await _api.updateMatchPlayTeeTime(widget.existing!.id, payload);
      }
      if (mounted) Navigator.of(context).pop(true);
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message, error: true);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Widget _drop(String label, String value, Map<String, String> options,
      void Function(String) onChanged) {
    return DropdownButtonFormField<String>(
      initialValue: value,
      decoration: InputDecoration(
        labelText: label,
        border: const OutlineInputBorder(),
        isDense: true,
      ),
      items: options.entries
          .map((e) => DropdownMenuItem(value: e.key, child: Text(e.value)))
          .toList(),
      onChanged: (v) {
        if (v != null) setState(() => onChanged(v));
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final isNew = widget.existing == null;
    return Scaffold(
      appBar: AppBar(
          title: Text(isNew ? 'New Match' : 'Edit Match')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                TextField(
                  controller: _label,
                  decoration: const InputDecoration(
                    labelText: 'Match name',
                    hintText: 'e.g. Saturday showdown',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                if (isNew) ...[
                  DropdownButtonFormField<String>(
                    initialValue: _format,
                    decoration: const InputDecoration(
                      labelText: 'Format',
                      border: OutlineInputBorder(),
                      isDense: true,
                    ),
                    items: const [
                      DropdownMenuItem(
                          value: 'single',
                          child: Text('1v1 Matchplay')),
                      DropdownMenuItem(
                          value: 'bestball',
                          child: Text('Best Ball')),
                    ],
                    onChanged: (v) {
                      if (v != null) setState(() => _format = v);
                    },
                  ),
                  if (_format == 'bestball') ...[
                    const SizedBox(height: 8),
                    Row(
                      children: [
                        const Text('Players per side'),
                        const Spacer(),
                        IconButton(
                          icon: const Icon(Icons.remove),
                          onPressed: _teamSize > 2
                              ? () => setState(() => _teamSize--)
                              : null,
                        ),
                        Text('$_teamSize',
                            style: Theme.of(context).textTheme.titleMedium),
                        IconButton(
                          icon: const Icon(Icons.add),
                          onPressed: _teamSize < 4
                              ? () => setState(() => _teamSize++)
                              : null,
                        ),
                      ],
                    ),
                    const Text(
                      'Both sides have the same size (2-4 players).',
                      style: TextStyle(fontSize: 12, color: Colors.grey),
                    ),
                    const SizedBox(height: 12),
                  ] else
                    const SizedBox(height: 12),
                ],
                DropdownButtonFormField<GolfCourse>(
                  initialValue: _course,
                  decoration: const InputDecoration(
                    labelText: 'Course',
                    border: OutlineInputBorder(),
                    isDense: true,
                  ),
                  items: _courses
                      .map((c) =>
                          DropdownMenuItem(value: c, child: Text(c.name)))
                      .toList(),
                  onChanged: (v) => setState(() => _course = v),
                ),
                const SizedBox(height: 12),
                Row(
                  children: [
                    Expanded(
                        child:
                            _drop('Tees', _tee, _tees, (v) => _tee = v)),
                    const SizedBox(width: 8),
                    Expanded(
                        child:
                            _drop('Pins', _pin, _pins, (v) => _pin = v)),
                  ],
                ),
                const SizedBox(height: 12),
                Row(
                  children: [
                    Expanded(
                        child:
                            _drop('Wind', _wind, _winds, (v) => _wind = v)),
                    const SizedBox(width: 8),
                    Expanded(
                        child: _drop('Greens', _greenSpeed, _greens,
                            (v) => _greenSpeed = v)),
                  ],
                ),
                const SizedBox(height: 12),
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: const Icon(Icons.schedule),
                  title: Text(_when == null
                      ? 'Start time (required)'
                      : DateFormat('EEE, MMM d · h:mm a').format(_when!)),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: _pickWhen,
                ),
                const SizedBox(height: 4),
                TextField(
                  controller: _side1Name,
                  decoration: const InputDecoration(
                    labelText: 'Side 1 team name (optional)',
                    hintText: 'Shown instead of player names',
                    helperText: 'Leave blank and Golf+ usernames will show'
                        ' on the leaderboard.',
                    border: OutlineInputBorder(),
                    isDense: true,
                  ),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _side2Name,
                  decoration: const InputDecoration(
                    labelText: 'Side 2 team name (optional)',
                    hintText: 'Shown instead of player names',
                    helperText: 'Leave blank and Golf+ usernames will show'
                        ' on the leaderboard.',
                    border: OutlineInputBorder(),
                    isDense: true,
                  ),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _notes,
                  decoration: const InputDecoration(
                    labelText: 'Notes (optional)',
                    border: OutlineInputBorder(),
                  ),
                  maxLines: 2,
                ),
                if (isNew) ...[
                  const SizedBox(height: 8),
                  const Text(
                    'You take side 1\'s first spot automatically.',
                    style: TextStyle(fontSize: 12, color: Colors.grey),
                  ),
                ],
                const SizedBox(height: 20),
                ElevatedButton(
                  onPressed: _saving ? null : _save,
                  child: _saving
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(strokeWidth: 2))
                      : Text(isNew ? 'Create' : 'Save'),
                ),
              ],
            ),
    );
  }
}
