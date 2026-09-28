import 'package:flutter/material.dart';

/// A hole score rendered in standard golf scorecard notation:
///
/// - double circle: eagle or better (≤ par − 2)
/// - circle: birdie (par − 1)
/// - plain number: par
/// - square: bogey (par + 1)
/// - double square: double bogey or worse (≥ par + 2)
///
/// Under-par shapes are tinted green, over-par red, matching the app's
/// score-entry buttons. Without a known par it renders a plain number.
class ScoreBadge extends StatelessWidget {
  final int score;
  final int? par;
  final double size;
  final TextStyle? textStyle;

  const ScoreBadge({
    super.key,
    required this.score,
    required this.par,
    this.size = 30,
    this.textStyle,
  });

  @override
  Widget build(BuildContext context) {
    final label = Text(
      '$score',
      style: textStyle ??
          TextStyle(
            fontSize: size * 0.44,
            fontWeight: FontWeight.bold,
            height: 1.0,
          ),
    );
    if (par == null) return label;
    final delta = score - par!;
    if (delta == 0) return label;
    final color = delta < 0 ? Colors.green.shade700 : Colors.red.shade700;
    final border = BorderSide(color: color, width: 1.8);
    if (delta <= -2) return _ring(label, border, circle: true, isDouble: true);
    if (delta == -1) return _ring(label, border, circle: true, isDouble: false);
    if (delta == 1) return _ring(label, border, circle: false, isDouble: false);
    return _ring(label, border, circle: false, isDouble: true);
  }

  Widget _ring(Widget label, BorderSide border,
      {required bool circle, required bool isDouble}) {
    BoxDecoration deco() => BoxDecoration(
          shape: circle ? BoxShape.circle : BoxShape.rectangle,
          border: Border.fromBorderSide(border),
          borderRadius: circle ? null : BorderRadius.circular(5),
        );
    final inner = Container(
      width: size,
      height: size,
      decoration: deco(),
      alignment: Alignment.center,
      child: label,
    );
    if (!isDouble) return inner;
    // Double ring: outer shape with a small gap around the inner one.
    return Container(
      decoration: deco(),
      padding: const EdgeInsets.all(2.5),
      child: inner,
    );
  }
}
