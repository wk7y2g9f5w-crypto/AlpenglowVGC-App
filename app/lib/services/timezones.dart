/// A short list of common IANA timezones for the picker, plus a
/// client-side validator for free-text entry.
const List<String> commonTimezones = [
  'America/Denver',
  'America/Phoenix',
  'America/Chicago',
  'America/New_York',
  'America/Los_Angeles',
  'America/Anchorage',
  'Pacific/Honolulu',
  'America/Toronto',
  'America/Vancouver',
  'America/Mexico_City',
  'America/Sao_Paulo',
  'Europe/London',
  'Europe/Paris',
  'Europe/Berlin',
  'Europe/Amsterdam',
  'Europe/Madrid',
  'Europe/Rome',
  'Europe/Stockholm',
  'Europe/Athens',
  'Europe/Moscow',
  'Africa/Cairo',
  'Asia/Dubai',
  'Asia/Karachi',
  'Asia/Kolkata',
  'Asia/Bangkok',
  'Asia/Singapore',
  'Asia/Tokyo',
  'Asia/Seoul',
  'Asia/Shanghai',
  'Australia/Sydney',
  'Australia/Melbourne',
  'Pacific/Auckland',
  'UTC',
];

/// Loose client-side check: must look like `Area/City` (optionally with
/// sub-areas), letters/underscores/hyphens only.
bool isValidTimezone(String v) {
  final t = v.trim();
  if (t == 'UTC') return true;
  final re = RegExp(r'^[A-Za-z][A-Za-z0-9_\-]*(\/[A-Za-z][A-Za-z0-9_\-]+)+$');
  return re.hasMatch(t) && t.length <= 64;
}
