import inspect
import json

from rank42 import hard_case_escalator
from rank42.analysis_method_history import (
    basis_fingerprint_from_curve_row,
    budget_dominates,
    method_budget_state,
    method_family_state,
    upper_bound_method_history,
    upper_bound_method_history_map,
)
from rank42.analysis_planner import analysis_plan
from rank42.analyze_workspace import curve_analysis_snapshot
from rank42.db import connect, get_curve, update_curve, upsert_curve
from rank42.hard_case_escalator import _record_stage_attempt


MODEL = ["0", "0", "0", "-1", "1"]


def _curve(db, parameter):
    curve_id = upsert_curve(
        db,
        family="analysis-history",
        parameter=str(parameter),
        a_invariants_json=json.dumps(MODEL),
    )
    update_curve(
        db,
        curve_id,
        descent_lower=1,
        generators_json='[["0","1"]]',
    )
    return curve_id


def _stage(db, curve_id, method, budget, *, status="timeout"):
    row = get_curve(db, curve_id)
    fingerprint = basis_fingerprint_from_curve_row(row)
    return _record_stage_attempt(
        db,
        curve_id=curve_id,
        model=MODEL,
        method=method,
        basis_fingerprint=fingerprint,
        budget=budget,
        status=status,
    )


def test_budget_dominance_matches_hard_case_semantics():
    prior = {"timeout": 600, "max_halvings": 250}
    assert budget_dominates(prior, {"timeout": 300, "max_halvings": 250})
    assert not budget_dominates(prior, {"timeout": 900, "max_halvings": 250})
    assert not budget_dominates(prior, {"timeout": 300, "max_halvings": 300})


def test_method_history_marks_equal_or_stronger_attempt_exhausted(tmp_path):
    db = connect(tmp_path / "history.db")
    try:
        curve_id = _curve(db, "equal")
        evidence_id = _stage(
            db,
            curve_id,
            "sage_proof_rank",
            {"timeout": 300},
        )
        fingerprint = basis_fingerprint_from_curve_row(get_curve(db, curve_id))

        state = method_budget_state(
            db,
            curve_id=curve_id,
            method="sage_proof_rank",
            basis_fingerprint=fingerprint,
            budget={"timeout": 180},
            point_scoped=False,
        )

        assert state["attempted"] is True
        assert state["exhausted_at_budget"] is True
        assert state["retry_available"] is False
        assert state["dominating_evidence_id"] == evidence_id
        assert state["stronger_budget_available"] is False
    finally:
        db.close()


def test_method_history_keeps_stronger_budget_available(tmp_path):
    db = connect(tmp_path / "history.db")
    try:
        curve_id = _curve(db, "stronger")
        _stage(
            db,
            curve_id,
            "descent:mwrank_selmer",
            {"timeout": 90},
        )
        fingerprint = basis_fingerprint_from_curve_row(get_curve(db, curve_id))

        state = method_budget_state(
            db,
            curve_id=curve_id,
            method="descent:mwrank_selmer",
            basis_fingerprint=fingerprint,
            budget={"timeout": 300},
            point_scoped=False,
        )

        assert state["attempted"] is True
        assert state["exhausted_at_budget"] is False
        assert state["retry_available"] is True
        assert state["stronger_budget_available"] is True
    finally:
        db.close()


def test_method_history_is_scoped_to_active_basis(tmp_path):
    db = connect(tmp_path / "history.db")
    try:
        curve_id = _curve(db, "basis")
        _stage(db, curve_id, "sage_proof_rank", {"timeout": 300})
        row = get_curve(db, curve_id)
        old_fp = basis_fingerprint_from_curve_row(row)
        update_curve(
            db,
            curve_id,
            descent_lower=1,
            generators_json='[["2","3"]]',
        )
        new_fp = basis_fingerprint_from_curve_row(get_curve(db, curve_id))
        assert new_fp != old_fp

        state = method_budget_state(
            db,
            curve_id=curve_id,
            method="sage_proof_rank",
            basis_fingerprint=new_fp,
            budget={"timeout": 180},
            point_scoped=False,
        )
        assert state["attempted"] is False
        assert state["exhausted_at_budget"] is False
    finally:
        db.close()


