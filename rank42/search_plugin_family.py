"""Pipeline-owned host for legacy/plugin Family Search adapters.

Each Pipeline candidate is exported as the same one-row JSONL contract used by
the Family Search UI. The adapter command runs against a temporary SQLite copy.
Only exact points / typed certificates accepted by the core plugin-result
boundary may reach live scientific state.
"""
from __future__ import annotations

import json
import sys

from sage.all import QQ, EllipticCurve

from rank42.candidates import export_rows_jsonl
from rank42.db import get_curve
from rank42.plugin_geometry_result import (
    apply_plugin_geometry_result,
    force_command_database,
    legacy_sandbox_geometry_result,
    load_plugin_geometry_result,
    plugin_geometry_environment,
    plugin_geometry_sandbox,
    snapshot_plugin_geometry_subject,
)
from rank42.plugins import apply_search_command_features, load_adapter
from rank42.search_plugin_geometry import (
    _adapter_default_options,
    _run_bounded_command,
    _trial_snapshot,
)


def bounded_family_options(
    adapter,
    *,
    ratpoints,
    overrides=None,
    plugin=None,
    variant=None,
):
    """Resolve one candidate-local Family adapter option payload."""
    options = _adapter_default_options(
        adapter,
        context="family",
        plugin=plugin,
        variant=variant,
    )
    if overrides is not None:
        if not isinstance(overrides, dict):
            raise ValueError("plugin family option overrides must be a mapping")
        options.update(overrides)
    options["limit"] = 1
    options["ratpoints"] = ratpoints
    options["ratpoints_backend"] = (
        "GPU" if ratpoints and "gpu" in str(ratpoints).lower() else "CPU"
    )
    return options


