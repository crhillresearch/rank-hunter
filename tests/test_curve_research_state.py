import inspect
import json

import pytest

from sage.all import EllipticCurve, QQ

from rank42 import curve_research_state
from rank42.curve_research_state import (
    curve_rank_summary_map,
    curve_research_state_map,
    get_curve_research_state,
    list_curve_research_states,
)
from rank42.db import connect, upsert_curve, update_curve
from rank42.points import rigorous_witness_basis, upsert_point
from rank42.rank_evidence import record_rank_evidence


MODEL = ["0", "0", "0", "-1", "0"]


def _curve(db, parameter, *, family="test", score=0.0, lower=None, upper=None, exact=None):
    curve_id = upsert_curve(db, family=family, parameter=str(parameter), score=score)
    update_curve(
        db,
        curve_id,
        a_invariants_json=json.dumps(MODEL),
        generic_lower=lower,
        descent_upper=upper,
        exact_rank=exact,
    )
    return curve_id


def _evidence(db, curve_id, **overrides):
    data = {
        "engine": "test",
        "evidence_type": "rank_bounds",
        "status": "completed",
        "rigorous": True,
        "rigorous_lower": None,
        "rigorous_upper": None,
        "exact_rank": None,
        "conditional_analytic_upper": None,
        "numerical_rank_signal": None,
        "assumptions": [],
        "points_found": [],
        "options": {},
    }
    data.update(overrides)
    return record_rank_evidence(
        db,
        curve_id=curve_id,
        model=MODEL,
        data=data,
    )


def test_single_state_uses_evidence_over_stale_compatibility_columns(tmp_path):
    db = connect(tmp_path / "x.db")
    curve_id = _curve(db, "1", lower=2)
    evidence_id = _evidence(db, curve_id, rigorous_lower=5, rigorous_upper=7)

    state = get_curve_research_state(db, curve_id)

    assert state["rigorous_lower"] == 5
    assert state["rigorous_upper"] == 7
    assert state["exact_rank"] is None
    assert state["curve"]["generic_lower"] == 2
    assert state["rank_evidence_count"] == 1
    assert state["rigorous_evidence_count"] == 1
    assert state["completed_rigorous_evidence_count"] == 1
    assert state["latest_rank_evidence_id"] == evidence_id


def test_bulk_state_filter_and_order_follow_reduced_evidence(tmp_path):
    db = connect(tmp_path / "x.db")
    low = _curve(db, "low", score=100.0, lower=2)
    high = _curve(db, "high", score=1.0, lower=1)
    other = _curve(db, "other", family="other-family", score=500.0, lower=9)

    _evidence(db, high, rigorous_lower=6)
    _evidence(db, other, rigorous_lower=10)

    states = list_curve_research_states(db, family="test", minimum_lower=2)

    assert [state["curve_id"] for state in states] == [high, low]
    assert [state["rigorous_lower"] for state in states] == [6, 2]


def test_bulk_state_preserves_timeout_as_inconclusive(tmp_path):
    db = connect(tmp_path / "x.db")
    curve_id = _curve(db, "1", lower=4)
    _evidence(
        db,
        curve_id,
        status="timeout",
        timed_out=True,
        rigorous_upper=5,
    )

    state = get_curve_research_state(db, curve_id)

    assert state["rigorous_lower"] == 4
    assert state["rigorous_upper"] is None
    assert state["exact_rank"] is None
    assert state["rank_evidence_count"] == 1
    assert state["completed_rigorous_evidence_count"] == 0


def test_bulk_state_exposes_conflicting_rigorous_bounds(tmp_path):
    db = connect(tmp_path / "x.db")
    curve_id = _curve(db, "1", lower=5)
    _evidence(db, curve_id, rigorous_upper=4)

    state = get_curve_research_state(db, curve_id)

    assert state["rigorous_lower"] == 5
    assert state["rigorous_upper"] == 4
    assert state["exact_rank"] is None
    assert state["rank_inconsistent"] is True


def test_sql_rank_summary_matches_full_reducer_semantics(tmp_path):
    db = connect(tmp_path / "summary.db")
    try:
        cases = []
        cases.append(_curve(db, "stored-lower", lower=4))
        cases.append(_curve(db, "stored-exact", lower=3, exact=3))
        interval = _curve(db, "interval", lower=2, upper=8)
        _evidence(db, interval, rigorous_lower=6, rigorous_upper=7)
        cases.append(interval)
        timeout = _curve(db, "timeout", lower=5)
        _evidence(
            db,
            timeout,
            status="timeout",
            timed_out=True,
            rigorous_upper=5,
        )
        cases.append(timeout)
        conflict = _curve(db, "conflict", lower=7)
        _evidence(db, conflict, rigorous_upper=6)
        cases.append(conflict)
        closed = _curve(db, "closed", lower=1)
        _evidence(db, closed, rigorous_lower=9, rigorous_upper=9)
        cases.append(closed)

        summary = curve_rank_summary_map(db, cases)
        full = curve_research_state_map(db, curve_ids=cases)

        for curve_id in cases:
            assert summary[curve_id] == {
                "rigorous_lower": full[curve_id]["rigorous_lower"],
                "rigorous_upper": full[curve_id]["rigorous_upper"],
                "exact_rank": full[curve_id]["exact_rank"],
                "rank_inconsistent": full[curve_id]["rank_inconsistent"],
            }
    finally:
        db.close()


