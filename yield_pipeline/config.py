"""
Schema configuration for the HD-Map yield-to pipeline.

Every column name, delimiter and sign code the pipeline touches lives here, so
that only this file changes when the attribute table changes.

TUNED FOR THE REAL EXPORT - see the CHANGED notes below. The column names in
the export already match the defaults, so the edits here are about the shape
of the *values*, not the names.
"""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class SchemaConfig:
    """Names of the columns in the HD-Map attribute table.

    Confirmed present in the export:
        road_id, lane_type, line_type, line_sub, re_id, way_id,
        usage, one_way, lane_morphology, speed_limit, traffic_rule
    """

    # --- identity -------------------------------------------------------
    road_id: str = "road_id"
    way_id: str = "way_id"

    # --- classification -------------------------------------------------
    lane_type: str = "lane_type"
    lane_morphology: str = "lane_morphology"
    line_type: str = "line_type"
    line_sub: str = "line_sub"

    # --- linkage --------------------------------------------------------
    re_id: str = "re_id"
    traffic_rule: str = "traffic_rule"

    # --- output ---------------------------------------------------------
    yield_to: str = "yield_to"

    # --- optional, used only when the column exists ----------------------
    # None of these are in the export yet. The pipeline infers each one from
    # the geometry instead, and keeps working. Point them at real columns if
    # they are ever added.
    #
    # NOTE: the export has `one_way`, which is NOT the same thing. `direction`
    # means "is this line drawn backwards relative to travel"; `one_way` means
    # "is this road single-direction". Do not wire one to the other.
    direction: str = "direction"          # "forward" / "backward"
    turn_intent: str = "turn_direction"   # "left" / "straight" / "right"
    lane_index: str = "lane_index"        # 0 = kerbside, increasing inward
    signal_group: str = "signal_group"    # phase id at signalised junctions

    # --- values ---------------------------------------------------------
    # Confirmed against the export: lane_type is 'centerline' or
    # 'regulatory_element'; lane_morphology is 'intersection'.
    centerline_value: str = "centerline"
    intersection_value: str = "intersection"
    regulatory_element_value: str = "regulatory_element"
    traffic_sign_value: str = "traffic_sign"
    traffic_light_value: str = "traffic_light"

    # CHANGED: lane_type='road' is in the export. These are the road borders /
    # road reference lines - the physical edges of the carriageway. They are
    # context, not manoeuvres: they never get a yield_to value of their own.
    # They are used to locate the junction centre, which makes arm detection
    # far steadier than relying on centerline endpoints alone.
    road_border_values: tuple = ("road", "road_border", "border", "roadedge")

    # CHANGED: widened, because the export's crossing lane_type is not yet
    # known. Add yours to whichever tuple fits; unknown lane_types are skipped
    # with a warning rather than silently ignored, so the log will name it.
    crosswalk_values: tuple = ("crosswalk", "pedestrian_crossing", "zebra",
                               "pedestrian", "walkway", "footpath")
    cycle_values: tuple = ("bicycle_crossing", "cycle_crossing", "bicycle",
                           "cycleway", "bike_crossing")

    # traffic_rule may hold several re_ids in one cell. Any of these
    # characters separates them. The export shows single values (re_975), but
    # a centerline governed by both a sign and a light will hold two.
    #
    # CHANGED: space removed from the separator list. The export writes codes
    # as '(de306)', but a human-entered '(DE 306)' would otherwise be split
    # into 'de' and '306' and never match. Commas are what the export uses.
    # Put the space back only if your re_ids turn out to be space-separated.
    multi_value_separators: str = ",;|"

    # CHANGED: QGIS NULLs sometimes arrive as the literal text "NULL" rather
    # than a real null (CSV exports, some providers). Treated as empty.
    null_values: tuple = ("null", "none", "nan", "", "na", "-")


@dataclass
class OutputConfig:
    """How the yield_to cell is written."""

    # Item format:  road_id''_way_id   e.g.  106_20012
    item_template: str = "{road_id}_{way_id}"
    item_separator: str = ","
    quote_items: bool = False       # wrap each item in double quotes
    write_empty_as: str = ""       # what a non-yielding centerline gets
    deduplicate: bool = True       # never repeat the same id
    sort_items: bool = True        # stable, diff-friendly output


