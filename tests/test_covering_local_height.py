import json
import subprocess
from pathlib import Path

import pytest
from sage.all import EllipticCurve, QQ

from rank42 import covering_local_height as local_height
from rank42 import pipeline_runner as runner
from rank42 import research_modules as research
from rank42.covering_local_height import (
    CoveringLocalTimeout,
    plan_covering_searches,
    run_covering_local_analysis,
)
from rank42.db import connect, upsert_curve
from rank42.lattice_store import store_covering, update_covering_metadata
from rank42.pipeline_catalog import (
    geometry_search_budget,
    normalize_pipeline,
    stage_spec,
    validate_pipeline,
)


def _db(tmp_path):
    db = connect(tmp_path / "covering-local.db")
    curve_id = upsert_curve(
        db,
        family="covering-local-test",
        parameter="1",
        a_invariants_json=json.dumps(["0", "0", "0", "-1", "0"]),
    )
    E = EllipticCurve(QQ, [0, 0, 0, -1, 0])
    return db, curve_id, E


def _store(db, curve_id, coefficients, *, tag):
    return store_covering(db, {
        "schema": "rank42.covering.v1",
        "curve_id": curve_id,
        "quartic": {"coefficients": [str(x) for x in coefficients], "height": 100},
        "map": {"x": "u", "y": "v/2"},
        "metadata": {"tag": tag},
    })


def test_real_pari_full_local_solver_distinguishes_quartics():
    soluble = run_covering_local_analysis([1, 0, 0, 0, 1], timeout=10)
    assert soluble["status"] == "everywhere_locally_soluble"
    assert soluble["all_places_checked"] is True
    assert soluble["rational_point_at_infinity"] is True

    obstructed = run_covering_local_analysis([2, 0, 1, 0, 2], timeout=10)
    assert obstructed["status"] == "locally_insoluble"
    assert obstructed["obstruction"] == 2
    assert obstructed["relevant_primes"] == [2, 3, 5]
    assert obstructed["checks"][1]["method"] == "PARI_hyperell_locally_soluble"


def test_local_worker_timeout_is_inconclusive(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout", 1))

    monkeypatch.setattr(local_height.subprocess, "run", timeout)
    with pytest.raises(CoveringLocalTimeout, match="exceeded 1s"):
        run_covering_local_analysis([1, 0, 0, 0, 1], timeout=1)


def test_stage_persists_full_local_proof_and_heuristic_plan(tmp_path):
    db, curve_id, _E = _db(tmp_path)
    easy = _store(db, curve_id, [0, -4, 0, 4], tag="easy")
    obstructed = _store(db, curve_id, [2, 0, 1, 0, 2], tag="obstructed")

    result = plan_covering_searches(db, curve_id=curve_id, timeout=10)
    assert result["status"] == "completed"
    assert result["locally_obstructed"] == 1
    assert result["height_guidance_is_rigorous"] is False
    assert result["rank_evidence_created"] is False

    easy_row = db.execute("SELECT * FROM coverings WHERE id=?", (easy["id"],)).fetchone()
    easy_meta = json.loads(easy_row["metadata_json"])["covering_local_height_plan"]
    assert easy_row["status"] == "ready"
    assert easy_meta["local_analysis"]["status"] == "everywhere_locally_soluble"
    assert easy_meta["proof_scope"] == "all_completions_of_Q"
    assert easy_meta["height_plan"]["rigorous_height_bound"] is False
    assert easy_meta["height_plan"]["search_priority"] == 1

    bad_row = db.execute(
        "SELECT * FROM coverings WHERE id=?", (obstructed["id"],)
    ).fetchone()
    bad_meta = json.loads(bad_row["metadata_json"])["covering_local_height_plan"]
    assert bad_row["status"] == "locally_obstructed"
    assert bad_meta["local_analysis"]["obstruction"] == 2
    assert bad_meta["height_plan"]["recommended_search"] is False


def test_stage_timeout_leaves_covering_active(tmp_path, monkeypatch):
    db, curve_id, _E = _db(tmp_path)
    covering = _store(db, curve_id, [0, -4, 0, 4], tag="timeout")
    monkeypatch.setattr(
        local_height,
        "run_covering_local_analysis",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            CoveringLocalTimeout("injected timeout", runtime=0.1)
        ),
    )
    result = plan_covering_searches(db, curve_id=curve_id, timeout=1)
    row = db.execute("SELECT * FROM coverings WHERE id=?", (covering["id"],)).fetchone()
    assert result["status"] == "timeout"
    assert result["retryable"] is True
    assert row["status"] == "ready"


