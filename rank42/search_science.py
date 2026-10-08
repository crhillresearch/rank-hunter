"""Shared Search scientific helpers independent of legacy Auto orchestration.

These helpers are intentionally UI/orchestrator-neutral so Pipeline and legacy
Search paths can share exact scientific behavior without Pipeline importing
rank42.auto_search.
"""
from __future__ import annotations

from rank42.curve_research_state import get_curve_research_state
from rank42.db import log_event
from rank42.descent import DescentFailure, DescentTimeout, run_descent
from rank42.point_promotion import promote_rigorous_rank_interval


def research_rank_state(db, curve_id):
    """Read reducer-backed rank truth without compatibility-column dependence."""
    state = get_curve_research_state(db, int(curve_id))
    inconsistent = bool(state.get("rank_inconsistent", False))
    return {
        "rigorous_lower": int(state.get("rigorous_lower") or 0),
        "specialization_rigorous_lower": int(
            state.get("specialization_rigorous_lower") or 0
        ),
        "rigorous_upper": state.get("rigorous_upper"),
        "exact_rank": state.get("exact_rank"),
        "inconsistent": inconsistent,
        "rank_inconsistent": inconsistent,
    }


def try_pari_upper(db, curve_id, E, timeout, *, phase):
    """Run bounded PARI quick descent and promote only rigorous interval evidence."""
    timeout = max(1, int(timeout))
    prior_state = research_rank_state(db, curve_id)
    prior_upper = prior_state.get("rigorous_upper")

    def _attempt_result(state, *, attempt_status, new_upper=None, failure_class=None):
        effective_upper = state.get("rigorous_upper")
        upper_source = None
        if effective_upper is not None:
            if (
                attempt_status == "completed"
                and new_upper is not None
                and int(effective_upper) == int(new_upper)
                and (prior_upper is None or int(new_upper) <= int(prior_upper))
            ):
                upper_source = "current_attempt"
            else:
                upper_source = "prior_evidence"
        return {
            **state,
            "attempt_status": str(attempt_status),
            "new_upper": None if new_upper is None else int(new_upper),
            "effective_rigorous_upper": (
                None if effective_upper is None else int(effective_upper)
            ),
            "upper_source": upper_source,
            "attempt_engine": "pari",
            "attempt_phase": str(phase),
            "attempt_timeout": timeout,
            "failure_class": failure_class,
        }

    try:
        result = run_descent(
            {"mode": "quick_pari", "a_invariants": [str(x) for x in E.a_invariants()]},
            timeout=timeout,
        )
    except DescentTimeout as exc:
        print(f"[auto:upper] {phase} PARI timeout ({exc})", flush=True)
        log_event(db, curve_id, "warn", f"Auto Hunter PARI upper-bound timeout during {phase}")
        return _attempt_result(
            research_rank_state(db, curve_id),
            attempt_status="timeout",
            failure_class="timeout",
        )
    except DescentFailure as exc:
        print(f"[auto:upper] {phase} PARI inconclusive ({exc.failure_class})", flush=True)
        log_event(
            db,
            curve_id,
            "warn",
            f"Auto Hunter PARI upper-bound inconclusive during {phase}: {exc.failure_class}",
        )
        return _attempt_result(
            research_rank_state(db, curve_id),
            attempt_status="inconclusive",
            failure_class=str(exc.failure_class),
        )

    upper = int(result["upper"])
    state = promote_rigorous_rank_interval(
        db,
        curve_id=curve_id,
        model=E.a_invariants(),
        rigorous_lower=None,
        rigorous_upper=upper,
        certificate=result,
        engine="pari",
        evidence_type="rank_bounds",
        source="auto_search",
        engine_version=result.get("engine_version"),
        sage_version=result.get("sage_version"),
        options={
            "phase": str(phase),
            "timeout": timeout,
        },
        metadata={
            "attempt_kind": "quick_pari_upper",
            "phase": str(phase),
        },
        elapsed_seconds=result.get("_runtime_seconds"),
    )
    print(
        f"[auto:upper] {phase} rigorous interval "
        f"{state['rigorous_lower']} <= rank"
        + (f" <= {state['rigorous_upper']}" if state["rigorous_upper"] is not None else ""),
        flush=True,
    )
    return _attempt_result(
        state,
        attempt_status="completed",
        new_upper=upper,
    )


__all__ = ["research_rank_state", "try_pari_upper"]
