import 'dart:ui' as ui;

import 'package:flutter/services.dart';

import 'hole_map_manifest.dart';

/// Palette entry: an exact mask RGB plus the lie it reports. Water has no
/// lie in the API, so it maps to 'rough'.
class _PaletteEntry {
  final int r;
  final int g;
  final int b;
  final String lie;

  const _PaletteEntry(this.r, this.g, this.b, this.lie);
}

/// One hole's realistic map: the rendered image plus a decoded lie mask.
///
/// Coordinate space is 0..1 in image space, identical to the procedural
/// [HoleMapGeometry] path — the image is drawn full-bleed in the same 3:4
/// viewport, so shot save/load payloads are unchanged.
class HoleMapImage {
  final ui.Image map;

  /// Tee-box and pin positions (normalized 0..1 image space), copied from
  /// the manifest asset. Markers are drawn at runtime, not baked in.
  final ui.Offset tee;
  final ui.Offset pin;

  final Uint8List _maskRgba;
  final int _maskW;
  final int _maskH;

  HoleMapImage._({
    required this.map,
    required this.tee,
    required this.pin,
    required this._maskRgba,
    required this._maskW,
    required this._maskH,
  });

  /// Loads [asset]'s map image and decodes its lie mask once. Returns
  /// null on any failure (missing or corrupt asset) so the caller falls
  /// back to the procedural map.
  static Future<HoleMapImage?> load(HoleMapAsset asset) async {
    try {
      final map = await _decodeImage(await rootBundle.load(asset.map));
      final mask = await _decodeImage(await rootBundle.load(asset.mask));
      if (map == null || mask == null) return null;
      final rgba =
          await mask.toByteData(format: ui.ImageByteFormat.rawRgba);
      if (rgba == null) return null;
      return HoleMapImage._(
        map: map,
        tee: asset.tee,
        pin: asset.pin,
        maskRgba: rgba.buffer.asUint8List(),
        maskW: mask.width,
        maskH: mask.height,
      );
    } catch (_) {
      return null;
    }
  }

  static Future<ui.Image?> _decodeImage(ByteData data) async {
    final codec =
        await ui.instantiateImageCodec(data.buffer.asUint8List());
    final frame = await codec.getNextFrame();
    return frame.image;
  }

  static const _palette = <_PaletteEntry>[
    _PaletteEntry(27, 77, 46, 'rough'),
    _PaletteEntry(46, 125, 67, 'fairway'),
    _PaletteEntry(63, 163, 77, 'green'),
    _PaletteEntry(232, 216, 160, 'sand'),
    _PaletteEntry(240, 240, 235, 'tee'),
    // No water lie in the API — water reports as rough.
    _PaletteEntry(58, 110, 165, 'rough'),
  ];

  /// Lie at a 0..1 map coordinate, sampled from the decoded lie mask.
  /// The nearest palette color wins (Euclidean distance), which tolerates
  /// anti-aliased edges in the rendered mask.
  String lieAt(double x, double y) {
    if (_maskRgba.isEmpty || _maskW < 1 || _maskH < 1) return 'rough';
    final px = (x.clamp(0.0, 1.0) * (_maskW - 1)).round();
    final py = (y.clamp(0.0, 1.0) * (_maskH - 1)).round();
    final i = (py * _maskW + px) * 4;
    final r = _maskRgba[i];
    final g = _maskRgba[i + 1];
    final b = _maskRgba[i + 2];
    var best = 'rough';
    var bestDist = 1 << 30;
    for (final p in _palette) {
      final dr = r - p.r;
      final dg = g - p.g;
      final db = b - p.b;
      final d = dr * dr + dg * dg + db * db;
      if (d < bestDist) {
        bestDist = d;
        best = p.lie;
      }
    }
    return best;
  }
}