def test_upper_bound_family_only_exhausts_when_every_method_is_blocked(tmp_path):
    db = connect(tmp_path / "history.db")
    try:
        curve_id = _curve(db, "family")
        for method, budget in (
            ("descent:mwrank_selmer", {"timeout": 300}),
            ("descent:simon_known", {"timeout": 300}),
            ("descent:mwrank_coverings", {"timeout": 300}),
            ("sage_proof_rank", {"timeout": 180}),
        ):
            _stage(db, curve_id, method, budget)

        history = upper_bound_method_history(
            db,
            curve_id=curve_id,
            curve_row=get_curve(db, curve_id),
        )
        assert history["attempted_methods"] == 4
        assert history["method_count"] == 4
        assert history["all_exhausted_at_budget"] is True
        assert history["available_methods"] == []
        assert method_family_state(history["methods"])["all_exhausted_at_budget"] is True
    finally:
        db.close()


def test_bulk_upper_bound_history_matches_per_curve_semantics(tmp_path):
    db = connect(tmp_path / "history-bulk.db")
    try:
        exhausted_curve = _curve(db, "bulk-exhausted")
        retry_curve = _curve(db, "bulk-retry")
        for method, budget in (
            ("descent:mwrank_selmer", {"timeout": 300}),
            ("descent:simon_known", {"timeout": 300}),
            ("descent:mwrank_coverings", {"timeout": 300}),
            ("sage_proof_rank", {"timeout": 180}),
        ):
            _stage(db, exhausted_curve, method, budget)
        _stage(
            db,
            retry_curve,
            "descent:mwrank_selmer",
            {"timeout": 90},
        )

        history = upper_bound_method_history_map(
            db,
            [
                get_curve(db, exhausted_curve),
                get_curve(db, retry_curve),
            ],
        )

        assert history[exhausted_curve]["all_exhausted_at_budget"] is True
        assert history[exhausted_curve]["available_methods"] == []
        assert history[retry_curve]["all_exhausted_at_budget"] is False
        assert history[retry_curve]["stronger_budget_available"] is True
        assert history[retry_curve]["attempted_methods"] == 1
    finally:
        db.close()


def test_analyze_snapshot_and_planner_expose_exhausted_upper_bound_history(tmp_path):
    db = connect(tmp_path / "analysis.db")
    try:
        curve_id = _curve(db, "planner-exhausted")
        for method, budget in (
            ("descent:mwrank_selmer", {"timeout": 300}),
            ("descent:simon_known", {"timeout": 300}),
            ("descent:mwrank_coverings", {"timeout": 300}),
            ("sage_proof_rank", {"timeout": 180}),
        ):
            _stage(db, curve_id, method, budget)

        snapshot = curve_analysis_snapshot(db, curve_id)
        history = snapshot["method_history"]["upper_bound"]
        action = next(
            rec for rec in analysis_plan(snapshot)["actions"]
            if rec["kind"] == "descent"
            and rec["title"] == "Try a rigorous upper bound"
        )

        assert history["all_exhausted_at_budget"] is True
        assert action["exhausted"] is True
        assert action["prior_attempt_state"]["method_history"] == history
        assert "Equal-or-stronger attempts already exist" in action["why"]
        assert "deliberately raise a method budget" in action["recommended_method"]
    finally:
        db.close()


def test_planner_reports_stronger_retry_when_prior_budget_was_weaker(tmp_path):
    db = connect(tmp_path / "analysis.db")
    try:
        curve_id = _curve(db, "planner-stronger")
        _stage(
            db,
            curve_id,
            "descent:mwrank_selmer",
            {"timeout": 90},
        )

        snapshot = curve_analysis_snapshot(db, curve_id)
        action = next(
            rec for rec in analysis_plan(snapshot)["actions"]
            if rec["kind"] == "descent"
            and rec["title"] == "Try a rigorous upper bound"
        )

        history = action["prior_attempt_state"]["method_history"]
        assert history["all_exhausted_at_budget"] is False
        assert history["stronger_budget_available"] is True
        assert action["exhausted"] is False
        assert "materially stronger" in action["why"]
    finally:
        db.close()


def test_hard_case_escalator_delegates_reusable_history_rules():
    source = inspect.getsource(hard_case_escalator)

    assert "from rank42.analysis_method_history import (" in source
    assert "def _budget_dominates(" not in source
    assert "def _prior_stage_attempt(" not in source
    assert "def _basis_fingerprint_from_row(" not in source
    assert "def _latest_basis_fingerprint_before(" not in source

    # Execution-specific compatibility and cost-barrier rules remain local.
    assert "def _legacy_independence_exhaustion(" in source
    assert "def _saturation_family_blocker(" in source
