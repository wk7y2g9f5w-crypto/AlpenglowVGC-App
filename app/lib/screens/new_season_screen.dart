import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../services/auth.dart';
import '../widgets/common.dart';

/// Admin screen for seasons: every active season (they run side by
/// side) with Edit dates / End season actions, plus the form to start
/// another one.
class NewSeasonScreen extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;

  const NewSeasonScreen(
      {super.key, required this.auth, required this.settings});

  @override
  State<NewSeasonScreen> createState() => _NewSeasonScreenState();
}

class _NewSeasonScreenState extends State<NewSeasonScreen> {
  final _nameController = TextEditingController();
  DateTime? _startDate;
  DateTime? _endDate;
  bool _saving = false;
  Future<List<Season>>? _future;

  ApiClient get _api => ApiClient(
      baseUrl: widget.settings.baseUrl, token: widget.auth.token ?? '');

  @override
  void initState() {
    super.initState();
    _future = _api.listSeasons();
  }

  @override
  void dispose() {
    _nameController.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    final f = _api.listSeasons();
    setState(() => _future = f);
    await f;
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
      '${d.year.toString().padLeft(4, '0')}-'
      '${d.month.toString().padLeft(2, '0')}-'
      '${d.day.toString().padLeft(2, '0')}';

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

  Future<void> _create() async {
    final name = _nameController.text.trim();
    if (name.isEmpty) {
      showSnack(context, 'Give the season a name.', error: true);
      return;
    }
    if (_startDate != null &&
        _endDate != null &&
        _startDate!.isAfter(_endDate!)) {
      showSnack(context, 'Start date must not be after the end date.',
          error: true);
      return;
    }
    setState(() => _saving = true);
    try {
      final season = await _api.createSeason(
        name: name,
        startDate: _startDate != null ? _iso(_startDate!) : null,
        endDate: _endDate != null ? _iso(_endDate!) : null,
      );
      if (!mounted) return;
      showSnack(context, "Season '${season['name']}' started.");
      Navigator.of(context).pop(true);
    } on ApiException catch (e) {
      if (mounted) showSnack(context, friendlyApiMessage(e), error: true);
    } catch (e) {
      if (mounted) {
        showSnack(context, 'Could not create season: $e', error: true);
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  /// Adjust a season's date window via the edit-dates dialog.
  Future<void> _editSeasonDates(Season s) async {
    final seasonId = s.id;
    DateTime? parse(String? iso) {
      if (iso == null || iso.isEmpty) return null;
      return DateTime.tryParse(iso);
    }

    final result = await showDialog<_SeasonDateEdit>(
      context: context,
      builder: (dialogContext) => _EditSeasonDatesDialog(
        initialStart: parse(s.startDate),
        initialEnd: parse(s.endDate),
      ),
    );
    if (result == null) return;
    if (result.start != null &&
        result.end != null &&
        result.start!.isAfter(result.end!)) {
      if (mounted) {
        showSnack(context, 'Start date must not be after the end date.',
            error: true);
      }
      return;
    }
    try {
      await _api.updateSeasonDates(
        seasonId,
        startDate: result.start != null ? _iso(result.start!) : null,
        endDate: result.end != null ? _iso(result.end!) : null,
      );
      if (!mounted) return;
      showSnack(context, 'Season dates updated.');
      await _refresh();
    } on ApiException catch (e) {
      if (mounted) showSnack(context, friendlyApiMessage(e), error: true);
    }
  }

  Future<void> _confirmEndSeason(Season s) async {
    final seasonId = s.id;
    final seasonName = s.name;
    final confirmController = TextEditingController();
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => _EndSeasonDialog(
        seasonName: seasonName,
        controller: confirmController,
      ),
    );
    confirmController.dispose();
    if (confirmed != true) return;
    try {
      await _api.completeSeason(seasonId);
      if (!mounted) return;
      showSnack(context, "Season '$seasonName' ended.");
      await _refresh();
    } on ApiException catch (e) {
      if (mounted) showSnack(context, friendlyApiMessage(e), error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Seasons')),
      body: AsyncBody<List<Season>>(
        future: _future!,
        onRefresh: _refresh,
        builder: (context, seasons) {
          final active = seasons.where((s) => s.isActive).toList();
          final completed = seasons.where((s) => !s.isActive).toList();
          return ListView(
            padding: const EdgeInsets.all(16),
            children: [
              if (active.isNotEmpty) ...[
                const Text('Active seasons',
                    style: TextStyle(
                        fontSize: 16, fontWeight: FontWeight.bold)),
                const SizedBox(height: 4),
                const Text(
                  'Seasons run side by side — each tournament picks the '
                  'one it counts toward.',
                  style: TextStyle(color: Colors.grey, fontSize: 13),
                ),
                const SizedBox(height: 8),
                for (final s in active)
                  Card(
                    child: ListTile(
                      leading: const Icon(Icons.emoji_events,
                          color: Colors.amber),
                      title: Text(s.name,
                          style:
                              const TextStyle(fontWeight: FontWeight.bold)),
                      subtitle: Text(_rangeLabel(s)),
                      trailing: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          TextButton(
                            onPressed: () => _editSeasonDates(s),
                            child: const Text('Edit dates'),
                          ),
                          TextButton(
                            onPressed: () => _confirmEndSeason(s),
                            child: const Text('End season'),
                          ),
                        ],
                      ),
                    ),
                  ),
                const SizedBox(height: 16),
                const Divider(),
                const SizedBox(height: 8),
              ],
              const Text('Start a new season',
                  style: TextStyle(
                      fontSize: 16, fontWeight: FontWeight.bold)),
              const SizedBox(height: 12),
              TextField(
                controller: _nameController,
                textCapitalization: TextCapitalization.words,
                decoration: const InputDecoration(
                  labelText: 'Season Name',
                  hintText: "e.g. Fall 2026",
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 12),
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton.icon(
                      icon: const Icon(Icons.calendar_today, size: 18),
                      label: Text('Start: ${_dateLabel(_startDate)}'),
                      onPressed: () => _pickDate(true),
                    ),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: OutlinedButton.icon(
                      icon: const Icon(Icons.calendar_today, size: 18),
                      label: Text('End: ${_dateLabel(_endDate)}'),
                      onPressed: () => _pickDate(false),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 20),
              FilledButton.icon(
                icon: _saving
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.add),
                label: const Text('Start Season'),
                onPressed: _saving ? null : () => _create(),
              ),
              if (completed.isNotEmpty) ...[
                const SizedBox(height: 24),
                const Divider(),
                const SizedBox(height: 8),
                const Text('Completed seasons',
                    style: TextStyle(
                        fontSize: 16, fontWeight: FontWeight.bold)),
                const SizedBox(height: 8),
                for (final s in completed)
                  ListTile(
                    leading: const Icon(Icons.flag_outlined,
                        color: Colors.grey),
                    title: Text(s.name),
                    subtitle: Text(
                        '${s.tournamentCount} tournament'
                        '${s.tournamentCount == 1 ? '' : 's'} • Completed'),
                    dense: true,
                  ),
              ],
            ],
          );
        },
      ),
    );
  }

  String _rangeLabel(Season s) {
    String? fmt(String? iso) {
      if (iso == null || iso.isEmpty) return null;
      final d = DateTime.tryParse(iso);
      if (d == null) return iso;
      return _dateLabel(d);
    }

    final start = fmt(s.startDate);
    final end = fmt(s.endDate);
    final count =
        '${s.tournamentCount} tournament${s.tournamentCount == 1 ? '' : 's'}';
    if (start == null && end == null) return 'Active • $count';
    if (start != null && end != null) return 'Active • $start – $end • $count';
    return 'Active • ${start ?? end} • $count';
  }
}

/// Two-step "End season" confirmation: the user must type "delete"
/// (case-insensitive, trimmed) before the End button enables. Prevents
/// accidental season endings.
class _EndSeasonDialog extends StatefulWidget {
  final String seasonName;
  final TextEditingController controller;

  const _EndSeasonDialog(
      {required this.seasonName, required this.controller});

  @override
  State<_EndSeasonDialog> createState() => _EndSeasonDialogState();
}

class _EndSeasonDialogState extends State<_EndSeasonDialog> {
  bool get _matches =>
      widget.controller.text.trim().toLowerCase() == 'delete';

  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_onChanged);
  }

