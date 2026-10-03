#!/usr/bin/env python3
"""Phase 1 — extract per-hole geometry from OpenStreetMap.

For each configured course:
  1. Fetch OSM XML via the main API (bbox around the course; polite,
     sequential, 1.5s sleeps; raw XML cached under data/raw/).
  2. Find the course boundary (leisure=golf_course way/relation, name
     fuzz-match). If the boundary extends beyond the first bbox, re-fetch
     with a boundary-fitted bbox.
  3. Parse golf features: golf=hole (centerlines, need ref+par),
     golf=fairway/green/bunker/tee/water_hazard, natural=water
     (ways AND multipolygon relations).
  4. Keep features whose centroid falls inside the course boundary.
  5. Cluster features to holes (nearest centerline in local meters).
  6. Synthesize thin geometry (tapered fairway / green ellipse / seeded
     bunkers / tee boxes) where OSM data is missing, in the same flat
     style as the real data. Holes with NO OSM centerline get no image.
  7. Write data/<slug>/holes.json + qa.txt (both gitignored).

Deterministic (sorted ids, seeded RNG) and resumable: courses with a
complete holes.json are skipped unless --force.

Only rendered images + this code + the generated manifest are committed;
everything under data/ (raw XML, holes.json, qa.txt, contact sheets) is
gitignored derived OSM data.
"""

import argparse
import json
import math
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
RAW = os.path.join(DATA, "raw")

UA = {"User-Agent": "AlpenglowVGC-hole-maps/1.0 (research use; sequential polite fetching)"}
SLEEP = 1.5  # seconds between OSM requests

# --------------------------------------------------------------------------
# Course configuration.
# boundaries: ordered preferred name substrings (case-insensitive contains);
#   the first that matches a leisure=golf_course object wins, but when
#   several candidates match we prefer the one yielding refs closest to 1-18.
# par_overrides: hole_number -> par, ONLY for holes whose OSM way lacks a
#   par tag; each entry must cite the verified source in the comment.
# --------------------------------------------------------------------------
COURSES = [
    dict(slug="yale-golf-course", display="Yale Golf Course",
         lat=41.3194105, lon=-72.9854398, boundaries=["Yale Golf Course"]),
    dict(slug="lofoten-links", display="Lofoten Links",
         lat=68.3404464, lon=14.1341774, boundaries=["Lofoten Golf Links"]),
    dict(slug="bay-hill-club-lodge", display="Bay Hill Club & Lodge",
         lat=28.4639, lon=-81.5097, boundaries=["Bay Hill Golf Club"]),
    dict(slug="tpc-southwind", display="TPC Southwind",
         lat=35.0546, lon=-89.7791, boundaries=["TPC Southwind"],
         tile_fetch=True),  # dense suburbia: single bbox hits node limit
    dict(slug="pinehurst-no-2", display="Pinehurst No. 2",
         lat=35.1916623, lon=-79.4637323, boundaries=["Pinehurst No. 2"]),
    # Stadium Course hosts the famous island-green 17th; fall back to the
    # whole-club boundary only if no Stadium-named boundary exists.
    dict(slug="tpc-sawgrass", display="TPC Sawgrass",
         lat=30.1960044, lon=-81.3937539,
         boundaries=["Stadium Course", "TPC Sawgrass"],
         # Hole ways are tagged 'Stadium N' vs 'Valley N' (Dye's Valley);
         # the Stadium Course hosts the famous island-green 17th.
         hole_name_preferred="stadium"),
    dict(slug="valhalla-golf-club", display="Valhalla Golf Club",
         lat=38.242317, lon=-85.4706879, boundaries=["Valhalla Golf Club"]),
    dict(slug="wolf-creek-golf-club", display="Wolf Creek Golf Club",
         lat=36.8368779, lon=-114.0595391, boundaries=["Wolf Creek Golf Club"],
         # OSM hole ways lack par tags (verified 2026-10-03). Pars from the
         # published scorecard, cross-checked on allsquaregolf.com and
         # greenskeeper.org (both agree, total par 72).
         par_overrides={1: 5, 2: 4, 3: 3, 4: 4, 5: 5, 6: 4, 7: 4, 8: 3, 9: 4,
                        10: 4, 11: 3, 12: 5, 13: 4, 14: 4, 15: 3, 16: 4, 17: 5,
                        18: 4}),
    dict(slug="old-course-st-andrews", display="The Old Course at St Andrews",
         lat=56.35191, lon=-2.8162055, boundaries=["Old Course"]),
    dict(slug="pebble-beach-golf-links", display="Pebble Beach Golf Links",
         lat=36.5689, lon=-121.95, boundaries=["Pebble Beach Golf Course"]),
    dict(slug="ocean-course-kiawah-island", display="The Ocean Course at Kiawah Island",
         lat=32.61, lon=-80.045,
         boundaries=["Kiawah Island Golf Resort - The Ocean Course"]),
    dict(slug="tpc-scottsdale", display="TPC Scottsdale",
         lat=33.6401402, lon=-111.9156552,
         boundaries=["TPC Scottsdale Stadium Course"]),
    dict(slug="riviera-country-club", display="The Riviera Country Club",
         lat=34.0451645, lon=-118.5024932, boundaries=["Riviera Country Club"],
         tile_fetch=True),  # dense LA: single bbox hits node limit
    dict(slug="kapalua-plantation-course", display="Kapalua Plantation Course",
         lat=21.0045967, lon=-156.6349994, boundaries=["The Plantation Course"]),
    dict(slug="east-lake-golf-club", display="East Lake Golf Club",
         lat=33.7436053, lon=-84.3024566, boundaries=["East Lake Golf Club"]),
    # North + South courses share one club boundary and hole ways carry
    # no course tags. The 36 centerlines split cleanly into a northern and a
    # southern cluster of 18; par sums confirm it (north=70, south=72 —
    # the North Course is par 70). Keep the northern cluster.
    dict(slug="olympia-fields-country-club", display="Olympia Fields Country Club",
         lat=41.5181015, lon=-87.6879777,
         boundaries=["Olympia Fields Country Club"],
         keep_northern_hole_cluster=True),
    dict(slug="harbour-town-golf-links", display="Harbour Town Golf Links",
         lat=32.1307456, lon=-80.8107889, boundaries=["Harbour Town Golf Links"]),
    # Adjacent 'The Country Club at Castle Pines' is a different club;
    # exact-name preference keeps us on the right boundary.
    dict(slug="castle-pines-golf-club", display="Castle Pines Golf Club",
         lat=39.4366699, lon=-104.9003579, boundaries=["Castle Pines Golf Club"]),
]

