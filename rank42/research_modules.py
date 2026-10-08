"""Research-grade Builder modules layered on exact Rank Hunter primitives.

The functions here deliberately separate generic orchestration from mathematics
that must remain family/engine owned.  Plugin hooks may supply exact coverings,
higher-descent points/bounds, or p-adic covering points, but core validates every
returned rational point on the stored curve and keeps proof metadata explicit.
"""
from __future__ import annotations

import json
import time

from sage.all import QQ

from rank42.auto_point_search import certify_ledger_growth
from rank42.classical_descent import (
    ClassicalDescentFailure,
    ClassicalDescentTimeout,
    run_classical_descent,
)
from rank42.coverings import SCHEMA as COVERING_SCHEMA, map_quartic_point, validate_covering
from rank42.covering_search import search_stored_covering
from rank42.curve_research_state import get_curve_research_state
from rank42.independence_check import certify_stored_independence
from rank42.lattice import LatticeFailure, LatticeTimeout, run_lattice_build
from rank42.lattice_store import (
    completed_covering_search_attempt,
    list_lattices,
    record_covering_search_attempt,
    store_covering,
    store_lattice,
    update_covering,
)
from rank42.plugin_hook_runner import run_isolated_plugin_hook
from rank42.plugin_padic_result import (
    SCHEMA as PADIC_RESULT_SCHEMA,
    validate_padic_covering_result,
)
from rank42.plugin_rank_certificate import verify_plugin_rigorous_upper_certificate
from rank42.plugins import load_adapter
from rank42.point_promotion import promote_rigorous_rank_interval
from rank42.points import rigorous_witness_basis, upsert_point
from rank42.rank_evidence import apply_reduced_rank_state, record_rank_evidence
from rank42.ratpoints import RatpointsFailure, RatpointsTimeout, run_ratpoints
from rank42.saturation import saturate_curve


def _point_json(P):
    return [str(P[0]), str(P[1])]


def _rigorous_lower(db, curve_id):
    state = get_curve_research_state(db, int(curve_id))
    return int(state["rigorous_lower"])


def geometry_branch_summary(branches):
    """Reduce independent Geometry branch records to one honest stage outcome."""
    branches = [dict(branch) for branch in (branches or [])]
    counts = {
        "planned_branches": len(branches),
        "attempted_branches": 0,
        "completed_branches": 0,
        "partial_branches": 0,
        "timeout_branches": 0,
        "error_branches": 0,
        "unsupported_branches": 0,
        "inconclusive_branches": 0,
        "skipped_branches": 0,
    }
    for branch in branches:
        status = str(branch.get("status") or "inconclusive")
        if status in {"skipped", "skipped-after-growth"}:
            counts["skipped_branches"] += 1
            continue
        counts["attempted_branches"] += 1
        if status == "completed":
            counts["completed_branches"] += 1
        elif status == "partial":
            counts["partial_branches"] += 1
        elif status == "timeout":
            counts["timeout_branches"] += 1
        elif status == "error":
            counts["error_branches"] += 1
        elif status == "unsupported":
            counts["unsupported_branches"] += 1
        else:
            counts["inconclusive_branches"] += 1

    planned = counts["planned_branches"]
    completed = counts["completed_branches"]
    skipped = counts["skipped_branches"]
    partials = counts["partial_branches"]
    timeouts = counts["timeout_branches"]
    errors = counts["error_branches"]
    unsupported = counts["unsupported_branches"]
    inconclusive = counts["inconclusive_branches"]
    incomplete = partials + timeouts + errors + unsupported + inconclusive

    if planned == 0:
        outcome = "inconclusive"
    elif completed > 0 and incomplete == 0:
        outcome = "completed"
    elif completed > 0 or partials > 0:
        outcome = "partial"
    elif timeouts > 0 and errors == 0 and inconclusive == 0:
        outcome = "timeout"
    elif errors > 0 and timeouts == 0 and inconclusive == 0:
        outcome = "error"
    elif unsupported > 0 and timeouts == 0 and errors == 0 and inconclusive == 0:
        outcome = "unsupported"
    else:
        outcome = "inconclusive"

    covered = completed + skipped
    counts.update({
        "incomplete_branches": incomplete,
        "coverage_fraction": (
            float(covered) / float(planned) if planned > 0 else 0.0
        ),
        "complete": outcome == "completed",
    })
    return {
        "status": outcome,
        "branch_coverage": counts,
        "retryable": bool(partials or timeouts or errors or inconclusive),
    }


def _safe_hook(adapter, name, payload):
    hook = getattr(adapter, name, None) if adapter is not None else None
    if not callable(hook):
        return None
    return hook(dict(payload))


