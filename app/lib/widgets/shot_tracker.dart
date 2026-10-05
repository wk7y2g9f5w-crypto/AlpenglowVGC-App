import 'dart:async';

import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import 'common.dart';
import 'hole_map.dart';
import 'hole_map_image.dart';
import 'hole_map_manifest.dart';

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

  /// Realistic rendered map when one exists for this hole (see
  /// [holeMapManifest]); null means the procedural schematic is used.
  HoleMapImage? _holeImage;

  List<Shot> _shots = [];

  /// Snapshot of what the server has (set on load and after each
  /// successful save). Compared against [_shots] so closing the sheet
  /// can never silently discard tracked work.
  List<Shot> _savedShots = [];
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

  /// Pinch-zoom state. [_scale] is the uniform zoom (1 = whole hole);
  /// [_translate] is the pan offset in viewport pixels, applied with the
  /// transform origin at the top-left so scene point p lands at
  /// p * _scale + _translate.
  double _scale = 1.0;
  Offset _translate = Offset.zero;
  static const double _minScale = 1.0;
  static const double _maxScale = 4.0;

  /// Baseline captured at scale-gesture start for the pinch/pan math.
  Offset _focalStart = Offset.zero;
  double _baseScale = 1.0;
  Offset _baseTranslate = Offset.zero;

  /// Key on the map viewport (the AspectRatio) for focal-point math in
  /// [_onScaleUpdate].
  final GlobalKey _viewportKey = GlobalKey();

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
    // Load the realistic map when the manifest has one. A missing or
    // corrupt asset resolves to null and the procedural schematic stays.
    final asset =
        holeMapManifest[widget.courseName]?[widget.holeNumber];
    if (asset != null) {
      HoleMapImage.load(asset).then((img) {
        if (img != null && mounted) {
          setState(() => _holeImage = img);
        }
      });
    }
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
          _savedShots = List.of(shots);
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
    final lie = _suggestLie(x, y, firstShot: _shots.isEmpty);
    setState(() {
      _shots = [..._shots, Shot(x: x, y: y, lie: lie)];
    });
  }

  /// Suggested lie for a map coordinate. In image mode the lie comes
  /// from the mask (whose tee color yields 'tee'); in procedural mode a
  /// first shot near the tee marker suggests 'tee'.
  String _suggestLie(double x, double y, {bool firstShot = false}) {
    final img = _holeImage;
    if (img != null) return img.lieAt(x, y);
    if (firstShot && _geometry.isNearTee(x, y)) return 'tee';
    return _geometry.lieAt(x, y);
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
  /// across the full canvas with no letterboxing, and the pinch-zoom
  /// Transform sits above the map in the tree, so globalToLocal inverts
  /// the zoom/pan automatically — this stays the exact inverse of the
  /// paint transform at any zoom level.
  Offset? _toMapCoords(Offset globalPosition) {
    final box = _mapBox();
    if (box == null) return null;
    final local = box.globalToLocal(globalPosition);
    // When zoomed and panned, taps can land on the empty margin around
    // the map — ignore those instead of clamping to the map edge.
    if (local.dx < 0 ||
        local.dy < 0 ||
        local.dx > box.size.width ||
        local.dy > box.size.height) {
      return null;
    }
    return Offset(
      local.dx / box.size.width,
      local.dy / box.size.height,
    );
  }

  /// Index of the placed shot nearest to a global pointer position, when
  /// within touch slop — otherwise null.
  int? _shotNear(Offset globalPosition) {
    final box = _mapBox();
    if (box == null || _shots.isEmpty) return null;
    final local = box.globalToLocal(globalPosition);
    int? best;
    // The slop is a screen-space constant but the comparison happens in
    // scene (unzoomed) pixels, so shrink it as the map zooms in.
    var bestDist = _touchSlopPx / _scale;
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

  /// Scale-gesture start: a single-finger press landing on a placed shot
  /// makes that shot the drag target (this also freezes sheet scrolling
  /// for the gesture, as before); anything else starts a map pan/zoom.
  void _onScaleStart(ScaleStartDetails d) {
    if (_loading || _finished) return;
    _focalStart = d.focalPoint;
    _baseScale = _scale;
    _baseTranslate = _translate;
    if (d.pointerCount == 1) {
      final idx = _shotNear(d.focalPoint);
      if (idx != null) {
        setState(() => _dragIndex = idx);
      }
    }
  }

  /// Scale-gesture update: either move the dragged shot, or pan/zoom the
  /// map. The pinch math keeps the content point that was under the
  /// starting focal point fixed, so a one-finger drag pans and a
  /// two-finger pinch zooms around the fingers.
  void _onScaleUpdate(ScaleUpdateDetails d) {
    if (_loading || _finished) return;
    if (_dragIndex != null) {
      _moveDraggedShot(d.focalPoint);
      return;
    }
    final obj = _viewportKey.currentContext?.findRenderObject();
    if (obj is! RenderBox || !obj.hasSize) return;
    final f0 = obj.globalToLocal(_focalStart);
    final f = obj.globalToLocal(d.focalPoint);
    final s = (_baseScale * d.scale).clamp(_minScale, _maxScale);
    final content = (f0 - _baseTranslate) / _baseScale;
    var t = f - content * s;
    // Clamp the pan so the map can never leave the viewport.
    final w = obj.size.width;
    final h = obj.size.height;
    t = Offset(
      t.dx.clamp(w - w * s, 0.0),
      t.dy.clamp(h - h * s, 0.0),
    );
    setState(() {
      _scale = s;
      _translate = t;
    });
  }

  void _onScaleEnd(ScaleEndDetails _) {
    _endDrag();
  }

  /// Reset pinch-zoom back to the whole-hole view.
  void _resetZoom() {
    setState(() {
      _scale = _minScale;
      _translate = Offset.zero;
    });
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
    // The lift is a screen-space constant; local is in scene pixels.
    final y =
        ((local.dy - _dragLiftPx / _scale) / box.size.height).clamp(0.0, 1.0);
    setState(() {
      _shots = [
        ..._shots.sublist(0, idx),
        _shots[idx].copyWith(x: x, y: y, lie: _suggestLie(x, y)),
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

  /// True when the on-screen shots differ from what the server has.
  bool get _dirty {
    if (_shots.length != _savedShots.length) return true;
    for (var i = 0; i < _shots.length; i++) {
      if (_shots[i] != _savedShots[i]) return true;
    }
    return false;
  }

  /// PUT the current shots. Returns true on success. Never pops — the
  /// caller decides what happens next.
  Future<bool> _persist() async {
    if (_saving) return false;
    setState(() => _saving = true);
    try {
      await widget.api.putHoleShots(
          widget.cardId, widget.holeNumber, _shots);
      _savedShots = List.of(_shots);
      if (mounted) setState(() => _saving = false);
      return true;
    } on ApiException catch (e) {
      if (mounted) {
        showSnack(context, friendlyApiMessage(e), error: true);
        setState(() => _saving = false);
      }
      return false;
    } catch (e) {
      if (mounted) {
        showSnack(context, 'Save failed: $e', error: true);
        setState(() => _saving = false);
      }
      return false;
    }
  }

  Future<void> _save() async {
    if (await _persist() && mounted) {
      Navigator.of(context).pop(true);
      showSnack(context,
          'Hole ${widget.holeNumber}: ${_shots.length} shot${_shots.length == 1 ? '' : 's'} saved.');
    }
  }

  /// Close button: unsaved work is flushed first so it is never silently
  /// lost. If the save fails the sheet stays open so the player can
  /// retry instead of losing shots.
  Future<void> _close() async {
    if (_dirty) {
      final ok = await _persist();
      if (!ok || !mounted) return;
    }
    if (mounted) Navigator.of(context).pop(false);
  }

  @override
  void dispose() {
    // Backstop for dismiss paths that bypass the close button
    // (drag-to-dismiss, system back): fire-and-forget the pending shots
    // so they are never silently lost.
    if (_dirty && !_saving) {
      unawaited(widget.api
          .putHoleShots(widget.cardId, widget.holeNumber, _shots)
          .then((_) {}, onError: (_) {}));
    }
    super.dispose();
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
                  onPressed: _saving ? null : _close,
                ),
              ],
            ),
            Text(
              'Tap the map to place each shot\u2019s landing spot. '
              'Drag a placed shot to move it. '
              'Pinch to zoom in for precise placement. '
              '${_holeImage?.yardsPerPixel != null ? 'The amber label shows your latest shot\u2019s distance to the pin. ' : ''}'
              'Shots save automatically when you close.',
              style: const TextStyle(color: Colors.grey, fontSize: 13),
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
              Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Stack(
                    children: [
                      GestureDetector(
                        onTapUp: _onTapUp,
                        onScaleStart: _onScaleStart,
                        onScaleUpdate: _onScaleUpdate,
                        onScaleEnd: _onScaleEnd,
                        child: AspectRatio(
                          key: _viewportKey,
                          aspectRatio: 3 / 4,
                          child: ClipRRect(
                            borderRadius: BorderRadius.circular(12),
                            child: Transform(
                              transform: Matrix4.identity()
                                ..translate(
                                    _translate.dx, _translate.dy)
                                ..scale(_scale),
                              alignment: Alignment.topLeft,
                              child: _holeImage != null
                                  ? HoleMap.imaged(
                                      key: _mapKey,
                                      image: _holeImage!,
                                      shots: _shots,
                                      activeIndex: _dragIndex,
                                      // Distance-to-pin readout follows the shot
                                      // being dragged, else the most recent shot.
                                      labeledIndex: _dragIndex ??
                                          (_shots.isNotEmpty
                                              ? _shots.length - 1
                                              : null),
                                    )
                                  : HoleMap(
                                      key: _mapKey,
                                      geometry: _geometry,
                                      shots: _shots,
                                      activeIndex: _dragIndex,
                                    ),
                            ),
                          ),
                        ),
                      ),
                      // Reset-zoom button, shown only while zoomed. It
                      // lives outside the GestureDetector so tapping it
                      // can never place a shot.
                      if (_scale > _minScale + 0.01)
                        Positioned(
                          right: 8,
                          bottom: 8,
                          child: Material(
                            color: Colors.black54,
                            shape: const CircleBorder(),
                            child: IconButton(
                              icon: const Icon(Icons.zoom_out_map,
                                  color: Colors.white, size: 20),
                              tooltip: 'Reset zoom',
                              onPressed: _resetZoom,
                            ),
                          ),
                        ),
                    ],
                  ),
                  if (_holeImage != null)
                    const Padding(
                      padding: EdgeInsets.only(top: 4),
                      child: Text(
                        '© OpenStreetMap contributors',
                        textAlign: TextAlign.right,
                        style:
                            TextStyle(fontSize: 10, color: Colors.grey),
                      ),
                    ),
                ],
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
