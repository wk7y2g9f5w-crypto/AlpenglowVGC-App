import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../services/auth.dart';
import '../widgets/common.dart';
import 'score_entry_screen.dart';

/// Tee time detail: player list, join/leave/request, pending requests for
/// the creator, and the "Enter scores" button.
class TeeTimeDetailScreen extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;
  final Tournament tournament;
  final String teeTimeId;
  final String myDiscordId;

  const TeeTimeDetailScreen({
    super.key,
    required this.auth,
    required this.settings,
    required this.tournament,
    required this.teeTimeId,
    required this.myDiscordId,
  });

  @override
  State<TeeTimeDetailScreen> createState() => _TeeTimeDetailScreenState();
}

class _TeeTimeDetailScreenState extends State<TeeTimeDetailScreen> {
  late Future<_DetailData> _future;

  ApiClient get _api => ApiClient(
      baseUrl: widget.settings.baseUrl, token: widget.auth.token ?? '');

  @override
  void initState() {
    super.initState();
    _future = _load();
  }

  Future<_DetailData> _load() async {
    final teeTimes = await _api.getTeeTimes(widget.tournament.id);
    final tt = teeTimes.firstWhere(
      (t) => t.id == widget.teeTimeId,
      orElse: () => throw Exception('Tee time not found.'),
    );
    List<TeeTimeRequest> requests = [];
    if (tt.createdBy == widget.myDiscordId) {
      try {
        requests = await _api.getTeeTimeRequests(tt.id);
      } on ApiException {
        // Non-creators get 403; ignore.
      }
    }
    bool isCrew = false;
    try {
      isCrew = (await _api.getMe()).isCrew;
    } catch (_) {
      // Offline — crew actions just stay hidden.
    }
    return _DetailData(teeTime: tt, requests: requests, isCrew: isCrew);
  }

  Future<void> _refresh() async {
    final f = _load();
    setState(() => _future = f);
    await f;
  }

  Future<void> _act(Future<void> Function() call, String okMsg) async {
    try {
      await call();
      await _refresh();
      if (mounted) showSnack(context, okMsg);
    } on ApiException catch (e) {
      if (mounted) showSnack(context, friendlyApiMessage(e), error: true);
    } catch (e) {
      if (mounted) showSnack(context, 'Failed: $e', error: true);
    }
  }

  String _dateStr(DateTime d) =>
      '${d.year}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

  String _timeStr(TimeOfDay t) =>
      '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';

  Future<void> _editTeeTime(TeeTime tt) async {
    final labelCtrl = TextEditingController(text: tt.label);
    final current = DateTime.tryParse(tt.startsAtUtc.toString())?.toLocal();
    DateTime date = current ?? DateTime.now();
    TimeOfDay time = current == null
        ? TimeOfDay.now()
        : TimeOfDay(hour: current.hour, minute: current.minute);

    final saved = await showDialog<bool>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDlg) => AlertDialog(
          title: const Text('Edit tee time'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextField(
                controller: labelCtrl,
                decoration: const InputDecoration(labelText: 'Name'),
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
                          firstDate: DateTime(DateTime.now().year - 1),
                          lastDate: DateTime(DateTime.now().year + 2),
                        );
                        if (picked != null) setDlg(() => date = picked);
                      },
                      child: Text(_dateStr(date)),
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
            ],
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
    final label = labelCtrl.text.trim();
    if (label.isNotEmpty && label != tt.label) fields['label'] = label;
    final dateIso = _dateStr(date);
    final curDateIso =
        current == null ? null : _dateStr(DateTime(current.year, current.month, current.day));
    if (dateIso != curDateIso) fields['date'] = dateIso;
    final timeIso = _timeStr(time);
    final curTimeIso = current == null
        ? null
        : _timeStr(TimeOfDay(hour: current.hour, minute: current.minute));
    if (timeIso != curTimeIso) fields['time'] = timeIso;
    if (fields.isEmpty) {
      showSnack(context, 'Nothing changed.');
      return;
    }

