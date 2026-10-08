import json
import subprocess
from pathlib import Path

import pytest
from sage.all import EllipticCurve, QQ

from rank42 import covering_reduce
from rank42 import pipeline_runner as runner
from rank42.covering_reduce import (
    CoveringReductionFailure,
    CoveringReductionTimeout,
    minimize_reduce_coverings,
    reduce_covering_model,
    validate_reduction_record,
)
from rank42.coverings import map_quartic_point
from rank42.db import connect, upsert_curve
from rank42.lattice_store import store_covering
from rank42.pipeline_catalog import (
    geometry_search_budget,
    normalize_pipeline,
    stage_spec,
    validate_pipeline,
)


TRANSLATION = 1000
SOURCE_COEFFICIENTS = [
    -TRANSLATION**3 + TRANSLATION,
    3 * TRANSLATION**2 - 1,
    -3 * TRANSLATION,
    1,
]


def _covering(curve_id):
    return {
        "schema": "rank42.covering.v1",
        "curve_id": curve_id,
        "quartic": {
            "coefficients": [str(x) for x in SOURCE_COEFFICIENTS],
            "height": 10000,
        },
        "map": {"x": f"u-{TRANSLATION}", "y": "v"},
        "metadata": {"source": "translated-cubic-fixture"},
    }


def _stored_curve_and_covering(tmp_path):
    db = connect(tmp_path / "covering-reduce.db")
    curve_id = upsert_curve(
        db,
        family="covering-reduce-test",
        parameter="1",
        a_invariants_json=json.dumps(["0", "0", "0", "-1", "0"]),
    )
    source = store_covering(db, _covering(curve_id))
    E = EllipticCurve(QQ, [0, 0, 0, -1, 0])
    return db, curve_id, E, source


def test_real_pari_reduction_preserves_exact_covering_map():
    result = reduce_covering_model(
        SOURCE_COEFFICIENTS,
        {"x": f"u-{TRANSLATION}", "y": "v"},
        timeout=20,
    )

    assert result["status"] == "completed"
    assert result["engine"] == "PARI hyperellred"
    assert result["coefficients"] == ["0", "-4", "0", "4"]
    assert result["map"] == {"x": "u", "y": "1/2*v"}
    assert result["transform"]["inverse_u"] == "u + 1000"
    assert result["transform"]["inverse_v"] == "1/2*v"
    assert result["equivalence_verified"] is True
    assert result["local_solubility_checked"] is False

    E = EllipticCurve(QQ, [0, 0, 0, -1, 0])
    data = {
        "schema": "rank42.covering.v1",
        "curve_id": 1,
        "quartic": {"coefficients": result["coefficients"], "height": 100},
        "map": result["map"],
    }
    assert map_quartic_point(E, data, 0, 0) == E(0, 0)


def test_real_pari_minimization_removes_nonminimal_scaling():
    result = reduce_covering_model(
        [0, -10000, 0, 1],
        {"x": "u/100", "y": "v/1000"},
        timeout=20,
    )
    assert result["coefficients"] == ["0", "-4", "0", "4"]
    assert result["map"] == {"x": "u", "y": "1/2*v"}
    assert result["transform"]["minimalized"] is True
    assert result["transform"]["e"] == "1000"


