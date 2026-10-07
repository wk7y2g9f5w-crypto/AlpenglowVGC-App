import 'package:flutter/material.dart';
import '../services/api_client.dart';
import '../services/auth.dart';

/// Admin-only Archive: completed tee times, separated into Solo and Group.
/// Tournament entries show when Start Round was pressed. Casual entries show
/// a "Mark Complete" button until an admin reviews them.
class ArchiveScreen extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;

  const ArchiveScreen({super.key, required this.auth, required this.settings});

  @override
  State<ArchiveScreen> createState() => _ArchiveScreenState();
}

class _ArchiveScreenState extends State<ArchiveScreen>
    with SingleTickerProviderStateMixin {
  late TabController _tab;
  ApiClient get _api => ApiClient(
      baseUrl: widget.settings.baseUrl, token: widget.auth.token ?? '');
  bool _loading = true;
  String? _error;
  List<Map<String, dynamic>> _tournamentSolo = [];
  List<Map<String, dynamic>> _tournamentGroup = [];
  List<Map<String, dynamic>> _casualSolo = [];
  List<Map<String, dynamic>> _casualGroup = [];

  @override
  void initState() {
    super.initState();
    _tab = TabController(length: 2, vsync: this);
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final t = await _api.getArchiveTournamentTeeTimes();
      final c = await _api.getArchiveCasualTeeTimes();
      if (mounted) {
        setState(() {
          _tournamentSolo = List<Map<String, dynamic>>.from(t['solo'] ?? []);
          _tournamentGroup = List<Map<String, dynamic>>.from(t['group'] ?? []);
          _casualSolo = List<Map<String, dynamic>>.from(c['solo'] ?? []);
          _casualGroup = List<Map<String, dynamic>>.from(c['group'] ?? []);
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

  Future<void> _markComplete(String ttId) async {
    try {
      await _api.completeCasualTeeTime(ttId);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Marked complete.')),
        );
        _load();
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Failed: $e')),
        );
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

  Widget _buildTournamentCard(Map<String, dynamic> e) {
    final players = (e['players'] as List?) ?? [];
    final names = players
        .map((p) => (p['golfplus_handle'] ?? p['display_name'] ?? '?').toString())
        .join(', ');
    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      child: ListTile(
        leading: const Icon(Icons.emoji_events, color: Colors.amber),
        title: Text(e['label']?.toString() ?? 'Tee time'),
        subtitle: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(e['tournament_name']?.toString() ?? '',
                style: const TextStyle(fontWeight: FontWeight.bold)),
            Text('Players: $names'),
            Text('Started: ${_fmtDate(e['started_at']?.toString())}',
                style: const TextStyle(fontSize: 12, color: Colors.grey)),
            Text('Archived: ${_fmtDate(e['archived_at']?.toString())}',
                style: const TextStyle(fontSize: 12, color: Colors.grey)),
          ],
        ),
        isThreeLine: true,
      ),
    );
  }

  Widget _buildCasualCard(Map<String, dynamic> e) {
    final players = (e['players'] as List?) ?? [];
    final names = players
        .map((p) => (p['golfplus_handle'] ?? p['display_name'] ?? '?').toString())
        .join(', ');
    final completed = e['completed_at'] != null;
    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      child: ListTile(
        leading: Icon(
          Icons.golf_course,
          color: completed ? Colors.green : Colors.orange,
        ),
        title: Text(e['label']?.toString() ?? 'Tee time'),
        subtitle: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(e['course']?.toString() ?? '',
                style: const TextStyle(fontWeight: FontWeight.bold)),
            Text('Players: $names'),
            Text('Archived: ${_fmtDate(e['archived_at']?.toString())}',
                style: const TextStyle(fontSize: 12, color: Colors.grey)),
            if (completed)
              Text('Completed: ${_fmtDate(e['completed_at']?.toString())}',
                  style: const TextStyle(fontSize: 12, color: Colors.green)),
          ],
        ),
        isThreeLine: true,
        trailing: completed
            ? const Chip(
                label: Text('Done', style: TextStyle(fontSize: 10)),
                visualDensity: VisualDensity.compact,
              )
            : TextButton(
                onPressed: () => _markComplete(e['id'].toString()),
                child: const Text('Mark Complete'),
              ),
      ),
    );
  }

  Widget _buildList(List<Map<String, dynamic>> tournament,
      List<Map<String, dynamic>> casual) {
    if (tournament.isEmpty && casual.isEmpty) {
      return const Center(
        child: Text('No archived tee times yet.',
            style: TextStyle(color: Colors.grey)),
      );
    }
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        children: [
          if (tournament.isNotEmpty) ...[
            const Padding(
              padding: EdgeInsets.fromLTRB(16, 12, 16, 4),
              child: Text('Tournaments',
                  style: TextStyle(
                      fontWeight: FontWeight.bold, fontSize: 14)),
            ),
            ...tournament.map(_buildTournamentCard),
          ],
          if (casual.isNotEmpty) ...[
            const Padding(
              padding: EdgeInsets.fromLTRB(16, 12, 16, 4),
              child: Text('Casual',
                  style: TextStyle(
                      fontWeight: FontWeight.bold, fontSize: 14)),
            ),
            ...casual.map(_buildCasualCard),
          ],
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Archive'),
        bottom: TabBar(
          controller: _tab,
          tabs: const [
            Tab(text: 'Solo Rounds'),
            Tab(text: 'Group Rounds'),
          ],
        ),
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
              : TabBarView(
                  controller: _tab,
                  children: [
                    _buildList(_tournamentSolo, _casualSolo),
                    _buildList(_tournamentGroup, _casualGroup),
                  ],
                ),
    );
  }

  @override
  void dispose() {
    _tab.dispose();
    super.dispose();
  }
}
