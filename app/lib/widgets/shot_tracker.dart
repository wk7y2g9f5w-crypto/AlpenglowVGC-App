import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import 'common.dart';
import 'hole_map.dart';

/// Opt-in shot-by-shot tracking for one hole, as a tall bottom sheet.
///
/// Purely additive: nothing here prompts, badges, or requires anything —
/// the sheet only exists while the player has it open. Tap the map to
/// place each shot's landing spot, or drag a placed shot to fine-tune it;
/// the lie is suggested from the map geometry with manual override chips.
/// Saving never blocks on a shot-count vs strokes mismatch — it shows a
/// gentle inline warning.
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

  /// Key on the HoleMap so taps/drags are measured in the map's own
  /// rendered coordinate space. (The old code divided the tap's local
  /// position by the LayoutBuilder constraints — but maxHeight is
  /// unbounded inside the scroll view, so every tap's y collapsed to 0
  /// and shots always landed on the top edge.)
  final GlobalKey _mapKey = GlobalKey();

  /// Index of the shot currently being dragged, or null.
  int? _dragIndex;

  /// A press starting within this many logical pixels of a placed shot
  /// drags that shot instead of adding a new one.
  static const double _touchSlopPx = 24.0;

  /// While dragging, the stored point is lifted this many pixels above
  /// the fingertip so the marker stays visible under the finger.
  static const double _dragLiftPx = 32.0;

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

  /// The map's own RenderBox, or null if it isn't laid out yet.
  RenderBox? _mapBox() {
    final obj = _mapKey.currentContext?.findRenderObject();
    if (obj is RenderBox && obj.hasSize && obj.size.width > 0 && obj.size.height > 0) {
      return obj;
    }
    return null;
  }

  /// Convert a global pointer position to 0..1 map coordinates using the
  /// map's actual rendered size. The painter draws the 0..1 geometry
  /// across the full canvas with no letterboxing, so this is the exact
  /// inverse of the paint transform.
  Offset? _toMapCoords(Offset globalPosition) {
    final box = _mapBox();
    if (box == null) return null;
    final local = box.globalToLocal(globalPosition);
    return Offset(
      (local.dx / box.size.width).clamp(0.0, 1.0),
      (local.dy / box.size.height).clamp(0.0, 1.0),
    );
  }

  /// Index of the placed shot nearest to a global pointer position, when
  /// within touch slop — otherwise null.
  int? _shotNear(Offset globalPosition) {
    final box = _mapBox();
    if (box == null || _shots.isEmpty) return null;
    final local = box.globalToLocal(globalPosition);
    int? best;
    var bestDist = _touchSlopPx;
    for (var i = 0; i < _shots.length; i++) {
      final p = Offset(
          _shots[i].x * box.size.width, _shots[i].y * box.size.height);
      final d = (p - local).distance;
      if (d <= bestDist) {
        bestDist = d;
        best = i;
      }
    }
    return best;
  }

  /// Tap (finger lifted without moving): add a shot, unless the tap
  /// landed on an existing shot.
  void _onTapUp(TapUpDetails d) {
    if (_loading || _finished) return;
    if (_shotNear(d.globalPosition) != null) return;
    final coords = _toMapCoords(d.globalPosition);
    if (coords == null) return;
    _addShot(coords.dx, coords.dy);
  }

  /// Press down: if it starts on a placed shot, that shot becomes the
  /// drag target (this also freezes sheet scrolling for the gesture).
  void _onPanDown(DragDownDetails d) {
    if (_loading || _finished) return;
    final idx = _shotNear(d.globalPosition);
    if (idx != null) {
      setState(() => _dragIndex = idx);
    }
  }

  /// Drag move: reposition the dragged shot, lifted above the fingertip
  /// so the marker stays visible. The lie is re-suggested from the new
  /// spot, matching placement behavior.
  void _moveDraggedShot(Offset globalPosition) {
    final idx = _dragIndex;
    if (idx == null || idx >= _shots.length) return;
    final box = _mapBox();
    if (box == null) return;
    final local = box.globalToLocal(globalPosition);
    final x = (local.dx / box.size.width).clamp(0.0, 1.0);
    final y = ((local.dy - _dragLiftPx) / box.size.height).clamp(0.0, 1.0);
    setState(() {
      _shots = [
        ..._shots.sublist(0, idx),
        _shots[idx].copyWith(x: x, y: y, lie: _geometry.lieAt(x, y)),
        ..._shots.sublist(idx + 1),
      ];
    });
  }

  void _endDrag() {
    if (_dragIndex != null) {
      setState(() => _dragIndex = null);
    }
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
        // Freeze sheet scrolling while a shot is being dragged so the
        // drag gesture isn't stolen by the scroll view.
        physics: _dragIndex != null
            ? const NeverScrollableScrollPhysics()
            : null,
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
              'Tap the map to place each shot\u2019s landing spot. '
              'Drag a placed shot to move it.',
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
              GestureDetector(
                onTapUp: _onTapUp,
                onPanDown: _onPanDown,
                onPanUpdate: (d) => _moveDraggedShot(d.globalPosition),
                onPanEnd: (_) => _endDrag(),
                onPanCancel: _endDrag,
                child: AspectRatio(
                  aspectRatio: 3 / 4,
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(12),
                    child: HoleMap(
                      key: _mapKey,
                      geometry: _geometry,
                      shots: _shots,
                      activeIndex: _dragIndex,
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
