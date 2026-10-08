from types import SimpleNamespace

import pytest

from rank42 import pipeline_transforms as transforms
from rank42.plugin_transform_artifact import (
    SCHEMA,
    register_transform_verifier,
    unregister_transform_verifier,
    verify_plugin_transform_artifact,
)


def _context():
    return {
        "parameter": "5/7",
        "curve_id": 42,
        "E": SimpleNamespace(a_invariants=lambda: [0, 0, 0, 1, 1]),
    }


def _plugin_variant():
    return (
        SimpleNamespace(id="demo", adapter_path="adapter.py"),
        SimpleNamespace(id="v1", family_spec="json:demo"),
    )


def _artifact(**overrides):
    data = {
        "schema": SCHEMA,
        "status": "completed",
        "transform_kind": "rank_jump_base_change",
        "source": {
            "plugin_id": "demo",
            "variant_id": "v1",
            "parameter": "5/7",
            "a_invariants": ["0", "0", "0", "1", "1"],
        },
        "target": {
            "plugin_id": "demo",
            "variant_id": "v1",
            "parameter": "11/13",
        },
        "relation": {
            "kind": "rational_base_change",
            "data": {"parameter_map": "u"},
        },
        "implementation": {
            "plugin_id": "demo",
            "implementation_version": "fixture-1",
        },
    }
    data.update(overrides)
    return data


def test_untyped_plugin_transform_remains_plugin_asserted():
    plugin, variant = _plugin_variant()
    result = verify_plugin_transform_artifact(
        None,
        transform_kind="rank_jump_base_change",
        context=_context(),
        plugin=plugin,
        variant=variant,
        child_record={"parameter": "11/13"},
    )
    assert result["verified"] is False
    assert result["verification_status"] == "plugin_asserted"
    assert result["reason"] == "legacy_or_untyped_transform"


def test_typed_transform_binds_source_and_target_exactly():
    plugin, variant = _plugin_variant()
    result = verify_plugin_transform_artifact(
        _artifact(),
        transform_kind="rank_jump_base_change",
        context=_context(),
        plugin=plugin,
        variant=variant,
        child_record={"parameter": "11/13"},
    )
    assert result["verified"] is False
    assert result["verification_status"] == "plugin_asserted"
    assert result["relation_kind"] == "rational_base_change"

    bad = _artifact(
        source={
            "plugin_id": "demo",
            "variant_id": "v1",
            "parameter": "9/10",
            "a_invariants": ["0", "0", "0", "1", "1"],
        }
    )
    with pytest.raises(ValueError, match="source parameter mismatch"):
        verify_plugin_transform_artifact(
            bad,
            transform_kind="rank_jump_base_change",
            context=_context(),
            plugin=plugin,
            variant=variant,
            child_record={"parameter": "11/13"},
        )

    bad_target = _artifact(
        target={
            "plugin_id": "demo",
            "variant_id": "v1",
            "parameter": "3/4",
        }
    )
    with pytest.raises(ValueError, match="target parameter mismatch"):
        verify_plugin_transform_artifact(
            bad_target,
            transform_kind="rank_jump_base_change",
            context=_context(),
            plugin=plugin,
            variant=variant,
            child_record={"parameter": "11/13"},
        )


def test_registered_core_verifier_can_upgrade_transform_relation():
    plugin, variant = _plugin_variant()
    name = "test_exact_relation"
    register_transform_verifier(
        name,
        lambda **kwargs: {
            "verified": True,
            "method": "fixture_exact_identity",
            "relation_kind": kwargs["artifact"]["relation"]["kind"],
        },
    )
    try:
        result = verify_plugin_transform_artifact(
            _artifact(verifier=name),
            transform_kind="rank_jump_base_change",
            context=_context(),
            plugin=plugin,
            variant=variant,
            child_record={"parameter": "11/13"},
        )
    finally:
        unregister_transform_verifier(name)
    assert result["verified"] is True
    assert result["verification_status"] == "core_verified"
    assert result["verifier_result"]["method"] == "fixture_exact_identity"


