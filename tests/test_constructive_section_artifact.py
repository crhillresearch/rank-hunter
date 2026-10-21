from types import SimpleNamespace

from rank42 import constructive_family as constructive
from rank42.constructive_artifact import (
    SECTION_SCHEMA,
    register_section_verifier,
    unregister_section_verifier,
)


def _context():
    return {
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


def _typed_section(*, verifier=None, height_kind="family_enumeration_shell"):
    artifact = {
        "id": "s1",
        "schema": SECTION_SCHEMA,
        "artifact_kind": "section",
        "family": {
            "plugin_id": "demo",
            "variant_id": "v1",
            "family_spec": "json:demo",
        },
        "section": {
            "parameter_variable": "T",
            "x": "(T^2+1)/(T+1)",
            "y": "(T^3+2)/(T+1)^2",
        },
        "height": {
            "kind": height_kind,
            "value": 10,
            "mode": "exact",
            "normalization": "family_defined",
            "coefficient_bound": 8,
        },
        "implementation": {
            "plugin_id": "demo",
            "implementation_version": "fixture-1",
        },
    }
    if verifier is not None:
        artifact["verifier"] = verifier
    return artifact


def _run(monkeypatch, record):
    class Adapter:
        @staticmethod
        def derive_pipeline_section_shell(request):
            assert request["height"] == 10
            assert request["height_kind"] == "family_enumeration_shell"
            assert request["height_normalization"] == "family_defined"
            return {"status": "completed", "sections": [record]}

    _patch_adapter(monkeypatch, Adapter)
    return constructive.section_height_shell(
        context=_context(),
        config={
            "height": 10,
            "height_mode": "exact",
            "height_kind": "family_enumeration_shell",
            "height_normalization": "family_defined",
            "coefficient_bound": 8,
        },
    )


def test_legacy_section_cannot_self_assert_exactness(monkeypatch):
    result = _run(
        monkeypatch,
        {
            "id": "legacy",
            "height": 10,
            "exact_construction": True,
        },
    )
    assert result["status"] == "completed"
    section = result["sections"][0]
    assert section["producer_exact_construction"] is True
    assert section["exact_construction"] is False
    assert section["verification_status"] == "unverified"
    assert section["verification"]["reason"] == (
        "legacy_or_untyped_section_artifact"
    )


def test_typed_section_without_verifier_remains_unverified(monkeypatch):
    result = _run(monkeypatch, _typed_section())
    assert result["status"] == "completed"
    section = result["sections"][0]
    assert section["exact_construction"] is False
    assert section["verification_status"] == "unverified"
    assert section["verification"]["schema"] == SECTION_SCHEMA
    assert section["verification"]["height"]["kind"] == (
        "family_enumeration_shell"
    )
    assert section["verification"]["height"]["normalization"] == (
        "family_defined"
    )


def test_registered_section_verifier_upgrades_exact_construction(monkeypatch):
    name = "fixture_section_identity"
    register_section_verifier(
        name,
        lambda **kwargs: {
            "verified": True,
            "method": "fixture_exact_symbolic_substitution",
            "height_kind": kwargs["height"]["kind"],
        },
    )
    try:
        result = _run(monkeypatch, _typed_section(verifier=name))
    finally:
        unregister_section_verifier(name)

    section = result["sections"][0]
    assert section["exact_construction"] is True
    assert section["verification_status"] == "verified"
    assert section["verification"]["verified"] is True
    assert section["verification"]["verifier_result"]["method"] == (
        "fixture_exact_symbolic_substitution"
    )


def test_typed_section_height_contract_mismatch_is_explicit_error(monkeypatch):
    result = _run(
        monkeypatch,
        _typed_section(height_kind="coefficient_height"),
    )
    assert result["status"] == "error"
    assert result["sections"] == []
    assert result["section_failures"] == 1
    failure = result["section_failure_samples"][0]
    assert failure["reason"] == "section_artifact_verification_error"
    assert "height kind mismatch" in failure["error"]


def test_typed_section_requires_exact_symbolic_coordinates(monkeypatch):
    artifact = _typed_section()
    artifact["section"] = {"parameter_variable": "T", "x": "T"}
    result = _run(monkeypatch, artifact)
    assert result["status"] == "error"
    assert result["sections"] == []
    assert "exact x/y formulas are required" in (
        result["section_failure_samples"][0]["error"]
    )


def test_typed_section_family_binding_is_enforced(monkeypatch):
    artifact = _typed_section()
    artifact["family"]["plugin_id"] = "other"
    result = _run(monkeypatch, artifact)
    assert result["status"] == "error"
    assert "family plugin_id mismatch" in (
        result["section_failure_samples"][0]["error"]
    )
