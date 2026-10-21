import json
import subprocess

import pytest
from sage.all import EllipticCurve, QQ

from rank42 import cassels_tate
from rank42 import pipeline_runner as runner
from rank42.cassels_tate import (
    CasselsTateTimeout,
    run_cassels_tate_refinement,
)
from rank42.curve_research_state import get_curve_research_state
from rank42.db import connect, get_curve, upsert_curve
from rank42.pipeline_catalog import normalize_pipeline, stage_spec, validate_pipeline
from rank42.rank_evidence import record_rank_evidence


PAIRING_CURVE = ["0", "-1", "1", "-929", "-10595"]


def _curve(db, *, lower=0, parameter="1"):
    curve_id = upsert_curve(
        db,
        family="cassels-tate-test",
        parameter=str(parameter),
        a_invariants_json=json.dumps(PAIRING_CURVE),
        generic_lower=lower,
    )
    return curve_id, EllipticCurve(QQ, [QQ(x) for x in PAIRING_CURVE])


def _completed_attempt(E):
    return {
        "status": "completed",
        "engine": "pari.ellrank_cassels_pairing",
        "engine_version": "test-pari",
        "sage_version": "test-sage",
        "minimal_model_a_invariants": [
            str(x) for x in E.global_minimal_model().a_invariants()
        ],
        "effort": 0,
        "mordell_weil_rank_lower_diagnostic": 0,
        "mordell_weil_rank_upper": 0,
        "pre_pairing_selmer_upper": 2,
        "cassels_tate_pairing_rank": 2,
        "refinement_amount": 2,
        "pari_points_diagnostic": [],
        "pairing_computed": True,
        "unconditional": True,
        "assumptions": [],
        "started_at": "2026-09-26T00:00:00+00:00",
        "finished_at": "2026-09-26T00:00:01+00:00",
        "_runtime_seconds": 1.0,
    }


def test_real_pari_cassels_tate_refines_571a1():
    E = EllipticCurve(QQ, [QQ(x) for x in PAIRING_CURVE])
    result = run_cassels_tate_refinement(E, timeout=30, effort=0)

    assert result["status"] == "completed"
    assert result["engine"] == "pari.ellrank_cassels_pairing"
    assert result["unconditional"] is True
    assert result["assumptions"] == []
    assert result["pairing_computed"] is True
    assert result["cassels_tate_pairing_rank"] == 2
    assert result["pre_pairing_selmer_upper"] == 2
    assert result["mordell_weil_rank_upper"] == 0
    assert result["refinement_amount"] == 2


def test_cassels_tate_runner_enforces_hard_timeout(monkeypatch):
    E = EllipticCurve(QQ, [QQ(x) for x in PAIRING_CURVE])

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout", 1))

    monkeypatch.setattr(cassels_tate.subprocess, "run", timeout)
    with pytest.raises(CasselsTateTimeout):
        run_cassels_tate_refinement(E, timeout=1, effort=0)


def test_catalog_declares_cassels_tate_contract_and_validates_config():
    spec = stage_spec("cassels_tate_refinement")
    assert spec.label == "Cassels–Tate Selmer Refinement"
    assert spec.category == "Arithmetic"
    assert spec.evidence == "rigorous"
    assert "cassels_tate_refinement" in spec.provides
    assert spec.defaults == {"effort": 0, "timeout": 300}
    assert "raw pre-pairing Selmer ceiling" in spec.description
    assert "unconditionally" in spec.conditional

    assert validate_pipeline(
        "curve",
        normalize_pipeline(["cassels_tate_refinement"]),
    ) == []

    bad = normalize_pipeline([{
        "id": "cassels_tate_refinement",
        "config": {"effort": 11, "timeout": 0},
    }])
    errors = validate_pipeline("curve", bad)
    assert any("effort" in error for error in errors)
    assert any("hard timeout" in error for error in errors)


