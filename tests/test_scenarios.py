"""
Verification against the reference scenario set (German Intersection Rules).

Each test builds the junction the reference animates and asserts the yield_to
sets the engine produces. Run with pytest, or directly:  python tests/test_scenarios.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yield_pipeline import DEFAULT_CONFIG, build_junction, evaluate   # noqa: E402
from yield_pipeline.config import PipelineConfig, RuleConfig          # noqa: E402
from tests.fixtures import all_yields, make_junction, yield_keys      # noqa: E402

PRIO = ["de306"]
YIELD = ["de205"]
STOP = ["de206"]


def _run(features, cfg=DEFAULT_CONFIG):
    return evaluate(build_junction(features, cfg), cfg)


# =============================================== Scenario 6: right-before-left

def test_sc6_ph1_straight_yields_to_the_right():
    """S->N gives way to everything out of the east arm, whichever way it turns."""
    res = _run(make_junction([("S", "N"), ("E", "W"), ("E", "N"), ("E", "S")]))
    assert yield_keys(res, "S->N") == {"E->W", "E->N", "E->S"}
    for key in ("E->W", "E->N", "E->S"):
        assert yield_keys(res, key) == set(), f"{key} should not yield"


def test_sc6_ph2_four_right_turns_nobody_yields():
    res = _run(make_junction([("S", "E"), ("N", "W"), ("W", "S"), ("E", "N")]))
    assert all(v == set() for v in all_yields(res).values()), all_yields(res)


def test_sc6_ph3_left_turn_goes_last():
    res = _run(make_junction(
        [("S", "W"), ("N", "S"), ("N", "W"), ("E", "W"), ("E", "S")]))
    assert yield_keys(res, "S->W") == {"N->S", "N->W", "E->W", "E->S"}
    # The east arm in turn gives way to the north arm, which is on its right.
    assert yield_keys(res, "E->W") == {"N->S", "N->W"}
    # Nothing is to the north arm's right here, so it goes first.
    assert yield_keys(res, "N->S") == set()
    assert yield_keys(res, "N->W") == set()


# ====================================== Scenario 5: priority E-W, yield N-S

SC5_SIGNS = {"E": PRIO, "W": PRIO, "N": YIELD, "S": YIELD}


def test_sc5_ph1_priority_straight_and_right_are_free():
    res = _run(make_junction(
        [("W", "E"), ("W", "S"), ("E", "W"), ("E", "N")], SC5_SIGNS))
    assert all(v == set() for v in all_yields(res).values()), all_yields(res)


def test_sc5_ph2_priority_left_still_yields_to_oncoming():
    res = _run(make_junction([("W", "N"), ("E", "W"), ("E", "N")], SC5_SIGNS))
    assert yield_keys(res, "W->N") == {"E->W", "E->N"}
    assert yield_keys(res, "E->W") == set()
    assert yield_keys(res, "E->N") == set()


def test_sc5_ph3_yield_arm_straight_waits_for_the_whole_priority_road():
    res = _run(make_junction(
        [("S", "N"), ("W", "E"), ("E", "W"), ("E", "S"), ("E", "N"), ("W", "N")],
        SC5_SIGNS))
    assert yield_keys(res, "S->N") == {"W->E", "E->W", "E->S", "E->N", "W->N"}
    # A priority left turn still gives way to the oncoming priority traffic.
    assert "W->E" in yield_keys(res, "E->S")
    assert "E->W" in yield_keys(res, "W->N")
    # ...but never to the yield arm.
    assert "S->N" not in yield_keys(res, "E->S")


def test_sc5_ph4_yield_arm_right_turn_merges():
    res = _run(make_junction([("W", "E"), ("S", "E")], SC5_SIGNS))
    assert yield_keys(res, "S->E") == {"W->E"}
    assert yield_keys(res, "W->E") == set()


def test_sc5_ph5_yield_arm_left_turn_goes_last():
    res = _run(make_junction(
        [("S", "W"), ("W", "E"), ("E", "W"), ("E", "S"),
         ("W", "N"), ("N", "S"), ("N", "W")], SC5_SIGNS))
    assert yield_keys(res, "S->W") == {
        "W->E", "E->W", "E->S", "W->N", "N->S", "N->W"}


def test_stop_sign_behaves_like_yield():
    signs = {"E": PRIO, "W": PRIO, "N": STOP, "S": STOP}
    res = _run(make_junction([("W", "E"), ("S", "E")], signs))
    assert yield_keys(res, "S->E") == {"W->E"}


# ================================ Scenario 5b: DE 1002-10, every movement

ALL_12 = [(a, b) for a in "NESW" for b in "NESW"
          if a != b and not (a, b) in (("N", "S"), ("S", "N"),
                                       ("E", "W"), ("W", "E"))] \
    + [("N", "S"), ("S", "N"), ("E", "W"), ("W", "E")]

PLATE_10 = {
    "S": ["de306", "1002-10"], "W": ["de306", "1002-10"],
    "N": ["de205", "1002-10"], "E": ["de205", "1002-10"],
}

# Taken movement by movement from CASES_10 in the reference file.
EXPECTED_1002_10 = {
    "S->W": set(),
    "S->N": set(),
    "S->E": set(),
    "W->S": set(),
    "W->E": {"S->W", "S->N", "S->E"},
    "W->N": {"S->W", "S->N"},
    "N->S": {"S->W", "W->S", "W->E", "W->N"},
    "N->W": {"S->W"},
    "N->E": {"S->N", "S->E", "W->N", "W->E"},
    "E->N": {"S->N", "W->N"},
    "E->W": {"S->W", "S->N", "W->N", "N->S", "N->W", "N->E"},
    "E->S": {"S->W", "S->N", "W->S", "W->E", "N->S", "N->E"},
}


def test_sc5b_bent_priority_every_movement():
    res = _run(make_junction(ALL_12, PLATE_10))
    got = all_yields(res)
    for key, expect in EXPECTED_1002_10.items():
        assert got[key] == expect, (
            f"{key}: expected {sorted(expect)}, got {sorted(got[key])}")


def test_plate_fills_in_unsigned_minor_arms():
    """Only the priority arms carry a sign; the plate classifies the rest."""
    signs = {"S": ["de306", "1002-10"], "W": ["de306", "1002-10"]}
    res = _run(make_junction(ALL_12, signs))
    assert res.junction.arm("N").priority.name == "YIELD"
    assert res.junction.arm("E").priority.name == "YIELD"
    assert all_yields(res)["E->W"] == EXPECTED_1002_10["E->W"]


# ============================================= Scenario 5c: DE 1002-12 (T)

PLATE_12 = {"S": ["de306", "1002-12"], "W": ["de306", "1002-12"],
            "N": ["de205", "1002-12"]}

T_MOVES = [("S", "W"), ("S", "N"), ("W", "S"), ("W", "N"),
           ("N", "S"), ("N", "W")]

EXPECTED_1002_12 = {
    "S->W": set(),
    "S->N": set(),
    "W->S": set(),
    "W->N": {"S->W", "S->N"},
    "N->S": {"S->W", "W->S", "W->N"},
    "N->W": {"S->W"},
}


def test_sc5c_bent_priority_t_junction():
    res = _run(make_junction(T_MOVES, PLATE_12))
    got = all_yields(res)
    for key, expect in EXPECTED_1002_12.items():
        assert got[key] == expect, (
            f"{key}: expected {sorted(expect)}, got {sorted(got[key])}")


# ========================================== Scenario 1-4: signal control

NS_PHASE = [("N", "S"), ("N", "W"), ("N", "E"), ("S", "N"), ("S", "E"), ("S", "W")]
EW_PHASE = [("E", "W"), ("E", "N"), ("E", "S"), ("W", "E"), ("W", "N"), ("W", "S")]


def test_sc1_two_phase_unprotected_left_yields_to_oncoming():
    res = _run(make_junction(NS_PHASE + EW_PHASE, signalised=True))
    # Left turners give way to the oncoming arm only.
    assert yield_keys(res, "S->W") == {"N->S", "N->W"}
    assert yield_keys(res, "N->E") == {"S->N", "S->E"}
    # Straight and right go, and nothing crosses phases.
    assert yield_keys(res, "S->N") == set()
    assert yield_keys(res, "S->E") == set()
    for other in ("E->W", "W->E", "E->S", "W->N"):
        assert other not in yield_keys(res, "S->W")


def test_sc1_right_turn_yields_to_the_crossing_it_exits_into():
    res = _run(make_junction(NS_PHASE, signalised=True, crossings=("E", "W")))
    assert any(m.is_vru for m in res.junction.movements)
    assert "XE/WXE" in {f"{m.road_id}/{m.way_id}"
                        for m in res.yield_to[_fid(res, "S->E")]}
    # A straight-through movement does not: its own crossing is held at red.
    assert res.yield_to[_fid(res, "S->N")] == []


def test_sc1_turner_waits_only_for_the_crossing_it_exits_into():
    """The crossing over the ego's own arm is red while the ego has green."""
    res = _run(make_junction(NS_PHASE + EW_PHASE, signalised=True,
                             crossings=("N", "S", "E", "W")))

    def vru(key):
        return {m.way_id for m in res.yield_to[_fid(res, key)] if m.is_vru}

    assert vru("S->E") == {"WXE"}          # right turn: exit crossing only
    assert vru("S->W") == {"WXW"}          # left turn: exit crossing only
    assert vru("S->N") == set()            # straight: its crossings are held
    assert vru("E->N") == {"WXN"}


