import json

from sage.all import QQ, EllipticCurve

from rank42.db import connect, update_curve, upsert_curve
from rank42.pipeline_catalog import template_stages, validate_pipeline
from rank42.pipeline_runner import _record_breaker_goal_hit
from rank42.pipeline_state import get_pipeline_run, pipeline_run_payload
from rank42.points import upsert_point
from rank42.record_breaker import (
    build_submission,
    create_record_breaker_run,
    curve_frontier,
)


def test_frontiermath_record_breaker_preset_is_valid_across_modes():
    for mode in ("family", "general", "curve", "torsion"):
        stages = template_stages("frontiermath_record_breaker", mode)
        assert stages
        assert validate_pipeline(mode, stages) == []
        ids = [rec["id"] for rec in stages]
        assert "record_breaker_lane" in ids
        assert ids[-1] == "stop_goal"
        assert "final_upper" not in ids


def test_record_breaker_goal_requires_rigorous_lower_and_exportable_witnesses():
    config = {"record_breaker_mode": True, "target_rank": 30}
    assert _record_breaker_goal_hit(
        config,
        {"rigorous_lower": 30, "rigorous_witnesses": 30, "numerical_rank_signal": 40},
    ) is True
    assert _record_breaker_goal_hit(
        config,
        {"rigorous_lower": 30, "rigorous_witnesses": 29, "numerical_rank_signal": 100},
    ) is False
    assert _record_breaker_goal_hit(
        config,
        {"rigorous_lower": 29, "rigorous_witnesses": 40, "numerical_rank_signal": 100},
    ) is False
    assert _record_breaker_goal_hit(
        {"record_breaker_mode": False, "target_rank": 30},
        {"rigorous_lower": 31, "rigorous_witnesses": 31},
    ) is False


def test_create_record_breaker_run_sets_target_mode_and_flag(tmp_path):
    db = connect(tmp_path / "rank42.db")
    run_id = create_record_breaker_run(
        db,
        target_mode="curve",
        target={"curve_id": 7},
        target_rank=31,
        project_root=tmp_path,
    )
    run = pipeline_run_payload(get_pipeline_run(db, run_id))
    assert run["target_mode"] == "curve"
    assert run["run_config"]["target_rank"] == 31
    assert run["run_config"]["record_breaker_mode"] is True
    assert [rec["id"] for rec in run["stages"]][-1] == "stop_goal"
    db.close()


def test_frontier_and_submission_use_only_rigorous_exact_witnesses(tmp_path):
    db = connect(tmp_path / "rank42.db")
    E = EllipticCurve(QQ, [0, 0, 0, 0, -2])
    P = E(3, 5)
    curve_id = upsert_curve(
        db,
        family="test",
        parameter="rank-one",
        a_invariants_json=json.dumps([str(x) for x in E.a_invariants()]),
        status="proven_lower",
    )
    update_curve(
        db,
        curve_id,
        descent_lower=1,
        generators_json=json.dumps([[str(P[0]), str(P[1])]]),
    )
    upsert_point(
        db,
        curve_id=curve_id,
        x=P[0],
        y=P[1],
        source="test",
        role="rigorous_witness",
        exact_verified=True,
        independence_status="rigorous_independent",
        rigorous_independent=True,
    )

    frontier = curve_frontier(db, target_rank=1, limit=5)
    assert frontier[0]["curve_id"] == curve_id
    assert frontier[0]["rigorous_lower"] == 1
    assert frontier[0]["rank_gap"] == 0
    assert frontier[0]["witness_gap"] == 0
    assert frontier[0]["target_reached"] is True
    assert frontier[0]["export_candidate"] is True

    payload, meta = build_submission(
        db,
        curve_id=curve_id,
        target_rank=1,
        verify_independence=False,
    )
    assert set(payload) == {"curve", "x_coords"}
    assert len(payload["curve"]) == 5
    assert all("/" not in value for value in payload["curve"])
    assert payload["x_coords"] == ["3"]
    assert meta["rigorous_lower"] == 1
    db.close()


def test_submission_refuses_curve_below_target(tmp_path):
    db = connect(tmp_path / "rank42.db")
    E = EllipticCurve(QQ, [0, 0, 0, 0, -2])
    curve_id = upsert_curve(
        db,
        family="test",
        parameter="below",
        a_invariants_json=json.dumps([str(x) for x in E.a_invariants()]),
    )
    try:
        build_submission(
            db,
            curve_id=curve_id,
            target_rank=1,
            verify_independence=False,
        )
    except ValueError as exc:
        assert "below target" in str(exc)
    else:
        raise AssertionError("expected submission refusal below rigorous target")
    db.close()
