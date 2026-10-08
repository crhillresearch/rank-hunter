from types import SimpleNamespace

from sage.all import EllipticCurve, QQ

from rank42 import pipeline_transforms as transforms
from rank42.db import connect, get_curve, upsert_curve
from rank42.pipeline_state import (
    create_pipeline_run,
    ensure_pipeline_schema,
    pipeline_strategy_steps,
    upsert_pipeline_candidate,
)


def test_squarefree_twist_values_filters_zero_one_squares_and_duplicates():
    values = transforms.squarefree_twist_values({
        "d_values": [0, 1, -1, 2, 4, -5, -5, 12, 13],
    })
    assert values == [-1, 2, -5, 13]


def test_parameter_pullback_applies_exact_mobius_maps():
    class Family:
        def curve(self, value):
            return object()

    result = transforms.parameter_pullback_children(
        context={
            "family": Family(),
            "parameter": "2",
            "score": 7.5,
        },
        config={
            "maps": [
                [1, 1, 0, 1],
                [1, -1, 0, 1],
                [1, 0, 1, 1],
            ]
        },
    )
    assert result["status"] == "completed"
    assert [child["parameter"] for child in result["children"]] == ["3", "1", "2/3"]
    assert all(child["kind"] == "family_parameter" for child in result["children"])
    assert all(child["score"] is None for child in result["children"])


def test_mobius_parameter_transform_rejects_zero_determinant_at_runtime():
    class Family:
        def curve(self, value):
            return object()

    result = transforms.parameter_pullback_children(
        context={
            "family": Family(),
            "parameter": "2",
            "score": 9.0,
        },
        config={"maps": [[1, 2, 2, 4]]},
    )
    assert result["status"] == "error"
    assert result["attempted"] == 1
    assert result["succeeded"] == 0
    assert result["failed"] == 1
    assert "nonzero determinant" in result["failure_samples"][0]["error"]


def test_quadratic_twist_all_construction_failures_are_not_completed(tmp_path):
    db = connect(tmp_path / "fail.db")
    parent_id = upsert_curve(
        db,
        family="test",
        parameter="parent",
        a_invariants_json='["0","0","0","-1","0"]',
    )

    class BrokenCurve:
        def quadratic_twist(self, d):
            raise RuntimeError(f"cannot twist {d}")

    result = transforms.quadratic_twist_children(
        db,
        context={
            "curve_id": parent_id,
            "E": BrokenCurve(),
            "score": 50.0,
        },
        config={"d_values": [-1, 2], "max_children": 2},
    )
    assert result["status"] == "error"
    assert result["attempted"] == 2
    assert result["succeeded"] == 0
    assert result["failed"] == 2
    assert len(result["failure_samples"]) == 2


def test_rank_jump_base_change_uses_family_adapter_hook(monkeypatch):
    plugin = SimpleNamespace(id="demo", adapter_path="adapter.py")
    variant = SimpleNamespace(id="v1", family_spec="json:demo")

    def isolated(plugin_arg, hook_name, context, timeout):
        assert plugin_arg is plugin
        assert hook_name == "derive_pipeline_transform"
        assert context["transform_kind"] == "rank_jump_base_change"
        assert context["parameter"] == "5/7"
        assert timeout == 30
        return {
            "children": [
                {
                    "parameter": "11/13",
                    "score": 12.5,
                    "metadata": {"map": "published-base-change"},
                }
            ],
            "_isolated_hook": {"runtime_seconds": 0.01},
        }

    monkeypatch.setattr(transforms, "run_isolated_plugin_hook", isolated)
    result = transforms.plugin_base_change_children(
        object(),
        context={
            "plugin": plugin,
            "variant": variant,
            "parameter": "5/7",
            "curve_id": 42,
            "E": SimpleNamespace(a_invariants=lambda: [0, 0, 0, 1, 1]),
            "score": 8.0,
        },
        config={"max_children": 8},
    )
    assert result["status"] == "completed"
    assert result["adapter_plugin_id"] == "demo"
    child = result["children"][0]
    assert child["kind"] == "family_parameter"
    assert child["parameter"] == "11/13"
    assert child["metadata"]["map"] == "published-base-change"
    assert child["metadata"]["rank_inherited"] is False


