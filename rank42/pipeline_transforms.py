"""Exact Builder transform primitives.

Transforms create new mathematical search objects. They never inherit a rank
claim merely because their parent was interesting. The pipeline runner records
the exact parent -> child edge separately in search_pipeline_derivations.
"""
from __future__ import annotations

import hashlib
import json
import time

from sage.all import QQ, ZZ, EllipticCurve

from rank42.constructive_artifact import (
    verify_square_specialization_artifact,
)
from rank42.independence_check import certify_stored_independence
from rank42.constructive_family import (
    forced_bisection_constructor,
    section_height_shell,
    trace_section_constructor,
)
from rank42.curve_identity import get_or_create_canonical_curve
from rank42.isogeny_discovery import run_isogeny_degree_discovery
from rank42.plugin_hook_runner import run_isolated_plugin_hook
from rank42.pipeline_state import (
    get_pipeline_strategy_step,
    upsert_pipeline_strategy_step,
)
from rank42.plugins import load_adapter
from rank42.plugin_transform_artifact import verify_plugin_transform_artifact
from rank42.points import rigorous_witness_basis, upsert_point
from rank42.targeted_twist_scan import run_targeted_twist_trial


FANOUT_STAGE_IDS = frozenset({
    "quadratic_twist_sweep",
    "targeted_twist_search",
    "rank_jump_base_change",
    "parameter_pullback",
    "isogeny_walk",
    "surface_fibration_switch",
    "square_condition_specializer",
    "constructive_rank_jump_loop",
})


def _safe_int(value, default):
    try:
        return int(value)
    except Exception:
        return int(default)


def squarefree_twist_values(config):
    explicit = config.get("d_values") or []
    if explicit:
        values = []
        seen = set()
        for raw in explicit:
            d = int(raw)
            if d in {0, 1} or d in seen or not ZZ(d).is_squarefree():
                continue
            seen.add(d)
            values.append(d)
        return values

    low = _safe_int(config.get("d_min"), -50)
    high = _safe_int(config.get("d_max"), 50)
    if high < low:
        low, high = high, low
    values = [
        d for d in range(low, high + 1)
        if d not in {0, 1} and ZZ(d).is_squarefree()
    ]
    values.sort(key=lambda d: (abs(d), 0 if d < 0 else 1, d))
    return values


def _minimal_curve(E):
    try:
        return E.global_minimal_model()
    except Exception:
        return E


