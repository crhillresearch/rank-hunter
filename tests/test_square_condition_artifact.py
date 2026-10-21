from types import SimpleNamespace

from rank42 import pipeline_transforms as transforms
from rank42.constructive_artifact import (
    DIVISION_SCHEMA,
    SQUARE_SPECIALIZATION_SCHEMA,
    bind_artifact_hash,
    register_square_specialization_verifier,
    unregister_square_specialization_verifier,
)


def _context():
    return {
        "pipeline_run_id": 77,
        "pipeline_candidate_id": 123,
        "plugin": SimpleNamespace(id="demo", adapter_path="adapter.py"),
        "variant": SimpleNamespace(id="v1", family_spec="json:demo"),
        "parameter": "1/2",
        "curve_id": 7,
        "E": SimpleNamespace(a_invariants=lambda: [0, 0, 0, 1, 1]),
    }


def _condition():
    return bind_artifact_hash({
        "id": "c1",
        "schema": DIVISION_SCHEMA,
        "artifact_kind": "division_condition",
        "kind": "bisection_condition",
        "exact_construction": True,
        "relation": {
            "kind": "division",
            "division": 2,
            "slope_mode": "forced_tangent",
            "condition": "fixture_F(T)",
        },
    })


def _square_artifact(condition, *, verifier="fixture_square", value="9/4", root="3/2", source_hash=None):
    return {
        "id": "sq1",
        "schema": SQUARE_SPECIALIZATION_SCHEMA,
        "artifact_kind": "square_specialization",
        "family": {
            "plugin_id": "demo",
            "variant_id": "v1",
            "family_spec": "json:demo",
        },
        "lineage": {
            "pipeline_run_id": 77,
            "pipeline_candidate_id": 123,
        },
        "source_condition": {
            "artifact_id": condition["id"],
            "artifact_hash": (
                condition["artifact_hash"]
                if source_hash is None
                else source_hash
            ),
        },
        "specialization": {"parameter": "2/3"},
        "square": {"value": value, "root": root},
        "verifier": verifier,
        "constructed_points": [["0", "1"]],
    }


def _run(monkeypatch, payload, condition, *, include_coverage=True):
    returned = payload
    if isinstance(payload, dict) and include_coverage:
        returned = {
            "tests_completed": 12,
            "domain_completed": {
                "numerator_abs": 500,
                "denominator_max": 500,
                "note": "fixture coverage",
            },
            **payload,
        }

    def isolated(plugin, hook_name, request, timeout):
        assert hook_name == "derive_pipeline_square_specializations"
        assert request["pipeline_run_id"] == 77
        assert request["pipeline_candidate_id"] == 123
        assert request["max_tests"] == 200000
        assert timeout == 7
        return returned

    monkeypatch.setattr(
        transforms, "run_isolated_plugin_hook", isolated
    )
    return transforms.square_condition_specializer_children(
        object(),
        context={
            **_context(),
            "constructive_state": {
                "forced_bisection_constructor": {
                    "conditions": [condition]
                }
            },
        },
        config={"max_children": 10, "timeout": 7},
    )


def test_square_specialization_verifies_condition_value_and_square(monkeypatch):
    condition = _condition()
    register_square_specialization_verifier(
        "fixture_square",
        lambda **kwargs: {
            "verified": True,
            "condition_value": "9/4",
            "method": "fixture_exact_condition_evaluation",
        },
    )
    try:
        result = _run(
            monkeypatch,
            {
                "status": "completed",
                "children": [_square_artifact(condition)],
            },
            condition,
        )
    finally:
        unregister_square_specialization_verifier("fixture_square")

    assert result["status"] == "completed"
    assert len(result["children"]) == 1
    child = result["children"][0]
    assert child["parameter"] == "2/3"
    assert child["constructed_points"] == [["0", "1"]]
    meta = child["metadata"]
    assert meta["exact_square_verified"] is True
    assert meta["exact_condition_evaluation_verified"] is True
    assert meta["condition_id"] == "c1"
    assert meta["condition_artifact_hash"] == condition["artifact_hash"]
    assert meta["condition_value"] == "9/4"
    assert meta["square_value"] == "9/4"
    assert meta["square_root"] == "3/2"


def test_perfect_square_is_rejected_when_condition_evaluates_differently(monkeypatch):
    condition = _condition()
    register_square_specialization_verifier(
        "fixture_square",
        lambda **kwargs: {
            "verified": True,
            "condition_value": "1",
        },
    )
    try:
        result = _run(
            monkeypatch,
            {
                "status": "completed",
                "children": [_square_artifact(condition)],
            },
            condition,
        )
    finally:
        unregister_square_specialization_verifier("fixture_square")

    assert result["status"] == "error"
    assert result["children"] == []
    assert result["rejected_condition_bindings"] == 1
    assert result["rejected_square_witnesses"] == 0
    assert "does not equal square value" in (
        result["verification_failure_samples"][0]["error"]
    )


