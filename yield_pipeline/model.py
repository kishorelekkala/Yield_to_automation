"""Core data model: arms, movements, regulatory elements, conflicts, results."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Dict, List, Optional, Sequence, Tuple

Point = Tuple[float, float]


class TurnType(str, Enum):
    STRAIGHT = "straight"
    LEFT = "left"
    RIGHT = "right"
    U_TURN = "u_turn"


class PriorityClass(IntEnum):
    """Higher value = stronger claim on the junction.

    Ordering is what the rule engine compares, so the numbers matter.
    """

    STOP = 0          # DE 206
    YIELD = 1         # DE 205
    UNREGULATED = 2   # no sign -> right-before-left applies between equals
    PRIORITY = 3      # DE 306 / DE 301
    SIGNALISED = 4    # signal plan governs; class is not compared directly


class ConflictType(str, Enum):
    CROSS = "cross"     # paths intersect inside the junction
    MERGE = "merge"     # paths end in the same outgoing lane
    NONE = "none"


class Reason(str, Enum):
    """Why a yield relation was (or was not) emitted. Carried into the log."""

    SIGNAL_PHASE_SEPARATED = "signal: movements in different phases"
    SIGNAL_PROTECTED = "signal: protected arrow, conflict is held"
    SIGNAL_UNPROTECTED_LEFT = "signal: unprotected left yields to oncoming"
    SIGNAL_TURN_VRU = "signal: turning across an open crossing"
    SIGN_LOWER_CLASS = "sign: lower priority class yields"
    SIGN_HIGHER_CLASS = "sign: higher priority class does not yield"
    RIGHT_BEFORE_LEFT = "right-before-left: other approaches from my right"
    LEFT_YIELDS_ONCOMING = "left turn yields to oncoming traffic"
    VRU_CROSSING = "turning across a pedestrian/cycle crossing"
    NO_CONFLICT = "paths do not conflict"
    SAME_APPROACH = "same approach arm: queueing, not yielding"
    EQUAL_NO_RULE = "equal class, other is not from the right and not oncoming"


@dataclass
class RegulatoryElement:
    """A lanelet with lane_type == regulatory_element linked via re_id."""

    re_id: str
    line_type: Optional[str] = None     # traffic_sign / traffic_light / ...
    line_sub: Optional[str] = None      # de306, de205, de206, 1002-10, ...
    codes: List[str] = field(default_factory=list)   # normalised sign codes
    geometry: Optional[List[Point]] = None
    raw: Dict = field(default_factory=dict)

    def has_code(self, candidates: Sequence[str]) -> bool:
        for code in self.codes:
            for want in candidates:
                if code == f"de{want}" or code == want:
                    return True
        return False

    def plate_variant(self, prefix: str) -> Optional[str]:
        """Return e.g. '1002-10' if this element carries a 1002 plate."""
        for code in self.codes:
            stripped = code[2:] if code.startswith("de") else code
            if stripped.startswith(prefix):
                return stripped
        return None


@dataclass
class Arm:
    """One leg of the junction, carrying all movements that enter from it."""

    name: str                      # N / S / E / W, or A1.. for extra legs
    bearing_deg: float             # from junction centre outwards
    priority: PriorityClass = PriorityClass.UNREGULATED
    reg_elements: List[RegulatoryElement] = field(default_factory=list)
    plate_variant: Optional[str] = None
    explicit_sign: bool = False
    signal_normal: bool = False
    signal_left_controlled: bool = False
    signal_left_green: bool = False
    signal_right_controlled: bool = False    # class came from a sign, not a default

    def __repr__(self) -> str:                      # pragma: no cover
        return f"<Arm {self.name} {self.bearing_deg:.0f}deg {self.priority.name}>"


@dataclass
class Movement:
    """One intersection centerline: a single manoeuvre through the junction."""

    feature_id: str
    road_id: str
    way_id: str
    geometry: List[Point]
    entry_arm: str
    exit_arm: str
    turn: TurnType
    lane_index: int = 0
    signal_group: Optional[str] = None
    protected: bool = False            # has its own green arrow
    is_vru: bool = False               # pedestrian / cycle crossing
    reg_ids: List[str] = field(default_factory=list)
    raw: Dict = field(default_factory=dict)
    # New field to hold the compass direction
    dot_path: str = ""

    direction_source: str = "digitised order"

    # How the direction of travel was decided, and the sideways offset of each
    # end from its arm axis (negative = left looking outward = inbound side).
    # Carried so the choice can be audited rather than taken on trust.
    direction_source: str = "digitised order"
    start_side: float = 0.0
    end_side: float = 0.0

    @property
    def key(self) -> str:
        return f"{self.entry_arm}->{self.exit_arm}"

    @property
    def exit_lane_key(self) -> str:
        """Identifies the outgoing lane a movement feeds."""
        return f"{self.exit_arm}#{self.lane_index}"

    def __repr__(self) -> str:                      # pragma: no cover
        return f"<Movement {self.key} {self.turn.value} {self.road_id}/{self.way_id}>"


@dataclass
class Conflict:
    a: Movement
    b: Movement
    kind: ConflictType
    point: Optional[Point] = None
    dist_along_a: Optional[float] = None
    dist_along_b: Optional[float] = None


@dataclass
class YieldRelation:
    """`ego` must give way to `other`."""

    ego: Movement
    other: Movement
    conflict: ConflictType
    reason: Reason

    def __repr__(self) -> str:                      # pragma: no cover
        return f"<Yield {self.ego.key} -> {self.other.key} ({self.reason.value})>"


@dataclass
class Junction:
    centre: Point
    arms: Dict[str, Arm]
    movements: List[Movement]
    signalised: bool = False
    warnings: List[str] = field(default_factory=list)
    has_controlled_left: bool = False
    has_controlled_right: bool = False
    has_left_green: bool = False

    def arm(self, name: str) -> Arm:
        return self.arms[name]

    def movements_from(self, arm: str) -> List[Movement]:
        return [m for m in self.movements if m.entry_arm == arm]


@dataclass
class PipelineResult:
    junction: Junction
    relations: List[YieldRelation]
    yield_to: Dict[str, List[Movement]]      # movement.feature_id -> others
    decisions: List[Dict] = field(default_factory=list)   # full audit trail
    warnings: List[str] = field(default_factory=list)
