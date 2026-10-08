"""Constructive-family Builder stages.

These stages orchestrate exact symbolic constructions supplied by Family
adapters.  Core never invents section/trace/bisection formulae; it validates
the shape of returned artifacts and keeps them separate from rank evidence.
"""
from __future__ import annotations

from rank42.constructive_artifact import (
    SUPPORTED_DIVISIONS,
    SUPPORTED_SLOPE_MODES,
    bind_artifact_hash,
    verify_division_artifact,
    verify_section_artifact,
    verify_trace_artifact,
)
from rank42.plugin_hook_runner import run_isolated_plugin_hook
from rank42.plugins import plugin_fingerprints


CONSTRUCTIVE_HOOK_STATUSES = {
    "completed",
    "partial",
    "timeout",
    "error",
    "unsupported",
    "user_stopped",
    "inconclusive",
}


def _constructive_plugin_provenance(context):
    plugin = context.get("plugin")
    variant = context.get("variant")
    if plugin is None:
        return {
            "plugin_id": None,
            "plugin_version": None,
            "variant_id": None,
            "fingerprints": {},
        }
    try:
        fingerprints = dict(plugin_fingerprints(plugin, variant) or {})
    except Exception:
        fingerprints = {}
    return {
        "plugin_id": str(plugin.id),
        "plugin_version": str(getattr(plugin, "version", "") or "") or None,
        "variant_id": (
            None if variant is None else str(variant.id)
        ),
        "fingerprints": fingerprints,
    }


def _run_constructive_hook(
    context,
    *,
    hook_name,
    request,
    config,
    artifact_key,
):
    plugin = context.get("plugin")
    timeout = max(1, int(config.get("timeout") or 30))
    raw = run_isolated_plugin_hook(
        plugin,
        hook_name,
        request,
        timeout=timeout,
    )
    if not isinstance(raw, dict):
        raw = {
            "status": "error",
            "reason": "isolated Constructive hook returned malformed result",
            "error": repr(raw),
        }
    payload = dict(raw)
    status = str(payload.get("status") or "error")
    if status not in CONSTRUCTIVE_HOOK_STATUSES:
        payload["status"] = "error"
        payload["reason"] = "invalid_constructive_hook_status"
        payload["error"] = f"unsupported hook status {status!r}"
        status = "error"
    payload.setdefault(artifact_key, [])

    isolated = dict(payload.get("_isolated_hook") or {})
    runtime = payload.get("runtime_seconds")
    if runtime is None:
        runtime = isolated.get("runtime_seconds")
    provenance = _constructive_plugin_provenance(context)
    payload["constructive_attempt"] = {
        "status": status,
        "hook_name": str(hook_name),
        "runtime_seconds": runtime,
        "timeout_seconds": timeout,
        "budget": {"wall_clock_seconds": timeout},
        "plugin_id": provenance["plugin_id"],
        "plugin_version": provenance["plugin_version"],
        "variant_id": provenance["variant_id"],
        "fingerprints": provenance["fingerprints"],
        "worker_exit_code": isolated.get("worker_exit_code"),
        "checkpoint": payload.get("checkpoint"),
        "retry_token": payload.get("retry_token"),
        "retryable": bool(
            payload.get("retryable")
            or status in {"partial", "timeout", "error", "inconclusive"}
        ),
    }
    payload["hard_isolated"] = True
    payload["timeout_seconds"] = timeout
    return payload


def _family_request(context, *, stage_id, config):
    plugin = context.get("plugin")
    variant = context.get("variant")
    return {
        "stage_id": str(stage_id),
        "pipeline_run_id": (
            None
            if context.get("pipeline_run_id") is None
            else int(context["pipeline_run_id"])
        ),
        "pipeline_candidate_id": (
            None
            if context.get("pipeline_candidate_id") is None
            else int(context["pipeline_candidate_id"])
        ),
        "plugin_id": None if plugin is None else plugin.id,
        "variant_id": None if variant is None else variant.id,
        "family_spec": None if variant is None else variant.family_spec,
        "parameter": str(context.get("parameter")),
        "curve_id": int(context["curve_id"]),
        "a_invariants": [str(x) for x in context["E"].a_invariants()],
        "config": dict(config or {}),
    }


def _normalize_artifacts(records, *, kind, limit):
    out = []
    seen = set()
    for index, rec in enumerate(records or [], 1):
        if not isinstance(rec, dict):
            continue
        artifact = dict(rec)
        artifact_id = str(
            artifact.get("id")
            or artifact.get("label")
            or f"{kind}-{index}"
        ).strip()
        if not artifact_id or artifact_id in seen:
            continue
        seen.add(artifact_id)
        artifact["id"] = artifact_id
        artifact["kind"] = str(artifact.get("kind") or kind)
        artifact["rank_claim"] = False
        artifact["producer_exact_construction"] = bool(
            artifact.get("exact_construction", False)
        )
        artifact["exact_construction"] = False
        artifact["verification_status"] = "unverified"
        out.append(artifact)
        if len(out) >= int(limit):
            break
    return out