def test_wrong_parent_condition_hash_is_rejected(monkeypatch):
    condition = _condition()
    register_square_specialization_verifier(
        "fixture_square",
        lambda **kwargs: {
            "verified": True,
            "condition_value": "9/4",
        },
    )
    try:
        result = _run(
            monkeypatch,
            {
                "status": "completed",
                "children": [
                    _square_artifact(condition, source_hash="wrong")
                ],
            },
            condition,
        )
    finally:
        unregister_square_specialization_verifier("fixture_square")

    assert result["status"] == "error"
    assert result["children"] == []
    assert "source condition hash mismatch" in (
        result["verification_failure_samples"][0]["error"]
    )


def test_false_square_root_is_rejected_before_condition_acceptance(monkeypatch):
    condition = _condition()
    register_square_specialization_verifier(
        "fixture_square",
        lambda **kwargs: {
            "verified": True,
            "condition_value": "2",
        },
    )
    try:
        result = _run(
            monkeypatch,
            {
                "status": "completed",
                "children": [
                    _square_artifact(
                        condition,
                        value="2",
                        root="1",
                    )
                ],
            },
            condition,
        )
    finally:
        unregister_square_specialization_verifier("fixture_square")

    assert result["status"] == "error"
    assert result["children"] == []
    assert result["rejected_square_witnesses"] == 1


def test_square_plugin_status_reason_and_error_are_preserved(monkeypatch):
    condition = _condition()
    for status in ("partial", "timeout", "inconclusive", "error"):
        result = _run(
            monkeypatch,
            {
                "status": status,
                "reason": f"fixture-{status}",
                "error": (
                    "fixture-error" if status == "error" else None
                ),
                "children": [],
            },
            condition,
        )
        assert result["status"] == status
        assert result["reason"] == f"fixture-{status}"
        assert result["plugin_status_preserved"] is True
        if status == "error":
            assert result["error"] == "fixture-error"


def test_legacy_bare_square_record_cannot_fan_out_as_exact_child(monkeypatch):
    condition = _condition()
    result = _run(
        monkeypatch,
        {
            "status": "completed",
            "children": [{
                "parameter": "2/3",
                "condition_id": "c1",
                "square_value": "9/4",
                "square_root": "3/2",
            }],
        },
        condition,
    )

    assert result["status"] == "error"
    assert result["children"] == []
    assert result["rejected_condition_bindings"] == 1
    assert "unsupported square specialization artifact schema" in (
        result["verification_failure_samples"][0]["error"]
    )



def test_square_completed_result_requires_explicit_search_coverage(monkeypatch):
    condition = _condition()
    result = _run(
        monkeypatch,
        {"status": "completed", "children": []},
        condition,
        include_coverage=False,
    )
    assert result["status"] == "inconclusive"
    assert result["reason"] == "square_search_coverage_unreported"
    assert result["tests_requested"] == 200000
    assert result["tests_completed"] is None
    assert result["domain_requested"] == {
        "numerator_abs": 500,
        "denominator_max": 500,
    }
    assert result["domain_completed"] is None
    assert result["coverage_reported"] is False
    assert result["hard_isolated"] is True
    assert result["timeout_seconds"] == 7


def test_square_children_do_not_inherit_producer_score(monkeypatch):
    condition = _condition()
    artifact = _square_artifact(condition)
    artifact["score"] = 99.25
    register_square_specialization_verifier(
        "fixture_square",
        lambda **kwargs: {
            "verified": True,
            "condition_value": "9/4",
        },
    )
    try:
        result = _run(
            monkeypatch,
            {"status": "completed", "children": [artifact]},
            condition,
        )
    finally:
        unregister_square_specialization_verifier("fixture_square")

    assert result["status"] == "completed"
    child = result["children"][0]
    assert child["score"] is None
    assert child["metadata"]["score_inherited"] is False
    assert child["metadata"]["producer_score_discarded"] is True


def test_square_hook_hard_timeout_kills_sleeping_plugin(tmp_path):
    adapter = tmp_path / "slow_square.py"
    adapter.write_text(
        "import time\n"
        "def derive_pipeline_square_specializations(payload):\n"
        "    time.sleep(10)\n"
        "    return {'status':'completed','tests_completed':1,"
        "'domain_completed':{},'children':[]}\n",
        encoding="utf-8",
    )
    condition = _condition()
    context = {
        **_context(),
        "plugin": SimpleNamespace(
            id="demo",
            version="1.0",
            adapter_path=adapter,
        ),
        "constructive_state": {
            "forced_bisection_constructor": {
                "conditions": [condition]
            }
        },
    }
    result = transforms.square_condition_specializer_children(
        object(),
        context=context,
        config={
            "numerator_abs": 25,
            "denominator_max": 30,
            "max_tests": 100,
            "max_children": 4,
            "timeout": 1,
        },
    )
    assert result["status"] == "timeout"
    assert result["children"] == []
    assert result["hard_isolated"] is True
    assert result["timeout_seconds"] == 1
    assert result["tests_requested"] == 100
    assert result["tests_completed"] is None
    assert result["domain_requested"] == {
        "numerator_abs": 25,
        "denominator_max": 30,
    }