def test_unsignalised_junction_everyone_yields_to_a_marked_crossing():
    res = _run(make_junction([("S", "N"), ("S", "E")], crossings=("N", "E")))
    assert {m.way_id for m in res.yield_to[_fid(res, "S->N")] if m.is_vru} == {"WXN"}
    assert {m.way_id for m in res.yield_to[_fid(res, "S->E")] if m.is_vru} == {"WXE"}


def test_sc2_protected_left_arrow_clears_the_turn():
    res = _run(make_junction(NS_PHASE + EW_PHASE, signalised=True,
                             protected=[("S", "W"), ("N", "E")]))
    assert yield_keys(res, "S->W") == set()
    assert yield_keys(res, "N->E") == set()


def test_sc3_protected_right_arrow_holds_the_crossing():
    res = _run(make_junction(NS_PHASE, signalised=True, crossings=("E", "W"),
                             protected=[("S", "E")]))
    assert res.yield_to[_fid(res, "S->E")] == []


def test_sc4_protected_right_can_be_made_to_yield_to_pedestrians():
    cfg = PipelineConfig(rules=RuleConfig(protected_clears_vru=False))
    res = _run(make_junction(NS_PHASE, signalised=True, crossings=("E", "W"),
                             protected=[("S", "E")]), cfg)
    assert res.yield_to[_fid(res, "S->E")] != []


