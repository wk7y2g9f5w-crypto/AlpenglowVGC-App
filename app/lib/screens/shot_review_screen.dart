import 'package:flutter/material.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../widgets/hole_map.dart';

/// Full-screen, read-only review of a scorecard's tracked shots.
///
/// Loads every hole's shots fresh from the server (source of truth), so
/// what the player reviews is exactly what is stored. Holes with no
/// tracked shots are skipped; the header shows how many holes were
/// tracked. Editing stays in the per-hole shot tracker — this screen is
/// for reviewing before scorecard submission.
Future<void> showShotReview({
  required BuildContext context,
  required ApiClient api,
  required int cardId,
  required String courseName,
  required List<int> pars,
  List<int?>? strokes,
}) {
  return Navigator.of(context).push(
    MaterialPageRoute(
      builder: (_) => _ShotReviewScreen(
        api: api,
        cardId: cardId,
        courseName: courseName,
        pars: pars,
        strokes: strokes,
      ),
    ),
  );
}

class _ShotReviewScreen extends StatefulWidget {
  final ApiClient api;
  final int cardId;
  final String courseName;
  final List<int> pars;
  final List<int?>? strokes;

  const _ShotReviewScreen({
    required this.api,
    required this.cardId,
    required this.courseName,
    required this.pars,
    this.strokes,
  });

  @override
  State<_ShotReviewScreen> createState() => _ShotReviewScreenState();
}

class _ShotReviewScreenState extends State<_ShotReviewScreen> {
  bool _loading = true;
  String? _loadError;
  Map<int, List<Shot>> _byHole = {};
  List<int> _holes = [];
  late final PageController _pages;
  int _page = 0;

  @override
  void initState() {
    super.initState();
    _pages = PageController();
    _load();
  }

  @override
  void dispose() {
    _pages.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _loadError = null;
    });
    try {
      final holes = List.generate(widget.pars.length, (i) => i + 1);
      final results = await Future.wait(
        holes.map((h) => widget.api.getHoleShots(widget.cardId, h)),
      );
      final byHole = <int, List<Shot>>{};
      for (var i = 0; i < holes.length; i++) {
        if (results[i].isNotEmpty) byHole[holes[i]] = results[i];
      }
      if (mounted) {
        setState(() {
          _byHole = byHole;
          _holes = byHole.keys.toList()..sort();
          _page = 0;
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

  void _goTo(int index) {
    _pages.animateToPage(
      index,
      duration: const Duration(milliseconds: 250),
      curve: Curves.easeInOut,
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Review tracked shots')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _loadError != null
              ? _errorBody()
              : _holes.isEmpty
                  ? _emptyBody()
                  : _reviewBody(),
    );
  }

  Widget _errorBody() {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(_loadError!,
                textAlign: TextAlign.center,
                style: const TextStyle(color: Colors.grey)),
            const SizedBox(height: 12),
            ElevatedButton(onPressed: _load, child: const Text('Retry')),
          ],
        ),
      ),
    );
  }

  Widget _emptyBody() {
    return const Center(
      child: Padding(
        padding: EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.timeline, size: 64, color: Colors.grey),
            SizedBox(height: 16),
            Text('No shots tracked yet.',
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
            SizedBox(height: 8),
            Text(
              'Use \u2018Track shots\u2019 on any hole to start mapping your round.',
              textAlign: TextAlign.center,
              style: TextStyle(color: Colors.grey),
            ),
          ],
        ),
      ),
    );
  }

  Widget _reviewBody() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
          child: Text(
            '${_holes.length} of ${widget.pars.length} holes tracked',
            style: const TextStyle(color: Colors.grey, fontSize: 13),
          ),
        ),
        SizedBox(
          height: 44,
          child: ListView.separated(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: 16),
            itemCount: _holes.length,
            separatorBuilder: (_, _) => const SizedBox(width: 8),
            itemBuilder: (ctx, i) {
              final hole = _holes[i];
              return ChoiceChip(
                label: Text('Hole $hole'),
                selected: i == _page,
                onSelected: (_) => _goTo(i),
              );
            },
          ),
        ),
        const SizedBox(height: 8),
        Expanded(
          child: PageView.builder(
            controller: _pages,
            itemCount: _holes.length,
            onPageChanged: (i) => setState(() => _page = i),
            itemBuilder: (ctx, i) => _holePage(_holes[i]),
          ),
        ),
      ],
    );
  }

  Widget _holePage(int hole) {
    final shots = _byHole[hole]!;
    final par = widget.pars[hole - 1];
    final geometry = HoleMapGeometry(
      courseName: widget.courseName,
      holeNumber: hole,
      par: par,
    );
    final strokes = (widget.strokes != null && hole - 1 < widget.strokes!.length)
        ? widget.strokes![hole - 1]
        : null;
    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  'Hole $hole \u00b7 Par $par',
                  style: const TextStyle(
                      fontSize: 20, fontWeight: FontWeight.bold),
                ),
              ),
              Text(
                '${shots.length} shot${shots.length == 1 ? '' : 's'}',
                style: const TextStyle(color: Colors.grey),
              ),
            ],
          ),
          if (strokes != null && strokes > 0 && strokes != shots.length)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(
                '\u26a0 ${shots.length} tracked, $strokes stroke${strokes == 1 ? '' : 's'} entered',
                style: const TextStyle(color: Colors.orange, fontSize: 13),
              ),
            ),
          const SizedBox(height: 8),
          AspectRatio(
            aspectRatio: 3 / 4,
            child: ClipRRect(
              borderRadius: BorderRadius.circular(12),
              child: HoleMap(geometry: geometry, shots: shots),
            ),
          ),
          const SizedBox(height: 12),
          ...shots.asMap().entries.map((e) {
            final i = e.key;
            final s = e.value;
            return Padding(
              padding: const EdgeInsets.symmetric(vertical: 3),
              child: Row(
                children: [
                  Container(
                    width: 26,
                    height: 26,
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: s.holed
                          ? Colors.amber.shade700
                          : Colors.grey.shade200,
                    ),
                    alignment: Alignment.center,
                    child: Text(
                      s.holed ? '\u2713' : '${i + 1}',
                      style: TextStyle(
                        fontWeight: FontWeight.bold,
                        fontSize: 13,
                        color: s.holed ? Colors.white : Colors.black87,
                      ),
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      'Shot ${i + 1} \u2014 ${s.lie}${s.holed ? ' \u00b7 holed' : ''}',
                      style: const TextStyle(fontSize: 14),
                    ),
                  ),
                ],
              ),
            );
          }),
        ],
      ),
    );
  }
}
