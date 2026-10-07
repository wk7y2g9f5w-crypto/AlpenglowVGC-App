#!/usr/bin/env python3
"""Update Pinehurst No. 2 manifest: fixes + yardsPerPixel + markers.

Reads markers from markers.json (produced by subagents).
Updates:
- H1: tee -> (0.530, 0.914)
- H10: pin -> (0.436, 0.150)
- H16: tee -> (0.605, 0.910), pin -> (0.606, 0.112)
- yardsPerPixel for Tier A/B holes (2,3,6,8,9,13,14,15,16,17)
- yardages markers for those holes
"""
import json
import re

MANIFEST = '/home/hatch/workspace/golf-companion-app/app/lib/widgets/hole_map_manifest.dart'
MARKERS = '/home/hatch/workspace/golf-companion-app/tools/hole-maps/markers.json'

# yardsPerPixel values (computed as header / map_tee_pin_px)
YPP = {
    2: 0.8013, 3: 0.6047, 6: 0.4224, 8: 0.8244, 9: 0.3585,
    13: 0.6250, 14: 0.8258, 15: 0.3464, 16: 0.8459, 17: 0.3810,
}

# Hole 2 markers (computed manually)
H2_MARKERS = [
    {'offset': [0.416, 0.464], 'yards': 205},
    {'offset': [0.405, 0.510], 'yards': 233},
    {'offset': [0.405, 0.548], 'yards': 254},
]

def format_marker(m):
    x, y = m['offset']
    return f'YardageMarker(offset: Offset({x:.3f}, {y:.3f}), yards: {m["yards"]})'

def main():
    with open(MANIFEST) as f:
        content = f.read()
    
    with open(MARKERS) as f:
        markers = json.load(f)
    # markers keys are strings; convert to int
    markers = {int(k): v for k, v in markers.items()}
    markers[2] = H2_MARKERS
    
    # 1. H1 tee
    content = content.replace(
        "1: HoleMapAsset(par: 4, map: 'assets/hole_maps/pinehurst-no-2/1.jpg', mask: 'assets/hole_maps/pinehurst-no-2/1_mask.png', tee: Offset(0.561, 0.798),",
        "1: HoleMapAsset(par: 4, map: 'assets/hole_maps/pinehurst-no-2/1.jpg', mask: 'assets/hole_maps/pinehurst-no-2/1_mask.png', tee: Offset(0.530, 0.914),"
    )
    
    # 2. H10 pin
    content = content.replace(
        "10: HoleMapAsset(par: 5, map: 'assets/hole_maps/pinehurst-no-2/10.jpg', mask: 'assets/hole_maps/pinehurst-no-2/10_mask.png', tee: Offset(0.427, 0.908), pin: Offset(0.426, 0.178),",
        "10: HoleMapAsset(par: 5, map: 'assets/hole_maps/pinehurst-no-2/10.jpg', mask: 'assets/hole_maps/pinehurst-no-2/10_mask.png', tee: Offset(0.427, 0.908), pin: Offset(0.436, 0.150),"
    )
    
    # 3. H16 tee/pin
    content = content.replace(
        "16: HoleMapAsset(par: 5, map: 'assets/hole_maps/pinehurst-no-2/16.jpg', mask: 'assets/hole_maps/pinehurst-no-2/16_mask.png', tee: Offset(0.605, 0.890), pin: Offset(0.686, 0.319),",
        "16: HoleMapAsset(par: 5, map: 'assets/hole_maps/pinehurst-no-2/16.jpg', mask: 'assets/hole_maps/pinehurst-no-2/16_mask.png', tee: Offset(0.605, 0.910), pin: Offset(0.606, 0.112),"
    )
    
    # 3b. H8 tee/pin x-fix (worker found manifest x=0.801/0.803 but artwork at x~0.53)
    content = content.replace(
        "8: HoleMapAsset(par: 5, map: 'assets/hole_maps/pinehurst-no-2/8.jpg', mask: 'assets/hole_maps/pinehurst-no-2/8_mask.png', tee: Offset(0.801, 0.901), pin: Offset(0.803, 0.152),",
        "8: HoleMapAsset(par: 5, map: 'assets/hole_maps/pinehurst-no-2/8.jpg', mask: 'assets/hole_maps/pinehurst-no-2/8_mask.png', tee: Offset(0.528, 0.901), pin: Offset(0.530, 0.152),"
    )
    
    # 4. Add yardsPerPixel and markers for Tier A/B holes
    for n in sorted(YPP.keys()):
        ypp = YPP[n]
        mlist = markers.get(n, [])
        if mlist:
            yard_str = ', '.join(format_marker(m) for m in mlist)
            suffix = f", yardsPerPixel: {ypp:.4f}, yardages: const [{yard_str}]),"
        else:
            suffix = f", yardsPerPixel: {ypp:.4f}),"
        
        # Find and replace the hole entry
        # Pattern: "<n>: HoleMapAsset(par: X, map: '...', mask: '...', tee: Offset(...), pin: Offset(...), source: '...'),"
        pat = rf"({n}: HoleMapAsset\(par: \d+, map: 'assets/hole_maps/pinehurst-no-2/{n}\.jpg', mask: 'assets/hole_maps/pinehurst-no-2/{n}_mask\.png', tee: Offset\([\d.]+, [\d.]+\), pin: Offset\([\d.]+, [\d.]+\), source: '\w+'\),)"
        m = re.search(pat, content)
        if m:
            old = m.group(1)
            # Remove trailing ), and add suffix
            new = old[:-2] + suffix
            content = content.replace(old, new)
            print(f'Hole {n}: added ypp={ypp:.4f}, {len(mlist)} markers')
        else:
            print(f'Hole {n}: PATTERN NOT FOUND!')
    
    with open(MANIFEST, 'w') as f:
        f.write(content)
    print('Manifest updated.')

if __name__ == '__main__':
    main()