def _persist_hook_points(
    db,
    *,
    curve_id,
    E,
    points,
    source,
    search_ref,
    metadata,
    certificate_timeout,
    exact_candidates,
):
    accepted = 0
    rejected = 0
    rejection_samples = []
    for index, raw in enumerate(points or [], 1):
        try:
            if not isinstance(raw, (list, tuple)) or len(raw) < 2:
                raise ValueError("point must be [x,y]")
            P = E(QQ(str(raw[0])), QQ(str(raw[1])))
            if P.is_zero():
                continue
        except Exception as exc:
            rejected += 1
            if len(rejection_samples) < 5:
                rejection_samples.append({
                    "index": int(index),
                    "raw": repr(raw)[:240],
                    "error_class": type(exc).__name__,
                    "error": str(exc)[:240],
                })
            continue
        upsert_point(
            db,
            curve_id=int(curve_id),
            x=P[0],
            y=P[1],
            source=str(source),
            role="candidate_extra",
            exact_verified=True,
            independence_status="unknown",
            rigorous_independent=False,
            search_ref=str(search_ref),
            metadata={**dict(metadata or {}), "hook_point_index": index},
        )
        accepted += 1

    cert = certify_ledger_growth(
        db,
        curve_id=int(curve_id),
        E=E,
        source=str(source),
        search_ref=f"{search_ref}:exact",
        certificate_timeout=max(1, int(certificate_timeout)),
        max_candidates=max(1, int(exact_candidates)),
    )
    return {
        "exact_points_accepted": accepted,
        "points_rejected": rejected,
        "point_rejection_samples": rejection_samples,
        "certificate_attempts": cert.get("attempts", 0),
        "rank_growth": cert.get("growth", 0),
        "rigorous_lower": cert.get("rigorous_lower"),
        "basis_complete": cert.get("basis_complete", True),
    }


def _persist_plugin_upper(
    db,
    *,
    curve_id,
    E,
    plugin,
    result,
    source,
    stage_id,
    known_points,
    certificate_timeout,
):
    certificate = result.get("rigorous_upper_certificate")
    verification = verify_plugin_rigorous_upper_certificate(
        E,
        certificate,
        known_points=list(known_points or []),
        timeout=max(1, int(certificate_timeout)),
    )
    result["_rigorous_upper_verification"] = verification
    if verification.get("verified") is not True:
        return None

    upper = int(verification["rigorous_upper"])
    lower = _rigorous_lower(db, curve_id)
    if upper < lower:
        result["_rigorous_upper_verification"] = {
            **verification,
            "verified": False,
            "reason": "verified_upper_below_current_rigorous_lower",
            "current_rigorous_lower": lower,
        }
        return None

    promotion = promote_rigorous_rank_interval(
        db,
        curve_id=int(curve_id),
        model=E.a_invariants(),
        rigorous_lower=None,
        rigorous_upper=upper,
        certificate=verification["certificate"],
        engine=str(verification.get("engine") or "plugin_certificate_verifier"),
        evidence_type=str(result.get("evidence_type") or "descent"),
        source=str(source),
        engine_version=verification.get("engine_version"),
        points_found=[],
        options={
            "plugin_id": plugin.id,
            "stage_id": stage_id,
            "plugin_engine": result.get("engine"),
            "plugin_engine_version": result.get("engine_version"),
            "plugin_hook_status": result.get("status"),
            "certificate_verifier": verification.get("verifier"),
        },
        metadata={
            "plugin_result": {
                k: v for k, v in result.items()
                if k not in {"points", "_rigorous_upper_verification"}
            },
        },
        elapsed_seconds=verification.get("elapsed_seconds"),
    )
    result["_rigorous_upper_promotion"] = {
        "evidence_id": promotion.get("evidence_id"),
        "rank_inconsistent": bool(promotion.get("rank_inconsistent")),
        "rigorous_upper": promotion.get("rigorous_upper"),
        "exact_rank": promotion.get("exact_rank"),
    }
    if promotion.get("rank_inconsistent"):
        return None
    return upper