def test_reduction_runner_enforces_hard_timeout(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout", 1))

    monkeypatch.setattr(covering_reduce.subprocess, "run", timeout)
    with pytest.raises(CoveringReductionTimeout, match="exceeded 1s"):
        reduce_covering_model(
            SOURCE_COEFFICIENTS,
            {"x": f"u-{TRANSLATION}", "y": "v"},
            timeout=1,
        )


def test_parent_revalidates_worker_inverse_identity():
    forged = {
        "scale": "1",
        "f": ["0", "-1", "0", "1", "0"],
        "q": ["0", "0", "0"],
        "transform": {
            "e": "1",
            "matrix": ["1", "1", "0", "1"],
            "h": ["0", "0", "0"],
        },
    }
    with pytest.raises(CoveringReductionFailure, match="identity failed"):
        validate_reduction_record([0, -1, 0, 1], forged)


def test_stage_persists_child_then_supersedes_source_idempotently(tmp_path):
    db, curve_id, E, source = _stored_curve_and_covering(tmp_path)

    first = minimize_reduce_coverings(
        db,
        curve_id=curve_id,
        max_coverings=4,
        timeout=20,
        require_improvement=True,
    )
    assert first["status"] == "completed"
    assert first["coverings_reduced"] == 1
    assert first["rank_evidence_created"] is False
    branch = first["branches"][0]
    child_id = branch["reduced_covering_id"]
    assert branch["complexity_after"] < branch["complexity_before"]

    parent = db.execute(
        "SELECT * FROM coverings WHERE id=?", (int(source["id"]),)
    ).fetchone()
    child = db.execute(
        "SELECT * FROM coverings WHERE id=?", (child_id,)
    ).fetchone()
    assert parent["status"] == "reduced"
    assert child["status"] == "ready"
    child_meta = json.loads(child["metadata_json"])
    reduction = child_meta["covering_reduction"]
    assert reduction["parent_covering_id"] == int(source["id"])
    assert reduction["equivalence_verified"] is True
    assert reduction["local_solubility_checked"] is False
    child_data = {
        "schema": child["schema_version"],
        "curve_id": curve_id,
        "quartic": json.loads(child["quartic_json"]),
        "map": {"x": child["map_x"], "y": child["map_y"]},
    }
    assert map_quartic_point(E, child_data, 0, 0) == E(0, 0)

    second = minimize_reduce_coverings(
        db,
        curve_id=curve_id,
        max_coverings=4,
        timeout=20,
        require_improvement=True,
    )
    assert second["status"] == "completed"
    assert second["reason"] == "no_unreduced_coverings"
    assert db.execute("SELECT COUNT(*) FROM coverings").fetchone()[0] == 2


def test_stage_failure_leaves_original_covering_searchable(tmp_path, monkeypatch):
    db, curve_id, _E, source = _stored_curve_and_covering(tmp_path)
    monkeypatch.setattr(
        covering_reduce,
        "reduce_covering_model",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            CoveringReductionFailure("injected failure")
        ),
    )

    result = minimize_reduce_coverings(db, curve_id=curve_id, timeout=1)
    row = db.execute(
        "SELECT * FROM coverings WHERE id=?", (int(source["id"]),)
    ).fetchone()
    assert result["status"] == "error"
    assert result["retryable"] is True
    assert row["status"] == "ready"
    assert db.execute("SELECT COUNT(*) FROM coverings").fetchone()[0] == 1


def test_catalog_declares_scoped_covering_reduction_and_budget():
    spec = stage_spec("covering_minimize_reduce")
    assert spec.label == "Covering Minimization & Reduction"
    assert spec.category == "Geometry"
    assert spec.evidence == "exact"
    assert spec.defaults == {
        "max_coverings": 16,
        "timeout": 20,
        "require_improvement": True,
    }
    assert "reduced_coverings" in spec.provides
    assert "not local solubility" in spec.conditional
    assert validate_pipeline(
        "curve", normalize_pipeline(["covering_minimize_reduce"])
    ) == []
    bad = normalize_pipeline([{
        "id": "covering_minimize_reduce",
        "config": {
            "max_coverings": 0,
            "timeout": 0,
            "require_improvement": "yes",
        },
    }])
    errors = validate_pipeline("curve", bad)
    assert any("count and timeout" in error for error in errors)
    assert any("must be boolean" in error for error in errors)
    assert geometry_search_budget(
        "covering_minimize_reduce", spec.defaults
    )["worst_case_timeout_seconds"] == 320


def test_pipeline_runner_dispatches_covering_reduction(tmp_path, monkeypatch):
    db, curve_id, E, _source = _stored_curve_and_covering(tmp_path)
    captured = {}

    def fake_reduce(database, **kwargs):
        captured.update(kwargs)
        return {"status": "completed", "coverings_reduced": 1}

    monkeypatch.setattr(runner, "minimize_reduce_coverings", fake_reduce)
    result, terminal, stop = runner._stage_result(
        "covering_minimize_reduce",
        db=db,
        run={"id": 1, "target_mode": "curve"},
        run_config={"target_rank": 10},
        target={},
        context={"curve_id": curve_id, "E": E, "parameter": "1"},
        config=stage_spec("covering_minimize_reduce").defaults,
        stage_index=1,
    )
    assert result == {"status": "completed", "coverings_reduced": 1}
    assert captured == {
        "curve_id": curve_id,
        "max_coverings": 16,
        "timeout": 20,
        "require_improvement": True,
    }
    assert terminal is None
    assert stop is False


def test_builder_exposes_covering_reduction_controls():
    source = (
        Path(runner.__file__).parent / "ui_pages" / "build_your_own.py"
    ).read_text(encoding="utf-8")
    assert 'stage_id == "covering_minimize_reduce"' in source
    assert "Maximum coverings" in source
    assert "Hard timeout per covering" in source
    assert "Keep only smaller models" in source
    assert "claim local solubility" in source
