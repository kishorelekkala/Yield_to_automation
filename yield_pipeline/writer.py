"""
Epic 6 - output generation.

Formats the yield_to cell and builds the yield-line geometries that get
rendered on the QGIS canvas.
"""

from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from . import geometry as geo
from .config import PipelineConfig, DEFAULT_CONFIG
from .conflicts import first_conflict_distance
from .model import Movement, PipelineResult

Point = Tuple[float, float]


def format_item(movement: Movement, cfg: PipelineConfig) -> str:
    """One entry in a yield_to cell, e.g.  "4711''_880231" ."""
    item = cfg.output.item_template.format(road_id=movement.road_id,
                                           way_id=movement.way_id)
    return f'"{item}"' if cfg.output.quote_items else item


def format_yield_to(targets: Sequence[Movement],
                    cfg: PipelineConfig = DEFAULT_CONFIG) -> str:
    """Render the whole cell for one centerline."""
    if not targets:
        return cfg.output.write_empty_as

    items = [format_item(m, cfg) for m in targets]
    if cfg.output.deduplicate:
        seen, unique = set(), []
        for item in items:
            if item not in seen:
                seen.add(item)
                unique.append(item)
        items = unique
    if cfg.output.sort_items:
        items.sort()
    return cfg.output.item_separator.join(items)


def build_yield_to_map(result: PipelineResult,
                       cfg: PipelineConfig = DEFAULT_CONFIG) -> Dict[str, str]:
    """feature_id -> the string to write into the yield_to column."""
    out: Dict[str, str] = {}
    for movement in result.junction.movements:
        if movement.is_vru:
            continue
        out[movement.feature_id] = format_yield_to(
            result.yield_to.get(movement.feature_id, []), cfg)
    return out


def build_yield_lines(result: PipelineResult,
                      cfg: PipelineConfig = DEFAULT_CONFIG
                      ) -> List[Dict]:
    """A short bar across each yielding centerline, where the driver waits.

    Placed a setback before the first point of conflict on that movement, so
    it lands at the hold line rather than inside the junction.
    """
    g = cfg.geometry
    conflicts = []
    for rel in result.relations:
        conflicts.append(rel)

    lines: List[Dict] = []
    for movement in result.junction.movements:
        targets = result.yield_to.get(movement.feature_id, [])
        if movement.is_vru or not targets:
            continue

        # Earliest conflict point along this movement's own geometry.
        earliest = float("inf")
        for rel in result.relations:
            if rel.ego is not movement:
                continue
            hits = geo.polyline_intersections(movement.geometry,
                                              rel.other.geometry)
            for hit in hits:
                earliest = min(earliest, geo.distance_along(movement.geometry, hit))
            if not hits:
                # merge case: use the closest approach between the two paths
                for pa in geo.densify(movement.geometry, 2.0):
                    for pb in geo.densify(rel.other.geometry, 2.0):
                        if geo.distance(pa, pb) <= cfg.geometry.conflict_clearance * 2:
                            earliest = min(
                                earliest,
                                geo.distance_along(movement.geometry, pa))
                            break

        total = geo.polyline_length(movement.geometry)
        if earliest == float("inf"):
            s = max(0.0, total * 0.25)
        else:
            s = max(0.0, earliest - g.yield_line_setback)

        p1, p2 = geo.perpendicular_at(movement.geometry, s, g.yield_line_half_width)
        lines.append({
            "feature_id": movement.feature_id,
            "road_id": movement.road_id,
            "way_id": movement.way_id,
            "movement": movement.key,
            "turn": movement.turn.value,
            "yield_count": len(targets),
            "yield_to": format_yield_to(targets, cfg),
            "geometry": [p1, p2],
        })
    return lines


def summarise(result: PipelineResult, cfg: PipelineConfig = DEFAULT_CONFIG) -> str:
    """Human-readable report for the QGIS log panel."""
    j = result.junction
    control_str = 'signalised' if j.signalised else 'sign / unregulated'
    
    if j.has_controlled_left and j.has_controlled_right:
        control_str += " (controlled left & right)"
    elif j.has_controlled_left:
        control_str += " (controlled left)"
    elif j.has_controlled_right:
        control_str += " (controlled right)"
        
    if j.has_left_green:
        control_str += " [left green arrow present]"
    out = [
        "Junction summary",
        f"  centre        : ({j.centre[0]:.2f}, {j.centre[1]:.2f})",
        f"  control       : {'signalised' if j.signalised else 'sign / unregulated'}",
        f"  arms          : " + ", ".join(
            f"{name}({arm.priority.name}"
            + (f", plate {arm.plate_variant}" if arm.plate_variant else "") + ")"
            for name, arm in sorted(j.arms.items())),
        f"  movements     : {len([m for m in j.movements if not m.is_vru])}"
        f" ({len([m for m in j.movements if m.is_vru])} VRU crossings)",
        f"  yield relations: {len(result.relations)}",
        "",
        "yield_to assignments",
    ]
    for movement in sorted((m for m in j.movements if not m.is_vru),
                           key=lambda m: m.key):
        targets = result.yield_to.get(movement.feature_id, [])
        cell = format_yield_to(targets, cfg) or "(none)"
        out.append(f"  {movement.key:<10} {movement.turn.value:<9} -> {cell}")

    if result.warnings:
        out.append("")
        out.append("warnings")
        for w in result.warnings:
            out.append(f"  ! {w}")
    return "\n".join(out)