def higher_descent_ladder(
    db,
    *,
    context,
    config,
    run_id,
    stage_index,
    certificate_timeout,
    exact_candidates,
):
    """Run built-in rigorous 2-descent, then optional true higher-descent hooks."""
    curve_id = int(context["curve_id"])
    E = context["E"]
    plugin = context.get("plugin")
    levels = [int(x) for x in (config.get("levels") or [2, 4, 8, 12])]
    timeout = max(1, int(config.get("timeout") or 120))
    stop_on_growth = bool(config.get("stop_on_growth", False))
    before = _rigorous_lower(db, curve_id)
    branches = []
    best_upper = None

    basis, required, complete = rigorous_witness_basis(db, curve_id, E)
    if 2 in levels:
        try:
            result = run_classical_descent(
                E.a_invariants(),
                basis if complete else [],
                mode="mwrank_coverings",
                timeout=timeout,
                first_limit=int(config.get("first_limit") or 20),
                second_limit=int(config.get("second_limit") or 10),
            )
            upper = result.get("rigorous_upper")
            if upper is not None and int(upper) >= before:
                record_rank_evidence(
                    db,
                    curve_id=curve_id,
                    model=E.a_invariants(),
                    data={
                        "engine": "eclib_mwrank_full_two_descent",
                        "evidence_type": "descent",
                        "status": "completed",
                        "rigorous": True,
                        "rigorous_lower": None,
                        "rigorous_upper": int(upper),
                        "exact_rank": None,
                        "assumptions": [],
                        "points_found": list(result.get("points") or []),
                        "options": {
                            "source": "builder_higher_descent_ladder",
                            "pipeline_run_id": int(run_id),
                            "stage_index": int(stage_index),
                            "descent_level": 2,
                        },
                        "elapsed_seconds": result.get("runtime_seconds"),
                    },
                )
                apply_reduced_rank_state(db, curve_id)
                best_upper = int(upper)
            point_result = _persist_hook_points(
                db,
                curve_id=curve_id,
                E=E,
                points=result.get("points") or [],
                source="pipeline_higher_descent",
                search_ref=f"pipeline:{run_id}:higher-descent:{stage_index}:2",
                metadata={"descent_level": 2, "engine": "mwrank_coverings"},
                certificate_timeout=certificate_timeout,
                exact_candidates=exact_candidates,
            )
            branches.append({
                "level": 2,
                "status": "completed",
                "rigorous_upper": upper,
                **point_result,
            })
        except ClassicalDescentTimeout as exc:
            branches.append({"level": 2, "status": "timeout", "error": str(exc)})
        except ClassicalDescentFailure as exc:
            branches.append({"level": 2, "status": "error", "error": str(exc)})

    for level in levels:
        if level == 2:
            continue
        if stop_on_growth and _rigorous_lower(db, curve_id) > before:
            branches.append({"level": level, "status": "skipped-after-growth"})
            continue
        payload = {
            "stage_id": "higher_descent_ladder",
            "descent_level": int(level),
            "curve_id": curve_id,
            "a_invariants": [str(x) for x in E.a_invariants()],
            "parameter": str(context.get("parameter")),
            "known_points": [_point_json(P) for P in basis] if complete else [],
            "timeout": timeout,
            "config": dict(config or {}),
        }
        result = run_isolated_plugin_hook(
            plugin,
            "run_pipeline_higher_descent",
            payload,
            timeout=timeout,
        )
        result = dict(result)
        status = str(result.get("status") or "completed")
        upper = None
        if plugin is not None:
            upper = _persist_plugin_upper(
                db,
                curve_id=curve_id,
                E=E,
                plugin=plugin,
                result=result,
                source="builder_higher_descent_ladder",
                stage_id=f"higher_descent_{level}",
                known_points=basis if complete else [],
                certificate_timeout=certificate_timeout,
            )
        if upper is not None:
            best_upper = upper if best_upper is None else min(best_upper, upper)
        if status in {"timeout", "error", "unsupported"}:
            branches.append({
                "level": level,
                "status": status,
                "reason": result.get("reason"),
                "error": result.get("error"),
                "rigorous_upper": upper,
                "hook_result": {
                    k: v for k, v in result.items()
                    if k not in {"points"}
                },
            })
            continue
        point_result = _persist_hook_points(
            db,
            curve_id=curve_id,
            E=E,
            points=result.get("points") or [],
            source="pipeline_higher_descent",
            search_ref=f"pipeline:{run_id}:higher-descent:{stage_index}:{level}",
            metadata={
                "descent_level": level,
                "plugin_id": None if plugin is None else plugin.id,
            },
            certificate_timeout=certificate_timeout,
            exact_candidates=exact_candidates,
        )
        branches.append({
            "level": level,
            "status": status,
            "rigorous_upper": upper,
            "hook_result": {
                k: v for k, v in result.items()
                if k not in {"points"}
            },
            **point_result,
        })

    after = _rigorous_lower(db, curve_id)
    summary = geometry_branch_summary(branches)
    retry_policy = str(config.get("retry_policy") or "manual")
    retry_timeout = max(
        1, int(config.get("retry_timeout") or max(300, timeout))
    )
    return {
        "status": summary["status"],
        "levels": levels,
        "branches": branches,
        "branch_coverage": summary["branch_coverage"],
        "retryable": summary["retryable"],
        "rank_growth": max(0, after - before),
        "rigorous_lower": after,
        "best_rigorous_upper": best_upper,
        "attempt_complete": True,
        "mathematical_outcome": summary["status"],
        "attempt_number": max(1, int(config.get("_attempt_number") or 1)),
        "attempt_timeout": timeout,
        "retry_policy": retry_policy,
        "retry_timeout": retry_timeout,
        "higher_levels_require_real_engine_hook": True,
        "timeouts_are_inconclusive": True,
    }


def _covering_row_to_data(row, *, height):
    return validate_covering({
        "schema": str(row["schema_version"]),
        "curve_id": int(row["curve_id"]),
        "lattice_id": row["lattice_id"],
        "hole_id": row["hole_id"],
        "quartic": {
            **json.loads(row["quartic_json"] or "{}"),
            "height": int(height),
        },
        "map": {"x": row["map_x"], "y": row["map_y"]},
        "metadata": json.loads(row["metadata_json"] or "{}"),
    })


