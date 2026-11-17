"""
Epic 6 / MP-468 - wait lines.

For every intersection centerline that has to give way:

  1. find its wait line
       a. a wait line already named in the centerline's traffic_rule, or
       b. an unlinked wait line drawn across the start of the centerline,
  2. or plan a new one at the yield-line position, with a fresh re_id,
  3. append the wait line's re_id to the centerline's traffic_rule,
  4. set the wait line's yield_to to the centerline's yield_to.

A wait line that several centerlines start behind (left / straight / right
out of one lane) gets the union of their yield_to values.

Pure Python, no QGIS: the result is a plan the adapter writes out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import geometry as geo
from .config import DEFAULT_CONFIG, PipelineConfig, normalise_code, split_multi
from .model import Movement, PipelineResult, PriorityClass
from .writer import format_yield_to, yield_line_geometry

Point = Tuple[float, float]

_NUMBERED = re.compile(r"^(.*?)(\d+)$")


# ----------------------------------------------------------------- re_ids ---

class ReIdAllocator:
    """Hands out re_ids that do not clash with any already in the map.

    Continues the highest number in use, keeping its prefix: if the layer
    holds re_975, the next id is re_976. Share one allocator across all
    junctions of a run so two junctions never get the same id.
    """

    def __init__(self, existing: Iterable[str], prefix: str = "re_"):
        self.used = {str(r).strip() for r in existing if r not in (None, "")}
        self.prefix = prefix
        self.number = 0
        best = -1
        for rid in self.used:
            m = _NUMBERED.match(rid)
            if m and int(m.group(2)) > best:
                best = int(m.group(2))
                self.prefix = m.group(1) or prefix
        self.number = max(best, 0)

    def next(self) -> str:
        while True:
            self.number += 1
            rid = f"{self.prefix}{self.number}"
            if rid not in self.used:
                self.used.add(rid)
                return rid


# ------------------------------------------------------------ recognition ---

def _attrs(feature: dict) -> dict:
    return feature.get("attributes", {})


def _text(feature: dict, name: str) -> str:
    value = _attrs(feature).get(name)
    return "" if value is None else str(value).strip()


def is_wait_line(feature: dict, cfg: PipelineConfig = DEFAULT_CONFIG) -> bool:
    """regulatory_element / traffic_sign, line_sub 341 or 294, drawn as a line."""
    s, w = cfg.schema, cfg.wait_lines
    if _text(feature, s.lane_type).lower() != s.regulatory_element_value.lower():
        return False
    if _text(feature, s.line_type).lower() != s.traffic_sign_value.lower():
        return False
    codes = [normalise_code(c) for c in
             split_multi(_text(feature, s.line_sub), s.multi_value_separators)]
    stripped = {c[2:] if c.startswith("de") else c for c in codes}
    if not stripped & set(w.codes):
        return False
    pts = feature.get("geometry") or []
    return len(pts) >= 2 and geo.polyline_length(pts) >= w.min_length


def _distance_to_entry(line: Sequence[Point], movement: Movement,
                       cfg: PipelineConfig) -> float:
    """Closest approach of `line` to the first entry_window metres of movement."""
    w = cfg.wait_lines
    total = geo.polyline_length(movement.geometry)
    window = min(w.entry_window, total)
    steps = max(1, int(window / 0.25))
    entry = [geo.point_at_distance(movement.geometry, window * i / steps)
             for i in range(steps + 1)]
    if geo.polyline_intersections(line, entry):
        return 0.0
    return geo.min_separation(geo.densify(line, 0.25), entry)


# ------------------------------------------------------------------- plan ---

@dataclass
class WaitLineRow:
    """One line of the report: a centerline and the wait line it got."""

    movement: Movement
    re_id: str
    status: str               # linked / matched / new
    wait_feature_id: Optional[str]
    yield_to: str


@dataclass
class WaitLinePlan:
    # existing wait-line feature_id -> new yield_to
    updates: Dict[str, str] = field(default_factory=dict)
    # existing wait lines that had no re_id: feature_id -> assigned re_id
    re_id_fixes: Dict[str, str] = field(default_factory=dict)
    # centerline feature_id -> new traffic_rule (only where it changes)
    traffic_rule: Dict[str, str] = field(default_factory=dict)
    # new wait-line records: {"attributes": {...}, "geometry": [p1, p2]}
    new_lines: List[dict] = field(default_factory=list)
    rows: List[WaitLineRow] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _append_re_id(cell, re_id: str, cfg: PipelineConfig) -> Optional[str]:
    """traffic_rule with re_id added, or None if it is already there."""
    parts = split_multi(cell, cfg.schema.multi_value_separators,
                        cfg.schema.null_values)
    if re_id in parts:
        return None
    return ",".join(parts + [re_id])


def _new_code(movement: Movement, result: PipelineResult,
              cfg: PipelineConfig) -> str:
    w = cfg.wait_lines
    arm = result.junction.arms.get(movement.entry_arm)
    # Read the sign itself: with stop_equals_yield a DE 206 arm is classed
    # YIELD, but it still needs a stop line, not a wait line.
    stop = result.junction.signalised or (
        arm is not None and (
            arm.priority in (PriorityClass.STOP, PriorityClass.SIGNALISED)
            or any(e.has_code(cfg.signs.stop_sign) for e in arm.reg_elements)))
    return w.line_sub_template.format(code=w.new_code_stop if stop else w.new_code)


def plan_wait_lines(result: PipelineResult,
                    candidates: Sequence[dict],
                    allocator: ReIdAllocator,
                    cfg: PipelineConfig = DEFAULT_CONFIG) -> WaitLinePlan:
    """Work out every wait-line change for one junction.

    `candidates` are regulatory-element features (any; non-wait-lines are
    ignored), in the same metric CRS as the junction.
    """
    s, w = cfg.schema, cfg.wait_lines
    plan = WaitLinePlan()

    waits = [f for f in candidates if is_wait_line(f, cfg)]
    by_re: Dict[str, dict] = {}
    for f in waits:
        rid = _text(f, s.re_id)
        if rid:
            by_re.setdefault(rid, f)

    movements = sorted((m for m in result.junction.movements if not m.is_vru),
                       key=lambda m: m.key)

    # wait-line feature_id -> (feature, [movements behind it])
    served: Dict[str, Tuple[dict, List[Movement]]] = {}
    chosen: Dict[str, Tuple[str, str, Optional[str]]] = {}   # mv fid -> (re_id, status, wl fid)

    for mv in movements:
        targets = result.yield_to.get(mv.feature_id, [])

        # a. already linked through traffic_rule
        linked = [by_re[r] for r in mv.reg_ids if r in by_re]
        if len(linked) > 1:
            plan.warnings.append(
                f"{mv.road_id}/{mv.way_id} ({mv.key}): traffic_rule names "
                f"{len(linked)} wait lines "
                f"({', '.join(_text(f, s.re_id) for f in linked)}); using the first")
        feature, status = (linked[0], "linked") if linked else (None, "")

        # b. drawn across the start of the centerline, not linked yet
        if feature is None:
            start = mv.geometry[0]
            best, best_d = None, float("inf")
            for f in waits:
                pts = f["geometry"]
                reach = w.entry_window + w.match_radius + geo.polyline_length(pts)
                if geo.distance(pts[0], start) > reach:
                    continue
                d = _distance_to_entry(pts, mv, cfg)
                if d <= w.match_radius and d < best_d:
                    best, best_d = f, d
            if best is not None:
                feature, status = best, "matched"

        if feature is not None:
            rid = _text(feature, s.re_id)
            fid = str(_attrs(feature).get("fid"))
            if not rid:
                rid = allocator.next()
                plan.warnings.append(
                    f"wait line {fid} had no {s.re_id}; assigned {rid}")
                feature.setdefault("attributes", {})[s.re_id] = rid
                plan.re_id_fixes[fid] = rid
            served.setdefault(fid, (feature, []))[1].append(mv)
            chosen[mv.feature_id] = (rid, status, fid)
            continue

        # c. nothing drawn - plan a new one, but only if this lane waits
        if not targets:
            continue
        rid = allocator.next()
        p1, p2 = yield_line_geometry(mv, result, cfg)
        cell = format_yield_to(targets, cfg)
        plan.new_lines.append({
            "attributes": {
                s.lane_type: s.regulatory_element_value,
                s.line_type: s.traffic_sign_value,
                s.line_sub: _new_code(mv, result, cfg),
                s.re_id: rid,
                s.yield_to: cell,
                # review aids - not part of the HD map schema
                "centerline": f"{mv.road_id}_{mv.way_id}",
                "movement": mv.key,
            },
            "geometry": [p1, p2],
        })
        chosen[mv.feature_id] = (rid, "new", None)

    # yield_to on existing wait lines: union over every lane behind them
    for fid, (feature, mvs) in served.items():
        seen, union = set(), []
        for mv in mvs:
            for t in result.yield_to.get(mv.feature_id, []):
                if t.feature_id not in seen:
                    seen.add(t.feature_id)
                    union.append(t)
        plan.updates[fid] = format_yield_to(union, cfg)
        if len(mvs) > 1:
            cells = {format_yield_to(result.yield_to.get(m.feature_id, []), cfg)
                     for m in mvs}
            if len(cells) > 1:
                plan.warnings.append(
                    f"wait line {_text(feature, s.re_id)} serves "
                    f"{', '.join(m.key for m in mvs)} which yield differently; "
                    f"its yield_to is the union - check it")

    # link: re_id into traffic_rule, and the report rows
    for mv in movements:
        if mv.feature_id not in chosen:
            continue
        rid, status, wl_fid = chosen[mv.feature_id]
        new_rule = _append_re_id(mv.raw.get(s.traffic_rule), rid, cfg)
        if new_rule is not None:
            plan.traffic_rule[mv.feature_id] = new_rule
        cell = (plan.updates[wl_fid] if wl_fid is not None
                else format_yield_to(result.yield_to.get(mv.feature_id, []), cfg))
        plan.rows.append(WaitLineRow(mv, rid, status, wl_fid, cell))

    return plan


def summarise_plan(plan: WaitLinePlan) -> str:
    out = ["wait lines",
           f"  {'road/way':<16}{'move':<9}{'status':<9}{'re_id':<12}yield_to"]
    for row in plan.rows:
        mv = row.movement
        out.append(f"  {mv.road_id + '/' + mv.way_id:<16}{mv.key:<9}"
                   f"{row.status:<9}{row.re_id:<12}{row.yield_to or '(empty)'}")
    out.append(f"  -> {len(plan.new_lines)} new, "
               f"{len(plan.updates)} existing updated, "
               f"{len(plan.traffic_rule)} traffic_rule link(s) added")
    if plan.warnings:
        out.append("")
        out.extend(f"  ! {w}" for w in plan.warnings)
    return "\n".join(out)