def test_rank_jump_plugin_inconclusive_status_and_reason_survive(monkeypatch):
    plugin = SimpleNamespace(id="demo", adapter_path="adapter.py")
    variant = SimpleNamespace(id="v1", family_spec="json:demo")

    monkeypatch.setattr(
        transforms,
        "run_isolated_plugin_hook",
        lambda plugin, hook_name, payload, timeout: {
            "status": "inconclusive",
            "reason": "no exact lift found",
            "children": [],
            "_isolated_hook": {"runtime_seconds": 0.01},
        },
    )
    result = transforms.plugin_base_change_children(
        object(),
        context={
            "plugin": plugin,
            "variant": variant,
            "parameter": "1",
            "curve_id": 1,
            "E": SimpleNamespace(a_invariants=lambda: [0, 0, 0, 1, 1]),
            "score": 0.0,
        },
        config={},
    )
    assert result["status"] == "inconclusive"
    assert result["reason"] == "no exact lift found"
    assert result["children"] == []
    assert result["plugin_status_preserved"] is True


def test_surface_plugin_error_status_and_error_survive(monkeypatch):
    plugin = SimpleNamespace(id="surface", adapter_path="adapter.py")
    variant = SimpleNamespace(id="v1", family_spec="json:surface")

    monkeypatch.setattr(
        transforms,
        "run_isolated_plugin_hook",
        lambda plugin, hook_name, payload, timeout: {
            "status": "error",
            "reason": "surface map failed",
            "error": "fixture failure",
            "children": [],
            "_isolated_hook": {"runtime_seconds": 0.01},
        },
    )
    result = transforms.surface_fibration_switch_children(
        object(),
        context={
            "plugin": plugin,
            "variant": variant,
            "parameter": "1",
            "curve_id": 1,
            "E": SimpleNamespace(a_invariants=lambda: [0, 0, 0, 1, 1]),
            "score": 0.0,
        },
        config={},
    )
    assert result["status"] == "error"
    assert result["reason"] == "surface map failed"
    assert result["error"] == "fixture failure"
    assert result["children"] == []


def test_plugin_transform_malformed_children_is_explicit_error(monkeypatch):
    plugin = SimpleNamespace(id="demo", adapter_path="adapter.py")
    variant = SimpleNamespace(id="v1", family_spec="json:demo")

    monkeypatch.setattr(
        transforms,
        "run_isolated_plugin_hook",
        lambda plugin, hook_name, payload, timeout: {
            "status": "completed",
            "children": "not-a-list",
            "_isolated_hook": {"runtime_seconds": 0.01},
        },
    )
    result = transforms.plugin_base_change_children(
        object(),
        context={
            "plugin": plugin,
            "variant": variant,
            "parameter": "1",
            "curve_id": 1,
            "E": SimpleNamespace(a_invariants=lambda: [0, 0, 0, 1, 1]),
            "score": 0.0,
        },
        config={},
    )
    assert result["status"] == "error"
    assert "children must be a list" in result["reason"]
    assert result["children"] == []


def test_rank_jump_base_change_without_hook_is_safe_skip(monkeypatch):
    plugin = SimpleNamespace(id="demo", adapter_path=None)
    variant = SimpleNamespace(id="v1", family_spec="json:demo")
    result = transforms.plugin_base_change_children(
        object(),
        context={
            "plugin": plugin,
            "variant": variant,
            "parameter": "1",
            "curve_id": 1,
            "E": SimpleNamespace(a_invariants=lambda: [0, 0, 0, 1, 1]),
            "score": 0.0,
        },
        config={},
    )
    assert result["status"] == "skipped"
    assert result["children"] == []



def test_quadratic_twist_reuses_q_isomorphic_parent_curve(tmp_path):
    db = connect(tmp_path / "rank42.db")
    E = EllipticCurve(QQ, [0, 0, 0, -1, 0])
    parent_id = upsert_curve(
        db,
        family="test",
        parameter="parent",
        a_invariants_json='["0","0","0","-1","0"]',
        status="test",
    )
    result = transforms.quadratic_twist_children(
        db,
        context={
            "curve_id": parent_id,
            "E": E,
            "score": 3.5,
        },
        config={"d_values": [-1], "max_children": 1},
    )
    assert result["status"] == "completed"
    assert len(result["children"]) == 1
    child = result["children"][0]
    assert child["metadata"]["twist_d"] == -1
    assert child["metadata"]["rank_inherited"] is False
    assert child["metadata"]["score_inherited"] is False
    assert child["score"] is None
    assert child["curve_id"] == parent_id
    assert child["created_by_pipeline"] is False
    assert child["metadata"]["exact_q_isomorphism_verified"] is True
    assert child["metadata"]["derivation_identity_separate"] is True
    stored = get_curve(db, child["curve_id"])
    assert stored is not None
    derived = EllipticCurve(
        QQ,
        [QQ(str(x)) for x in __import__("json").loads(stored["a_invariants_json"])],
    )
    assert derived.is_isomorphic(E.quadratic_twist(-1))


