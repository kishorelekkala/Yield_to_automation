"""
Pure-python planar geometry helpers.

No QGIS or shapely dependency, so the rule engine can be unit-tested from a
plain interpreter. Coordinates are projected CRS units (metres); bearings are
compass degrees, clockwise from +Y (north).
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

Point = Tuple[float, float]


# ---------------------------------------------------------------- bearings --

def bearing(p_from: Point, p_to: Point) -> float:
    """Compass bearing from one point to another, in [0, 360)."""
    dx = p_to[0] - p_from[0]
    dy = p_to[1] - p_from[1]
    return (math.degrees(math.atan2(dx, dy))) % 360.0


def normalise_angle(deg: float) -> float:
    """Wrap to (-180, 180]."""
    out = (deg + 180.0) % 360.0 - 180.0
    return 180.0 if out == -180.0 else out


def relative_bearing(heading_deg: float, target_deg: float) -> float:
    """Signed angle from a heading to a target bearing.

    Positive = clockwise = to the right of the heading.
    """
    return normalise_angle(target_deg - heading_deg)


def cardinal_from_bearing(deg: float) -> str:
    """Nearest of N / E / S / W for a bearing."""
    idx = int((deg % 360.0 + 45.0) // 90.0) % 4
    return ("N", "E", "S", "W")[idx]


# ------------------------------------------------------------- polylines ----

def densify(points: Sequence[Point], max_step: float) -> List[Point]:
    """Insert vertices so no segment is longer than max_step."""
    if len(points) < 2:
        return list(points)
    out: List[Point] = [tuple(points[0])]
    for a, b in zip(points, points[1:]):
        seg = distance(a, b)
        n = max(1, int(math.ceil(seg / max_step)))
        for i in range(1, n + 1):
            t = i / n
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


def distance(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def polyline_length(points: Sequence[Point]) -> float:
    return sum(distance(a, b) for a, b in zip(points, points[1:]))


def distance_along(points: Sequence[Point], target: Point) -> float:
    """Arc length from the start of the polyline to the point nearest target."""
    best_d, best_s = float("inf"), 0.0
    run = 0.0
    for a, b in zip(points, points[1:]):
        seg = distance(a, b)
        if seg == 0:
            continue
        t = (((target[0] - a[0]) * (b[0] - a[0]) + (target[1] - a[1]) * (b[1] - a[1]))
             / (seg * seg))
        t = max(0.0, min(1.0, t))
        proj = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        d = distance(proj, target)
        if d < best_d:
            best_d, best_s = d, run + seg * t
        run += seg
    return best_s


def point_at_distance(points: Sequence[Point], s: float) -> Point:
    """Interpolate the polyline at arc length s."""
    if s <= 0:
        return tuple(points[0])
    run = 0.0
    for a, b in zip(points, points[1:]):
        seg = distance(a, b)
        if run + seg >= s:
            t = (s - run) / seg if seg else 0.0
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        run += seg
    return tuple(points[-1])


def heading_at_start(points: Sequence[Point], window: float = 5.0) -> float:
    """Bearing of the first `window` metres of the polyline."""
    ref = point_at_distance(points, min(window, polyline_length(points)))
    return bearing(points[0], ref)


def heading_at_end(points: Sequence[Point], window: float = 5.0) -> float:
    """Bearing of the last `window` metres of the polyline."""
    total = polyline_length(points)
    ref = point_at_distance(points, max(0.0, total - window))
    return bearing(ref, points[-1])


def perpendicular_at(points: Sequence[Point], s: float, half_width: float
                     ) -> Tuple[Point, Point]:
    """A line segment across the polyline at arc length s."""
    centre = point_at_distance(points, s)
    ahead = point_at_distance(points, min(polyline_length(points), s + 0.5))
    behind = point_at_distance(points, max(0.0, s - 0.5))
    dx, dy = ahead[0] - behind[0], ahead[1] - behind[1]
    norm = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / norm, dx / norm
    return ((centre[0] + nx * half_width, centre[1] + ny * half_width),
            (centre[0] - nx * half_width, centre[1] - ny * half_width))


# ---------------------------------------------------------- intersections ---

def _segment_intersection(p1: Point, p2: Point, p3: Point, p4: Point
                          ) -> Optional[Point]:
    """Proper intersection point of two segments, or None."""
    d1x, d1y = p2[0] - p1[0], p2[1] - p1[1]
    d2x, d2y = p4[0] - p3[0], p4[1] - p3[1]
    denom = d1x * d2y - d1y * d2x
    if abs(denom) < 1e-12:
        return None
    sx, sy = p3[0] - p1[0], p3[1] - p1[1]
    t = (sx * d2y - sy * d2x) / denom
    u = (sx * d1y - sy * d1x) / denom
    if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
        return (p1[0] + d1x * t, p1[1] + d1y * t)
    return None


def polyline_intersections(a: Sequence[Point], b: Sequence[Point]
                           ) -> List[Point]:
    """All crossing points between two polylines."""
    hits: List[Point] = []
    for a1, a2 in zip(a, a[1:]):
        for b1, b2 in zip(b, b[1:]):
            pt = _segment_intersection(a1, a2, b1, b2)
            if pt is None:
                continue
            if not any(distance(pt, h) < 1e-6 for h in hits):
                hits.append(pt)
    return hits


def min_separation(a: Sequence[Point], b: Sequence[Point]) -> float:
    """Smallest distance between any vertex of one polyline and the other."""
    best = float("inf")
    for pa in a:
        for pb in b:
            d = distance(pa, pb)
            if d < best:
                best = d
    return best


def centroid(polylines: Sequence[Sequence[Point]]) -> Point:
    """Length-weighted centroid of a set of polylines."""
    sx = sy = total = 0.0
    for line in polylines:
        for p, q in zip(line, line[1:]):
            seg = distance(p, q)
            if seg == 0:
                continue
            sx += (p[0] + q[0]) / 2 * seg
            sy += (p[1] + q[1]) / 2 * seg
            total += seg
    if total == 0:
        pts = [p for line in polylines for p in line]
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
    return (sx / total, sy / total)
