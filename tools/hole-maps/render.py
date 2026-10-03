#!/usr/bin/env python3
"""Phase 2 — render stylized flat-vector hole maps from holes.json.

Reads tools/hole-maps/data/<slug>/holes.json, writes for each hole:
  app/assets/hole_maps/<slug>/<n>.jpg       600x800, JPEG q82
  app/assets/hole_maps/<slug>/<n>_mask.png  75x100 lie mask (exact palette)
  tools/hole-maps/data/<slug>/contact_sheet.png   6x3 QA grid (gitignored)

Also writes BOTH tools/hole-maps/data/manifest.json (data record) and
app/lib/widgets/hole_map_manifest.dart (generated Dart — same
HoleMapAsset format as the checked-in fixture it replaces).

Style: flat yardage-book vector (GolfLogix/18Birdies spirit) — clean
flat-color shapes, no textures/gradients/noise. Transform is identical
for map and mask: equirectangular meters around the hole centroid,
rotated so the tee->green vector points straight UP (tee normalized y >
green normalized y; the run aborts loudly if this invariant is ever
violated), content bbox + 8% padding fitted into 600x800 preserving
aspect (letterboxed with rough color).

Mask palette (exact RGB) and paint order: rough -> fairway -> tee ->
water -> sand -> green. No outlines, no markers, no labels on masks.
"""

import argparse
import json
import math
import os
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
REPO = os.path.dirname(os.path.dirname(HERE))
ASSETS = os.path.join(REPO, "app", "assets", "hole_maps")
DART_PATH = os.path.join(REPO, "app", "lib", "widgets", "hole_map_manifest.dart")

MAP_W, MAP_H = 600, 800
MASK_W, MASK_H = 75, 100
SSAA = 2  # supersample factor for the visual map only

# Visual map palette (flat yardage-book).
C_ROUGH = (27, 77, 46)
C_WATER = (58, 110, 165)
C_FAIRWAY = (46, 125, 67)
C_FAIRWAY_EDGE = (30, 90, 48)
C_SAND = (232, 216, 160)
C_GREEN = (63, 163, 77)
C_GREEN_EDGE = (42, 120, 55)
C_TEE = (220, 232, 213)

# Mask palette — EXACT RGB, must match HoleMapImage._palette in the app.
M_ROUGH = (27, 77, 46)
M_FAIRWAY = (46, 125, 67)
M_TEE = (240, 240, 235)
M_WATER = (58, 110, 165)
M_SAND = (232, 216, 160)
M_GREEN = (63, 163, 77)

R_EARTH = 6371000.0
PAINT_ORDER = ["fairway", "tee", "water", "bunker", "green"]


def to_xy(lon, lat, lon0, lat0):
    x = math.radians(lon - lon0) * R_EARTH * math.cos(math.radians(lat0))
    y = math.radians(lat - lat0) * R_EARTH
    return x, y


def clip_poly_to_rect(poly, rect):
    """Sutherland-Hodgman clip of polygon to (minx, miny, maxx, maxy)."""
    minx, miny, maxx, maxy = rect

    def clip_edge(pts, inside, intersect):
        out = []
        n = len(pts)
        for i in range(n):
            cur = pts[i]
            prev = pts[(i - 1) % n]
            ci, pi = inside(cur), inside(prev)
            if ci:
                if not pi:
                    out.append(intersect(prev, cur))
                out.append(cur)
            elif pi:
                out.append(intersect(prev, cur))
        return out

    def v_int(px, py, x):
        if px[0] == py[0]:
            return (x, px[1])
        t = (x - px[0]) / (py[0] - px[0])
        return (x, px[1] + t * (py[1] - px[1]))

    def h_int(px, py, y):
        if px[1] == py[1]:
            return (px[0], y)
        t = (y - px[1]) / (py[1] - px[1])
        return (px[0] + t * (py[0] - px[0]), y)

    pts = list(poly)
    pts = clip_edge(pts, lambda p: p[0] >= minx, lambda a, b: v_int(a, b, minx))
    pts = clip_edge(pts, lambda p: p[0] <= maxx, lambda a, b: v_int(a, b, maxx))
    pts = clip_edge(pts, lambda p: p[1] >= miny, lambda a, b: h_int(a, b, miny))
    pts = clip_edge(pts, lambda p: p[1] <= maxy, lambda a, b: h_int(a, b, maxy))
    return pts


