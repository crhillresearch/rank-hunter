from types import SimpleNamespace

from rank42 import constructive_family as constructive
from rank42.constructive_artifact import (
    DIVISION_SCHEMA,
    TRACE_SCHEMA,
    bind_artifact_hash,
    register_division_verifier,
    register_trace_verifier,
    unregister_division_verifier,
    unregister_trace_verifier,
)


def _context():
    return {
        "pipeline_run_id": 77,
        "pipeline_candidate_id": 123,
        "plugin": SimpleNamespace(id="demo", adapter_path="adapter.py"),
        "variant": SimpleNamespace(id="v1", family_spec="json:demo"),
        "parameter": "5/7",
        "curve_id": 11,
        "E": SimpleNamespace(a_invariants=lambda: [0, 0, 0, 1, 1]),
    }


def _patch_adapter(monkeypatch, adapter):
    def isolated(plugin, hook_name, payload, timeout):
        hook = getattr(adapter, hook_name, None)
        if not callable(hook):
            return {
                "status": "unsupported",
                "reason": f"plugin adapter does not define {hook_name}",
                "_isolated_hook": {
                    "runtime_seconds": 0.001,
                    "worker_exit_code": 0,
                },
            }
        raw = hook(payload)
        if isinstance(raw, dict):
            result = dict(raw)
            result.setdefault("status", "completed")
        elif isinstance(raw, (list, tuple)):
            key = {
                "derive_pipeline_section_shell": "sections",
                "derive_pipeline_trace_sections": "trace_sections",
                "derive_pipeline_bisection_conditions": "conditions",
            }[hook_name]
            result = {
                "status": "completed",
                key: list(raw),
                "legacy_list_result": True,
            }
        elif raw is None:
            result = {
                "status": "inconclusive",
                "reason": f"{hook_name} returned no result",
            }
        else:
            return {
                "status": "error",
                "reason": "fixture hook returned malformed result",
                "error": repr(raw),
            }
        result["_isolated_hook"] = {
            "runtime_seconds": 0.001,
            "worker_exit_code": 0,
        }
        return result

    monkeypatch.setattr(
        constructive, "run_isolated_plugin_hook", isolated
    )


def _exact_section():
    return bind_artifact_hash({
        "id": "s1",
        "schema": "rank42.constructive_section.v1",
        "kind": "section",
        "artifact_kind": "section",
        "exact_construction": True,
        "section": {
            "parameter_variable": "T",
            "x": "T",
            "y": "T+1",
        },
    })


def _exact_trace():
    return bind_artifact_hash({
        "id": "tr1",
        "schema": TRACE_SCHEMA,
        "kind": "trace_section",
        "artifact_kind": "trace_section",
        "exact_construction": True,
        "trace": {
            "parameter_variable": "T",
            "x": "2*T",
            "y": "2*T+2",
            "relation": "sum_of_conjugates",
            "base_field": "QQ",
        },
    })


def _trace_artifact(source, *, verifier=None, candidate_id=123):
    artifact = {
        "id": "tr1",
        "schema": TRACE_SCHEMA,
        "artifact_kind": "trace_section",
        "family": {
            "plugin_id": "demo",
            "variant_id": "v1",
            "family_spec": "json:demo",
        },
        "lineage": {
            "pipeline_run_id": 77,
            "pipeline_candidate_id": candidate_id,
        },
        "source_section": {
            "artifact_id": source["id"],
            "artifact_hash": source["artifact_hash"],
        },
        "extension": {
            "degree": 2,
            "field": "QQ(a), a^2-5",
            "conjugates": ["identity", "a->-a"],
        },
        "trace": {
            "parameter_variable": "T",
            "x": "2*T",
            "y": "2*T+2",
            "relation": "sum_of_conjugates",
            "base_field": "QQ",
        },
        "implementation": {
            "plugin_id": "demo",
            "implementation_version": "fixture-1",
        },
    }
    if verifier is not None:
        artifact["verifier"] = verifier
    return artifact