def test_quadratic_twist_persists_new_nonisomorphic_child(tmp_path):
    db = connect(tmp_path / "new-child.db")
    E = EllipticCurve(QQ, [0, 0, 0, -1, 0])
    parent_id = upsert_curve(
        db,
        family="test",
        parameter="parent",
        a_invariants_json='["0","0","0","-1","0"]',
        status="test",
    )
    twist = E.quadratic_twist(2)
    assert not twist.is_isomorphic(E)

    result = transforms.quadratic_twist_children(
        db,
        context={
            "curve_id": parent_id,
            "E": E,
            "score": 3.5,
        },
        config={"d_values": [2], "max_children": 1},
    )

    assert result["status"] == "completed"
    child = result["children"][0]
    assert child["curve_id"] != parent_id
    assert child["created_by_pipeline"] is True
    assert child["metadata"]["exact_q_isomorphism_verified"] is True
    stored = get_curve(db, child["curve_id"])
    assert stored is not None
    derived = EllipticCurve(
        QQ,
        [QQ(str(x)) for x in __import__("json").loads(stored["a_invariants_json"])],
    )
    assert derived.is_isomorphic(twist)



def test_rank_jump_base_change_can_reference_another_family(monkeypatch):
    plugin = SimpleNamespace(id="parent", adapter_path="adapter.py")
    variant = SimpleNamespace(id="v1", family_spec="json:parent")

    monkeypatch.setattr(
        transforms,
        "run_isolated_plugin_hook",
        lambda plugin, hook_name, payload, timeout: {
            "children": [{
                "plugin_id": "child_family",
                "variant_id": "cover",
                "parameter": "17/19",
                "metadata": {"exact_lift": True},
            }],
            "_isolated_hook": {"runtime_seconds": 0.01},
        },
    )
    result = transforms.plugin_base_change_children(
        object(),
        context={
            "plugin": plugin,
            "variant": variant,
            "parameter": "5/7",
            "curve_id": 42,
            "E": SimpleNamespace(a_invariants=lambda: [0, 0, 0, 1, 1]),
            "score": 8.0,
        },
        config={},
    )
    child = result["children"][0]
    assert child["kind"] == "family_reference"
    assert child["plugin_id"] == "child_family"
    assert child["variant_id"] == "cover"
    assert child["parameter"] == "17/19"
    assert child["metadata"]["exact_lift"] is True



def test_surface_fibration_switch_uses_exact_plugin_transform_hook(monkeypatch):
    plugin = SimpleNamespace(id="surface_parent", adapter_path="adapter.py")
    variant = SimpleNamespace(id="v1", family_spec="json:surface-parent")

    def isolated_surface(plugin_arg, hook_name, payload, timeout):
        assert plugin_arg is plugin
        assert hook_name == "derive_pipeline_transform"
        assert payload["transform_kind"] == "surface_fibration_switch"
        return {
            "children": [{
                "plugin_id": "alternate_fibration",
                "variant_id": "f2",
                "parameter": "23/29",
                "metadata": {"exact_surface_map": True},
            }],
            "_isolated_hook": {"runtime_seconds": 0.01},
        }

    monkeypatch.setattr(
        transforms, "run_isolated_plugin_hook", isolated_surface
    )
    result = transforms.surface_fibration_switch_children(
        object(),
        context={
            "plugin": plugin,
            "variant": variant,
            "parameter": "5/7",
            "curve_id": 42,
            "E": SimpleNamespace(a_invariants=lambda: [0, 0, 0, 1, 1]),
            "score": 8.0,
        },
        config={},
    )
    assert result["status"] == "completed"
    child = result["children"][0]
    assert child["kind"] == "family_reference"
    assert child["plugin_id"] == "alternate_fibration"
    assert child["variant_id"] == "f2"
    assert child["metadata"]["exact_surface_map"] is True
    assert child["metadata"]["rank_inherited"] is False



