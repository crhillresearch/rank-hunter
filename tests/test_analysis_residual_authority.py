import json

import rank42.attack as attack
from rank42.db import connect, get_curve, update_curve, upsert_curve
from rank42.independence_check import _authoritative_rank_baseline
from rank42.rank_evidence import record_rank_evidence


MODEL = ["0", "0", "0", "-1", "1"]


def _curve(db, parameter):
    curve_id = upsert_curve(
        db,
        family="analysis-residual-authority",
        parameter=str(parameter),
        a_invariants_json=json.dumps(MODEL),
    )
    update_curve(
        db,
        curve_id,
        generic_lower=1,
        descent_lower=1,
    )
    return curve_id


def _structured_lower(db, curve_id, lower, *, key):
    return record_rank_evidence(
        db,
        curve_id=curve_id,
        model=MODEL,
        data={
            "engine": "analysis-residual-test",
            "evidence_type": "rank_bounds",
            "status": "completed",
            "rigorous": True,
            "rigorous_lower": int(lower),
            "rigorous_upper": None,
            "exact_rank": None,
            "conditional_analytic_upper": None,
            "numerical_rank_signal": None,
            "assumptions": [],
            "points_found": [],
            "options": {},
        },
        key=key,
    )


def test_manual_attack_baseline_reads_structured_rank_evidence(tmp_path):
    db = connect(tmp_path / "attack-authority.db")
    try:
        curve_id = _curve(db, "attack")
        _structured_lower(db, curve_id, 5, key="attack-lower-5")

        curve = get_curve(db, curve_id)
        assert int(curve["descent_lower"]) == 1

        state = attack._authoritative_curve_rank_state(db, curve_id)

        assert state["rigorous_lower"] == 5
        assert state["exact_rank"] is None
        assert state["inconsistent"] is False
    finally:
        db.close()


def test_independence_baseline_reads_structured_rank_evidence(tmp_path):
    db = connect(tmp_path / "independence-authority.db")
    try:
        curve_id = _curve(db, "independence")
        _structured_lower(db, curve_id, 6, key="independence-lower-6")

        curve = get_curve(db, curve_id)
        assert int(curve["descent_lower"]) == 1

        state = _authoritative_rank_baseline(db, curve_id)

        assert state["rigorous_lower"] == 6
        assert state["exact_rank"] is None
        assert state["inconsistent"] is False
    finally:
        db.close()


def test_analysis_workers_no_longer_use_legacy_lower_for_decisions():
    import inspect
    import rank42.independence_check as independence_check

    attack_source = inspect.getsource(attack)
    independence_source = inspect.getsource(independence_check)

    assert 'max(int(row["generic_lower"]' not in attack_source
    assert "proven_lower(row)" not in independence_source
    assert "reduce_rank_state" in attack_source
    assert "reduce_rank_state" in independence_source
