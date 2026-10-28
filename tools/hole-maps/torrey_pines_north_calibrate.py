#!/usr/bin/env python3
"""Calibrate Torrey Pines North yardsPerPixel + yardage markers.

Method (same as the Pinehurst/Riviera screenshot calibrations):
- Per hole, Cayden's Golf+ screenshot gives exact GPS landmarks: the
  tee dot, the pin dot, and the white "from-tee" yardage pills whose
  value is the straight-line distance from the tee to the pill's tip
  (verified on H1: 296 matched to 0.1%). The club panels (Dr 260 etc.)
  are stock club distances, NOT measured segments - sanity only.
- ss_scale (yards per screenshot px) comes from the pills:
  value / dist_screenshot(tee, tip).
- A similarity transform maps screenshot px -> map px via the tee/pin
  pair (screenshot pin dot <-> manifest pin = green centroid), giving
  s = map px per screenshot px. Predicted map scale = ss_scale / s.
- yardsPerPixel decision per hole:
    * predicted within 5% of the standard header / tee-pin-px value
      -> commit the header value (screenshot-verified);
    * header off by more, but predicted agrees with the artwork's own
      metric scale (HoleTransform) within 4% and the pill anchors are
      mutually consistent -> commit the predicted value (the header
      is the outlier: H14's card says 451 but the game's own geometry
      and OSM both measure ~406-409);
    * no pills: commit the header value only when it agrees with the
      artwork's metric scale within 4% (H2/H16/H18);
    * otherwise commit NO yardsPerPixel - the app hides the distance
      readout for that hole (Valhalla H13 precedent).
- Markers: pill tips transformed into map space; yards = straight-line
  distance to the pin measured in the screenshot x ss_scale (the
  Pinehurst convention). Cross-checked against the map-side distance
  x committed yardsPerPixel.

Landmarks below are visual-grounding measurements on the archived
screenshots (0-1000 normalized), 2026-10-28.

Usage: python3 torrey_pines_north_calibrate.py [--apply]
Without --apply it only prints the table.
"""
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render import HoleTransform

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
MANIFEST = os.path.join(REPO, "app", "lib", "widgets", "hole_map_manifest.dart")
HOLES_JSON = os.path.join(HERE, "data", "torrey-pines-north", "holes.json")
SS_W, SS_H = 1290.0, 2796.0  # screenshot pixel size

HEADERS = {1: 421, 2: 495, 3: 241, 4: 479, 5: 525, 6: 416, 7: 322, 8: 214,
           10: 536, 11: 339, 12: 203, 13: 459, 14: 451, 15: 202, 16: 393,
           17: 520, 18: 486}
PANELS = {1: (260, 167), 2: (259, 220), 3: (240,), 4: (253, 214),
          5: (254, 217, 53), 6: (260, 154), 7: (248, 72), 8: (211,),
          10: (264, 238, 31), 11: (246, 90), 12: (201,), 13: (262, 197),
          14: (255, 154), 15: (200,), 16: (253, 137), 17: (263, 244),
          18: (256, 229)}

# hole: tee, pin, diamond (or None), pills {value: tip}
LM = {
    1: dict(tee=(513, 741), pin=(507, 231), dia=(504, 411), pills={296: (450, 381)}),
    2: dict(tee=(463, 740), pin=(470, 246), dia=(515, 454), pills={}),
    3: dict(tee=(501, 730), pin=(507, 264), dia=None, pills={230: (447, 269)}),
    4: dict(tee=(406, 730), pin=(412, 238), dia=(569, 443), pills={264: (551, 441)}),
    # H5's 284 tip and H8's two tips were re-measured by hand on
    # full-res zooms (grounding returned the 284 pill's label centre).
    5: dict(tee=(432, 744), pin=(434, 238), dia=(559, 483), pills={284: (618, 474)}),
    6: dict(tee=(512, 741), pin=(507, 244), dia=(494, 408), pills={272: (449, 410)}),
    7: dict(tee=(508, 738), pin=(509, 249), dia=(508, 335), pills={266: (476, 319), 301: (480, 266)}),
    8: dict(tee=(506, 737), pin=(527, 268), dia=None, pills={192: (463, 292), 196: (583, 283)}),
    10: dict(tee=(509, 744), pin=(509, 237), dia=(502, 472), pills={279: (518, 478)}),
    11: dict(tee=(532, 741), pin=(528, 249), dia=(431, 353), pills={241: (483, 376)}),
    12: dict(tee=(410, 733), pin=(414, 288), dia=None, pills={210: (484, 251)}),
    13: dict(tee=(520, 741), pin=(514, 244), dia=(456, 436), pills={289: (525, 421)}),
    14: dict(tee=(459, 736), pin=(459, 239), dia=(579, 397), pills={}),
    15: dict(tee=(623, 741), pin=(642, 276), dia=None, pills={188: (556, 294), 189: (678, 294)}),
    16: dict(tee=(537, 737), pin=(528, 248), dia=(414, 397), pills={}),
    17: dict(tee=(429, 740), pin=(428, 238), dia=(542, 463), pills={267: (534, 472), 296: (522, 439)}),
    18: dict(tee=(507, 742), pin=(503, 237), dia=(476, 454), pills={}),
}