def _import_plugin_coverings(db, *, context, config):
    """Import exact covering search objects with explicit plugin diagnostics."""
    plugin = context.get("plugin")
    timeout = max(
        1,
        int(
            config.get("derive_timeout")
            or config.get("timeout")
            or 30
        ),
    )
    diagnostic = {
        "hook_name": "derive_pipeline_coverings",
        "timeout_seconds": timeout,
        "plugin_id": None if plugin is None else plugin.id,
        "records_returned": 0,
        "coverings_stored": 0,
        "invalid_coverings": 0,
        "map_schema_failures": 0,
        "rejections": [],
        "stored_covering_ids": [],
        "selmer_class_verified": False,
        "provenance_scope": "exact_covering_only",
    }
    if plugin is None:
        return {
            **diagnostic,
            "status": "capability_absent",
            "reason": "no active family plugin for covering derivation",
        }

    payload = {
        "stage_id": "selmer_element_fanout",
        "surface_label": "exact_covering_fanout",
        "curve_id": int(context["curve_id"]),
        "a_invariants": [str(x) for x in context["E"].a_invariants()],
        "parameter": str(context.get("parameter")),
        "config": dict(config or {}),
    }
    hook_result = run_isolated_plugin_hook(
        plugin,
        "derive_pipeline_coverings",
        payload,
        timeout=timeout,
    )
    hook_result = dict(hook_result or {})
    hook_status = str(hook_result.get("status") or "completed")
    diagnostic["hook_result"] = {
        k: v for k, v in hook_result.items() if k != "coverings"
    }

    if hook_status == "unsupported":
        return {
            **diagnostic,
            "status": "capability_absent",
            "reason": hook_result.get("reason")
            or "plugin does not provide covering derivation",
        }
    if hook_status in {"timeout", "error"}:
        return {
            **diagnostic,
            "status": hook_status,
            "reason": hook_result.get("reason"),
            "error": hook_result.get("error"),
        }

    records = hook_result.get("coverings")
    if records is None:
        records = []
    if not isinstance(records, (list, tuple)):
        return {
            **diagnostic,
            "status": "error",
            "reason": "covering hook result must contain a coverings list",
            "result_type": type(records).__name__,
        }

    diagnostic["records_returned"] = len(records)
    for index, rec in enumerate(records, 1):
        if not isinstance(rec, dict):
            diagnostic["invalid_coverings"] += 1
            diagnostic["rejections"].append({
                "index": index,
                "kind": "invalid_covering",
                "error": "covering record must be an object",
            })
            continue

        data = dict(rec)
        data.setdefault("schema", COVERING_SCHEMA)
        data["curve_id"] = int(context["curve_id"])
        metadata = dict(data.get("metadata") or {})
        metadata["rank42_core_provenance"] = {
            "kind": "exact_covering",
            "selmer_class_verified": False,
            "plugin_id": plugin.id,
            "hook": "derive_pipeline_coverings",
        }
        data["metadata"] = metadata

        try:
            validate_covering(data)
        except Exception as exc:
            message = str(exc)
            kind = (
                "map_schema_failure"
                if "map.x" in message or "map.y" in message
                else "invalid_covering"
            )
            if kind == "map_schema_failure":
                diagnostic["map_schema_failures"] += 1
            else:
                diagnostic["invalid_coverings"] += 1
            diagnostic["rejections"].append({
                "index": index,
                "kind": kind,
                "error": message,
            })
            continue

        try:
            row = store_covering(db, data)
        except Exception as exc:
            diagnostic["invalid_coverings"] += 1
            diagnostic["rejections"].append({
                "index": index,
                "kind": "covering_store_error",
                "error": repr(exc),
            })
            continue

        diagnostic["coverings_stored"] += 1
        diagnostic["stored_covering_ids"].append(int(row["id"]))

    rejected = (
        diagnostic["invalid_coverings"]
        + diagnostic["map_schema_failures"]
    )
    if diagnostic["coverings_stored"] and rejected:
        status = "partial"
        reason = "some plugin coverings were rejected"
    elif diagnostic["coverings_stored"]:
        status = "completed"
        reason = None
    elif diagnostic["records_returned"]:
        status = "invalid_coverings"
        reason = "plugin returned no valid exact coverings"
    else:
        status = "no_coverings_derived"
        reason = "plugin covering hook completed without coverings"

    return {
        **diagnostic,
        "status": status,
        "reason": reason,
    }

