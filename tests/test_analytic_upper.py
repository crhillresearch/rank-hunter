import json
import subprocess

import pytest
from sage.all import EllipticCurve, QQ

from rank42 import analytic_upper
from rank42 import pipeline_runner as runner
from rank42.analytic_upper import AnalyticUpperTimeout, run_conditional_analytic_upper
from rank42.db import connect, upsert_curve
from rank42.pipeline_catalog import normalize_pipeline, stage_spec, validate_pipeline
from rank42.rank_evidence import list_rank_evidence, record_rank_evidence
from rank42.curve_research_state import get_curve_research_state


def _curve(db, *, lower=None):
    model = ["0", "0", "0", "-1", "0"]
    curve_id = upsert_curve(
        db,
        family="analytic-upper-test",
        parameter="1",
        a_invariants_json=json.dumps(model),
        generic_lower=lower,
    )
    return curve_id, EllipticCurve(QQ, [QQ(x) for x in model])


def test_real_sage_mestre_bober_controls():
    rank0 = EllipticCurve(QQ, [0, -1, 1, -10, -20])
    result0 = run_conditional_analytic_upper(
        rank0,
        timeout=30,
        max_delta=1.0,
        adaptive=False,
        ncpus=1,
    )
    assert result0["status"] == "completed"
    assert result0["rigorous"] is False
    assert result0["rigorous_upper"] is None
    assert result0["exact_rank"] is None
    assert int(result0["conditional_analytic_upper"]) >= 0
    assert result0["assumptions"] == [
        "Generalized Riemann Hypothesis for L(E,s)"
    ]

    rank1 = EllipticCurve(QQ, [0, 0, 0, -39, 123])
    result1 = run_conditional_analytic_upper(
        rank1,
        timeout=30,
        max_delta=1.0,
        adaptive=True,
        ncpus=1,
    )
    assert int(result1["conditional_analytic_upper"]) >= 1


def test_analytic_upper_runner_enforces_hard_timeout(monkeypatch):
    E = EllipticCurve(QQ, [0, -1, 1, -10, -20])

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout", 1))

    monkeypatch.setattr(analytic_upper.subprocess, "run", timeout)
    with pytest.raises(AnalyticUpperTimeout):
        run_conditional_analytic_upper(
            E,
            timeout=1,
            max_delta=1.0,
            adaptive=False,
            ncpus=1,
        )


def test_catalog_declares_conditional_evidence_and_validates_budget():
    spec = stage_spec("mestre_bober_analytic_upper")
    assert spec.label == "Conditional Analytic Rank Upper"
    assert spec.category == "Arithmetic"
    assert spec.evidence == "conditional"
    assert spec.provides == frozenset({"conditional_analytic_upper"})
    assert spec.defaults == {
        "max_delta": 1.5,
        "adaptive": True,
        "ncpus": 1,
        "timeout": 60,
    }
    assert "conditional on GRH" in spec.description
    assert "never become a rigorous Mordell-Weil upper" in spec.description

    assert validate_pipeline(
        "curve",
        normalize_pipeline(["mestre_bober_analytic_upper"]),
    ) == []

    bad = normalize_pipeline([{
        "id": "mestre_bober_analytic_upper",
        "config": {
            "max_delta": 3.1,
            "adaptive": "yes",
            "ncpus": 0,
            "timeout": 0,
        },
    }])
    errors = validate_pipeline("curve", bad)
    assert any("max Delta" in error for error in errors)
    assert any("hard timeout" in error for error in errors)
    assert any("CPU count" in error for error in errors)
    assert any("adaptive must be boolean" in error for error in errors)