def test_compact_bulk_state_matches_full_rank_semantics(tmp_path):
    db = connect(tmp_path / "compact.db")
    try:
        first = _curve(db, "first", score=3.0, lower=2)
        second = _curve(db, "second", score=1.0, lower=1)
        _evidence(
            db,
            first,
            rigorous_lower=5,
            rigorous_upper=7,
            conditional_analytic_upper=9,
            numerical_rank_signal=6,
        )
        _evidence(db, second, rigorous_lower=4)

        full = curve_research_state_map(
            db,
            curve_ids=[first, second],
        )
        compact = curve_research_state_map(
            db,
            curve_ids=[first, second],
            compact=True,
        )

        semantic_keys = {
            "curve_id",
            "family",
            "parameter",
            "score",
            "status",
            "rigorous_lower",
            "specialization_rigorous_lower",
            "rigorous_upper",
            "exact_rank",
            "conditional_analytic_upper",
            "conditional_mw_upper",
            "numerical_rank_signal",
            "rank_inconsistent",
            "rank_evidence_count",
            "rigorous_evidence_count",
            "completed_rigorous_evidence_count",
            "latest_rank_evidence_id",
        }
        for curve_id in (first, second):
            assert {
                key: compact[curve_id][key]
                for key in semantic_keys
            } == {
                key: full[curve_id][key]
                for key in semantic_keys
            }

        curve_rows = inspect.getsource(curve_research_state._curve_rows)
        evidence_rows = inspect.getsource(curve_research_state._evidence_by_curve)
        assert '"id,family,parameter,score,status,"' in curve_rows
        assert '"exact_rank,descent_lower,generic_lower,descent_upper"' in curve_rows
        assert '"id,curve_id,rigorous,rigorous_lower,rigorous_upper,"' in evidence_rows
        assert "stdout_summary" not in evidence_rows
        assert "options_json" not in evidence_rows
    finally:
        db.close()


def test_state_map_and_missing_curve_contract(tmp_path):
    db = connect(tmp_path / "x.db")
    first = _curve(db, "1", lower=2)
    second = _curve(db, "2", lower=3)

    state_map = curve_research_state_map(db, curve_ids=[first, second])

    assert set(state_map) == {first, second}
    assert state_map[first]["rigorous_lower"] == 2
    assert state_map[second]["rigorous_lower"] == 3

    with pytest.raises(ValueError, match=r"curve #999 not found"):
        get_curve_research_state(db, 999)

def test_rigorous_witness_basis_uses_authoritative_reduced_lower(tmp_path):
    db = connect(tmp_path / "basis.db")
    model = MODEL
    curve_id = upsert_curve(
        db,
        family="basis",
        parameter="1",
        a_invariants_json=json.dumps(model),
        generic_lower=0,
    )
    _evidence(db, curve_id, rigorous_lower=1)
    upsert_point(
        db,
        curve_id=curve_id,
        x="0",
        y="0",
        source="test",
        role="rigorous_witness",
        exact_verified=True,
        independence_status="rigorous_independent",
        rigorous_independent=True,
    )
    E = EllipticCurve(QQ, [QQ(value) for value in model])

    basis, required, complete = rigorous_witness_basis(db, curve_id, E)

    assert required == 1
    assert complete is True
    assert len(basis) == 1


def test_specialization_lower_excludes_generic_only_baseline(tmp_path):
    db = connect(tmp_path / "x.db")
    generic_only = _curve(db, "generic", lower=10)
    update_curve(db, generic_only, generic_lower=10, descent_lower=None)
    evidence_curve = _curve(db, "evidence", lower=None)
    _evidence(db, evidence_curve, rigorous_lower=7)

    generic_state = get_curve_research_state(db, generic_only)
    evidence_state = get_curve_research_state(db, evidence_curve)

    assert generic_state["rigorous_lower"] == 10
    assert generic_state["specialization_rigorous_lower"] == 0
    assert evidence_state["specialization_rigorous_lower"] == 7

def test_specialization_lower_includes_exact_closed_from_generic_lower_and_upper(tmp_path):
    db = connect(tmp_path / "x.db")
    curve_id = _curve(db, "closed", lower=8)
    _evidence(db, curve_id, rigorous_upper=8)

    state = get_curve_research_state(db, curve_id)

    assert state["rigorous_lower"] == 8
    assert state["exact_rank"] == 8
    assert state["specialization_rigorous_lower"] == 8

