import 'dart:math';

import 'package:flutter/material.dart';

import '../models/models.dart';
import 'hole_map_image.dart';
import 'hole_map_manifest.dart';

/// Stylized, procedurally drawn hole schematic for shot tracking.
///
/// The hole shape is deterministic per (course, hole): par 3s are short and
/// straight, par 4s dogleg left or right (seeded), par 5s are longer with a
/// dogleg or a gentle S. Coordinate space is 0..1 (x right, y down), tee near
/// the bottom, green near the top.
///
/// Painter and hit-test share one [HoleMapGeometry] so the lie suggestion
/// can never drift from what's drawn: green ellipse -> 'green', bunker ->
/// 'sand', fairway polygon -> 'fairway', everything else -> 'rough'.
/// Ellipse primitive in the hole map's 0..1 coordinate space, used for the
/// green and the bunkers by both the painter and the [HoleMapGeometry.lieAt]
/// hit-test.
class HoleEllipse {
  final double cx;
  final double cy;
  final double rx;
  final double ry;

  const HoleEllipse(
      {required this.cx,
      required this.cy,
      required this.rx,
      required this.ry});

  bool contains(double x, double y) {
    final dx = (x - cx) / rx;
    final dy = (y - cy) / ry;
    return dx * dx + dy * dy <= 1.0;
  }
}

/// Deterministic geometry for one hole's map. Build once per hole and hand
/// the same instance to both [HoleMap] and [lieAt].
class HoleMapGeometry {
  final String courseName;
  final int holeNumber;
  final int par;

  late final List<Offset> path;
  late final List<Offset> fairwayPolygon;
  late final List<HoleEllipse> bunkers;
  late final HoleEllipse green;
  late final Offset pin;
  late final Offset tee;

  HoleMapGeometry(
      {required this.courseName,
      required this.holeNumber,
      required this.par}) {
    _build();
  }

  void _build() {
    final rand = Random(courseName.hashCode ^ holeNumber);
    const minX = 0.16;
    const maxX = 0.84;
    double cx(double x) => x.clamp(minX, maxX);

    if (par <= 3) {
      // Short, straight par 3.
      path = const [Offset(0.5, 0.80), Offset(0.5, 0.30)];
    } else if (par == 4) {
      // Dogleg left or right, seeded.
      final left = rand.nextBool();
      final bendX =
          cx(0.5 + (left ? -1 : 1) * (0.13 + rand.nextDouble() * 0.10));
      path = [
        const Offset(0.5, 0.92),
        const Offset(0.5, 0.54),
        Offset(bendX, 0.32),
        Offset(bendX, 0.10),
      ];
    } else {
      // Longer par 5: dogleg or gentle S, seeded.
      final dir = rand.nextBool() ? 1.0 : -1.0;
      final sShape = rand.nextBool();
      final x1 = cx(0.5 + dir * (0.10 + rand.nextDouble() * 0.08));
      final x2 = sShape
          ? cx(0.5 - dir * (0.08 + rand.nextDouble() * 0.10))
          : cx(x1 + dir * (0.05 + rand.nextDouble() * 0.07));
      path = [
        const Offset(0.5, 0.92),
        const Offset(0.5, 0.68),
        Offset(x1, 0.45),
        Offset(x2, 0.24),
        Offset(x2, 0.10),
      ];
    }

    tee = path.first;
    pin = path.last;
    green = HoleEllipse(
      cx: pin.dx,
      cy: pin.dy,
      rx: par <= 3 ? 0.075 : 0.09,
      ry: 0.045,
    );
    _buildFairway();
    _buildBunkers(rand);
  }

  /// Fairway: tapered polygon following the centerline — narrower at the
  /// tee and the green, widest mid-hole.
  void _buildFairway() {
    final n = path.length;
    final left = <Offset>[];
    final right = <Offset>[];
    for (var i = 0; i < n; i++) {
      final a = path[max(0, i - 1)];
      final b = path[min(n - 1, i + 1)];
      var d = b - a;
      if (d.distance < 1e-6) d = const Offset(0, -1);
      d = d / d.distance;
      final perp = Offset(-d.dy, d.dx);
      final t = n == 1 ? 0.5 : i / (n - 1);
      var w = 0.06 + 0.03 * sin(pi * t);
      if (par <= 3) w *= 0.8;
      left.add(path[i] + perp * w);
      right.add(path[i] - perp * w);
    }
    fairwayPolygon = [...left, ...right.reversed];
  }

