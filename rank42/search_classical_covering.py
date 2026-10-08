"""Shared classical covering escalation for Search and Pipeline.

This module owns the known-subgroup 2-descent escalation itself, independent
of legacy Auto campaign/trial orchestration.
"""
from __future__ import annotations

from sage.all import QQ

from rank42.auto_point_search import certify_ledger_growth
from rank42.auto_search_policy import classical_covering_policy
from rank42.classical_descent import (
    ClassicalDescentFailure,
    ClassicalDescentTimeout,
    run_classical_descent,
)
from rank42.db import log_event
from rank42.point_promotion import promote_rigorous_rank_interval
from rank42.points import rigorous_witness_basis, upsert_point
from rank42.search_science import research_rank_state


def run_classical_covering_escalation(
    db,
    *,
    curve_id,
    E,
    policy,
    certificate_timeout,
    exact_candidates,
    attempt_identity=None,
    attempt_number=1,
    retry_policy="manual",
    retry_timeout=None,
):
    """Try a classical known-subgroup 2-descent after direct point search stalls.

    The covering/descent engine may return exact rational points and a rigorous
    upper bound. Rank Hunter ignores the engine's lower-bound claim: every new
    point must still pass the ordinary exact independence certificate before the
    stored rigorous lower bound can rise.
    """
    cfg = classical_covering_policy(policy)
    attempt_timeout = max(1, int(cfg["timeout"]))
    retry_policy = str(retry_policy or "manual")
    retry_timeout = max(
        1,
        int(retry_timeout or max(300, attempt_timeout)),
    )
    attempt_identity = (
        str(attempt_identity)
        if attempt_identity is not None
        else f"curve:{int(curve_id)}:classical-covering:{cfg['engine']}"
    )
    attempt_number = max(1, int(attempt_number or 1))
    basis, required, complete = rigorous_witness_basis(db, curve_id, E)
    if not complete:
        return {
            "scheduler_tier": "classical-covering",
            "status": "skipped-incomplete-basis",
            "engine": cfg["engine"],
            "known_basis": len(basis),
            "required_basis": required,
            "exact_points": 0,
            "rank_growth": 0,
        }

    print(
        f"[auto:classical-covering] engine={cfg['engine']} "
        f"known_basis={len(basis)} timeout={cfg['timeout']}s",
        flush=True,
    )
    before = int(research_rank_state(db, curve_id).get("rigorous_lower") or 0)
    try:
        result = run_classical_descent(
            E.a_invariants(),
            basis,
            mode=cfg["engine"],
            timeout=cfg["timeout"],
            first_limit=cfg["first_limit"],
            second_limit=cfg["second_limit"],
            n_aux=cfg["n_aux"],
            lim1=cfg["lim1"],
            lim3=cfg["lim3"],
            limbigprime=cfg["limbigprime"],
        )
    except ClassicalDescentTimeout as exc:
        print(f"[auto:classical-covering] TIMEOUT: {exc}", flush=True)
        return {
            "scheduler_tier": "classical-covering",
            "status": "timeout",
            "engine": cfg["engine"],
            "known_basis": len(basis),
            "exact_points": 0,
            "rank_growth": 0,
            "error": str(exc),
            "attempt_identity": attempt_identity,
            "attempt_number": attempt_number,
            "attempt_complete": True,
            "mathematical_outcome": "timeout",
            "attempt_timeout": attempt_timeout,
            "retry_policy": retry_policy,
            "retry_timeout": retry_timeout,
        }
    except ClassicalDescentFailure as exc:
        print(f"[auto:classical-covering] ERROR: {exc}", flush=True)
        return {
            "scheduler_tier": "classical-covering",
            "status": "error",
            "engine": cfg["engine"],
            "known_basis": len(basis),
            "exact_points": 0,
            "rank_growth": 0,
            "error": str(exc),
            "attempt_identity": attempt_identity,
            "attempt_number": attempt_number,
            "attempt_complete": True,
            "mathematical_outcome": "error",
            "attempt_timeout": attempt_timeout,
            "retry_policy": retry_policy,
            "retry_timeout": retry_timeout,
        }

    upper = result.get("rigorous_upper")
    promotion = None
    upper_conflict = False
    persisted_upper = None
    if upper is not None:
        engine_name = (
            "simon_two_descent_known_points"
            if cfg["engine"] == "simon_known"
            else "eclib_mwrank_full_two_descent"
        )
        promotion = promote_rigorous_rank_interval(
            db,
            curve_id=int(curve_id),
            model=E.a_invariants(),
            rigorous_lower=None,
            rigorous_upper=int(upper),
            certificate=result,
            engine=engine_name,
            evidence_type="descent",
            source="auto_search_classical_covering",
            points_found=list(result.get("points") or []),
            options={
                "covering_engine": cfg["engine"],
                "known_basis_size": len(basis),
                "lim1": cfg["lim1"],
                "lim3": cfg["lim3"],
                "limbigprime": cfg["limbigprime"],
                "upper_bound_rigorous": bool(result.get("upper_bound_rigorous")),
                "reported_upper": result.get("reported_upper"),
                "first_limit": cfg["first_limit"],
                "second_limit": cfg["second_limit"],
                "n_aux": cfg["n_aux"],
            },
            metadata={"stage": "classical_covering"},
            minimal_model_a_invariants=result.get("minimal_model_a_invariants"),
            elapsed_seconds=result.get("runtime_seconds"),
        )
        upper_conflict = bool(promotion.get("rank_inconsistent"))
        persisted_upper = (
            None if upper_conflict else promotion.get("rigorous_upper")
        )
        if upper_conflict:
            print(
                f"[auto:classical-covering] EVIDENCE CONFLICT upper={int(upper)} "
                "recorded but not usable while rank state is inconsistent",
                flush=True,
            )
            log_event(
                db,
                curve_id,
                "warn",
                f"Classical covering returned contradictory upper {int(upper)}; "
                "recorded as conflicting rigorous evidence",
            )

    exact_points = 0
    seen = set()
    for xy in result.get("points") or []:
        try:
            P = E(QQ(str(xy[0])), QQ(str(xy[1])))
        except Exception:
            continue
        if P.is_zero():
            continue
        Q = -P
        key = min((str(P[0]), str(P[1])), (str(Q[0]), str(Q[1])))
        if key in seen:
            continue
        seen.add(key)
        upsert_point(
            db,
            curve_id=curve_id,
            x=P[0],
            y=P[1],
            source="auto_classical_2covering",
            role="candidate_extra",
            exact_verified=True,
            independence_status="unknown",
            rigorous_independent=False,
            search_ref=f"auto:classical-covering:{cfg['engine']}",
            metadata={
                "auto_search": True,
                "engine": cfg["engine"],
                "runtime_seconds": result.get("runtime_seconds"),
                "rigorous_upper": persisted_upper,
            },
        )
        exact_points += 1

    cert = certify_ledger_growth(
        db,
        curve_id=curve_id,
        E=E,
        source="auto_classical_2covering",
        search_ref=f"auto:classical-covering:{cfg['engine']}:exact",
        certificate_timeout=certificate_timeout,
        max_candidates=exact_candidates,
    )
    after = int(research_rank_state(db, curve_id).get("rigorous_lower") or 0)
    growth = max(0, after - before)
    print(
        f"[auto:classical-covering] completed upper={upper} "
        f"exact_points={exact_points} certified_growth={growth} rank>={after}",
        flush=True,
    )
    return {
        "scheduler_tier": "classical-covering",
        "tier": "classical-covering",
        "status": "completed",
        "engine": cfg["engine"],
        "known_basis": len(basis),
        "exact_points": exact_points,
        "rank_growth": growth,
        "rigorous_upper": persisted_upper,
        "effective_rigorous_upper": (
            None if promotion is None else promotion.get("rigorous_upper")
        ),
        "interval_evidence_id": (
            None if promotion is None else promotion.get("evidence_id")
        ),
        "reported_upper": result.get("reported_upper"),
        "upper_bound_rigorous": bool(result.get("upper_bound_rigorous")),
        "upper_bound_scope": result.get("upper_bound_scope"),
        "deprecated_engine": bool(result.get("deprecated_engine", False)),
        "upper_conflict": bool(upper_conflict),
        "descent_lower_reported": result.get("descent_lower"),
        "runtime_seconds": result.get("runtime_seconds"),
        "certificate_attempts": cert.get("attempts", 0),
        "basis_complete": cert.get("basis_complete", True),
        "attempt_identity": attempt_identity,
        "attempt_number": attempt_number,
        "attempt_complete": True,
        "mathematical_outcome": (
            "inconclusive" if upper_conflict else "completed"
        ),
        "attempt_timeout": attempt_timeout,
        "retry_policy": retry_policy,
        "retry_timeout": retry_timeout,
    }


__all__ = ["run_classical_covering_escalation"]
