#!/usr/bin/env python3
"""Strip hole-map assets from the Flutter web service-worker's precache list.

Older `flutter build web` output (offline-first PWA strategy) precached every
entry in the generated flutter_service_worker.js RESOURCES manifest on first
load. The ~6.3MB of hole-map images would all download up front; removing
them from RESOURCES means they are fetched on demand when a hole map is
actually viewed (the SW's fetch handler only manages RESOURCES entries,
everything else goes straight to network).

Flutter 3.45+ changed the default: flutter_service_worker.js is now a tiny
self-unregistering worker with NO RESOURCES precache at all, so there is
nothing to strip and hole maps already load on demand. This script detects
that case and exits quietly (0). If a RESOURCES map with hole-map entries
shows up (older SDK or a future format change), they are removed.

Usage: strip_sw_precache.py <path-to-build-web-dir>
"""
import re
import sys
from pathlib import Path


def main() -> int:
    web_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if web_dir is None or not web_dir.is_dir():
        print("usage: strip_sw_precache.py <build-web-dir>", file=sys.stderr)
        return 2
    sw = web_dir / "flutter_service_worker.js"
    if not sw.is_file():
        print(f"note: {sw} not found - nothing to strip")
        return 0

    text = sw.read_text(encoding="utf-8")
    # Remove whole lines like:   "assets/hole_maps/yale-golf-course/1.jpg": "abc123",
    pattern = re.compile(r'^[ \t]*"assets/hole_maps/[^"]*":[ \t]*"[^"]*",?[ \t]*\r?\n', re.M)
    stripped, n = pattern.subn("", text)
    if n == 0:
        if "RESOURCES" not in text:
            # Flutter 3.45+ self-unregistering SW: no precache exists.
            print("note: no RESOURCES precache in service worker "
                  "(self-unregistering SW) - nothing to strip")
            return 0
        print(
            "WARNING: RESOURCES precache present but no assets/hole_maps "
            "entries found in flutter_service_worker.js - precache NOT "
            "tuned. The SW format may have changed; investigate.",
            file=sys.stderr,
        )
        return 0
    sw.write_text(stripped, encoding="utf-8")
    print(f"stripped {n} hole-map entries from service worker precache")
    return 0


if __name__ == "__main__":
    sys.exit(main())
