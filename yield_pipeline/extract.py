"""
Epic 1 - base data extraction.

Takes raw features out of QGIS (or a test fixture) and produces a Junction:
intersection centerlines confirmed, arms identified and named N/S/E/W,
movements classified by turn, regulatory elements resolved via re_id.

A "feature" here is a plain dict::

    {"attributes": {...column -> value...}, "geometry": [(x, y), ...]}

so this module stays testable without QGIS.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import geometry as geo
from .config import PipelineConfig, DEFAULT_CONFIG, normalise_code, split_multi
from .model import (Arm, Junction, Movement, PriorityClass, RegulatoryElement,
                    TurnType)

CARDINALS = ("N", "E", "S", "W")


class ExtractionError(Exception):
    pass


# ------------------------------------------------------------- partition ----

def _attr(feature: dict, name: str, default=None):
    return feature.get("attributes", {}).get(name, default)


def _str_attr(feature: dict, name: str) -> str:
    value = _attr(feature, name)
    return "" if value is None else str(value).strip()


def partition_features(features: Sequence[dict], cfg: PipelineConfig
                       ) -> Tuple[List[dict], List[dict], List[dict],
                                  List[dict], List[str]]:
    """Split the selection by lane_type.

    Returns (centerlines, regulatory elements, VRU crossings, road borders,
    warnings). Road borders carry no yield_to of their own but are used to
    locate the junction centre.
    """
    s = cfg.schema
    centerlines, reg_elements, vru, borders, warnings = [], [], [], [], []
    unhandled: Dict[str, int] = {}

    for feat in features:
        lane_type = _str_attr(feat, s.lane_type).lower()
        line_type = _str_attr(feat, s.line_type).lower()

        if lane_type == s.centerline_value.lower():
            centerlines.append(feat)
        elif lane_type == s.regulatory_element_value.lower():
            reg_elements.append(feat)
        elif lane_type in cfg.schema.road_border_values:
            borders.append(feat)
        elif (lane_type in cfg.schema.crosswalk_values
              or lane_type in cfg.schema.cycle_values
              or line_type in cfg.schema.crosswalk_values
              or line_type in cfg.schema.cycle_values):
            vru.append(feat)
        else:
            key = lane_type or f"(no {s.lane_type} value)"
            unhandled[key] = unhandled.get(key, 0) + 1

    for lane_type, count in sorted(unhandled.items()):
        warnings.append(
            f"{count} feature(s) with lane_type '{lane_type}' are not handled "
            f"by the pipeline and were skipped")

    return centerlines, reg_elements, vru, borders, warnings


def confirm_intersection(centerlines: Sequence[dict], cfg: PipelineConfig
                         ) -> Tuple[List[dict], List[str]]:
    """Stage 1 gate: keep only centerlines whose lane_morphology is intersection."""
    s = cfg.schema
    keep, warnings = [], []
    for feat in centerlines:
        morph = _str_attr(feat, s.lane_morphology).lower()
        if morph == s.intersection_value.lower():
            keep.append(feat)
        else:
            warnings.append(
                f"centerline {_str_attr(feat, s.road_id)}/"
                f"{_str_attr(feat, s.way_id)}: {s.lane_morphology}='{morph or 'empty'}' "
                f"- NOT at an intersection, excluded from the rule engine")
    return keep, warnings


# --------------------------------------------------- regulatory elements ----

def build_reg_index(reg_features: Sequence[dict], cfg: PipelineConfig
                    ) -> Dict[str, RegulatoryElement]:
    """re_id -> RegulatoryElement, with sign codes normalised."""
    s = cfg.schema
    index: Dict[str, RegulatoryElement] = {}
    for feat in reg_features:
        re_id = _str_attr(feat, s.re_id)
        if not re_id:
            continue
        line_sub = _str_attr(feat, s.line_sub)
        codes = [normalise_code(c)
                 for c in split_multi(line_sub, s.multi_value_separators)]
        if not codes and line_sub:
            codes = [normalise_code(line_sub)]
        index[re_id] = RegulatoryElement(
            re_id=re_id,
            line_type=_str_attr(feat, s.line_type) or None,
            line_sub=line_sub or None,
            codes=codes,
            geometry=feat.get("geometry"),
            raw=feat.get("attributes", {}),
        )
    return index


def linked_reg_ids(feature: dict, cfg: PipelineConfig) -> List[str]:
    """The re_ids a centerline names in its traffic_rule cell."""
    raw = _attr(feature, cfg.schema.traffic_rule)
    return split_multi(raw, cfg.schema.multi_value_separators)


def classify_reg_elements(elements: Sequence[RegulatoryElement],
                          cfg: PipelineConfig
                          ) -> Tuple[PriorityClass, Optional[str], bool, bool, dict]:
    """Return (class, plate_variant, is_signal, had_explicit_sign, signals_dict)."""
    codes = cfg.signs
    priority = PriorityClass.UNREGULATED
    plate: Optional[str] = None
    is_signal = False
    explicit = False
    
    signals = {"normal": False, "left_controlled": False, 
               "left_green": False, "right_controlled": False}

    for elem in elements:
        is_tl = (elem.line_type or "").lower() == cfg.schema.traffic_light_value
        
        if is_tl or elem.has_code(codes.traffic_light):
            is_signal = True
            if elem.has_code(codes.traffic_light):
                signals["normal"] = True
                
        if elem.has_code(codes.light_left_controlled):
            is_signal = True
            signals["left_controlled"] = True
            
        if elem.has_code(codes.light_left_green):
            is_signal = True
            signals["left_green"] = True
            
        if elem.has_code(codes.light_right_controlled):
            is_signal = True
            signals["right_controlled"] = True

        if elem.has_code(codes.priority_road) or elem.has_code(codes.priority_at_next):
            priority = PriorityClass.PRIORITY
            explicit = True
        elif elem.has_code(codes.stop_sign):
            priority = (PriorityClass.YIELD if cfg.rules.stop_equals_yield
                        else PriorityClass.STOP)
            explicit = True
        elif elem.has_code(codes.yield_sign):
            priority = PriorityClass.YIELD
            explicit = True
            
        variant = elem.plate_variant(codes.supplementary_prefix)
        if variant:
            plate = variant

    return priority, plate, is_signal, explicit, signals


# ------------------------------------------------------------------ arms ----

def cluster_arms(bearings: Sequence[float], tolerance: float
                 ) -> List[float]:
    """Group endpoint bearings into arm bearings (circular mean per cluster)."""
    import math

    clusters: List[List[float]] = []
    for b in sorted(bearings):
        placed = False
        for cluster in clusters:
            mean = _circular_mean(cluster)
            if abs(geo.normalise_angle(b - mean)) <= tolerance:
                cluster.append(b)
                placed = True
                break
        if not placed:
            clusters.append([b])

    if len(clusters) > 1:
        first, last = _circular_mean(clusters[0]), _circular_mean(clusters[-1])
        if abs(geo.normalise_angle(first - last)) <= tolerance:
            clusters[0].extend(clusters.pop())

    return [_circular_mean(c) for c in clusters]


def _circular_mean(bearings: Sequence[float]) -> float:
    import math
    sx = sum(math.sin(math.radians(b)) for b in bearings)
    sy = sum(math.cos(math.radians(b)) for b in bearings)
    return math.degrees(math.atan2(sx, sy)) % 360.0


def name_arms(bearings: Sequence[float]) -> Dict[str, float]:
    """Assign N/E/S/W to arm bearings, best fit first; extras get A5, A6..."""
    scored = []
    for b in bearings:
        for card in CARDINALS:
            ideal = {"N": 0.0, "E": 90.0, "S": 180.0, "W": 270.0}[card]
            scored.append((abs(geo.normalise_angle(b - ideal)), b, card))
    scored.sort()

    out: Dict[str, float] = {}
    used_bearings, used_cards = set(), set()
    for _, b, card in scored:
        if b in used_bearings or card in used_cards:
            continue
        out[card] = b
        used_bearings.add(b)
        used_cards.add(card)

    extra = 5
    for b in bearings:
        if b not in used_bearings:
            out[f"A{extra}"] = b
            extra += 1
    return out


def _arm_for_bearing(arms: Dict[str, float], b: float) -> str:
    return min(arms, key=lambda name: abs(geo.normalise_angle(b - arms[name])))


def lane_side(centre: geo.Point, arm_bearing: float, point: geo.Point) -> float:
    import math
    rad = math.radians(arm_bearing)
    right_x, right_y = math.cos(rad), -math.sin(rad)
    vx, vy = point[0] - centre[0], point[1] - centre[1]
    return vx * right_x + vy * right_y


def endpoint_role(side: float, cfg: PipelineConfig) -> Optional[str]:
    g = cfg.geometry
    if abs(side) < g.lane_side_min_offset:
        return None
    inbound_is_left = g.right_hand_traffic
    on_left = side < 0
    if on_left:
        return "entry" if inbound_is_left else "exit"
    return "exit" if inbound_is_left else "entry"


def way_id_says_reversed(way_id: str, pts: Sequence[geo.Point],
                         cfg: PipelineConfig) -> Optional[bool]:
    g = cfg.geometry
    if not g.use_way_id_convention:
        return None

    digits = "".join(ch for ch in str(way_id) if ch.isdigit())
    if len(digits) < g.way_id_prefix_digits:
        return None
    try:
        prefix = int(digits[:g.way_id_prefix_digits])
    except ValueError:
        return None

    if prefix in g.way_id_forward_prefixes:
        wants_increasing_x = True
    elif prefix in g.way_id_reverse_prefixes:
        wants_increasing_x = False
    else:
        return None

    dx = pts[-1][0] - pts[0][0]
    length = geo.polyline_length(pts)
    if length <= 0 or abs(dx) / length < g.way_id_min_dx_ratio:
        return None

    drawn_increasing_x = dx > 0
    return drawn_increasing_x != wants_increasing_x


def orient_movement(pts: List[geo.Point], centre: geo.Point,
                    named: Dict[str, float], cfg: PipelineConfig,
                    way_id: str = "") -> Tuple[List[geo.Point], str,
                                               Optional[str]]:
    if not cfg.geometry.infer_travel_direction:
        return pts, "digitised order", None

    first_arm = _arm_for_bearing(named, geo.bearing(centre, pts[0]))
    last_arm = _arm_for_bearing(named, geo.bearing(centre, pts[-1]))

    first_role = endpoint_role(
        lane_side(centre, named[first_arm], pts[0]), cfg)
    last_role = endpoint_role(
        lane_side(centre, named[last_arm], pts[-1]), cfg)

    if first_role == "entry" and last_role == "exit":
        geom_flip, geom_how = False, "lane side"
    elif first_role == "exit" and last_role == "entry":
        geom_flip, geom_how = True, "lane side"
    elif first_role == "entry" or last_role == "exit":
        geom_flip, geom_how = False, "lane side (one end)"
    elif first_role == "exit" or last_role == "entry":
        geom_flip, geom_how = True, "lane side (one end)"
    else:
        geom_flip, geom_how = None, None

    way_flip = way_id_says_reversed(way_id, pts, cfg)
    if way_flip is not None:
        note = None
        how = "way_id" + (" (reversed)" if way_flip else "")
        if geom_flip is not None and geom_flip != way_flip:
            how = "way_id, geometry disagrees" + (" (reversed)" if way_flip else "")
            note = (f"way_id {way_id} and the lane's position on the road "
                    f"disagree about which way it travels; following the "
                    f"way_id convention. Worth checking this lane by eye")
        return (list(reversed(pts)) if way_flip else pts), how, note

    if geom_flip is not None:
        suffix = " (reversed)" if geom_flip else ""
        return ((list(reversed(pts)) if geom_flip else pts),
                geom_how + suffix, None)

    return pts, "digitised order (fallback)", (
        "both ends sit on the road axis, so the direction of travel could not "
        "be read from the geometry; drawing order was used instead")


def _junction_centre(movement_geoms: Sequence[Sequence[geo.Point]],
                     border_geoms: Sequence[Sequence[geo.Point]]) -> geo.Point:
    pts = [p for line in movement_geoms for p in line]
    if border_geoms:
        pts += [p for line in border_geoms for p in line]
    if not pts:
        return (0.0, 0.0)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)


# ------------------------------------------------------------ movements -----

def classify_turn(points: Sequence[geo.Point], cfg: PipelineConfig) -> TurnType:
    """Turn type from the heading change along the centerline."""
    g = cfg.geometry
    delta = geo.normalise_angle(
        geo.heading_at_end(points) - geo.heading_at_start(points))
    if abs(delta) <= g.straight_max_deg:
        return TurnType.STRAIGHT
    if abs(delta) >= g.uturn_min_deg:
        return TurnType.U_TURN
    return TurnType.RIGHT if delta > 0 else TurnType.LEFT


def build_junction(features: Sequence[dict],
                   cfg: PipelineConfig = DEFAULT_CONFIG) -> Junction:
    s = cfg.schema
    (centerlines, reg_features, vru_features, border_features,
     warnings) = partition_features(features, cfg)

    if not centerlines:
        raise ExtractionError(
            f"No features with {s.lane_type}='{s.centerline_value}' in the selection.")

    junction_lines, morph_warnings = confirm_intersection(centerlines, cfg)
    warnings.extend(morph_warnings)
    if not junction_lines:
        raise ExtractionError(
            f"None of the selected centerlines carry "
            f"{s.lane_morphology}='{s.intersection_value}'. "
            f"The selection is not an intersection.")

    reg_index = build_reg_index(reg_features, cfg)

    geometries = [f["geometry"] for f in junction_lines]
    centre = geo.centroid(geometries)

    border_geoms = [f["geometry"] for f in border_features
                    if len(f.get("geometry") or []) >= 2]
    if border_geoms:
        centre = _junction_centre(geometries, border_geoms)

    endpoint_bearings: List[float] = []
    for line in geometries:
        endpoint_bearings.append(geo.bearing(centre, line[0]))
        endpoint_bearings.append(geo.bearing(centre, line[-1]))
    arm_bearings = cluster_arms(endpoint_bearings, cfg.geometry.arm_cluster_tolerance_deg)
    named = name_arms(arm_bearings)

    movements: List[Movement] = []
    arm_elements: Dict[str, List[RegulatoryElement]] = {k: [] for k in named}

    for feat in junction_lines:
        pts = list(feat["geometry"])
        if len(pts) < 2:
            warnings.append(
                f"centerline {_str_attr(feat, s.road_id)}/{_str_attr(feat, s.way_id)}:"
                f" degenerate geometry, skipped")
            continue

        direction_attr = _str_attr(feat, s.direction).lower()
        if direction_attr in ("backward", "reverse", "-1"):
            pts = list(reversed(pts))
            how = "direction column"
        elif direction_attr in ("forward", "1"):
            how = "direction column"
        else:
            pts, how, note = orient_movement(
                pts, centre, named, cfg, _str_attr(feat, s.way_id))
            if note:
                warnings.append(
                    f"centerline {_str_attr(feat, s.road_id)}/"
                    f"{_str_attr(feat, s.way_id)}: {note}")

        entry = _arm_for_bearing(named, geo.bearing(centre, pts[0]))
        exit_ = _arm_for_bearing(named, geo.bearing(centre, pts[-1]))

        turn_attr = _str_attr(feat, s.turn_intent).lower()
        if turn_attr in ("left", "right", "straight", "u_turn"):
            turn = TurnType(turn_attr)
        else:
            turn = classify_turn(pts, cfg)

        re_ids = linked_reg_ids(feat, cfg)
        elements = [reg_index[r] for r in re_ids if r in reg_index]
        for missing in [r for r in re_ids if r not in reg_index]:
            warnings.append(
                f"centerline {_str_attr(feat, s.road_id)}/{_str_attr(feat, s.way_id)}:"
                f" {s.traffic_rule} names re_id '{missing}' but no "
                f"{s.regulatory_element_value} with that {s.re_id} is in the selection")
        arm_elements.setdefault(entry, []).extend(elements)

        lane_raw = _attr(feat, s.lane_index)
        try:
            lane_idx = int(lane_raw) if lane_raw not in (None, "") else 0
        except (TypeError, ValueError):
            lane_idx = 0

        movements.append(Movement(
            feature_id=str(_attr(feat, "fid", None)
                           or f"{_str_attr(feat, s.road_id)}/{_str_attr(feat, s.way_id)}"),
            road_id=_str_attr(feat, s.road_id),
            way_id=_str_attr(feat, s.way_id),
            geometry=pts,
            entry_arm=entry,
            exit_arm=exit_,
            turn=turn,
            lane_index=lane_idx,
            signal_group=_str_attr(feat, s.signal_group) or None,
            protected=bool(_attr(feat, "protected", False)),
            reg_ids=re_ids,
            raw=feat.get("attributes", {}),
            # Read the dot_path generated by correct_lane_direction.py
            dot_path=_str_attr(feat, "dot_path"),
            direction_source=how,
            start_side=lane_side(centre, named[entry], pts[0]),
            end_side=lane_side(centre, named[exit_], pts[-1]),
        ))

    if cfg.rules.include_vru_crossings:
        for feat in vru_features:
            pts = list(feat.get("geometry") or [])
            if len(pts) < 2:
                continue
            mid = ((pts[0][0] + pts[-1][0]) / 2, (pts[0][1] + pts[-1][1]) / 2)
            arm = _arm_for_bearing(named, geo.bearing(centre, mid))
            movements.append(Movement(
                feature_id=str(_attr(feat, "fid", None)
                               or f"{_str_attr(feat, s.road_id)}/{_str_attr(feat, s.way_id)}"),
                road_id=_str_attr(feat, s.road_id),
                way_id=_str_attr(feat, s.way_id),
                geometry=pts,
                entry_arm=arm,
                exit_arm=arm,
                turn=TurnType.STRAIGHT,
                is_vru=True,
                raw=feat.get("attributes", {}),
            ))

    arms: Dict[str, Arm] = {}
    signalised = False
    for name, brg in named.items():
        priority, plate, is_signal, explicit, signals = classify_reg_elements(
            arm_elements.get(name, []), cfg)
        signalised = signalised or is_signal
        arms[name] = Arm(
            name=name,
            bearing_deg=brg,
            priority=PriorityClass.SIGNALISED if is_signal else priority,
            reg_elements=arm_elements.get(name, []),
            plate_variant=plate,
            explicit_sign=explicit,
            signal_normal=signals["normal"],
            signal_left_controlled=signals["left_controlled"],
            signal_left_green=signals["left_green"],
            signal_right_controlled=signals["right_controlled"]
        )

    junction = Junction(centre=centre, arms=arms, movements=movements,
                        signalised=signalised, warnings=warnings)
                        
    junction.has_controlled_left = any(a.signal_left_controlled for a in junction.arms.values())
    junction.has_controlled_right = any(a.signal_right_controlled for a in junction.arms.values())
    junction.has_left_green = any(a.signal_left_green for a in junction.arms.values())
    
    _apply_plate(junction, cfg)
    _check_consistency(junction, cfg)
    return junction


# ------------------------------------------------- Epic 4: 1002-xx plate ----

def _apply_plate(junction: Junction, cfg: PipelineConfig) -> None:
    variants = {arm.plate_variant for arm in junction.arms.values() if arm.plate_variant}
    if not variants:
        return
    if len(variants) > 1:
        junction.warnings.append(
            f"more than one 1002 variant on this junction: {sorted(variants)} "
            f"- using '{sorted(variants)[0]}'")
    variant = sorted(variants)[0]
    spec = cfg.signs.plate_variants.get(variant)
    if not spec:
        junction.warnings.append(f"unknown supplementary plate variant '{variant}'")
        return

    prio_arms = set(spec["prio"])
    for name, arm in junction.arms.items():
        if arm.priority == PriorityClass.SIGNALISED:
            continue
        if name in prio_arms:
            if not arm.explicit_sign:
                arm.priority = PriorityClass.PRIORITY
            elif arm.priority != PriorityClass.PRIORITY:
                junction.warnings.append(
                    f"arm {name}: plate {variant} puts the priority road through "
                    f"this arm but its own sign says {arm.priority.name}")
        else:
            if not arm.explicit_sign:
                arm.priority = PriorityClass.YIELD


def _check_consistency(junction: Junction, cfg: PipelineConfig) -> None:
    classes = {a.priority for a in junction.arms.values()}
    if PriorityClass.PRIORITY in classes and len(classes) == 1:
        junction.warnings.append(
            "every arm is signed as priority road - right-of-way is ambiguous; "
            "falling back to right-before-left between them")
    if (PriorityClass.PRIORITY in classes
            and PriorityClass.UNREGULATED in classes):
        unsigned = [n for n, a in junction.arms.items()
                    if a.priority == PriorityClass.UNREGULATED]
        junction.warnings.append(
            f"arms {unsigned} carry no sign while another arm is a priority road; "
            f"treating them as yield arms (DE 205 implied)")
        for n in unsigned:
            junction.arms[n].priority = PriorityClass.YIELD

    if (PriorityClass.YIELD in classes
            and PriorityClass.UNREGULATED in classes
            and PriorityClass.PRIORITY not in classes):
        unsigned = sorted(n for n, a in junction.arms.items()
                          if a.priority == PriorityClass.UNREGULATED)
        yielding = sorted(n for n, a in junction.arms.items()
                          if a.priority == PriorityClass.YIELD)
        junction.warnings.append(
            f"no priority sign (DE 306/301) anywhere at this junction. "
            f"Arms {yielding} carry a give-way instruction and arms {unsigned} "
            f"do not, so {unsigned} are being treated as the priority arms by "
            f"default. Check that against the junction: if {unsigned} should "
            f"also give way, the give-way marking is missing from the data")