@dataclass
class SignCodes:
    """German sign codes as they appear in `line_sub`.

    The export writes them in parentheses: (de112), (de341). normalise_code()
    strips the brackets, so 'de306', '(de306)', 'DE 306' and '306' all match.
    """

    priority_road: tuple = ("306",)             # Vorfahrtstrasse
    priority_at_next: tuple = ("301",)          # Vorfahrt (single junction)
    stop_sign: tuple = ("206",)               # Halt. Vorfahrt gewaehren
    traffic_light: tuple = ("201", "1000",)     # Lichtzeichenanlage
    traffic_light: tuple = ("201", "1000", "redyellowgreen")
    light_left_controlled: tuple = ("arrowleftredyellowgreen",)
    light_left_green: tuple = ("arrowleftgreen",)
    light_right_controlled: tuple = ("arrowrightredyellowgreen", "arrowrightyellowgreen")

    # ONLY the primary regulatory signs decide priority: 306, 205, 206 (and
    # 301). If none of those is present the junction is unsignalised and
    # right-before-left applies - that is the specification.
    #
    # de341 (Haifischzaehne, the shark-tooth give-way line painted on the
    # road) is NOT in this list on purpose. It is a road marking, not a
    # primary sign. Where a junction is really signed, the 205 sign is in the
    # data alongside it; where only 341 appears, the junction counts as
    # unsignalised. Adding "341" here would silently turn right-before-left
    # junctions into signed ones.
    yield_sign: tuple = ("205",)

    # Codes with no priority meaning need no entry here. Anything that is not
    # listed above is simply not a priority instruction, so it leaves the arm
    # UNREGULATED and right-before-left applies. The export's (de112) -
    # Unebene Fahrbahn, uneven road - is handled that way: seen, ignored.

    # Zusatzzeichen 1002-xx: course of the priority road through the junction.
    supplementary_prefix: str = "1002"

    # 1002 variant -> which arms the priority road connects.
    plate_variants: Dict[str, Dict[str, tuple]] = field(default_factory=lambda: {
        "1002-10": {"prio": ("S", "W"), "minors": ("N", "E")},
        "1002-11": {"prio": ("N", "W"), "minors": ("S", "E")},
        "1002-20": {"prio": ("S", "E"), "minors": ("N", "W")},
        "1002-21": {"prio": ("N", "E"), "minors": ("S", "W")},
        "1002-12": {"prio": ("S", "W"), "minors": ("N",)},
        "1002-13": {"prio": ("S", "W"), "minors": ("E",)},
        "1002-14": {"prio": ("N", "W"), "minors": ("S",)},
        "1002-22": {"prio": ("S", "E"), "minors": ("N",)},
        "1002-23": {"prio": ("S", "E"), "minors": ("W",)},
        "1002-24": {"prio": ("N", "E"), "minors": ("S",)},
    })


@dataclass
class GeometryConfig:
    """Tolerances for the geometric stage. Units = layer CRS units.

    These assume a PROJECTED CRS in metres (UTM / Gauss-Krueger). If the layer
    is in degrees the tolerances are meaningless - reproject first.
    """

    # Bearings within this many degrees are treated as the same arm.
    arm_cluster_tolerance_deg: float = 35.0

    # Classifying a movement's turn from its heading change.
    straight_max_deg: float = 45.0     # |delta| below this -> STRAIGHT
    uturn_min_deg: float = 150.0       # |delta| above this -> U_TURN

    # Relative-bearing windows, measured from the vehicle's approach heading
    # (positive = clockwise = to its right).
    right_window_deg: tuple = (40.0, 140.0)     # other arm is on my right
    ahead_max_deg: float = 45.0                 # |rel| below this -> oncoming

    # Two vertices closer than this count as the same point.
    point_snap: float = 0.25

    # A crossing closer than this to a shared exit is a merge, not a cross.
    merge_tail_tolerance: float = 3.0

    # Paths that never strictly cross but pass this close still conflict -
    # a vehicle has width. Raise it for coarsely drawn maps, lower it if the
    # log reports conflicts between movements that clearly never meet.
    conflict_clearance: float = 1.0

    # Yield line placement: how far back from the first conflict point, and
    # half-width either side of the centerline.
    yield_line_setback: float = 2.0
    yield_line_half_width: float = 1.75

    # --- travel direction -------------------------------------------------
    # CHANGED. The old code assumed a centerline's FIRST vertex is where the
    # car starts, i.e. that every line is digitised in the direction of
    # travel. That assumption is false in the real export: two lanes of one
    # road carrying opposite traffic are both drawn from the same end, so both
    # were read as going the same way.
    #
    # With this on, the direction is taken from WHERE THE LANE SITS instead.
    # In right-hand traffic, looking outward from the junction along an arm,
    # the inbound lane is on the left and the outbound lane is on the right.
    # Drawing order stops mattering.
    #
    # Set False to go back to trusting the digitisation order.
    infer_travel_direction: bool = True

    # How far off the arm axis an endpoint must sit before its side is
    # believed. Below this the two lanes are too close to tell apart and the
    # line is reported as ambiguous rather than guessed at.
    lane_side_min_offset: float = 0.4

    # False for left-hand traffic (UK, IE, JP): the sides swap.
    right_hand_traffic: bool = True

    # --- the converter's own lane-numbering convention --------------------
    # The HD-map converter builds a centerline's way_id as
    #
    #     centerline_id = int(f"{min(left_border, right_border)}12")
    #
    # so borders 100 & 200 give 10012, and borders 300 & 200 give 20012. The
    # leading digits are therefore the border number, and the converter's
    # line_direction_logic() says which way a lane with that border travels:
    # the 100/400/500 series runs with increasing x, the 200/300/700/800
    # series runs with decreasing x.
    #
    # That is the data producer's own statement of direction, so it is trusted
    # ahead of anything inferred from geometry. The geometric lane-side rule
    # is kept as a cross-check and as the fallback for way_ids that do not
    # match the convention.
    use_way_id_convention: bool = True
    way_id_prefix_digits: int = 3
    way_id_forward_prefixes: tuple = (100, 101, 102, 103,
                                      400, 401, 402, 403, 404, 500)
    way_id_reverse_prefixes: tuple = (200, 201, 202, 203, 204,
                                      300, 600, 700, 800, 801, 802, 803)

    # The convention is expressed in x, so it only means something on a road
    # with a real east-west component. On a road running nearly due north the
    # x difference is noise. Below this ratio of |dx| to the line's length the
    # way_id rule stands aside and the lane-side geometry decides.
    way_id_min_dx_ratio: float = 0.15


