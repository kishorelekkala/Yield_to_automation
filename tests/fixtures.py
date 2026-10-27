"""
Synthetic intersections for verifying the rule engine.

Geometry is built the way a real HD map draws it: right-hand traffic, each
approach and exit offset to the right of its own direction of travel, turns
cut as tangent arcs (tight for right turns, wide for left turns). That is
enough for conflict detection to behave exactly as it will on real data.

Coordinates are metres, centred on (0, 0). North is +Y.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

Point = Tuple[float, float]

ARM_BEARING = {"N": 0.0, "E": 90.0, "S": 180.0, "W": 270.0}

LANE_OFFSET = 1.75      # half a 3.5 m lane
ARM_LENGTH = 40.0
R_RIGHT = 6.0           # tight right-turn radius
R_LEFT = 12.0           # wider left-turn radius
ARC_STEPS = 24


# --------------------------------------------------------------- vectors ---

def _unit(bearing_deg: float) -> Point:
    rad = math.radians(bearing_deg)
    return (math.sin(rad), math.cos(rad))


def _right_of(d: Point) -> Point:
    """Unit vector 90 degrees clockwise from d (the driver's right)."""
    return (d[1], -d[0])


def _add(p: Point, d: Point, k: float) -> Point:
    return (p[0] + d[0] * k, p[1] + d[1] * k)


def entry_point(arm: str, lane: int = 0, bearings=ARM_BEARING) -> Point:
    u = _unit(bearings[arm])
    inbound = (-u[0], -u[1])
    off = LANE_OFFSET + 3.5 * lane
    return _add(_add((0.0, 0.0), u, ARM_LENGTH), _right_of(inbound), off)


def exit_point(arm: str, lane: int = 0, bearings=ARM_BEARING) -> Point:
    u = _unit(bearings[arm])
    off = LANE_OFFSET + 3.5 * lane
    return _add(_add((0.0, 0.0), u, ARM_LENGTH), _right_of(u), off)


def _line_intersection(p1: Point, d1: Point, p2: Point, d2: Point) -> Point:
    denom = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(denom) < 1e-9:
        raise ValueError("parallel tangents")
    t = ((p2[0] - p1[0]) * d2[1] - (p2[1] - p1[1]) * d2[0]) / denom
    return _add(p1, d1, t)


def movement_geometry(from_arm: str, to_arm: str, lane_in: int = 0,
                      lane_out: int = 0, bearings=ARM_BEARING) -> List[Point]:
    """Approach leg -> tangent arc -> exit leg."""
    start = entry_point(from_arm, lane_in, bearings)
    end = exit_point(to_arm, lane_out, bearings)
    u_in = _unit(bearings[from_arm])
    start_dir = (-u_in[0], -u_in[1])
    end_dir = _unit(bearings[to_arm])

    delta = math.degrees(math.atan2(
        start_dir[0] * end_dir[1] - start_dir[1] * end_dir[0],
        start_dir[0] * end_dir[0] + start_dir[1] * end_dir[1]))
    # delta here is counter-clockwise-positive in xy; convert to "turn sign"
    # where positive = right (clockwise on the map).
    turn = -delta

    if abs(turn) < 1.0 or abs(abs(turn) - 180.0) < 1.0:
        return [start, end]

    radius = R_RIGHT if turn > 0 else R_LEFT
    corner = _line_intersection(start, start_dir, end, end_dir)
    tangent = radius * math.tan(math.radians(abs(turn)) / 2.0)
    arc_start = _add(corner, start_dir, -tangent)
    arc_end = _add(corner, end_dir, tangent)

    normal = _right_of(start_dir) if turn > 0 else (-_right_of(start_dir)[0],
                                                   -_right_of(start_dir)[1])
    centre = _add(arc_start, normal, radius)

    a0 = math.atan2(arc_start[1] - centre[1], arc_start[0] - centre[0])
    a1 = math.atan2(arc_end[1] - centre[1], arc_end[0] - centre[0])
    if turn > 0:                       # clockwise
        while a1 > a0:
            a1 -= 2 * math.pi
    else:                              # counter-clockwise
        while a1 < a0:
            a1 += 2 * math.pi

    arc = [(centre[0] + radius * math.cos(a0 + (a1 - a0) * i / ARC_STEPS),
            centre[1] + radius * math.sin(a0 + (a1 - a0) * i / ARC_STEPS))
           for i in range(ARC_STEPS + 1)]
    return [start] + arc + [end]


def crossing_geometry(arm: str, bearings=ARM_BEARING, setback: float = 9.0
                      ) -> List[Point]:
    """A pedestrian crossing laid across one arm, just outside the junction."""
    u = _unit(bearings[arm])
    centre = _add((0.0, 0.0), u, setback)
    across = _right_of(u)
    return [_add(centre, across, -6.0), _add(centre, across, 6.0)]


# ------------------------------------------------------------- features -----

def _sign_feature(re_id: str, codes: Sequence[str]) -> dict:
    return {
        "attributes": {
            "road_id": "REG",
            "way_id": re_id,
            "lane_type": "regulatory_element",
            "line_type": "traffic_sign",
            "line_sub": ",".join(codes),
            "re_id": re_id,
        },
        "geometry": [(0.0, 0.0), (0.1, 0.1)],
    }


def _light_feature(re_id: str) -> dict:
    return {
        "attributes": {
            "road_id": "REG",
            "way_id": re_id,
            "lane_type": "regulatory_element",
            "line_type": "traffic_light",
            "line_sub": "de201",
            "re_id": re_id,
        },
        "geometry": [(0.0, 0.0), (0.1, 0.1)],
    }


def make_junction(moves: Sequence[Tuple[str, str]],
                  arm_signs: Optional[Dict[str, Sequence[str]]] = None,
                  signalised: bool = False,
                  protected: Optional[Sequence[Tuple[str, str]]] = None,
                  crossings: Sequence[str] = (),
                  signal_groups: Optional[Dict[Tuple[str, str], str]] = None,
                  bearings: Dict[str, float] = ARM_BEARING,
                  road_id_of=None) -> List[dict]:
    """Build a feature list for the pipeline.

    moves       - (from_arm, to_arm) pairs to include
    arm_signs   - arm -> list of sign codes carried by that approach
    signalised  - attach a traffic light to every approach
    protected   - movements with their own green arrow
    crossings   - arms that have a pedestrian crossing
    """
    arm_signs = dict(arm_signs or {})
    protected = set(protected or ())
    signal_groups = dict(signal_groups or {})
    features: List[dict] = []

    # one regulatory element per arm
    re_for_arm: Dict[str, List[str]] = {}
    for arm in {m[0] for m in moves}:
        ids: List[str] = []
        codes = list(arm_signs.get(arm, []))
        if codes:
            re_id = f"RE_{arm}_SIGN"
            features.append(_sign_feature(re_id, codes))
            ids.append(re_id)
        if signalised:
            re_id = f"RE_{arm}_LIGHT"
            features.append(_light_feature(re_id))
            ids.append(re_id)
        re_for_arm[arm] = ids

    for i, (a, b) in enumerate(moves):
        road_id = road_id_of(a, b) if road_id_of else f"R{a}"
        features.append({
            "attributes": {
                "road_id": road_id,
                "way_id": f"W{a}{b}",
                "lane_type": "centerline",
                "lane_morphology": "intersection",
                "traffic_rule": ",".join(re_for_arm.get(a, [])),
                "yield_to": None,
                "signal_group": signal_groups.get((a, b), ""),
                "protected": (a, b) in protected,
            },
            "geometry": movement_geometry(a, b, bearings=bearings),
        })

    for arm in crossings:
        features.append({
            "attributes": {
                "road_id": f"X{arm}",
                "way_id": f"WX{arm}",
                "lane_type": "crosswalk",
                "lane_morphology": "intersection",
            },
            "geometry": crossing_geometry(arm, bearings),
        })

    return features


# --------------------------------------------------------------- helpers ---

def yield_keys(result, movement_key: str) -> set:
    """The set of 'A->B' movement keys a given movement yields to."""
    for movement in result.junction.movements:
        if movement.key == movement_key:
            return {m.key for m in result.yield_to.get(movement.feature_id, [])}
    raise KeyError(f"no movement {movement_key} in junction")


def all_yields(result) -> Dict[str, set]:
    return {m.key: {o.key for o in result.yield_to.get(m.feature_id, [])}
            for m in result.junction.movements if not m.is_vru}