def _fid(res, key):
    return next(m.feature_id for m in res.junction.movements if m.key == key)


# ======================================================= Epic 1 guard rails

def test_non_intersection_centerlines_are_flagged_not_used():
    features = make_junction([("S", "N"), ("E", "W")])
    for feat in features:
        if feat["attributes"].get("way_id") == "WEW":
            feat["attributes"]["lane_morphology"] = "road"
    junction = build_junction(features)
    assert [m.key for m in junction.movements] == ["S->N"]
    assert any("NOT at an intersection" in w for w in junction.warnings)


def test_missing_regulatory_element_is_reported():
    features = make_junction([("S", "N"), ("E", "W")], {"S": PRIO})
    features = [f for f in features
                if f["attributes"].get("lane_type") != "regulatory_element"]
    junction = build_junction(features)
    assert any("no regulatory_element with that re_id" in w
               for w in junction.warnings)


def test_turn_classification():
    junction = build_junction(make_junction(ALL_12))
    turns = {m.key: m.turn.value for m in junction.movements}
    assert turns["S->N"] == "straight"
    assert turns["S->E"] == "right"
    assert turns["S->W"] == "left"
    assert turns["E->N"] == "right"
    assert turns["E->S"] == "left"


def test_arm_naming_on_a_skewed_junction():
    bearings = {"N": 20.0, "E": 105.0, "S": 200.0, "W": 285.0}
    features = make_junction([("S", "N"), ("E", "W"), ("E", "N")],
                             bearings=bearings)
    res = _run(features)
    assert yield_keys(res, "S->N") == {"E->W", "E->N"}


# ============================================================ output format

def test_yield_to_cell_format():
    from yield_pipeline import build_yield_to_map
    res = _run(make_junction([("S", "N"), ("E", "W"), ("E", "N")]))
    cells = build_yield_to_map(res)
    cell = cells[_fid(res, "S->N")]
    #  road_id '' _ way_id , quoted, comma separated, de-duplicated, sorted
    assert cell == '"RE\'\'_WEN","RE\'\'_WEW"', repr(cell)
    # a centerline that yields to nobody gets an empty cell, not a null
    assert cells[_fid(res, "E->W")] == ""


def test_yield_to_cell_is_deduplicated():
    """The same target is never written twice, even via two conflicts."""
    from yield_pipeline import build_yield_to_map
    res = _run(make_junction(ALL_12, PLATE_10))
    for cell in build_yield_to_map(res).values():
        items = [i for i in cell.split(",") if i]
        assert len(items) == len(set(items)), cell


# =================================================================== runner

if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {name}\n      {exc}")
        except Exception as exc:                      # noqa: BLE001
            failures += 1
            print(f"ERROR {name}\n      {type(exc).__name__}: {exc}")
    print(f"\n{failures} failure(s)")
    sys.exit(1 if failures else 0)
