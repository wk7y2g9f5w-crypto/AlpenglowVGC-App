"""One-off: rebuild Kiawah Ocean holes 2-9 from Cayden's GPS-app screenshots.

The OSM extraction (extract.py) found no usable centerlines for holes 2-8
inside the course boundary, so those holes fell back to the procedural
schematic. Cayden supplied 8 phone screenshots (holes 2-9) from a golf GPS
app; this script color-segments the hole geometry out of them and renders
maps + lie masks in the same house style as render.py (600x800 JPEG q82,
75x100 exact-palette PNG mask, tee-bottom/green-top, 8% padded bbox fit).

Input screenshots (not in the repo - Cayden's media library):
  ~/workspace/user/media_library/image/52/...  -> hole 9 (PAR 4)
  ~/workspace/user/media_library/image/20/...  -> hole 8 (PAR 3)
  ~/workspace/user/media_library/image/5a/...  -> hole 7 (PAR 5)
  ~/workspace/user/media_library/image/fc/...  -> hole 6 (PAR 4)
  ~/workspace/user/media_library/image/db/...  -> hole 5 (PAR 3)
  ~/workspace/user/media_library/image/84/...  -> hole 4 (PAR 4)
  ~/workspace/user/media_library/image/b8/...  -> hole 3 (PAR 4)
  ~/workspace/user/media_library/image/4b/...  -> hole 2 (PAR 5)

Geometry only is extracted - no third-party pixels, UI chrome, or textures
are shipped. Hole 2's screenshot had shot-tracer overlays (club labels,
dispersion ellipse); those are inpainted/excluded and the fairway is
hand-traced from a measured centerline. Writes:
  app/assets/hole_maps/ocean-course-kiawah-island/<n>.jpg / <n>_mask.png
and /tmp/kiawah/results.json (tee/pin), which is then merged into the
manifest via render.write_manifest.

Requires: opencv-python-headless, pillow, numpy, scipy.
"""

import json, os
import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

MEDIA = "/home/hatch/workspace/user/media_library"
REPO = "/home/hatch/workspace/golf-companion-app"
ASSETS = os.path.join(REPO, "app", "assets", "hole_maps", "ocean-course-kiawah-island")
QA = "/tmp/kiawah/qa"

HOLES = [
    (9, 4, "image/52/528a5e983cf6092919f27c7f1866c1a37b15bfcc8a9e43edde17f957848d5174.jpg"),
    (8, 3, "image/20/2026e6fb46b8d342f43b54de240449c79fcad3b7e4dc85428000890981146768.jpg"),
    (7, 5, "image/5a/5a6255bc304c6a61bb801e38e4943d282cd476001249ce0f7a5d313d6dd858b8.jpg"),
    (6, 4, "image/fc/fc0456bc3dabb2355307fc23481de6b2fa77bc59499743438b284fd747dd59ea.jpg"),
    (5, 3, "image/db/db8179cf89de771a9365f2eb02b701fceac7fcc598949471c3e88b0f0ecd52e0.jpg"),
    (4, 4, "image/84/8440cd46655fc42211730a25432b28c7dadf34c56cec89466ffd4444ec5f4c37.jpg"),
    (3, 4, "image/b8/b87534ff905e671514d437cef99ada8d70b07c4f27290098b99a3164663eaea4.jpg"),
    (2, 5, "image/4b/4b39f703c721b633799be56dc34da6cba195166a0822b83b265ecde5ab0ed2e7.jpg"),
]

# House palette (must match tools/hole-maps/render.py exactly).
C_ROUGH = (27, 77, 46); C_WATER = (58, 110, 165); C_FAIRWAY = (46, 125, 67)
C_FAIRWAY_EDGE = (30, 90, 48); C_SAND = (232, 216, 160); C_GREEN = (63, 163, 77)
C_GREEN_EDGE = (42, 120, 55); C_TEE = (220, 232, 213)
M_ROUGH = (27, 77, 46); M_FAIRWAY = (46, 125, 67); M_TEE = (240, 240, 235)
M_WATER = (58, 110, 165); M_SAND = (232, 216, 160); M_GREEN = (63, 163, 77)

MAP_W, MAP_H = 600, 800
MASK_W, MASK_H = 75, 100
SSAA = 2

CROP = (0, 460, 1290, 2210)
EXCLUDE = [
    (25, 95, 225, 425),
    (1065, 95, 1265, 325),
    (1065, 1365, 1265, 1625),
    (10, 1600, 180, 1700),
]