def selmer_element_fanout(
    db,
    *,
    context,
    config,
    ratpoints,
    run_id,
    stage_index,
    certificate_timeout,
    exact_candidates,
):
    """Search exact stored/plugin-supplied coverings as independent branches.

    The legacy stage id remains selmer_element_fanout for saved Pipeline
    compatibility. Generic covering records do not certify Selmer-class
    provenance.
    """
    curve_id = int(context["curve_id"])
    E = context["E"]
    covering_import = _import_plugin_coverings(
        db,
        context=context,
        config=config,
    )
    max_coverings = max(1, int(config.get("max_coverings") or 16))
    height = max(1, int(config.get("height") or 100000))
    timeout = max(1, int(config.get("timeout") or 30))
    use_covering_plans = bool(config.get("use_covering_plans", True))
    one_point = bool(config.get("one_point", False))
    retry_policy = str(config.get("retry_policy") or "manual")
    retry_timeout = max(
        1, int(config.get("retry_timeout") or max(120, timeout))
    )
    rows = db.execute(
        """SELECT * FROM coverings
           WHERE curve_id=? AND status IN ('ready','searched','mapped')
           ORDER BY id LIMIT ?""",
        (curve_id, max_coverings),
    ).fetchall()
    before = _rigorous_lower(db, curve_id)
    branches = []
    total_points = 0

    for row in rows:
        covering_id = int(row["id"])
        branch_height = height
        branch_timeout = timeout
        applied_plan = None
        if use_covering_plans:
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
                applied_plan = metadata.get("covering_local_height_plan", {}).get(
                    "height_plan"
                )
                if isinstance(applied_plan, dict) and applied_plan.get(
                    "recommended_search", True
                ):
                    branch_height = max(
                        1, int(applied_plan.get("recommended_height") or height)
                    )
                    branch_timeout = max(
                        1, int(applied_plan.get("recommended_timeout") or timeout)
                    )
                else:
                    applied_plan = None
            except Exception:
                applied_plan = None

        branch = search_stored_covering(
            db,
            row=row,
            E=E,
            height=branch_height,
            timeout=branch_timeout,
            ratpoints=ratpoints,
            one_point=one_point,
            run_id=run_id,
            stage_index=stage_index,
            stage_id="selmer_element_fanout",
            applied_plan=applied_plan,
            resume_completed=True,
            source="pipeline_exact_covering_fanout",
            run_search=run_ratpoints,
        )
        total_points += int(branch.get("mapped_points") or 0)
        branches.append(branch)

    cert = certify_ledger_growth(
        db,
        curve_id=curve_id,
        E=E,
        source="pipeline_exact_covering_fanout",
        search_ref=f"pipeline:{run_id}:covering-fanout:{stage_index}:exact",
        certificate_timeout=max(1, int(certificate_timeout)),
        max_candidates=max(1, int(exact_candidates)),
    )
    after = _rigorous_lower(db, curve_id)
    summary = geometry_branch_summary(branches)
    if rows:
        outer_status = summary["status"]
        outer_reason = (
            None
            if outer_status == "completed"
            else f"covering_branch_coverage_{outer_status}"
        )
        retryable = summary["retryable"]
    else:
        import_status = str(covering_import.get("status") or "")
        if import_status == "timeout":
            outer_status = "timeout"
            outer_reason = "covering_derivation_timeout"
            retryable = True
        elif import_status == "error":
            outer_status = "error"
            outer_reason = "covering_derivation_error"
            retryable = True
        elif import_status == "invalid_coverings":
            outer_status = "error"
            outer_reason = "invalid_plugin_coverings"
            retryable = True
        elif import_status == "capability_absent":
            outer_status = "inconclusive"
            outer_reason = "covering_derivation_capability_absent"
            retryable = False
        elif import_status == "no_coverings_derived":
            outer_status = "inconclusive"
            outer_reason = "no_exact_coverings_derived"
            retryable = False
        else:
            outer_status = "inconclusive"
            outer_reason = "no_exact_coverings_available"
            retryable = False

    return {
        "status": outer_status,
        "reason": outer_reason,
        "module_label": "Exact Covering Fan-Out",
        "legacy_stage_id": "selmer_element_fanout",
        "covering_provenance_scope": "exact_covering_only",
        "selmer_class_verified": False,
        "covering_import": covering_import,
        "coverings_seen": len(rows),
        "branches": branches,
        "branch_coverage": summary["branch_coverage"],
        "retryable": retryable,
        "exact_points": total_points,
        "rank_growth": max(0, after - before),
        "rigorous_lower": after,
        "certificate_attempts": cert.get("attempts", 0),
        "attempt_complete": True,
        "mathematical_outcome": outer_status,
        "attempt_number": max(1, int(config.get("_attempt_number") or 1)),
        "attempt_timeout": (
            int(covering_import.get("timeout_seconds") or timeout)
            if outer_reason == "covering_derivation_timeout"
            else timeout
        ),
        "retry_policy": retry_policy,
        "retry_timeout": retry_timeout,
        "timeouts_are_inconclusive": True,
    }