def test_pipeline_promotes_only_refined_upper_and_closes_via_reducer(
    tmp_path,
    monkeypatch,
):
    db = connect(tmp_path / "refined.db")
    curve_id, E = _curve(db, lower=0)
    monkeypatch.setattr(
        runner,
        "run_cassels_tate_refinement",
        lambda *args, **kwargs: _completed_attempt(E),
    )

    result, terminal, stop = runner._stage_result(
        "cassels_tate_refinement",
        db=db,
        run={"id": 1, "target_mode": "curve"},
        run_config={"target_rank": 10},
        target={},
        context={"curve_id": curve_id, "E": E, "parameter": "1"},
        config={"effort": 0, "timeout": 300},
        stage_index=1,
    )

    state = get_curve_research_state(db, curve_id)
    evidence = db.execute(
        "SELECT * FROM rank_evidence WHERE id=?",
        (result["evidence_id"],),
    ).fetchone()
    assert result["status"] == "completed"
    assert result["pre_pairing_selmer_upper"] == 2
    assert result["cassels_tate_pairing_rank"] == 2
    assert result["rigorous_upper"] == 0
    assert result["exact_rank"] == 0
    assert state["rigorous_upper"] == 0
    assert state["exact_rank"] == 0
    assert int(get_curve(db, curve_id)["exact_rank"]) == 0
    assert evidence["rigorous_upper"] == 0
    assert json.loads(evidence["points_json"]) == []
    assert terminal is None
    assert stop is False


def test_pipeline_rejects_malformed_pairing_attestation(tmp_path, monkeypatch):
    db = connect(tmp_path / "malformed.db")
    curve_id, E = _curve(db, lower=0, parameter="malformed")
    malformed = _completed_attempt(E)
    malformed["pairing_computed"] = False
    monkeypatch.setattr(
        runner,
        "run_cassels_tate_refinement",
        lambda *args, **kwargs: malformed,
    )

    result, terminal, stop = runner._stage_result(
        "cassels_tate_refinement",
        db=db,
        run={"id": 1, "target_mode": "curve"},
        run_config={"target_rank": 10},
        target={},
        context={
            "curve_id": curve_id,
            "E": E,
            "parameter": "malformed",
        },
        config={"effort": 0, "timeout": 300},
        stage_index=1,
    )

    state = get_curve_research_state(db, curve_id)
    assert result["status"] == "error"
    assert result["reason"] == "cassels_tate_invalid_or_failed"
    assert state["rigorous_upper"] is None
    assert state["exact_rank"] is None
    assert get_curve(db, curve_id)["exact_rank"] is None
    assert terminal is None
    assert stop is False


def test_pipeline_timeout_preserves_prior_rigorous_upper(tmp_path, monkeypatch):
    db = connect(tmp_path / "timeout.db")
    curve_id, E = _curve(db, lower=0, parameter="timeout")
    record_rank_evidence(
        db,
        curve_id=curve_id,
        model=[str(x) for x in E.a_invariants()],
        data={
            "engine": "prior-rigorous",
            "evidence_type": "rank_bounds",
            "status": "completed",
            "rigorous": True,
            "rigorous_lower": None,
            "rigorous_upper": 4,
            "exact_rank": None,
            "assumptions": [],
            "points_found": [],
            "options": {},
        },
    )
    monkeypatch.setattr(
        runner,
        "run_cassels_tate_refinement",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            CasselsTateTimeout("injected timeout", runtime=0.25)
        ),
    )

    result, terminal, stop = runner._stage_result(
        "cassels_tate_refinement",
        db=db,
        run={"id": 1, "target_mode": "curve"},
        run_config={"target_rank": 10},
        target={},
        context={"curve_id": curve_id, "E": E, "parameter": "timeout"},
        config={"effort": 0, "timeout": 1},
        stage_index=1,
    )

    state = get_curve_research_state(db, curve_id)
    assert result["status"] == "timeout"
    assert result["rigorous_upper"] == 4
    assert state["rigorous_upper"] == 4
    assert state["exact_rank"] is None
    assert terminal is None
    assert stop is False


def test_builder_exposes_cassels_tate_controls():
    source = open(runner.__file__.replace("pipeline_runner.py", "ui_pages/build_your_own.py"), encoding="utf-8").read()
    assert 'stage_id == "cassels_tate_refinement"' in source
    assert "PARI point-search effort" in source
    assert "2-part Cassels pairing" in source
