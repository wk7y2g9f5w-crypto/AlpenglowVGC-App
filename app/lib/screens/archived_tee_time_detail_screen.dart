import 'package:flutter/material.dart';
import '../models/models.dart';
import '../services/api_client.dart';
import '../services/auth.dart';
import 'score_entry_screen.dart';

/// Read-only view of an archived tournament tee time's submitted scorecards.
/// Admins get an "Edit scores" button to switch to the editable editor.
class ArchivedTeeTimeDetailScreen extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;
  final Tournament tournament;
  final TeeTime teeTime;

  const ArchivedTeeTimeDetailScreen({
    super.key,
    required this.auth,
    required this.settings,
    required this.tournament,
    required this.teeTime,
  });

  @override
  State<ArchivedTeeTimeDetailScreen> createState() =>
      _ArchivedTeeTimeDetailScreenState();
}

class _PlayerCard {
  final TeeTimePlayer player;
  final Scorecard? card;
  _PlayerCard(this.player, this.card);
}

class _ArchivedTeeTimeDetailScreenState
    extends State<ArchivedTeeTimeDetailScreen> {
  ApiClient get _api => ApiClient(
      baseUrl: widget.settings.baseUrl, token: widget.auth.token ?? '');

  bool _loading = true;
  String? _error;
  List<_PlayerCard> _cards = [];
  bool _isAdmin = false;
  int? _expandedIndex;
  String _myDiscordId = '';

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
      final me = await _api.getMe();
      final cards = <_PlayerCard>[];
      for (final p in widget.teeTime.players) {
        final card = await _api.getScorecard(
          widget.teeTime.id,
          playerDiscordId: p.discordId,
          roundNumber: widget.teeTime.roundNumber,
        );
        cards.add(_PlayerCard(p, card));
      }
      if (mounted) {
        setState(() {
          _isAdmin = me.isAdmin;
          _myDiscordId = me.discordId;
          _cards = cards;
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

  String _fmtDate(String? iso) {
    if (iso == null) return '—';
    try {
      final dt = DateTime.parse(iso).toLocal();
      return '${dt.month}/${dt.day}/${dt.year} ${dt.hour}:${dt.minute.toString().padLeft(2, '0')}';
    } catch (_) {
      return iso;
    }
  }

  Widget _holeGrid(Scorecard card) {
    final pars = widget.tournament.pars ?? [];
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      child: Table(
        border: TableBorder.all(color: Colors.grey.shade300, width: 0.5),
        children: [
          TableRow(
            children: [
              const Padding(
                padding: EdgeInsets.all(4),
                child: Text('Hole',
                    style:
                        TextStyle(fontWeight: FontWeight.bold, fontSize: 11)),
              ),
              ...List.generate(
                card.scores.length,
                (i) => Padding(
                  padding: const EdgeInsets.all(4),
                  child: Text('${i + 1}',
                      textAlign: TextAlign.center,
                      style: const TextStyle(fontSize: 11)),
                ),
              ),
            ],
          ),
          TableRow(
            children: [
              const Padding(
                padding: EdgeInsets.all(4),
                child: Text('Par',
                    style:
                        TextStyle(fontWeight: FontWeight.bold, fontSize: 11)),
              ),
              ...List.generate(
                card.scores.length,
                (i) => Padding(
                  padding: const EdgeInsets.all(4),
                  child: Text(
                      '${i < pars.length ? pars[i] : '–'}',
                      textAlign: TextAlign.center,
                      style:
                          const TextStyle(fontSize: 11, color: Colors.grey)),
                ),
              ),
            ],
          ),
          TableRow(
            children: [
              const Padding(
                padding: EdgeInsets.all(4),
                child: Text('Score',
                    style:
                        TextStyle(fontWeight: FontWeight.bold, fontSize: 11)),
              ),
              ...card.scores.map(
                (s) => Padding(
                  padding: const EdgeInsets.all(4),
                  child: Text('${s ?? '–'}',
                      textAlign: TextAlign.center,
                      style: const TextStyle(
                          fontSize: 12, fontWeight: FontWeight.w600)),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.teeTime.label),
        actions: [
          if (_isAdmin && !_loading && _error == null)
            TextButton(
              onPressed: () {
                Navigator.of(context).push(MaterialPageRoute(
                  builder: (_) => ScoreEntryScreen(
                    auth: widget.auth,
                    settings: widget.settings,
                    tournament: widget.tournament,
                    teeTime: widget.teeTime,
                    myDiscordId: _myDiscordId,
                  ),
                ));
              },
              child: const Text('Edit scores',
                  style: TextStyle(color: Colors.white)),
            ),
        ],
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
              : RefreshIndicator(
                  onRefresh: _load,
                  child: ListView(
                    children: [
                      Padding(
                        padding: const EdgeInsets.all(16),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(widget.tournament.name,
                                style: const TextStyle(
                                    fontWeight: FontWeight.bold,
                                    fontSize: 16)),
                            const SizedBox(height: 4),
                            Text(
                                'Started: ${_fmtDate(widget.teeTime.startedAtUtc?.toIso8601String())}',
                                style: const TextStyle(
                                    fontSize: 12, color: Colors.grey)),
                            Text(
                                'Archived: ${_fmtDate(widget.teeTime.archivedAtUtc?.toIso8601String())}',
                                style: const TextStyle(
                                    fontSize: 12, color: Colors.grey)),
                          ],
                        ),
                      ),
                      const Divider(),
                      ..._cards.asMap().entries.map((entry) {
                        final i = entry.key;
                        final pc = entry.value;
                        final card = pc.card;
                        final name = golferDisplayName(pc.player.displayName,
                            pc.player.golfplusHandle);
                        final expanded = _expandedIndex == i;
                        return Column(
                          children: [
                            ListTile(
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
                                            : Colors.orange,
                                        fontSize: 12,
                                      ),
                                    ),
                              trailing: card == null
                                  ? null
                                  : Icon(expanded
                                      ? Icons.expand_less
                                      : Icons.expand_more),
                              onTap: card == null
                                  ? null
                                  : () => setState(() => _expandedIndex =
                                      expanded ? null : i),
                            ),
                            if (expanded && card != null) _holeGrid(card),
                            const Divider(height: 1),
                          ],
                        );
                      }),
                    ],
                  ),
                ),
    );
  }
}