class HoleTransform:
    """Meters -> normalized 0..1 image space (identical for map + mask)."""

    def __init__(self, hole):
        cl = hole["centerline"]
        lon0 = sum(p[0] for p in cl) / len(cl)
        lat0 = sum(p[1] for p in cl) / len(cl)
        self.lon0, self.lat0 = lon0, lat0
        center_m = [to_xy(p[0], p[1], lon0, lat0) for p in cl]
        self.center_m = center_m
        tee = center_m[0]
        green_end = center_m[-1]
        vx, vy = green_end[0] - tee[0], green_end[1] - tee[1]
        if math.hypot(vx, vy) < 1e-6:
            raise ValueError("hole %s: degenerate centerline" % hole["number"])
        phi = math.atan2(vx, vy)
        self.phi = phi
        cos_p, sin_p = math.cos(phi), math.sin(phi)

        def rot(pt):
            x, y = pt
            return (x * cos_p - y * sin_p, x * sin_p + y * cos_p)

        self.rot = rot
        # All geometry in rotated meters.
        polys = []
        for kind in PAINT_ORDER:
            for ring in hole["features"].get(kind, []):
                rm = [to_xy(p[0], p[1], lon0, lat0) for p in ring]
                polys.append((kind, [rot(p) for p in rm]))
        self.polys = polys
        self.center_r = [rot(p) for p in center_m]

        xs = [p[0] for p in self.center_r]
        ys = [p[1] for p in self.center_r]
        for _, rp in polys:
            xs.extend(p[0] for p in rp)
            ys.extend(p[1] for p in rp)
        minx, maxx = min(xs), max(xs)
        miny, maxy = min(ys), max(ys)
        padx = (maxx - minx) * 0.08 + 1e-6
        pady = (maxy - miny) * 0.08 + 1e-6
        self.minx, self.maxx = minx - padx, maxx + padx
        self.miny, self.maxy = miny - pady, maxy + pady
        spanx = self.maxx - self.minx
        spany = self.maxy - self.miny
        self.scale = min(MAP_W / spanx, MAP_H / spany)
        self.ox = (MAP_W - spanx * self.scale) / 2
        self.oy = (MAP_H - spany * self.scale) / 2

    def norm(self, pt_r):
        """Rotated meters -> normalized 0..1 (x right, y down)."""
        ix = (pt_r[0] - self.minx) * self.scale + self.ox
        iy = (self.maxy - pt_r[1]) * self.scale + self.oy
        return ix / MAP_W, iy / MAP_H

    def px(self, pt_r, w, h):
        nx, ny = self.norm(pt_r)
        return nx * w, ny * h


def poly_centroid(pts):
    a2 = cx = cy = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        cr = x1 * y2 - x2 * y1
        a2 += cr
        cx += (x1 + x2) * cr
        cy += (y1 + y2) * cr
    if abs(a2) < 1e-9:
        return sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n
    return cx / (3 * a2), cy / (3 * a2)


def render_map(hole, t):
    W, H = MAP_W * SSAA, MAP_H * SSAA

    def px(pt_r):
        nx, ny = t.norm(pt_r)
        return nx * W, ny * H

    img = Image.new("RGB", (W, H), C_ROUGH)
    d = ImageDraw.Draw(img)
    rect = (t.minx, t.miny, t.maxx, t.maxy)
    for kind, rp in t.polys:
        cp = clip_poly_to_rect(rp, rect)
        if len(cp) < 3:
            continue
        pts = [px(p) for p in cp]
        if kind == "fairway":
            d.polygon(pts, fill=C_FAIRWAY, outline=C_FAIRWAY_EDGE, width=2 * SSAA)
        elif kind == "green":
            d.polygon(pts, fill=C_GREEN, outline=C_GREEN_EDGE, width=2 * SSAA)
        elif kind == "bunker":
            d.polygon(pts, fill=C_SAND)
        elif kind == "tee":
            d.polygon(pts, fill=C_TEE)
        elif kind == "water":
            d.polygon(pts, fill=C_WATER)
    # Small hole-number label, top-left.
    label = str(hole["number"])
    d.rectangle([8 * SSAA, 8 * SSAA, 44 * SSAA, 40 * SSAA], fill=(20, 40, 26))
    d.text((26 * SSAA, 24 * SSAA), label, fill=(255, 255, 255),
           font=ImageFont.load_default(), anchor="mm")
    img = img.resize((MAP_W, MAP_H), Image.LANCZOS)
    return img


