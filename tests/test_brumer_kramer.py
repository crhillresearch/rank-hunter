import json
import subprocess

import pytest
from sage.all import EllipticCurve, QQ

from rank42 import brumer_kramer
from rank42 import pipeline_runner as runner
from rank42.brumer_kramer import (
    BrumerKramerTimeout,
    run_brumer_kramer_bound,
)
from rank42.curve_research_state import get_curve_research_state
from rank42.db import connect, get_curve, upsert_curve
from rank42.pipeline_catalog import normalize_pipeline, stage_spec, validate_pipeline
from rank42.rank_evidence import record_rank_evidence


def _curve(db, *, lower=None, parameter="1"):
    model = ["0", "-1", "1", "-10", "-20"]
    curve_id = upsert_curve(
        db,
        family="brumer-kramer-test",
        parameter=str(parameter),
        a_invariants_json=json.dumps(model),
        generic_lower=lower,
    )
    return curve_id, EllipticCurve(QQ, [QQ(x) for x in model])


def _completed_attempt(E, *, upper, proof_mode):
    proof = proof_mode == "unconditional"
    return {
        "status": "completed",
        "engine": "rank42.brumer_kramer",
        "engine_version": "1",
        "sage_version": "test-sage",
        "theorem": "Brumer-Kramer Proposition 7.1",
        "theorem_scope": "E(Q)[2]=0",
        "class_group_proof": proof,
        "assumptions": (
            []
            if proof
            else ["Generalized Riemann Hypothesis for class-group certification"]
        ),
        "minimal_model_a_invariants": [
            str(x) for x in E.global_minimal_model().a_invariants()
        ],
        "curve_discriminant": str(E.global_minimal_model().discriminant()),
        "two_torsion_rank": 0,
        "two_division_polynomial": str(
            E.global_minimal_model().two_division_polynomial().monic()
        ),
        "cubic_field_polynomial": "x^3 - x + 1",
        "cubic_field_discriminant": "-23",
        "class_group_order": "1",
        "class_group_invariants": [],
        "class_group_2_rank": 0,
        "u_term": 1,
        "phi_m": [],
        "phi_a": [],
        "additive_splitting": {},
        "local_records": [],
        "n_term": int(upper) - 1,
        "selmer_2_dimension_upper": int(upper),
        "mordell_weil_rank_upper": int(upper),
        "started_at": "2026-09-26T00:00:00+00:00",
        "finished_at": "2026-09-26T00:00:01+00:00",
        "_runtime_seconds": 1.0,
        "proof_mode": proof_mode,
    }


def test_real_brumer_kramer_small_curve_grh_and_unconditional():
    E = EllipticCurve(QQ, [0, -1, 1, -10, -20])
    assert E.two_torsion_rank() == 0

    grh = run_brumer_kramer_bound(
        E,
        timeout=60,
        proof_mode="grh",
    )
    assert grh["status"] == "completed"
    assert grh["class_group_proof"] is False
    assert grh["assumptions"] == [
        "Generalized Riemann Hypothesis for class-group certification"
    ]
    assert grh["two_torsion_rank"] == 0
    assert {record["prime"] for record in grh["local_records"]} == {11}
    assert grh["selmer_2_dimension_upper"] == (
        grh["class_group_2_rank"] + grh["u_term"] + grh["n_term"]
    )
    assert grh["mordell_weil_rank_upper"] == grh["selmer_2_dimension_upper"]

    rigorous = run_brumer_kramer_bound(
        E,
        timeout=60,
        proof_mode="unconditional",
    )
    assert rigorous["status"] == "completed"
    assert rigorous["class_group_proof"] is True
    assert rigorous["assumptions"] == []
    assert rigorous["class_group_2_rank"] == grh["class_group_2_rank"]
    assert rigorous["mordell_weil_rank_upper"] == (
        rigorous["class_group_2_rank"]
        + rigorous["u_term"]
        + rigorous["n_term"]
    )


def test_real_brumer_kramer_declines_rational_two_torsion():
    E = EllipticCurve(QQ, [0, 0, 0, -1, 0])
    assert E.two_torsion_rank() > 0

    result = run_brumer_kramer_bound(
        E,
        timeout=30,
        proof_mode="grh",
    )
    assert result["status"] == "unsupported"
    assert result["reason"] == "brumer_kramer_requires_E_Q_2_zero"


def test_brumer_kramer_runner_enforces_hard_timeout(monkeypatch):
    E = EllipticCurve(QQ, [0, -1, 1, -10, -20])

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout", 1))

    monkeypatch.setattr(brumer_kramer.subprocess, "run", timeout)
    with pytest.raises(BrumerKramerTimeout):
        run_brumer_kramer_bound(E, timeout=1, proof_mode="grh")


def test_catalog_declares_brumer_kramer_contract_and_validates_config():
    spec = stage_spec("brumer_kramer_classgroup_bound")
    assert spec.label == "Cubic Class-Group / 2-Selmer Bound"
    assert spec.category == "Arithmetic"
    assert spec.evidence == "conditional"
    assert "conditional_mw_upper" in spec.provides
    assert "upper_bound_possible" in spec.provides
    assert spec.defaults == {
        "proof_mode": "grh",
        "timeout": 300,
    }
    assert "E(Q)[2]=0" in spec.description
    assert "unconditional" in spec.description

    assert validate_pipeline(
        "curve",
        normalize_pipeline(["brumer_kramer_classgroup_bound"]),
    ) == []

    bad = normalize_pipeline([{
        "id": "brumer_kramer_classgroup_bound",
        "config": {"proof_mode": "maybe", "timeout": 0},
    }])
    errors = validate_pipeline("curve", bad)
    assert any("proof mode" in error for error in errors)
    assert any("hard timeout" in error for error in errors)