def padic_covering_point_search(
    db,
    *,
    context,
    config,
    run_id,
    stage_index,
    certificate_timeout,
    exact_candidates,
):
    """Run one isolated, typed p-adic covering-point plugin search."""
    curve_id = int(context["curve_id"])
    E = context["E"]
    plugin = context.get("plugin")
    covering_ids = [
        int(row["id"]) for row in db.execute(
            "SELECT id FROM coverings WHERE curve_id=? ORDER BY id",
            (curve_id,),
        ).fetchall()
    ]
    payload = {
        "stage_id": "padic_covering_search",
        "result_schema": PADIC_RESULT_SCHEMA,
        "curve_id": curve_id,
        "a_invariants": [str(x) for x in E.a_invariants()],
        "parameter": str(context.get("parameter")),
        "prime": int(config.get("prime") or 2),
        "precision": int(config.get("precision") or 40),
        "timeout": int(config.get("timeout") or 120),
        "covering_ids": covering_ids,
        "config": dict(config or {}),
    }
    result = run_isolated_plugin_hook(
        plugin,
        "run_pipeline_padic_covering_search",
        payload,
        timeout=payload["timeout"],
    )
    result = dict(result or {})
    status = str(result.get("status") or "completed")
    if status in {"timeout", "error", "unsupported"}:
        return {
            "status": status,
            "reason": (
                "no true p-adic covering engine hook is installed for this family"
                if status == "unsupported"
                else result.get("reason")
            ),
            "error": result.get("error"),
            "prime": payload["prime"],
            "precision": payload["precision"],
            "result_schema": PADIC_RESULT_SCHEMA,
            "timeout_seconds": result.get("timeout_seconds"),
            "runtime_seconds": result.get("runtime_seconds"),
            "hook_result": {
                k: v for k, v in result.items() if k != "points"
            },
            "heuristic_only": False,
        }

    try:
        typed = validate_padic_covering_result(
            result,
            requested_prime=payload["prime"],
            requested_precision=payload["precision"],
            allowed_covering_ids=covering_ids,
        )
    except ValueError as exc:
        return {
            "status": "error",
            "reason": "invalid_padic_result_contract",
            "error": str(exc),
            "prime": payload["prime"],
            "precision": payload["precision"],
            "result_schema": PADIC_RESULT_SCHEMA,
            "hook_result": {
                k: v for k, v in result.items() if k != "points"
            },
            "points_persisted": False,
            "timeouts_are_inconclusive": True,
        }

    before = _rigorous_lower(db, curve_id)
    point_result = _persist_hook_points(
        db,
        curve_id=curve_id,
        E=E,
        points=typed.get("points") or [],
        source="pipeline_padic_covering_search",
        search_ref=f"pipeline:{run_id}:padic-covering:{stage_index}",
        metadata={
            "prime": typed["prime"],
            "precision": typed["precision"],
            "precision_semantics": typed["precision_semantics"],
            "plugin_id": None if plugin is None else plugin.id,
            "engine": typed["engine"],
            "engine_version": typed["engine_version"],
            "algorithm": typed["algorithm"],
            "covering_ids": typed["covering_ids"],
            "local_lifting_bounds": typed["local_lifting_bounds"],
            "completeness": typed["completeness"],
            "search_complete": typed["search_complete"],
            "result_schema": typed["schema"],
        },
        certificate_timeout=certificate_timeout,
        exact_candidates=exact_candidates,
    )
    after = _rigorous_lower(db, curve_id)
    return {
        "status": typed["status"],
        "prime": typed["prime"],
        "precision": typed["precision"],
        "precision_semantics": typed["precision_semantics"],
        "engine": typed["engine"],
        "engine_version": typed["engine_version"],
        "algorithm": typed["algorithm"],
        "covering_ids": typed["covering_ids"],
        "local_lifting_bounds": typed["local_lifting_bounds"],
        "completeness": typed["completeness"],
        "search_complete": typed["search_complete"],
        "result_schema": typed["schema"],
        "hook_result": {
            k: v for k, v in typed.items() if k != "points"
        },
        **point_result,
        "rank_growth": max(0, after - before),
        "rigorous_lower": after,
        "timeouts_are_inconclusive": True,
    }

