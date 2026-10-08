"""PARI-first rigorous rank-bounds pipeline for stored Rank Hunter curves."""
from __future__ import annotations

import json

from rank42.db import get_curve, log_event, proven_lower, update_curve
from rank42.rank_bounds import eclib_rh_available, run_engine_bounds, runtime_versions
from rank42.rank_evidence import (
    apply_reduced_rank_state, cached_rank_evidence, evidence_key, record_rank_evidence,
)


def _row_to_result(row):
    def j(name, default):
        try:
            return json.loads(row[name] or json.dumps(default))
        except Exception:
            return default
    return {
        "engine": row["engine"], "engine_version": row["engine_version"], "sage_version": row["sage_version"],
        "evidence_type": row["evidence_type"], "status": row["status"], "rigorous": bool(row["rigorous"]),
        "rigorous_lower": row["rigorous_lower"], "rigorous_upper": row["rigorous_upper"], "exact_rank": row["exact_rank"],
        "conditional_analytic_upper": row["conditional_analytic_upper"], "numerical_rank_signal": row["numerical_rank_signal"],
        "assumptions": j("assumptions_json", []), "points_found": j("points_json", []),
        "minimal_model_a_invariants": j("minimal_model_a_invariants_json", None),
        "elapsed_seconds": row["elapsed_seconds"], "timed_out": bool(row["timed_out"]), "partial": bool(row["partial"]),
        "options": j("options_json", {}), "cached": True,
    }


def _run_one(
    db, *, curve_id, model, engine, timeout, force=False,
    known_lower=None, tight=True, pari_stack_max_bytes=None,
):
    versions = runtime_versions(engine)
    options = {"timeout": int(timeout)}
    if engine == "pari" and pari_stack_max_bytes is not None:
        options["pari_stack_max_bytes"] = int(pari_stack_max_bytes)
    if engine == "eclib_rh":
        options.update({
            "known_lower": int(known_lower if known_lower is not None else 0),
            "tight": bool(tight),
        })
    key = evidence_key(
        curve_id=int(curve_id), model=model, engine=engine,
        engine_version=versions.get("engine_version"), evidence_type="rank_bounds", options=options,
    )
    if not force:
        cached = cached_rank_evidence(db, key=key)
        if cached is not None and str(cached["status"] or "") == "completed":
            return _row_to_result(cached), key
    result = run_engine_bounds(
        model,
        engine=engine,
        timeout=int(timeout),
        known_lower=known_lower,
        tight=tight,
        pari_stack_max_bytes=pari_stack_max_bytes,
    )
    # Preserve the pre-run runtime fingerprint in the cache identity; worker
    # metadata still records the exact runtime version it observed.
    record_rank_evidence(db, curve_id=int(curve_id), model=model, data=result, key=key)
    return result, key


def _known_engine_limit(db, curve_id, model, *, engine, failure_class):
    """Return whether this exact stored model already hit a stable engine limit."""
    wanted = [str(x) for x in model]
    rows = db.execute(
        """SELECT model_a_invariants_json, options_json
           FROM rank_evidence
           WHERE curve_id=? AND engine=? AND status='inconclusive'
           ORDER BY id DESC LIMIT 50""",
        (int(curve_id), str(engine)),
    ).fetchall()
    for row in rows:
        try:
            stored = [str(x) for x in json.loads(row["model_a_invariants_json"] or "[]")]
            options = json.loads(row["options_json"] or "{}")
        except Exception:
            continue
        if stored == wanted and str(options.get("failure_class") or "") == str(failure_class):
            return True
    return False


def _log_exact_if_any(db, curve_id, state, engine):
    if state["exact_rank"] is None:
        return False
    log_event(
        db, int(curve_id), "best",
        f"rigorous rank bounds certify exact rank {state['exact_rank']} via {engine}",
    )
    return True