def section_height_shell(*, context, config):
    request = _family_request(
        context,
        stage_id="section_height_shell",
        config=config,
    )
    request.update({
        "height": int(config.get("height") or 10),
        "height_mode": str(config.get("height_mode") or "exact"),
        "height_kind": str(
            config.get("height_kind") or "family_enumeration_shell"
        ),
        "height_normalization": str(
            config.get("height_normalization") or "family_defined"
        ),
        "coefficient_bound": int(config.get("coefficient_bound") or 8),
        "max_sections": int(config.get("max_sections") or 128),
    })
    payload = _run_constructive_hook(
        context,
        hook_name="derive_pipeline_section_shell",
        request=request,
        config=config,
        artifact_key="sections",
    )
    raw_sections = payload.get("sections") or []
    sections = []
    failures = []
    seen = set()
    for index, rec in enumerate(raw_sections, 1):
        if not isinstance(rec, dict):
            if len(failures) < 5:
                failures.append({
                    "section_index": index,
                    "reason": "section_artifact_must_be_object",
                })
            continue
        artifact = dict(rec)
        artifact_id = str(
            artifact.get("id")
            or artifact.get("label")
            or f"section-{index}"
        ).strip()
        if not artifact_id or artifact_id in seen:
            continue
        seen.add(artifact_id)
        artifact["id"] = artifact_id
        artifact["kind"] = "section"
        artifact = bind_artifact_hash(artifact)
        artifact["rank_claim"] = False
        artifact["producer_exact_construction"] = bool(
            artifact.get("exact_construction", False)
        )
        artifact["exact_construction"] = False
        try:
            verification = verify_section_artifact(
                artifact,
                context=context,
                request=request,
            )
        except Exception as exc:
            if len(failures) < 5:
                failures.append({
                    "section_index": index,
                    "artifact_id": artifact_id,
                    "reason": "section_artifact_verification_error",
                    "error_class": type(exc).__name__,
                    "error": str(exc)[:240],
                })
            continue
        artifact["verification"] = verification
        artifact["verification_status"] = verification[
            "verification_status"
        ]
        artifact["exact_construction"] = bool(
            verification.get("verified")
        )
        sections.append(artifact)
        if len(sections) >= request["max_sections"]:
            break

    status = str(payload.get("status") or "completed")
    if failures and status == "completed":
        status = "partial" if sections else "error"
    return {
        **{k: v for k, v in payload.items() if k != "sections"},
        "status": status,
        "sections": sections,
        "section_count": len(sections),
        "section_failures": len(failures),
        "section_failure_samples": failures,
        "requested_height": request["height"],
        "height_mode": request["height_mode"],
        "height_kind": request["height_kind"],
        "height_normalization": request["height_normalization"],
        "coefficient_bound": request["coefficient_bound"],
        "rank_claim": False,
    }


def trace_section_constructor(*, context, config, section_shell):
    sections = []
    for rec in list((section_shell or {}).get("sections") or []):
        if not isinstance(rec, dict):
            continue
        sections.append(
            rec if rec.get("artifact_hash") else bind_artifact_hash(rec)
        )
    if not sections:
        return {
            "status": "inconclusive",
            "reason": "no_section_shell_artifacts",
            "trace_sections": [],
            "rank_claim": False,
        }

    request = _family_request(
        context,
        stage_id="trace_section_constructor",
        config=config,
    )
    request.update({
        "sections": sections,
        "extension_degree": int(config.get("extension_degree") or 2),
        "max_sections": int(config.get("max_sections") or 128),
    })
    payload = _run_constructive_hook(
        context,
        hook_name="derive_pipeline_trace_sections",
        request=request,
        config=config,
        artifact_key="trace_sections",
    )
    raw_traces = payload.get("trace_sections") or payload.get("sections") or []
    traces = []
    failures = []
    seen = set()
    for index, rec in enumerate(raw_traces, 1):
        if not isinstance(rec, dict):
            if len(failures) < 5:
                failures.append({
                    "trace_index": index,
                    "reason": "trace_artifact_must_be_object",
                })
            continue
        artifact = dict(rec)
        artifact_id = str(
            artifact.get("id")
            or artifact.get("label")
            or f"trace-{index}"
        ).strip()
        if not artifact_id or artifact_id in seen:
            continue
        seen.add(artifact_id)
        artifact["id"] = artifact_id
        artifact["kind"] = "trace_section"
        artifact = bind_artifact_hash(artifact)
        artifact["rank_claim"] = False
        artifact["producer_exact_construction"] = bool(
            artifact.get("exact_construction", False)
        )
        artifact["exact_construction"] = False
        try:
            verification = verify_trace_artifact(
                artifact,
                context=context,
                request=request,
                source_sections=sections,
            )
        except Exception as exc:
            if len(failures) < 5:
                failures.append({
                    "trace_index": index,
                    "artifact_id": artifact_id,
                    "reason": "trace_artifact_verification_error",
                    "error_class": type(exc).__name__,
                    "error": str(exc)[:240],
                })
            continue
        artifact["verification"] = verification
        artifact["verification_status"] = verification[
            "verification_status"
        ]
        artifact["exact_construction"] = bool(
            verification.get("verified")
        )
        traces.append(artifact)
        if len(traces) >= request["max_sections"]:
            break

    status = str(payload.get("status") or "completed")
    if failures and status == "completed":
        status = "partial" if traces else "error"
    return {
        **{
            k: v for k, v in payload.items()
            if k not in {"trace_sections", "sections"}
        },
        "status": status,
        "trace_sections": traces,
        "trace_count": len(traces),
        "trace_failures": len(failures),
        "trace_failure_samples": failures,
        "extension_degree": request["extension_degree"],
        "rank_claim": False,
    }