def test_exact_covering_fanout_consumes_stored_search_plan(tmp_path, monkeypatch):
    db, curve_id, E = _db(tmp_path)
    covering = _store(db, curve_id, [0, -4, 0, 4], tag="planned")
    update_covering_metadata(db, covering["id"], {
        "covering_local_height_plan": {
            "height_plan": {
                "recommended_height": 12345,
                "recommended_timeout": 77,
                "recommended_search": True,
            }
        }
    })
    captured = {}
    monkeypatch.setattr(
        research,
        "_import_plugin_coverings",
        lambda *args, **kwargs: {"status": "capability_absent"},
    )

    def fake_search(coefficients, height, **kwargs):
        captured.update(height=height, timeout=kwargs["timeout"])
        return {"points": [], "runtime": 0.01}

    monkeypatch.setattr(research, "run_ratpoints", fake_search)
    monkeypatch.setattr(
        research,
        "certify_ledger_growth",
        lambda *args, **kwargs: {"attempts": 0, "growth": 0, "rigorous_lower": 0},
    )
    result = research.selmer_element_fanout(
        db,
        context={"curve_id": curve_id, "E": E, "plugin": None, "parameter": "1"},
        config={
            "max_coverings": 1,
            "height": 1000,
            "timeout": 3,
            "use_covering_plans": True,
        },
        ratpoints=None,
        run_id=8,
        stage_index=2,
        certificate_timeout=10,
        exact_candidates=8,
    )
    assert result["status"] == "completed"
    assert captured == {"height": 12345, "timeout": 77}
    attempt = db.execute(
        "SELECT * FROM covering_search_attempts WHERE covering_id=?",
        (int(covering["id"]),),
    ).fetchone()
    assert attempt["height"] == 12345
    assert attempt["timeout_seconds"] == 77


def test_catalog_contract_validation_and_budget():
    spec = stage_spec("covering_local_height_planner")
    assert spec.label == "Local Solubility & Covering Height Planner"
    assert spec.evidence == "exact"
    assert "covering_search_plans" in spec.provides
    assert spec.defaults["reject_locally_insoluble"] is True
    assert validate_pipeline(
        "curve", normalize_pipeline(["covering_local_height_planner"])
    ) == []
    bad = normalize_pipeline([{
        "id": "covering_local_height_planner",
        "config": {
            **spec.defaults,
            "base_height": 100,
            "max_height": 10,
            "reject_locally_insoluble": "yes",
        },
    }])
    errors = validate_pipeline("curve", bad)
    assert any("maximum budgets" in error for error in errors)
    assert any("must be boolean" in error for error in errors)
    budget = geometry_search_budget("covering_local_height_planner", spec.defaults)
    assert budget["worst_case_timeout_seconds"] == 480


def test_pipeline_runner_dispatches_planner(tmp_path, monkeypatch):
    db, curve_id, E = _db(tmp_path)
    captured = {}

    def fake_plan(database, **kwargs):
        captured.update(kwargs)
        return {"status": "completed", "coverings_seen": 0}

    monkeypatch.setattr(runner, "plan_covering_searches", fake_plan)
    result, terminal, stop = runner._stage_result(
        "covering_local_height_planner",
        db=db,
        run={"id": 1, "target_mode": "curve"},
        run_config={"target_rank": 10},
        target={},
        context={"curve_id": curve_id, "E": E, "parameter": "1"},
        config=stage_spec("covering_local_height_planner").defaults,
        stage_index=1,
    )
    assert result["status"] == "completed"
    assert captured["curve_id"] == curve_id
    assert captured["base_height"] == 10000
    assert captured["reject_locally_insoluble"] is True
    assert terminal is None and stop is False


def test_builder_exposes_local_proof_and_heuristic_boundary():
    source = (
        Path(runner.__file__).parent / "ui_pages" / "build_your_own.py"
    ).read_text(encoding="utf-8")
    assert 'stage_id == "covering_local_height_planner"' in source
    assert "Local-analysis timeout per covering" in source
    assert "Suppress rigorously insoluble coverings" in source
    assert "planning heuristics" in source
