import json
import subprocess
from pathlib import Path

import pytest
from sage.all import EllipticCurve, QQ

from rank42 import isogeny_descent
from rank42 import pipeline_runner as runner
from rank42.curve_research_state import get_curve_research_state
from rank42.db import connect, get_curve, upsert_curve
from rank42.isogeny_descent import (
    IsogenyDescentTimeout,
    run_isogeny_descent,
)
from rank42.pipeline_catalog import normalize_pipeline, stage_spec, validate_pipeline
from rank42.rank_evidence import record_rank_evidence


ONE_ISOGENY_CURVE = ["1", "0", "1", "4", "-6"]  # 14a1
THREE_ISOGENY_CURVE = ["0", "0", "0", "-25", "0"]
NO_TWO_ISOGENY_CURVE = ["0", "-1", "1", "-929", "-10595"]


def _curve(db, *, lower=0, parameter="1"):
    curve_id = upsert_curve(
        db,
        family="isogeny-descent-test",
        parameter=str(parameter),
        a_invariants_json=json.dumps(ONE_ISOGENY_CURVE),
        generic_lower=lower,
    )
    return curve_id, EllipticCurve(QQ, [QQ(x) for x in ONE_ISOGENY_CURVE])


def _completed_attempt(E):
    Em = E.global_minimal_model()
    phi = list(Em.isogenies_prime_degree(2))[0]
    dual = phi.dual()
    target = phi.codomain()
    route = {
        "route_index": 1,
        "isogenous_model_a_invariants": [str(x) for x in target.a_invariants()],
        "isogenous_minimal_model_a_invariants": [
            str(x) for x in target.global_minimal_model().a_invariants()
        ],
        "phi_selmer_dimension": 1,
        "phi_selmer_relation": "=",
        "dual_phi_selmer_dimension": 1,
        "dual_phi_selmer_relation": "=",
        "first_descent_rank_upper": 0,
        "first_descent_relation": "=",
        "phi_image_two_selmer_dimension": 1,
        "phi_image_two_selmer_relation": "=",
        "dual_phi_image_two_selmer_dimension": 1,
        "dual_phi_image_two_selmer_relation": "=",
        "source_two_selmer_dimension": 1,
        "source_two_selmer_relation": "=",
        "target_two_selmer_dimension": 1,
        "target_two_selmer_relation": "=",
        "second_descent_rank_upper": 0,
        "second_descent_relation": "=",
        "sage_map_index": 1,
        "map_verified": True,
        "degree": 2,
        "kernel_polynomial": str(phi.kernel_polynomial()),
        "phi_rational_maps": [str(value) for value in phi.rational_maps()],
        "dual_rational_maps": [str(value) for value in dual.rational_maps()],
        "sage_codomain_a_invariants": [str(x) for x in target.a_invariants()],
    }
    return {
        "status": "completed",
        "engine": "eclib.mwrank_2_isogeny_descent",
        "engine_version": "test-eclib",
        "sage_version": "test-sage",
        "degree": 2,
        "minimal_model_a_invariants": [str(x) for x in Em.a_invariants()],
        "two_torsion_rank": 1,
        "isogeny_count": 1,
        "routes": [route],
        "first_descent_combined_upper": 0,
        "mordell_weil_rank_lower_diagnostic": 0,
        "mordell_weil_rank_upper": 0,
        "two_selmer_rank": 1,
        "mwrank_certain": True,
        "second_descent_requested": True,
        "first_limit": 20,
        "second_limit": 8,
        "map_verification": "exact_sage_rational_2_isogenies",
        "unconditional": True,
        "assumptions": [],
        "transcript_sha256": "test",
        "transcript_tail": [],
        "started_at": "2026-09-26T00:00:00+00:00",
        "finished_at": "2026-09-26T00:00:01+00:00",
        "_runtime_seconds": 1.0,
    }


def test_real_mwrank_isogeny_descent_exposes_phi_and_dual_selmer_routes():
    E = EllipticCurve(QQ, [QQ(x) for x in THREE_ISOGENY_CURVE])
    result = run_isogeny_descent(E, timeout=30)

    assert result["status"] == "completed"
    assert result["engine"] == "eclib.mwrank_2_isogeny_descent"
    assert result["degree"] == 2
    assert result["isogeny_count"] == 3
    assert result["two_torsion_rank"] == 2
    assert result["first_descent_combined_upper"] == 1
    assert result["mordell_weil_rank_upper"] == 1
    assert result["unconditional"] is True
    assert result["assumptions"] == []
    assert len(result["routes"]) == 3
    for route in result["routes"]:
        assert route["map_verified"] is True
        assert route["degree"] == 2
        assert route["phi_selmer_relation"] == "="
        assert route["dual_phi_selmer_relation"] == "="
        assert route["first_descent_rank_upper"] == (
            route["phi_selmer_dimension"]
            + route["dual_phi_selmer_dimension"]
            - 2
        )


def test_real_isogeny_descent_declines_missing_two_isogeny_and_degree_three():
    no_two = EllipticCurve(QQ, [QQ(x) for x in NO_TWO_ISOGENY_CURVE])
    unsupported = run_isogeny_descent(no_two, timeout=30)
    assert unsupported["status"] == "unsupported"
    assert unsupported["reason"] == "no_rational_2_isogeny"

    one_two = EllipticCurve(QQ, [QQ(x) for x in ONE_ISOGENY_CURVE])
    degree_three = run_isogeny_descent(one_two, degree=3, timeout=30)
    assert degree_three["status"] == "unsupported"
    assert degree_three["reason"] == "core_isogeny_descent_v1_supports_degree_2_only"