def test_pipeline_stage_persists_conditional_upper_without_rank_promotion(
    tmp_path,
    monkeypatch,
):
    db = connect(tmp_path / "analytic.db")
    curve_id, E = _curve(db, lower=1)
    record_rank_evidence(
        db,
        curve_id=curve_id,
        model=[str(x) for x in E.a_invariants()],
        data={
            "engine": "test-rigorous",
            "evidence_type": "rank_bounds",
            "status": "completed",
            "rigorous": True,
            "rigorous_lower": None,
            "rigorous_upper": 3,
            "exact_rank": None,
            "conditional_analytic_upper": None,
            "numerical_rank_signal": None,
            "assumptions": [],
            "points_found": [],
            "options": {},
        },
    )

    monkeypatch.setattr(
        runner,
        "run_conditional_analytic_upper",
        lambda *args, **kwargs: {
            "status": "completed",
            "engine": "sage.mestre_bober_zero_sum",
            "engine_version": "test-sage",
            "sage_version": "test-sage",
            "conditional_analytic_upper": 2,
            "minimal_model_a_invariants": [
                str(x) for x in E.global_minimal_model().a_invariants()
            ],
            "started_at": "2026-09-26T00:00:00+00:00",
            "finished_at": "2026-09-26T00:00:01+00:00",
            "_runtime_seconds": 1.0,
        },
    )

    result, terminal, stop = runner._stage_result(
        "mestre_bober_analytic_upper",
        db=db,
        run={"id": 1, "target_mode": "curve"},
        run_config={"target_rank": 10},
        target={},
        context={"curve_id": curve_id, "E": E, "parameter": "1"},
        config={
            "max_delta": 1.5,
            "adaptive": True,
            "ncpus": 1,
            "timeout": 60,
        },
        stage_index=1,
    )

    state = get_curve_research_state(db, curve_id)
    assert result["status"] == "completed"
    assert result["attempt_conditional_analytic_upper"] == 2
    assert result["conditional_analytic_upper"] == 2
    assert result["rigorous_upper"] == 3
    assert result["exact_rank"] is None
    assert state["conditional_analytic_upper"] == 2
    assert state["rigorous_upper"] == 3
    assert state["exact_rank"] is None
    assert terminal is None
    assert stop is False

    rows = list_rank_evidence(db, curve_id, limit=20)
    analytic = next(
        row for row in rows
        if row["evidence_type"] == "conditional_analytic_upper"
    )
    assert int(analytic["rigorous"]) == 0
    assert analytic["rigorous_upper"] is None
    assert int(analytic["conditional_analytic_upper"]) == 2


def test_pipeline_timeout_preserves_prior_conditional_and_rigorous_state(
    tmp_path,
    monkeypatch,
):
    db = connect(tmp_path / "timeout.db")
    curve_id, E = _curve(db, lower=1)
    model = [str(x) for x in E.a_invariants()]
    record_rank_evidence(
        db,
        curve_id=curve_id,
        model=model,
        data={
            "engine": "prior-analytic",
            "evidence_type": "conditional_analytic_upper",
            "status": "completed",
            "rigorous": False,
            "rigorous_lower": None,
            "rigorous_upper": None,
            "exact_rank": None,
            "conditional_analytic_upper": 4,
            "numerical_rank_signal": None,
            "assumptions": ["Generalized Riemann Hypothesis for L(E,s)"],
            "points_found": [],
            "options": {"max_delta": 1.0},
        },
    )
    record_rank_evidence(
        db,
        curve_id=curve_id,
        model=model,
        data={
            "engine": "prior-rigorous",
            "evidence_type": "rank_bounds",
            "status": "completed",
            "rigorous": True,
            "rigorous_lower": None,
            "rigorous_upper": 5,
            "exact_rank": None,
            "conditional_analytic_upper": None,
            "numerical_rank_signal": None,
            "assumptions": [],
            "points_found": [],
            "options": {},
        },
    )

    monkeypatch.setattr(
        runner,
        "run_conditional_analytic_upper",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AnalyticUpperTimeout("injected timeout", runtime=0.25)
        ),
    )

    result, terminal, stop = runner._stage_result(
        "mestre_bober_analytic_upper",
        db=db,
        run={"id": 1, "target_mode": "curve"},
        run_config={"target_rank": 10},
        target={},
        context={"curve_id": curve_id, "E": E, "parameter": "1"},
        config={
            "max_delta": 1.5,
            "adaptive": True,
            "ncpus": 1,
            "timeout": 1,
        },
        stage_index=1,
    )

    state = get_curve_research_state(db, curve_id)
    assert result["status"] == "timeout"
    assert result["attempt_conditional_analytic_upper"] is None
    assert result["conditional_analytic_upper"] == 4
    assert result["rigorous_upper"] == 5
    assert state["conditional_analytic_upper"] == 4
    assert state["rigorous_upper"] == 5
    assert state["exact_rank"] is None
    assert terminal is None
    assert stop is False