def _division_artifact(source, *, verifier=None, source_hash=None):
    artifact = {
        "id": "d1",
        "schema": DIVISION_SCHEMA,
        "artifact_kind": "division_condition",
        "family": {
            "plugin_id": "demo",
            "variant_id": "v1",
            "family_spec": "json:demo",
        },
        "lineage": {
            "pipeline_run_id": 77,
            "pipeline_candidate_id": 123,
        },
        "source_trace": {
            "artifact_id": source["id"],
            "artifact_hash": (
                source["artifact_hash"]
                if source_hash is None
                else source_hash
            ),
        },
        "relation": {
            "kind": "division",
            "division": 2,
            "slope_mode": "forced_tangent",
            "equation": "2*P=Q",
            "condition": "U^2-(T^2+1)",
        },
        "implementation": {
            "plugin_id": "demo",
            "implementation_version": "fixture-1",
        },
    }
    if verifier is not None:
        artifact["verifier"] = verifier
    return artifact


def test_legacy_trace_cannot_self_assert_exactness(monkeypatch):
    source = _exact_section()

    class Adapter:
        @staticmethod
        def derive_pipeline_trace_sections(request):
            return {
                "trace_sections": [{
                    "id": "legacy-trace",
                    "exact_construction": True,
                }]
            }

    _patch_adapter(monkeypatch, Adapter)
    result = constructive.trace_section_constructor(
        context=_context(),
        config={"extension_degree": 2},
        section_shell={"sections": [source]},
    )
    trace = result["trace_sections"][0]
    assert trace["producer_exact_construction"] is True
    assert trace["exact_construction"] is False
    assert trace["verification_status"] == "unverified"
    assert trace["verification"]["reason"] == (
        "legacy_or_untyped_trace_artifact"
    )


def test_registered_trace_verifier_upgrades_parent_bound_trace(monkeypatch):
    source = _exact_section()
    name = "fixture_trace"

    class Adapter:
        @staticmethod
        def derive_pipeline_trace_sections(request):
            return {
                "trace_sections": [
                    _trace_artifact(source, verifier=name)
                ]
            }

    register_trace_verifier(
        name,
        lambda **kwargs: {
            "verified": True,
            "method": "fixture_exact_galois_trace",
            "source_hash": kwargs["source_section"]["artifact_hash"],
        },
    )
    _patch_adapter(monkeypatch, Adapter)
    try:
        result = constructive.trace_section_constructor(
            context=_context(),
            config={"extension_degree": 2},
            section_shell={"sections": [source]},
        )
    finally:
        unregister_trace_verifier(name)

    trace = result["trace_sections"][0]
    assert trace["exact_construction"] is True
    assert trace["verification_status"] == "verified"
    assert trace["verification"]["source_section_hash"] == (
        source["artifact_hash"]
    )
    assert trace["verification"]["lineage"]["pipeline_candidate_id"] == 123


def test_trace_wrong_candidate_lineage_is_explicit_error(monkeypatch):
    source = _exact_section()

    class Adapter:
        @staticmethod
        def derive_pipeline_trace_sections(request):
            return {
                "trace_sections": [
                    _trace_artifact(source, candidate_id=999)
                ]
            }

    _patch_adapter(monkeypatch, Adapter)
    result = constructive.trace_section_constructor(
        context=_context(),
        config={"extension_degree": 2},
        section_shell={"sections": [source]},
    )
    assert result["status"] == "error"
    assert result["trace_sections"] == []
    assert "pipeline_candidate_id mismatch" in (
        result["trace_failure_samples"][0]["error"]
    )


def test_trace_requires_complete_conjugate_provenance(monkeypatch):
    source = _exact_section()
    artifact = _trace_artifact(source)
    artifact["extension"]["conjugates"] = ["identity"]

    class Adapter:
        @staticmethod
        def derive_pipeline_trace_sections(request):
            return {"trace_sections": [artifact]}

    _patch_adapter(monkeypatch, Adapter)
    result = constructive.trace_section_constructor(
        context=_context(),
        config={"extension_degree": 2},
        section_shell={"sections": [source]},
    )
    assert result["status"] == "error"
    assert "exactly extension degree entries" in (
        result["trace_failure_samples"][0]["error"]
    )


def test_legacy_division_condition_cannot_self_assert_exactness(monkeypatch):
    source = _exact_trace()

    class Adapter:
        @staticmethod
        def derive_pipeline_bisection_conditions(request):
            return {
                "conditions": [{
                    "id": "legacy-condition",
                    "exact_construction": True,
                }]
            }

    _patch_adapter(monkeypatch, Adapter)
    result = constructive.forced_bisection_constructor(
        context=_context(),
        config={"division": 2, "slope_mode": "forced_tangent"},
        trace_state={"trace_sections": [source]},
    )
    condition = result["conditions"][0]
    assert condition["producer_exact_construction"] is True
    assert condition["exact_construction"] is False
    assert condition["verification_status"] == "unverified"