def test_isogeny_descent_runner_enforces_hard_timeout(monkeypatch):
    E = EllipticCurve(QQ, [QQ(x) for x in ONE_ISOGENY_CURVE])

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout", 1))

    monkeypatch.setattr(isogeny_descent.subprocess, "run", timeout)
    with pytest.raises(IsogenyDescentTimeout):
        run_isogeny_descent(E, timeout=1)


def test_catalog_declares_real_two_isogeny_descent_and_validates_config():
    spec = stage_spec("isogeny_descent")
    assert spec.label == "Isogeny Descent"
    assert spec.category == "Arithmetic"
    assert spec.evidence == "rigorous"
    assert "isogeny_selmer_data" in spec.provides
    assert spec.defaults == {
        "degree": 2,
        "first_limit": 20,
        "second_limit": 8,
        "second_descent": True,
        "timeout": 300,
    }
    assert "phi- and dual-phi Selmer dimensions" in spec.description
    assert "not an isogeny walk" in spec.description
    assert "Degree-3" in spec.conditional

    assert validate_pipeline(
        "curve",
        normalize_pipeline(["isogeny_descent"]),
    ) == []

    bad = normalize_pipeline([{
        "id": "isogeny_descent",
        "config": {
            "degree": 3,
            "first_limit": 0,
            "second_limit": 0,
            "second_descent": "yes",
            "timeout": 0,
        },
    }])
    errors = validate_pipeline("curve", bad)
    assert any("degree 2 only" in error for error in errors)
    assert any("search limits" in error for error in errors)
    assert any("second descent" in error for error in errors)
    assert any("hard timeout" in error for error in errors)


def test_pipeline_promotes_isogeny_descent_upper_only_through_reducer(
    tmp_path,
    monkeypatch,
):
    db = connect(tmp_path / "isogeny.db")
    curve_id, E = _curve(db, lower=0)
    monkeypatch.setattr(
        runner,
        "run_isogeny_descent",
        lambda *args, **kwargs: _completed_attempt(E),
    )

    result, terminal, stop = runner._stage_result(
        "isogeny_descent",
        db=db,
        run={"id": 1, "target_mode": "curve"},
        run_config={"target_rank": 10},
        target={},
        context={"curve_id": curve_id, "E": E, "parameter": "1"},
        config=stage_spec("isogeny_descent").defaults,
        stage_index=1,
    )

    state = get_curve_research_state(db, curve_id)
    evidence = db.execute(
        "SELECT * FROM rank_evidence WHERE id=?",
        (result["evidence_id"],),
    ).fetchone()
    assert result["status"] == "completed"
    assert result["routes"][0]["phi_selmer_dimension"] == 1
    assert result["routes"][0]["dual_phi_selmer_dimension"] == 1
    assert result["rigorous_upper"] == 0
    assert result["exact_rank"] == 0
    assert state["rigorous_upper"] == 0
    assert state["exact_rank"] == 0
    assert int(get_curve(db, curve_id)["exact_rank"]) == 0
    assert evidence["evidence_type"] == "isogeny_descent_2"
    assert json.loads(evidence["points_json"]) == []
    assert terminal is None
    assert stop is False


def test_pipeline_rejects_forged_isogeny_map_attestation(tmp_path, monkeypatch):
    db = connect(tmp_path / "forged.db")
    curve_id, E = _curve(db, lower=0, parameter="forged")
    forged = _completed_attempt(E)
    forged["routes"][0]["kernel_polynomial"] = "x + 12345"
    monkeypatch.setattr(
        runner,
        "run_isogeny_descent",
        lambda *args, **kwargs: forged,
    )

    result, terminal, stop = runner._stage_result(
        "isogeny_descent",
        db=db,
        run={"id": 1, "target_mode": "curve"},
        run_config={"target_rank": 10},
        target={},
        context={"curve_id": curve_id, "E": E, "parameter": "forged"},
        config=stage_spec("isogeny_descent").defaults,
        stage_index=1,
    )

    state = get_curve_research_state(db, curve_id)
    assert result["status"] == "error"
    assert result["reason"] == "isogeny_descent_invalid_or_failed"
    assert state["rigorous_upper"] is None
    assert state["exact_rank"] is None
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
        "run_isogeny_descent",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            IsogenyDescentTimeout("injected timeout", runtime=0.25)
        ),
    )

    result, terminal, stop = runner._stage_result(
        "isogeny_descent",
        db=db,
        run={"id": 1, "target_mode": "curve"},
        run_config={"target_rank": 10},
        target={},
        context={"curve_id": curve_id, "E": E, "parameter": "timeout"},
        config=stage_spec("isogeny_descent").defaults,
        stage_index=1,
    )

    state = get_curve_research_state(db, curve_id)
    assert result["status"] == "timeout"
    assert result["rigorous_upper"] == 4
    assert state["rigorous_upper"] == 4
    assert state["exact_rank"] is None
    assert terminal is None
    assert stop is False


def test_builder_exposes_scoped_isogeny_descent_controls():
    source = (Path(runner.__file__).parent / "ui_pages" / "build_your_own.py").read_text(
        encoding="utf-8"
    )
    assert 'stage_id == "isogeny_descent"' in source
    assert "Isogeny degree" in source
    assert "Degree 3 requires optional Magma" in source
    assert "matched to an exact Sage" in source
    assert "isogeny map before" in source