    try {
      await _api.editTeeTime(tt.id, fields);
      await _refresh();
      if (mounted) showSnack(context, 'Tee time updated.');
    } on ApiException catch (e) {
      if (mounted) showSnack(context, friendlyApiMessage(e), error: true);
    } catch (e) {
      if (mounted) showSnack(context, 'Update failed: $e', error: true);
    }
  }

  Future<void> _deleteTeeTime(TeeTime tt) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Delete tee time?'),
        content: Text(
            'This removes "${tt.label}" and takes its ${tt.players.length} player(s) with it. This can\'t be undone.'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('Keep it'),
          ),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: Colors.red),
            onPressed: () => Navigator.of(ctx).pop(true),
            child: const Text('Delete'),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    try {
      await _api.deleteTeeTime(tt.id);
      if (mounted) {
        showSnack(context, 'Tee time deleted.');
        Navigator.of(context).pop();
      }
    } on ApiException catch (e) {
      if (mounted) showSnack(context, friendlyApiMessage(e), error: true);
    } catch (e) {
      if (mounted) showSnack(context, 'Delete failed: $e', error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Tee time')),
      body: AsyncBody<_DetailData>(
        future: _future,
        onRefresh: _refresh,
        builder: (context, data) {
          final tt = data.teeTime;
          final isCreator = tt.createdBy == widget.myDiscordId;
          final inIt =
              tt.players.any((p) => p.discordId == widget.myDiscordId);
          final pending = data.requests
              .where((r) => r.status.toLowerCase() == 'pending')
              .toList();

          return ListView(
            padding: const EdgeInsets.all(16),
            children: [
              Text(tt.label,
                  style: const TextStyle(
                      fontSize: 22, fontWeight: FontWeight.bold)),
              const SizedBox(height: 4),
              Text(formatLocal(tt.startsAtUtc),
                  style: const TextStyle(color: Colors.grey)),
              Text('${tt.spotsFilled}/${tt.maxPlayers} players'),
              const SizedBox(height: 16),
              const Text('Players',
                  style:
                      TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
              const SizedBox(height: 8),
              if (tt.players.isEmpty)
                const Text('No players yet.',
                    style: TextStyle(color: Colors.grey)),
              ...tt.players.map((p) => ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: const Icon(Icons.person),
                    title: Text(p.displayName),
                    subtitle: p.golfplusHandle != null
                        ? Text('Golf+: ${p.golfplusHandle}')
                        : null,
                    trailing: p.discordId == tt.createdBy
                        ? const Chip(
                            label:
                                Text('CREATOR', style: TextStyle(fontSize: 10)),
                            visualDensity: VisualDensity.compact)
                        : null,
                  )),
              const SizedBox(height: 16),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  if (!inIt && !tt.isFull)
                    ElevatedButton.icon(
                      onPressed: () =>
                          _act(() => _api.joinTeeTime(tt.id), 'Joined.'),
                      icon: const Icon(Icons.add),
                      label: const Text('Join'),
                    ),
                  if (!inIt && tt.isFull)
                    OutlinedButton.icon(
                      onPressed: () => _act(
                          () => _api.requestTeeTime(tt.id),
                          'Request sent. The creator will approve it.'),
                      icon: const Icon(Icons.send),
                      label: const Text('Request to join'),
                    ),
                  if (inIt && !isCreator)
                    OutlinedButton.icon(
                      onPressed: () =>
                          _act(() => _api.leaveTeeTime(tt.id), 'Left.'),
                      icon: const Icon(Icons.remove),
                      label: const Text('Leave'),
                    ),
                  if (inIt)
                    ElevatedButton.icon(
                      onPressed: () {
                        Navigator.of(context).push(MaterialPageRoute(
                          builder: (_) => ScoreEntryScreen(
                            auth: widget.auth,
                            settings: widget.settings,
                            tournament: widget.tournament,
                            teeTime: tt,
                          ),
                        ));
                      },
                      icon: const Icon(Icons.scoreboard),
                      label: const Text('Enter scores'),
                    ),
                  if (!inIt)
                    const Padding(
                      padding: EdgeInsets.only(top: 4),
                      child: Text(
                        'Join this tee time to enter scores.',
                        style: TextStyle(color: Colors.grey, fontSize: 12),
                      ),
                    ),
                ],
              ),
              if (isCreator || data.isCrew) ...[
                const SizedBox(height: 16),
                const Divider(),
                const SizedBox(height: 4),
                const Text('Manage',
                    style:
                        TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
                const SizedBox(height: 8),
                Row(
                  children: [
                    OutlinedButton.icon(
                      onPressed: () => _editTeeTime(tt),
                      icon: const Icon(Icons.edit),
                      label: const Text('Edit'),
                    ),
                    const SizedBox(width: 8),
                    OutlinedButton.icon(
                      onPressed: () => _deleteTeeTime(tt),
                      icon: const Icon(Icons.delete, color: Colors.red),
                      label: const Text('Delete',
                          style: TextStyle(color: Colors.red)),
                    ),
                  ],
                ),
              ],
              if (isCreator && pending.isNotEmpty) ...[                const SizedBox(height: 24),
                const Text('Pending requests',
                    style:
                        TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
                const SizedBox(height: 8),
                ...pending.map((r) => Card(
                      child: ListTile(
                        title: Text(r.displayName),
                        trailing: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            IconButton(
                              icon: const Icon(Icons.check,
                                  color: Colors.green),
                              tooltip: 'Approve',
                              onPressed: () => _act(
                                  () => _api.approveRequest(tt.id, r.id),
                                  'Approved.'),
                            ),
                            IconButton(
                              icon: const Icon(Icons.close,
                                  color: Colors.red),
                              tooltip: 'Decline',
                              onPressed: () => _act(
                                  () => _api.declineRequest(tt.id, r.id),
                                  'Declined.'),
                            ),
                          ],
                        ),
                      ),
                    )),
              ],
            ],
          );
        },
      ),
    );
  }
}

class _DetailData {
  final TeeTime teeTime;
  final List<TeeTimeRequest> requests;
  final bool isCrew;
  _DetailData(
      {required this.teeTime, required this.requests, this.isCrew = false});
}
