"""
MP-468 - wait lines: re_id attached, centerline linked, yield_to aligned.

Run with pytest, or directly:  python tests/test_wait_lines.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yield_pipeline import (DEFAULT_CONFIG, ReIdAllocator, build_junction,  # noqa: E402
                            build_yield_to_map, evaluate, is_wait_line,
                            plan_wait_lines)
from yield_pipeline import geometry as geo                                 # noqa: E402
from tests.fixtures import make_junction, movement_geometry                # noqa: E402

RBL = [("S", "N"), ("E", "W"), ("E", "N")]


def _wait_line(re_id, arm, to_arm, at=1.0, code="(de341)", fid=None):
    """A wait line drawn across the entry of the arm->to_arm lane."""
    p1, p2 = geo.perpendicular_at(movement_geometry(arm, to_arm), at, 1.75)
    return {"attributes": {"fid": fid or f"wl_{re_id}",
                           "lane_type": "regulatory_element",
                           "line_type": "traffic_sign",
                           "line_sub": code, "re_id": re_id, "yield_to": None},
            "geometry": [p1, p2]}


def _fids(features):
    for i, f in enumerate(features):
        f["attributes"].setdefault("fid", f"f{i}")
    return features


def _plan(features, extra=(), existing_ids=("re_975",)):
    features = _fids(features)
    res = evaluate(build_junction(features + list(extra)), DEFAULT_CONFIG)
    regs = [f for f in features + list(extra)
            if f["attributes"].get("lane_type") == "regulatory_element"]
    alloc = ReIdAllocator(list(existing_ids)
                          + [f["attributes"].get("re_id") for f in regs])
    return res, plan_wait_lines(res, regs, alloc)


def _mv(res, key):
    return next(m for m in res.junction.movements if m.key == key)


def _row(plan, key):
    return next(r for r in plan.rows if r.movement.key == key)


# ----------------------------------------------------------------- create ---

def test_new_wait_line_per_yielding_lane_only():
    res, plan = _plan(make_junction(RBL))
    yielding = {m.key for m in res.junction.movements
                if res.yield_to.get(m.feature_id)}
    assert {l["attributes"]["movement"] for l in plan.new_lines} == yielding
    assert {r.status for r in plan.rows} == {"new"}


def test_new_re_ids_continue_the_highest_number():
    _, plan = _plan(make_junction(RBL))
    ids = sorted(l["attributes"]["re_id"] for l in plan.new_lines)
    assert ids[0] == "re_976", ids


def test_new_wait_line_yield_to_equals_centerline_yield_to():
    res, plan = _plan(make_junction(RBL))
    cells = build_yield_to_map(res)
    for line in plan.new_lines:
        mv = _mv(res, line["attributes"]["movement"])
        assert line["attributes"]["yield_to"] == cells[mv.feature_id]
        assert line["attributes"]["yield_to"]          # never empty


def test_new_re_id_is_appended_to_traffic_rule():
    res, plan = _plan(make_junction(RBL, {"S": ["de205"]}))
    mv = _mv(res, "S->N")
    rid = _row(plan, "S->N").re_id
    # the existing sign link is kept, the wait line is added after it
    assert plan.traffic_rule[mv.feature_id] == f"RE_S_SIGN,{rid}"


def test_new_wait_line_is_a_traffic_sign_regulatory_element():
    _, plan = _plan(make_junction(RBL))
    a = plan.new_lines[0]["attributes"]
    assert a["lane_type"] == "regulatory_element"
    assert a["line_type"] == "traffic_sign"
    assert a["line_sub"] == "(de341)"
    assert is_wait_line(plan.new_lines[0])


def test_stop_arm_gets_a_stop_line():
    _, plan = _plan(make_junction(RBL, {"S": ["de206"], "E": ["de306"]}))
    line = next(l for l in plan.new_lines if l["attributes"]["movement"] == "S->N")
    assert line["attributes"]["line_sub"] == "(de294)"


def test_new_wait_line_sits_across_its_own_lane_before_the_conflict():
    res, plan = _plan(make_junction(RBL))
    line = next(l for l in plan.new_lines if l["attributes"]["movement"] == "S->N")
    mv = _mv(res, "S->N")
    assert geo.polyline_intersections(line["geometry"], mv.geometry)


# --------------------------------------------------------------- existing ---

def test_linked_wait_line_is_updated_not_duplicated():
    feats = make_junction(RBL)
    s_n = next(f for f in feats if f["attributes"]["way_id"] == "WSN")
    s_n["attributes"]["traffic_rule"] = "re_500"
    res, plan = _plan(feats, [_wait_line("re_500", "S", "N", at=30.0)])
    row = _row(plan, "S->N")
    assert (row.status, row.re_id) == ("linked", "re_500")
    assert _mv(res, "S->N").feature_id not in plan.traffic_rule   # already there
    assert plan.updates["wl_re_500"] == build_yield_to_map(res)[_mv(res, "S->N").feature_id]
    assert all(l["attributes"]["movement"] != "S->N" for l in plan.new_lines)


def test_unlinked_wait_line_at_the_entry_is_matched_and_linked():
    res, plan = _plan(make_junction(RBL), [_wait_line("re_600", "S", "N")])
    row = _row(plan, "S->N")
    assert (row.status, row.re_id) == ("matched", "re_600")
    assert plan.traffic_rule[_mv(res, "S->N").feature_id] == "re_600"


def test_wait_line_on_another_arm_is_not_matched():
    res, plan = _plan(make_junction(RBL), [_wait_line("re_600", "E", "W")])
    assert _row(plan, "S->N").status == "new"


def test_wait_line_too_far_from_the_lane_is_not_matched():
    res, plan = _plan(make_junction(RBL),
                      [_wait_line("re_600", "S", "N", at=25.0)])
    assert _row(plan, "S->N").status == "new"


def test_shared_wait_line_gets_union_and_a_warning():
    """S->N waits, S->E (right turn) does not; one line across the lane."""
    res, plan = _plan(make_junction([("S", "N"), ("S", "E"), ("E", "W")]),
                      [_wait_line("re_700", "S", "N")])
    assert _row(plan, "S->N").re_id == _row(plan, "S->E").re_id == "re_700"
    union = build_yield_to_map(res)[_mv(res, "S->N").feature_id]
    assert plan.updates["wl_re_700"] == union
    assert any("re_700" in w and "union" in w for w in plan.warnings)


def test_existing_wait_line_on_non_yielding_lane_is_cleared():
    res, plan = _plan(make_junction(RBL), [_wait_line("re_800", "E", "W")])
    assert not res.yield_to.get(_mv(res, "E->W").feature_id)
    assert plan.updates["wl_re_800"] == ""


def test_wait_line_without_re_id_gets_one():
    wl = _wait_line("", "S", "N")
    res, plan = _plan(make_junction(RBL), [wl])
    rid = plan.re_id_fixes["wl_"]
    assert _row(plan, "S->N").re_id == rid
    assert plan.traffic_rule[_mv(res, "S->N").feature_id] == rid


# ------------------------------------------------------------- recognition --

def test_sign_post_is_not_a_wait_line():
    post = _wait_line("re_1", "S", "N")
    post["geometry"] = [(0.0, 0.0), (0.1, 0.1)]
    assert not is_wait_line(post)


def test_priority_sign_line_is_not_a_wait_line():
    assert not is_wait_line(_wait_line("re_1", "S", "N", code="(de306)"))


def test_code_spellings():
    for code in ("(de341)", "DE 341", "de_294", "341", "de112,(de341)"):
        assert is_wait_line(_wait_line("re_1", "S", "N", code=code)), code


def test_allocator_skips_used_ids_and_keeps_prefix():
    alloc = ReIdAllocator(["re_9", "re_10", "RE_S_SIGN", None])
    assert [alloc.next(), alloc.next()] == ["re_11", "re_12"]
    assert ReIdAllocator([], "re_").next() == "re_1"


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
