#!/usr/bin/env python3
"""Targeted data fix for Payne's Valley holes 5, 10, 16.

OSM assigns each of these holes one huge lake polygon (103-242 m
across, centroid 34-82 m from the green — the lake the course wraps
around). Rendered, the lake dominates the frame and shrinks the hole
to a sliver. Cayden's Golf+ GPS screenshots (2026-10-17, the ground
truth these maps are QA'd against) show NO water on holes 5, 10 or
16 — the game's own corridor view is water-free there.

Fix: drop the water feature from those three holes in holes.json,
then re-render the course with render.py (manifest content is
unchanged: same holes, same pars).

Usage:
    python3 paynes_valley_fix.py   # edits data/paynes-valley/holes.json
    python3 render.py --courses paynes-valley
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
HOLES_PATH = os.path.join(HERE, "data", "paynes-valley", "holes.json")
DROP_WATER_HOLES = {5, 10, 16}


def main():
    with open(HOLES_PATH) as f:
        d = json.load(f)
    changed = []
    for h in d["holes"]:
        if h["number"] in DROP_WATER_HOLES and h["features"].get("water"):
            h["features"]["water"] = []
            changed.append(h["number"])
    with open(HOLES_PATH, "w") as f:
        json.dump(d, f, indent=1)
    print("dropped water from holes:", changed or "none (already fixed)")


if __name__ == "__main__":
    main()
