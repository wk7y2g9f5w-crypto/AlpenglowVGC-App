import 'package:flutter/material.dart';
import '../models/models.dart';
import '../services/api_client.dart';
import '../services/auth.dart';

/// Admin review screen for verifying scorecards in a tee time.
/// Shows all players' cards; verify individually or all at once.
/// After verifying all, pops back to the tee-time list (tournament detail).
class VerifyScorecardsScreen extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;
  final Tournament tournament;
  final TeeTime teeTime;

  const VerifyScorecardsScreen({
    super.key,
    required this.auth,
    required this.settings,
    required this.tournament,
    required this.teeTime,
  });

  @override
  State<VerifyScorecardsScreen> createState() =>
      _VerifyScorecardsScreenState();
}

class _PlayerCardEntry {
  final TeeTimePlayer player;
  final Scorecard? card;
  bool verifying = false;
  _PlayerCardEntry(this.player, this.card);
}

class _VerifyScorecardsScreenState extends State<VerifyScorecardsScreen> {
  ApiClient get _api => ApiClient(
      baseUrl: widget.settings.baseUrl, token: widget.auth.token ?? '');

  bool _loading = true;
  bool _verifyingAll = false;
  String? _error;
  List<_PlayerCardEntry> _entries = [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final entries = <_PlayerCardEntry>[];
      for (final p in widget.teeTime.players) {
        final card = await _api.getScorecard(
          widget.teeTime.id,
          playerDiscordId: p.discordId,
          roundNumber: widget.teeTime.roundNumber,
        );
        entries.add(_PlayerCardEntry(p, card));
      }
      if (mounted) {
        setState(() {
          _entries = entries;
          _loading = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = e.toString();
          _loading = false;
        });
      }
    }
  }

  int get _pendingCount =>
      _entries.where((e) => e.card?.status == 'pending').length;

  Future<void> _verifyOne(_PlayerCardEntry entry) async {
    final cardId = entry.card?.id;
    if (cardId == null) return;
    setState(() => entry.verifying = true);
    try {
      await _api.verifyScorecard(cardId);
      await _load();
      if (mounted && _pendingCount == 0) _doneAndGoBack();
    } catch (e) {
      if (mounted) {
        setState(() => entry.verifying = false);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Verify failed: $e')),
        );
      }
    }
  }

  Future<void> _verifyAll() async {
    setState(() => _verifyingAll = true);
    try {
      final r = await _api.verifyTeeTime(widget.teeTime.id);
      final n = r['verified'] ?? 0;
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Verified $n scorecard${n == 1 ? '' : 's'}.')),
        );
        _doneAndGoBack();
      }
    } catch (e) {
      if (mounted) {
        setState(() => _verifyingAll = false);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Verify all failed: $e')),
        );
      }
    }
  }

  /// Pop back to the tournament detail (tee-time list). The verified tee
  /// time is archived and no longer in the active list, so staying on its
  /// detail screen would error out.
  void _doneAndGoBack() {
    // Stack: tournament detail -> tee-time detail -> this screen.
    // Pop twice to land back on the tournament detail (tee-time list).
    Navigator.of(context).pop();
    Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text('Verify — ${widget.teeTime.label}'),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Text('Failed to load: $_error'),
                      const SizedBox(height: 12),
                      ElevatedButton(
                          onPressed: _load, child: const Text('Retry')),
                    ],
                  ),
                )
              : Column(
                  children: [
                    if (_pendingCount > 0)
                      Padding(
                        padding: const EdgeInsets.all(16),
                        child: SizedBox(
                          width: double.infinity,
                          child: ElevatedButton.icon(
                            onPressed:
                                _verifyingAll ? null : _verifyAll,
                            icon: _verifyingAll
                                ? const SizedBox(
                                    width: 16,
                                    height: 16,
                                    child: CircularProgressIndicator(
                                        strokeWidth: 2))
                                : const Icon(Icons.verified),
                            label: Text(
                                'Verify all ($_pendingCount pending)'),
                            style: ElevatedButton.styleFrom(
                              backgroundColor: Colors.green,
                              foregroundColor: Colors.white,
                              padding: const EdgeInsets.symmetric(
                                  vertical: 14),
                            ),
                          ),
                        ),
                      ),
                    Expanded(
                      child: ListView.separated(
                        itemCount: _entries.length,
                        separatorBuilder: (_, __) =>
                            const Divider(height: 1),
                        itemBuilder: (context, i) {
                          final e = _entries[i];
                          final card = e.card;
                          final name = golferDisplayName(
                              e.player.displayName,
                              e.player.golfplusHandle);
                          final isPending = card?.status == 'pending';
                          return ListTile(
                            leading: CircleAvatar(
                              child: Text(name.isNotEmpty
                                  ? name[0].toUpperCase()
                                  : '?'),
                            ),
                            title: Text(name,
                                style: const TextStyle(
                                    fontWeight: FontWeight.w600)),
                            subtitle: card == null
                                ? const Text('No scorecard submitted',
                                    style: TextStyle(
                                        fontStyle: FontStyle.italic))
                                : Text(
                                    'Total ${card.total ?? '–'}'
                                    '${card.toPar != null ? ' (${card.toPar! > 0 ? '+' : ''}${card.toPar})' : ''}'
                                    ' • ${card.status}',
                                    style: TextStyle(
                                      color: card.status == 'verified'
                                          ? Colors.green
                                          : card.status == 'pending'
                                              ? Colors.orange
                                              : Colors.grey,
                                      fontSize: 12,
                                    ),
                                  ),
                            trailing: isPending
                                ? (e.verifying
                                    ? const SizedBox(
                                        width: 20,
                                        height: 20,
                                        child:
                                            CircularProgressIndicator(
                                                strokeWidth: 2))
                                    : TextButton(
                                        onPressed: () =>
                                            _verifyOne(e),
                                        child: const Text('Verify'),
                                      ))
                                : (card?.status == 'verified'
                                    ? const Icon(Icons.check_circle,
                                        color: Colors.green)
                                    : null),
                          );
                        },
                      ),
                    ),
                  ],
                ),
    );
  }
}