def load_crop(path, hole):
    img = Image.open(path).convert("RGB").crop(CROP)
    a = np.array(img)
    for x0, y0, x1, y1 in EXCLUDE:
        a[y0:y1, x0:x1] = (0, 0, 0)
    if hole == 2:
        # Tracer overlays (white line, labels, dispersion ellipse): inpaint
        # them away using surrounding pixels so the fairway stays continuous.
        white = a.min(axis=2) > 210
        white = ndimage.binary_dilation(white, iterations=2)
        a = cv2.inpaint(a, white.astype(np.uint8) * 255, 9,
                        cv2.INPAINT_TELEA)
    return a.astype(np.int16)


# Isolated bright markers (distance posts/bushes) to drop per hole: {hole: [(y_lo,y_hi), ...]}
DROP_BANDS = {
    8: [(950, 1100)],
    5: [(900, 1050)],
    3: [(1050, 1200)],
}


def ellipse_points(cx, cy, rx, ry, angle_deg, n=48):
    a = np.deg2rad(angle_deg)
    pts = []
    for i in range(n):
        t = 2 * np.pi * i / n
        x = rx * np.cos(t); y = ry * np.sin(t)
        pts.append((cx + x * np.cos(a) - y * np.sin(a),
                    cy + x * np.sin(a) + y * np.cos(a)))
    return pts


def segment(hole, par, path, debug=False):
    a = load_crop(path, hole)
    H, W, _ = a.shape
    R, G, B = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    tan = (R > 150) & ((R - B) > 40) & (G > 140)
    blue = ((B - R) > 30) & (B > 100)
    bright = (G > 110) & ((G - R) > 10) & ((G - B) > 10) & ~tan & ~blue
    bright = ndimage.binary_opening(bright, structure=np.ones((3, 3)))
    tan = ndimage.binary_opening(tan, structure=np.ones((3, 3)))
    blue = ndimage.binary_opening(blue, structure=np.ones((3, 3)))
    lab, n = ndimage.label(bright)
    comps = []
    for i in range(1, n + 1):
        m = lab == i
        if m.sum() < 300:
            continue
        m = ndimage.binary_fill_holes(m)
        ys, xs = np.where(m)
        comps.append({"mask": m, "area": int(m.sum()),
                      "cx": float(xs.mean()), "cy": float(ys.mean()),
                      "ymin": int(ys.min()), "ymax": int(ys.max())})
    if not comps:
        raise RuntimeError("hole %d: no geometry found" % hole)
    # Drop known isolated markers.
    for y_lo, y_hi in DROP_BANDS.get(hole, []):
        comps = [c for c in comps if not (y_lo < c["cy"] < y_hi)]
    if debug:
        for c in sorted(comps, key=lambda c: -c["area"])[:12]:
            print("   area %6d cx %7.1f cy %7.1f ymin %5d ymax %5d" %
                  (c["area"], c["cx"], c["cy"], c["ymin"], c["ymax"]))
    big = [c for c in comps if c["area"] >= 1500]
    green = min(big or comps, key=lambda c: c["cy"])
    rest = [c for c in comps if c is not green]
    if par == 3:
        # No fairway on par 3s: every sizable bright blob is a tee box.
        tees = [c for c in rest if c["area"] >= 2500]
        fairways = []
    else:
        fairways = [c for c in rest if c["area"] >= 15000]
        fw_ids = {id(c) for c in fairways}
        fymax = max([c["ymax"] for c in fairways]) if fairways else 0
        # Tee boxes sit below the fairway with a visible gap.
        tees = [c for c in rest if id(c) not in fw_ids
                and c["area"] >= 700 and c["ymin"] > fymax - 30]
    # (dicts contain arrays; compare by identity)
    t_ids = {id(c) for c in tees}
    tees = [c for c in rest if id(c) in t_ids]

    def comps_of(m, min_area):
        lab2, n2 = ndimage.label(m)
        out = []
        for i in range(1, n2 + 1):
            mm = lab2 == i
            if mm.sum() >= min_area:
                out.append(ndimage.binary_fill_holes(mm))
        return out
    return {"green": [green["mask"]], "fairway": [c["mask"] for c in fairways],
            "tee": [c["mask"] for c in tees],
            "bunker": comps_of(tan, 150), "water": comps_of(blue, 150)}