def run_rank_bounds_for_curve(
    db, curve_id: int, *, engine="auto", timeout=300, mwrank_timeout=None,
    eclib_rh_timeout=30, escalate=False, force=False, pari_stack_max_gib=4,
) -> dict:
    row = get_curve(db, int(curve_id))
    if row is None:
        raise ValueError(f"curve #{int(curve_id)} not found")
    try:
        model = json.loads(row["a_invariants_json"] or "[]")
    except Exception as exc:
        raise ValueError("curve has malformed stored a-invariants") from exc
    if len(model) != 5:
        raise ValueError("curve has no stored five-term Weierstrass model")

    engine = str(engine).lower()
    if engine not in {"auto", "pari", "mwrank", "eclib_rh"}:
        raise ValueError("engine must be auto, pari, mwrank, or eclib_rh")
    mwrank_timeout = int(mwrank_timeout if mwrank_timeout is not None else timeout)
    eclib_rh_timeout = int(eclib_rh_timeout)
    pari_stack_max_gib = int(pari_stack_max_gib)
    if pari_stack_max_gib < 2:
        raise ValueError("pari_stack_max_gib must be at least 2")
    pari_stack_max_bytes = pari_stack_max_gib * 1024 * 1024 * 1024
    if eclib_rh_timeout <= 0:
        raise ValueError("eclib_rh_timeout must be positive")

    runs = []
    primary = "pari" if engine == "auto" else engine

    if engine != "auto":
        state_before = apply_reduced_rank_state(db, int(curve_id))
        selected_timeout = (
            int(timeout) if engine == "pari"
            else eclib_rh_timeout if engine == "eclib_rh"
            else mwrank_timeout
        )
        result, _key = _run_one(
            db, curve_id=int(curve_id), model=model, engine=engine,
            timeout=selected_timeout, force=force,
            known_lower=state_before["rigorous_lower"] if engine == "eclib_rh" else None,
            tight=True,
            pari_stack_max_bytes=pari_stack_max_bytes if engine == "pari" else None,
        )
        runs.append(result)
        state = apply_reduced_rank_state(db, int(curve_id))
        if state["inconsistent"]:
            log_event(db, int(curve_id), "error", "rank evidence is inconsistent: rigorous upper below stored rigorous lower")
        else:
            _log_exact_if_any(db, int(curve_id), state, engine)
    else:
        # Keep PARI first.  Real-world rollout measurements showed that running
        # eclib-rh after every completed PARI gap adds cost without improving
        # the upper bound on the current corpus.  Default auto therefore uses
        # eclib-rh as the fast rigorous fallback after PARI timeout/error.
        # Explicit --escalate still requests the deeper eclib-rh -> mwrank chain.
        pari_result, _key = _run_one(
            db, curve_id=int(curve_id), model=model, engine="pari",
            timeout=int(timeout), force=force,
            pari_stack_max_bytes=pari_stack_max_bytes,
        )
        runs.append(pari_result)
        state = apply_reduced_rank_state(db, int(curve_id))

        if state["inconsistent"]:
            log_event(db, int(curve_id), "error", "rank evidence is inconsistent: rigorous upper below stored rigorous lower")
        elif not _log_exact_if_any(db, int(curve_id), state, "pari"):
            pari_status = str(pari_result.get("status") or "error")
            pari_failed = pari_status in {"timeout", "error", "inconclusive"}
            eclib_result = None

            if eclib_rh_available() and (pari_failed or bool(escalate)):
                eclib_result, _key = _run_one(
                    db, curve_id=int(curve_id), model=model, engine="eclib_rh",
                    timeout=eclib_rh_timeout, force=force,
                    known_lower=state["rigorous_lower"], tight=True,
                )
                runs.append(eclib_result)
                state = apply_reduced_rank_state(db, int(curve_id))
                if state["inconsistent"]:
                    log_event(db, int(curve_id), "error", "rank evidence is inconsistent: rigorous upper below stored rigorous lower")
                else:
                    _log_exact_if_any(db, int(curve_id), state, "eclib-rh")

            if not state["inconsistent"] and state["exact_rank"] is None:
                eclib_status = None if eclib_result is None else str(eclib_result.get("status") or "error")
                # If PARI failed, a completed eclib-rh result replaces the old
                # default full-mwrank fallback.  Full Sage mwrank is retained
                # when both lighter engines fail, or whenever escalation is
                # explicitly requested.
                need_mwrank = bool(escalate) or (
                    pari_failed and eclib_status not in {"completed"}
                )
                if (
                    need_mwrank
                    and not force
                    and _known_engine_limit(
                        db,
                        int(curve_id),
                        model,
                        engine="mwrank",
                        failure_class="MWRANK_SIZE_LIMIT",
                    )
                ):
                    log_event(
                        db,
                        int(curve_id),
                        "info",
                        "auto rank bounds skipped mwrank: this exact model previously hit MWRANK_SIZE_LIMIT",
                    )
                    need_mwrank = False

                if need_mwrank:
                    fallback, _key = _run_one(
                        db, curve_id=int(curve_id), model=model, engine="mwrank",
                        timeout=mwrank_timeout, force=force,
                    )
                    runs.append(fallback)
                    state = apply_reduced_rank_state(db, int(curve_id))
                    if state["inconsistent"]:
                        log_event(db, int(curve_id), "error", "rank evidence is inconsistent: rigorous upper below stored rigorous lower")
                    else:
                        _log_exact_if_any(db, int(curve_id), state, "mwrank")

    state = apply_reduced_rank_state(db, int(curve_id))
    current_row = get_curve(db, int(curve_id))
    run_statuses = [str(r.get("status") or "error") for r in runs]
    if runs:
        pari_completed = next((r for r in runs if r.get("engine") == "pari" and r.get("status") == "completed" and r.get("rigorous_upper") is not None), None)
        if pari_completed is not None:
            old_quick = current_row["quick_upper"]
            new_upper = int(pari_completed["rigorous_upper"])
            if old_quick is None or new_upper < int(old_quick):
                update_curve(db, int(curve_id), quick_upper=new_upper)
        if state["exact_rank"] is None and not state["inconsistent"]:
            if "completed" in run_statuses:
                curve_status = "rank_bounds_done"
                curve_error = None
            elif "timeout" in run_statuses:
                curve_status = "rank_bounds_timeout"
                curve_error = "rank-bounds engines timed out or were inconclusive; rigorous prior evidence preserved"
            elif "inconclusive" in run_statuses:
                curve_status = "rank_bounds_inconclusive"
                curve_error = "rank-bounds engines were inconclusive; rigorous prior evidence preserved"
            else:
                curve_status = "rank_bounds_error"
                curve_error = "rank-bounds engines failed; rigorous prior evidence preserved"
            update_curve(db, int(curve_id), status=curve_status, error=curve_error)

    if state["exact_rank"] is not None:
        overall_status = "exact"
    elif state["inconsistent"]:
        overall_status = "inconsistent"
    elif "completed" in run_statuses:
        overall_status = "completed"
    elif "timeout" in run_statuses:
        overall_status = "timeout"
    elif "inconclusive" in run_statuses:
        overall_status = "inconclusive"
    else:
        overall_status = "error"
    return {
        **state,
        "primary_engine": primary,
        "status": overall_status,
        "stored_lower_before": int(proven_lower(row)),
        "runs": runs,
    }