def test_grh_pipeline_bound_stays_conditional_even_when_it_matches_lower(
    tmp_path,
    monkeypatch,
):
    db = connect(tmp_path / "grh.db")
    curve_id, E = _curve(db, lower=2)
    monkeypatch.setattr(
        runner,
        "run_brumer_kramer_bound",
        lambda *args, **kwargs: _completed_attempt(
            E, upper=2, proof_mode="grh"
        ),
    )

    result, terminal, stop = runner._stage_result(
        "brumer_kramer_classgroup_bound",
        db=db,
        run={"id": 1, "target_mode": "curve"},
        run_config={"target_rank": 10},
        target={},
        context={"curve_id": curve_id, "E": E, "parameter": "1"},
        config={"proof_mode": "grh", "timeout": 300},
        stage_index=1,
    )

    state = get_curve_research_state(db, curve_id)
    assert result["status"] == "completed"
    assert result["result_class"] == "grh_conditional_mordell_weil_upper"
    assert result["conditional_mw_upper"] == 2
    assert result["rigorous_upper"] is None
    assert result["exact_rank"] is None
    assert state["conditional_mw_upper"] == 2
    assert state["rigorous_upper"] is None
    assert state["exact_rank"] is None
    assert get_curve(db, curve_id)["exact_rank"] is None
    assert terminal is None
    assert stop is False


def test_unconditional_pipeline_bound_closes_only_via_rigorous_reducer(
    tmp_path,
    monkeypatch,
):
    db = connect(tmp_path / "unconditional.db")
    curve_id, E = _curve(db, lower=2)
    monkeypatch.setattr(
        runner,
        "run_brumer_kramer_bound",
        lambda *args, **kwargs: _completed_attempt(
            E, upper=2, proof_mode="unconditional"
        ),
    )

    result, terminal, stop = runner._stage_result(
        "brumer_kramer_classgroup_bound",
        db=db,
        run={"id": 1, "target_mode": "curve"},
        run_config={"target_rank": 10},
        target={},
        context={"curve_id": curve_id, "E": E, "parameter": "1"},
        config={"proof_mode": "unconditional", "timeout": 300},
        stage_index=1,
    )

    state = get_curve_research_state(db, curve_id)
    assert result["status"] == "completed"
    assert result["result_class"] == "rigorous_mordell_weil_upper"
    assert result["conditional_mw_upper"] is None
    assert result["rigorous_upper"] == 2
    assert result["exact_rank"] == 2
    assert state["rigorous_upper"] == 2
    assert state["exact_rank"] == 2
    assert int(get_curve(db, curve_id)["exact_rank"]) == 2
    assert terminal is None
    assert stop is False


def test_conditional_mw_upper_is_distinct_from_conditional_analytic_upper(
    tmp_path,
):
    db = connect(tmp_path / "separate.db")
    curve_id, E = _curve(db, lower=1)
    model = [str(x) for x in E.a_invariants()]

    record_rank_evidence(
        db,
        curve_id=curve_id,
        model=model,
        data={
            "engine": "analytic",
            "evidence_type": "conditional_analytic_upper",
            "status": "completed",
            "rigorous": False,
            "conditional_analytic_upper": 4,
            "conditional_mw_upper": None,
            "assumptions": ["GRH for L(E,s)"],
            "points_found": [],
            "options": {},
        },
    )
    record_rank_evidence(
        db,
        curve_id=curve_id,
        model=model,
        data={
            "engine": "brumer-kramer",
            "evidence_type": "conditional_mw_upper",
            "status": "completed",
            "rigorous": False,
            "conditional_analytic_upper": None,
            "conditional_mw_upper": 3,
            "assumptions": ["GRH for class-group certification"],
            "points_found": [],
            "options": {},
        },
    )

    state = get_curve_research_state(db, curve_id)
    assert state["conditional_analytic_upper"] == 4
    assert state["conditional_mw_upper"] == 3
    assert state["rigorous_upper"] is None
    assert state["exact_rank"] is None


def test_unconditional_request_cannot_promote_without_worker_proof_attestation(
    tmp_path,
    monkeypatch,
):
    db = connect(tmp_path / "proof-mismatch.db")
    curve_id, E = _curve(db, lower=2, parameter="proof-mismatch")
    monkeypatch.setattr(
        runner,
        "run_brumer_kramer_bound",
        lambda *args, **kwargs: _completed_attempt(
            E, upper=2, proof_mode="grh"
        ),
    )

    result, terminal, stop = runner._stage_result(
        "brumer_kramer_classgroup_bound",
        db=db,
        run={"id": 1, "target_mode": "curve"},
        run_config={"target_rank": 10},
        target={},
        context={
            "curve_id": curve_id,
            "E": E,
            "parameter": "proof-mismatch",
        },
        config={"proof_mode": "unconditional", "timeout": 300},
        stage_index=1,
    )

    state = get_curve_research_state(db, curve_id)
    assert result["status"] == "completed"
    assert result["proof_mode_mismatch"] is True
    assert result["worker_class_group_proof"] is False
    assert result["result_class"] == "grh_conditional_mordell_weil_upper"
    assert state["conditional_mw_upper"] == 2
    assert state["rigorous_upper"] is None
    assert state["exact_rank"] is None
    assert get_curve(db, curve_id)["exact_rank"] is None
    assert terminal is None
    assert stop is False
