import json
import sqlite3

import pytest

from sage.all import EllipticCurve, QQ

from rank42.db import connect, upsert_curve
from rank42 import plugin_geometry_result as geometry_result
from rank42.plugin_geometry_result import (
    RESULT_ENV,
    SCHEMA,
    force_command_database,
    legacy_sandbox_geometry_result,
    load_plugin_geometry_result,
    plugin_geometry_environment,
    plugin_geometry_sandbox,
    snapshot_plugin_geometry_subject,
    validate_plugin_geometry_result,
)


def _valid(**overrides):
    result = {
        "schema": SCHEMA,
        "status": "completed",
        "engine": "demo-geometry",
        "engine_version": "1.0",
        "algorithm": "native-quartic-search",
        "points": [["0", "1"]],
        "artifacts": [{"kind": "quartic", "id": "q1"}],
        "metadata": {"fixture": True},
    }
    result.update(overrides)
    return result


def test_plugin_geometry_result_requires_typed_engine_contract():
    result = validate_plugin_geometry_result(_valid())
    assert result["schema"] == SCHEMA
    assert result["engine"] == "demo-geometry"
    assert result["algorithm"] == "native-quartic-search"
    assert result["points"] == [["0", "1"]]
    assert result["artifacts"][0]["kind"] == "quartic"


def test_plugin_geometry_result_rejects_untyped_and_malformed_claims():
    untyped = _valid()
    untyped.pop("schema")
    with pytest.raises(ValueError, match="unsupported Plugin Geometry result schema"):
        validate_plugin_geometry_result(untyped)

    with pytest.raises(ValueError, match="non-empty engine"):
        validate_plugin_geometry_result(_valid(engine=""))

    with pytest.raises(ValueError, match=r"point must be \[x,y\]"):
        validate_plugin_geometry_result(_valid(points=[["0"]]))

    with pytest.raises(ValueError, match="rigorous_upper_certificate must be an object"):
        validate_plugin_geometry_result(_valid(rigorous_upper_certificate=5))


def test_force_command_database_rewrites_only_declared_db_argument(tmp_path):
    sandbox = tmp_path / "sandbox.db"
    command = force_command_database(
        [
            "python",
            "worker.py",
            "--db",
            "/live/rank42.db",
            "--note",
            "/live/rank42.db",
        ],
        sandbox,
    )
    assert command[command.index("--db") + 1] == str(sandbox.resolve())
    assert command[command.index("--note") + 1] == "/live/rank42.db"

    equals = force_command_database(
        ["python", "worker.py", "--db=/live/rank42.db"],
        sandbox,
    )
    assert equals[-1] == f"--db={sandbox.resolve()}"


def test_force_command_database_refuses_uncontrollable_legacy_command(tmp_path):
    with pytest.raises(ValueError, match="must expose a --db argument"):
        force_command_database(
            ["python", "worker.py", "--curve-id", "7"],
            tmp_path / "sandbox.db",
        )



def test_unverified_plugin_geometry_upper_never_promotes(
    tmp_path, monkeypatch
):
    db = connect(tmp_path / "rank42.db")
    E = EllipticCurve(QQ, [0, 0, 0, -1, 1])
    curve_id = upsert_curve(
        db,
        family="geometry-upper-test",
        parameter="1",
        a_invariants_json=json.dumps(
            [str(value) for value in E.a_invariants()]
        ),
        status="test",
    )
    monkeypatch.setattr(
        geometry_result,
        "verify_plugin_rigorous_upper_certificate",
        lambda *args, **kwargs: {
            "verified": False,
            "reason": "unsupported_certificate_schema",
        },
    )
    monkeypatch.setattr(
        geometry_result,
        "promote_rigorous_rank_interval",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("unverified Plugin Geometry upper must not promote")
        ),
    )

    applied = geometry_result.apply_plugin_geometry_result(
        db,
        curve_id=curve_id,
        E=E,
        plugin_id="demo",
        result=_valid(
            points=[],
            rigorous_upper_certificate={
                "schema": "untrusted.plugin.upper",
                "status": "completed",
                "rigorous_upper": 99,
            },
        ),
        search_ref="plugin-geometry:test",
        certificate_timeout=5,
        exact_candidates=8,
    )
    assert applied["verified_plugin_upper"] is None
    assert applied["rigorous_upper_verification"]["verified"] is False
    assert db.execute(
        "SELECT COUNT(*) AS n FROM rank_evidence WHERE curve_id=?",
        (curve_id,),
    ).fetchone()["n"] == 0





def test_legacy_sandbox_generator_projection_becomes_core_point_candidate(tmp_path):
    db = connect(tmp_path / "rank42.db")
    E = EllipticCurve(QQ, [0, 0, 0, -1, 1])
    curve_id = upsert_curve(
        db,
        family="sandbox-generator-test",
        parameter="1",
        a_invariants_json=json.dumps(
            [str(value) for value in E.a_invariants()]
        ),
        status="test",
    )
    baseline = snapshot_plugin_geometry_subject(db, curve_id)

    with plugin_geometry_sandbox(db) as sandbox:
        con = sqlite3.connect(str(sandbox))
        try:
            con.execute(
                "UPDATE curves SET generators_json=? WHERE id=?",
                (json.dumps([["0", "1"]]), int(curve_id)),
            )
            con.commit()
        finally:
            con.close()

        result = legacy_sandbox_geometry_result(
            sandbox,
            baseline=baseline,
            plugin_id="demo",
            plugin_version="1",
            variant_id="default",
        )

    assert result["points"] == [["0", "1"]]
    assert result["metadata"]["sandbox_generator_candidates"] == 1
    assert result["metadata"]["sandbox_curve_projection_write_discarded"] is True

def test_plugin_geometry_result_file_round_trip_and_environment(tmp_path):
    path = tmp_path / "plugin-geometry-result.json"
    path.write_text(json.dumps(_valid()), encoding="utf-8")
    loaded = load_plugin_geometry_result(path)
    assert loaded["schema"] == SCHEMA
    assert loaded["engine"] == "demo-geometry"
    env = plugin_geometry_environment(path)
    assert env[RESULT_ENV] == str(path.resolve())


def test_plugin_geometry_off_curve_typed_point_is_rejected(tmp_path):
    db = connect(tmp_path / "rank42.db")
    E = EllipticCurve(QQ, [0, 0, 0, -1, 1])
    curve_id = upsert_curve(
        db,
        family="geometry-point-test",
        parameter="1",
        a_invariants_json=json.dumps(
            [str(value) for value in E.a_invariants()]
        ),
        status="test",
    )
    applied = geometry_result.apply_plugin_geometry_result(
        db,
        curve_id=curve_id,
        E=E,
        plugin_id="demo",
        result=_valid(points=[["0", "0"]]),
        search_ref="plugin-geometry:offcurve",
        certificate_timeout=5,
        exact_candidates=8,
    )
    assert applied["exact_points_accepted"] == 0
    assert applied["points_rejected"] == 1
    assert applied["point_rejection_samples"][0]["point"] == ["0", "0"]
    assert db.execute(
        "SELECT COUNT(*) AS n FROM points WHERE curve_id=?",
        (curve_id,),
    ).fetchone()["n"] == 0
