"""
Run every junction in a GeoJSON export and report what the engine managed.

    python3 scripts/audit_all.py <file.geojson>

Groups intersection centerlines into junctions by proximity, runs each one,
and prints a coverage table: how many arms, how they were classified, whether
the movements resolved cleanly, and what went wrong where it did.

This is the Epic-2 coverage check - which junctions the right-before-left
engine actually handles, and which need more work.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yield_pipeline import DEFAULT_CONFIG, build_junction, evaluate   # noqa: E402
from yield_pipeline.extract import ExtractionError                    # noqa: E402
from yield_pipeline.model import PriorityClass                        # noqa: E402

CLUSTER_RADIUS_DEG = 0.0006        # ~40 m


def centroid(coords):
    return (sum(p[0] for p in coords) / len(coords),
            sum(p[1] for p in coords) / len(coords))


def cluster_junctions(features):
    """Group intersection centerlines into junctions by proximity."""
    lanes = [f for f in features
             if f["properties"].get("lane_type") == "centerline"
             and f["properties"].get("lane_morphology") == "intersection"]
    groups = []
    for lane in lanes:
        mid = centroid(lane["geometry"]["coordinates"])
        for g in groups:
            if math.dist(mid, g["mid"]) < CLUSTER_RADIUS_DEG:
                g["lanes"].append(lane)
                g["mids"].append(mid)
                g["mid"] = centroid(g["mids"])
                break
        else:
            groups.append({"mid": mid, "lanes": [lane], "mids": [mid]})
    return groups


def project_for(coords_list):
    pts = [p for c in coords_list for p in c]
    lon0 = sum(p[0] for p in pts) / len(pts)
    lat0 = sum(p[1] for p in pts) / len(pts)
    kx = math.cos(math.radians(lat0)) * 111320.0
    return lambda c: ((c[0] - lon0) * kx, (c[1] - lat0) * 110540.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("geojson")
    args = ap.parse_args()

    with open(args.geojson) as fh:
        data = json.load(fh)
    feats = [f for f in data["features"]
             if f.get("geometry") and f["geometry"].get("coordinates")]

    regs = {f["properties"].get("re_id"): f for f in feats
            if f["properties"].get("lane_type") == "regulatory_element"}

    groups = cluster_junctions(feats)
    print(f"{len(groups)} junctions found\n")

    header = (f"{'#':>3} {'roads':<34}{'arms':>5}{'lanes':>6}{'moves':>6}"
              f"{'rules':>6}  {'control':<22}status")
    print(header)
    print("-" * len(header))

    summary = Counter()
    problems = []

    for i, g in enumerate(sorted(groups, key=lambda g: g["mid"]), 1):
        lanes = g["lanes"]
        road_ids = sorted({str(l["properties"].get("road_id")) for l in lanes})
        label = ",".join(road_ids)
        if len(label) > 32:
            label = label[:29] + "..."

        # pull in the regulatory elements these lanes reference
        want = set()
        for lane in lanes:
            rule = lane["properties"].get("traffic_rule")
            if rule:
                want.update(x.strip() for x in
                            str(rule).replace(";", ",").split(",") if x.strip())
        extra = [regs[r] for r in want if r in regs]

        members = lanes + extra
        project = project_for([f["geometry"]["coordinates"] for f in members])
        payload = [{"attributes": dict(f["properties"]),
                    "geometry": [project(c) for c in f["geometry"]["coordinates"]]}
                   for f in members]

        try:
            junction = build_junction(payload, DEFAULT_CONFIG)
            result = evaluate(junction, DEFAULT_CONFIG)
        except ExtractionError as exc:
            print(f"{i:>3} {label:<34}{'-':>5}{len(lanes):>6}{'-':>6}{'-':>6}"
                  f"  {'-':<22}FAILED: {exc}")
            summary["failed"] += 1
            continue
        except Exception as exc:                            # noqa: BLE001
            print(f"{i:>3} {label:<34}{'-':>5}{len(lanes):>6}{'-':>6}{'-':>6}"
                  f"  {'-':<22}ERROR: {type(exc).__name__}: {exc}")
            summary["error"] += 1
            continue

        moves = [m for m in junction.movements if not m.is_vru]
        classes = {a.priority for a in junction.arms.values()}

        if junction.signalised:
            control = "signalised"
        elif classes == {PriorityClass.UNREGULATED}:
            control = "right-before-left"
        elif PriorityClass.PRIORITY in classes:
            control = "signed (306/301)"
        elif PriorityClass.YIELD in classes and PriorityClass.UNREGULATED in classes:
            control = "give-way markings"
        else:
            control = "all arms give way"

        issues = []
        keys = [m.key for m in moves]
        dupes = {k for k in keys if keys.count(k) > 1}
        if dupes:
            issues.append(f"{len(dupes)} duplicate movement(s)")
        if any("deadlock" in w for w in result.warnings):
            issues.append("deadlock")
        if any("could not be read from the geometry" in w for w in result.warnings):
            issues.append("direction unreadable")
        if any("more than one 1002" in w for w in result.warnings):
            issues.append("multiple 1002 plates")
        if len(junction.arms) < 3:
            issues.append(f"only {len(junction.arms)} arm(s)")

        status = "OK" if not issues else "; ".join(issues)
        summary["ok" if not issues else "issues"] += 1
        summary[f"control:{control}"] += 1
        if issues:
            problems.append((i, label, status))

        print(f"{i:>3} {label:<34}{len(junction.arms):>5}{len(lanes):>6}"
              f"{len(moves):>6}{len(result.relations):>6}  {control:<22}{status}")

    print()
    print("=" * 60)
    print(f"clean          : {summary['ok']}")
    print(f"with issues    : {summary['issues']}")
    print(f"failed / error : {summary['failed'] + summary['error']}")
    print()
    print("control type:")
    for key, n in sorted(summary.items()):
        if key.startswith("control:"):
            print(f"  {key[8:]:<24}{n}")

    if problems:
        print("\njunctions needing attention:")
        for i, label, status in problems:
            print(f"  #{i} roads {label}: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
