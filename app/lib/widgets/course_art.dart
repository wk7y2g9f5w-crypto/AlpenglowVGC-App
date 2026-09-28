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
