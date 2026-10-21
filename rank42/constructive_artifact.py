"""Typed verification boundary for Constructive section artifacts."""
from __future__ import annotations

from fractions import Fraction
import hashlib
import json


SECTION_SCHEMA = "rank42.constructive_section.v1"
TRACE_SCHEMA = "rank42.constructive_trace.v1"
DIVISION_SCHEMA = "rank42.constructive_division_condition.v1"
SQUARE_SPECIALIZATION_SCHEMA = "rank42.constructive_square_specialization.v1"
HEIGHT_KINDS = frozenset({
    "family_enumeration_shell",
    "coefficient_height",
    "polynomial_degree",
    "naive_parameter_height",
    "canonical_shioda_height",
})

_SECTION_VERIFIERS = {}
_TRACE_VERIFIERS = {}
_DIVISION_VERIFIERS = {}
_SQUARE_SPECIALIZATION_VERIFIERS = {}

SUPPORTED_DIVISIONS = frozenset({2, 3, 4})
SUPPORTED_SLOPE_MODES = frozenset({
    "forced_tangent",
    "division_polynomial",
    "family_exact",
})


def register_section_verifier(name, verifier):
    key = str(name or "").strip()
    if not key:
        raise ValueError("section verifier name is required")
    if not callable(verifier):
        raise TypeError("section verifier must be callable")
    _SECTION_VERIFIERS[key] = verifier


def unregister_section_verifier(name):
    _SECTION_VERIFIERS.pop(str(name or "").strip(), None)


def register_trace_verifier(name, verifier):
    key = str(name or "").strip()
    if not key:
        raise ValueError("trace verifier name is required")
    if not callable(verifier):
        raise TypeError("trace verifier must be callable")
    _TRACE_VERIFIERS[key] = verifier


def unregister_trace_verifier(name):
    _TRACE_VERIFIERS.pop(str(name or "").strip(), None)


def register_division_verifier(name, verifier):
    key = str(name or "").strip()
    if not key:
        raise ValueError("division verifier name is required")
    if not callable(verifier):
        raise TypeError("division verifier must be callable")
    _DIVISION_VERIFIERS[key] = verifier


def unregister_division_verifier(name):
    _DIVISION_VERIFIERS.pop(str(name or "").strip(), None)


def register_square_specialization_verifier(name, verifier):
    key = str(name or "").strip()
    if not key:
        raise ValueError("square specialization verifier name is required")
    if not callable(verifier):
        raise TypeError("square specialization verifier must be callable")
    _SQUARE_SPECIALIZATION_VERIFIERS[key] = verifier


def unregister_square_specialization_verifier(name):
    _SQUARE_SPECIALIZATION_VERIFIERS.pop(
        str(name or "").strip(), None
    )


def _register_builtin_formula_family_verifiers():
    from rank42.formula_constructive_verifiers import (
        DIVISION_VERIFIER,
        SECTION_VERIFIER,
        SQUARE_VERIFIER,
        TRACE_VERIFIER,
        verify_base_section_trace,
        verify_condition_specialization,
        verify_declared_section,
        verify_exact_division,
    )

    register_section_verifier(SECTION_VERIFIER, verify_declared_section)
    register_trace_verifier(TRACE_VERIFIER, verify_base_section_trace)
    register_division_verifier(DIVISION_VERIFIER, verify_exact_division)
    register_square_specialization_verifier(
        SQUARE_VERIFIER, verify_condition_specialization
    )


_register_builtin_formula_family_verifiers()


