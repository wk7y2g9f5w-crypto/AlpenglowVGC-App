#!/usr/bin/env python3
"""Calibrate Torrey Pines South yardsPerPixel + yardage markers.

Method (same as the Pinehurst/Riviera screenshot calibrations and the
Torrey Pines North calibration, torrey_pines_north_calibrate.py):
- Per hole, Cayden's Golf+ screenshot gives exact GPS landmarks: the
  tee dot, the pin dot, and the white "from-tee" yardage pills whose
  value is the straight-line distance from the tee to the pill's tip.
  The club panels (Dr 266 etc.) are stock club distances, NOT measured
  segments - sanity only.
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
      is the outlier);
    * pill anchors mutually consistent (spread <= 1%), header agrees
      with the artwork's metric scale within 2%, and predicted within
      7% of header -> commit the header value (the prediction is
      biased by the game's pin dot sitting off the green centroid -
      the par-3 pattern, cf. North H8);
    * no pills: commit the header value only when it agrees with the
      artwork's metric scale within 4% (H15, whose two greenside
      bunker centres were also transform-checked onto the artwork's
      sand as a geometry cross-check);
    * otherwise commit NO yardsPerPixel - the app hides the distance
      readout for that hole (Valhalla H13 precedent).
- Markers: pill tips transformed into map space; yards = straight-line
  distance to the pin measured in the screenshot x ss_scale (the
  Pinehurst convention). Cross-checked against the map-side distance
  x committed yardsPerPixel.

Landmarks below are visual-grounding measurements on the archived
screenshots (0-1000 normalized), 2026-10-28. H3/H8/H11/H16 pins and
tips were re-measured by hand on full-res zooms (grounding returned
off points for those greens).

Usage: python3 torrey_pines_south_calibrate.py [--apply]
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
HOLES_JSON = os.path.join(HERE, "data", "torrey-pines-south", "holes.json")
SS_W, SS_H = 1290.0, 2796.0  # screenshot pixel size

HEADERS = {1: 451, 2: 389, 3: 201, 4: 490, 5: 454, 6: 564, 7: 462, 8: 177,
           9: 615, 10: 454, 11: 225, 12: 505, 13: 621, 14: 437, 15: 517,
           16: 227, 17: 443, 18: 570}

# hole: tee, pin, pills {value: tip}
LM = {
    1: dict(tee=(552, 744), pin=(541, 244), pills={312: (517, 387), 287: (446, 419), 273: (517, 431)}),
    2: dict(tee=(551, 740), pin=(548, 239), pills={257: (532, 397)}),
    3: dict(tee=(545, 734), pin=(552, 264), pills={185: (481, 274)}),
    4: dict(tee=(488, 742), pin=(489, 238), pills={305: (516, 421), 272: (534, 454)}),
    5: dict(tee=(547, 738), pin=(533, 237), pills={277: (437, 426), 273: (524, 426)}),
    6: dict(tee=(584, 748), pin=(582, 241), pills={306: (406, 469), 282: (402, 494)}),
    7: dict(tee=(572, 740), pin=(561, 243), pills={300: (396, 432)}),
    8: dict(tee=(484, 733), pin=(517, 285), pills={185: (474, 234), 164: (474, 288)}),
    9: dict(tee=(498, 741), pin=(500, 244), pills={302: (470, 494), 296: (524, 499)}),
    10: dict(tee=(489, 735), pin=(495, 236), pills={271: (474, 428)}),
    11: dict(tee=(502, 732), pin=(502, 255), pills={214: (437, 268), 203: (535, 288)}),
    12: dict(tee=(524, 740), pin=(537, 244), pills={302: (447, 434), 274: (510, 459)}),
    13: dict(tee=(532, 739), pin=(533, 239), pills={293: (553, 464), 255: (537, 500), 241: (481, 514)}),
    14: dict(tee=(462, 742), pin=(461, 243), pills={268: (485, 424)}),
    15: dict(tee=(505, 738), pin=(505, 228), pills={}),
    16: dict(tee=(512, 736), pin=(515, 269), pills={212: (442, 273), 207: (570, 282)}),
    17: dict(tee=(472, 737), pin=(461, 230), pills={295: (529, 394), 263: (530, 431)}),
    18: dict(tee=(527, 744), pin=(530, 237), pills={274: (466, 498)}),
}

# Manifest tee/pin (normalized) after the final render.
MAP_TP = {}


def load_map_tp():
    txt = open(MANIFEST).read()
    m = re.search(r"'Torrey Pines South Course': \{(.*?)\n  \},", txt, re.S)
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
                # Par-3 pattern (cf. North H8): pills agree tightly and
                # the artwork's own metric scale corroborates the
                # header, but the prediction is biased - the transform
                # anchors on the game's pin dot (off the green
                # centroid on a short hole), a several-% effect.
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
            r"(%d: HoleMapAsset\(par: \d+, map: 'assets/hole_maps/torrey-pines-south/%d\.jpg', "
            r"mask: 'assets/hole_maps/torrey-pines-south/%d_mask\.png', tee: Offset\([0-9.]+, [0-9.]+\)"
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
