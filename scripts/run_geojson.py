"""
Run the pipeline on a GeoJSON export, without QGIS.

    python3 scripts/run_geojson.py <file.geojson> --roads 106,108,110

Lets a junction be checked offline, which is far quicker than going back and
forth through the QGIS console. Coordinates in a geographic CRS (lon/lat) are
projected to a local metric frame first, exactly as the QGIS path reprojects
to UTM - the rule engine always works in metres.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yield_pipeline import (DEFAULT_CONFIG, build_junction, evaluate,   # noqa: E402
                            build_yield_to_map, summarise)
from yield_pipeline.extract import ExtractionError                      # noqa: E402


def local_projection(features):
    """Project lon/lat to a local metric frame centred on the data.

    Equirectangular about the centre. Over a junction-sized area the error is
    well under a centimetre, and bearings and distances come out in metres,
    which is all the rule engine needs.
    """
    pts = [p for f in features for p in f["geometry"]["coordinates"]]
    lon0 = sum(p[0] for p in pts) / len(pts)
    lat0 = sum(p[1] for p in pts) / len(pts)
    kx = math.cos(math.radians(lat0)) * 111320.0
    ky = 110540.0

    def to_metres(coord):
        return ((coord[0] - lon0) * kx, (coord[1] - lat0) * ky)

    return to_metres, (lon0, lat0)


def looks_geographic(features) -> bool:
    pts = [p for f in features for p in f["geometry"]["coordinates"][:1]]
    return all(abs(p[0]) <= 180 and abs(p[1]) <= 90 for p in pts)


def load(path, roads=None):
    with open(path) as handle:
        data = json.load(handle)
    feats = [f for f in data["features"]
             if f.get("geometry") and f["geometry"].get("coordinates")]
    if roads:
        want = {str(r) for r in roads}
        keep = [f for f in feats if str(f["properties"].get("road_id")) in want]
        # Regulatory elements carry no road_id, so pull in the ones these
        # lanes actually reference.
        wanted_re = set()
        for f in keep:
            rule = f["properties"].get("traffic_rule")
            if rule:
                wanted_re.update(str(rule).replace(";", ",").split(","))
        wanted_re = {r.strip() for r in wanted_re if r.strip()}
        keep += [f for f in feats
                 if f["properties"].get("re_id") in wanted_re
                 and f not in keep]
        feats = keep
    return feats


def to_pipeline(features, project):
    out = []
    for f in features:
        props = dict(f["properties"])
        geom = f["geometry"]
        coords = geom["coordinates"]
        if geom["type"].startswith("Multi"):
            coords = max(coords, key=len)
        out.append({"attributes": props,
                    "geometry": [project(c) for c in coords]})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("geojson")
    ap.add_argument("--roads", help="comma separated road_id values to keep")
    ap.add_argument("--verbose", action="store_true", default=True)
    args = ap.parse_args()

    roads = args.roads.split(",") if args.roads else None
    feats = load(args.geojson, roads)
    if not feats:
        print("Nothing matched.")
        return 1

    print(f"{len(feats)} feature(s) loaded"
          + (f" for road_id in {roads}" if roads else ""))

    if looks_geographic(feats):
        project, origin = local_projection(feats)
        print(f"Coordinates are lon/lat; projecting to a local metric frame "
              f"centred on {origin[0]:.6f}, {origin[1]:.6f}")
    else:
        project = lambda c: (c[0], c[1])        # noqa: E731
        print("Coordinates already look metric; using them unchanged")

    cfg = DEFAULT_CONFIG
    try:
        junction = build_junction(to_pipeline(feats, project), cfg)
    except ExtractionError as exc:
        print(f"\nExtraction stopped: {exc}")
        return 1

    result = evaluate(junction, cfg)
    print()
    print(summarise(result, cfg))

    if args.verbose:
        print("\nHow each movement was read")
        print("  start/end = metres right of the arm axis looking outward.")
        print("  NEGATIVE = inbound side, POSITIVE = outbound side.\n")
        print(f"  {'road/way':<16}{'move':<9}{'turn':<9}{'start':>8}{'end':>8}"
              f"  decided by")
        for mv in sorted(junction.movements, key=lambda m: m.key):
            if mv.is_vru:
                continue
            print(f"  {mv.road_id}/{mv.way_id:<10}{mv.key:<9}{mv.turn.value:<9}"
                  f"{mv.start_side:>+8.2f}{mv.end_side:>+8.2f}"
                  f"  {mv.direction_source}")

        print("\n  Arm axes (bearing out from the junction centre):")
        for name, arm in sorted(junction.arms.items()):
            signs = ", ".join(e.line_sub or "?" for e in arm.reg_elements) or "none"
            print(f"    {name}: {arm.bearing_deg:6.1f} deg   "
                  f"class={arm.priority.name:<12} signs={signs}")

    print("\nyield_to cells")
    for mv in sorted(junction.movements, key=lambda m: m.key):
        if mv.is_vru:
            continue
        cell = build_yield_to_map(result, cfg)[mv.feature_id]
        print(f"  {mv.road_id}/{mv.way_id:<10} {mv.key:<9} -> {cell or '(empty)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