def artifact_hash(artifact):
    """Stable content hash for Constructive lineage binding."""
    payload = {
        key: value
        for key, value in dict(artifact or {}).items()
        if key not in {
            "artifact_hash",
            "verification",
            "verification_status",
            "exact_construction",
            "producer_exact_construction",
        }
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def bind_artifact_hash(artifact):
    artifact = dict(artifact)
    artifact["artifact_hash"] = artifact_hash(artifact)
    return artifact


def _artifact_by_id(records, artifact_id):
    wanted = str(artifact_id or "")
    for rec in records or []:
        if isinstance(rec, dict) and str(rec.get("id") or "") == wanted:
            return rec
    return None


def _text(value):
    return None if value is None else str(value)


def _family_identity(context):
    plugin = context.get("plugin")
    variant = context.get("variant")
    return {
        "plugin_id": None if plugin is None else str(plugin.id),
        "variant_id": None if variant is None else str(variant.id),
        "family_spec": None if variant is None else str(variant.family_spec),
    }


def _normalize_height(height):
    if not isinstance(height, dict):
        raise ValueError("section artifact height must be an object")
    kind = str(height.get("kind") or "").strip()
    if kind not in HEIGHT_KINDS:
        raise ValueError("section artifact height.kind is unsupported")
    mode = str(height.get("mode") or "").strip()
    if mode not in {"exact", "up_to"}:
        raise ValueError("section artifact height.mode must be exact or up_to")
    normalization = str(height.get("normalization") or "").strip()
    if not normalization:
        raise ValueError("section artifact height.normalization is required")
    value = height.get("value")
    if value is None:
        raise ValueError("section artifact height.value is required")
    try:
        value_q = str(Fraction(str(value)))
    except Exception as exc:
        raise ValueError("section artifact height.value must be rational") from exc
    coefficient_bound = height.get("coefficient_bound")
    if coefficient_bound is not None:
        try:
            coefficient_bound = int(coefficient_bound)
        except Exception as exc:
            raise ValueError(
                "section artifact coefficient_bound must be an integer"
            ) from exc
        if coefficient_bound <= 0:
            raise ValueError(
                "section artifact coefficient_bound must be positive"
            )
    return {
        "kind": kind,
        "value": value_q,
        "mode": mode,
        "normalization": normalization,
        "coefficient_bound": coefficient_bound,
    }


def verify_section_artifact(artifact, *, context, request):
    """Validate a section artifact and classify its exactness.

    Producer-declared exactness is ignored. A legacy/untyped dictionary remains
    usable as an unverified construction hint. A typed artifact becomes exact
    only when its named registered verifier succeeds.
    """
    if not isinstance(artifact, dict):
        raise ValueError("section artifact must be an object")

    if artifact.get("schema") is None:
        return {
            "schema": None,
            "verified": False,
            "verification_status": "unverified",
            "reason": "legacy_or_untyped_section_artifact",
            "verifier": None,
        }

    if str(artifact.get("schema")) != SECTION_SCHEMA:
        raise ValueError("unsupported section artifact schema")
    if str(artifact.get("artifact_kind") or "") != "section":
        raise ValueError("section artifact kind must be section")

    family = artifact.get("family")
    if not isinstance(family, dict):
        raise ValueError("section artifact family must be an object")
    actual_family = _family_identity(context)
    for key in ("plugin_id", "variant_id", "family_spec"):
        claimed = _text(family.get(key))
        if claimed is not None and claimed != actual_family.get(key):
            raise ValueError(f"section artifact family {key} mismatch")

    section = artifact.get("section")
    if not isinstance(section, dict) or not section:
        raise ValueError("section artifact section data is required")
    parameter_variable = str(
        section.get("parameter_variable") or ""
    ).strip()
    if not parameter_variable:
        raise ValueError(
            "section artifact section.parameter_variable is required"
        )
    if section.get("x") is None or section.get("y") is None:
        raise ValueError("section artifact exact x/y formulas are required")

    height = _normalize_height(artifact.get("height"))
    requested_kind = str(request.get("height_kind") or "")
    requested_normalization = str(
        request.get("height_normalization") or ""
    )
    requested_mode = str(request.get("height_mode") or "")
    requested_value = str(Fraction(int(request.get("height") or 0)))
    requested_coeff = int(request.get("coefficient_bound") or 0)

    if height["kind"] != requested_kind:
        raise ValueError("section artifact height kind mismatch")
    if height["normalization"] != requested_normalization:
        raise ValueError("section artifact height normalization mismatch")
    if height["mode"] != requested_mode:
        raise ValueError("section artifact height mode mismatch")
    if height["value"] != requested_value:
        raise ValueError("section artifact height value mismatch")
    if (
        height["coefficient_bound"] is not None
        and height["coefficient_bound"] > requested_coeff
    ):
        raise ValueError("section artifact exceeds coefficient bound")

    implementation = artifact.get("implementation") or {}
    if not isinstance(implementation, dict):
        raise ValueError("section artifact implementation must be an object")
    implementation_plugin = implementation.get("plugin_id")
    if (
        implementation_plugin is not None
        and str(implementation_plugin) != actual_family["plugin_id"]
    ):
        raise ValueError("section artifact implementation plugin_id mismatch")

    verifier_name = str(artifact.get("verifier") or "").strip()
    base = {
        "schema": SECTION_SCHEMA,
        "verified": False,
        "verification_status": "unverified",
        "reason": "no_registered_section_verifier",
        "verifier": verifier_name or None,
        "family": actual_family,
        "section": dict(section),
        "height": height,
        "implementation": dict(implementation),
    }
    if not verifier_name:
        return base

    verifier = _SECTION_VERIFIERS.get(verifier_name)
    if verifier is None:
        return {
            **base,
            "reason": "unsupported_section_verifier",
        }

    result = verifier(
        artifact=dict(artifact),
        context=context,
        request=dict(request),
        family=actual_family,
        section=dict(section),
        height=height,
    )
    if not isinstance(result, dict):
        raise ValueError("section verifier must return an object")
    if result.get("verified") is not True:
        return {
            **base,
            "reason": str(
                result.get("reason") or "section_verification_failed"
            ),
            "verifier_result": dict(result),
        }
    return {
        **base,
        "verified": True,
        "verification_status": "verified",
        "reason": "section_verification_succeeded",
        "verifier_result": dict(result),
    }



def verify_trace_artifact(artifact, *, context, request, source_sections):
    if not isinstance(artifact, dict):
        raise ValueError("trace artifact must be an object")
    if artifact.get("schema") is None:
        return {
            "schema": None,
            "verified": False,
            "verification_status": "unverified",
            "reason": "legacy_or_untyped_trace_artifact",
            "verifier": None,
        }
    if str(artifact.get("schema")) != TRACE_SCHEMA:
        raise ValueError("unsupported trace artifact schema")
    if str(artifact.get("artifact_kind") or "") != "trace_section":
        raise ValueError("trace artifact kind must be trace_section")

    family = artifact.get("family")
    if not isinstance(family, dict):
        raise ValueError("trace artifact family must be an object")
    actual_family = _family_identity(context)
    for key in ("plugin_id", "variant_id", "family_spec"):
        claimed = _text(family.get(key))
        if claimed is not None and claimed != actual_family.get(key):
            raise ValueError(f"trace artifact family {key} mismatch")

    source = artifact.get("source_section")
    if not isinstance(source, dict):
        raise ValueError("trace artifact source_section is required")
    source_id = str(source.get("artifact_id") or "").strip()
    source_hash = str(source.get("artifact_hash") or "").strip()
    if not source_id or not source_hash:
        raise ValueError("trace artifact source id/hash are required")
    parent = _artifact_by_id(source_sections, source_id)
    if parent is None:
        raise ValueError("trace artifact source section not found")
    if str(parent.get("artifact_hash") or "") != source_hash:
        raise ValueError("trace artifact source section hash mismatch")

    extension = artifact.get("extension")
    if not isinstance(extension, dict):
        raise ValueError("trace artifact extension must be an object")
    degree = int(extension.get("degree") or 0)
    if degree != int(request.get("extension_degree") or 0):
        raise ValueError("trace artifact extension degree mismatch")
    field = str(extension.get("field") or "").strip()
    if not field:
        raise ValueError("trace artifact extension field is required")
    conjugates = extension.get("conjugates")
    if not isinstance(conjugates, list) or len(conjugates) != degree:
        raise ValueError(
            "trace artifact conjugates must list exactly extension degree entries"
        )

    lineage = artifact.get("lineage")
    if not isinstance(lineage, dict):
        raise ValueError("trace artifact lineage is required")
    expected_run = context.get("pipeline_run_id")
    expected_candidate = context.get("pipeline_candidate_id")
    if expected_run is not None and int(lineage.get("pipeline_run_id") or -1) != int(expected_run):
        raise ValueError("trace artifact pipeline_run_id mismatch")
    if (
        expected_candidate is not None
        and int(lineage.get("pipeline_candidate_id") or -1)
        != int(expected_candidate)
    ):
        raise ValueError("trace artifact pipeline_candidate_id mismatch")

    trace = artifact.get("trace")
    if not isinstance(trace, dict):
        raise ValueError("trace artifact trace data is required")
    if trace.get("x") is None or trace.get("y") is None:
        raise ValueError("trace artifact exact x/y formulas are required")
    if str(trace.get("relation") or "") != "sum_of_conjugates":
        raise ValueError("trace artifact relation must be sum_of_conjugates")
    if str(trace.get("base_field") or "") != "QQ":
        raise ValueError("trace artifact base_field must be QQ")
    if not str(trace.get("parameter_variable") or "").strip():
        raise ValueError(
            "trace artifact trace.parameter_variable is required"
        )

    implementation = artifact.get("implementation") or {}
    if not isinstance(implementation, dict):
        raise ValueError("trace artifact implementation must be an object")
    verifier_name = str(artifact.get("verifier") or "").strip()
    base = {
        "schema": TRACE_SCHEMA,
        "verified": False,
        "verification_status": "unverified",
        "reason": "no_registered_trace_verifier",
        "verifier": verifier_name or None,
        "family": actual_family,
        "source_section_id": source_id,
        "source_section_hash": source_hash,
        "source_section_exact": bool(parent.get("exact_construction")),
        "extension": dict(extension),
        "trace": dict(trace),
        "lineage": dict(lineage),
        "implementation": dict(implementation),
    }
    if not bool(parent.get("exact_construction")):
        return {
            **base,
            "reason": "source_section_not_exactly_verified",
        }
    if not verifier_name:
        return base
    verifier = _TRACE_VERIFIERS.get(verifier_name)
    if verifier is None:
        return {**base, "reason": "unsupported_trace_verifier"}
    result = verifier(
        artifact=dict(artifact),
        context=context,
        request=dict(request),
        source_section=dict(parent),
        extension=dict(extension),
        trace=dict(trace),
    )
    if not isinstance(result, dict):
        raise ValueError("trace verifier must return an object")
    if result.get("verified") is not True:
        return {
            **base,
            "reason": str(
                result.get("reason") or "trace_verification_failed"
            ),
            "verifier_result": dict(result),
        }
    return {
        **base,
        "verified": True,
        "verification_status": "verified",
        "reason": "trace_verification_succeeded",
        "verifier_result": dict(result),
    }


def verify_division_artifact(artifact, *, context, request, source_traces):
    if not isinstance(artifact, dict):
        raise ValueError("division artifact must be an object")
    if artifact.get("schema") is None:
        return {
            "schema": None,
            "verified": False,
            "verification_status": "unverified",
            "reason": "legacy_or_untyped_division_artifact",
            "verifier": None,
        }
    if str(artifact.get("schema")) != DIVISION_SCHEMA:
        raise ValueError("unsupported division artifact schema")
    if str(artifact.get("artifact_kind") or "") != "division_condition":
        raise ValueError(
            "division artifact kind must be division_condition"
        )

    family = artifact.get("family")
    if not isinstance(family, dict):
        raise ValueError("division artifact family must be an object")
    actual_family = _family_identity(context)
    for key in ("plugin_id", "variant_id", "family_spec"):
        claimed = _text(family.get(key))
        if claimed is not None and claimed != actual_family.get(key):
            raise ValueError(f"division artifact family {key} mismatch")

    source = artifact.get("source_trace")
    if not isinstance(source, dict):
        raise ValueError("division artifact source_trace is required")
    source_id = str(source.get("artifact_id") or "").strip()
    source_hash = str(source.get("artifact_hash") or "").strip()
    if not source_id or not source_hash:
        raise ValueError("division artifact source id/hash are required")
    parent = _artifact_by_id(source_traces, source_id)
    if parent is None:
        raise ValueError("division artifact source trace not found")
    if str(parent.get("artifact_hash") or "") != source_hash:
        raise ValueError("division artifact source trace hash mismatch")

    lineage = artifact.get("lineage")
    if not isinstance(lineage, dict):
        raise ValueError("division artifact lineage is required")
    expected_run = context.get("pipeline_run_id")
    expected_candidate = context.get("pipeline_candidate_id")
    if expected_run is not None and int(lineage.get("pipeline_run_id") or -1) != int(expected_run):
        raise ValueError("division artifact pipeline_run_id mismatch")
    if (
        expected_candidate is not None
        and int(lineage.get("pipeline_candidate_id") or -1)
        != int(expected_candidate)
    ):
        raise ValueError("division artifact pipeline_candidate_id mismatch")

    relation = artifact.get("relation")
    if not isinstance(relation, dict):
        raise ValueError("division artifact relation is required")
    if str(relation.get("kind") or "") != "division":
        raise ValueError("division artifact relation.kind must be division")
    division = int(relation.get("division") or 0)
    slope_mode = str(relation.get("slope_mode") or "").strip()
    if division not in SUPPORTED_DIVISIONS:
        raise ValueError("division artifact division is unsupported")
    if slope_mode not in SUPPORTED_SLOPE_MODES:
        raise ValueError("division artifact slope_mode is unsupported")
    if division != int(request.get("division") or 0):
        raise ValueError("division artifact division mismatch")
    if slope_mode != str(request.get("slope_mode") or ""):
        raise ValueError("division artifact slope_mode mismatch")
    if relation.get("condition") is None:
        raise ValueError("division artifact exact condition is required")

    implementation = artifact.get("implementation") or {}
    if not isinstance(implementation, dict):
        raise ValueError("division artifact implementation must be an object")
    verifier_name = str(artifact.get("verifier") or "").strip()
    base = {
        "schema": DIVISION_SCHEMA,
        "verified": False,
        "verification_status": "unverified",
        "reason": "no_registered_division_verifier",
        "verifier": verifier_name or None,
        "family": actual_family,
        "source_trace_id": source_id,
        "source_trace_hash": source_hash,
        "source_trace_exact": bool(parent.get("exact_construction")),
        "relation": dict(relation),
        "lineage": dict(lineage),
        "implementation": dict(implementation),
    }
    if not bool(parent.get("exact_construction")):
        return {
            **base,
            "reason": "source_trace_not_exactly_verified",
        }
    if not verifier_name:
        return base
    verifier = _DIVISION_VERIFIERS.get(verifier_name)
    if verifier is None:
        return {**base, "reason": "unsupported_division_verifier"}
    result = verifier(
        artifact=dict(artifact),
        context=context,
        request=dict(request),
        source_trace=dict(parent),
        relation=dict(relation),
    )
    if not isinstance(result, dict):
        raise ValueError("division verifier must return an object")
    if result.get("verified") is not True:
        return {
            **base,
            "reason": str(
                result.get("reason") or "division_verification_failed"
            ),
            "verifier_result": dict(result),
        }
    return {
        **base,
        "verified": True,
        "verification_status": "verified",
        "reason": "division_verification_succeeded",
        "verifier_result": dict(result),
    }



def verify_square_specialization_artifact(
    artifact,
    *,
    context,
    request,
    source_conditions,
):
    """Verify a child parameter is an exact hit of one exact parent condition."""
    if not isinstance(artifact, dict):
        raise ValueError("square specialization artifact must be an object")
    if str(artifact.get("schema") or "") != SQUARE_SPECIALIZATION_SCHEMA:
        raise ValueError("unsupported square specialization artifact schema")
    if str(artifact.get("artifact_kind") or "") != "square_specialization":
        raise ValueError(
            "square specialization artifact kind must be square_specialization"
        )

    family = artifact.get("family")
    if not isinstance(family, dict):
        raise ValueError("square specialization family must be an object")
    actual_family = _family_identity(context)
    for key in ("plugin_id", "variant_id", "family_spec"):
        claimed = _text(family.get(key))
        if claimed is not None and claimed != actual_family.get(key):
            raise ValueError(
                f"square specialization family {key} mismatch"
            )

    lineage = artifact.get("lineage")
    if not isinstance(lineage, dict):
        raise ValueError("square specialization lineage is required")
    expected_run = context.get("pipeline_run_id")
    expected_candidate = context.get("pipeline_candidate_id")
    if (
        expected_run is not None
        and int(lineage.get("pipeline_run_id") or -1)
        != int(expected_run)
    ):
        raise ValueError(
            "square specialization pipeline_run_id mismatch"
        )
    if (
        expected_candidate is not None
        and int(lineage.get("pipeline_candidate_id") or -1)
        != int(expected_candidate)
    ):
        raise ValueError(
            "square specialization pipeline_candidate_id mismatch"
        )

    source = artifact.get("source_condition")
    if not isinstance(source, dict):
        raise ValueError(
            "square specialization source_condition is required"
        )
    source_id = str(source.get("artifact_id") or "").strip()
    source_hash = str(source.get("artifact_hash") or "").strip()
    if not source_id or not source_hash:
        raise ValueError(
            "square specialization source condition id/hash are required"
        )
    parent = _artifact_by_id(source_conditions, source_id)
    if parent is None:
        raise ValueError(
            "square specialization source condition not found"
        )
    if str(parent.get("artifact_hash") or "") != source_hash:
        raise ValueError(
            "square specialization source condition hash mismatch"
        )
    if not bool(parent.get("exact_construction")):
        raise ValueError(
            "square specialization source condition is not exactly verified"
        )

    specialization = artifact.get("specialization")
    if not isinstance(specialization, dict):
        raise ValueError(
            "square specialization specialization data is required"
        )
    parameter = specialization.get("parameter")
    if parameter is None:
        raise ValueError(
            "square specialization child parameter is required"
        )
    try:
        parameter_q = str(Fraction(str(parameter)))
    except Exception as exc:
        raise ValueError(
            "square specialization child parameter must be rational"
        ) from exc

    square = artifact.get("square")
    if not isinstance(square, dict):
        raise ValueError("square specialization square data is required")
    try:
        square_value = Fraction(str(square.get("value")))
        square_root = Fraction(str(square.get("root")))
    except Exception as exc:
        raise ValueError(
            "square specialization square value/root must be rational"
        ) from exc
    if square_root * square_root != square_value:
        raise ValueError(
            "square specialization root^2 does not equal square value"
        )

    verifier_name = str(artifact.get("verifier") or "").strip()
    if not verifier_name:
        raise ValueError(
            "square specialization requires a registered verifier"
        )
    verifier = _SQUARE_SPECIALIZATION_VERIFIERS.get(verifier_name)
    if verifier is None:
        raise ValueError(
            "unsupported square specialization verifier"
        )

    result = verifier(
        artifact=dict(artifact),
        context=context,
        request=dict(request),
        source_condition=dict(parent),
        child_parameter=parameter_q,
        square_value=str(square_value),
        square_root=str(square_root),
    )
    if not isinstance(result, dict):
        raise ValueError(
            "square specialization verifier must return an object"
        )
    if result.get("verified") is not True:
        raise ValueError(
            str(
                result.get("reason")
                or "square condition evaluation verification failed"
            )
        )
    condition_value = result.get("condition_value")
    if condition_value is None:
        raise ValueError(
            "square specialization verifier must return condition_value"
        )
    try:
        condition_q = Fraction(str(condition_value))
    except Exception as exc:
        raise ValueError(
            "square specialization condition_value must be rational"
        ) from exc
    if condition_q != square_value:
        raise ValueError(
            "evaluated parent condition does not equal square value"
        )

    return {
        "schema": SQUARE_SPECIALIZATION_SCHEMA,
        "verified": True,
        "verification_status": "verified",
        "reason": "square_condition_specialization_verified",
        "verifier": verifier_name,
        "family": actual_family,
        "lineage": dict(lineage),
        "source_condition_id": source_id,
        "source_condition_hash": source_hash,
        "child_parameter": parameter_q,
        "condition_value": str(condition_q),
        "square_value": str(square_value),
        "square_root": str(square_root),
        "verifier_result": dict(result),
    }