# --------------------------------------------------------------------------
# Geo helpers (local equirectangular meters).
# --------------------------------------------------------------------------
R_EARTH = 6371000.0


def to_xy(lon, lat, lon0, lat0):
    x = math.radians(lon - lon0) * R_EARTH * math.cos(math.radians(lat0))
    y = math.radians(lat - lat0) * R_EARTH
    return x, y


def to_lonlat(x, y, lon0, lat0):
    lon = lon0 + math.degrees(x / (R_EARTH * math.cos(math.radians(lat0))))
    lat = lat0 + math.degrees(y / R_EARTH)
    return lon, lat


def ring_area(r):
    a = 0.0
    n = len(r)
    for i in range(n):
        x1, y1 = r[i]
        x2, y2 = r[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def poly_centroid(r):
    """Area-weighted centroid; falls back to mean for degenerate rings."""
    a2 = 0.0
    cx = cy = 0.0
    n = len(r)
    for i in range(n):
        x1, y1 = r[i]
        x2, y2 = r[(i + 1) % n]
        cross = x1 * y2 - x2 * y1
        a2 += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(a2) < 1e-9:
        sx = sum(p[0] for p in r) / n
        sy = sum(p[1] for p in r) / n
        return sx, sy
    return cx / (3 * a2), cy / (3 * a2)


def point_in_poly(x, y, ring):
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xint = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < xint:
                inside = not inside
    return inside


def parse_ref(ref):
    """Extract a hole number 1..18 from a ref tag ('1', '1 - #2', 'Hole 1')."""
    if not ref:
        return None
    m = re.findall(r"\d+", str(ref))
    for tok in m:
        n = int(tok)
        if 1 <= n <= 18:
            return n
    return None


def parse_par(par):
    if not par:
        return None
    m = re.search(r"\d+", str(par))
    return int(m.group(0)) if m else None


def inside_any(pts_lonlat, rings_lonlat, lon0, lat0):
    """True if any point of pts falls inside any of the rings."""
    rings_m = [[to_xy(p[0], p[1], lon0, lat0) for p in r] for r in rings_lonlat]
    for lon, lat in pts_lonlat:
        x, y = to_xy(lon, lat, lon0, lat0)
        if any(point_in_poly(x, y, r) for r in rings_m):
            return True
    return False


def ring_area_m(ring_lonlat, lon0, lat0):
    return ring_area([to_xy(p[0], p[1], lon0, lat0) for p in ring_lonlat])


def point_seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    if l2 == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


# --------------------------------------------------------------------------
# OSM fetch + parse.
# --------------------------------------------------------------------------
def fetch_osm(slug, lat, lon, half=0.015, _depth=0):
    """Fetch the map API bbox; cache raw XML. Shrinks the bbox on 509/400."""
    os.makedirs(RAW, exist_ok=True)
    path = os.path.join(RAW, slug + ".osm")
    if os.path.exists(path) and _depth == 0:
        return path
    left, bottom = lon - half, lat - half
    right, top = lon + half, lat + half
    url = ("https://www.openstreetmap.org/api/0.6/map"
           "?bbox=%f,%f,%f,%f" % (left, bottom, right, top))
    print("  GET %s" % url, flush=True)
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        body = e.read(300).decode("utf-8", "replace") if hasattr(e, "read") else ""
        if e.code in (400, 409, 509) and _depth < 3 and "too many" in body.lower():
            print("  node limit hit (%s); retrying with smaller bbox" % e.code)
            time.sleep(SLEEP)
            return fetch_osm(slug, lat, lon, half * 0.6, _depth + 1)
        raise RuntimeError("OSM fetch failed %s: %s" % (e.code, body[:200]))
    head = data.lstrip()[:500]
    if not (head.startswith(b"<?xml") or head.startswith(b"<osm")):
        raise RuntimeError("OSM fetch returned non-XML (%d bytes)" % len(data))
    if _depth == 0:
        with open(path, "wb") as f:
            f.write(data)
    time.sleep(SLEEP)
    if _depth == 0:
        return path
    tmp = path + ".small"
    with open(tmp, "wb") as f:
        f.write(data)
    return tmp


def merge_osm_tiles(tile_paths, out_path):
    """Merge tile XML files, deduping nodes/ways/relations by id."""
    nodes, ways, rels = {}, {}, {}
    for tp in tile_paths:
        root = ET.parse(tp).getroot()
        for n in root.iter("node"):
            nid = int(n.get("id"))
            if nid not in nodes:
                nodes[nid] = n
        for w in root.iter("way"):
            wid = int(w.get("id"))
            if wid not in ways:
                ways[wid] = w
        for r in root.iter("relation"):
            rid = int(r.get("id"))
            if rid not in rels:
                rels[rid] = r
    osm = ET.Element("osm", version="0.6", generator="hole-maps-merge")
    for d in (nodes, ways, rels):
        for k in sorted(d):
            osm.append(d[k])
    ET.ElementTree(osm).write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path


def fetch_osm_tiled(slug, clat, clon, half_lat, half_lon, tile_half=0.008):
    """Tile-fetch a bbox that is too dense for one map-API call; merge."""
    os.makedirs(RAW, exist_ok=True)
    path = os.path.join(RAW, slug + ".osm")
    if os.path.exists(path):
        return path
    tile_dir = os.path.join(RAW, "tiles", slug)
    os.makedirs(tile_dir, exist_ok=True)
    # Tile centers covering the bbox with slight overlap.
    lats, lat_c = [], clat - half_lat
    while lat_c < clat + half_lat:
        lats.append(lat_c + tile_half)
        lat_c += tile_half * 1.8
    lons, lon_c = [], clon - half_lon
    while lon_c < clon + half_lon:
        lons.append(lon_c + tile_half)
        lon_c += tile_half * 1.8
    tile_paths = []
    for i, tc_lat in enumerate(lats):
        for j, tc_lon in enumerate(lons):
            tp = os.path.join(tile_dir, "t%d_%d.osm" % (i, j))
            if not os.path.exists(tp):
                url = ("https://www.openstreetmap.org/api/0.6/map"
                       "?bbox=%f,%f,%f,%f"
                       % (tc_lon - tile_half, tc_lat - tile_half,
                          tc_lon + tile_half, tc_lat + tile_half))
                print("  GET(tile) %s" % url, flush=True)
                req = urllib.request.Request(url, headers=UA)
                try:
                    with urllib.request.urlopen(req, timeout=120) as resp:
                        data = resp.read()
                except urllib.error.HTTPError as e:
                    raise RuntimeError("tile fetch failed %s" % e.code)
                head = data.lstrip()[:500]
                if not (head.startswith(b"<?xml") or head.startswith(b"<osm")):
                    raise RuntimeError("tile fetch returned non-XML")
                with open(tp, "wb") as f:
                    f.write(data)
                time.sleep(SLEEP)
            tile_paths.append(tp)
    return merge_osm_tiles(tile_paths, path)


def parse_osm(path):
    tree = ET.parse(path)
    root = tree.getroot()
    nodes = {}
    for n in root.iter("node"):
        tags = {t.get("k"): t.get("v") for t in n.iter("tag")}
        nodes[int(n.get("id"))] = (float(n.get("lon")), float(n.get("lat")), tags)
    ways = {}
    for w in root.iter("way"):
        wid = int(w.get("id"))
        nds = [int(nd.get("ref")) for nd in w.iter("nd")]
        tags = {t.get("k"): t.get("v") for t in w.iter("tag")}
        ways[wid] = {"nds": nds, "tags": tags}
    rels = {}
    for r in root.iter("relation"):
        rid = int(r.get("id"))
        members = [(m.get("type"), int(m.get("ref")), m.get("role"))
                   for m in r.iter("member")]
        tags = {t.get("k"): t.get("v") for t in r.iter("tag")}
        rels[rid] = {"members": members, "tags": tags}
    return nodes, ways, rels


def way_ring_lonlat(way, nodes):
    pts = []
    for nid in way["nds"]:
        if nid in nodes:
            pts.append(nodes[nid][:2])
    return pts


def assemble_outer_rings(member_ways):
    """Chain multipolygon outer member ways into closed rings.

    Returns closed rings (node-id lists). Members that refuse to chain are
    kept as their largest member ring (closed artificially).
    """
    segs = [list(w) for w in member_ways if len(w) >= 2]
    rings = []
    open_segs = []
    for s in segs:
        if s[0] == s[-1]:
            rings.append(s)
        else:
            open_segs.append(s)
    leftovers = []
    while open_segs:
        cur = open_segs.pop(0)
        progress = True
        while progress and cur[0] != cur[-1]:
            progress = False
            for i, s in enumerate(open_segs):
                if s[0] == cur[-1]:
                    cur = cur + s[1:]
                elif s[-1] == cur[-1]:
                    cur = cur + s[-2::-1]
                elif s[-1] == cur[0]:
                    cur = s[:-1] + cur
                elif s[0] == cur[0]:
                    cur = s[:0:-1] + cur
                else:
                    continue
                open_segs.pop(i)
                progress = True
                break
        if cur[0] == cur[-1]:
            rings.append(cur)
        else:
            leftovers.append(cur)
    if leftovers:
        # Keep the largest unchained member as a ring (spec fallback).
        biggest = max(leftovers, key=len)
        rings.append(biggest + [biggest[0]])
    return rings


# --------------------------------------------------------------------------
# Feature extraction.
# --------------------------------------------------------------------------
def extract_features(nodes, ways, rels):
    holes = []      # dicts: id, pts(lonlat), ref, par, name
    polys = []      # dicts: kind, id, rings(list of lonlat rings), tags
    for wid in sorted(ways):
        w = ways[wid]
        tags = w["tags"]
        pts = way_ring_lonlat(w, nodes)
        if not pts:
            continue
        if tags.get("golf") == "hole":
            holes.append({"id": wid, "pts": pts, "tags": tags,
                          "name": tags.get("name", "")})
            continue
        kind = None
        if tags.get("golf") in ("fairway", "green", "bunker", "tee", "water_hazard"):
            kind = tags["golf"]
            if kind == "water_hazard":
                kind = "water"
        elif tags.get("natural") == "water":
            kind = "water"
        if kind:
            ring = list(pts)
            if ring[0] != ring[-1]:
                ring.append(ring[0])  # close unclosed feature ways
            if len(ring) >= 4:
                polys.append({"kind": kind, "id": wid, "rings": [ring], "tags": tags})
    for rid in sorted(rels):
        r = rels[rid]
        tags = r["tags"]
        is_mp = tags.get("type") == "multipolygon"
        if tags.get("golf") == "hole":
            continue  # hole relations are rare; centerlines are ways        kind = None
        if tags.get("golf") in ("fairway", "green", "bunker", "tee", "water_hazard"):
            kind = tags["golf"]
            if kind == "water_hazard":
                kind = "water"
        elif tags.get("natural") == "water":
            kind = "water"
        if kind and is_mp:
            outer_ids = [ref for (typ, ref, role) in r["members"]
                         if typ == "way" and role == "outer" and ref in ways]
            member_nds = [ways[w]["nds"] for w in outer_ids]
            rings = []
            for ring_ids in assemble_outer_rings(member_nds):
                pts = [nodes[n][:2] for n in ring_ids if n in nodes]
                if len(pts) >= 4:
                    rings.append(pts)
            if rings:
                polys.append({"kind": kind, "id": rid, "rings": rings, "tags": tags})
    # Point features: tees/greens/bunkers are sometimes mapped as nodes
    # (e.g. Yale's 70 tee boxes). They become small oriented shapes later,
    # once each node is assigned to its nearest hole centerline.
    node_feats = []
    for nid in sorted(nodes):
        lon, lat, tags = nodes[nid]
        g = tags.get("golf")
        if g in ("tee", "green", "bunker"):
            node_feats.append({"kind": g, "id": nid, "lon": lon, "lat": lat})
    return holes, polys, node_feats


def find_boundary_candidates(nodes, ways, rels, boundary_names):
    """All leisure=golf_course objects whose name matches a preferred name."""
    cands = []
    want = [b.lower() for b in boundary_names]

    def consider(obj_id, tags, ring_sets, otype):
        name = tags.get("name", "")
        nl = name.lower()
        idx = None
        exact = False
        for i, b in enumerate(want):
            if nl == b:
                idx, exact = i, True
                break
            if idx is None and b in nl:
                idx = i
        if idx is None:
            return
        cands.append({"id": obj_id, "name": name, "tags": tags,
                      "rings": ring_sets, "type": otype,
                      "pref": idx, "exact": exact})

    for wid in sorted(ways):
        w = ways[wid]
        if w["tags"].get("leisure") == "golf_course":
            pts = way_ring_lonlat(w, nodes)
            if len(pts) >= 4:
                ring = list(pts)
                if ring[0] != ring[-1]:
                    ring.append(ring[0])
                consider(wid, w["tags"], [ring], "way")
    for rid in sorted(rels):
        r = rels[rid]
        if r["tags"].get("leisure") != "golf_course":
            continue
        outer_ids = [ref for (typ, ref, role) in r["members"]
                     if typ == "way" and role == "outer" and ref in ways]
        rings = []
        for ring_ids in assemble_outer_rings([ways[w]["nds"] for w in outer_ids]):
            pts = [nodes[n][:2] for n in ring_ids if n in nodes]
            if len(pts) >= 4:
                rings.append(pts)
        if rings:
            consider(rid, r["tags"], rings, "relation")
    return cands

# --------------------------------------------------------------------------
# Synthetic geometry (same flat style as real data; mirrors the app's
# procedural taper approach, in meters).
# --------------------------------------------------------------------------
def synth_fairway(center_m, par):
    n = len(center_m)
    left, right = [], []
    for i in range(n):
        a = center_m[max(0, i - 1)]
        b = center_m[min(n - 1, i + 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        d = math.hypot(dx, dy) or 1.0
        px, py = -dy / d, dx / d
        t = 0.5 if n == 1 else i / (n - 1)
        w = 8.0 + 9.0 * math.sin(math.pi * t)
        if par <= 3:
            w *= 0.8
        left.append((center_m[i][0] + px * w, center_m[i][1] + py * w))
        right.append((center_m[i][0] - px * w, center_m[i][1] - py * w))
    return left + right[::-1]


def ellipse_poly(cx, cy, rx, ry, rot=0.0, n=24):
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        ex, ey = rx * math.cos(a), ry * math.sin(a)
        pts.append((cx + ex * math.cos(rot) - ey * math.sin(rot),
                    cy + ex * math.sin(rot) + ey * math.cos(rot)))
    pts.append(pts[0])
    return pts


def rect_poly(cx, cy, w, h, rot):
    pts = []
    for sx, sy in ((1, 1), (-1, 1), (-1, -1), (1, -1)):
        ex, ey = sx * w / 2, sy * h / 2
        pts.append((cx + ex * math.cos(rot) - ey * math.sin(rot),
                    cy + ex * math.sin(rot) + ey * math.cos(rot)))
    pts.append(pts[0])
    return pts


def synthesize(hole_m, par, rng):
    """Return dict of synthesized feature polygons in meters.

    centerline is real OSM; fairway taper + green ellipse + tee boxes +
    seeded bunkers mirror the app's procedural schematic approach.
    """
    center = hole_m["center_m"]
    tee = center[0]
    green_end = center[-1]
    dx, dy = green_end[0] - tee[0], green_end[1] - tee[1]
    d = math.hypot(dx, dy) or 1.0
    ux, uy = dx / d, dy / d
    rot = math.atan2(uy, ux)

    out = {}
    out["fairway"] = [synth_fairway(center, par)]
    grx, gry = (13.0, 9.0) if par <= 3 else (15.0, 11.0)
    out["green"] = [ellipse_poly(green_end[0], green_end[1], grx, gry, rot)]
    # Tee boxes near the tee end, stepped back along the hole direction.
    n_boxes = 1 if par <= 3 else (2 if par == 4 else 3)
    boxes = []
    for i in range(n_boxes):
        back = 4.0 + i * 5.0
        side = 6.0 if i % 2 == 0 else -6.0
        cx = tee[0] - ux * back + (-uy) * side
        cy = tee[1] - uy * back + (ux) * side
        boxes.append(rect_poly(cx, cy, 7.0, 4.0, rot))
    out["tee"] = boxes
    # Seeded bunkers: one fairway, two greenside.
    bunkers = []
    mid = center[len(center) // 2]
    side = 1.0 if rng.random() < 0.5 else -1.0
    off = 13.0 + rng.random() * 5.0
    t = 0.55 + rng.random() * 0.15
    bi = min(len(center) - 1, int(t * (len(center) - 1)))
    bp = center[bi]
    a = center[max(0, bi - 1)]
    b = center[min(len(center) - 1, bi + 1)]
    ddx, ddy = b[0] - a[0], b[1] - a[1]
    dd = math.hypot(ddx, ddy) or 1.0
    bunkers.append(ellipse_poly(bp[0] + (-ddy / dd) * side * off,
                                bp[1] + (ddx / dd) * side * off,
                                7.0, 4.0, math.atan2(ddy, ddx)))
    for j in range(2):
        ang = rot + (0.6 + rng.random() * 0.5) * (1.0 if j == 0 else -1.0)
        dist = 17.0 + rng.random() * 4.0
        bunkers.append(ellipse_poly(green_end[0] + math.cos(ang) * dist,
                                    green_end[1] + math.sin(ang) * dist,
                                    6.0, 3.5, ang))
    out["bunker"] = bunkers
    return out


# --------------------------------------------------------------------------
# Per-course pipeline.
# --------------------------------------------------------------------------
def process_course(cfg, args):
    slug = cfg["slug"]
    display = cfg["display"]
    out_dir = os.path.join(DATA, slug)
    holes_path = os.path.join(out_dir, "holes.json")
    if os.path.exists(holes_path) and not args.force:
        print("[%s] holes.json exists, skipping (use --force)" % slug)
        return "skipped"

    os.makedirs(out_dir, exist_ok=True)
    warnings = []
    log = []
    lat, lon = cfg["lat"], cfg["lon"]

    # 1. Fetch.
    if cfg.get("tile_fetch"):
        raw_path = fetch_osm_tiled(slug, lat, lon, 0.02, 0.02)
    else:
        raw_path = fetch_osm(slug, lat, lon)
    nodes, ways, rels = parse_osm(raw_path)
    log.append("raw: %d nodes, %d ways, %d relations"
               % (len(nodes), len(ways), len(rels)))

    holes_raw, polys, node_feats = extract_features(nodes, ways, rels)
    log.append("features: %d hole ways, %d polygons" % (len(holes_raw), len(polys)))

    # Optional per-course hole-way tag filter (e.g. Olympia North vs South).
    must = cfg.get("hole_name_must_contain")
    if must:
        before = len(holes_raw)
        holes_raw = [h for h in holes_raw
                     if must.lower() in (h["name"] + " " +
                                         h["tags"].get("golf:course", "")).lower()]
        log.append("hole_name_must_contain=%r: %d -> %d hole ways"
                   % (must, before, len(holes_raw)))

    # 2. Boundary candidates; prefer the one yielding refs closest to 1-18.
    lon0, lat0 = lon, lat
    cands = find_boundary_candidates(nodes, ways, rels, cfg["boundaries"])
    boundary = None
    if cands:
        def ref_count(c):
            refs = set()
            for h in holes_raw:
                n = parse_ref(h["tags"].get("ref", ""))
                if n and inside_any(h["pts"], c["rings"], lon0, lat0):
                    refs.add(n)
            return refs

        scored = []
        for c in cands:
            refs = ref_count(c)
            area = max((ring_area_m(r, lon0, lat0) for r in c["rings"]), default=0.0)
            scored.append((abs(18 - len(refs)), -1 if c["exact"] else 0,
                           c["pref"], -area, c, refs))
        scored.sort(key=lambda s: (s[0], s[1], s[2], s[3]))
        best = scored[0]
        boundary = best[4]
        log.append("boundary: %r (%s %d), %d distinct hole refs inside"
                   % (boundary["name"], boundary["type"], boundary["id"],
                      len(best[5])))
        if len(scored) > 1:
            others = ", ".join("%r(%d refs)" % (s[4]["name"], len(s[5]))
                               for s in scored[1:4])
            warnings.append("multiple boundary candidates; chose %r; others: %s"
                            % (boundary["name"], others))
    else:
        warnings.append("NO boundary matched %r — keeping all features (loud warning)"
                        % (cfg["boundaries"],))

    # Boundary-fitted re-fetch if the boundary extends beyond the first bbox.
    if boundary and not args.no_refetch:
        minlon = min(p[0] for r in boundary["rings"] for p in r)
        maxlon = max(p[0] for r in boundary["rings"] for p in r)
        minlat = min(p[1] for r in boundary["rings"] for p in r)
        maxlat = max(p[1] for r in boundary["rings"] for p in r)
        if (minlon < lon - 0.015 or maxlon > lon + 0.015 or
                minlat < lat - 0.015 or maxlat > lat + 0.015):
            pad = 1.10
            clat, clon = (minlat + maxlat) / 2, (minlon + maxlon) / 2
            half_lat = max((maxlat - minlat) / 2 * pad, 0.015)
            half_lon = max((maxlon - minlon) / 2 * pad, 0.015)
            half_lat = min(half_lat, 0.05)
            half_lon = min(half_lon, 0.05)
            print("  boundary extends past initial bbox; fetching fitted bbox and merging")
            raw_path = os.path.join(RAW, slug + ".osm")
            b1_path = os.path.join(RAW, slug + ".b1.osm")
            b2_path = os.path.join(RAW, slug + ".b2.osm")
            for p in (b1_path, b2_path):
                if os.path.exists(p):
                    os.remove(p)
            # Stash the initial fetch, fetch the fitted bbox under the
            # normal cache name, then merge both (union keeps everything).
            os.rename(raw_path, b1_path)
            try:
                got = fetch_osm(slug, clat, clon, half=max(half_lat, half_lon))
                os.rename(got, b2_path)
            finally:
                os.rename(b1_path, raw_path)
            merge_osm_tiles([raw_path, b2_path], raw_path)
            os.remove(b2_path)
            nodes, ways, rels = parse_osm(raw_path)
            holes_raw, polys, node_feats = extract_features(nodes, ways, rels)
            if must:
                holes_raw = [h for h in holes_raw
                             if must.lower() in (h["name"] + " " +
                                                 h["tags"].get("golf:course", "")).lower()]
            cands = find_boundary_candidates(nodes, ways, rels, cfg["boundaries"])
            # Re-pick the same boundary by id.
            for c in cands:
                if c["id"] == boundary["id"] and c["type"] == boundary["type"]:
                    boundary = c
                    break
            log.append("re-fetched boundary-fitted bbox; now %d hole ways, %d polys"
                       % (len(holes_raw), len(polys)))

    # 3. Filter features to the boundary (centroid inside).
    bound_rings_m = None
    if boundary:
        bound_rings_m = [[to_xy(p[0], p[1], lon0, lat0) for p in r]
                         for r in boundary["rings"]]

    def kept(poly):
        if not bound_rings_m:
            return True
        cx, cy = poly_centroid([to_xy(p[0], p[1], lon0, lat0)
                                for p in poly["rings"][0]])
        return any(point_in_poly(cx, cy, r) for r in bound_rings_m)

    kept_polys = [p for p in polys if kept(p)]
    dropped = len(polys) - len(kept_polys)
    if dropped:
        log.append("boundary filter: kept %d/%d polygons" % (len(kept_polys), len(polys)))
    if not bound_rings_m:
        warnings.append("NO BOUNDARY: all %d polygons kept without filtering" % len(polys))

    # 4. Hole ways: parse refs, dedup (prefer par tag, then longest centerline).
    hole_ways = []
    odd_refs = []
    for h in holes_raw:
        ref = h["tags"].get("ref", "")
        n = parse_ref(ref)
        if n is None:
            odd_refs.append(ref or "(empty)")
            continue
        if must is None and boundary and not inside_any(h["pts"], boundary["rings"], lon0, lat0):
            continue  # hole centerline outside the chosen boundary
        par = parse_par(h["tags"].get("par", ""))
        hole_ways.append({"n": n, "id": h["id"], "pts": h["pts"], "par": par,
                          "ref": ref, "name": h["name"]})
    if odd_refs:
        warnings.append("hole ways with unparseable refs: %s" % sorted(set(odd_refs))[:10])
    if cfg.get("keep_northern_hole_cluster") and hole_ways:
        # 1-D 2-means on centerline mean latitude; keep the northern cluster.
        lats = []
        for h in hole_ways:
            ml = sum(p[1] for p in h["pts"]) / len(h["pts"])
            lats.append((ml, h))
        lo, hi = min(l[0] for l in lats), max(l[0] for l in lats)
        c0, c1 = lo, hi
        for _ in range(50):
            g0 = [l for l in lats if abs(l[0] - c0) <= abs(l[0] - c1)]
            g1 = [l for l in lats if abs(l[0] - c0) > abs(l[0] - c1)]
            n0 = sum(l[0] for l in g0) / len(g0)
            n1 = sum(l[0] for l in g1) / len(g1)
            if (n0, n1) == (c0, c1):
                break
            c0, c1 = n0, n1
        north = g0 if c0 > c1 else g1
        dropped_n = len(hole_ways) - len(north)
        hole_ways = [l[1] for l in north]
        log.append("keep_northern_hole_cluster: kept %d/%d hole ways "
                   "(dropped southern cluster: %d)"
                   % (len(hole_ways), len(hole_ways) + dropped_n, dropped_n))
    if not hole_ways:
        raise RuntimeError("no hole ways with refs 1-18 after filtering")
    log.append("hole ways with refs 1-18: %d" % len(hole_ways))
    for h in sorted(hole_ways, key=lambda h: (h["n"], h["id"])):
        log.append("  hole way id=%d ref=%r parsed=%d par=%s name=%r"
                   % (h["id"], h["ref"], h["n"], h["par"], h["name"]))

    by_ref = {}
    for h in hole_ways:
        by_ref.setdefault(h["n"], []).append(h)
    dup_notes = []
    holes = {}
    name_pref = (cfg.get("hole_name_preferred") or "").lower()
    for n in sorted(by_ref):
        def dup_key(h):
            pref = 0 if name_pref and name_pref in h["name"].lower() else 1
            return (pref, 0 if h["par"] else 1, -len(h["pts"]), h["id"])
        cands_n = sorted(by_ref[n], key=dup_key)
        chosen = cands_n[0]
        holes[n] = chosen
        if len(cands_n) > 1:
            dup_notes.append("ref %d: kept id=%d par=%s (%d nodes); dropped %s"
                             % (n, chosen["id"], chosen["par"], len(chosen["pts"]),
                                ["id=%d par=%s" % (h["id"], h["par"]) for h in cands_n[1:]]))
    if dup_notes:
        warnings.append("duplicate refs resolved:\n  " + "\n  ".join(dup_notes))

    missing_refs = [n for n in range(1, 19) if n not in holes]
    if missing_refs:
        warnings.append("missing hole refs (no centerline, no image): %s" % missing_refs)

    # 5. Cluster features to holes (local meters).
    hole_ms = {}
    for n, h in holes.items():
        hole_ms[n] = [to_xy(p[0], p[1], lon0, lat0) for p in h["pts"]]

    poly_ms = []
    for p in kept_polys:
        rings_m = [[to_xy(q[0], q[1], lon0, lat0) for q in r] for r in p["rings"]]
        cx, cy = poly_centroid(rings_m[0])
        poly_ms.append({"kind": p["kind"], "id": p["id"], "rings_m": rings_m,
                        "cx": cx, "cy": cy})

    assigned = {n: {"fairway": [], "green": [], "bunker": [], "tee": [], "water": []}
                for n in holes}
    unassigned = {"fairway": 0, "green": 0, "bunker": 0, "tee": 0, "water": 0}
    for p in poly_ms:
        dists = []
        for n, center in hole_ms.items():
            dmin = min(point_seg_dist(p["cx"], p["cy"], center[i][0], center[i][1],
                                      center[i + 1][0], center[i + 1][1])
                       for i in range(len(center) - 1)) if len(center) > 1 else \
                math.hypot(p["cx"] - center[0][0], p["cy"] - center[0][1])
            dists.append((dmin, n))
        dists.sort()
        kind = p["kind"]
        if kind == "tee":
            # Tees: keep ALL within 80m (multiple tee boxes per hole).
            hit = [n for d, n in dists if d < 80.0]
            if hit:
                for n in hit:
                    assigned[n]["tee"].append(p)
            else:
                unassigned["tee"] += 1
        else:
            thresh = 150.0 if kind == "green" else 80.0
            d, n = dists[0]
            if d < thresh:
                assigned[n][kind].append(p)
            else:
                unassigned[kind] += 1
    log.append("unassigned: %s" % unassigned)

    # 5b. Point features (tee/green/bunker nodes): boundary-filter, assign
    # to nearest hole(s), and expand to small oriented shapes in meters.
    node_rng = random.Random("nodes#%s" % slug)
    n_node_kept = 0
    for nf in node_feats:
        x, y = to_xy(nf["lon"], nf["lat"], lon0, lat0)
        if bound_rings_m and not any(point_in_poly(x, y, r) for r in bound_rings_m):
            continue
        n_node_kept += 1
        dists = []
        for n, center in hole_ms.items():
            if len(center) > 1:
                best = (1e18, 0, 0.0)
                for i in range(len(center) - 1):
                    ax, ay = center[i]
                    bx, by = center[i + 1]
                    dx, dy = bx - ax, by - ay
                    l2 = dx * dx + dy * dy or 1e-9
                    t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / l2))
                    d = math.hypot(x - (ax + t * dx), y - (ay + t * dy))
                    if d < best[0]:
                        best = (d, i, math.atan2(dy, dx))
                dists.append((best[0], n, best[2]))
            else:
                dists.append((math.hypot(x - center[0][0], y - center[0][1]), n, 0.0))
        dists.sort()
        kind = nf["kind"]
        if kind == "tee":
            targets = [(n, rot) for d, n, rot in dists if d < 80.0]
        else:
            thresh = 150.0 if kind == "green" else 80.0
            d, n, rot = dists[0]
            targets = [(n, rot)] if d < thresh else []
        if not targets:
            unassigned[kind] += 1
            continue
        for n, rot in targets:
            if kind == "tee":
                ring_m = rect_poly(x, y, 7.0, 4.0, rot)
            elif kind == "green":
                ring_m = ellipse_poly(x, y, 13.0, 10.0, rot)
            else:
                ring_m = ellipse_poly(x, y, 7.0, 4.0, node_rng.random() * math.pi)
            cx, cy = poly_centroid(ring_m)
            assigned[n][kind].append({"kind": kind, "id": "n%d" % nf["id"],
                                     "rings_m": [ring_m], "cx": cx, "cy": cy})
    log.append("node features kept by boundary: %d/%d" % (n_node_kept, len(node_feats)))

    # 6. Orientation: tee -> green (node nearer the green centroid is the
    # green end; flip if needed). Fall back to tee boxes, then as-is.
    for n, h in holes.items():
        center = hole_ms[n]
        green_c = None
        if assigned[n]["green"]:
            g = max(assigned[n]["green"],
                    key=lambda p: ring_area(p["rings_m"][0]))
            green_c = (g["cx"], g["cy"])
        d_first = d_last = None
        if green_c:
            d_first = math.hypot(center[0][0] - green_c[0], center[0][1] - green_c[1])
            d_last = math.hypot(center[-1][0] - green_c[0], center[-1][1] - green_c[1])
        elif assigned[n]["tee"]:
            tcx = sum(p["cx"] for p in assigned[n]["tee"]) / len(assigned[n]["tee"])
            tcy = sum(p["cy"] for p in assigned[n]["tee"]) / len(assigned[n]["tee"])
            d_first = math.hypot(center[0][0] - tcx, center[0][1] - tcy)
            d_last = math.hypot(center[-1][0] - tcx, center[-1][1] - tcy)
            # tee end = nearer the tee boxes
            if d_first > d_last:
                center.reverse()
                h["pts"].reverse()
                h["flipped"] = "via_tee"
        else:
            warnings.append("hole %d: no green/tee for orientation; kept as-is" % n)
            continue
        if green_c and d_first < d_last:
            center.reverse()
            h["pts"].reverse()
            h["flipped"] = True

    # 7. Build hole records (+ synthesis) and write outputs.
    holes_out = []
    per_hole_log = []
    missing_pars = []
    for n in sorted(holes):
        h = holes[n]
        par = h["par"]
        if par is None and n in cfg.get("par_overrides", {}):
            par = cfg["par_overrides"][n]
            warnings.append("hole %d: par missing in OSM; override par=%d (verified, see config)"
                            % (n, par))
        if par is None:
            missing_pars.append(n)
        feats = {"fairway": [], "green": [], "bunker": [], "tee": [], "water": []}
        synth_used = set()
        for kind in feats:
            for p in assigned[n][kind]:
                feats[kind].append([list(to_lonlat(x, y, lon0, lat0))
                                    for x, y in p["rings_m"][0]])
        has_osm_fg = bool(feats["fairway"]) or bool(feats["green"])
        has_osm_any = any(feats[k] for k in feats)
        rng = random.Random("%s#%d" % (slug, n))
        if not feats["fairway"] or not feats["green"] or not feats["tee"]:
            hole_m = {"center_m": hole_ms[n]}
            synth = synthesize(hole_m, par or 4, rng)
            if not feats["fairway"]:
                feats["fairway"] = [[list(to_lonlat(x, y, lon0, lat0))
                                     for x, y in synth["fairway"][0]]]
                synth_used.add("fairway")
            if not feats["green"]:
                feats["green"] = [[list(to_lonlat(x, y, lon0, lat0))
                                   for x, y in synth["green"][0]]]
                synth_used.add("green")
            if not feats["tee"]:
                feats["tee"] = [[list(to_lonlat(x, y, lon0, lat0))
                                 for x, y in b] for b in synth["tee"]]
                synth_used.add("tee")
            if len(feats["bunker"]) < 2:
                feats["bunker"].extend(
                    [list(to_lonlat(x, y, lon0, lat0)) for x, y in b]
                    for b in synth["bunker"][:3 - len(feats["bunker"])])
                synth_used.add("bunker")
        if not synth_used:
            source = "osm"
        elif has_osm_any:
            source = "mixed"
        else:
            source = "synthetic"
        holes_out.append({
            "number": n,
            "par": par,
            "centerline": [list(p) for p in h["pts"]],
            "features": feats,
            "source": source,
        })
        per_hole_log.append(
            "  #%d par=%s source=%s nodes=%d fairway=%d green=%d bunker=%d tee=%d water=%d%s"
            % (n, par, source, len(h["pts"]),
               len(feats["fairway"]), len(feats["green"]), len(feats["bunker"]),
               len(feats["tee"]), len(feats["water"]),
               " synth=%s" % sorted(synth_used) if synth_used else ""))

    with open(holes_path, "w") as f:
        json.dump({"course": display, "slug": slug, "holes": holes_out}, f)

    with open(os.path.join(out_dir, "qa.txt"), "w") as f:
        f.write("course: %s (%s)\n" % (display, slug))
        f.write("boundary: %s\n"
                % (repr(boundary["name"]) + " (%s %d)" % (boundary["type"], boundary["id"])
                   if boundary else "NONE"))
        f.write("\n".join(log) + "\n")
        f.write("holes with centerlines: %d\n" % len(holes_out))
        f.write("missing pars: %s\n" % (missing_pars or "none"))
        f.write("\n".join(per_hole_log) + "\n")
        f.write("WARNINGS:\n")
        f.write(("\n".join("- " + w for w in warnings) if warnings else "none") + "\n")

    print("[%s] %d holes -> %s" % (slug, len(holes_out), holes_path))
    for w in warnings:
        print("  WARN: %s" % w.split("\n")[0])
    if missing_pars:
        print("  MISSING PARS: %s" % missing_pars)
    return "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="re-extract even when holes.json exists")
    ap.add_argument("--no-refetch", action="store_true",
                    help="never re-fetch a boundary-fitted bbox")
    ap.add_argument("--courses", nargs="*", default=None,
                    help="only these slugs")
    ap.add_argument("--list", action="store_true", help="list courses and exit")
    args = ap.parse_args()
    if args.list:
        for c in COURSES:
            print("%s -> %s" % (c["slug"], c["display"]))
        return
    todo = [c for c in COURSES if not args.courses or c["slug"] in args.courses]
    for cfg in todo:
        try:
            process_course(cfg, args)
        except Exception as e:
            print("[%s] FAILED: %s" % (cfg["slug"], e), file=sys.stderr)


if __name__ == "__main__":
    main()
