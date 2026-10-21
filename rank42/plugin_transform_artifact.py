"""Typed/core-verifiable artifacts for plugin-supplied curve transforms."""
from __future__ import annotations

from fractions import Fraction

SCHEMA = "rank42.plugin_transform_artifact.v1"

_VERIFIERS = {}


def register_transform_verifier(name, verifier):
    """Register a core-owned verifier callable for a typed transform artifact."""
    key = str(name or "").strip()
    if not key:
        raise ValueError("transform verifier name is required")
    if not callable(verifier):
        raise TypeError("transform verifier must be callable")
    _VERIFIERS[key] = verifier


def unregister_transform_verifier(name):
    """Remove a verifier registration (primarily useful for tests/plugins teardown)."""
    _VERIFIERS.pop(str(name or "").strip(), None)


def _q(value):
    return str(Fraction(str(value)))


def _model(values):
    if not isinstance(values, (list, tuple)) or len(values) != 5:
        raise ValueError("a_invariants must contain exactly five rational values")
    return [_q(value) for value in values]


def _optional_text(value):
    return None if value is None else str(value)


def _source_from_context(context, plugin, variant):
    return {
        "plugin_id": str(plugin.id),
        "variant_id": str(variant.id),
        "parameter": str(context["parameter"]),
        "a_invariants": _model(context["E"].a_invariants()),
    }


def _target_from_record(record, plugin, variant):
    target = {
        "plugin_id": str(record.get("plugin_id") or plugin.id),
        "variant_id": _optional_text(
            record.get("variant_id")
            if record.get("plugin_id")
            else variant.id
        ),
        "parameter": _optional_text(record.get("parameter")),
        "a_invariants": None,
    }
    if record.get("a_invariants") is not None:
        target["a_invariants"] = _model(record["a_invariants"])
    return target


def _normalize_endpoint(endpoint, *, require_model=False):
    if not isinstance(endpoint, dict):
        raise ValueError("transform artifact endpoint must be an object")
    out = {
        "plugin_id": _optional_text(endpoint.get("plugin_id")),
        "variant_id": _optional_text(endpoint.get("variant_id")),
        "parameter": _optional_text(endpoint.get("parameter")),
        "a_invariants": None,
    }
    if endpoint.get("a_invariants") is not None:
        out["a_invariants"] = _model(endpoint["a_invariants"])
    if require_model and out["a_invariants"] is None:
        raise ValueError("transform artifact endpoint requires a_invariants")
    return out


def _endpoint_matches(claimed, actual):
    for key in ("plugin_id", "variant_id", "parameter", "a_invariants"):
        value = claimed.get(key)
        if value is not None and value != actual.get(key):
            return False, key
    return True, None


def verify_plugin_transform_artifact(
    artifact,
    *,
    transform_kind,
    context,
    plugin,
    variant,
    child_record,
):
    """Validate a plugin transform artifact and optionally run a core verifier.

    A structurally valid artifact is still only plugin-asserted unless its named
    verifier is registered in core and returns verified=True.
    """
    if artifact is None:
        return {
            "schema": None,
            "verification_status": "plugin_asserted",
            "verified": False,
            "reason": "legacy_or_untyped_transform",
            "verifier": None,
        }
    if not isinstance(artifact, dict):
        raise ValueError("transform_artifact must be an object")
    if str(artifact.get("schema") or "") != SCHEMA:
        raise ValueError("unsupported transform artifact schema")
    if str(artifact.get("status") or "") != "completed":
        raise ValueError("transform artifact status must be completed")
    if str(artifact.get("transform_kind") or "") != str(transform_kind):
        raise ValueError("transform artifact kind does not match requested transform")

    relation = artifact.get("relation")
    if not isinstance(relation, dict) or not str(relation.get("kind") or ""):
        raise ValueError("transform artifact relation.kind is required")

    source = _normalize_endpoint(artifact.get("source"))
    target = _normalize_endpoint(artifact.get("target"))
    actual_source = _source_from_context(context, plugin, variant)
    actual_target = _target_from_record(child_record, plugin, variant)

    source_ok, source_key = _endpoint_matches(source, actual_source)
    if not source_ok:
        raise ValueError(f"transform artifact source {source_key} mismatch")
    target_ok, target_key = _endpoint_matches(target, actual_target)
    if not target_ok:
        raise ValueError(f"transform artifact target {target_key} mismatch")

    implementation = artifact.get("implementation") or {}
    if not isinstance(implementation, dict):
        raise ValueError("transform artifact implementation must be an object")
    impl_plugin_id = implementation.get("plugin_id")
    if impl_plugin_id is not None and str(impl_plugin_id) != str(plugin.id):
        raise ValueError("transform artifact implementation plugin_id mismatch")

    verifier_name = str(artifact.get("verifier") or "").strip()
    base = {
        "schema": SCHEMA,
        "verification_status": "plugin_asserted",
        "verified": False,
        "reason": "no_core_verifier_requested",
        "verifier": verifier_name or None,
        "relation_kind": str(relation["kind"]),
        "relation": dict(relation),
        "source": source,
        "target": target,
        "implementation": dict(implementation),
    }
    if not verifier_name:
        return base

    verifier = _VERIFIERS.get(verifier_name)
    if verifier is None:
        return {
            **base,
            "reason": "unsupported_transform_verifier",
        }

    result = verifier(
        artifact=dict(artifact),
        transform_kind=str(transform_kind),
        context=context,
        plugin=plugin,
        variant=variant,
        child_record=dict(child_record),
        source=actual_source,
        target=actual_target,
    )
    if not isinstance(result, dict):
        raise ValueError("transform verifier must return an object")
    if result.get("verified") is not True:
        return {
            **base,
            "reason": str(result.get("reason") or "core_transform_verification_failed"),
            "verifier_result": dict(result),
        }
    return {
        **base,
        "verification_status": "core_verified",
        "verified": True,
        "reason": "core_transform_verification_succeeded",
        "verifier_result": dict(result),
    }
