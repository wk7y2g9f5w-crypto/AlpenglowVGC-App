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
    return _DetailData(teeTime: tt, requests: requests);
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
                ],
              ),
              if (isCreator && pending.isNotEmpty) ...[
                const SizedBox(height: 24),
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
  _DetailData({required this.teeTime, required this.requests});
}