def saturation_index_recovery(
    db,
    *,
    context,
    config,
    run_id,
    stage_index,
    certificate_timeout,
    exact_candidates,
):
    curve_id = int(context["curve_id"])
    E = context["E"]
    bounds = sorted({int(x) for x in (config.get("prime_bounds") or [7, 31, 127, 509]) if int(x) >= 2})
    timeout = max(1, int(config.get("timeout") or 180))
    stop_on_unit_index = bool(config.get("stop_on_unit_index", False))
    retry_policy = str(config.get("retry_policy") or "manual")
    retry_timeout = max(
        1, int(config.get("retry_timeout") or max(600, timeout))
    )
    attempt_number = max(1, int(config.get("_attempt_number") or 1))
    before = _rigorous_lower(db, curve_id)
    rounds = []
    recovered = 0

    for bound in bounds:
        result = saturate_curve(
            db,
            curve_id,
            max_prime=bound,
            timeout=timeout,
            source=f"pipeline:{run_id}:saturation-index:{stage_index}",
        )
        round_rec = {
            "max_prime": bound,
            "status": result.get("status"),
            "index": result.get("index"),
            "witness_subgroup_regulator": result.get("regulator"),
            "regulator_scope": "bounded_saturated_witness_subgroup",
            "reason": result.get("reason"),
            "error": result.get("error"),
        }
        points = (
            result.get("saturated_points") or []
            if str(result.get("status") or "") == "completed"
            else []
        )
        for index, raw in enumerate(points, 1):
            try:
                P = E(QQ(str(raw[0])), QQ(str(raw[1])))
            except Exception:
                continue
            upsert_point(
                db,
                curve_id=curve_id,
                x=P[0],
                y=P[1],
                source="pipeline_saturation_index_recovery",
                role="candidate_witness",
                exact_verified=True,
                independence_status="unknown",
                rigorous_independent=False,
                search_ref=f"pipeline:{run_id}:saturation-index:{stage_index}:{bound}",
                metadata={
                    "max_prime": bound,
                    "saturation_index": result.get("index"),
                    "basis_position": index,
                },
            )
            recovered += 1
        rounds.append(round_rec)
        if (
            stop_on_unit_index
            and str(result.get("status") or "") == "completed"
            and str(result.get("index")) == "1"
        ):
            break

    cert = certify_ledger_growth(
        db,
        curve_id=curve_id,
        E=E,
        source="pipeline_saturation_index_recovery",
        search_ref=f"pipeline:{run_id}:saturation-index:{stage_index}:exact",
        certificate_timeout=max(1, int(certificate_timeout)),
        max_candidates=max(1, int(exact_candidates)),
    )
    after = _rigorous_lower(db, curve_id)
    statuses = [str(rec.get("status") or "inconclusive") for rec in rounds]
    completed_rounds = sum(status == "completed" for status in statuses)
    timeout_rounds = sum(status == "timeout" for status in statuses)
    error_rounds = sum(status == "error" for status in statuses)
    inconclusive_rounds = sum(
        status not in {"completed", "timeout", "error"}
        for status in statuses
    )
    if not rounds:
        outer_status = "inconclusive"
    elif completed_rounds == len(rounds):
        outer_status = "completed"
    elif completed_rounds:
        outer_status = "partial"
    elif timeout_rounds and not error_rounds and not inconclusive_rounds:
        outer_status = "timeout"
    elif error_rounds and not timeout_rounds and not inconclusive_rounds:
        outer_status = "error"
    else:
        outer_status = "inconclusive"

    return {
        "status": outer_status,
        "prime_bounds": bounds,
        "rounds": rounds,
        "round_coverage": {
            "planned_rounds": len(bounds),
            "attempted_rounds": len(rounds),
            "completed_rounds": completed_rounds,
            "timeout_rounds": timeout_rounds,
            "error_rounds": error_rounds,
            "inconclusive_rounds": inconclusive_rounds,
        },
        "recovered_basis_points": recovered,
        "rank_growth": max(0, after - before),
        "rigorous_lower": after,
        "certificate_attempts": cert.get("attempts", 0),
        "complete_saturation_proved": False,
        "proof_note": "Each round is exact through its stated prime bound; no global index bound is inferred.",
        "attempt_number": attempt_number,
        "attempt_timeout": timeout,
        "attempt_complete": True,
        "mathematical_outcome": outer_status,
        "retry_policy": retry_policy,
        "retry_timeout": retry_timeout,
        "retryable": outer_status in {
            "partial", "timeout", "error", "inconclusive"
        },
    }