  void _buildBunkers(Random rand) {
    final list = <HoleEllipse>[];
    // One fairway bunker off the side, mid-hole.
    final mp = path[path.length ~/ 2];
    final side = rand.nextBool() ? 1.0 : -1.0;
    list.add(HoleEllipse(
      cx: (mp.dx + side * 0.115).clamp(0.05, 0.95),
      cy: (mp.dy + 0.02).clamp(0.05, 0.95),
      rx: 0.035,
      ry: 0.022,
    ));
    // Two greenside bunkers, clear of the green edge.
    final gside = rand.nextBool() ? 1.0 : -1.0;
    list.add(HoleEllipse(
      cx: (green.cx + gside * 0.135).clamp(0.05, 0.95),
      cy: (green.cy + 0.015).clamp(0.05, 0.95),
      rx: 0.032,
      ry: 0.022,
    ));
    list.add(HoleEllipse(
      cx: (green.cx - gside * 0.125).clamp(0.05, 0.95),
      cy: (green.cy - 0.06).clamp(0.05, 0.95),
      rx: 0.028,
      ry: 0.020,
    ));
    bunkers = list;
  }

  /// Lie at a 0..1 map coordinate, using the exact shapes the painter
  /// draws (green > bunker > fairway > rough).
  String lieAt(double x, double y) {
    x = x.clamp(0.0, 1.0);
    y = y.clamp(0.0, 1.0);
    if (green.contains(x, y)) return 'green';
    for (final b in bunkers) {
      if (b.contains(x, y)) return 'sand';
    }
    if (_pointInPolygon(x, y, fairwayPolygon)) return 'fairway';
    return 'rough';
  }

  /// True when (x, y) is within a small radius of the tee marker — the
  /// tracker uses this to suggest the 'tee' lie for a first tap.
  bool isNearTee(double x, double y, [double radius = 0.045]) {
    final dx = x - tee.dx;
    final dy = y - tee.dy;
    return dx * dx + dy * dy <= radius * radius;
  }

  bool _pointInPolygon(double x, double y, List<Offset> poly) {
    var inside = false;
    for (var i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      final xi = poly[i].dx;
      final yi = poly[i].dy;
      final xj = poly[j].dx;
      final yj = poly[j].dy;
      if ((yi > y) != (yj > y) &&
          x < (xj - xi) * (y - yi) / (yj - yi) + xi) {
        inside = !inside;
      }
    }
    return inside;
  }
}

/// Paints [HoleMapGeometry] plus numbered shot markers with a connecting
/// trail from the tee. The parent must constrain the size (e.g. with an
/// AspectRatio); paint coordinates are the 0..1 map space.
class HoleMapPainter extends CustomPainter {
  final HoleMapGeometry geometry;
  final List<Shot> shots;

  /// Index of the shot currently being dragged, if any. It is drawn with
  /// a highlight ring so the player can see it while their finger is on
  /// the map.
  final int? activeIndex;

  HoleMapPainter(
      {required this.geometry, required this.shots, this.activeIndex});

  @override
  void paint(Canvas canvas, Size size) {
    Offset px(Offset o) => Offset(o.dx * size.width, o.dy * size.height);

    // Rough background.
    canvas.drawRect(
      Offset.zero & size,
      Paint()..color = const Color(0xFF1B4D2E),
    );

    // Fairway: tapered polygon along the hole path.
    final fairwayPath = Path()
      ..moveTo(
          geometry.fairwayPolygon.first.dx * size.width,
          geometry.fairwayPolygon.first.dy * size.height);
    for (final p in geometry.fairwayPolygon.skip(1)) {
      fairwayPath.lineTo(p.dx * size.width, p.dy * size.height);
    }
    fairwayPath.close();
    canvas.drawPath(fairwayPath, Paint()..color = const Color(0xFF2E7D43));

    // Bunkers (drawn before the green so the green wins any overlap,
    // matching lieAt's green-first priority).
    final sand = Paint()..color = const Color(0xFFE8D8A0);
    for (final b in geometry.bunkers) {
      canvas.drawOval(
        Rect.fromCenter(
          center: px(Offset(b.cx, b.cy)),
          width: b.rx * 2 * size.width,
          height: b.ry * 2 * size.height,
        ),
        sand,
      );
    }

    // Green + pin.
    final g = geometry.green;
    canvas.drawOval(
      Rect.fromCenter(
        center: px(Offset(g.cx, g.cy)),
        width: g.rx * 2 * size.width,
        height: g.ry * 2 * size.height,
      ),
      Paint()..color = const Color(0xFF3FA34D),
    );
    _paintPin(canvas, size, px(geometry.pin));

    // Tee box marker.
    _paintTeeBox(canvas, size, px(geometry.tee));

    _paintShots(canvas, size, px(geometry.tee), shots, activeIndex);
  }

  @override
  bool shouldRepaint(covariant HoleMapPainter oldDelegate) =>
      oldDelegate.geometry != geometry ||
      oldDelegate.shots != shots ||
      oldDelegate.activeIndex != activeIndex;
}