  @override
  void dispose() {
    widget.controller.removeListener(_onChanged);
    super.dispose();
  }

  void _onChanged() => setState(() {});

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('End season?'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            "This closes '${widget.seasonName}'. Its standings stay "
            'final and viewable, and tournaments already linked keep '
            'their points. This cannot be undone.',
          ),
          const SizedBox(height: 16),
          const Text(
            'Type "delete" to confirm:',
            style: TextStyle(fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 8),
          TextField(
            controller: widget.controller,
            autocorrect: false,
            decoration: const InputDecoration(
              hintText: 'delete',
              border: OutlineInputBorder(),
            ),
          ),
        ],
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(false),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed:
              _matches ? () => Navigator.of(context).pop(true) : null,
          style: FilledButton.styleFrom(
              backgroundColor: Colors.red.shade700),
          child: const Text('End'),
        ),
      ],
    );
  }
}

/// Result of the edit-dates dialog: the chosen window (null clears that end).
class _SeasonDateEdit {
  final DateTime? start;
  final DateTime? end;

  const _SeasonDateEdit(this.start, this.end);
}

/// Dialog for adjusting the active season's date window. Either date can
/// be cleared back to "not set".
class _EditSeasonDatesDialog extends StatefulWidget {
  final DateTime? initialStart;
  final DateTime? initialEnd;