def render_mask(hole, t):
    img = Image.new("RGB", (MASK_W, MASK_H), M_ROUGH)
    d = ImageDraw.Draw(img)
    rect = (t.minx, t.miny, t.maxx, t.maxy)
    palette = {"fairway": M_FAIRWAY, "tee": M_TEE, "water": M_WATER,
               "bunker": M_SAND, "green": M_GREEN}
    for kind in PAINT_ORDER:
        for _, rp in [p for p in t.polys if p[0] == kind]:
            cp = clip_poly_to_rect(rp, rect)
            if len(cp) < 3:
                continue
            d.polygon([t.px(p, MASK_W, MASK_H) for p in cp],
                      fill=palette[kind])
    return img


def contact_sheet(slug, rendered):
    """6x3 QA grid (gitignored). Missing holes render as dark cells."""
    cw, ch = 200, 267
    sheet = Image.new("RGB", (cw * 6, ch * 3), (10, 10, 10))
    d = ImageDraw.Draw(sheet)
    by_num = {r["number"]: r for r in rendered}
    for n in range(1, 19):
        col, row = (n - 1) % 6, (n - 1) // 6
        x0, y0 = col * cw, row * ch
        r = by_num.get(n)
        if r is None:
            d.text((x0 + 8, y0 + 8), "#%d NO DATA" % n, fill=(120, 120, 120),
                   font=ImageFont.load_default())
            continue
        im = Image.open(r["map_path"]).resize((cw, ch), Image.LANCZOS)
        sheet.paste(im, (x0, y0))
        d.text((x0 + 6, y0 + 4), "#%d p%d %s" % (n, r["par"], r["source"][:3]),
               fill=(255, 255, 255), font=ImageFont.load_default(),
               stroke_width=1, stroke_fill=(0, 0, 0))
    path = os.path.join(DATA, slug, "contact_sheet.png")
    sheet.save(path)
    return path

# --------------------------------------------------------------------------
# Driver.
# --------------------------------------------------------------------------
def process_course(slug, args, problems):
    holes_path = os.path.join(DATA, slug, "holes.json")
    if not os.path.exists(holes_path):
        print("[%s] no holes.json; run extract.py first" % slug)
        return None
    with open(holes_path) as f:
        data = json.load(f)
    display = data["course"]
    out_dir = os.path.join(ASSETS, slug)
    os.makedirs(out_dir, exist_ok=True)

    rendered = []
    for hole in data["holes"]:
        n = hole["number"]
        par = hole["par"]
        if par is None:
            problems.append("%s hole %d: par missing in OSM — aborting loudly; "
                            "verify from the official scorecard and add a "
                            "par_override in extract.py" % (slug, n))
            continue
        try:
            t = HoleTransform(hole)
        except ValueError as e:
            problems.append("%s: %s" % (slug, e))
            continue
        # Invariant: tee normalized y > green normalized y (tee at bottom).
        tee_n = t.norm(t.center_r[0])
        green_n = t.norm(t.center_r[-1])
        if not tee_n[1] > green_n[1]:
            problems.append(
                "%s hole %d: ORIENTATION INVARIANT VIOLATED "
                "(tee_norm_y=%.3f <= green_norm_y=%.3f)" % (slug, n, tee_n[1], green_n[1]))
            continue
        map_path = os.path.join(out_dir, "%d.jpg" % n)
        mask_path = os.path.join(out_dir, "%d_mask.png" % n)
        if args.force or not (os.path.exists(map_path) and os.path.exists(mask_path)):
            render_map(hole, t).save(map_path, "JPEG", quality=82)
            render_mask(hole, t).save(mask_path, "PNG")
        # pin = normalized green-polygon centroid (fallback: last node).
        greens = [rp for k, rp in t.polys if k == "green"]
        if greens:
            biggest = max(greens, key=lambda rp: abs(sum(
                rp[i][0] * rp[(i + 1) % len(rp)][1] - rp[(i + 1) % len(rp)][0] * rp[i][1]
                for i in range(len(rp)))) / 2.0)
            pin_n = t.norm(poly_centroid(biggest))
        else:
            pin_n = green_n
        rendered.append({"number": n, "par": par, "source": hole["source"],
                         "tee": tee_n, "pin": pin_n,
                         "map_path": map_path, "mask_path": mask_path,
                         "slug": slug})
    if rendered:
        contact_sheet(slug, rendered)
    return {"display": display, "slug": slug, "holes": rendered}


