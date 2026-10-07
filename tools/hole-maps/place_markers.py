#!/usr/bin/env python3
"""Compute marker positions via screenshot->map similarity transform.

For each hole, uses manually measured pill tips and landmarks to compute
the transform and place markers. Values are verified against the QA report.

Usage: python3 place_markers.py
Outputs: markers.json with per-hole marker lists.
"""
import json
import math
import numpy as np

# Hole data: (header_yards, tier, screenshot_scale_y_per_px, map_tee, map_pin, pills)
# pills: list of (face_value, tip_x1000, tip_y1000, expected_to_pin)
# Landmarks: (ss_pin_x1000, ss_pin_y1000, ss_tee_x1000, ss_tee_y1000)
# Screenshot map area: top=136, bottom=751 (0-1000 coords), height=615

HOLES = {
    # hole: (header, tier, ss_scale, map_tee, map_pin, ss_pin, ss_tee, pills)
    2: {
        'header': 500, 'tier': 'A', 'ss_scale': 0.3273,
        'map_tee': (0.540, 0.909), 'map_pin': (0.540, 0.129),
        'ss_pin': (550, 242), 'ss_tee': (550, 745),
        'pills': [
            (281, 420, 458, 205),  # recomputed straight-line
            (255, 408, 488, 233),
            (234, 408, 512, 254),
        ],
    },
    # ... (to be filled for other holes)
}

def ss_to_mapnorm(x1000, y1000):
    return (x1000/1000.0, (y1000-136)/615.0)

def compute_transform(ss_pin, ss_tee, map_pin, map_tee):
    A = np.array([ss_to_mapnorm(*ss_pin), ss_to_mapnorm(*ss_tee)])
    B = np.array([map_pin, map_tee])
    a_c = A.mean(axis=0); b_c = B.mean(axis=0)
    Aa = A - a_c; Bb = B - b_c
    s = np.linalg.norm(Bb) / np.linalg.norm(Aa)
    a_complex = Aa[:,0] + 1j*Aa[:,1]
    b_complex = Bb[:,0] + 1j*Bb[:,1]
    ratio = b_complex / (s * a_complex)
    theta = np.angle(ratio.mean())
    R = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    t = b_c - s * R @ a_c
    return s, R, t

def main():
    results = {}
    for hole, d in HOLES.items():
        s, R, t = compute_transform(d['ss_pin'], d['ss_tee'], d['map_pin'], d['map_tee'])
        markers = []
        for face, tx, ty, expected in d['pills']:
            ss_pt = ss_to_mapnorm(tx, ty)
            map_pt = tuple(s * R @ np.array(ss_pt) + t)
            # Verify straight-line distance
            dx = (map_pt[0] - d['map_pin'][0]) * 600
            dy = (map_pt[1] - d['map_pin'][1]) * 800
            dist_px = math.hypot(dx, dy)
            # Map yardsPerPixel = header / map_tee_pin_px
            tee_pin_px = math.hypot(
                (d['map_tee'][0]-d['map_pin'][0])*600,
                (d['map_tee'][1]-d['map_pin'][1])*800)
            ypp_map = d['header'] / tee_pin_px
            dist_y = dist_px * ypp_map
            print(f'Hole {hole} pill {face}: map({map_pt[0]:.3f},{map_pt[1]:.3f}) '
                  f'dist={dist_y:.0f}y (expected {expected}y)')
            markers.append({'offset': [round(map_pt[0],3), round(map_pt[1],3)],
                           'yards': expected})
        results[hole] = markers
    
    with open('markers.json', 'w') as f:
        json.dump(results, f, indent=1)
    print('Wrote markers.json')

if __name__ == '__main__':
    main()