@dataclass
class RuleConfig:
    """Switches for the rule engine."""

    # DE 206 (Stop) yields to the same traffic as DE 205; the difference is
    # the mandatory halt, which is not a yield_to relation.
    stop_equals_yield: bool = True

    # Opposing left turns pass front-to-front in Germany, so no relation
    # unless their geometries actually cross.
    opposing_lefts_conflict: bool = False

    # Include pedestrian / cycle crossing lanelets in yield_to.
    include_vru_crossings: bool = True

    # A protected arrow holds its crossing at red (reference scenario 3).
    # Scenario 4's prose says the opposite for the same situation - set this
    # False for that reading.
    protected_clears_vru: bool = True

    # Signalised junctions: with no signal_group column, assume a two-phase
    # cycle pairing opposite arms.
    assume_two_phase_when_unknown: bool = True

    # Movements separated in time by the signal plan get no yield relation.
    phases_separate_in_time: bool = True


@dataclass
class PipelineConfig:
    schema: SchemaConfig = field(default_factory=SchemaConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    signs: SignCodes = field(default_factory=SignCodes)
    geometry: GeometryConfig = field(default_factory=GeometryConfig)
    rules: RuleConfig = field(default_factory=RuleConfig)


DEFAULT_CONFIG = PipelineConfig()


# CHANGED: the export writes sign codes wrapped in brackets - (de112), (de341).
# Brackets and quotes are now stripped along with spaces, underscores and
# dots, so every spelling below collapses to the same comparable string:
#
#     (de306)  ->  de306        DE 306   ->  de306
#     de_306   ->  de306        "306"    ->  306
#     (1002-10) -> 1002-10      de306-1  ->  de306-1
#
# Hyphens are deliberately NOT stripped: the 1002 plate variants need them.
_STRIP_CHARS = (" ", "_", ".", "(", ")", "[", "]", "{", "}", "<", ">", '"', "'")


def normalise_code(value: str) -> str:
    """'(DE 306)' -> 'de306'  so that sign codes compare reliably."""
    if value is None:
        return ""
    out = str(value).strip().lower()
    for ch in _STRIP_CHARS:
        out = out.replace(ch, "")
    return out


def _clean_token(token: str) -> str:
    """Strip whitespace and wrapping brackets/quotes from one value."""
    out = str(token).strip()
    while out and out[0] in "([{<\"'":
        out = out[1:].strip()
    while out and out[-1] in ")]}>\"'":
        out = out[:-1].strip()
    return out


def _is_null(token: str, null_values: tuple) -> bool:
    """CHANGED: the export writes empty cells as NULL and as (NULL).

    The brackets are stripped before the test, so both spellings - and
    '( NULL )' - are recognised as empty rather than read as a value.
    """
    return _clean_token(token).lower() in null_values


def split_multi(value, separators: str,
                null_values: tuple = SchemaConfig.null_values) -> List[str]:
    """Split a cell that may hold several values into a clean list."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if not _is_null(v, null_values)]
    text = str(value).strip()
    if _is_null(text, null_values):
        return []
    for sep in separators[1:]:
        text = text.replace(sep, separators[0])
    return [part.strip() for part in text.split(separators[0])
            if not _is_null(part, null_values)]