def test_square_condition_specializer_rejects_unbound_square_witness(monkeypatch):
    plugin = SimpleNamespace(id="demo", adapter_path="adapter.py")
    variant = SimpleNamespace(id="v1", family_spec="json:demo")

    def isolated(plugin_arg, hook_name, request, timeout):
        assert plugin_arg is plugin
        assert hook_name == "derive_pipeline_square_specializations"
        return {
            "status": "completed",
            "tests_completed": 2,
            "domain_completed": {
                "numerator_abs": request["numerator_abs"],
                "denominator_max": request["denominator_max"],
            },
            "children": [
                {
                    "parameter": "2/3",
                    "condition_id": "c1",
                    "square_value": "9/4",
                    "square_root": "3/2",
                    "constructed_points": [["0", "1"]],
                },
                {
                    "parameter": "3/4",
                    "condition_id": "c1",
                    "square_value": "2",
                    "square_root": "1",
                },
            ],
        }

    monkeypatch.setattr(
        transforms, "run_isolated_plugin_hook", isolated
    )
    result = transforms.square_condition_specializer_children(
        object(),
        context={
            "plugin": plugin,
            "variant": variant,
            "parameter": "1/2",
            "curve_id": 7,
            "E": SimpleNamespace(a_invariants=lambda: [0, 0, 0, 1, 1]),
            "constructive_state": {
                "forced_bisection_constructor": {
                    "conditions": [{
                        "id": "c1",
                        "artifact_hash": "fixture-condition-hash",
                        "exact_construction": True,
                    }]
                }
            },
        },
        config={"max_children": 10},
    )
    assert result["children"] == []
    assert result["status"] == "error"
    assert result["rejected_condition_bindings"] == 2
    assert result["exact_square_tests_verified"] == 0


def test_constructive_rank_jump_loop_walks_height_shells(monkeypatch):
    seen_heights = []

    def fake_shell(*, context, config):
        seen_heights.append(config["height"])
        return {
            "status": "completed",
            "section_count": 1,
            "sections": [{"id": f"s{config['height']}"}],
        }

    monkeypatch.setattr(transforms, "section_height_shell", fake_shell)
    monkeypatch.setattr(
        transforms,
        "trace_section_constructor",
        lambda **kwargs: {
            "status": "completed",
            "trace_count": 1,
            "trace_sections": [{"id": "tr"}],
        },
    )
    monkeypatch.setattr(
        transforms,
        "forced_bisection_constructor",
        lambda **kwargs: {
            "status": "completed",
            "condition_count": 1,
            "conditions": [{"id": "b"}],
        },
    )

    calls = {"n": 0}

    def fake_square(db, *, context, config):
        calls["n"] += 1
        height = seen_heights[-1]
        return {
            "status": "completed",
            "children": [{
                "kind": "family_parameter",
                "parameter": f"{height}/11",
                "metadata": {"exact_square_verified": True},
            }],
            "rejected_square_witnesses": 0,
        }

    monkeypatch.setattr(
        transforms, "square_condition_specializer_children", fake_square
    )
    result = transforms.constructive_rank_jump_loop_children(
        object(),
        context={"curve_id": 1, "parameter": "1", "score": 9.0},
        config={
            "heights": [6, 8, 10],
            "max_children": 10,
            "stop_on_first_success": False,
        },
    )
    assert seen_heights == [6, 8, 10]
    assert calls["n"] == 3
    assert [child["parameter"] for child in result["children"]] == [
        "6/11", "8/11", "10/11"
    ]
    assert result["children"][1]["metadata"]["strategy"] == "constructive_rank_jump_loop"
    assert result["children"][1]["metadata"]["section_height"] == 8




