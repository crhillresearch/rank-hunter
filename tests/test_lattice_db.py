import json
import subprocess

import pytest
from sage.all import EllipticCurve, QQ, ZZ, matrix

from rank42.db import connect, upsert_curve, lattice_stats
from rank42 import lattice
from rank42.lattice_store import (
    list_covering_search_attempts,
    record_covering_search_attempt,
    store_covering,
    store_extra_point,
    store_holes,
    store_lattice,
)


def test_run_lattice_build_enforces_hard_subprocess_timeout(monkeypatch):
    captured = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(lattice.subprocess, "run", fake_run)

    with pytest.raises(lattice.LatticeTimeout, match="exceeded 7s"):
        lattice.run_lattice_build(
            ["0", "0", "1", "-1", "0"],
            [("0", "0")],
            precision=256,
            timeout=7,
        )

    assert captured["timeout"] == 7


def test_real_curve_lll_transform_is_unimodular_and_exact():
    # 389a1: y^2 + y = x^3 + x^2 - 2x, with rank-two generators
    # P=(0,0), Q=(1,0).  This checks Sage's documented transform convention
    # used by rank42.lattice.build_lattice().
    E = EllipticCurve(QQ, [0, 1, 1, -2, 0])
    points = [E(0, 0), E(1, 0)]

    result = lattice.build_lattice(
        E,
        points,
        precision=128,
        lll_reduce=True,
    )

    transform = result["lll_transform"]
    assert transform is not None
    U = matrix(
        ZZ,
        [[ZZ(value) for value in row] for row in transform],
    )
    assert abs(U.det()) == 1

    input_points = result["input_points"]
    for j, reduced_point in enumerate(result["points"]):
        reconstructed = E(0)
        for i, point in enumerate(input_points):
            reconstructed += U[i, j] * point
        assert reconstructed == reduced_point


def test_lattice_covering_schema_migrates_and_deduplicates(tmp_path):
    db = connect(tmp_path / "l.db")
    cid = upsert_curve(db, family="test", parameter="1", score=1.0)
    L = store_lattice(
        db,
        curve_id=cid,
        source="points-json",
        basis=[["0", "0"], ["1", "1"]],
        gram=[["1.0", "0.0"], ["0.0", "2.0"]],
        precision_bits=128,
        determinant="2.0",
        min_eigenvalue="1.0",
        positive_definite_screen=True,
    )
    store_holes(
        db, L["id"],
        [{"bits": "11", "representative": ["1/2", "1/2"], "norm2": 0.75}],
        method="test",
    )
    H = db.execute("SELECT * FROM lattice_holes WHERE lattice_id=?", (L["id"],)).fetchone()
    data = {
        "schema": "rank42.covering.v1",
        "curve_id": cid,
        "lattice_id": L["id"],
        "hole_id": H["id"],
        "quartic": {"coefficients": ["1", "0", "0", "0", "1"], "height": 100},
        "map": {"x": "u", "y": "v"},
    }
    C1 = store_covering(db, data)
    C2 = store_covering(db, data)
    assert C1["id"] == C2["id"]

    store_extra_point(
        db,
        covering_id=C1["id"], quartic_point_id=None, curve_id=cid,
        x="0", y="1", exact_verified=True, independence_screen=True,
        basis_count_before=2, basis_count_after=3,
        determinant="3", min_eigenvalue="0.5",
    )
    s = lattice_stats(db)
    assert s["lattices"] == 1
    assert s["holes"] == 1
    assert s["coverings"] == 1
    assert s["extra_points"] == 1
    assert s["independent_screens"] == 1



def test_covering_search_attempts_are_append_only(tmp_path):
    db = connect(tmp_path / "attempts.db")
    cid = upsert_curve(db, family="test", parameter="attempts", score=1.0)
    covering = store_covering(db, {
        "schema": "rank42.covering.v1",
        "curve_id": cid,
        "quartic": {
            "coefficients": ["1", "0", "0", "0", "1"],
            "height": 100,
        },
        "map": {"x": "u", "y": "v"},
    })

    first = record_covering_search_attempt(
        db,
        covering_id=covering["id"],
        curve_id=cid,
        pipeline_run_id=7,
        pipeline_stage_index=3,
        pipeline_stage_id="selmer_element_fanout",
        height=1000,
        timeout_seconds=5,
        backend="ratpoints",
        one_point=False,
        outcome="timeout",
        runtime_seconds=5.01,
        error="timed out",
        metadata={"ratpoints_executable": "/tmp/ratpoints"},
    )
    second = record_covering_search_attempt(
        db,
        covering_id=covering["id"],
        curve_id=cid,
        pipeline_run_id=7,
        pipeline_stage_index=3,
        pipeline_stage_id="selmer_element_fanout",
        height=10000,
        timeout_seconds=20,
        backend="ratpoints",
        one_point=True,
        outcome="completed",
        ratpoints_hits=2,
        mapped_points=1,
        runtime_seconds=0.25,
        metadata={"ratpoints_executable": "/tmp/ratpoints"},
    )

    assert second > first
    attempts = list_covering_search_attempts(db, covering["id"])
    assert [row["id"] for row in attempts] == [second, first]
    assert attempts[0]["height"] == 10000
    assert attempts[0]["timeout_seconds"] == 20
    assert attempts[0]["outcome"] == "completed"
    assert attempts[0]["ratpoints_hits"] == 2
    assert attempts[0]["mapped_points"] == 1
    assert attempts[1]["outcome"] == "timeout"
    assert attempts[1]["error"] == "timed out"

    # Mutating current covering state must not rewrite historical attempts.
    db.execute(
        "UPDATE coverings SET status='mapped',error=NULL WHERE id=?",
        (int(covering["id"]),),
    )
    db.commit()
    attempts = list_covering_search_attempts(db, covering["id"])
    assert [row["outcome"] for row in attempts] == ["completed", "timeout"]