/// Pin marker: white stick with a red cup dot. [pinPx] is already in
/// pixel coordinates. Shared by the procedural and image painters.
void _paintPin(Canvas canvas, Size size, Offset pinPx) {
  canvas.drawLine(
    pinPx + Offset(0, -size.height * 0.035),
    pinPx,
    Paint()
      ..color = Colors.white
      ..strokeWidth = max(1.5, size.width * 0.004),
  );
  canvas.drawCircle(pinPx, max(2.5, size.width * 0.008),
      Paint()..color = Colors.red.shade700);
}

/// Tee-box marker: small rounded white rectangle. [teePx] is in pixel
/// coordinates. Shared by the procedural and image painters.
void _paintTeeBox(Canvas canvas, Size size, Offset teePx) {
  canvas.drawRRect(
    RRect.fromRectAndRadius(
      Rect.fromCenter(
        center: teePx,
        width: size.width * 0.055,
        height: size.height * 0.02,
      ),
      const Radius.circular(4),
    ),
    Paint()..color = Colors.white70,
  );
}

/// Shot trail (tee -> shot 1 -> shot 2 -> ...) plus numbered shot
/// markers. Shared by the procedural and image painters; [teePx] is the
/// trail start in pixel coordinates.
void _paintShots(Canvas canvas, Size size, Offset teePx, List<Shot> shots,
    int? activeIndex,
    {Offset? pin,
    double? yardsPerPixel,
    int? labeledIndex}) {
  if (shots.isEmpty) return;
  final r = max(9.0, size.width * 0.032);

  // Trail: tee -> shot 1 -> shot 2 -> ...
  final trail = Path()..moveTo(teePx.dx, teePx.dy);
  for (final s in shots) {
    trail.lineTo(s.x * size.width, s.y * size.height);
  }
  canvas.drawPath(
    trail,
    Paint()
      ..color = Colors.white.withValues(alpha: 0.75)
      ..strokeWidth = max(1.5, size.width * 0.005)
      ..style = PaintingStyle.stroke,
  );

  for (var i = 0; i < shots.length; i++) {
    final s = shots[i];
    final c = Offset(s.x * size.width, s.y * size.height);
    final active = i == activeIndex;
    final fill = s.holed ? Colors.amber.shade700 : Colors.white;
    if (active) {
      // Highlight ring around the dragged shot.
      canvas.drawCircle(
        c,
        r + 6,
        Paint()
          ..color = Colors.amber.shade600
          ..strokeWidth = 3
          ..style = PaintingStyle.stroke,
      );
    }
    canvas.drawCircle(c, r, Paint()..color = fill);
    canvas.drawCircle(
      c,
      r,
      Paint()
        ..color = Colors.black87
        ..strokeWidth = 1.5
        ..style = PaintingStyle.stroke,
    );
    final tp = TextPainter(
      text: TextSpan(
        text: s.holed ? '✓' : '${i + 1}',
        style: TextStyle(
          color: s.holed ? Colors.white : Colors.black87,
          fontSize: r * 1.05,
          fontWeight: FontWeight.bold,
        ),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    tp.paint(canvas, c - Offset(tp.width / 2, tp.height / 2));
  }

  // Live distance-to-pin readout on the labeled shot (the one being
  // placed or most recently touched). Synced to the shot's position, so
  // dragging the point updates the number in real time — a check that
  // the point sits where the player intends. Hidden when the map scale
  // is unknown.
  if (pin != null &&
      yardsPerPixel != null &&
      labeledIndex != null &&
      labeledIndex >= 0 &&
      labeledIndex < shots.length) {
    final s = shots[labeledIndex];
    final c = Offset(s.x * size.width, s.y * size.height);
    final dx = (s.x - pin.dx) * 600;
    final dy = (s.y - pin.dy) * 800;
    final yards = (sqrt(dx * dx + dy * dy) * yardsPerPixel).round();
    final fontSize = max(9.0, size.width * 0.026);
    final label = TextPainter(
      text: TextSpan(
        text: '${yards}y',
        style: TextStyle(
          color: Colors.white,
          fontSize: fontSize,
          fontWeight: FontWeight.w700,
        ),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    final padX = fontSize * 0.45;
    final padY = fontSize * 0.25;
    final labelCenter = c + Offset(0, -(r + fontSize * 1.1));
    canvas.drawRRect(
      RRect.fromRectAndRadius(
        Rect.fromCenter(
          center: labelCenter,
          width: label.width + padX * 2,
          height: label.height + padY * 2,
        ),
        Radius.circular(fontSize * 0.55),
      ),
      Paint()..color = Colors.amber.shade800.withValues(alpha: 0.92),
    );
    label.paint(
        canvas, labelCenter - Offset(label.width / 2, label.height / 2));
  }
}

/// Reference yardage-to-pin labels (e.g. "150"). Drawn only when the
/// manifest asset carries markers; holes without markers render exactly
/// as before. Visual only — markers never affect taps or lie detection.
void _paintYardages(
    Canvas canvas, Size size, List<YardageMarker> yardages) {
  if (yardages.isEmpty) return;
  final fontSize = max(9.0, size.width * 0.026);
  for (final m in yardages) {
    final c = Offset(m.offset.dx * size.width, m.offset.dy * size.height);
    final tp = TextPainter(
      text: TextSpan(
        text: '${m.yards}',
        style: TextStyle(
          color: Colors.white,
          fontSize: fontSize,
          fontWeight: FontWeight.w600,
        ),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    final padX = fontSize * 0.45;
    final padY = fontSize * 0.25;
    canvas.drawRRect(
      RRect.fromRectAndRadius(
        Rect.fromCenter(
          center: c,
          width: tp.width + padX * 2,
          height: tp.height + padY * 2,
        ),
        Radius.circular(fontSize * 0.55),
      ),
      Paint()..color = Colors.black.withValues(alpha: 0.62),
    );
    tp.paint(canvas, c - Offset(tp.width / 2, tp.height / 2));
  }
}

/// Paints a realistic rendered hole image plus numbered shot markers
/// with a connecting trail from the tee. The image is drawn full-bleed
/// in the 0..1 coordinate space, identical to the procedural path; pin
/// and tee markers are drawn at runtime from the manifest's normalized
/// tee/pin (crisp at any size), never baked into the image.
class HoleMapImagePainter extends CustomPainter {
  final HoleMapImage image;
  final List<Shot> shots;

  /// Index of the shot currently being dragged, if any. It is drawn with
  /// a highlight ring so the player can see it while their finger is on
  /// the map.
  final int? activeIndex;

  /// Index of the shot carrying the live distance-to-pin readout.
  final int? labeledIndex;

  HoleMapImagePainter(
      {required this.image,
      required this.shots,
      this.activeIndex,
      this.labeledIndex});

  @override
  void paint(Canvas canvas, Size size) {
    Offset px(Offset o) => Offset(o.dx * size.width, o.dy * size.height);

    // Rendered map, full-bleed across the 3:4 viewport.
    final src = Rect.fromLTWH(
        0, 0, image.map.width.toDouble(), image.map.height.toDouble());
    canvas.drawImageRect(image.map, src, Offset.zero & size, Paint());

    // Pin + tee markers, drawn at runtime from the manifest positions.
    _paintPin(canvas, size, px(image.pin));
    _paintTeeBox(canvas, size, px(image.tee));

    // Reference yardages to the pin (only when the asset has markers).
    _paintYardages(canvas, size, image.yardages);

    _paintShots(canvas, size, px(image.tee), shots, activeIndex,
        pin: image.pin,
        yardsPerPixel: image.yardsPerPixel,
        labeledIndex: labeledIndex);
  }

  @override
  bool shouldRepaint(covariant HoleMapImagePainter oldDelegate) =>
      oldDelegate.image != image ||
      oldDelegate.shots != shots ||
      oldDelegate.activeIndex != activeIndex ||
      oldDelegate.labeledIndex != labeledIndex;
}

/// Hole map: either the procedural schematic ([HoleMap]) or a realistic
/// rendered image ([HoleMap.imaged]). The parent must constrain the size
/// (AspectRatio works well); map taps are handled by the caller. Both
/// modes share the 0..1 coordinate space, so shot storage is identical.
class HoleMap extends StatelessWidget {
  final HoleMapGeometry? geometry;
  final HoleMapImage? image;
  final List<Shot> shots;

  /// Index of the shot being dragged, drawn with a highlight ring.
  final int? activeIndex;

  /// Index of the shot carrying the live distance-to-pin readout (the
  /// shot being placed or most recently touched). Null hides the readout.
  /// Only applies in imaged mode when the asset has a known scale.
  final int? labeledIndex;

  /// Procedural schematic mode. Always available; also the fallback when
  /// no rendered image exists (or its assets fail to load).
  const HoleMap(
      {super.key,
      required this.geometry,
      this.shots = const [],
      this.activeIndex})
      : image = null,
        labeledIndex = null;

  /// Realistic rendered-image mode. Tee/pin markers come from the image
  /// itself (manifest tee/pin, drawn at runtime).
  const HoleMap.imaged(
      {super.key,
      required this.image,
      this.shots = const [],
      this.activeIndex,
      this.labeledIndex})
      : geometry = null;

  @override
  Widget build(BuildContext context) {
    final img = image;
    if (img != null) {
      return CustomPaint(
        painter: HoleMapImagePainter(
            image: img,
            shots: shots,
            activeIndex: activeIndex,
            labeledIndex: labeledIndex),
      );
    }
    return CustomPaint(
      painter: HoleMapPainter(
          geometry: geometry!, shots: shots, activeIndex: activeIndex),
    );
  }
}