def forced_bisection_constructor(*, context, config, trace_state):
    traces = []
    for rec in list((trace_state or {}).get("trace_sections") or []):
        if not isinstance(rec, dict):
            continue
        traces.append(
            rec if rec.get("artifact_hash") else bind_artifact_hash(rec)
        )
    if not traces:
        return {
            "status": "inconclusive",
            "reason": "no_trace_section_artifacts",
            "conditions": [],
            "rank_claim": False,
        }

    division = int(config.get("division") or 2)
    slope_mode = str(config.get("slope_mode") or "forced_tangent")
    if slope_mode == "forced":
        slope_mode = "forced_tangent"
    if division not in SUPPORTED_DIVISIONS:
        return {
            "status": "error",
            "reason": "unsupported_division",
            "division": division,
            "conditions": [],
            "rank_claim": False,
        }
    if slope_mode not in SUPPORTED_SLOPE_MODES:
        return {
            "status": "error",
            "reason": "unsupported_slope_mode",
            "slope_mode": slope_mode,
            "conditions": [],
            "rank_claim": False,
        }

    request = _family_request(
        context,
        stage_id="forced_bisection_constructor",
        config=config,
    )
    request.update({
        "trace_sections": traces,
        "division": division,
        "slope_mode": slope_mode,
        "max_conditions": int(config.get("max_conditions") or 128),
    })
    payload = _run_constructive_hook(
        context,
        hook_name="derive_pipeline_bisection_conditions",
        request=request,
        config=config,
        artifact_key="conditions",
    )
    conditions = []
    failures = []
    seen = set()
    for index, rec in enumerate(payload.get("conditions") or [], 1):
        if not isinstance(rec, dict):
            if len(failures) < 5:
                failures.append({
                    "condition_index": index,
                    "reason": "division_artifact_must_be_object",
                })
            continue
        artifact = dict(rec)
        artifact_id = str(
            artifact.get("id")
            or artifact.get("label")
            or f"condition-{index}"
        ).strip()
        if not artifact_id or artifact_id in seen:
            continue
        seen.add(artifact_id)
        artifact["id"] = artifact_id
        artifact["kind"] = "bisection_condition"
        artifact = bind_artifact_hash(artifact)
        artifact["rank_claim"] = False
        artifact["producer_exact_construction"] = bool(
            artifact.get("exact_construction", False)
        )
        artifact["exact_construction"] = False
        try:
            verification = verify_division_artifact(
                artifact,
                context=context,
                request=request,
                source_traces=traces,
            )
        except Exception as exc:
            if len(failures) < 5:
                failures.append({
                    "condition_index": index,
                    "artifact_id": artifact_id,
                    "reason": "division_artifact_verification_error",
                    "error_class": type(exc).__name__,
                    "error": str(exc)[:240],
                })
            continue
        artifact["verification"] = verification
        artifact["verification_status"] = verification[
            "verification_status"
        ]
        artifact["exact_construction"] = bool(
            verification.get("verified")
        )
        conditions.append(artifact)
        if len(conditions) >= request["max_conditions"]:
            break

    status = str(payload.get("status") or "completed")
    if failures and status == "completed":
        status = "partial" if conditions else "error"
    return {
        **{k: v for k, v in payload.items() if k != "conditions"},
        "status": status,
        "conditions": conditions,
        "condition_count": len(conditions),
        "condition_failures": len(failures),
        "condition_failure_samples": failures,
        "division": request["division"],
        "slope_mode": request["slope_mode"],
        "rank_claim": False,
    }