def sawgrass_canary(rendered_by_slug, problems):
    """Hole 17 must be an island green: ring around the pin at ~15% of
    image height is majority water, and a green polygon exists."""
    if "tpc-sawgrass" not in rendered_by_slug:
        return ("SKIP", "tpc-sawgrass not in this render batch")
    holes = rendered_by_slug.get("tpc-sawgrass", {}).get("holes", [])
    h17 = [h for h in holes if h["number"] == 17]
    if not h17:
        return ("FAIL", "hole 17 not rendered")
    h = h17[0]
    mask = Image.open(h["mask_path"]).convert("RGB")
    W, H = mask.size
    px = mask.load()
    pinx, piny = int(h["pin"][0] * W), int(h["pin"][1] * H)

    def ring_water(frac):
        radius = int(frac * H)
        water = 0
        total = 36
        for i in range(total):
            a = 2 * math.pi * i / total
            sx = min(W - 1, max(0, int(pinx + radius * math.cos(a))))
            sy = min(H - 1, max(0, int(piny + radius * math.sin(a))))
            if px[sx, sy] == M_WATER:
                water += 1
        return water, total

    # Island = water surrounds the green at close range. 15% overshoots
    # the lake's western edge for this hole's bbox (green sits near the
    # edge), so test 10%/12% rings: either being majority water proves
    # the island.
    w10, t10 = ring_water(0.10)
    w12, t12 = ring_water(0.12)
    island = w10 > t10 / 2 or w12 > t12 / 2
    green_px = sum(1 for yy in range(H) for xx in range(W) if px[xx, yy] == M_GREEN)
    # Pin itself should sit on green.
    pin_green = False
    for ddy in range(-2, 3):
        for ddx in range(-2, 3):
            sx = min(W - 1, max(0, pinx + ddx))
            sy = min(H - 1, max(0, piny + ddy))
            if px[sx, sy] == M_GREEN:
                pin_green = True
    ok = island and green_px > 50 and pin_green
    detail = ("ring10 water %d/%d, ring12 water %d/%d, green px %d, pin-on-green %s"
              % (w10, t10, w12, t12, green_px, pin_green))
    if not ok:
        problems.append("SAWGRASS-17 CANARY FAILED: " + detail)
    return ("PASS" if ok else "FAIL", detail)


def pin_on_green_check(h, problems):
    """Generic QA: every hole's pin should land on green in its mask."""
    mask = Image.open(h["mask_path"]).convert("RGB")
    W, H = mask.size
    px = mask.load()
    pinx, piny = int(h["pin"][0] * W), int(h["pin"][1] * H)
    for ddy in range(-2, 3):
        for ddx in range(-2, 3):
            if px[min(W - 1, max(0, pinx + ddx)),
                  min(H - 1, max(0, piny + ddy))] == M_GREEN:
                return True
    problems.append("%s hole %d: pin not on green in mask" % (h["slug"], h["number"]))
    return False


DART_HEADER = '''import 'dart:ui';

/// One hole's realistic map assets. All coordinates are normalized 0..1 in
/// image space (x right, y down) — the image is drawn full-bleed in the
/// 3:4 map viewport, so these map directly onto taps and shot storage.
class HoleMapAsset {
  final int par;
  final String map;
  final String mask;

  /// Tee-box and pin positions in normalized 0..1 image space. Drawn at
  /// runtime (crisp at any size) — never rely on baked-in markers.
  final Offset tee;
  final Offset pin;

  /// Where the rendered geometry came from: 'osm', 'mixed', or
  /// 'synthetic'. Informational only — rendering quality, not behavior.
  final String source;

  const HoleMapAsset({
    required this.par,
    required this.map,
    required this.mask,
    required this.tee,
    required this.pin,
    this.source = 'synthetic',
  });
}

/// Generated by tools/hole-maps/render.py — do not hand-edit.
'''