def run_plugin_family_search(
    args,
    db,
    *,
    curve_id,
    plugin,
    variant,
    source_pool_id,
    source_candidate_id,
    E=None,
):
    """Run one FamilyAdapterV1 family-search command inside Pipeline custody."""
    if "family_search" not in plugin.capabilities:
        return {
            "scheduler_tier": "plugin-family-search",
            "status": "skipped",
            "reason": "family_search_capability_missing",
            "rank_growth": 0,
        }
    if source_pool_id is None or source_candidate_id is None:
        return {
            "scheduler_tier": "plugin-family-search",
            "status": "error",
            "reason": "stored_pool_candidate_identity_missing",
            "rank_growth": 0,
        }

    if E is None:
        curve_row = get_curve(db, int(curve_id))
        if curve_row is None or not curve_row["a_invariants_json"]:
            return {
                "scheduler_tier": "plugin-family-search",
                "status": "error",
                "reason": "stored_curve_model_unavailable",
                "rank_growth": 0,
            }
        E = EllipticCurve(
            QQ,
            [QQ(str(value)) for value in json.loads(curve_row["a_invariants_json"])],
        )

    candidate_row = db.execute(
        "SELECT * FROM candidates WHERE id=? AND pool_id=?",
        (int(source_candidate_id), int(source_pool_id)),
    ).fetchone()
    if candidate_row is None:
        return {
            "scheduler_tier": "plugin-family-search",
            "status": "error",
            "reason": "source_candidate_unavailable",
            "rank_growth": 0,
        }

    adapter = load_adapter(plugin)
    builder = (
        getattr(adapter, "build_family_search_command", None)
        if adapter is not None
        else None
    )
    if not callable(builder):
        return {
            "scheduler_tier": "plugin-family-search",
            "status": "skipped",
            "reason": "family_search_builder_unavailable",
            "rank_growth": 0,
        }

    timeout = max(1, int(getattr(args, "family_search_timeout", 3600) or 3600))
    retry_policy = str(
        getattr(args, "family_search_retry_policy", "manual") or "manual"
    )
    retry_timeout = max(
        1,
        int(
            getattr(args, "family_search_retry_timeout", None)
            or max(7200, timeout)
        ),
    )
    attempt_number = max(
        1, int(getattr(args, "family_search_attempt_number", 1) or 1)
    )
    certificate_timeout = max(
        1, int(getattr(args, "certificate_timeout", 120) or 120)
    )
    exact_candidates = max(
        1, int(getattr(args, "exact_candidates", 64) or 64)
    )
    options = bounded_family_options(
        adapter,
        ratpoints=getattr(args, "ratpoints", None),
        overrides=getattr(args, "family_search_options", None),
        plugin=plugin,
        variant=variant,
    )
    options["plugin_variant"] = variant.id
    options["family_spec"] = variant.family_spec
    options["result_schema"] = "rank42.plugin_geometry_result.v1"
    options["scientific_write_policy"] = "sandbox_core_validated_artifacts_only"

    before = _trial_snapshot(db, curve_id)
    baseline = snapshot_plugin_geometry_subject(db, curve_id)
    print(
        f"[pipeline:plugin-family] plugin={plugin.id}/{variant.id} "
        f"curve=#{curve_id} source_candidate=#{source_candidate_id} "
        f"timeout={timeout}s sandboxed=yes",
        flush=True,
    )

    with plugin_geometry_sandbox(db) as sandbox_db:
        candidate_file = sandbox_db.with_name("plugin-family-candidate.jsonl")
        export_rows_jsonl(
            db,
            int(source_pool_id),
            [candidate_row],
            candidate_file,
        )
        result_path = sandbox_db.with_name("plugin-family-result.json")
        command = builder(
            python=sys.executable,
            db=str(sandbox_db),
            candidate_file=candidate_file,
            options=options,
        )
        try:
            command, feature_audit = apply_search_command_features(
                args.project_root,
                db,
                command,
                family_plugin=plugin,
                kind="family_search",
                metadata={
                    "plugin_id": plugin.id,
                    "plugin_variant": variant.id,
                    "curve_id": int(curve_id),
                    "candidate_pool_id": int(source_pool_id),
                    "candidate_id": int(source_candidate_id),
                    "source": "pipeline_plugin_family_search",
                    "scientific_write_policy": (
                        "sandbox_core_validated_artifacts_only"
                    ),
                },
                plugin_ids=getattr(args, "feature_plugin_ids", None),
            )
            if feature_audit:
                print(
                    "[pipeline:plugin-family features] "
                    + json.dumps(feature_audit, sort_keys=True),
                    flush=True,
                )
        except Exception as exc:
            print(
                f"[pipeline:plugin-family features] host inconclusive: {exc!r}; "
                "using unmodified plugin command",
                flush=True,
            )

        command = force_command_database(command, sandbox_db)
        rc, runtime, execution_error = _run_bounded_command(
            command,
            timeout=timeout,
            env=plugin_geometry_environment(result_path),
        )

        typed_result = None
        artifact_error = None
        if result_path.is_file():
            try:
                typed_result = load_plugin_geometry_result(result_path)
            except Exception as exc:
                artifact_error = repr(exc)

        sandbox_delta = None
        try:
            sandbox_delta = legacy_sandbox_geometry_result(
                sandbox_db,
                baseline=baseline,
                plugin_id=plugin.id,
                plugin_version=getattr(plugin, "version", None),
                variant_id=variant.id,
            )
        except Exception as exc:
            if artifact_error is None and typed_result is None:
                artifact_error = repr(exc)

        if artifact_error is None and typed_result is None:
            typed_result = sandbox_delta
        elif artifact_error is None and typed_result is not None:
            metadata = dict(typed_result.get("metadata") or {})
            metadata.setdefault("result_source", "typed_artifact")
            metadata["scientific_write_policy"] = "core_validated_artifacts_only"
            if sandbox_delta is not None:
                for key, value in (sandbox_delta.get("metadata") or {}).items():
                    if key.startswith("sandbox_"):
                        metadata[key] = value
            typed_result["metadata"] = metadata

    execution_status = (
        "timeout"
        if execution_error
        else ("completed" if rc == 0 else "error")
    )
    applied = None
    if typed_result is not None and artifact_error is None:
        try:
            applied = apply_plugin_geometry_result(
                db,
                curve_id=int(curve_id),
                E=E,
                plugin_id=plugin.id,
                result=typed_result,
                search_ref=(
                    f"plugin-family:{plugin.id}:{variant.id}:"
                    f"{int(source_candidate_id)}:{attempt_number}"
                ),
                certificate_timeout=certificate_timeout,
                exact_candidates=exact_candidates,
            )
        except Exception as exc:
            artifact_error = repr(exc)

    accepted = int((applied or {}).get("exact_points_accepted") or 0)
    if execution_status == "completed" and artifact_error is None:
        status = str((typed_result or {}).get("status") or "completed")
    elif accepted > 0:
        status = "partial"
    else:
        status = execution_status if execution_status != "completed" else "error"

    after = _trial_snapshot(db, curve_id)
    growth = max(
        0,
        int(after["rigorous_lower"]) - int(before["rigorous_lower"]),
    )
    result_metadata = dict((applied or {}).get("result_metadata") or {})
    print(
        f"[pipeline:plugin-family] {status} runtime={runtime:.1f}s "
        f"rank>={after['rigorous_lower']} growth={growth}",
        flush=True,
    )
    return {
        "scheduler_tier": "plugin-family-search",
        "status": status,
        "exit_code": rc,
        "runtime_seconds": runtime,
        "execution_status": execution_status,
        "execution_error": execution_error,
        "artifact_error": artifact_error,
        "result_schema": (
            None if typed_result is None else typed_result.get("schema")
        ),
        "result_source": (
            None if applied is None else applied.get("result_source")
        ),
        "scientific_write_policy": "sandbox_core_validated_artifacts_only",
        "legacy_raw_db_access": True,
        "legacy_raw_db_scope": "temporary_sandbox_only",
        "sandbox_scientific_writes_discarded": {
            key: value
            for key, value in result_metadata.items()
            if key.startswith("sandbox_")
        },
        "exact_points_accepted": accepted,
        "points_rejected": int((applied or {}).get("points_rejected") or 0),
        "point_rejection_samples": list(
            (applied or {}).get("point_rejection_samples") or []
        ),
        "verified_plugin_upper": (
            None if applied is None else applied.get("verified_plugin_upper")
        ),
        "rigorous_upper_verification": (
            None
            if applied is None
            else applied.get("rigorous_upper_verification")
        ),
        "rank_growth": growth,
        "rigorous_lower": after["rigorous_lower"],
        "rigorous_upper": after["rigorous_upper"],
        "source_pool_id": int(source_pool_id),
        "source_candidate_id": int(source_candidate_id),
        "attempt_complete": True,
        "mathematical_outcome": status,
        "attempt_number": attempt_number,
        "attempt_timeout": timeout,
        "retry_policy": retry_policy,
        "retry_timeout": retry_timeout,
        "retryable": status in {
            "timeout", "error", "partial", "inconclusive"
        },
    }


__all__ = ["bounded_family_options", "run_plugin_family_search"]
