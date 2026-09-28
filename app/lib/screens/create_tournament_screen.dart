import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../services/auth.dart';
import '../widgets/common.dart';

/// Crew-only screen for creating a tournament from the app.
///
/// Mirrors `/tournament create`: course picker with official Golf+ pars,
/// format, holes, date range, tee/pin/wind/green settings, description.
/// Pars auto-fill from the course database server-side for known courses.
class CreateTournamentScreen extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;

  const CreateTournamentScreen(
      {super.key, required this.auth, required this.settings});

  @override
  State<CreateTournamentScreen> createState() => _CreateTournamentScreenState();
}

class _RoundSettings {
  String tee;
  String pin;
  String wind;
  _RoundSettings({this.tee = 'middle', this.pin = 'white', this.wind = 'moderate'});
}

class _CreateTournamentScreenState extends State<CreateTournamentScreen> {
  final _formKey = GlobalKey<FormState>();
  final _nameCtrl = TextEditingController();
  final _descCtrl = TextEditingController();

  List<GolfCourse> _courses = [];
  bool _loadingCourses = true;
  bool _submitting = false;

  String _format = 'stroke';
  int _holes = 18;
  GolfCourse? _course;
  DateTime? _startDate;
  DateTime? _endDate;
  String _teePosition = 'middle';
  String _pinPosition = 'white';
  String _windStrength = 'moderate';
  String _greenSpeed = 'pro';
  int _numRounds = 1;
  List<_RoundSettings> _roundSettings = [_RoundSettings()];

  ApiClient get _api => ApiClient(
      baseUrl: widget.settings.baseUrl, token: widget.auth.token ?? '');

  static const _formats = {
    'stroke': 'Stroke play',
    'match': 'Match play',
    'best_ball': 'Best ball',
    'alt_shot': 'Alternate shot',
    'scramble': 'Scramble',
  };
  static const _tees = {'front': 'Front', 'middle': 'Middle', 'back': 'Back'};
  static const _pins = {'black': 'Black', 'white': 'White', 'red': 'Red'};
  static const _winds = {'low': 'Low', 'moderate': 'Moderate', 'severe': 'Severe'};
  static const _greens = {'veryfast': 'Very Fast', 'pro': 'Pro'};

  @override
  void initState() {
    super.initState();
    _loadCourses();
  }

  @override
  void dispose() {
    _nameCtrl.dispose();
    _descCtrl.dispose();
    super.dispose();
  }

  Future<void> _loadCourses() async {
    try {
      final courses = await _api.getCourses();
      if (mounted) {
        setState(() {
          _courses = courses;
          _loadingCourses = false;
        });
      }
    } on ApiException catch (e) {
      if (mounted) {
        setState(() => _loadingCourses = false);
        showSnack(context, friendlyApiMessage(e), error: true);
      }
    } catch (e) {
      if (mounted) {
        setState(() => _loadingCourses = false);
        showSnack(context, 'Could not load courses: $e', error: true);
      }
    }
  }

  Future<void> _pickDate(bool isStart) async {
    final now = DateTime.now();
    final picked = await showDatePicker(
      context: context,
      initialDate: (isStart ? _startDate : _endDate) ?? now,
      firstDate: DateTime(now.year - 1),
      lastDate: DateTime(now.year + 2),
    );
    if (picked != null) {
      setState(() {
        if (isStart) {
          _startDate = picked;
        } else {
          _endDate = picked;
        }
      });
    }
  }

  String _dateLabel(DateTime? d) {
    if (d == null) return 'Select date';
    const months = [
      'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'
    ];
    return '${months[d.month - 1]} ${d.day}, ${d.year}';
  }

  String _iso(DateTime d) =>
      '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

  void _setNumRounds(int n) {
    setState(() {
      if (n > _numRounds) {
        // New rounds start from the base settings — tune each below.
        for (var i = _numRounds; i < n; i++) {
          _roundSettings.add(_RoundSettings(
              tee: _teePosition, pin: _pinPosition, wind: _windStrength));
        }
      } else {
        _roundSettings = _roundSettings.sublist(0, n);
      }
      _numRounds = n;
      if (n > 1) _holes = 18; // multi-round is 18 holes per round
    });
  }