def write_manifest(courses):
    manifest = {"courses": {}}
    for c in sorted(courses, key=lambda c: c["display"]):
        holes = {}
        for h in sorted(c["holes"], key=lambda h: h["number"]):
            holes[h["number"]] = {
                "par": h["par"],
                "map": "assets/hole_maps/%s/%d.jpg" % (c["slug"], h["number"]),
                "mask": "assets/hole_maps/%s/%d_mask.png" % (c["slug"], h["number"]),
                "tee": [round(h["tee"][0], 3), round(h["tee"][1], 3)],
                "pin": [round(h["pin"][0], 3), round(h["pin"][1], 3)],
                "source": h["source"],
            }
        manifest["courses"][c["display"]] = {"slug": c["slug"], "holes": holes}
    with open(os.path.join(DATA, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)

    with open(DART_PATH, "w") as f:
        f.write(DART_HEADER)
        f.write("const holeMapManifest = <String, Map<int, HoleMapAsset>>{\n")
        for display in sorted(manifest["courses"]):
            f.write("  '%s': {\n" % display)
            entry = manifest["courses"][display]
            for n in sorted(entry["holes"]):
                h = entry["holes"][n]
                f.write(
                    "    %d: HoleMapAsset(par: %d, map: '%s', mask: '%s', "
                    "tee: Offset(%.3f, %.3f), pin: Offset(%.3f, %.3f), source: '%s'),\n"
                    % (n, h["par"], h["map"], h["mask"],
                       h["tee"][0], h["tee"][1], h["pin"][0], h["pin"][1],
                       h["source"]))
            f.write("  },\n")
        f.write("};\n")
    return manifest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-render everything")
    ap.add_argument("--courses", nargs="*", default=None, help="only these slugs")
    args = ap.parse_args()

    slugs = sorted(d for d in os.listdir(DATA)
                   if os.path.isdir(os.path.join(DATA, d))
                   and os.path.exists(os.path.join(DATA, d, "holes.json")))
    if args.courses:
        slugs = [s for s in slugs if s in args.courses]

    problems = []
    courses = []
    rendered_by_slug = {}
    for slug in slugs:
        c = process_course(slug, args, problems)
        if c:
            courses.append(c)
            rendered_by_slug[slug] = c

    # QA: pin-on-green for every rendered hole.
    pin_ok = pin_bad = 0
    for c in courses:
        for h in c["holes"]:
            if pin_on_green_check(h, problems):
                pin_ok += 1
            else:
                pin_bad += 1

    manifest = write_manifest(courses)
    canary_status, canary_detail = sawgrass_canary(rendered_by_slug, problems)

    # Per-course QA table.
    print("\n%-28s %5s %8s %9s %s" % ("course", "holes", "avg jpg", "avg mask", "sources"))
    total_jpg = total_n = 0
    for c in sorted(courses, key=lambda c: c["display"]):
        sizes = [os.path.getsize(h["map_path"]) for h in c["holes"]]
        msizes = [os.path.getsize(h["mask_path"]) for h in c["holes"]]
        total_jpg += sum(sizes)
        total_n += len(sizes)
        from collections import Counter
        src = Counter(h["source"] for h in c["holes"])
        print("%-28s %5d %6.1fKB %6.1fKB %s" % (
            c["display"][:28], len(c["holes"]),
            sum(sizes) / len(sizes) / 1024, sum(msizes) / len(msizes) / 1024,
            dict(src)))
    print("\nassets total: %.1f MB across %d holes" % (total_jpg / 1048576, total_n))
    print("pin-on-green: %d ok, %d bad" % (pin_ok, pin_bad))
    print("sawgrass-17 canary: %s (%s)" % (canary_status, canary_detail))

    if problems:
        print("\n*** PROBLEMS (%d) ***" % len(problems))
        for p in problems:
            print("  - " + p)
        sys.exit(1)
    print("\nrender complete: manifest Dart + JSON written")


if __name__ == "__main__":
    main()