  const _EditSeasonDatesDialog({this.initialStart, this.initialEnd});

  @override
  State<_EditSeasonDatesDialog> createState() => _EditSeasonDatesDialogState();
}

class _EditSeasonDatesDialogState extends State<_EditSeasonDatesDialog> {
  DateTime? _start;
  DateTime? _end;

  @override
  void initState() {
    super.initState();
    _start = widget.initialStart;
    _end = widget.initialEnd;
  }

  String _label(DateTime? d) {
    if (d == null) return 'Not set';
    const months = [
      'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'
    ];
    return '${months[d.month - 1]} ${d.day}, ${d.year}';
  }

  Future<void> _pick(bool isStart) async {
    final now = DateTime.now();
    final picked = await showDatePicker(
      context: context,
      initialDate: (isStart ? _start : _end) ?? now,
      firstDate: DateTime(now.year - 1),
      lastDate: DateTime(now.year + 2),
    );
    if (picked != null) {
      setState(() {
        if (isStart) {
          _start = picked;
        } else {
          _end = picked;
        }
      });
    }
  }

  Widget _dateRow(String label, DateTime? value, bool isStart) {
    return Row(
      children: [
        Expanded(child: Text(label)),
        TextButton.icon(
          icon: const Icon(Icons.calendar_today, size: 18),
          label: Text(_label(value)),
          onPressed: () => _pick(isStart),
        ),
        if (value != null)
          IconButton(
            tooltip: 'Clear',
            icon: const Icon(Icons.clear, size: 18),
            onPressed: () => setState(() {
              if (isStart) {
                _start = null;
              } else {
                _end = null;
              }
            }),
          ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Edit season dates'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          _dateRow('Start', _start, true),
          _dateRow('End', _end, false),
        ],
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: () => Navigator.of(context)
              .pop(_SeasonDateEdit(_start, _end)),
          child: const Text('Save'),
        ),
      ],
    );
  }
}
