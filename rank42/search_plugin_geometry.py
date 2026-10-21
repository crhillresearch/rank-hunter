"""Shared Plugin Geometry Search host for Auto and Pipeline.

The host preserves legacy plugin CLI compatibility through a temporary SQLite
sandbox while accepting scientific changes only through core-validated typed
artifacts or a validated sandbox point delta.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

from sage.all import QQ, EllipticCurve

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
from rank42.rank_evidence import apply_reduced_rank_state
from rank42.search_config import adapter_option_defaults, adapter_search_option_defs


def _adapter_default_options(
    adapter,
    *,
    context,
    plugin=None,
    variant=None,
):
    if plugin is not None and variant is not None:
        return adapter_option_defaults(
            adapter_search_option_defs(
                plugin,
                variant,
                adapter,
                context=context,
            )
        )
    getter = getattr(adapter, "search_options", None)
    if not callable(getter):
        return {}
    out = {}
    for rec in getter(context=context) or []:
        if isinstance(rec, dict) and rec.get("key") and "default" in rec:
            out[str(rec["key"])] = rec.get("default")
    return out


def bounded_geometry_options(
    adapter,
    *,
    target_rank,
    ratpoints,
    certificate_timeout,
    exact_candidates,
    overrides=None,
    plugin=None,
    variant=None,
):
    options = _adapter_default_options(
        adapter,
        context="target",
        plugin=plugin,
        variant=variant,
    )
    if overrides is not None:
        if not isinstance(overrides, dict):
            raise ValueError("plugin geometry option overrides must be a mapping")
        options.update(overrides)
    options["limit"] = 1
    options["ratpoints"] = ratpoints
    options["ratpoints_backend"] = (
        "GPU" if ratpoints and "gpu" in str(ratpoints).lower() else "CPU"
    )
    if "target_lower" in options:
        options["target_lower"] = int(target_rank)
    if "target_rank" in options:
        options["target_rank"] = int(target_rank)
    options["exact_candidates"] = min(
        int(options.get("exact_candidates") or exact_candidates),
        int(exact_candidates),
    )
    options["certificate_timeout"] = min(
        int(options.get("certificate_timeout") or certificate_timeout),
        int(certificate_timeout),
    )
    for key, cap in {
        "timeout": 15,
        "construction_timeout": 90,
        "baseline_timeout": min(120, int(certificate_timeout)),
        "selmer_timeout": 30,
        "covering_timeout": 90,
    }.items():
        if key in options and options[key] is not None:
            try:
                options[key] = min(int(options[key]), int(cap))
            except (TypeError, ValueError):
                pass
    for key, cap in {
        "charts": 32,
        "subgroup_scan": 256,
        "anchor_charts": 31,
    }.items():
        if key in options and options[key] is not None:
            try:
                options[key] = min(int(options[key]), int(cap))
            except (TypeError, ValueError):
                pass
    return options


def geometry_rank_selected(rank_order, keep):
    rank_order = int(rank_order or 0)
    return rank_order == 0 or 0 < rank_order <= int(keep)


def _run_bounded_command(command, *, timeout, env=None):
    started = time.monotonic()
    proc = subprocess.Popen(command, start_new_session=True, env=env)
    try:
        return (
            proc.wait(timeout=max(1, int(timeout))),
            time.monotonic() - started,
            None,
        )
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
        return None, time.monotonic() - started, "timeout"


def _trial_snapshot(db, curve_id):
    row = get_curve(db, curve_id)
    state = apply_reduced_rank_state(db, curve_id)
    point_count = db.execute(
        "SELECT COUNT(*) AS n FROM points WHERE curve_id=? AND exact_verified=1",
        (int(curve_id),),
    ).fetchone()["n"]
    return {
        "rigorous_lower": int(state["rigorous_lower"]),
        "rigorous_upper": state["rigorous_upper"],
        "exact_rank": state["exact_rank"],
        "exact_points": int(point_count or 0),
        "curve_status": str(row["status"] or ""),
    }


def run_plugin_geometry_target(
    args,
    db,
    *,
    curve_id,
    plugin,
    variant,
    rank_order,
    E=None,
):
    """Run family-native target geometry without granting live scientific writes."""
    if not args.geometry_first or not geometry_rank_selected(
        rank_order, args.geometry_keep
    ):
        return None
    if "target_search" not in plugin.capabilities:
        return None

    if E is None:
        curve_row = get_curve(db, int(curve_id))
        if curve_row is None or not curve_row["a_invariants_json"]:
            return {
                "scheduler_tier": "plugin-geometry",
                "status": "error",
                "reason": "stored_curve_model_unavailable",
                "rank_growth": 0,
            }
        E = EllipticCurve(
            QQ,
            [
                QQ(str(value))
                for value in json.loads(curve_row["a_invariants_json"])
            ],
        )

    adapter = load_adapter(plugin)
    builder = (
        getattr(adapter, "build_target_search_command", None)
        if adapter else None
    )
    if not callable(builder):
        return None

    timeout = max(1, int(args.geometry_timeout))
    retry_policy = str(
        getattr(args, "geometry_retry_policy", "manual") or "manual"
    )
    retry_timeout = max(
        1,
        int(
            getattr(args, "geometry_retry_timeout", None)
            or max(600, timeout)
        ),
    )
    attempt_number = max(
        1, int(getattr(args, "geometry_attempt_number", 1) or 1)
    )
    options = bounded_geometry_options(
        adapter,
        target_rank=args.target_rank,
        ratpoints=args.ratpoints,
        certificate_timeout=args.certificate_timeout,
        exact_candidates=args.exact_candidates,
        overrides=getattr(args, "geometry_options", None),
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
        f"[auto:geometry] plugin={plugin.id}/{variant.id} curve=#{curve_id} "
        f"rank_order={rank_order} timeout={timeout}s sandboxed=yes",
        flush=True,
    )

    with plugin_geometry_sandbox(db) as sandbox_db:
        result_path = sandbox_db.with_name("plugin-geometry-result.json")
        command = builder(
            python=sys.executable,
            db=str(sandbox_db),
            curve_id=int(curve_id),
            options=options,
        )
        try:
            command, feature_audit = apply_search_command_features(
                args.project_root,
                db,
                command,
                family_plugin=plugin,
                kind="target_plugin",
                metadata={
                    "plugin_id": plugin.id,
                    "plugin_variant": variant.id,
                    "curve_id": int(curve_id),
                    "source": "geometry_or_pipeline",
                    "scientific_write_policy": (
                        "sandbox_core_validated_artifacts_only"
                    ),
                },
                plugin_ids=getattr(args, "feature_plugin_ids", None),
            )
            if feature_audit:
                print(
                    "[auto:geometry features] "
                    + json.dumps(feature_audit, sort_keys=True),
                    flush=True,
                )
        except Exception as exc:
            print(
                f"[auto:geometry features] host inconclusive: {exc!r}; "
                "using unmodified plugin command",
                flush=True,
            )
        command = force_command_database(command, sandbox_db)
        env = plugin_geometry_environment(result_path)
        rc, runtime, execution_error = _run_bounded_command(
            command,
            timeout=timeout,
            env=env,
        )

        typed_result = None
        artifact_error = None
        typed_artifact_present = result_path.is_file()
        if typed_artifact_present:
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
            metadata["scientific_write_policy"] = (
                "core_validated_artifacts_only"
            )
            if sandbox_delta is not None:
                for key, value in (
                    sandbox_delta.get("metadata") or {}
                ).items():
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
                curve_id=curve_id,
                E=E,
                plugin_id=plugin.id,
                result=typed_result,
                search_ref=(
                    f"plugin-geometry:{plugin.id}:{variant.id}:"
                    f"{int(curve_id)}:{attempt_number}"
                ),
                certificate_timeout=int(args.certificate_timeout),
                exact_candidates=int(args.exact_candidates),
            )
        except Exception as exc:
            artifact_error = repr(exc)

    accepted = int((applied or {}).get("exact_points_accepted") or 0)
    if execution_status == "completed" and artifact_error is None:
        status = str(typed_result.get("status") or "completed")
    elif accepted > 0:
        status = "partial"
    else:
        status = (
            execution_status
            if execution_status != "completed"
            else "error"
        )

    after = _trial_snapshot(db, curve_id)
    growth = max(
        0,
        int(after["rigorous_lower"]) - int(before["rigorous_lower"]),
    )
    result_source = None
    result_metadata = {}
    if applied is not None:
        result_source = applied.get("result_source")
        result_metadata = dict(applied.get("result_metadata") or {})

    print(
        f"[auto:geometry] {status} runtime={runtime:.1f}s "
        f"rank>={after['rigorous_lower']} growth={growth} "
        f"source={result_source or '-'}",
        flush=True,
    )
    return {
        "scheduler_tier": "plugin-geometry",
        "status": status,
        "exit_code": rc,
        "runtime_seconds": runtime,
        "execution_status": execution_status,
        "execution_error": execution_error,
        "artifact_error": artifact_error,
        "result_schema": (
            None if typed_result is None else typed_result.get("schema")
        ),
        "result_source": result_source,
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


__all__ = [
    "bounded_geometry_options",
    "geometry_rank_selected",
    "run_plugin_geometry_target",
]
