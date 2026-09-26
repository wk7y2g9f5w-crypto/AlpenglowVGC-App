import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../services/auth.dart';
import '../services/timezones.dart';
import '../widgets/common.dart';

/// Profile: timezone picker, Golf+ handle, my stats, my registrations.
class ProfileScreen extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;

  const ProfileScreen({super.key, required this.auth, required this.settings});

  @override
  State<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends State<ProfileScreen> {
  late Future<_ProfileData> _future;
  final _tzCtrl = TextEditingController();
  final _handleCtrl = TextEditingController();
  bool _saving = false;

  ApiClient get _api => ApiClient(
      baseUrl: widget.settings.baseUrl, token: widget.auth.token ?? '');

  @override
  void initState() {
    super.initState();
    _future = _load();
  }

  @override
  void dispose() {
    _tzCtrl.dispose();
    _handleCtrl.dispose();
    super.dispose();
  }

  Future<_ProfileData> _load() async {
    final me = await _api.getMe();
    Map<String, dynamic> stats = {};
    try {
      stats = await _api.getMyStats();
    } on ApiException {
      // Stats are nice-to-have.
    }
    List<Tournament> mine = [];
    try {
      final all = await _api.getTournaments();
      mine = all.where((t) => t.registered).toList();
    } on ApiException {
      // Registrations are nice-to-have.
    }
    return _ProfileData(me: me, stats: stats, registrations: mine);
  }

  Future<void> _refresh() async {
    final f = _load();
    setState(() => _future = f);
    await f.then((d) {
      _tzCtrl.text = d.me.timezone ?? '';
      _handleCtrl.text = d.me.golfplusHandle ?? '';
    });
  }

  Future<void> _save() async {
    final tz = _tzCtrl.text.trim();
    if (tz.isNotEmpty && !isValidTimezone(tz)) {
      showSnack(
          context,
          'That doesn\'t look like a valid IANA timezone '
          '(e.g. America/Denver).',
          error: true);
      return;
    }
    setState(() => _saving = true);
    try {
      await _api.updateMe(
        timezone: tz.isEmpty ? null : tz,
        golfplusHandle: _handleCtrl.text.trim(),
      );
      if (mounted) showSnack(context, 'Profile saved.');
      await _refresh();
    } on ApiException catch (e) {
      if (mounted) showSnack(context, friendlyApiMessage(e), error: true);
    } catch (e) {
      if (mounted) showSnack(context, 'Save failed: $e', error: true);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _pickTimezone() async {
    final picked = await showDialog<String>(
      context: context,
      builder: (ctx) {
        String query = '';
        return StatefulBuilder(
          builder: (ctx, setDlg) {
            final filtered = commonTimezones
                .where(
                    (z) => z.toLowerCase().contains(query.toLowerCase()))
                .toList();
            return AlertDialog(
              title: const Text('Pick a timezone'),
              content: SizedBox(
                width: double.maxFinite,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    TextField(
                      decoration: const InputDecoration(
                        hintText: 'Search…',
                        prefixIcon: Icon(Icons.search),
                        border: OutlineInputBorder(),
                        isDense: true,
                      ),
                      onChanged: (v) => setDlg(() => query = v),
                    ),
                    const SizedBox(height: 8),
                    Flexible(
                      child: ListView.builder(
                        shrinkWrap: true,
                        itemCount: filtered.length,
                        itemBuilder: (c, i) => ListTile(
                          dense: true,
                          title: Text(filtered[i]),
                          onTap: () => Navigator.of(ctx).pop(filtered[i]),
                        ),
                      ),
                    ),
                  ],
                ),
              ),
              actions: [
                TextButton(
                    onPressed: () => Navigator.of(ctx).pop(),
                    child: const Text('Cancel')),
              ],
            );
          },
        );
      },
    );
    if (picked != null) setState(() => _tzCtrl.text = picked);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Profile')),
      body: AsyncBody<_ProfileData>(
        future: _future,
        onRefresh: _refresh,
        builder: (context, data) {
          if (_tzCtrl.text.isEmpty && data.me.timezone != null) {
            _tzCtrl.text = data.me.timezone!;
          }
          if (_handleCtrl.text.isEmpty && data.me.golfplusHandle != null) {
            _handleCtrl.text = data.me.golfplusHandle!;
          }
          final tzText = _tzCtrl.text;
          return ListView(
            padding: const EdgeInsets.all(16),
            children: [
              Row(
                children: [
                  const CircleAvatar(
                      radius: 28, child: Icon(Icons.person, size: 32)),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(data.me.displayName,
                            style: const TextStyle(
                                fontSize: 20,
                                fontWeight: FontWeight.bold)),
                        if (data.me.timezone == null ||
                            data.me.timezone!.isEmpty)
                          const Text(
                              'No timezone set — set one below so tee times work.',
                              style: TextStyle(
                                  color: Colors.orange, fontSize: 12)),
                      ],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 20),
              TextField(
                controller: _tzCtrl,
                onChanged: (_) => setState(() {}),
                decoration: InputDecoration(
                  labelText: 'Timezone',
                  hintText: 'America/Denver',
                  border: const OutlineInputBorder(),
                  suffixIcon: IconButton(
                    icon: const Icon(Icons.arrow_drop_down),
                    tooltip: 'Pick from common timezones',
                    onPressed: _pickTimezone,
                  ),
                  helperText:
                      tzText.isEmpty || isValidTimezone(tzText)
                          ? 'IANA format, e.g. America/Denver'
                          : 'Invalid format — expected e.g. America/Denver',
                  helperStyle: TextStyle(
                      color: tzText.isEmpty || isValidTimezone(tzText)
                          ? null
                          : Colors.red),
                ),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _handleCtrl,
                decoration: const InputDecoration(
                  labelText: 'Golf+ username',
                  hintText: 'Your Golf+ handle',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 12),
              SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  onPressed: _saving ? null : _save,
                  child: _saving
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(strokeWidth: 2))
                      : const Text('Save'),
                ),
              ),
              const SizedBox(height: 24),
              const Text('My stats',
                  style:
                      TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
              const SizedBox(height: 8),
              _statsCard(data.stats),
              const SizedBox(height: 24),
              const Text('My registrations',
                  style:
                      TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
              const SizedBox(height: 8),
              if (data.registrations.isEmpty)
                const Text('Not registered for anything yet.',
                    style: TextStyle(color: Colors.grey)),
              ...data.registrations.map((t) => ListTile(
                    contentPadding: EdgeInsets.zero,
                    title: Text(t.name),
                    subtitle: Text(
                        '${t.course ?? ''} · ${formatDateRange(t.startDate, t.endDate)}'),
                    trailing: StatusChip(status: t.status),
                  )),
            ],
          );
        },
      ),
    );
  }

  Widget _statsCard(Map<String, dynamic> stats) {
    if (stats.isEmpty) {
      return const Text('No stats yet — play a round first.',
          style: TextStyle(color: Colors.grey));
    }
    const keys = [
      'rounds',
      'best',
      'avg',
      'average',
      'birdies',
      'pars',
      'bogeys'
    ];
    final shown = <Widget>[];
    for (final k in keys) {
      if (stats.containsKey(k) && stats[k] != null) {
        shown.add(_statTile(k, stats[k].toString()));
      }
    }
    // Anything else the backend returns, show generically.
    for (final e in stats.entries) {
      if (keys.contains(e.key) || e.value == null) continue;
      if (e.value is Map || e.value is List) continue;
      shown.add(_statTile(e.key, e.value.toString()));
    }
    if (shown.isEmpty) {
      return const Text('No stats yet.',
          style: TextStyle(color: Colors.grey));
    }
    return Card(
      child: Column(children: shown),
    );
  }

  Widget _statTile(String key, String value) {
    final label = key
        .replaceAll('_', ' ')
        .split(' ')
        .map((w) => w.isEmpty
            ? w
            : '${w[0].toUpperCase()}${w.substring(1).toLowerCase()}')
        .join(' ');
    return ListTile(
      dense: true,
      title: Text(label),
      trailing:
          Text(value, style: const TextStyle(fontWeight: FontWeight.bold)),
    );
  }
}

class _ProfileData {
  final PlayerMe me;
  final Map<String, dynamic> stats;
  final List<Tournament> registrations;
  _ProfileData(
      {required this.me, required this.stats, required this.registrations});
}