def height_lattice_reduction(
    db,
    *,
    context,
    config,
    run_id,
    stage_index,
    certificate_timeout,
    exact_candidates,
):
    curve_id = int(context["curve_id"])
    E = context["E"]
    precision = max(64, int(config.get("precision_bits") or 256))
    timeout = max(1, int(config.get("timeout") or 300))
    retry_policy = str(config.get("retry_policy") or "manual")
    retry_timeout = max(
        1, int(config.get("retry_timeout") or max(900, timeout))
    )
    attempt_number = max(1, int(config.get("_attempt_number") or 1))

    basis, required, complete = rigorous_witness_basis(db, curve_id, E)
    if not complete or not basis:
        return {
            "status": "inconclusive",
            "reason": "incomplete_rigorous_witness_basis",
            "basis_count": len(basis),
            "basis_required": int(required),
            "lattice_status": "not_attempted",
            "certification_status": "not_attempted",
            "precision_bits": precision,
            "attempt_number": attempt_number,
            "attempt_timeout": timeout,
            "retry_policy": retry_policy,
            "retry_timeout": retry_timeout,
            "retryable": True,
        }

    try:
        result = run_lattice_build(
            E.a_invariants(),
            basis,
            precision=precision,
            lll_reduce=True,
            timeout=timeout,
        )
    except LatticeTimeout as exc:
        return {
            "status": "timeout",
            "reason": "height_lattice_timeout",
            "error": str(exc),
            "lattice_status": "timeout",
            "certification_status": "not_attempted",
            "basis_count": len(basis),
            "basis_required": int(required),
            "precision_bits": precision,
            "runtime_seconds": float(timeout),
            "basis_fingerprint": None,
            "attempt_number": attempt_number,
            "attempt_timeout": timeout,
            "retry_policy": retry_policy,
            "retry_timeout": retry_timeout,
            "retryable": True,
            "numerical_lattice_screen_is_not_rank_proof": True,
        }
    except LatticeFailure as exc:
        return {
            "status": "error",
            "reason": "height_lattice_error",
            "error": str(exc),
            "lattice_status": "error",
            "certification_status": "not_attempted",
            "basis_count": len(basis),
            "basis_required": int(required),
            "precision_bits": precision,
            "runtime_seconds": None,
            "basis_fingerprint": None,
            "attempt_number": attempt_number,
            "attempt_timeout": timeout,
            "retry_policy": retry_policy,
            "retry_timeout": retry_timeout,
            "retryable": True,
            "numerical_lattice_screen_is_not_rank_proof": True,
        }

    def point_from_json(raw):
        return E(QQ(str(raw[0])), QQ(str(raw[1])))

    try:
        reduced = [point_from_json(raw) for raw in result["points"]]
        input_points = [
            point_from_json(raw) for raw in result["input_points"]
        ]
    except Exception as exc:
        return {
            "status": "error",
            "reason": "height_lattice_worker_point_replay_error",
            "error": repr(exc),
            "lattice_status": "error",
            "certification_status": "not_attempted",
            "basis_count": len(basis),
            "basis_required": int(required),
            "precision_bits": precision,
            "runtime_seconds": result.get("runtime_seconds"),
            "basis_fingerprint": result.get("basis_fingerprint"),
            "attempt_number": attempt_number,
            "attempt_timeout": timeout,
            "retry_policy": retry_policy,
            "retry_timeout": retry_timeout,
            "retryable": True,
            "numerical_lattice_screen_is_not_rank_proof": True,
        }

    stored = store_lattice(
        db,
        curve_id=curve_id,
        source="pipeline_height_lattice_reduction",
        family_spec=(
            context.get("variant").family_spec
            if context.get("variant") is not None
            else None
        ),
        parameter=str(context.get("parameter")),
        basis=[_point_json(P) for P in reduced],
        gram=result["gram"],
        precision_bits=precision,
        determinant=result["determinant"],
        min_eigenvalue=result["min_eigenvalue"],
        positive_definite_screen=result["positive_definite_screen"],
        metadata={
            "pipeline_run_id": int(run_id),
            "stage_index": int(stage_index),
            "worker_status": "completed",
            "runtime_seconds": result.get("runtime_seconds"),
            "basis_fingerprint": result.get("basis_fingerprint"),
            "sage_version": result.get("sage_version"),
            "engine_version": result.get("engine_version"),
            "lll_reduced": bool(result.get("lll_transform")),
            "lll_transform": result.get("lll_transform"),
            "input_basis": [_point_json(P) for P in input_points],
            "input_gram": result["input_gram"],
            "input_determinant": result["input_determinant"],
            "eigenvalues": result["eigenvalues"],
        },
    )
    for index, P in enumerate(reduced, 1):
        upsert_point(
            db,
            curve_id=curve_id,
            x=P[0],
            y=P[1],
            source="pipeline_height_lattice_reduction",
            role="candidate_witness",
            exact_verified=True,
            independence_status="unknown",
            rigorous_independent=False,
            search_ref=f"pipeline:{run_id}:height-lll:{stage_index}",
            metadata={
                "lattice_id": int(stored["id"]),
                "basis_position": index,
                "lll_reduced": bool(result.get("lll_transform")),
                "basis_fingerprint": result.get("basis_fingerprint"),
            },
        )

    cert = certify_stored_independence(
        db,
        curve_id=curve_id,
        E=E,
        source="pipeline_height_lattice_reduction",
        search_ref=f"pipeline:{run_id}:height-lll:{stage_index}:exact",
        certificate_timeout=max(1, int(certificate_timeout)),
        max_candidates=max(1, int(exact_candidates)),
    )
    cert_status = str(cert.get("status") or "inconclusive")
    overall_status = "completed" if cert_status == "completed" else "partial"

    return {
        "status": overall_status,
        "reason": (
            None
            if overall_status == "completed"
            else "lattice_completed_recertification_incomplete"
        ),
        "lattice_status": "completed",
        "certification_status": cert_status,
        "lattice_id": int(stored["id"]),
        "basis_count": len(reduced),
        "basis_required": int(required),
        "precision_bits": precision,
        "runtime_seconds": result.get("runtime_seconds"),
        "basis_fingerprint": result.get("basis_fingerprint"),
        "lll_reduced": bool(result.get("lll_transform")),
        "lll_transform": result.get("lll_transform"),
        "determinant": result.get("determinant"),
        "min_eigenvalue": result.get("min_eigenvalue"),
        "positive_definite_screen": bool(
            result.get("positive_definite_screen")
        ),
        "rigorous_lower": cert.get("rigorous_lower"),
        "rank_growth": cert.get("rank_growth", 0),
        "certificate_attempts": cert.get("exact_attempts", 0),
        "certificate_outcomes": cert.get("attempt_outcomes", []),
        "certificate_reason": cert.get("reason"),
        "attempt_number": attempt_number,
        "attempt_timeout": timeout,
        "retry_policy": retry_policy,
        "retry_timeout": retry_timeout,
        "retryable": overall_status != "completed",
        "numerical_lattice_screen_is_not_rank_proof": True,
    }

