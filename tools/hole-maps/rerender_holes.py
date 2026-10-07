#!/usr/bin/env python3
"""Targeted re-render of individual holes without full-manifest rewrite.

Usage: python3 rerender_holes.py <slug> <hole_numbers...>
Renders map + mask for the given holes using tools/hole-maps/render.py
functions, writing to app/assets/hole_maps/<slug>/.
Does NOT touch the Dart manifest or other courses.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render import HoleTransform, render_map, render_mask

TOOLS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(TOOLS))  # tools/hole-maps/ -> golf-companion-app/
DATA = os.path.join(TOOLS, "data")
ASSETS = os.path.join(REPO, "app", "assets", "hole_maps")


def main():
    slug = sys.argv[1]
    numbers = [int(x) for x in sys.argv[2:]]
    holes_path = os.path.join(DATA, slug, "holes.json")
    with open(holes_path) as f:
        data = json.load(f)
    out_dir = os.path.join(ASSETS, slug)
    os.makedirs(out_dir, exist_ok=True)
    for hole in data["holes"]:
        n = hole["number"]
        if n not in numbers:
            continue
        t = HoleTransform(hole)
        # Orientation invariant: tee (centerline[0]) must be below green end.
        tee_n = t.norm(t.center_r[0])
        green_n = t.norm(t.center_r[-1])
        assert tee_n[1] > green_n[1], \
            "hole %d orientation invariant violated" % n
        map_path = os.path.join(out_dir, "%d.jpg" % n)
        mask_path = os.path.join(out_dir, "%d_mask.png" % n)
        render_map(hole, t).save(map_path, "JPEG", quality=82)
        render_mask(hole, t).save(mask_path, "PNG")
        print("rendered %s hole %d" % (slug, n))


if __name__ == "__main__":
    main()
