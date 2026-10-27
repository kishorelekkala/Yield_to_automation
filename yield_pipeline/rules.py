"""
Epics 2-5 - the rule engine.

Every scenario in the reference set resolves through one ladder, applied to
each conflicting pair of movements:

    1. signals        - different phases      -> no relation (separated in time)
                      - protected arrow       -> no relation
                      - same phase, turning   -> left yields oncoming,
                                                 turns yield to open crossings
    2. signs          - lower priority class  -> yields
    3. equal class    - other arm is on my right              -> yields
                      - other is oncoming and I turn left     -> yields

The supplementary 1002-xx plate needs no branch of its own: it only decides
which arms are priority arms (handled in extract.py), after which the ladder
produces the bent-priority behaviour on its own - including a priority-arm
vehicle losing its protection against the other priority arm the moment it
leaves the course of the priority road.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from . import geometry as geo
from .config import PipelineConfig, DEFAULT_CONFIG
from .conflicts import detect_conflicts
from .model import (Conflict, ConflictType, Junction, Movement, PipelineResult,
                    PriorityClass, Reason, TurnType, YieldRelation)


# ------------------------------------------------------- spatial helpers ----

def approach_heading(junction: Junction, arm_name: str) -> float:
    """Direction a vehicle entering from this arm is travelling."""
    return (junction.arm(arm_name).bearing_deg + 180.0) % 360.0


def relative_arm_angle(junction: Junction, ego_arm: str, other_arm: str) -> float:
    """Signed angle from the ego's heading to the other arm. + = to the right."""
    return geo.relative_bearing(approach_heading(junction, ego_arm),
                                junction.arm(other_arm).bearing_deg)


def is_on_my_right(junction: Junction, ego_arm: str, other_arm: str,
                   cfg: PipelineConfig) -> bool:
    """Is the other arm to my right - and not the arm I am facing?

    The oncoming cone wins the overlap. On a skewed junction an arm can fall
    inside both windows, and then right-before-left and left-yields-oncoming
    both fire on the same pair of movements - each side told to yield to the
    other, which is a deadlock rather than a rule. An arm straight ahead of me
    is the one I meet head-on, never one approaching from my side, so treating
    it as oncoming is the correct reading.
    """
    rel = relative_arm_angle(junction, ego_arm, other_arm)
    if abs(rel) <= cfg.geometry.ahead_max_deg:
        return False
    lo, hi = cfg.geometry.right_window_deg
    return lo <= rel <= hi


def is_oncoming(junction: Junction, ego_arm: str, other_arm: str,
                cfg: PipelineConfig) -> bool:
    """The other arm is the one straight ahead of the ego."""
    return abs(relative_arm_angle(junction, ego_arm, other_arm)) \
        <= cfg.geometry.ahead_max_deg


def opposite_arm(junction: Junction, arm_name: str,
                 cfg: PipelineConfig) -> Optional[str]:
    for other in junction.arms:
        if other != arm_name and is_oncoming(junction, arm_name, other, cfg):
            return other
    return None


# -------------------------------------------------------------- signals ----

def phase_of(junction: Junction, movement: Movement,
             cfg: PipelineConfig) -> Optional[str]:
    """Which signal phase a movement runs in."""
    if movement.signal_group:
        return movement.signal_group
    if not cfg.rules.assume_two_phase_when_unknown:
        return None
    # Default two-phase plan: opposite arms run together.
    partner = opposite_arm(junction, movement.entry_arm, cfg)
    pair = sorted([movement.entry_arm, partner]) if partner else [movement.entry_arm]
    return "|".join(pair)


def phase_arms(junction: Junction, movement: Movement,
               cfg: PipelineConfig) -> set:
    """Which approach arms hold green at the same time as this movement."""
    phase = phase_of(junction, movement, cfg)
    if phase is None:
        return {movement.entry_arm}
    return {m.entry_arm for m in junction.movements
            if not m.is_vru and phase_of(junction, m, cfg) == phase}


def crossing_is_open(junction: Junction, ego: Movement, crossing: Movement,
                     cfg: PipelineConfig) -> bool:
    """Is the pedestrian signal on that crossing's arm green while ego runs?

    In a German staged plan the crossing over an arm is held at red exactly
    while that arm's vehicles have green - otherwise people and cars would be
    released onto the same asphalt. So a crossing is open to pedestrians when
    its arm is NOT in the ego's phase. That is why a straight-through vehicle
    never waits (it only ever meets the crossings on its own two arms, both
    red) while a turner does (it leaves across an arm that is red for cars).
    """
    return crossing.entry_arm not in phase_arms(junction, ego, cfg)


# ----------------------------------------------------------- the ladder ----

