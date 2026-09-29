import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../services/auth.dart';
import '../widgets/common.dart';

/// Admin screen for starting a new season: shows the active season (if
/// any) with an "End season" action, plus a Season Name + start/end
/// date form.
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
  Future<SeasonStandings>? _future;

  ApiClient get _api => ApiClient(
      baseUrl: widget.settings.baseUrl, token: widget.auth.token ?? '');

  @override
  void initState() {
    super.initState();
    _future = _api.seasonStandings();
  }

  @override
  void dispose() {
    _nameController.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    final f = _api.seasonStandings();
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
      if (!mounted) return;
      if (e.statusCode == 409) {
        showSnack(
            context,
            'A season is already active — end it before starting a new one.',
            error: true);
        await _refresh();
      } else {
        showSnack(context, friendlyApiMessage(e), error: true);
      }
    } catch (e) {
      if (mounted) {
        showSnack(context, 'Could not create season: $e', error: true);
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _confirmEndSeason(SeasonStandings s) async {
    final seasonId = s.seasonId;
    if (seasonId == null) return;
    final seasonName = s.seasonName;
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
      appBar: AppBar(title: const Text('New Season')),
      body: AsyncBody<SeasonStandings>(
        future: _future!,
        onRefresh: _refresh,
        builder: (context, s) {
          final hasActive = s.seasonName.isNotEmpty;
          return ListView(
            padding: const EdgeInsets.all(16),
            children: [
              if (hasActive) ...[
                Card(
                  child: ListTile(
                    leading: const Icon(Icons.emoji_events,
                        color: Colors.amber),
                    title: Text(s.seasonName,
                        style: const TextStyle(fontWeight: FontWeight.bold)),
                    subtitle: Text(_activeRangeLabel(s)),
                    trailing: TextButton(
                      onPressed: () => _confirmEndSeason(s),
                      child: const Text('End season'),
                    ),
                  ),
                ),
                const SizedBox(height: 8),
                const Text(
                  'End the current season before starting a new one.',
                  style: TextStyle(color: Colors.grey),
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
                onPressed:
                    (_saving || hasActive) ? null : () => _create(),
              ),
              if (hasActive)
                const Padding(
                  padding: EdgeInsets.only(top: 8),
                  child: Text(
                    'Disabled while a season is active.',
                    textAlign: TextAlign.center,
                    style: TextStyle(color: Colors.grey),
                  ),
                ),
            ],
          );
        },
      ),
    );
  }

  String _activeRangeLabel(SeasonStandings s) {
    String? fmt(String? iso) {
      if (iso == null || iso.isEmpty) return null;
      final d = DateTime.tryParse(iso);
      if (d == null) return iso;
      return _dateLabel(d);
    }

    final start = fmt(s.seasonStartDate);
    final end = fmt(s.seasonEndDate);
    if (start == null && end == null) return 'Active season';
    if (start != null && end != null) return 'Active • $start – $end';
    return 'Active • ${start ?? end}';
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
            "This closes '${widget.seasonName}'. New tournaments will no "
            'longer earn points toward it, and you can then start a new '
            'season. This cannot be undone.',
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