def _curve_fingerprint(E):
    Em = _minimal_curve(E)
    payload = json.dumps([str(x) for x in Em.a_invariants()], separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _store_direct_child(db, *, parent_curve_id, kind, token, E, score=None):
    canonical = get_or_create_canonical_curve(
        db, E, status="pipeline_derived"
    )
    Em = canonical["minimal_curve"]
    fp = _curve_fingerprint(Em)
    # This label is intentionally derivation-local. Scientific curve identity
    # lives in canonical["curve_id"]; many path labels may point to that row.
    label = f"{kind}:{int(parent_curve_id)}:{token}:{fp[:16]}"
    identity = dict(canonical["identity"])
    return {
        "kind": "direct_curve",
        "curve_id": int(canonical["curve_id"]),
        "created_by_pipeline": bool(canonical["created"]),
        "parameter": label,
        "score": None if score is None else float(score),
        "metadata": {
            "transform_kind": str(kind),
            "token": str(token),
            "curve_fingerprint": fp,
            "curve_fingerprint_is_identity_authority": False,
            "a_invariants": [str(x) for x in Em.a_invariants()],
            "rank_inherited": False,
            "canonical_curve_id": int(canonical["curve_id"]),
            "canonical_curve_family": str(canonical["family"]),
            "canonical_curve_parameter": str(canonical["parameter"]),
            "canonical_identity_method": identity.get("method"),
            "canonical_signature_sha256": identity.get(
                "signature_sha256"
            ),
            "canonical_identity_match": identity.get("matched_by"),
            "exact_q_isomorphism_verified": bool(
                identity.get("exact_q_isomorphism_verified")
            ),
            "derivation_identity_separate": True,
        },
    }


def quadratic_twist_children(db, *, context, config):
    parent_curve_id = int(context["curve_id"])
    E = context["E"]
    values = squarefree_twist_values(config)
    max_children = max(1, _safe_int(config.get("max_children"), 24))
    selected = values[:max_children]
    children = []
    failures = []
    for d in selected:
        try:
            child = E.quadratic_twist(ZZ(d))
        except Exception as exc:
            if len(failures) < 5:
                failures.append({
                    "twist_d": int(d),
                    "error_class": type(exc).__name__,
                    "error": str(exc)[:240],
                })
            continue
        rec = _store_direct_child(
            db,
            parent_curve_id=parent_curve_id,
            kind="quadratic_twist",
            token=f"d={d}",
            E=child,
            score=None,
        )
        rec["metadata"].update({
            "twist_d": int(d),
            "exact_transform": True,
            "score_inherited": False,
        })
        children.append(rec)
    if failures and children:
        status = "partial"
    elif failures and not children:
        status = "error"
    else:
        status = "completed"
    return {
        "status": status,
        "transform_kind": "quadratic_twist",
        "children": children,
        "attempted": len(selected),
        "succeeded": len(children),
        "failed": len(selected) - len(children),
        "failure_samples": failures,
        "tested": len(selected),
        "heuristic_only": False,
    }


def targeted_twist_children(db, *, context, config):
    parent_curve_id = int(context["curve_id"])
    E = context["E"]
    values = squarefree_twist_values(config)
    prime_bound = max(3, _safe_int(config.get("prime_bound"), 199))
    root_sign = str(config.get("root_sign") or "any")
    max_scan = max(1, _safe_int(config.get("scan_limit"), 200))
    keep = max(1, _safe_int(config.get("max_children"), 12))
    stage_timeout = max(1, _safe_int(config.get("timeout"), 60))
    per_twist_timeout = max(
        1, _safe_int(config.get("per_twist_timeout"), 10)
    )
    allow_partial_scores = bool(config.get("allow_partial_scores", True))

    selected = values[:max_scan]
    ranked = []
    outcomes = []
    failure_samples = []
    root_filtered = 0
    completed_trials = 0
    partial_trials = 0
    timeout_trials = 0
    error_trials = 0
    inconclusive_trials = 0
    budget_exhausted = False
    started = time.monotonic()

    for offset, d in enumerate(selected):
        elapsed = time.monotonic() - started
        remaining = float(stage_timeout) - elapsed
        if remaining <= 0:
            budget_exhausted = True
            for pending in selected[offset:]:
                outcomes.append({
                    "twist_d": int(pending),
                    "status": "not_attempted",
                    "reason": "stage_timeout_budget_exhausted",
                })
            break

        if remaining < 0.05:
            budget_exhausted = True
            for pending in selected[offset:]:
                outcomes.append({
                    "twist_d": int(pending),
                    "status": "not_attempted",
                    "reason": "stage_timeout_budget_exhausted",
                })
            break
        trial_timeout = min(float(per_twist_timeout), remaining)
        trial = run_targeted_twist_trial(
            E.a_invariants(),
            int(d),
            prime_bound=prime_bound,
            root_sign=root_sign,
            timeout=trial_timeout,
        )
        trial_status = str(trial.get("status") or "error")
        score_info = (
            dict(trial.get("score"))
            if isinstance(trial.get("score"), dict)
            else {}
        )
        outcome = {
            "twist_d": int(d),
            "status": trial_status,
            "reason": trial.get("reason"),
            "error": trial.get("error"),
            "runtime_seconds": trial.get("runtime_seconds"),
            "timeout_seconds": trial.get("timeout_seconds"),
            "root_number": trial.get("root_number"),
            "score_status": score_info.get("status"),
            "score": score_info.get("pipeline_score"),
            "score_algorithm": score_info.get("algorithm"),
            "score_domain": score_info.get("score_domain"),
        }
        outcomes.append(outcome)

        if trial_status == "filtered":
            root_filtered += 1
            continue
        if trial_status == "timeout":
            timeout_trials += 1
            if len(failure_samples) < 5:
                failure_samples.append(dict(outcome))
            continue
        if trial_status == "error":
            error_trials += 1
            if len(failure_samples) < 5:
                failure_samples.append(dict(outcome))
            continue
        if trial_status == "inconclusive":
            inconclusive_trials += 1
            if len(failure_samples) < 5:
                failure_samples.append(dict(outcome))
            continue
        if trial_status == "partial":
            partial_trials += 1
            if not allow_partial_scores:
                continue
        elif trial_status == "completed":
            completed_trials += 1
        else:
            error_trials += 1
            if len(failure_samples) < 5:
                failure_samples.append({
                    **outcome,
                    "reason": "unsupported_targeted_twist_trial_status",
                })
            continue

        raw_score = score_info.get("pipeline_score")
        ainvs = trial.get("a_invariants")
        if raw_score is None or not isinstance(ainvs, (list, tuple)) or len(ainvs) != 5:
            inconclusive_trials += 1
            if len(failure_samples) < 5:
                failure_samples.append({
                    **outcome,
                    "status": "inconclusive",
                    "reason": "missing_score_or_exact_child_model",
                })
            continue
        try:
            child = EllipticCurve(QQ, [QQ(str(x)) for x in ainvs])
        except Exception as exc:
            error_trials += 1
            if len(failure_samples) < 5:
                failure_samples.append({
                    **outcome,
                    "status": "error",
                    "reason": "invalid_targeted_twist_child_model",
                    "error": repr(exc),
                })
            continue

        ranked.append((
            float(raw_score),
            int(d),
            int(trial.get("root_number")),
            score_info,
            child,
        ))

    ranked.sort(key=lambda rec: (-rec[0], abs(rec[1]), rec[1]))
    children = []
    for score, d, sign, score_info, child in ranked[:keep]:
        rec = _store_direct_child(
            db,
            parent_curve_id=parent_curve_id,
            kind="targeted_twist",
            token=f"d={d}",
            E=child,
            score=score,
        )
        rec["metadata"].update({
            "twist_d": d,
            "root_number": sign,
            "prime_bound": prime_bound,
            "prime_terms": int(score_info.get("primes_used") or 0),
            "pipeline_score": score,
            "score_algorithm": score_info.get("algorithm"),
            "score_heuristic_kind": score_info.get("heuristic_kind"),
            "score_status": score_info.get("status"),
            "score_domain": score_info.get("score_domain"),
            "score_source_provenance": score_info.get(
                "source_provenance"
            ),
            "published_mestre_nagao_formula": bool(
                score_info.get("published_mestre_nagao_formula", False)
            ),
            "root_number_is_rank_parity_proof": False,
            "heuristic_selection": True,
            "score_inherited": False,
        })
        children.append(rec)

    attempted = sum(
        str(item.get("status")) != "not_attempted"
        for item in outcomes
    )
    not_attempted = len(selected) - attempted
    unresolved = (
        partial_trials
        + timeout_trials
        + error_trials
        + inconclusive_trials
        + not_attempted
    )
    if unresolved == 0:
        status = "completed"
    elif completed_trials or partial_trials or root_filtered or ranked:
        status = "partial"
    elif timeout_trials and not error_trials and not inconclusive_trials:
        status = "timeout"
    elif error_trials and not timeout_trials and not inconclusive_trials:
        status = "error"
    else:
        status = "inconclusive"

    return {
        "status": status,
        "reason": (
            None if status == "completed"
            else (
                "stage_timeout_budget_exhausted"
                if budget_exhausted
                else "targeted_twist_scan_incomplete"
            )
        ),
        "transform_kind": "targeted_twist",
        "children": children,
        "attempted": attempted,
        "constructed": completed_trials + partial_trials + root_filtered,
        "failed": timeout_trials + error_trials + inconclusive_trials,
        "failure_samples": failure_samples,
        "root_filtered": root_filtered,
        "scanned": attempted,
        "eligible": len(ranked),
        "selected_for_output": len(children),
        "not_attempted": not_attempted,
        "outcomes": outcomes,
        "prime_bound": prime_bound,
        "root_sign": root_sign,
        "score_algorithm": "cached-nagao-log-cardinality-v1",
        "score_source": "direct_exact_frobenius",
        "allow_partial_scores": allow_partial_scores,
        "stage_timeout_seconds": stage_timeout,
        "per_twist_timeout_seconds": per_twist_timeout,
        "budget_exhausted": budget_exhausted,
        "runtime_seconds": time.monotonic() - started,
        "hard_isolated": True,
        "heuristic_only": True,
    }

def _mobius_value(t, coefficients):
    if not isinstance(coefficients, (list, tuple)) or len(coefficients) != 4:
        raise ValueError("Möbius map must be [a,b,c,d]")
    a, b, c, d = [QQ(str(x)) for x in coefficients]
    if a * d - b * c == 0:
        raise ValueError("Möbius map must have nonzero determinant")
    den = c * t + d
    if den == 0:
        return None
    return (a * t + b) / den


def parameter_pullback_children(*, context, config):
    family = context.get("family")
    if family is None:
        return {
            "status": "skipped",
            "reason": "Möbius parameter transform requires an active family specialization",
            "transform_kind": "parameter_pullback",
            "children": [],
        }
    t = QQ(str(context["parameter"]))
    maps = config.get("maps") or [
        [1, 1, 0, 1],
        [1, -1, 0, 1],
        [1, 0, 1, 1],
    ]
    children = []
    failures = []
    map_outcomes = []
    skipped = 0
    seen = {str(t)}
    for index, coefficients in enumerate(maps, 1):
        mobius = [str(x) for x in coefficients]
        try:
            child_t = _mobius_value(t, coefficients)
        except Exception as exc:
            outcome = {
                "map_index": index,
                "mobius": mobius,
                "status": "error",
                "reason": "invalid_mobius_map",
                "error_class": type(exc).__name__,
                "error": str(exc)[:240],
            }
            map_outcomes.append(outcome)
            if len(failures) < 5:
                failures.append(dict(outcome))
            continue
        if child_t is None:
            skipped += 1
            map_outcomes.append({
                "map_index": index,
                "mobius": mobius,
                "status": "skipped",
                "reason": "mobius_pole_at_parent_parameter",
            })
            continue
        if str(child_t) in seen:
            skipped += 1
            map_outcomes.append({
                "map_index": index,
                "mobius": mobius,
                "status": "skipped",
                "reason": "duplicate_parameter",
                "child_parameter": str(child_t),
            })
            continue
        seen.add(str(child_t))
        try:
            child_curve = family.curve(child_t)
            if child_curve is None:
                raise ValueError("family specialization is singular or undefined")
        except Exception as exc:
            outcome = {
                "map_index": index,
                "mobius": mobius,
                "child_parameter": str(child_t),
                "status": "error",
                "reason": "family_specialization_failed",
                "error_class": type(exc).__name__,
                "error": str(exc)[:240],
            }
            map_outcomes.append(outcome)
            if len(failures) < 5:
                failures.append(dict(outcome))
            continue
        map_outcomes.append({
            "map_index": index,
            "mobius": mobius,
            "status": "completed",
            "child_parameter": str(child_t),
        })
        children.append({
            "kind": "family_parameter",
            "parameter": str(child_t),
            "score": None,
            "metadata": {
                "transform_kind": "parameter_pullback",
                "transform_label": "mobius_parameter_transform",
                "map_index": index,
                "mobius": [str(x) for x in coefficients],
                "parent_parameter": str(t),
                "child_parameter": str(child_t),
                "exact_transform": True,
                "rank_inherited": False,
                "score_inherited": False,
            },
        })
    if failures and children:
        status = "partial"
    elif failures and not children and skipped:
        status = "inconclusive"
    elif failures and not children:
        status = "error"
    else:
        status = "completed"
    return {
        "status": status,
        "transform_kind": "parameter_pullback",
        "transform_label": "mobius_parameter_transform",
        "children": children,
        "attempted": len(maps),
        "succeeded": len(children),
        "failed": len(failures),
        "skipped": skipped,
        "failure_samples": failures,
        "map_outcomes": map_outcomes,
        "maps_tested": len(maps),
        "heuristic_only": False,
    }


PLUGIN_TRANSFORM_STATUSES = frozenset({
    "completed", "partial", "inconclusive", "unsupported", "skipped",
    "timeout", "error",
})


def _normalize_plugin_transform_result(raw, *, transform_kind):
    """Normalize legacy/plugin transform returns without erasing status."""
    if raw is None:
        return {
            "status": "inconclusive",
            "reason": "plugin transform returned no result",
            "error": None,
            "records": [],
            "legacy_result": True,
        }
    if isinstance(raw, dict):
        status = str(raw.get("status") or "completed")
        if status not in PLUGIN_TRANSFORM_STATUSES:
            return {
                "status": "error",
                "reason": "plugin transform returned invalid status",
                "error": f"unsupported status {status!r}",
                "records": [],
                "legacy_result": False,
            }
        records = raw.get("children") or []
        if not isinstance(records, (list, tuple)):
            return {
                "status": "error",
                "reason": "plugin transform children must be a list",
                "error": f"children type={type(records).__name__}",
                "records": [],
                "legacy_result": False,
            }
        isolated_hook = dict(raw.get("_isolated_hook") or {})
        return {
            "status": status,
            "reason": raw.get("reason"),
            "error": raw.get("error"),
            "records": list(records),
            "legacy_result": "status" not in raw,
            "isolated_hook": isolated_hook,
            "runtime_seconds": (
                raw.get("runtime_seconds")
                if raw.get("runtime_seconds") is not None
                else isolated_hook.get("runtime_seconds")
            ),
        }
    if isinstance(raw, (list, tuple)):
        return {
            "status": "completed",
            "reason": None,
            "error": None,
            "records": list(raw),
            "legacy_result": True,
        }
    return {
        "status": "error",
        "reason": "plugin transform returned unsupported result type",
        "error": f"result type={type(raw).__name__}",
        "records": [],
        "legacy_result": False,
    }


def _plugin_transform_child(rec, *, db, context, plugin, variant, kind, index):
    if not isinstance(rec, dict):
        raise ValueError("plugin transform child must be an object")
    verification = verify_plugin_transform_artifact(
        rec.get("transform_artifact"),
        transform_kind=kind,
        context=context,
        plugin=plugin,
        variant=variant,
        child_record=rec,
    )
    metadata = dict(rec.get("metadata") or {})
    metadata.update({
        "transform_kind": kind,
        "adapter_plugin_id": plugin.id,
        "adapter_variant_id": variant.id,
        "adapter_child_index": int(index),
        "rank_inherited": False,
        "transform_verification_status": verification["verification_status"],
        "transform_verification": verification,
    })
    if rec.get("plugin_id") and rec.get("parameter") is not None:
        return {
            "kind": "family_reference",
            "plugin_id": str(rec["plugin_id"]),
            "variant_id": (
                None if rec.get("variant_id") is None
                else str(rec.get("variant_id"))
            ),
            "parameter": str(rec["parameter"]),
            "score": rec.get("score"),
            "metadata": metadata,
        }
    if rec.get("parameter") is not None:
        return {
            "kind": "family_parameter",
            "parameter": str(rec["parameter"]),
            "score": rec.get("score"),
            "metadata": metadata,
        }
    ainvs = rec.get("a_invariants")
    if isinstance(ainvs, (list, tuple)) and len(ainvs) == 5:
        child = EllipticCurve(QQ, [QQ(str(x)) for x in ainvs])
        saved = _store_direct_child(
            db,
            parent_curve_id=int(context["curve_id"]),
            kind=kind,
            token=str(rec.get("label") or index),
            E=child,
            score=rec.get("score"),
        )
        saved["metadata"].update(metadata)
        return saved
    raise ValueError(
        "plugin transform child must provide parameter or five a_invariants"
    )


def plugin_base_change_children(db, *, context, config):
    plugin = context.get("plugin")
    variant = context.get("variant")
    if plugin is None or variant is None:
        return {
            "status": "skipped",
            "reason": "rank-jump/base-change requires a family plugin",
            "transform_kind": "rank_jump_base_change",
            "children": [],
        }
    if not getattr(plugin, "adapter_path", None):
        return {
            "status": "skipped",
            "reason": "family plugin has no adapter for transform hook",
            "transform_kind": "rank_jump_base_change",
            "children": [],
        }

    timeout = max(1, _safe_int(config.get("timeout"), 30))
    request = {
        "transform_kind": "rank_jump_base_change",
        "plugin_id": plugin.id,
        "variant_id": variant.id,
        "family_spec": variant.family_spec,
        "parameter": str(context["parameter"]),
        "curve_id": int(context["curve_id"]),
        "a_invariants": [str(x) for x in context["E"].a_invariants()],
        "config": dict(config or {}),
    }
    raw = run_isolated_plugin_hook(
        plugin,
        "derive_pipeline_transform",
        request,
        timeout=timeout,
    )

    normalized = _normalize_plugin_transform_result(
        raw, transform_kind="rank_jump_base_change"
    )
    children = []
    failures = []
    for index, rec in enumerate(normalized["records"], 1):
        try:
            child = _plugin_transform_child(
                rec,
                db=db,
                context=context,
                plugin=plugin,
                variant=variant,
                kind="rank_jump_base_change",
                index=index,
            )
        except Exception as exc:
            if len(failures) < 5:
                failures.append({
                    "child_index": index,
                    "error_class": type(exc).__name__,
                    "error": str(exc)[:240],
                })
            continue
        children.append(child)

    status = normalized["status"]
    if failures:
        if children and status == "completed":
            status = "partial"
        elif not children and status == "completed":
            status = "error"

    return {
        "status": status,
        "reason": normalized.get("reason"),
        "error": normalized.get("error"),
        "transform_kind": "rank_jump_base_change",
        "children": children,
        "children_received": len(normalized["records"]),
        "children_accepted": len(children),
        "child_failures": len(failures),
        "child_failure_samples": failures,
        "adapter_plugin_id": plugin.id,
        "adapter_variant_id": variant.id,
        "plugin_status_preserved": True,
        "legacy_plugin_result": bool(normalized["legacy_result"]),
        "isolated_hook": normalized.get("isolated_hook") or {},
        "runtime_seconds": normalized.get("runtime_seconds"),
        "timeout_seconds": timeout,
        "hard_isolated": True,
        "heuristic_only": False,
    }

def _constructive_loop_round_status(*statuses):
    normalized = [str(status or "inconclusive") for status in statuses]
    if normalized and all(status == "completed" for status in normalized):
        return "completed"
    if "partial" in normalized:
        return "partial"
    if "error" in normalized:
        return "error"
    if "timeout" in normalized:
        return "timeout"
    if "user_stopped" in normalized:
        return "user_stopped"
    if "unsupported" in normalized:
        return "unsupported"
    return "inconclusive"


def _constructive_loop_occurrence(context):
    run_id = context.get("pipeline_run_id")
    candidate_id = context.get("pipeline_candidate_id")
    stage_index = context.get("pipeline_stage_index")
    if run_id is None or candidate_id is None or stage_index is None:
        return None
    return (
        f"pipeline:{int(run_id)}:candidate:{int(candidate_id)}:"
        f"stage:{int(stage_index)}:constructive_rank_jump_loop"
    )


def constructive_rank_jump_loop_children(db, *, context, config):
    heights = [
        int(x)
        for x in (config.get("heights") or [6, 8, 10, 12])
        if int(x) > 0
    ]
    limit = max(1, int(config.get("max_children") or 96))
    stop_first = bool(config.get("stop_on_first_success", False))
    retry_policy = str(config.get("retry_policy") or "automatic")
    occurrence_id = _constructive_loop_occurrence(context)
    run_id = context.get("pipeline_run_id")
    candidate_id = context.get("pipeline_candidate_id")
    stage_index = context.get("pipeline_stage_index")
    durable = occurrence_id is not None

    children, rounds, seen = [], [], set()
    resume_token = None

    def add_children(records, *, round_index, height):
        added = 0
        for raw_child in records or []:
            if not isinstance(raw_child, dict):
                continue
            child = dict(raw_child)
            key = (
                str(child.get("kind") or ""),
                str(child.get("plugin_id") or ""),
                str(child.get("variant_id") or ""),
                str(child.get("parameter") or child.get("curve_id")),
            )
            if key in seen:
                continue
            seen.add(key)
            meta = dict(child.get("metadata") or {})
            meta.update({
                "strategy": "constructive_rank_jump_loop",
                "strategy_round": int(round_index),
                "section_height": int(height),
            })
            child["metadata"] = meta
            children.append(child)
            added += 1
            if len(children) >= limit:
                break
        return added

    for round_index, height in enumerate(heights, 1):
        step_key = f"height:{round_index}:{height}"
        prior = None
        if durable:
            prior = get_pipeline_strategy_step(
                db,
                run_id=int(run_id),
                candidate_id=int(candidate_id),
                stage_index=int(stage_index),
                occurrence_id=occurrence_id,
                step_key=step_key,
            )
        if prior is not None and str(prior["status"]) == "completed":
            try:
                cached = json.loads(prior["result_json"] or "{}")
            except Exception:
                cached = {}
            cached_round = cached.get("round")
            if isinstance(cached_round, dict):
                round_rec = dict(cached_round)
            else:
                round_rec = {
                    "round": round_index,
                    "section_height": height,
                    "status": "completed",
                }
            cached_children = list(cached.get("children") or [])
            added = add_children(
                cached_children,
                round_index=round_index,
                height=height,
            )
            round_rec["new_children"] = added
            round_rec["resumed_from_checkpoint"] = True
            rounds.append(round_rec)
            if len(children) >= limit or (stop_first and added):
                break
            continue

        if durable:
            upsert_pipeline_strategy_step(
                db,
                run_id=int(run_id),
                candidate_id=int(candidate_id),
                curve_id=int(context["curve_id"]),
                stage_index=int(stage_index),
                strategy_id="constructive_rank_jump_loop",
                occurrence_id=occurrence_id,
                step_index=round_index,
                step_key=step_key,
                step_kind="height_shell",
                nested_stage_id="constructive_rank_jump_loop",
                status="running",
                retry_policy=retry_policy,
                result={},
            )

        started = time.monotonic()
        shell = section_height_shell(
            context=context,
            config={
                "height": height,
                "height_mode": str(config.get("height_mode") or "exact"),
                "coefficient_bound": int(
                    config.get("coefficient_bound") or 10
                ),
                "max_sections": limit * 2,
                "timeout": int(config.get("section_timeout") or 30),
            },
        )
        trace = trace_section_constructor(
            context=context,
            config={
                "extension_degree": int(
                    config.get("extension_degree") or 2
                ),
                "max_sections": limit * 2,
                "timeout": int(config.get("trace_timeout") or 30),
            },
            section_shell=shell,
        )
        bisection = forced_bisection_constructor(
            context=context,
            config={
                "division": int(config.get("division") or 2),
                "slope_mode": str(
                    config.get("slope_mode") or "forced"
                ),
                "max_conditions": limit * 2,
                "timeout": int(config.get("division_timeout") or 30),
            },
            trace_state=trace,
        )
        derived_context = dict(context)
        derived_context["constructive_state"] = {
            "section_height_shell": shell,
            "trace_section_constructor": trace,
            "forced_bisection_constructor": bisection,
        }
        square = square_condition_specializer_children(
            db,
            context=derived_context,
            config={
                "numerator_abs": int(
                    config.get("numerator_abs") or 1000
                ),
                "denominator_max": int(
                    config.get("denominator_max") or 1000
                ),
                "max_tests": int(config.get("max_tests") or 500000),
                "max_children": max(1, limit - len(children)),
                "timeout": int(config.get("square_timeout") or 30),
            },
        )
        round_status = _constructive_loop_round_status(
            shell.get("status"),
            trace.get("status"),
            bisection.get("status"),
            square.get("status"),
        )
        round_children = list(square.get("children") or [])
        added = add_children(
            round_children,
            round_index=round_index,
            height=height,
        )
        round_rec = {
            "round": round_index,
            "section_height": height,
            "status": round_status,
            "section_status": shell.get("status"),
            "section_count": int(shell.get("section_count") or 0),
            "trace_status": trace.get("status"),
            "trace_count": int(trace.get("trace_count") or 0),
            "bisection_status": bisection.get("status"),
            "condition_count": int(
                bisection.get("condition_count") or 0
            ),
            "square_status": square.get("status"),
            "new_children": added,
            "rejected_square_witnesses": int(
                square.get("rejected_square_witnesses") or 0
            ),
            "resumed_from_checkpoint": False,
        }
        rounds.append(round_rec)
        elapsed = time.monotonic() - started

        if durable:
            checkpoint = {
                "status": round_status,
                "round": round_rec,
                "children": round_children,
            }
            upsert_pipeline_strategy_step(
                db,
                run_id=int(run_id),
                candidate_id=int(candidate_id),
                curve_id=int(context["curve_id"]),
                stage_index=int(stage_index),
                strategy_id="constructive_rank_jump_loop",
                occurrence_id=occurrence_id,
                step_index=round_index,
                step_key=step_key,
                step_kind="height_shell",
                nested_stage_id="constructive_rank_jump_loop",
                status=round_status,
                elapsed_seconds=elapsed,
                retry_policy=retry_policy,
                result=checkpoint,
                error=(
                    square.get("error")
                    or bisection.get("error")
                    or trace.get("error")
                    or shell.get("error")
                ),
            )

        if round_status != "completed":
            resume_token = step_key
            break
        if len(children) >= limit or (stop_first and added):
            break

    if resume_token is None:
        for round_index, height in enumerate(heights, 1):
            completed_here = any(
                int(rec.get("round") or 0) == round_index
                and str(rec.get("status") or "") == "completed"
                for rec in rounds
            )
            if not completed_here:
                if len(children) >= limit or (
                    stop_first and any(
                        int(rec.get("new_children") or 0) > 0
                        for rec in rounds
                    )
                ):
                    break
                resume_token = f"height:{round_index}:{height}"
                break

    statuses = [str(rec.get("status") or "inconclusive") for rec in rounds]
    incomplete = [
        status for status in statuses if status != "completed"
    ]
    if incomplete:
        if children:
            status = "partial"
        else:
            status = incomplete[0]
    elif children:
        status = "completed"
    else:
        status = "inconclusive"

    return {
        "status": status,
        "transform_kind": "constructive_rank_jump_loop",
        "children": children,
        "rounds": rounds,
        "height_shells": heights,
        "strategy_occurrence_id": occurrence_id,
        "strategy_checkpointed": durable,
        "strategy_resume_token": resume_token,
        "strategy_retry_policy": retry_policy,
        "rank_inherited": False,
        "heuristic_only": False,
    }

def surface_fibration_switch_children(db, *, context, config):
    """Invoke a family-owned elliptic-surface/fibration switch."""
    plugin = context.get("plugin")
    variant = context.get("variant")
    if plugin is None or variant is None:
        return {
            "status": "skipped",
            "reason": "surface/fibration switch requires a family plugin",
            "transform_kind": "surface_fibration_switch",
            "children": [],
        }
    if not getattr(plugin, "adapter_path", None):
        return {
            "status": "skipped",
            "reason": "family plugin has no adapter for transform hook",
            "transform_kind": "surface_fibration_switch",
            "children": [],
        }

    timeout = max(1, _safe_int(config.get("timeout"), 30))
    request = {
        "transform_kind": "surface_fibration_switch",
        "plugin_id": plugin.id,
        "variant_id": variant.id,
        "family_spec": variant.family_spec,
        "parameter": str(context["parameter"]),
        "curve_id": int(context["curve_id"]),
        "a_invariants": [str(x) for x in context["E"].a_invariants()],
        "config": dict(config or {}),
    }
    raw = run_isolated_plugin_hook(
        plugin,
        "derive_pipeline_transform",
        request,
        timeout=timeout,
    )

    normalized = _normalize_plugin_transform_result(
        raw, transform_kind="surface_fibration_switch"
    )
    children = []
    failures = []
    for index, rec in enumerate(normalized["records"], 1):
        try:
            child = _plugin_transform_child(
                rec,
                db=db,
                context=context,
                plugin=plugin,
                variant=variant,
                kind="surface_fibration_switch",
                index=index,
            )
        except Exception as exc:
            if len(failures) < 5:
                failures.append({
                    "child_index": index,
                    "error_class": type(exc).__name__,
                    "error": str(exc)[:240],
                })
            continue
        children.append(child)

    status = normalized["status"]
    if failures:
        if children and status == "completed":
            status = "partial"
        elif not children and status == "completed":
            status = "error"

    return {
        "status": status,
        "reason": normalized.get("reason"),
        "error": normalized.get("error"),
        "transform_kind": "surface_fibration_switch",
        "children": children,
        "children_received": len(normalized["records"]),
        "children_accepted": len(children),
        "child_failures": len(failures),
        "child_failure_samples": failures,
        "adapter_plugin_id": plugin.id,
        "adapter_variant_id": variant.id,
        "plugin_status_preserved": True,
        "legacy_plugin_result": bool(normalized["legacy_result"]),
        "isolated_hook": normalized.get("isolated_hook") or {},
        "runtime_seconds": normalized.get("runtime_seconds"),
        "timeout_seconds": timeout,
        "hard_isolated": True,
        "heuristic_only": False,
    }

def _exact_square_witness(record):
    """Return exact QQ square data or None when a claimed witness is invalid."""
    if record.get("square_value") is None or record.get("square_root") is None:
        return None
    try:
        value = QQ(str(record["square_value"]))
        root = QQ(str(record["square_root"]))
    except Exception:
        return None
    if root * root != value:
        return None
    return {
        "square_value": str(value),
        "square_root": str(root),
    }


def square_condition_specializer_children(db, *, context, config):
    """Fan only condition-bound, exactly verified square specializations."""
    plugin = context.get("plugin")
    variant = context.get("variant")
    construction = dict(context.get("constructive_state") or {})
    bisection = dict(
        construction.get("forced_bisection_constructor") or {}
    )
    conditions = list(bisection.get("conditions") or [])
    if plugin is None or variant is None:
        return {
            "status": "skipped",
            "reason": (
                "square-condition specialization requires a family plugin"
            ),
            "transform_kind": "square_condition_specializer",
            "children": [],
        }
    if not conditions:
        return {
            "status": "inconclusive",
            "reason": "no_bisection_conditions",
            "transform_kind": "square_condition_specializer",
            "children": [],
        }

    timeout = max(1, _safe_int(config.get("timeout"), 30))
    request = {
        "stage_id": "square_condition_specializer",
        "pipeline_run_id": context.get("pipeline_run_id"),
        "pipeline_candidate_id": context.get("pipeline_candidate_id"),
        "plugin_id": plugin.id,
        "variant_id": variant.id,
        "family_spec": variant.family_spec,
        "parent_parameter": str(context["parameter"]),
        "curve_id": int(context["curve_id"]),
        "a_invariants": [
            str(x) for x in context["E"].a_invariants()
        ],
        "conditions": conditions,
        "numerator_abs": int(config.get("numerator_abs") or 500),
        "denominator_max": int(config.get("denominator_max") or 500),
        "max_tests": int(config.get("max_tests") or 200000),
        "max_children": int(config.get("max_children") or 64),
        "timeout": timeout,
        "config": dict(config or {}),
    }
    raw = run_isolated_plugin_hook(
        plugin,
        "derive_pipeline_square_specializations",
        request,
        timeout=timeout,
    )
    if not isinstance(raw, dict):
        raw = {
            "status": "error",
            "reason": "isolated Square specialization hook returned malformed result",
            "error": repr(raw),
            "children": [],
        }

    payload = dict(raw)
    records = list(payload.get("children") or [])
    plugin_status = str(payload.get("status") or "error")
    plugin_reason = payload.get("reason")
    plugin_error = payload.get("error")
    legacy_result = bool(payload.get("legacy_list_result", False))

    allowed_statuses = {
        "completed",
        "partial",
        "timeout",
        "error",
        "unsupported",
        "skipped",
        "user_stopped",
        "inconclusive",
    }
    if plugin_status not in allowed_statuses:
        plugin_error = (
            f"unsupported square specialization status "
            f"{plugin_status!r}"
        )
        plugin_reason = "invalid_square_specialization_status"
        plugin_status = "error"

    tests_requested = int(request["max_tests"])
    domain_requested = {
        "numerator_abs": int(request["numerator_abs"]),
        "denominator_max": int(request["denominator_max"]),
    }
    tests_completed = None
    coverage_error = None
    if payload.get("tests_completed") is not None:
        try:
            tests_completed = int(payload.get("tests_completed"))
        except Exception:
            coverage_error = "tests_completed must be an integer"
        else:
            if tests_completed < 0:
                coverage_error = "tests_completed must be nonnegative"
            elif tests_completed > tests_requested:
                coverage_error = (
                    "tests_completed exceeds configured max_tests"
                )
    domain_completed = payload.get("domain_completed")
    if (
        domain_completed is not None
        and not isinstance(domain_completed, dict)
    ):
        coverage_error = "domain_completed must be an object"
        domain_completed = None
    coverage_reported = (
        tests_completed is not None
        and isinstance(domain_completed, dict)
    )

    max_children = max(1, int(config.get("max_children") or 64))
    children = []
    rejected_square_witnesses = 0
    rejected_condition_bindings = 0
    failure_samples = []
    seen = set()

    for index, rec in enumerate(records, 1):
        if not isinstance(rec, dict):
            rejected_condition_bindings += 1
            if len(failure_samples) < 5:
                failure_samples.append({
                    "record_index": index,
                    "reason": "square_specialization_record_must_be_object",
                })
            continue

        try:
            verification = verify_square_specialization_artifact(
                rec,
                context=context,
                request=request,
                source_conditions=conditions,
            )
        except Exception as exc:
            message = str(exc)
            if "root^2 does not equal square value" in message:
                rejected_square_witnesses += 1
            else:
                rejected_condition_bindings += 1
            if len(failure_samples) < 5:
                failure_samples.append({
                    "record_index": index,
                    "artifact_id": rec.get("id"),
                    "condition_id": rec.get("condition_id"),
                    "reason": "square_specialization_verification_error",
                    "error_class": type(exc).__name__,
                    "error": message[:240],
                })
            continue

        constructed_points = []
        for raw_point in rec.get("constructed_points") or []:
            if (
                not isinstance(raw_point, (list, tuple))
                or len(raw_point) < 2
            ):
                continue
            constructed_points.append([
                str(raw_point[0]),
                str(raw_point[1]),
            ])

        child_parameter = str(verification["child_parameter"])
        metadata = dict(rec.get("metadata") or {})
        metadata.update({
            "transform_kind": "square_condition_specializer",
            "adapter_plugin_id": plugin.id,
            "adapter_variant_id": variant.id,
            "adapter_child_index": index,
            "condition_id": verification["source_condition_id"],
            "condition_artifact_hash": verification[
                "source_condition_hash"
            ],
            "condition_value": verification["condition_value"],
            "square_value": verification["square_value"],
            "square_root": verification["square_root"],
            "exact_square_verified": True,
            "exact_condition_evaluation_verified": True,
            "square_specialization_verification": verification,
            "rank_inherited": False,
            "score_inherited": False,
            "producer_score_discarded": rec.get("score") is not None,
            "constructed_points": constructed_points,
        })

        child = None
        if rec.get("plugin_id"):
            child = {
                "kind": "family_reference",
                "plugin_id": str(rec["plugin_id"]),
                "variant_id": (
                    None
                    if rec.get("variant_id") is None
                    else str(rec.get("variant_id"))
                ),
                "parameter": child_parameter,
                "score": None,
                "constructed_points": constructed_points,
                "metadata": metadata,
            }
        elif rec.get("a_invariants") is None:
            child = {
                "kind": "family_parameter",
                "parameter": child_parameter,
                "score": None,
                "constructed_points": constructed_points,
                "metadata": metadata,
            }
        elif (
            isinstance(rec.get("a_invariants"), (list, tuple))
            and len(rec["a_invariants"]) == 5
        ):
            direct = EllipticCurve(
                QQ, [QQ(str(x)) for x in rec["a_invariants"]]
            )
            child = _store_direct_child(
                db,
                parent_curve_id=int(context["curve_id"]),
                kind="square_condition_specializer",
                token=str(rec.get("label") or index),
                E=direct,
                score=None,
            )
            child["constructed_points"] = constructed_points
            child["metadata"].update(metadata)

        if child is None:
            rejected_condition_bindings += 1
            if len(failure_samples) < 5:
                failure_samples.append({
                    "record_index": index,
                    "artifact_id": rec.get("id"),
                    "reason": "square_specialization_child_shape_invalid",
                })
            continue

        key = (
            str(child.get("kind")),
            str(child.get("plugin_id") or ""),
            str(child.get("variant_id") or ""),
            str(child.get("parameter") or child.get("curve_id")),
        )
        if key in seen:
            continue
        seen.add(key)
        children.append(child)
        if len(children) >= max_children:
            break

    status = plugin_status
    rejected = rejected_square_witnesses + rejected_condition_bindings
    if plugin_status == "completed" and rejected:
        status = "partial" if children else "error"

    coverage_reason = None
    if coverage_error is not None:
        coverage_reason = "invalid_square_search_coverage"
        if status == "completed":
            status = "partial" if children else "error"
    elif plugin_status == "completed" and not coverage_reported:
        coverage_reason = "square_search_coverage_unreported"
        status = "partial" if children else "inconclusive"

    isolated_hook = dict(payload.get("_isolated_hook") or {})
    runtime_seconds = payload.get("runtime_seconds")
    if runtime_seconds is None:
        runtime_seconds = isolated_hook.get("runtime_seconds")

    return {
        **{
            k: v for k, v in payload.items()
            if k not in {"children", "status", "reason", "error"}
        },
        "status": status,
        "reason": (
            plugin_reason
            if plugin_reason is not None
            else (
                coverage_reason
                if coverage_reason is not None
                else (
                    "square_specialization_verification_incomplete"
                    if status in {"partial", "error"} and rejected
                    else None
                )
            )
        ),
        "error": plugin_error,
        "transform_kind": "square_condition_specializer",
        "children": children,
        "conditions_received": len(conditions),
        "records_received": len(records),
        "rejected_square_witnesses": rejected_square_witnesses,
        "rejected_condition_bindings": rejected_condition_bindings,
        "verification_failure_samples": failure_samples,
        "exact_square_tests_verified": len(children),
        "tests_requested": tests_requested,
        "tests_completed": tests_completed,
        "domain_requested": domain_requested,
        "domain_completed": domain_completed,
        "coverage_reported": coverage_reported,
        "coverage_error": coverage_error,
        "plugin_status_preserved": True,
        "legacy_plugin_result": legacy_result,
        "hard_isolated": True,
        "timeout_seconds": timeout,
        "runtime_seconds": runtime_seconds,
        "isolated_hook": isolated_hook,
        "heuristic_only": False,
    }

def _isogeny_certification_status(cert):
    if cert is None:
        return "certification_not_attempted"
    status = str(cert.get("status") or "inconclusive")
    return {
        "completed": "certification_completed",
        "partial": "certification_partial",
        "timeout": "certification_timeout",
        "error": "certification_failed",
        "inconclusive": "certification_inconclusive",
    }.get(status, "certification_inconclusive")


def isogeny_walk_children(db, *, context, config):
    parent_curve_id = int(context["curve_id"])
    E = context["E"]
    degrees = [
        int(x) for x in (config.get("degrees") or [2, 3, 5, 7, 11, 13])
        if int(x) > 1
    ]
    max_children = max(1, _safe_int(config.get("max_children"), 12))
    timeout = max(1, _safe_int(config.get("timeout"), 60))
    transfer_basis = bool(config.get("transfer_basis", True))
    certificate_timeout = max(
        1, _safe_int(config.get("certificate_timeout"), 120)
    )
    exact_candidates = max(
        1, _safe_int(config.get("exact_candidates"), 64)
    )
    basis, required_basis, basis_complete = rigorous_witness_basis(
        db, parent_curve_id, E
    )
    transfer_points = basis if transfer_basis and basis_complete else []
    seen = {_curve_fingerprint(E)}
    children = []
    degree_outcomes = []

    for degree_index, degree in enumerate(degrees):
        if len(children) >= max_children:
            degree_outcomes.extend({
                "degree": int(rest),
                "status": "skipped",
                "reason": "max_children_reached",
                "child_count": 0,
                "timeout_seconds": timeout,
            } for rest in degrees[degree_index:])
            break

        discovery = run_isogeny_degree_discovery(
            E.a_invariants(),
            degree,
            transfer_points,
            timeout=timeout,
        )
        discovery_status = str(discovery.get("status") or "error")
        outcome = {
            "degree": int(degree),
            "status": discovery_status,
            "reason": discovery.get("reason"),
            "error": discovery.get("error"),
            "timeout_seconds": timeout,
            "runtime_seconds": discovery.get("runtime_seconds"),
            "worker_exit_code": discovery.get("worker_exit_code"),
            "discovered_edges": len(discovery.get("children") or []),
            "children_created": 0,
            "child_failures": 0,
            "child_failure_samples": [],
            "transfer_outcomes": [],
        }
        if discovery_status != "completed":
            degree_outcomes.append(outcome)
            continue

        created_this_degree = 0
        failures = []
        transfer_incomplete = False
        for branch in discovery.get("children") or []:
            index = int(branch.get("edge_index") or (created_this_degree + 1))
            try:
                raw_child = EllipticCurve(
                    QQ,
                    [
                        QQ(str(x))
                        for x in branch["codomain_a_invariants"]
                    ],
                )
                child = _minimal_curve(raw_child)
                fp = _curve_fingerprint(child)
            except Exception as exc:
                if len(failures) < 5:
                    failures.append({
                        "edge_index": index,
                        "error_class": type(exc).__name__,
                        "error": str(exc)[:240],
                    })
                continue
            if fp in seen:
                continue
            seen.add(fp)
            rec = _store_direct_child(
                db,
                parent_curve_id=parent_curve_id,
                kind="isogeny_walk",
                token=f"degree={degree}:edge={index}",
                E=child,
                score=None,
            )

            mapped_points = 0
            transferred_point_ids = []
            transfer_failures = [
                {
                    "kind": "worker_mapping",
                    **dict(sample),
                }
                for sample in (
                    branch.get("mapping_failure_samples") or []
                )[:5]
                if isinstance(sample, dict)
            ]
            certification = None
            if not transfer_basis:
                transfer_status = "transfer_not_requested"
                transfer_reason = None
                certification_status = "certification_not_requested"
            elif not basis_complete:
                transfer_status = "transfer_failed"
                transfer_reason = "parent_rigorous_basis_incomplete"
                certification_status = "certification_not_attempted"
            else:
                model_map = None
                model_map_ok = True
                try:
                    if list(raw_child.a_invariants()) != list(child.a_invariants()):
                        model_map = raw_child.isomorphism_to(child)
                except Exception as exc:
                    model_map_ok = False
                    if len(transfer_failures) < 5:
                        transfer_failures.append({
                            "kind": "child_model_isomorphism",
                            "error_class": type(exc).__name__,
                            "error": str(exc)[:240],
                        })

                if model_map_ok:
                    for mapped in branch.get("mapped_points") or []:
                        try:
                            xy = mapped["point"]
                            Q = raw_child(QQ(str(xy[0])), QQ(str(xy[1])))
                            if model_map is not None:
                                Q = model_map(Q)
                            if Q.is_zero():
                                raise ValueError(
                                    "mapped rigorous witness is the identity"
                                )
                        except Exception as exc:
                            if len(transfer_failures) < 5:
                                transfer_failures.append({
                                    "kind": "mapped_witness",
                                    "parent_basis_index": int(
                                        mapped.get("parent_basis_index") or 0
                                    ),
                                    "error_class": type(exc).__name__,
                                    "error": str(exc)[:240],
                                })
                            continue
                        stored = upsert_point(
                            db,
                            curve_id=int(rec["curve_id"]),
                            x=Q[0],
                            y=Q[1],
                            source="pipeline_isogeny_transfer",
                            role="candidate_witness",
                            exact_verified=True,
                            independence_status="unknown",
                            rigorous_independent=False,
                            search_ref=(
                                f"pipeline:isogeny:{parent_curve_id}:"
                                f"{degree}:{index}"
                            ),
                            metadata={
                                "parent_curve_id": parent_curve_id,
                                "isogeny_degree": degree,
                                "isogeny_edge_index": index,
                                "parent_basis_index": int(
                                    mapped.get("parent_basis_index") or 0
                                ),
                                "discovery_hard_isolated": True,
                            },
                        )
                        mapped_points += 1
                        if stored is not None:
                            transferred_point_ids.append(int(stored["id"]))

                expected_points = len(basis)
                worker_complete = bool(
                    branch.get("mapping_complete", True)
                )
                if (
                    model_map_ok
                    and worker_complete
                    and mapped_points == expected_points
                ):
                    transfer_status = "transfer_completed"
                    transfer_reason = None
                elif mapped_points > 0:
                    transfer_status = "transfer_partial"
                    transfer_reason = "not_all_rigorous_witnesses_transferred"
                else:
                    transfer_status = "transfer_failed"
                    transfer_reason = "no_rigorous_witnesses_transferred"

                if transfer_status != "transfer_failed":
                    certification = certify_stored_independence(
                        db,
                        curve_id=int(rec["curve_id"]),
                        E=child,
                        source="pipeline_isogeny_transfer",
                        search_ref=(
                            f"pipeline:isogeny:{parent_curve_id}:"
                            f"{degree}:{index}:exact"
                        ),
                        certificate_timeout=certificate_timeout,
                        max_candidates=exact_candidates,
                        candidate_point_ids=transferred_point_ids,
                    )
                    certification_status = _isogeny_certification_status(
                        certification
                    )
                else:
                    certification_status = "certification_not_attempted"

            transfer_complete = transfer_status == "transfer_completed"
            certified_lower = (
                None
                if certification is None
                else int(certification.get("rigorous_lower") or 0)
            )
            certification_summary = {
                "status": certification_status,
                "reason": (
                    None
                    if certification is None
                    else certification.get("reason")
                ),
                "certificate_service": (
                    None
                    if certification is None
                    else certification.get("certificate_service")
                ),
                "exact_attempts": (
                    0
                    if certification is None
                    else int(certification.get("exact_attempts") or 0)
                ),
                "attempt_outcomes": (
                    []
                    if certification is None
                    else list(certification.get("attempt_outcomes") or [])
                ),
                "evidence_ids": (
                    []
                    if certification is None
                    else list(certification.get("evidence_ids") or [])
                ),
                "promotion_evidence_id": (
                    None
                    if certification is None
                    else certification.get("promotion_evidence_id")
                ),
                "rigorous_lower": certified_lower,
                "candidate_point_ids": list(transferred_point_ids),
            }

            rec["metadata"].update({
                "isogeny_degree": degree,
                "edge_index": index,
                "rank_over_Q_is_invariant": True,
                "rank_inherited_in_database": False,
                "exact_transform": True,
                "parent_required_basis": int(required_basis),
                "parent_basis_complete": bool(basis_complete),
                "basis_transfer_requested": bool(transfer_basis),
                "basis_transfer_status": transfer_status,
                "basis_transfer_reason": transfer_reason,
                "basis_transfer_complete": bool(transfer_complete),
                "expected_witness_points": (
                    len(basis) if transfer_basis and basis_complete else 0
                ),
                "mapped_witness_points": int(mapped_points),
                "transferred_point_ids": list(transferred_point_ids),
                "worker_mapping_failures": int(
                    branch.get("mapping_failures") or 0
                ),
                "transfer_failure_samples": transfer_failures,
                "transfer_certification": certification_summary,
                "child_certified_lower": certified_lower,
                "isogeny_discovery_hard_isolated": True,
                "isogeny_discovery_timeout_seconds": timeout,
                "isogeny_discovery_runtime_seconds": discovery.get(
                    "runtime_seconds"
                ),
            })

            outcome["transfer_outcomes"].append({
                "edge_index": index,
                "curve_id": int(rec["curve_id"]),
                "transfer_status": transfer_status,
                "transfer_reason": transfer_reason,
                "mapped_witness_points": int(mapped_points),
                "expected_witness_points": (
                    len(basis) if transfer_basis and basis_complete else 0
                ),
                "certification_status": certification_status,
                "certification_reason": certification_summary["reason"],
                "certificate_attempts": certification_summary[
                    "exact_attempts"
                ],
                "certificate_outcomes": certification_summary[
                    "attempt_outcomes"
                ],
                "evidence_ids": certification_summary["evidence_ids"],
                "promotion_evidence_id": certification_summary[
                    "promotion_evidence_id"
                ],
                "rigorous_lower": certified_lower,
            })
            if transfer_basis and (
                transfer_status != "transfer_completed"
                or certification_status != "certification_completed"
            ):
                transfer_incomplete = True

            children.append(rec)
            created_this_degree += 1
            if len(children) >= max_children:
                break

        outcome["children_created"] = int(created_this_degree)
        outcome["child_failures"] = len(failures)
        outcome["child_failure_samples"] = failures
        if failures:
            outcome["status"] = (
                "partial" if created_this_degree else "error"
            )
            outcome["reason"] = "isogeny_child_materialization_incomplete"
        elif transfer_incomplete and created_this_degree:
            outcome["status"] = "partial"
            outcome["reason"] = "isogeny_transfer_or_certification_incomplete"
        degree_outcomes.append(outcome)

    statuses = [str(item.get("status") or "error") for item in degree_outcomes]
    completed = sum(status == "completed" for status in statuses)
    skipped = sum(status == "skipped" for status in statuses)
    partials = sum(status == "partial" for status in statuses)
    timeouts = sum(status == "timeout" for status in statuses)
    errors = sum(status == "error" for status in statuses)
    unsupported = sum(status == "unsupported" for status in statuses)
    inconclusive = sum(
        status not in {
            "completed", "skipped", "partial", "timeout", "error", "unsupported"
        }
        for status in statuses
    )
    incomplete = partials + timeouts + errors + unsupported + inconclusive

    if not degree_outcomes:
        status = "inconclusive"
    elif incomplete == 0:
        status = "completed"
    elif completed or skipped or partials:
        status = "partial"
    elif timeouts and not errors and not unsupported and not inconclusive:
        status = "timeout"
    elif errors and not timeouts and not unsupported and not inconclusive:
        status = "error"
    elif unsupported and not timeouts and not errors and not inconclusive:
        status = "unsupported"
    else:
        status = "inconclusive"

    return {
        "status": status,
        "reason": (
            None if status == "completed"
            else f"isogeny_degree_coverage_{status}"
        ),
        "retryable": status in {
            "partial", "timeout", "error", "inconclusive"
        },
        "transform_kind": "isogeny_walk",
        "children": children,
        "degrees_requested": degrees,
        "degrees_tested": [
            item["degree"]
            for item in degree_outcomes
            if item.get("status") != "skipped"
        ],
        "degree_outcomes": degree_outcomes,
        "degree_coverage": {
            "requested": len(degrees),
            "resolved": completed + skipped,
            "completed": completed,
            "skipped": skipped,
            "partial": partials,
            "timeout": timeouts,
            "error": errors,
            "unsupported": unsupported,
            "inconclusive": inconclusive,
        },
        "timeout_seconds": timeout,
        "hard_isolated": True,
        "parent_basis_size": len(basis),
        "parent_basis_complete": bool(basis_complete),
        "heuristic_only": False,
    }

def derive_transform_children(stage_id, db, *, context, config):
    if stage_id == "quadratic_twist_sweep":
        return quadratic_twist_children(db, context=context, config=config)
    if stage_id == "targeted_twist_search":
        return targeted_twist_children(db, context=context, config=config)
    if stage_id == "parameter_pullback":
        return parameter_pullback_children(context=context, config=config)
    if stage_id == "rank_jump_base_change":
        return plugin_base_change_children(db, context=context, config=config)
    if stage_id == "isogeny_walk":
        return isogeny_walk_children(db, context=context, config=config)
    if stage_id == "surface_fibration_switch":
        return surface_fibration_switch_children(db, context=context, config=config)
    if stage_id == "square_condition_specializer":
        return square_condition_specializer_children(db, context=context, config=config)
    if stage_id == "constructive_rank_jump_loop":
        return constructive_rank_jump_loop_children(db, context=context, config=config)
    raise ValueError(f"unsupported transform stage {stage_id!r}")
