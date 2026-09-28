import 'package:flutter/material.dart';

/// AI-generated course artwork bundled with the app (assets/courses/).
///
/// Each of the 18 Golf+ courses has a painterly background image, keyed by
/// slug. Unknown or missing course names return null so callers can fall
/// back to a plain gradient.
const _courseArt = {
  'valhalla-golf-club': 'Valhalla Golf Club',
  'wolf-creek-golf-club': 'Wolf Creek Golf Club',
  'old-course-st-andrews': 'The Old Course at St Andrews',
  'pebble-beach-golf-links': 'Pebble Beach Golf Links',
  'pinehurst-no-2': 'Pinehurst No. 2',
  'ocean-course-kiawah-island': 'The Ocean Course at Kiawah Island',
  'tpc-sawgrass': 'TPC Sawgrass',
  'tpc-scottsdale': 'TPC Scottsdale',
  'tpc-southwind': 'TPC Southwind',
  'riviera-country-club': 'The Riviera Country Club',
  'kapalua-plantation-course': 'Kapalua Plantation Course',
  'east-lake-golf-club': 'East Lake Golf Club',
  'olympia-fields-country-club': 'Olympia Fields Country Club',
  'yale-golf-course': 'Yale Golf Course',
  'harbour-town-golf-links': 'Harbour Town Golf Links',
  'bay-hill-club-lodge': 'Bay Hill Club & Lodge',
  'castle-pines-golf-club': 'Castle Pines Golf Club',
  'lofoten-links': 'Lofoten Links',
};

/// Asset path for a course's background art, or null when the course has
/// no bundled artwork (e.g. a free-text course name).
String? courseArtAsset(String? courseName) {
  if (courseName == null || courseName.trim().isEmpty) return null;
  final needle = courseName.trim().toLowerCase();
  for (final e in _courseArt.entries) {
    if (e.value.toLowerCase() == needle) {
      return 'assets/courses/${e.key}.jpg';
    }
  }
  return null;
}

/// Dark overlay applied over course art so white text stays readable.
const courseArtOverlay = LinearGradient(
  begin: Alignment.topCenter,
  end: Alignment.bottomCenter,
  colors: [
    Color.fromRGBO(0, 0, 0, 0.25),
    Color.fromRGBO(0, 0, 0, 0.72),
  ],
);

/// Fallback gradient for courses with no bundled artwork.
const courseArtFallback = LinearGradient(
  begin: Alignment.topLeft,
  end: Alignment.bottomRight,
  colors: [Color(0xFF1B5E20), Color(0xFF0D3311)],
);

/// Background decoration for a tee-time card/header: the course's artwork
/// with a dark overlay, or the dark-green fallback gradient.
BoxDecoration courseArtDecoration(String? courseName) {
  final art = courseArtAsset(courseName);
  return BoxDecoration(
    image: art != null
        ? DecorationImage(
            image: AssetImage(art),
            fit: BoxFit.cover,
            alignment: Alignment.center,
          )
        : null,
    gradient: art == null ? courseArtFallback : null,
  );
}

/// Header block for a tee-time detail screen: course artwork (or the
/// fallback gradient) with a dark overlay and white [children].
class CourseArtHeader extends StatelessWidget {
  final String? course;
  final List<Widget> children;

  const CourseArtHeader(
      {super.key, required this.course, required this.children});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      decoration: courseArtDecoration(course),
      child: Container(
        decoration: const BoxDecoration(gradient: courseArtOverlay),
        padding: const EdgeInsets.fromLTRB(16, 20, 16, 16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: children,
        ),
      ),
    );
  }
}

/// A tee-time list card with the course's artwork as its background
/// (dark-green fallback gradient when the course has no bundled art).
class CourseTeeTimeCard extends StatelessWidget {
  final String? course;
  final String title;
  final String line1;
  final String? line2;
  final Widget? trailing;
  final VoidCallback onTap;

  const CourseTeeTimeCard({
    super.key,
    required this.course,
    required this.title,
    required this.line1,
    this.line2,
    this.trailing,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: Container(
          decoration: courseArtDecoration(course),
          child: Container(
            decoration: const BoxDecoration(gradient: courseArtOverlay),
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
            child: ListTile(
              textColor: Colors.white,
              iconColor: Colors.white,
              title: Text(title,
                  style: const TextStyle(fontWeight: FontWeight.bold)),
              subtitle: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const SizedBox(height: 4),
                  Text(line1,
                      style: const TextStyle(color: Colors.white70)),
                  if (line2 != null) ...[
                    const SizedBox(height: 2),
                    Text(line2!,
                        style: const TextStyle(
                            fontSize: 12, color: Colors.white70)),
                  ],
                ],
              ),
              trailing: trailing ?? const Icon(Icons.chevron_right),
            ),
          ),
        ),
      ),
    );
  }
}