def resolve(ego: Movement, other: Movement, conflict: Conflict,
            junction: Junction, cfg: PipelineConfig) -> Tuple[bool, Reason]:
    """Does `ego` give way to `other`? Returns (yields, reason)."""

    # --- 0. pedestrians and cyclists ---------------------------------
    if other.is_vru:
        if junction.signalised:
            # A protected arrow is only protected because the crossing is held.
            if ego.protected and cfg.rules.protected_clears_vru:
                return False, Reason.SIGNAL_PROTECTED
            if not crossing_is_open(junction, ego, other, cfg):
                return False, Reason.SIGNAL_PHASE_SEPARATED
            return True, Reason.SIGNAL_TURN_VRU
        # No signals: everyone gives way to people already on the crossing
        # (StVO 26 at a marked crossing, StVO 9(3) when turning).
        return True, Reason.VRU_CROSSING
    if ego.is_vru:
        return False, Reason.NO_CONFLICT

    ego_arm = junction.arm(ego.entry_arm)
    other_arm = junction.arm(other.entry_arm)

    # --- 1. signal-controlled junction --------------------------------
    if junction.signalised:
        ego_phase = phase_of(junction, ego, cfg)
        other_phase = phase_of(junction, other, cfg)
        if (cfg.rules.phases_separate_in_time and ego_phase and other_phase
                and ego_phase != other_phase):
            return False, Reason.SIGNAL_PHASE_SEPARATED
        if ego.protected:
            return False, Reason.SIGNAL_PROTECTED
        if other.protected:
            # The protected movement holds the junction; anything still
            # running alongside it gives way.
            return True, Reason.SIGNAL_PROTECTED
        if ego.turn is TurnType.LEFT and is_oncoming(
                junction, ego.entry_arm, other.entry_arm, cfg):
            return True, Reason.SIGNAL_UNPROTECTED_LEFT
        return False, Reason.EQUAL_NO_RULE

    # --- 2. sign-controlled: compare priority classes -----------------
    if ego_arm.priority != other_arm.priority:
        if ego_arm.priority < other_arm.priority:
            return True, Reason.SIGN_LOWER_CLASS
        return False, Reason.SIGN_HIGHER_CLASS

    # --- 3. equal class: right-before-left, then left-yields-oncoming --
    if is_on_my_right(junction, ego.entry_arm, other.entry_arm, cfg):
        return True, Reason.RIGHT_BEFORE_LEFT

    if (ego.turn is TurnType.LEFT
            and is_oncoming(junction, ego.entry_arm, other.entry_arm, cfg)):
        return True, Reason.LEFT_YIELDS_ONCOMING

    return False, Reason.EQUAL_NO_RULE


# ----------------------------------------------------------------- run -----

def evaluate(junction: Junction,
             cfg: PipelineConfig = DEFAULT_CONFIG) -> PipelineResult:
    """Run conflict detection and the rule ladder over a whole junction."""
    conflicts = detect_conflicts(junction.movements, cfg)

    relations: List[YieldRelation] = []
    decisions: List[Dict] = []

    for conflict in conflicts:
        for ego, other in ((conflict.a, conflict.b), (conflict.b, conflict.a)):
            yields, reason = resolve(ego, other, conflict, junction, cfg)
            decisions.append({
                "ego": ego.key,
                "ego_id": f"{ego.road_id}/{ego.way_id}",
                "other": other.key,
                "other_id": f"{other.road_id}/{other.way_id}",
                "conflict": conflict.kind.value,
                "yields": yields,
                "reason": reason.value,
                "ego_class": junction.arm(ego.entry_arm).priority.name
                             if not ego.is_vru else "VRU",
                "other_class": junction.arm(other.entry_arm).priority.name
                               if not other.is_vru else "VRU",
            })
            if yields:
                relations.append(YieldRelation(ego, other, conflict.kind, reason))

    yield_to: Dict[str, List[Movement]] = {m.feature_id: [] for m in junction.movements}
    for rel in relations:
        bucket = yield_to.setdefault(rel.ego.feature_id, [])
        if rel.other not in bucket:
            bucket.append(rel.other)

    warnings = list(junction.warnings)
    warnings.extend(_sanity_checks(junction, relations))

    return PipelineResult(junction=junction, relations=relations,
                          yield_to=yield_to, decisions=decisions,
                          warnings=warnings)


def _sanity_checks(junction: Junction, relations: Sequence[YieldRelation]
                   ) -> List[str]:
    """Catch rule outcomes that cannot be right, so they surface in QGIS."""
    out: List[str] = []
    pairs = {(r.ego.feature_id, r.other.feature_id) for r in relations}

    # Mutual yield: both sides waiting for each other is a deadlock.
    for ego_id, other_id in pairs:
        if (other_id, ego_id) in pairs and ego_id < other_id:
            out.append(
                f"deadlock: {ego_id} and {other_id} are each set to yield to "
                f"the other - check the signs on their approaches")

    # A movement on the strongest class that still yields to a weaker one.
    for rel in relations:
        if rel.ego.is_vru or rel.other.is_vru:
            continue
        ego_c = junction.arm(rel.ego.entry_arm).priority
        other_c = junction.arm(rel.other.entry_arm).priority
        if ego_c > other_c:
            out.append(
                f"unexpected: {rel.ego.key} ({ego_c.name}) yields to "
                f"{rel.other.key} ({other_c.name}) - {rel.reason.value}")
    return out