  void _setFormat(String v) {
    setState(() {
      _format = v;
      if (v == 'match' && _numRounds > 1) _setNumRounds(1);
    });
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    if (_course == null) {
      showSnack(context, 'Pick a course.', error: true);
      return;
    }
    if (_startDate == null || _endDate == null) {
      showSnack(context, 'Pick a start and end date.', error: true);
      return;
    }
    if (_endDate!.isBefore(_startDate!)) {
      showSnack(context, 'The end date can\'t be before the start date.',
          error: true);
      return;
    }
    setState(() => _submitting = true);
    try {
      final rounds = _numRounds > 1
          ? List.generate(
              _numRounds,
              (i) => {
                'tee_position': _roundSettings[i].tee,
                'pin_position': _roundSettings[i].pin,
                'wind_strength': _roundSettings[i].wind,
              })
          : [
              {
                'tee_position': _teePosition,
                'pin_position': _pinPosition,
                'wind_strength': _windStrength,
              }
            ];
      await _api.createTournament(
        name: _nameCtrl.text.trim(),
        format: _format,
        holes: _holes,
        course: _course!.name,
        startDate: _iso(_startDate!),
        endDate: _iso(_endDate!),
        teePosition: _teePosition,
        pinPosition: _pinPosition,
        windStrength: _windStrength,
        greenSpeed: _greenSpeed,
        description:
            _descCtrl.text.trim().isEmpty ? null : _descCtrl.text.trim(),
        rounds: rounds,
      );
      if (mounted) {
        showSnack(context, 'Tournament created.');
        Navigator.of(context).pop(true);
      }
    } on ApiException catch (e) {
      if (mounted) showSnack(context, friendlyApiMessage(e), error: true);
    } catch (e) {
      if (mounted) showSnack(context, 'Create failed: $e', error: true);
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  /// Per-round tee/pin/wind card shown when the tournament has 2-5 rounds.
  Widget _roundCard(int i) {
    final rs = _roundSettings[i];
    DropdownButtonFormField<String> field(
        String label, String value, Map<String, String> options,
        void Function(String) onChanged) {
      return DropdownButtonFormField<String>(
        initialValue: value,
        decoration: InputDecoration(
          labelText: label,
          border: const OutlineInputBorder(),
          isDense: true,
        ),
        items: options.entries
            .map((e) =>
                DropdownMenuItem(value: e.key, child: Text(e.value)))
            .toList(),
        onChanged: (v) {
          if (v != null) setState(() => onChanged(v));
        },
      );
    }

    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Round ${i + 1}',
                style:
                    const TextStyle(fontWeight: FontWeight.bold)),
            const SizedBox(height: 8),
            Row(
              children: [
                Expanded(
                    child: field('Tees', rs.tee, _tees,
                        (v) => rs.tee = v)),
                const SizedBox(width: 8),
                Expanded(
                    child: field('Pins', rs.pin, _pins,
                        (v) => rs.pin = v)),
                const SizedBox(width: 8),
                Expanded(
                    child: field('Wind', rs.wind, _winds,
                        (v) => rs.wind = v)),
              ],
            ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {    return Scaffold(
      appBar: AppBar(title: const Text('New tournament')),
      body: _loadingCourses
          ? const Center(child: CircularProgressIndicator())
          : Form(
              key: _formKey,
              child: ListView(
                padding: const EdgeInsets.all(16),
                children: [
                  TextFormField(
                    controller: _nameCtrl,
                    decoration: const InputDecoration(
                      labelText: 'Tournament name',
                      border: OutlineInputBorder(),
                    ),
                    validator: (v) => (v == null || v.trim().isEmpty)
                        ? 'Give it a name'
                        : null,
                  ),
                  const SizedBox(height: 12),
                  DropdownButtonFormField<String>(
                    initialValue: _format,
                    decoration: const InputDecoration(
                      labelText: 'Format',
                      border: OutlineInputBorder(),
                    ),
                    items: _formats.entries
                        .map((e) => DropdownMenuItem(
                            value: e.key, child: Text(e.value)))
                        .toList(),
                    onChanged: (v) =>
                        _setFormat(v ?? 'stroke'),
                  ),
                  const SizedBox(height: 12),
                  Row(
                    children: [
                      Expanded(
                        child: DropdownButtonFormField<int>(
                          initialValue: _holes,
                          decoration: InputDecoration(
                            labelText: 'Holes',
                            border: const OutlineInputBorder(),
                            helperText: _numRounds > 1
                                ? 'Multi-round is 18 holes'
                                : null,
                          ),
                          items: const [9, 18]
                              .map((h) => DropdownMenuItem(
                                  value: h, child: Text('$h holes')))
                              .toList(),
                          onChanged: _numRounds > 1
                              ? null
                              : (v) =>
                                  setState(() => _holes = v ?? 18),
                        ),
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        flex: 2,
                        child: DropdownButtonFormField<GolfCourse>(
                          initialValue: _course,
                          decoration: const InputDecoration(
                            labelText: 'Course',
                            border: OutlineInputBorder(),
                          ),
                          isExpanded: true,
                          items: _courses
                              .map((c) => DropdownMenuItem(
                                    value: c,
                                    child: Text(c.name,
                                        overflow: TextOverflow.ellipsis),
                                  ))
                              .toList(),
                          onChanged: (v) => setState(() => _course = v),
                        ),
                      ),
                    ],
                  ),
                  if (_course != null) ...[
                    const SizedBox(height: 4),
                    Text('Par ${_course!.parTotal} · official Golf+ pars',
                        style: const TextStyle(
                            color: Colors.grey, fontSize: 12)),
                  ],
                  const SizedBox(height: 12),
                  Row(
                    children: [
                      Expanded(
                        child: OutlinedButton.icon(
                          onPressed: () => _pickDate(true),
                          icon: const Icon(Icons.calendar_today, size: 16),
                          label: Text(_dateLabel(_startDate)),
                        ),
                      ),
                      const Padding(
                        padding: EdgeInsets.symmetric(horizontal: 8),
                        child: Text('→'),
                      ),
                      Expanded(
                        child: OutlinedButton.icon(
                          onPressed: () => _pickDate(false),
                          icon: const Icon(Icons.calendar_today, size: 16),
                          label: Text(_dateLabel(_endDate)),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 16),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      const Text('Rounds',
                          style: TextStyle(
                              fontSize: 16, fontWeight: FontWeight.bold)),
                      Row(
                        children: [
                          IconButton(
                            icon: const Icon(Icons.remove),
                            onPressed: _numRounds > 1
                                ? () => _setNumRounds(_numRounds - 1)
                                : null,
                          ),
                          Text('$_numRounds',
                              style: const TextStyle(
                                  fontSize: 18,
                                  fontWeight: FontWeight.bold)),
                          IconButton(
                            icon: const Icon(Icons.add),
                            onPressed: _numRounds < 5 && _format != 'match'
                                ? () => _setNumRounds(_numRounds + 1)
                                : null,
                          ),
                        ],
                      ),
                    ],
                  ),
                  if (_format == 'match')
                    const Text(
                        'Match play is single-round.',
                        style: TextStyle(color: Colors.grey, fontSize: 12)),
                  if (_numRounds > 1) ...[
                    const Text(
                      'Same course and pars every round — only tee, pin and '
                      'wind change. Green speed applies to all rounds.',
                      style: TextStyle(color: Colors.grey, fontSize: 12),
                    ),
                    const SizedBox(height: 8),
                    for (var i = 0; i < _numRounds; i++) _roundCard(i),
                  ],
                  const SizedBox(height: 16),
                  Text(
                      _numRounds > 1
                          ? 'Green speed (all rounds)'
                          : 'Round settings',
                      style: const TextStyle(
                          fontSize: 16, fontWeight: FontWeight.bold)),
                  const SizedBox(height: 8),
                  if (_numRounds == 1) ...[
                    Row(
                      children: [
                        Expanded(
                          child: DropdownButtonFormField<String>(
                            initialValue: _teePosition,
                            decoration: const InputDecoration(
                              labelText: 'Tees',
                              border: OutlineInputBorder(),
                              isDense: true,
                            ),
                            items: _tees.entries
                                .map((e) => DropdownMenuItem(
                                    value: e.key, child: Text(e.value)))
                                .toList(),
                            onChanged: (v) =>
                                setState(() => _teePosition = v ?? 'middle'),
                          ),
                        ),
                        const SizedBox(width: 8),
                        Expanded(
                          child: DropdownButtonFormField<String>(
                            initialValue: _pinPosition,
                            decoration: const InputDecoration(
                              labelText: 'Pins',
                              border: OutlineInputBorder(),
                              isDense: true,
                            ),
                            items: _pins.entries
                                .map((e) => DropdownMenuItem(
                                    value: e.key, child: Text(e.value)))
                                .toList(),
                            onChanged: (v) =>
                                setState(() => _pinPosition = v ?? 'white'),
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 12),
                    Row(
                      children: [
                        Expanded(
                          child: DropdownButtonFormField<String>(
                            initialValue: _windStrength,
                            decoration: const InputDecoration(
                              labelText: 'Wind',
                              border: OutlineInputBorder(),
                              isDense: true,
                            ),
                            items: _winds.entries
                                .map((e) => DropdownMenuItem(
                                    value: e.key, child: Text(e.value)))
                                .toList(),
                            onChanged: (v) => setState(
                                () => _windStrength = v ?? 'moderate'),
                          ),
                        ),
                        const SizedBox(width: 8),
                        Expanded(
                          child: DropdownButtonFormField<String>(
                            initialValue: _greenSpeed,
                            decoration: const InputDecoration(
                              labelText: 'Greens',
                              border: OutlineInputBorder(),
                              isDense: true,
                            ),
                            items: _greens.entries
                                .map((e) => DropdownMenuItem(
                                    value: e.key, child: Text(e.value)))
                                .toList(),
                            onChanged: (v) =>
                                setState(() => _greenSpeed = v ?? 'pro'),
                          ),
                        ),
                      ],
                    ),
                  ] else ...[
                    DropdownButtonFormField<String>(
                      initialValue: _greenSpeed,
                      decoration: const InputDecoration(
                        labelText: 'Greens',
                        border: OutlineInputBorder(),
                        isDense: true,
                      ),
                      items: _greens.entries
                          .map((e) => DropdownMenuItem(
                              value: e.key, child: Text(e.value)))
                          .toList(),
                      onChanged: (v) =>
                          setState(() => _greenSpeed = v ?? 'pro'),
                    ),
                  ],
                  const SizedBox(height: 12),
                  TextFormField(
                    controller: _descCtrl,
                    maxLines: 3,
                    decoration: const InputDecoration(
                      labelText: 'Description (optional)',
                      border: OutlineInputBorder(),
                    ),
                  ),
                  const SizedBox(height: 20),
                  ElevatedButton.icon(
                    onPressed: _submitting ? null : _submit,
                    icon: _submitting
                        ? const SizedBox(
                            width: 18,
                            height: 18,
                            child: CircularProgressIndicator(strokeWidth: 2))
                        : const Icon(Icons.add),
                    label: const Text('Create tournament'),
                    style: ElevatedButton.styleFrom(
                      padding: const EdgeInsets.symmetric(vertical: 14),
                    ),
                  ),
                  const SizedBox(height: 8),
                  const Text(
                    'The bot picks it up within a minute: Register button '
                    'attached and the Tee Sheet board refreshed.',
                    style: TextStyle(color: Colors.grey, fontSize: 12),
                    textAlign: TextAlign.center,
                  ),
                ],
              ),
            ),
    );
  }
}
