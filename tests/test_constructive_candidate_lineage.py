from types import SimpleNamespace

from rank42 import pipeline_runner as runner
from rank42.db import connect, upsert_curve
from rank42.pipeline_state import (
    create_pipeline_run,
    get_pipeline_candidate,
    upsert_pipeline_candidate,
)


def _setup(tmp_path):
    db = connect(tmp_path / "rank42.db")
    curve_id = upsert_curve(
        db,
        family="lineage-test",
        parameter="shared",
        a_invariants_json='["0","0","0","-1","0"]',
        status="test",
    )
    run_id = create_pipeline_run(
        db,
        pipeline_name="Constructive lineage",
        target_mode="family",
        target={"plugin_id": "demo", "variant_id": "v1"},
        stages=[
            {"id": "section_height_shell", "config": {}},
            {"id": "trace_section_constructor", "config": {}},
            {"id": "forced_bisection_constructor", "config": {}},
            {"id": "square_condition_specializer", "config": {}},
        ],
        run_config={},
    )
    a = upsert_pipeline_candidate(
        db,
        run_id=run_id,
        provider_key="demo:v1",
        parameter="path-a",
        curve_id=curve_id,
        status="running",
        stage_results={
            "1": {
                "stage_id": "section_height_shell",
                "result": {"sections": [{"id": "section-a"}]},
            },
            "2": {
                "stage_id": "trace_section_constructor",
                "result": {"trace_sections": [{"id": "trace-a"}]},
            },
            "3": {
                "stage_id": "forced_bisection_constructor",
                "result": {"conditions": [{"id": "condition-a"}]},
            },
        },
    )
    b = upsert_pipeline_candidate(
        db,
        run_id=run_id,
        provider_key="demo:v1",
        parameter="path-b",
        curve_id=curve_id,
        status="running",
        stage_results={
            "1": {
                "stage_id": "section_height_shell",
                "result": {"sections": [{"id": "section-b"}]},
            },
            "2": {
                "stage_id": "trace_section_constructor",
                "result": {"trace_sections": [{"id": "trace-b"}]},
            },
            "3": {
                "stage_id": "forced_bisection_constructor",
                "result": {"conditions": [{"id": "condition-b"}]},
            },
        },
    )
    return db, run_id, curve_id, a, b


def test_candidate_history_prefers_candidate_identity_over_shared_curve(
    tmp_path,
):
    db, run_id, curve_id, a, b = _setup(tmp_path)

    history_a = runner._candidate_stage_history(
        db,
        run_id=run_id,
        candidate_id=int(a["id"]),
        curve_id=curve_id,
        before_stage_index=4,
    )
    history_b = runner._candidate_stage_history(
        db,
        run_id=run_id,
        candidate_id=int(b["id"]),
        curve_id=curve_id,
        before_stage_index=4,
    )

    assert history_a[0][2]["sections"][0]["id"] == "section-a"
    assert history_b[0][2]["sections"][0]["id"] == "section-b"
    assert history_a[1][2]["trace_sections"][0]["id"] == "trace-a"
    assert history_b[1][2]["trace_sections"][0]["id"] == "trace-b"


def test_trace_constructor_receives_current_candidate_section_chain(
    tmp_path, monkeypatch
):
    db, run_id, curve_id, _a, b = _setup(tmp_path)
    captured = {}

    def fake_trace(*, context, config, section_shell):
        captured["section_shell"] = section_shell
        return {"status": "completed", "trace_sections": []}

    monkeypatch.setattr(
        runner, "trace_section_constructor", fake_trace
    )
    result, terminal, stop = runner._stage_result(
        "trace_section_constructor",
        db=db,
        run={"id": run_id},
        run_config={},
        target={},
        context={
            "curve_id": curve_id,
            "E": object(),
            "parameter": "path-b",
            "pipeline_candidate_id": int(b["id"]),
        },
        config={},
        stage_index=2,
    )

    assert result["status"] == "completed"
    assert terminal is None
    assert stop is False
    assert captured["section_shell"]["sections"][0]["id"] == "section-b"


def test_bisection_constructor_receives_current_candidate_trace_chain(
    tmp_path, monkeypatch
):
    db, run_id, curve_id, a, _b = _setup(tmp_path)
    captured = {}

    def fake_bisection(*, context, config, trace_state):
        captured["trace_state"] = trace_state
        return {"status": "completed", "conditions": []}

    monkeypatch.setattr(
        runner, "forced_bisection_constructor", fake_bisection
    )
    result, _terminal, _stop = runner._stage_result(
        "forced_bisection_constructor",
        db=db,
        run={"id": run_id},
        run_config={},
        target={},
        context={
            "curve_id": curve_id,
            "E": object(),
            "parameter": "path-a",
            "pipeline_candidate_id": int(a["id"]),
        },
        config={},
        stage_index=3,
    )

    assert result["status"] == "completed"
    assert captured["trace_state"]["trace_sections"][0]["id"] == "trace-a"


def test_square_specializer_population_uses_parent_candidate_chain(
    tmp_path, monkeypatch
):
    db, run_id, curve_id, _a, b = _setup(tmp_path)
    captured = {}

    parent_item = {
        "provider_key": "demo:v1",
        "candidate": {
            "parameter": "path-b",
            "curve_id": curve_id,
            "score": 0.0,
        },
        "plugin": SimpleNamespace(id="demo"),
        "variant": SimpleNamespace(id="v1"),
        "family": object(),
        "fingerprints": {},
    }

    monkeypatch.setattr(
        runner,
        "_materialize_population_context",
        lambda *args, **kwargs: {
            "curve_id": curve_id,
            "E": object(),
            "source_E": object(),
            "family": object(),
            "plugin": SimpleNamespace(id="demo"),
            "variant": SimpleNamespace(id="v1"),
            "parameter": "path-b",
            "score": 0.0,
            "rank_order": 1,
            "pipeline_candidate_id": int(b["id"]),
        },
    )

    def derive(stage_id, db_arg, *, context, config):
        captured.update(context["constructive_state"])
        return {
            "status": "completed",
            "transform_kind": stage_id,
            "children": [],
        }

    monkeypatch.setattr(runner, "derive_transform_children", derive)
    monkeypatch.setattr(
        runner,
        "_snapshot",
        lambda db_arg, cid: {
            "rigorous_lower": 0,
            "rigorous_upper": None,
            "exact_rank": None,
        },
    )

    out = runner._transform_population(
        db,
        run={"id": run_id},
        run_config={},
        active_items=[parent_item],
        stage_index=4,
        stage_id="square_condition_specializer",
        config={"include_parent": True, "max_children": 8},
        cache={},
    )

    assert len(out) == 1
    assert captured["section_height_shell"]["sections"][0]["id"] == (
        "section-b"
    )
    assert captured["trace_section_constructor"][
        "trace_sections"
    ][0]["id"] == "trace-b"
    assert captured["forced_bisection_constructor"][
        "conditions"
    ][0]["id"] == "condition-b"

    row = get_pipeline_candidate(db, run_id, "demo:v1", "path-b")
    assert row is not None


def test_constructive_lookup_without_candidate_id_never_falls_back_to_curve(
    tmp_path,
):
    db, run_id, curve_id, _a, _b = _setup(tmp_path)
    result = runner._constructive_prior_stage_result(
        db,
        run_id=run_id,
        context={"curve_id": curve_id},
        before_stage_index=4,
        stage_id="section_height_shell",
    )
    assert result is None