def test_registered_division_verifier_upgrades_parent_bound_condition(
    monkeypatch,
):
    source = _exact_trace()
    name = "fixture_division"

    class Adapter:
        @staticmethod
        def derive_pipeline_bisection_conditions(request):
            return {
                "conditions": [
                    _division_artifact(source, verifier=name)
                ]
            }

    register_division_verifier(
        name,
        lambda **kwargs: {
            "verified": True,
            "method": "fixture_exact_division_identity",
            "equation": kwargs["relation"]["equation"],
        },
    )
    _patch_adapter(monkeypatch, Adapter)
    try:
        result = constructive.forced_bisection_constructor(
            context=_context(),
            config={"division": 2, "slope_mode": "forced_tangent"},
            trace_state={"trace_sections": [source]},
        )
    finally:
        unregister_division_verifier(name)

    condition = result["conditions"][0]
    assert condition["exact_construction"] is True
    assert condition["verification_status"] == "verified"
    assert condition["verification"]["source_trace_hash"] == (
        source["artifact_hash"]
    )
    assert condition["verification"]["relation"]["division"] == 2


def test_division_wrong_parent_hash_is_explicit_error(monkeypatch):
    source = _exact_trace()

    class Adapter:
        @staticmethod
        def derive_pipeline_bisection_conditions(request):
            return {
                "conditions": [
                    _division_artifact(source, source_hash="wrong")
                ]
            }

    _patch_adapter(monkeypatch, Adapter)
    result = constructive.forced_bisection_constructor(
        context=_context(),
        config={"division": 2, "slope_mode": "forced_tangent"},
        trace_state={"trace_sections": [source]},
    )
    assert result["status"] == "error"
    assert result["conditions"] == []
    assert "source trace hash mismatch" in (
        result["condition_failure_samples"][0]["error"]
    )


def test_unsupported_division_and_slope_modes_stop_before_plugin(monkeypatch):
    source = _exact_trace()
    called = []

    class Adapter:
        @staticmethod
        def derive_pipeline_bisection_conditions(request):
            called.append(request)
            return {"conditions": []}

    _patch_adapter(monkeypatch, Adapter)
    bad_division = constructive.forced_bisection_constructor(
        context=_context(),
        config={"division": 5, "slope_mode": "forced_tangent"},
        trace_state={"trace_sections": [source]},
    )
    bad_mode = constructive.forced_bisection_constructor(
        context=_context(),
        config={"division": 2, "slope_mode": "mystery"},
        trace_state={"trace_sections": [source]},
    )
    assert bad_division["status"] == "error"
    assert bad_division["reason"] == "unsupported_division"
    assert bad_mode["status"] == "error"
    assert bad_mode["reason"] == "unsupported_slope_mode"
    assert called == []


def test_typed_trace_without_verifier_remains_unverified(monkeypatch):
    source = _exact_section()

    class Adapter:
        @staticmethod
        def derive_pipeline_trace_sections(request):
            return {"trace_sections": [_trace_artifact(source)]}

    _patch_adapter(monkeypatch, Adapter)
    result = constructive.trace_section_constructor(
        context=_context(),
        config={"extension_degree": 2},
        section_shell={"sections": [source]},
    )
    trace = result["trace_sections"][0]
    assert trace["exact_construction"] is False
    assert trace["verification"]["reason"] == (
        "no_registered_trace_verifier"
    )


def test_typed_division_without_verifier_remains_unverified(monkeypatch):
    source = _exact_trace()

    class Adapter:
        @staticmethod
        def derive_pipeline_bisection_conditions(request):
            return {"conditions": [_division_artifact(source)]}

    _patch_adapter(monkeypatch, Adapter)
    result = constructive.forced_bisection_constructor(
        context=_context(),
        config={"division": 2, "slope_mode": "forced_tangent"},
        trace_state={"trace_sections": [source]},
    )
    condition = result["conditions"][0]
    assert condition["exact_construction"] is False
    assert condition["verification"]["reason"] == (
        "no_registered_division_verifier"
    )