def test_failed_or_missing_core_verifier_never_upgrades_plugin_claim():
    plugin, variant = _plugin_variant()
    missing = verify_plugin_transform_artifact(
        _artifact(verifier="not_registered"),
        transform_kind="rank_jump_base_change",
        context=_context(),
        plugin=plugin,
        variant=variant,
        child_record={"parameter": "11/13"},
    )
    assert missing["verified"] is False
    assert missing["verification_status"] == "plugin_asserted"
    assert missing["reason"] == "unsupported_transform_verifier"

    name = "test_reject_relation"
    register_transform_verifier(
        name,
        lambda **kwargs: {"verified": False, "reason": "relation_not_proved"},
    )
    try:
        rejected = verify_plugin_transform_artifact(
            _artifact(verifier=name),
            transform_kind="rank_jump_base_change",
            context=_context(),
            plugin=plugin,
            variant=variant,
            child_record={"parameter": "11/13"},
        )
    finally:
        unregister_transform_verifier(name)
    assert rejected["verified"] is False
    assert rejected["verification_status"] == "plugin_asserted"
    assert rejected["reason"] == "relation_not_proved"


def test_rank_jump_wrapper_persists_core_transform_verification(monkeypatch):
    plugin, variant = _plugin_variant()
    name = "test_wrapper_relation"
    register_transform_verifier(
        name,
        lambda **kwargs: {"verified": True, "method": "fixture_relation"},
    )

    monkeypatch.setattr(
        transforms,
        "run_isolated_plugin_hook",
        lambda plugin, hook_name, payload, timeout: {
            "status": "completed",
            "children": [{
                "parameter": "11/13",
                "transform_artifact": _artifact(verifier=name),
            }],
            "_isolated_hook": {"runtime_seconds": 0.01},
        },
    )
    try:
        result = transforms.plugin_base_change_children(
            object(),
            context={
                **_context(),
                "plugin": plugin,
                "variant": variant,
                "score": 8.0,
            },
            config={},
        )
    finally:
        unregister_transform_verifier(name)

    assert result["status"] == "completed"
    child = result["children"][0]
    assert child["metadata"]["transform_verification_status"] == "core_verified"
    assert child["metadata"]["transform_verification"]["verified"] is True


def test_malformed_typed_artifact_is_explicit_transform_child_failure(monkeypatch):
    plugin, variant = _plugin_variant()

    monkeypatch.setattr(
        transforms,
        "run_isolated_plugin_hook",
        lambda plugin, hook_name, payload, timeout: {
            "status": "completed",
            "children": [{
                "parameter": "11/13",
                "transform_artifact": {
                    "schema": "wrong.schema",
                    "status": "completed",
                },
            }],
            "_isolated_hook": {"runtime_seconds": 0.01},
        },
    )
    result = transforms.plugin_base_change_children(
        object(),
        context={
            **_context(),
            "plugin": plugin,
            "variant": variant,
            "score": 8.0,
        },
        config={},
    )
    assert result["status"] == "error"
    assert result["children"] == []
    assert result["child_failures"] == 1
    assert "unsupported transform artifact schema" in result["child_failure_samples"][0]["error"]


def test_surface_wrapper_preserves_typed_plugin_asserted_classification(monkeypatch):
    plugin = SimpleNamespace(id="surface", adapter_path="adapter.py")
    variant = SimpleNamespace(id="v1", family_spec="json:surface")
    context = {
        "parameter": "2/3",
        "curve_id": 7,
        "E": SimpleNamespace(a_invariants=lambda: [0, 0, 0, -1, 1]),
    }
    artifact = {
        "schema": SCHEMA,
        "status": "completed",
        "transform_kind": "surface_fibration_switch",
        "source": {
            "plugin_id": "surface",
            "variant_id": "v1",
            "parameter": "2/3",
            "a_invariants": ["0", "0", "0", "-1", "1"],
        },
        "target": {
            "plugin_id": "alternate",
            "variant_id": "f2",
            "parameter": "3/5",
        },
        "relation": {
            "kind": "elliptic_surface_fibration_switch",
            "data": {"fibration": "f2"},
        },
        "implementation": {"plugin_id": "surface"},
    }

    monkeypatch.setattr(
        transforms,
        "run_isolated_plugin_hook",
        lambda plugin, hook_name, payload, timeout: {
            "status": "completed",
            "children": [{
                "plugin_id": "alternate",
                "variant_id": "f2",
                "parameter": "3/5",
                "transform_artifact": artifact,
            }],
            "_isolated_hook": {"runtime_seconds": 0.01},
        },
    )
    result = transforms.surface_fibration_switch_children(
        object(),
        context={**context, "plugin": plugin, "variant": variant, "score": 1.0},
        config={},
    )
    assert result["status"] == "completed"
    child = result["children"][0]
    assert child["metadata"]["transform_verification_status"] == "plugin_asserted"
    assert child["metadata"]["transform_verification"]["schema"] == SCHEMA
