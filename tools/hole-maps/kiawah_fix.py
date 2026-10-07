#!/usr/bin/env python3
"""Targeted re-render for Ocean Course Kiawah Island fixes.

Loads holes.json, applies per-hole feature edits in lon/lat space,
re-renders ONLY the specified holes (map + mask) without touching
the manifest. Reuses render.py's HoleTransform/render_map/render_mask.

Usage:
    python3 kiawah_fix.py  # applies edits defined in main() and re-renders
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render import HoleTransform, render_map, render_mask, to_xy, R_EARTH

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "ocean-course-kiawah-island")
REPO = os.path.dirname(os.path.dirname(HERE))
ASSETS = os.path.join(REPO, "app", "assets", "hole_maps", "ocean-course-kiawah-island")

HOLES_PATH = os.path.join(DATA, "holes.json")
MAP_W, MAP_H = 600, 800


def load():
    with open(HOLES_PATH) as f:
        return json.load(f)


def save(d):
    with open(HOLES_PATH, "w") as f:
        json.dump(d, f, indent=1)


def get_hole(d, n):
    for h in d["holes"]:
        if h["number"] == n:
            return h
    raise ValueError("hole %d not in holes.json" % n)


def lonlat_to_map_px(hole, lon, lat):
    """lon/lat -> map pixel (600x800) using the hole's current transform."""
    t = HoleTransform(hole)
    x, y = to_xy(lon, lat, t.lon0, t.lat0)
    rx, ry = t.rot((x, y))
    return t.px((rx, ry), MAP_W, MAP_H)


def map_px_to_lonlat(hole, px, py):
    """Inverse: map pixel (600x800) -> lon/lat using the hole's current transform."""
    t = HoleTransform(hole)
    # norm: ix = (rx - minx)*scale + ox ; iy = (maxy - ry)*scale + oy
    # px = nx*W, py = ny*H ; nx = ix/W ; ny = iy/H
    ix = px / MAP_W * MAP_W
    iy = py / MAP_H * MAP_H
    rx = (ix - t.ox) / t.scale + t.minx
    ry = t.maxy - (iy - t.oy) / t.scale
    # inverse rotation: rot was (x*c - y*s, x*s + y*c); inverse is (x*c + y*s, -x*s + y*c)
    c, s = math.cos(t.phi), math.sin(t.phi)
    x = rx * c + ry * s
    y = -rx * s + ry * c
    lon = t.lon0 + math.degrees(x / (R_EARTH * math.cos(math.radians(t.lat0))))
    lat = t.lat0 + math.degrees(y / R_EARTH)
    return lon, lat


def screenshot_to_map_px(ss_x, ss_y, ss_tee, ss_pin, map_tee_px, map_pin_px):
    """Similarity transform: screenshot px -> map px via tee/pin correspondences.

    ss_tee, ss_pin: (x, y) in screenshot pixels.
    map_tee_px, map_pin_px: (x, y) in map pixels (600x800).
    Returns (mx, my).
    """
    # Vector in screenshot
    svx, svy = ss_pin[0] - ss_tee[0], ss_pin[1] - ss_tee[1]
    # Vector in map
    mvx, mvy = map_pin_px[0] - map_tee_px[0], map_pin_px[1] - map_tee_px[1]
    ss_len = math.hypot(svx, svy)
    map_len = math.hypot(mvx, mvy)
    scale = map_len / ss_len
    # Rotation from screenshot vector to map vector
    ang_ss = math.atan2(svy, svx)
    ang_map = math.atan2(mvy, mvx)
    rot = ang_map - ang_ss
    # Translate ss_tee to map_tee_px, rotate+scale offset
    ox, oy = ss_x - ss_tee[0], ss_y - ss_tee[1]
    rx = ox * math.cos(rot) - oy * math.sin(rot)
    ry = ox * math.sin(rot) + oy * math.cos(rot)
    return map_tee_px[0] + rx * scale, map_tee_px[1] + ry * scale


def render_hole(d, n):
    hole = get_hole(d, n)
    t = HoleTransform(hole)
    m = render_map(hole, t)
    m.save(os.path.join(ASSETS, "%d.jpg" % n), quality=82)
    mk = render_mask(hole, t)
    mk.save(os.path.join(ASSETS, "%d_mask.png" % n))
    print("re-rendered hole %d" % n)


def ellipse_poly(cx, cy, rx, ry, n=24):
    return [[cx + rx * math.cos(2 * math.pi * i / n),
             cy + ry * math.sin(2 * math.pi * i / n)] for i in range(n)]