# Manifest tee/pin (normalized) after the final render.
MAP_TP = {}


def load_map_tp():
    txt = open(MANIFEST).read()
    m = re.search(r"'Torrey Pines North Course': \{(.*?)\n  \},", txt, re.S)
    for line in m.group(1).splitlines():
        mm = re.match(r"\s+(\d+): HoleMapAsset\(par: \d+, .*tee: Offset\(([0-9.]+), ([0-9.]+)\), pin: Offset\(([0-9.]+), ([0-9.]+)\)", line)
        if mm:
            MAP_TP[int(mm.group(1))] = ((float(mm.group(2)), float(mm.group(3))),
                                        (float(mm.group(4)), float(mm.group(5))))


def ss_px(pt):
    return (pt[0] / 1000.0 * SS_W, pt[1] / 1000.0 * SS_H)


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def similarity(ss_tee, ss_pin, map_tee, map_pin):
    """Return (s, R, t): map_px = s * R @ ss_px + t (complex method)."""
    import numpy as np
    A = np.array([ss_pin, ss_tee], dtype=float)
    B = np.array([map_pin, map_tee], dtype=float)
    a_c, b_c = A.mean(axis=0), B.mean(axis=0)
    Aa, Bb = A - a_c, B - b_c
    s = np.linalg.norm(Bb) / np.linalg.norm(Aa)
    ac = Aa[:, 0] + 1j * Aa[:, 1]
    bc = Bb[:, 0] + 1j * Bb[:, 1]
    theta = np.angle((bc / (s * ac)).mean())
    R = np.array([[np.cos(theta), -np.sin(theta)],
                  [np.sin(theta), np.cos(theta)]])
    t = b_c - s * R @ a_c
    return s, R, t


