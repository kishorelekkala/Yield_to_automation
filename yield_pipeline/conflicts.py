"""
Conflict detection.

Two movements conflict when their paths cross inside the junction, or when
they end in the same outgoing lane. Everything else - including the whole
family of bent-priority (1002-xx) cases - falls out of this one step, because
a manoeuvre that never meets another manoeuvre never yields to it, whatever
the signs say.

Reference checks taken from the attached scenario set:
  * four simultaneous right turns  -> no pair conflicts      (Sc. 6, Ph2)
  * priority road round a bend     -> S->W and W->S do not meet  (Sc. 5b)
  * yield-arm right turn           -> merges with the straight it joins (Sc. 5, Ph4)
  * left turn vs oncoming right    -> merge in the target lane (Sc. 5, Ph2)
"""

from __future__ import annotations

from typing import List, Sequence

from . import geometry as geo
from .config import PipelineConfig, DEFAULT_CONFIG
from .model import Conflict, ConflictType, Movement, TurnType


def detect_conflicts(movements: Sequence[Movement],
                     cfg: PipelineConfig = DEFAULT_CONFIG) -> List[Conflict]:
    """All unordered conflicting pairs among the junction's movements."""
    vehicles = [m for m in movements if not m.is_vru]
    out: List[Conflict] = []

    for i, a in enumerate(vehicles):
        for b in vehicles[i + 1:]:
            conflict = classify_pair(a, b, cfg)
            if conflict.kind is not ConflictType.NONE:
                out.append(conflict)

    out.extend(_vru_conflicts(movements, cfg))
    return out


def classify_pair(a: Movement, b: Movement,
                  cfg: PipelineConfig = DEFAULT_CONFIG) -> Conflict:
    """CROSS, MERGE or NONE for one pair of movements."""
    g = cfg.geometry

    # Vehicles from the same arm queue behind one another; that is following
    # behaviour, not a yield relation.
    if a.entry_arm == b.entry_arm:
        return Conflict(a, b, ConflictType.NONE)

    # --- merge: both feed the same outgoing lane -----------------------
    if a.exit_lane_key == b.exit_lane_key:
        point = _merge_point(a, b, g.point_snap)
        return Conflict(a, b, ConflictType.MERGE, point,
                        geo.distance_along(a.geometry, point) if point else None,
                        geo.distance_along(b.geometry, point) if point else None)

    # --- cross: geometries intersect -----------------------------------
    hits = geo.polyline_intersections(a.geometry, b.geometry)
    if hits:
        # Ignore a crossing that is really the two paths touching as they
        # leave on the same side of a shared exit arm.
        exit_a, exit_b = a.geometry[-1], b.geometry[-1]
        real = [h for h in hits
                if geo.distance(h, exit_a) > g.merge_tail_tolerance
                and geo.distance(h, exit_b) > g.merge_tail_tolerance]
        if real:
            first = min(real, key=lambda p: geo.distance_along(a.geometry, p))
            return Conflict(a, b, ConflictType.CROSS, first,
                            geo.distance_along(a.geometry, first),
                            geo.distance_along(b.geometry, first))

    # Opposing left turns pass front-to-front and normally do not meet.
    # The switch exists so a map where they do can be handled.
    opposing_lefts = (a.turn is TurnType.LEFT and b.turn is TurnType.LEFT
                      and a.exit_arm == b.entry_arm and b.exit_arm == a.entry_arm)
    if cfg.rules.opposing_lefts_conflict and opposing_lefts:
        return Conflict(a, b, ConflictType.CROSS, None)

    # Paths that graze each other without strictly crossing: a vehicle has
    # width, so a near-tangent pass is still a conflict. Opposing lefts are
    # exempt - that pass is deliberate and legal.
    if not opposing_lefts and g.conflict_clearance > 0:
        da = geo.densify(a.geometry, 1.0)
        db = geo.densify(b.geometry, 1.0)
        gap, point = _closest_approach(da, db)
        if gap <= g.conflict_clearance:
            return Conflict(a, b, ConflictType.CROSS, point,
                            geo.distance_along(a.geometry, point),
                            geo.distance_along(b.geometry, point))

    return Conflict(a, b, ConflictType.NONE)


def _closest_approach(a: Sequence, b: Sequence):
    """Smallest gap between two densified polylines, and where it occurs."""
    best, point = float("inf"), None
    for pa in a:
        for pb in b:
            d = geo.distance(pa, pb)
            if d < best:
                best, point = d, pa
    return best, point


def _merge_point(a: Movement, b: Movement, snap: float):
    """Where two paths joining the same lane first come together."""
    best = None
    best_d = float("inf")
    for pa in geo.densify(a.geometry, 2.0):
        for pb in geo.densify(b.geometry, 2.0):
            d = geo.distance(pa, pb)
            if d < best_d:
                best_d, best = d, pa
            if d <= snap:
                return pa
    return best


def _vru_conflicts(movements: Sequence[Movement], cfg: PipelineConfig
                   ) -> List[Conflict]:
    """A turning vehicle crossing a pedestrian or cycle crossing."""
    if not cfg.rules.include_vru_crossings:
        return []
    vrus = [m for m in movements if m.is_vru]
    vehicles = [m for m in movements if not m.is_vru]
    out: List[Conflict] = []
    for vehicle in vehicles:
        for vru in vrus:
            hits = geo.polyline_intersections(vehicle.geometry, vru.geometry)
            if not hits:
                continue
            first = min(hits, key=lambda p: geo.distance_along(vehicle.geometry, p))
            out.append(Conflict(vehicle, vru, ConflictType.CROSS, first,
                                geo.distance_along(vehicle.geometry, first), None))
    return out


def first_conflict_distance(movement: Movement, conflicts: Sequence[Conflict]
                            ) -> float:
    """Arc length to the earliest conflict on a movement, for the yield line."""
    best = float("inf")
    for c in conflicts:
        if c.a is movement and c.dist_along_a is not None:
            best = min(best, c.dist_along_a)
        elif c.b is movement and c.dist_along_b is not None:
            best = min(best, c.dist_along_b)
    return best
