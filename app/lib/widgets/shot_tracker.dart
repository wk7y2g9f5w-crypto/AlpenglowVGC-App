import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import 'common.dart';
import 'hole_map.dart';

/// Opt-in shot-by-shot tracking for one hole, as a tall bottom sheet.
///
/// Purely additive: nothing here prompts, badges, or requires anything —
/// the sheet only exists while the player has it open. Tap the map to
/// place each shot's landing spot; the lie is suggested from the map
/// geometry with manual override chips. Saving never blocks on a
/// shot-count vs strokes mismatch — it shows a gentle inline warning.
Future<bool> showShotTracker({
  required BuildContext context,
  required ApiClient api,
  required int cardId,
  required String courseName,
  required int holeNumber,
  required int par,
  int? strokes,
}) async {
  final saved = await showModalBottomSheet<bool>(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    builder: (ctx) => _ShotTrackerSheet(
      api: api,
      cardId: cardId,
      courseName: courseName,
      holeNumber: holeNumber,
      par: par,
      strokes: strokes,
    ),
  );
  return saved == true;
}

const _lies = ['tee', 'fairway', 'rough', 'sand', 'green'];

class _ShotTrackerSheet extends StatefulWidget {
  final ApiClient api;
  final int cardId;
  final String courseName;
  final int holeNumber;
  final int par;
  final int? strokes;

  const _ShotTrackerSheet({
    required this.api,
    required this.cardId,
    required this.courseName,
    required this.holeNumber,
    required this.par,
    this.strokes,
  });

  @override
  State<_ShotTrackerSheet> createState() => _ShotTrackerSheetState();
}

class _ShotTrackerSheetState extends State<_ShotTrackerSheet> {
  late final HoleMapGeometry _geometry;
  List<Shot> _shots = [];
  bool _loading = true;
  bool _saving = false;
  String? _loadError;

  bool get _finished => _shots.any((s) => s.holed);

