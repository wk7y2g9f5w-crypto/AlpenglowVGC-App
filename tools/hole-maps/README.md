# Realistic hole maps pipeline

Generates stylized flat-vector bird's-eye hole layouts for the app's shot
tracker, derived from OpenStreetMap (OSM) golf data. Replaces the
procedural schematics with recognizable real hole geometry: correct
dogleg direction, green at the right end, real fairway / green / bunker /
water / tee shapes.

Two phases:

- **`extract.py` — Phase 1: fetch + extract.** For each course, fetches OSM
  XML via the main API (`/api/0.6/map?bbox=…`; Overpass was flaky, the main
  API works), finds the course boundary (`leisure=golf_course`), parses
  `golf=hole|fairway|green|bunker|tee|water_hazard` and `natural=water`
  (ways, nodes, and multipolygon relations), keeps features inside the
  boundary, and clusters them to holes by nearest centerline in local
  meters. Thin holes get synthesized geometry (tapered fairway along the
  real centerline, green ellipse, seeded bunkers, tee boxes) in the same
  flat style. Holes with no OSM centerline at all get no image — the app
  falls back to its procedural schematic.
- **`render.py` — Phase 2: render.** Reads each `holes.json` and renders a
  600×800 JPEG map (q82) plus a 75×100 PNG lie mask per hole, using an
  identical transform for both (rotate tee→green straight up, fit +8%
  padding, letterbox). Writes `app/assets/hole_maps/<slug>/`,
  `data/manifest.json`, the generated
  `app/lib/widgets/hole_map_manifest.dart`, and a per-course
  `contact_sheet.png` QA grid.

## Usage

```bash
cd tools/hole-maps
python3 extract.py [--force] [--courses slug ...] [--list] [--no-refetch]
python3 render.py  [--force] [--courses slug ...]
```

- Extraction is deterministic (sorted ids, seeded RNG) and resumable:
  courses with a complete `holes.json` are skipped unless `--force`.
- Be polite to OSM: requests are sequential with 1.5s sleeps; raw XML is
  cached under `data/raw/` and never re-fetched unless forced.
- `render.py` aborts loudly (non-zero exit) on missing pars, orientation
  invariant violations (tee must be below green in image space), pins not
  landing on green, or a failed Sawgrass-17 island-green canary.

## Per-hole `source`

- `osm` — full real OSM data (fairway + green + tees, no synthesis).
- `mixed` — real centerline, some features synthesized (thin OSM data).
- `synthetic` — centerline real, all features synthesized from par.

Seeded bunkers are only added to `mixed`/`synthetic` holes (never invented
on top of complete real data). Water is never synthesized.

## Licensing (OSM)

- Everything under `tools/hole-maps/data/` (raw XML, `holes.json`,
  `qa.txt`, contact sheets) is **derived OSM vector data** and is
  **gitignored — never commit it**.
- Committed: pipeline code (`*.py`, this README), the rendered
  **images** (OSM "Produced Work": attribution only — the app shows the
  © OpenStreetMap contributors caption), and the generated manifest.
- If you re-render, attribution stays with the images; do not ship the
  intermediate vectors.

## Course notes / gotchas

- **TPC Sawgrass**: hole ways are tagged `Stadium N` vs `Valley N`
  (Dye's Valley). `hole_name_preferred="stadium"` picks the Stadium
  Course; the hole-17 island-green canary guards this.
- **Olympia Fields**: North + South share one boundary and hole ways carry
  no course tags. `keep_northern_hole_cluster=True` keeps the northern
  18-way cluster (par sums confirm: north=70, south=72).
- **TPC Southwind / Riviera**: dense suburbs hit the map API node limit;
  `tile_fetch=True` tiles the bbox and merges.
- **Pinehurst No. 2**: refs look like `"1 - #2"`; the ref parser takes the
  first 1–18 integer.
- **Boundary re-fetch merges** with the initial bbox (never replaces), so
  holes outside the fitted bbox are not lost (cf. Kiawah).
- **Wolf Creek**: OSM hole ways lack par tags; pars are overridden from
  the published scorecard (cross-checked on two sites, 2026-10-03).
- Tee/green/bunker **nodes** (not just ways) are honored — e.g. Yale's 70
  tee boxes are nodes.