def main():
    apply = "--apply" in sys.argv
    load_map_tp()
    holes = {h["number"]: h for h in json.load(open(HOLES_JSON))["holes"]}
    results = {}
    print(f"{'H':>3} {'hdr':>4} {'ypp_hdr':>8} {'ypp_true':>8} {'ss_scale':>9} "
          f"{'ypp_pred':>8} {'pred/hdr':>8} {'pred/true':>9}  decision")
    for n in sorted(LM):
        lm = LM[n]
        tee, pin = ss_px(lm["tee"]), ss_px(lm["pin"])
        (mtx, mty), (mpx, mpy) = MAP_TP[n]
        map_tee = (mtx * 600, mty * 800)
        map_pin = (mpx * 600, mpy * 800)
        s, R, t = similarity(tee, pin, map_tee, map_pin)
        import numpy as np

        def to_map(pt_ss):
            v = s * R @ np.array(pt_ss) + t
            return (float(v[0]), float(v[1]))

        ypp_hdr = HEADERS[n] / dist(map_tee, map_pin)
        ypp_true = 1.09361 / HoleTransform(holes[n]).scale

        # Pill anchors, with a tip-side rescue: if the returned point
        # implies a scale >12% off the rough expectation, try shifting
        # the tip 60 normalized units toward the hole axis in x.
        rough = HEADERS[n] / dist(tee, pin)
        anchors = []
        pill_notes = []
        for value, tipn in sorted(lm["pills"].items()):
            tip = ss_px(tipn)
            sc = value / dist(tee, tip)
            used = tip
            if abs(sc / rough - 1) > 0.12:
                for shift in (-60, 60):
                    alt = (tip[0] + shift / 1000.0 * SS_W, tip[1])
                    sc_alt = value / dist(tee, alt)
                    if abs(sc_alt / rough - 1) < abs(sc / rough - 1):
                        tip, sc, used = alt, sc_alt, alt
                pill_notes.append("pill %d tip adjusted" % value)
            anchors.append((value, used, sc))
        ss_scale = None
        if anchors:
            ss_scale = sum(a[2] for a in anchors) / len(anchors)
        ypp_pred = (ss_scale / s) if ss_scale else None

        decision, ypp_commit = "HIDE", None
        if anchors:
            spread = (max(a[2] for a in anchors) / min(a[2] for a in anchors) - 1) if len(anchors) > 1 else 0.0
            if spread <= 0.04 and abs(ypp_pred / ypp_hdr - 1) <= 0.05:
                decision, ypp_commit = "header (pill-verified)", ypp_hdr
            elif spread <= 0.04 and abs(ypp_pred / ypp_true - 1) <= 0.04:
                decision, ypp_commit = "PREDICTED (header outlier)", ypp_pred
            elif (spread <= 0.01 and abs(ypp_hdr / ypp_true - 1) <= 0.02
                    and abs(ypp_pred / ypp_hdr - 1) <= 0.07):
                # H8: two pills agree to 0.15% and the artwork's own
                # metric scale corroborates the header within 1%, but
                # the prediction is biased ~6% - the transform anchors
                # on the game's pin (back of this short hole's green)
                # vs the map's green centroid, a ~5% effect at 214 yd.
                decision, ypp_commit = "header (pills consistent, pin-offset pred)", ypp_hdr
        else:
            if abs(ypp_hdr / ypp_true - 1) <= 0.04:
                decision, ypp_commit = "header (geometry x-check)", ypp_hdr
        markers = []
        if anchors and ypp_commit:
            for value, tip, sc in anchors:
                mp = to_map(tip)
                yards_ss = dist(tip, pin) * ss_scale
                yards_map = dist(mp, map_pin) * ypp_commit
                markers.append({"offset": [round(mp[0] / 600, 3), round(mp[1] / 800, 3)],
                                "yards": int(round(yards_ss)),
                                "check_map_yards": round(yards_map, 1)})
        results[n] = dict(ypp=ypp_commit, decision=decision, markers=markers,
                          ypp_hdr=ypp_hdr, ypp_true=ypp_true, ypp_pred=ypp_pred)
        print(f"{n:>3} {HEADERS[n]:>4} {ypp_hdr:>8.4f} {ypp_true:>8.4f} "
              f"{(ss_scale if ss_scale else float('nan')):>9.4f} "
              f"{(ypp_pred if ypp_pred else float('nan')):>8.4f} "
              f"{(ypp_pred / ypp_hdr if ypp_pred else float('nan')):>8.3f} "
              f"{(ypp_pred / ypp_true if ypp_pred else float('nan')):>9.3f}  "
              f"{decision}" + ("; " + "; ".join(pill_notes) if pill_notes else ""))
        for mk in markers:
            print(f"      marker {mk['yards']}y at {mk['offset']} "
                  f"(map-side check {mk['check_map_yards']}y)")

    if not apply:
        print("\n(dry run - rerun with --apply to write the manifest)")
        return
    txt = open(MANIFEST).read()
    for n, r in sorted(results.items()):
        if not r["ypp"]:
            continue
        pat = re.compile(
            r"(%d: HoleMapAsset\(par: \d+, map: 'assets/hole_maps/torrey-pines-north/%d\.jpg', "
            r"mask: 'assets/hole_maps/torrey-pines-north/%d_mask\.png', tee: Offset\([0-9.]+, [0-9.]+\)"
            r", pin: Offset\([0-9.]+, [0-9.]+\)"
            r", source: '\w+'\),)" % (n, n, n))
        m = pat.search(txt)
        assert m, "manifest pattern not found for hole %d" % n
        tail = ", yardsPerPixel: %.4f" % r["ypp"]
        if r["markers"]:
            mks = ", ".join(
                "YardageMarker(offset: Offset(%.3f, %.3f), yards: %d)"
                % (mk["offset"][0], mk["offset"][1], mk["yards"])
                for mk in r["markers"])
            tail += ", yardages: const [%s]" % mks
        txt = txt.replace(m.group(1), m.group(1)[:-2] + tail + "),")
        print("applied H%d: %s%s" % (n, r["decision"], tail))
    open(MANIFEST, "w").write(txt)
    print("manifest updated")


if __name__ == "__main__":
    main()
