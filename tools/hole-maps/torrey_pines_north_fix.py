#!/usr/bin/env python3
"""Targeted data fixes for Torrey Pines North, from screenshot QA.

Cayden's Golf+ GPS screenshots (2026-10-28) are the ground truth these
maps are QA'd against. Three defect classes, all verified per hole
against the screenshots:

1. Wash/beach sand polygons tagged as bunkers. OSM maps the canyon
   washes and sand areas around Torrey Pines as huge "bunker" polygons
   (500-6,600 m2, up to ~300 yd across). The game's corridor view shows
   plain rough at every one of these spots. Dropped below by area /
   position signature (asserted, so a data change fails loudly):
     H2:  three wash polygons left of the lower fairway (740-1,528 m2)
     H3:  one 3,698 m2 sand mass below the tee
     H4:  one 6,618 m2 sand wall left of the fairway
     H8:  three off-line bunkers (two at the tee end, one far right of
          the green); the two greenside bunkers stay
     H12: one small bunker at the tee end (rough in the game view)
     H13: one 1,806 m2 sand blob left of the green
     H14: two wash polygons at the tee end (523 + 2,616 m2)
     H18: one 2,234 m2 sand mass at the tee end + one 314 m2 bunker
          beside the lower fairway (rough in the game view)

2. Hole 9 is REMOVED from holes.json. OSM's only ref=9 way inside the
   North boundary traces a 406 yd corridor, but the hole is a 556 yd
   par 5 (Golf+ header + the Farmers Insurance Open North scorecard,
   which agree on all 18 holes). The trace ends at the right green
   with a matching bunker pattern, but ~150 yd of the hole is missing
   and cannot be recovered from OSM data. Per the README, a hole with
   no valid centerline gets no image — the app falls back to its
   procedural schematic. Also remove hole 9's line from the committed
   Dart manifest by hand (the manifest writer never deletes) and
   delete app/assets/hole_maps/torrey-pines-north/9.jpg + 9_mask.png.

Tee polygons are NOT touched: Torrey's holes are packed tightly, so
neighbouring holes' tee complexes land within the extractor's 80 m
tee rule on most holes — the same bleed the committed Medinah and
Payne's Valley data carries.

Usage:
    python3 torrey_pines_north_fix.py   # edits data/torrey-pines-north/holes.json
    python3 render.py --courses torrey-pines-north
"""
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
HOLES_PATH = os.path.join(HERE, "data", "torrey-pines-north", "holes.json")

R_EARTH = 6371000.0


def to_xy(lon, lat, lon0, lat0):
    return (math.radians(lon - lon0) * R_EARTH * math.cos(math.radians(lat0)),
            math.radians(lat - lat0) * R_EARTH)


def ring_area_m(rm):
    a = 0.0
    for i in range(len(rm)):
        x1, y1 = rm[i]
        x2, y2 = rm[(i + 1) % len(rm)]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def hole_frame(hole):
    """Return (project, L_yd): project(lonlat) -> (along_yd, lateral_yd)."""
    cl = hole["centerline"]
    lon0 = sum(p[0] for p in cl) / len(cl)
    lat0 = sum(p[1] for p in cl) / len(cl)
    cm = [to_xy(p[0], p[1], lon0, lat0) for p in cl]
    cum = [0.0]
    for a, b in zip(cm, cm[1:]):
        cum.append(cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))

    def project(lonlat):
        pt = to_xy(lonlat[0], lonlat[1], lon0, lat0)
        best = (1e18, 0.0)
        for i in range(len(cm) - 1):
            ax, ay = cm[i]
            bx, by = cm[i + 1]
            dx, dy = bx - ax, by - ay
            l2 = dx * dx + dy * dy or 1e-9
            t = max(0.0, min(1.0, ((pt[0] - ax) * dx + (pt[1] - ay) * dy) / l2))
            dd = math.hypot(pt[0] - (ax + t * dx), pt[1] - (ay + t * dy))
            if dd < best[0]:
                best = (dd, cum[i] + t * (cum[i + 1] - cum[i]))
        return best[1] * 1.09361, best[0] * 1.09361

    return project, cum[-1] * 1.09361, lon0, lat0


def feature_stats(hole, kind, ring):
    project, _, lon0, lat0 = hole_frame(hole)
    rm = [to_xy(p[0], p[1], lon0, lat0) for p in ring]
    cx = sum(p[0] for p in rm) / len(rm)
    cy = sum(p[1] for p in rm) / len(rm)
    clon = lon0 + math.degrees(cx / (R_EARTH * math.cos(math.radians(lat0))))
    clat = lat0 + math.degrees(cy / R_EARTH)
    along, lat = project((clon, clat))
    return {"area": ring_area_m(rm), "along": along, "lat": lat}


# (hole, kind, predicate(stats) -> drop?, expected_count, why)
DROPS = [
    (2, "bunker", lambda s: s["area"] > 700, 3, "canyon wash polygons"),
    (3, "bunker", lambda s: s["area"] > 1000, 1, "sand mass below tee"),
    (4, "bunker", lambda s: s["area"] > 1000, 1, "sand wall left of fairway"),
    (8, "bunker", lambda s: s["along"] < 60 or s["lat"] > 40, 3,
     "tee-end / far-right off-line bunkers"),
    (12, "bunker", lambda s: s["along"] < 10, 1, "bunker at tee end"),
    (13, "bunker", lambda s: s["area"] > 1000, 1, "sand blob left of green"),
    (14, "bunker", lambda s: s["area"] > 400, 2, "wash polygons at tee end"),
    (18, "bunker", lambda s: s["area"] > 1000, 1, "sand mass at tee end"),
    (18, "bunker", lambda s: 60 < s["along"] < 85 and 250 < s["area"] < 400,
     1, "bunker beside lower fairway (rough in game view)"),
]

REMOVE_HOLES = {9}


def main():
    with open(HOLES_PATH) as f:
        d = json.load(f)
    by_num = {h["number"]: h for h in d["holes"]}
    for n, kind, pred, expected, why in DROPS:
        hole = by_num[n]
        rings = hole["features"].get(kind, [])
        kept, dropped = [], 0
        for ring in rings:
            s = feature_stats(hole, kind, ring)
            if pred(s):
                dropped += 1
                print("H%d drop %s (area=%.0f m2, along=%.0f yd, lat=%.0f yd): %s"
                      % (n, kind, s["area"], s["along"], s["lat"], why))
            else:
                kept.append(ring)
        assert dropped == expected, \
            "H%d %s: expected %d drops (%s), got %d — OSM data changed?" \
            % (n, kind, expected, why, dropped)
        hole["features"][kind] = kept
    before = len(d["holes"])
    d["holes"] = [h for h in d["holes"] if h["number"] not in REMOVE_HOLES]
    removed = before - len(d["holes"])
    assert removed == len(REMOVE_HOLES), "hole removal mismatch: %d" % removed
    print("removed hole records:", sorted(REMOVE_HOLES),
          "(OSM trace invalid vs verified yardage — see docstring)")
    with open(HOLES_PATH, "w") as f:
        json.dump(d, f, indent=1)
    print("holes.json updated:", len(d["holes"]), "holes")


if __name__ == "__main__":
    main()