def trace_hole2():
    """Hand-guided trace for hole 2 (tracer overlays defeat segmentation)."""
    path = os.path.join(MEDIA, HOLES[7][2])
    seg = segment(2, 5, path)
    # Green: fitted ellipse to the measured green (robust to the label bite).
    gm = seg["green"][0].astype(np.uint8) * 255
    cnts, _ = cv2.findContours(gm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cnt = max(cnts, key=cv2.contourArea)
    (cx, cy), (MA, ma), ang = cv2.fitEllipse(cnt)
    green = [ellipse_points(cx, cy, MA / 2 * 1.12, ma / 2 * 1.12, ang)]
    # Fairway: hand-defined centerline (measured from the GPS render) with
    # a width profile; the tracer overlays destroyed too much of the
    # measured strip to interpolate across the dogleg cleanly.
    centerline = [(540, 450), (580, 650), (630, 850), (615, 1050),
                  (570, 1250), (535, 1380)]
    widths = [100, 110, 115, 115, 110, 100]
    # Subdivide smoothly (Catmull-Rom).
    pts = []
    ext = [centerline[0]] + centerline + [centerline[-1]]
    for i in range(1, len(ext) - 2):
        p0, p1, p2, p3 = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]
        for t in np.linspace(0, 1, 24, endpoint=False):
            t2, t3 = t * t, t * t * t
            x = 0.5 * (2 * p1[0] + (-p0[0] + p2[0]) * t +
                       (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 +
                       (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * (2 * p1[1] + (-p0[1] + p2[1]) * t +
                       (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 +
                       (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            w = widths[i - 1] + (widths[i] - widths[i - 1]) * t
            pts.append((x, y, w))
    pts.append((centerline[-1][0], centerline[-1][1], widths[-1]))
    left, right = [], []
    for j, (x, y, w) in enumerate(pts):
        j0 = max(0, j - 1); j1 = min(len(pts) - 1, j + 1)
        dx = pts[j1][0] - pts[j0][0]; dy = pts[j1][1] - pts[j0][1]
        L = np.hypot(dx, dy) or 1.0
        nx, ny = -dy / L, dx / L
        left.append((x + nx * w / 2, y + ny * w / 2))
        right.append((x - nx * w / 2, y - ny * w / 2))
    fairway = [left + right[::-1]]
    # Bunkers: measured (clean). Water: measured, closed to heal label bite;
    # drop tiny fragments.
    bunkers = []
    for mm in seg["bunker"]:
        bunkers.extend(mask_to_polys(mm))
    waters = []
    wunion = np.zeros(seg["water"][0].shape, bool)
    for mm in seg["water"]:
        if mm.sum() >= 3000:
            wunion |= mm
    if wunion.any():
        # The "Dr" label split the water in two; heal with a big closing.
        wunion = ndimage.binary_closing(wunion, structure=np.ones((61, 61)))
        waters.extend(mask_to_polys(wunion))
    # Tees: hand-placed ellipses over the visible tee boxes.
    tees = [ellipse_points(520, 1530, 62, 78, -12),
            ellipse_points(680, 1452, 52, 62, 8)]
    return {"green": green, "fairway": fairway, "tee": tees,
            "bunker": bunkers, "water": waters}


def mask_to_polys(m):
    u8 = m.astype(np.uint8) * 255
    cnts, _ = cv2.findContours(u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    polys = []
    for c in cnts:
        if cv2.contourArea(c) < 200:
            continue
        approx = cv2.approxPolyDP(c, 1.5, True).reshape(-1, 2)
        if len(approx) >= 3:
            polys.append([(float(x), float(y)) for x, y in approx])
    return polys


def poly_centroid(pts):
    a2 = cx = cy = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]; x2, y2 = pts[(i + 1) % n]
        cr = x1 * y2 - x2 * y1
        a2 += cr; cx += (x1 + x2) * cr; cy += (y1 + y2) * cr
    if abs(a2) < 1e-9:
        return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)
    return (cx / (3 * a2), cy / (3 * a2))


class Fit:
    def __init__(self, polys_by_kind):
        xs, ys = [], []
        for polys in polys_by_kind.values():
            for p in polys:
                xs.extend(x for x, y in p); ys.extend(y for x, y in p)
        minx, maxx = min(xs), max(xs); miny, maxy = min(ys), max(ys)
        padx = (maxx - minx) * 0.08 + 1e-6; pady = (maxy - miny) * 0.08 + 1e-6
        self.minx, self.maxx = minx - padx, maxx + padx
        self.miny, self.maxy = miny - pady, maxy + pady
        spanx = self.maxx - self.minx; spany = self.maxy - self.miny
        self.scale = min(MAP_W / spanx, MAP_H / spany)
        self.ox = (MAP_W - spanx * self.scale) / 2
        self.oy = (MAP_H - spany * self.scale) / 2

    def norm(self, x, y):
        ix = (x - self.minx) * self.scale + self.ox
        iy = (y - self.miny) * self.scale + self.oy
        return ix / MAP_W, iy / MAP_H

    def px(self, x, y, w, h):
        nx, ny = self.norm(x, y)
        return nx * w, ny * h


def render(number, polys, fit):
    W, H = MAP_W * SSAA, MAP_H * SSAA
    img = Image.new("RGB", (W, H), C_ROUGH)
    d = ImageDraw.Draw(img)

    def P(poly):
        return [fit.px(x, y, W, H) for x, y in poly]

    for poly in polys["fairway"]:
        d.polygon(P(poly), fill=C_FAIRWAY, outline=C_FAIRWAY_EDGE, width=2 * SSAA)
    for poly in polys["tee"]:
        d.polygon(P(poly), fill=C_TEE)
    for poly in polys["water"]:
        d.polygon(P(poly), fill=C_WATER)
    for poly in polys["bunker"]:
        d.polygon(P(poly), fill=C_SAND)
    for poly in polys["green"]:
        d.polygon(P(poly), fill=C_GREEN, outline=C_GREEN_EDGE, width=2 * SSAA)
    d.rectangle([8 * SSAA, 8 * SSAA, 44 * SSAA, 40 * SSAA], fill=(20, 40, 26))
    d.text((26 * SSAA, 24 * SSAA), str(number), fill=(255, 255, 255),
           font=ImageFont.load_default(), anchor="mm")
    return img.resize((MAP_W, MAP_H), Image.LANCZOS)


def render_mask(polys, fit):
    img = Image.new("RGB", (MASK_W, MASK_H), M_ROUGH)
    d = ImageDraw.Draw(img)
    pal = {"fairway": M_FAIRWAY, "tee": M_TEE, "water": M_WATER,
           "bunker": M_SAND, "green": M_GREEN}
    for kind in ["fairway", "tee", "water", "bunker", "green"]:
        for poly in polys[kind]:
            d.polygon([fit.px(x, y, MASK_W, MASK_H) for x, y in poly],
                      fill=pal[kind])
    return img


def main():
    os.makedirs(ASSETS, exist_ok=True)
    os.makedirs(QA, exist_ok=True)
    results = []
    for hole, par, rel in HOLES:
        path = os.path.join(MEDIA, rel)
        print("hole %d components:" % hole)
        if hole == 2:
            polys = trace_hole2()
        else:
            seg = segment(hole, par, path, debug=True)
            polys = {k: [p for m in seg[k] for p in mask_to_polys(m)]
                     for k in ["fairway", "tee", "water", "bunker", "green"]}
        fit = Fit(polys)
        if polys["tee"]:
            # Back (bottom-most) tee box: the visual tee marker should sit on
            # a tee box, not on rough between boxes.
            bt = max(polys["tee"], key=lambda p: poly_centroid(p)[1])
            tx, ty = poly_centroid(bt)
        else:
            fpts = [pt for p in polys["fairway"] for pt in p]
            tx = sum(x for x, y in fpts) / len(fpts)
            ty = max(y for x, y in fpts)
        gx, gy = poly_centroid(max(polys["green"], key=lambda p: abs(sum(
            p[i][0] * p[(i + 1) % len(p)][1] - p[(i + 1) % len(p)][0] * p[i][1]
            for i in range(len(p)))) / 2.0))
        tee_n = fit.norm(tx, ty); pin_n = fit.norm(gx, gy)
        assert tee_n[1] > pin_n[1], "hole %d orientation violated" % hole
        mapp = render(hole, polys, fit)
        mask = render_mask(polys, fit)
        mapp.save(os.path.join(ASSETS, "%d.jpg" % hole), "JPEG", quality=82)
        mask.save(os.path.join(ASSETS, "%d_mask.png" % hole), "PNG")
        mapp.save(os.path.join(QA, "map_%d.jpg" % hole), quality=85)
        mask.resize((150, 200), Image.NEAREST).save(
            os.path.join(QA, "mask_%d.png" % hole))
        print("hole %d par %d polys %s tee=(%.3f,%.3f) pin=(%.3f,%.3f)" %
              (hole, par, {k: len(v) for k, v in polys.items()},
               tee_n[0], tee_n[1], pin_n[0], pin_n[1]))
        results.append({"number": hole, "par": par, "source": "synthetic",
                        "tee": [round(tee_n[0], 3), round(tee_n[1], 3)],
                        "pin": [round(pin_n[0], 3), round(pin_n[1], 3)]})
    with open("/tmp/kiawah/results.json", "w") as f:
        json.dump(results, f, indent=1)


if __name__ == "__main__":
    main()