  @override
  void initState() {
    super.initState();
    _geometry = HoleMapGeometry(
      courseName: widget.courseName,
      holeNumber: widget.holeNumber,
      par: widget.par,
    );
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _loadError = null;
    });
    try {
      final shots =
          await widget.api.getHoleShots(widget.cardId, widget.holeNumber);
      if (mounted) {
        setState(() {
          _shots = shots;
          _loading = false;
        });
      }
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _loading = false;
          _loadError = friendlyApiMessage(e);
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _loading = false;
          _loadError = e.toString();
        });
      }
    }
  }

  void _addShot(double x, double y) {
    if (_loading || _finished) return;
    x = x.clamp(0.0, 1.0);
    y = y.clamp(0.0, 1.0);
    final lie = _shots.isEmpty && _geometry.isNearTee(x, y)
        ? 'tee'
        : _geometry.lieAt(x, y);
    setState(() {
      _shots = [..._shots, Shot(x: x, y: y, lie: lie)];
    });
  }

  void _setLastLie(String lie) {
    if (_shots.isEmpty) return;
    setState(() {
      _shots = [
        ..._shots.sublist(0, _shots.length - 1),
        _shots.last.copyWith(lie: lie),
      ];
    });
  }

  void _holedIt() {
    if (_shots.isEmpty || _finished) return;
    setState(() {
      _shots = [
        ..._shots.sublist(0, _shots.length - 1),
        _shots.last.copyWith(holed: true),
      ];
    });
  }

  void _undo() {
    if (_shots.isEmpty) return;
    setState(() => _shots = _shots.sublist(0, _shots.length - 1));
  }

  Future<void> _clear() async {
    if (_shots.isEmpty) return;
    final confirm = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Clear tracked shots?'),
        content: Text(
            'Remove all ${_shots.length} tracked shots for hole ${widget.holeNumber}? '
            'Your entered score is kept.'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('Keep'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            style: FilledButton.styleFrom(
                backgroundColor: Colors.red.shade700),
            child: const Text('Clear'),
          ),
        ],
      ),
    );
    if (confirm == true && mounted) {
      setState(() => _shots = []);
    }
  }

  Future<void> _save() async {
    if (_saving) return;
    setState(() => _saving = true);
    try {
      await widget.api.putHoleShots(
          widget.cardId, widget.holeNumber, _shots);
      if (mounted) {
        Navigator.of(context).pop(true);
        showSnack(context,
            'Hole ${widget.holeNumber}: ${_shots.length} shot${_shots.length == 1 ? '' : 's'} saved.');
      }
    } on ApiException catch (e) {
      if (mounted) {
        showSnack(context, friendlyApiMessage(e), error: true);
        setState(() => _saving = false);
      }
    } catch (e) {
      if (mounted) {
        showSnack(context, 'Save failed: $e', error: true);
        setState(() => _saving = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final strokes = widget.strokes;
    final showMismatch = !_loading &&
        strokes != null &&
        strokes > 0 &&
        _shots.length != strokes;
    return DraggableScrollableSheet(
      initialChildSize: 0.94,
      minChildSize: 0.6,
      maxChildSize: 0.98,
      expand: false,
      builder: (ctx, scrollCtrl) => SingleChildScrollView(
        controller: scrollCtrl,
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Center(
              child: Container(
                width: 40,
                height: 4,
                margin: const EdgeInsets.only(bottom: 8),
                decoration: BoxDecoration(
                  color: Colors.grey.shade400,
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
            ),
            Row(
              children: [
                Expanded(
                  child: Text(
                    'Hole ${widget.holeNumber} · Par ${widget.par}',
                    style: const TextStyle(
                        fontSize: 18, fontWeight: FontWeight.bold),
                  ),
                ),
                if (!_loading)
                  Text(
                    '${_shots.length} shot${_shots.length == 1 ? '' : 's'}',
                    style: const TextStyle(color: Colors.grey),
                  ),
                IconButton(
                  icon: const Icon(Icons.close),
                  tooltip: 'Close',
                  onPressed: () => Navigator.of(context).pop(false),
                ),
              ],
            ),
            const Text(
              'Tap the map to place each shot\u2019s landing spot.',
              style: TextStyle(color: Colors.grey, fontSize: 13),
            ),
            const SizedBox(height: 8),
            if (_loading)
              const AspectRatio(
                aspectRatio: 3 / 4,
                child: Center(child: CircularProgressIndicator()),
              )
            else if (_loadError != null)
              AspectRatio(
                aspectRatio: 3 / 4,
                child: Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(_loadError!,
                          textAlign: TextAlign.center,
                          style: const TextStyle(color: Colors.grey)),
                      const SizedBox(height: 8),
                      ElevatedButton(
                          onPressed: _load,
                          child: const Text('Retry')),
                    ],
                  ),
                ),
              )
            else
              LayoutBuilder(
                builder: (ctx, constraints) => GestureDetector(
                  onTapDown: (d) => _addShot(
                    d.localPosition.dx / constraints.maxWidth,
                    d.localPosition.dy / constraints.maxHeight,
                  ),
                  child: AspectRatio(
                    aspectRatio: 3 / 4,
                    child: ClipRRect(
                      borderRadius: BorderRadius.circular(12),
                      child: HoleMap(
                          geometry: _geometry, shots: _shots),
                    ),
                  ),
                ),
              ),
            if (_finished) ...[
              const SizedBox(height: 8),
              const Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Icon(Icons.check_circle,
                      color: Colors.green, size: 18),
                  SizedBox(width: 6),
                  Text('Holed out — nice.',
                      style: TextStyle(
                          color: Colors.green,
                          fontWeight: FontWeight.w600)),
                ],
              ),
            ],
            if (showMismatch) ...[
              const SizedBox(height: 8),
              Text(
                '⚠ ${_shots.length} shot${_shots.length == 1 ? '' : 's'} tracked, '
                '$strokes stroke${strokes == 1 ? '' : 's'} entered — '
                'both are kept as-is.',
                textAlign: TextAlign.center,
                style:
                    const TextStyle(color: Colors.orange, fontSize: 13),
              ),
            ],
            const SizedBox(height: 12),
            // Lie override for the most recent shot.
            Row(
              children: [
                const Text('Last shot lie:',
                    style: TextStyle(fontWeight: FontWeight.w600)),
                const SizedBox(width: 8),
                Expanded(
                  child: SingleChildScrollView(
                    scrollDirection: Axis.horizontal,
                    child: Row(
                      children: _lies.map((lie) {
                        final selected = _shots.isNotEmpty &&
                            _shots.last.lie == lie;
                        return Padding(
                          padding:
                              const EdgeInsets.only(right: 6),
                          child: ChoiceChip(
                            label: Text(lie,
                                style: const TextStyle(
                                    fontSize: 12)),
                            selected: selected,
                            visualDensity:
                                VisualDensity.compact,
                            onSelected: _shots.isEmpty
                                ? null
                                : (_) => _setLastLie(lie),
                          ),
                        );
                      }).toList(),
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
            Row(
              children: [
                TextButton.icon(
                  onPressed: _shots.isEmpty ? null : _undo,
                  icon: const Icon(Icons.undo, size: 18),
                  label: const Text('Undo'),
                ),
                TextButton.icon(
                  onPressed: _shots.isEmpty ? null : _clear,
                  icon: const Icon(Icons.delete_outline, size: 18),
                  label: const Text('Clear hole'),
                  style: TextButton.styleFrom(
                      foregroundColor: Colors.red.shade700),
                ),
                const Spacer(),
                OutlinedButton.icon(
                  onPressed: (_shots.isEmpty || _finished)
                      ? null
                      : _holedIt,
                  icon: const Icon(Icons.flag, size: 18),
                  label: const Text('Holed it'),
                ),
              ],
            ),
            const SizedBox(height: 8),
            SizedBox(
              width: double.infinity,
              child: ElevatedButton(
                onPressed: (_loading || _saving) ? null : _save,
                child: _saving
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(
                            strokeWidth: 2))
                    : const Text('Save shots'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
