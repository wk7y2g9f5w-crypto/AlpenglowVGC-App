import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../services/auth.dart';
import '../widgets/common.dart';

/// Create or edit an alt-shot tee time. Same round-setup concepts as a
/// tournament round (course, tees, pins, wind, green speed), plus a start
/// time, team cap (1-2), and notes.
class AltShotFormScreen extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;
  final AltShotTeeTime? existing;

  const AltShotFormScreen(
      {super.key,
      required this.auth,
      required this.settings,
      this.existing});

  @override
  State<AltShotFormScreen> createState() => _AltShotFormScreenState();
}

class _AltShotFormScreenState extends State<AltShotFormScreen> {
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
  List<GolfCourse> _courses = [];
  GolfCourse? _course;
  String _tee = 'middle';
  String _pin = 'white';
  String _wind = 'moderate';
  String _greenSpeed = 'pro';
  DateTime? _when;
  int _maxTeams = 2;
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
    _tee = e?.teePosition ?? 'middle';
    _pin = e?.pinPosition ?? 'white';
    _wind = e?.windStrength ?? 'moderate';
    _greenSpeed = e?.greenSpeed ?? 'pro';
    _maxTeams = e?.maxTeams ?? 2;
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
      _when = DateTime(
          date.year, date.month, date.day, time.hour, time.minute);
    });
  }

  Future<void> _save() async {
    if (_label.text.trim().isEmpty) {
      showSnack(context, 'Give this round a name.', error: true);
      return;
    }
    if (_course == null) {
      showSnack(context, 'Pick a course.', error: true);
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
        'max_teams': _maxTeams,
        'notes': _notes.text.trim(),
      };
      if (widget.existing == null) {
        await _api.createAltShotTeeTime(payload);
      } else {
        await _api.updateAltShotTeeTime(widget.existing!.id, payload);
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
    return Scaffold(
      appBar: AppBar(
          title: Text(widget.existing == null
              ? 'New Alt-Shot Round'
              : 'Edit Alt-Shot Round')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                TextField(
                  controller: _label,
                  decoration: const InputDecoration(
                    labelText: 'Round name',
                    hintText: 'e.g. Saturday alt-shot',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                DropdownButtonFormField<GolfCourse>(
                  initialValue: _course,
                  decoration: const InputDecoration(
                    labelText: 'Course',
                    border: OutlineInputBorder(),
                    isDense: true,
                  ),
                  items: _courses
                      .map((c) => DropdownMenuItem(
                          value: c, child: Text(c.name)))
                      .toList(),
                  onChanged: (v) => setState(() => _course = v),
                ),
                const SizedBox(height: 12),
                Row(
                  children: [
                    Expanded(
                        child: _drop('Tees', _tee, _tees,
                            (v) => _tee = v)),
                    const SizedBox(width: 8),
                    Expanded(
                        child: _drop('Pins', _pin, _pins,
                            (v) => _pin = v)),
                  ],
                ),
                const SizedBox(height: 12),
                Row(
                  children: [
                    Expanded(
                        child: _drop('Wind', _wind, _winds,
                            (v) => _wind = v)),
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
                      ? 'Start time (optional)'
                      : DateFormat('EEE, MMM d · h:mm a').format(_when!)),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: _pickWhen,
                ),
                const SizedBox(height: 4),
                Row(
                  children: [
                    const Text('Teams'),
                    const Spacer(),
                    IconButton(
                      icon: const Icon(Icons.remove),
                      onPressed: _maxTeams > 1
                          ? () => setState(() => _maxTeams--)
                          : null,
                    ),
                    Text('$_maxTeams',
                        style: Theme.of(context).textTheme.titleMedium),
                    IconButton(
                      icon: const Icon(Icons.add),
                      onPressed: _maxTeams < 2
                          ? () => setState(() => _maxTeams++)
                          : null,
                    ),
                  ],
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
                const SizedBox(height: 20),
                ElevatedButton(
                  onPressed: _saving ? null : _save,
                  child: _saving
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child:
                              CircularProgressIndicator(strokeWidth: 2))
                      : Text(widget.existing == null ? 'Create' : 'Save'),
                ),
              ],
            ),
    );
  }
}