def test_constructive_rank_jump_loop_resumes_at_first_unfinished_shell(
    tmp_path,
    monkeypatch,
):
    db = connect(tmp_path / "constructive-loop-resume.db")
    ensure_pipeline_schema(db)
    E = EllipticCurve(QQ, [0, 0, 0, -1, 1])
    curve_id = upsert_curve(
        db,
        family="constructive-loop-fixture",
        parameter="1",
        a_invariants_json='["0","0","0","-1","1"]',
        status="test",
    )
    run_id = create_pipeline_run(
        db,
        pipeline_name="constructive loop resume",
        target_mode="family",
        target={"plugin_id": "fixture", "variant_id": "default"},
        stages=[{"id": "constructive_rank_jump_loop", "config": {}}],
    )
    candidate = upsert_pipeline_candidate(
        db,
        run_id=run_id,
        provider_key="fixture:default",
        parameter="1",
        curve_id=curve_id,
        status="running",
    )
    context = {
        "pipeline_run_id": run_id,
        "pipeline_candidate_id": int(candidate["id"]),
        "pipeline_stage_index": 1,
        "curve_id": curve_id,
        "parameter": "1",
        "E": E,
    }
    shell_calls = []

    def fake_shell(*, context, config):
        height = int(config["height"])
        shell_calls.append(height)
        return {
            "status": "completed",
            "section_count": 1,
            "requested_height": height,
            "sections": [{"id": f"s{height}"}],
        }

    monkeypatch.setattr(transforms, "section_height_shell", fake_shell)
    monkeypatch.setattr(
        transforms,
        "trace_section_constructor",
        lambda **kwargs: {
            "status": "completed",
            "trace_count": 1,
            "trace_sections": [{"id": "tr"}],
        },
    )
    monkeypatch.setattr(
        transforms,
        "forced_bisection_constructor",
        lambda **kwargs: {
            "status": "completed",
            "condition_count": 1,
            "conditions": [{"id": "b"}],
        },
    )

    state = {"timeout_height": 8}

    def fake_square(db, *, context, config):
        height = int(
            context["constructive_state"]["section_height_shell"][
                "requested_height"
            ]
        )
        if height == state["timeout_height"]:
            return {
                "status": "timeout",
                "children": [],
                "rejected_square_witnesses": 0,
            }
        return {
            "status": "completed",
            "children": [{
                "kind": "family_parameter",
                "parameter": f"{height}/11",
                "metadata": {"exact_square_verified": True},
            }],
            "rejected_square_witnesses": 0,
        }

    monkeypatch.setattr(
        transforms, "square_condition_specializer_children", fake_square
    )
    cfg = {
        "heights": [6, 8, 10],
        "max_children": 10,
        "stop_on_first_success": False,
        "retry_policy": "automatic",
    }
    first = transforms.constructive_rank_jump_loop_children(
        db,
        context=context,
        config=cfg,
    )
    assert shell_calls == [6, 8]
    assert first["status"] == "partial"
    assert first["strategy_resume_token"] == "height:2:8"
    assert [child["parameter"] for child in first["children"]] == ["6/11"]

    occurrence = first["strategy_occurrence_id"]
    rows = pipeline_strategy_steps(
        db,
        run_id,
        candidate_id=candidate["id"],
        stage_index=1,
        occurrence_id=occurrence,
    )
    assert [row["status"] for row in rows] == ["completed", "timeout"]

    state["timeout_height"] = None
    shell_calls.clear()
    second = transforms.constructive_rank_jump_loop_children(
        db,
        context=context,
        config=cfg,
    )
    assert shell_calls == [8, 10]
    assert second["status"] == "completed"
    assert second["strategy_resume_token"] is None
    assert [child["parameter"] for child in second["children"]] == [
        "6/11",
        "8/11",
        "10/11",
    ]
    assert second["rounds"][0]["resumed_from_checkpoint"] is True

    rows = pipeline_strategy_steps(
        db,
        run_id,
        candidate_id=candidate["id"],
        stage_index=1,
        occurrence_id=occurrence,
    )
    by_key = {row["step_key"]: row for row in rows}
    assert by_key["height:1:6"]["attempt_count"] == 1
    assert by_key["height:2:8"]["attempt_count"] == 2
    assert by_key["height:3:10"]["attempt_count"] == 1


def test_mobius_parameter_transform_records_pole_outcome():
    family = SimpleNamespace(curve=lambda t: EllipticCurve(QQ, [0, 0, 0, -1, 0]))
    result = transforms.parameter_pullback_children(
        context={"family": family, "parameter": "1"},
        config={"maps": [[1, 0, 1, -1]]},
    )
    assert result["status"] == "completed"
    assert result["children"] == []
    assert result["skipped"] == 1
    assert result["map_outcomes"] == [{
        "map_index": 1,
        "mobius": ["1", "0", "1", "-1"],
        "status": "skipped",
        "reason": "mobius_pole_at_parent_parameter",
    }]


def test_mobius_parameter_transform_records_family_construction_failure():
    def fail_curve(t):
        raise ValueError("fixture singular specialization")

    family = SimpleNamespace(curve=fail_curve)
    result = transforms.parameter_pullback_children(
        context={"family": family, "parameter": "1"},
        config={"maps": [[1, 1, 0, 1]]},
    )
    assert result["status"] == "error"
    assert result["children"] == []
    assert result["failed"] == 1
    outcome = result["map_outcomes"][0]
    assert outcome["status"] == "error"
    assert outcome["reason"] == "family_specialization_failed"
    assert outcome["child_parameter"] == "2"
    assert "fixture singular specialization" in outcome["error"]
