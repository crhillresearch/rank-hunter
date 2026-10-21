"""Read-only Analysis method-history and budget comparison helpers.

These helpers extract reusable planning semantics from Hard-Case Escalator:
method identity, active-basis fingerprints, scoped prior attempts, and
equal-or-stronger budget dominance. They never launch work or mutate
scientific state.
"""
from __future__ import annotations

import hashlib
import json

from rank42.db import proven_lower


HARD_CASE_ENGINE = "rank42.hard_case_escalator"
HARD_CASE_STAGE_TYPE = "hard_case_stage"
EXHAUSTION_STATUSES = {
    "timeout",
    "error",
    "inconclusive",
    "completed",
    "interrupted",
}


def stage_options(rec):
    try:
        value = json.loads(rec["options_json"] or "{}")
    except Exception:
        value = {}
    return value if isinstance(value, dict) else {}


def budget_dominates(previous, current):
    """True when a prior budget is equal-or-stronger in every requested field."""
    previous = dict(previous or {})
    current = dict(current or {})
    for key, wanted in current.items():
        if wanted is None:
            continue
        try:
            wanted_n = int(wanted)
            prior_n = int(previous.get(key))
        except (TypeError, ValueError):
            if str(previous.get(key)) != str(wanted):
                return False
        else:
            if prior_n < wanted_n:
                return False
    return True


def basis_fingerprint_from_curve_row(row):
    """Fingerprint the replayable rigorous basis represented by one curve row."""
    required = int(proven_lower(row))
    try:
        raw = json.loads(row["generators_json"] or "[]")
    except Exception:
        raw = []
    coords = []
    if isinstance(raw, list):
        for xy in raw:
            if not isinstance(xy, (list, tuple)) or len(xy) < 2:
                continue
            coords.append((str(xy[0]), str(xy[1])))
            if len(coords) >= required:
                break
    if len(coords) < required:
        return None
    encoded = json.dumps(coords, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def latest_basis_fingerprint_before(db, curve_id, timestamp):
    """Return the newest durable basis fingerprint at or before a timestamp."""
    rows = db.execute(
        """
        SELECT options_json FROM rank_evidence
        WHERE curve_id=? AND created_at<=?
        ORDER BY id DESC
        LIMIT 500
        """,
        (int(curve_id), str(timestamp or "")),
    ).fetchall()
    for rec in rows:
        options = stage_options(rec)
        for key in (
            "basis_fingerprint",
            "basis_fingerprint_before",
            "basis_fingerprint_after",
        ):
            value = str(options.get(key) or "")
            if value:
                return value
    return None


def method_attempts(
    db,
    *,
    curve_id,
    method,
    basis_fingerprint,
    point_id=None,
    prime=None,
    point_scoped=True,
    engine=HARD_CASE_ENGINE,
    evidence_type=HARD_CASE_STAGE_TYPE,
    limit=500,
):
    """Return matching durable attempts newest-first for one method/context."""
    rows = db.execute(
        """
        SELECT * FROM rank_evidence
        WHERE curve_id=? AND engine=? AND evidence_type=?
        ORDER BY id DESC
        LIMIT ?
        """,
        (int(curve_id), str(engine), str(evidence_type), int(limit)),
    ).fetchall()
    out = []
    for rec in rows:
        options = stage_options(rec)
        if str(options.get("method") or "") != str(method):
            continue
        if str(options.get("basis_fingerprint") or "") != str(basis_fingerprint or ""):
            continue
        if point_scoped and int(options.get("point_id") or -1) != int(point_id or -1):
            continue
        if prime is not None and int(options.get("prime") or -1) != int(prime):
            continue
        out.append(rec)
    return out


def prior_stage_attempt(
    db,
    *,
    curve_id,
    method,
    basis_fingerprint,
    budget,
    point_id=None,
    prime=None,
    point_scoped=True,
    engine=HARD_CASE_ENGINE,
):
    """Return the newest equal-or-stronger durable attempt, if one exists."""
    for rec in method_attempts(
        db,
        curve_id=curve_id,
        method=method,
        basis_fingerprint=basis_fingerprint,
        point_id=point_id,
        prime=prime,
        point_scoped=point_scoped,
        engine=engine,
    ):
        if str(rec["status"] or "") not in EXHAUSTION_STATUSES:
            continue
        options = stage_options(rec)
        if budget_dominates(options.get("budget"), budget):
            return rec
    return None


def method_budget_state(
    db,
    *,
    curve_id,
    method,
    basis_fingerprint,
    budget,
    point_id=None,
    prime=None,
    point_scoped=False,
    engine=HARD_CASE_ENGINE,
):
    """Summarize whether a method is exhausted at a proposed budget."""
    rows = method_attempts(
        db,
        curve_id=curve_id,
        method=method,
        basis_fingerprint=basis_fingerprint,
        point_id=point_id,
        prime=prime,
        point_scoped=point_scoped,
        engine=engine,
    )
    relevant = [
        rec for rec in rows
        if str(rec["status"] or "") in EXHAUSTION_STATUSES
    ]
    dominating = None
    stronger_available = False
    normalized = []
    for rec in relevant:
        options = stage_options(rec)
        prior_budget = dict(options.get("budget") or {})
        if dominating is None and budget_dominates(prior_budget, budget):
            dominating = rec
        if (
            budget_dominates(budget, prior_budget)
            and not budget_dominates(prior_budget, budget)
        ):
            stronger_available = True
        normalized.append({
            "evidence_id": int(rec["id"]),
            "status": str(rec["status"] or ""),
            "budget": prior_budget,
            "created_at": rec["created_at"],
        })

    return {
        "method": str(method),
        "basis_fingerprint": basis_fingerprint,
        "requested_budget": dict(budget or {}),
        "attempted": bool(relevant),
        "attempt_count": len(relevant),
        "exhausted_at_budget": dominating is not None,
        "dominating_evidence_id": (
            None if dominating is None else int(dominating["id"])
        ),
        "dominating_status": (
            None if dominating is None else str(dominating["status"] or "")
        ),
        "stronger_budget_available": bool(
            relevant and dominating is None and stronger_available
        ),
        "retry_available": dominating is None,
        "attempts": normalized,
    }


def method_family_state(states):
    """Combine per-method states without hiding stronger retry opportunities."""
    rows = [dict(state) for state in (states or [])]
    attempted = [row for row in rows if row.get("attempted")]
    available = [row for row in rows if not row.get("exhausted_at_budget")]
    return {
        "methods": rows,
        "attempted_methods": len(attempted),
        "method_count": len(rows),
        "all_exhausted_at_budget": bool(rows) and not available,
        "available_methods": [row["method"] for row in available],
        "stronger_budget_available": any(
            bool(row.get("stronger_budget_available")) for row in rows
        ),
    }


UPPER_BOUND_METHOD_BUDGETS = (
    ("descent:mwrank_selmer", {"timeout": 300}),
    ("descent:simon_known", {"timeout": 300}),
    ("descent:mwrank_coverings", {"timeout": 300}),
    ("sage_proof_rank", {"timeout": 180}),
)


def upper_bound_method_history_map(
    db,
    curve_rows,
    *,
    method_budgets=UPPER_BOUND_METHOD_BUDGETS,
):
    """Bulk upper-bound history for read-only queue projections.

    This preserves upper_bound_method_history() semantics while avoiding four
    evidence queries per curve in Campaign-sized Work Center queues.
    """
    rows_by_curve = {}
    curves = {}
    for row in curve_rows or []:
        try:
            curve_id = int(row["id"])
        except (KeyError, TypeError, ValueError):
            continue
        curves[curve_id] = row
    if not curves:
        return {}

    ordered_ids = sorted(curves)
    for offset in range(0, len(ordered_ids), 800):
        chunk = ordered_ids[offset : offset + 800]
        marks = ",".join("?" for _ in chunk)
        evidence_rows = db.execute(
            f"""SELECT * FROM rank_evidence
                WHERE curve_id IN ({marks})
                  AND engine=? AND evidence_type=?
                ORDER BY id DESC""",
            [
                *chunk,
                HARD_CASE_ENGINE,
                HARD_CASE_STAGE_TYPE,
            ],
        ).fetchall()
        for rec in evidence_rows:
            rows_by_curve.setdefault(int(rec["curve_id"]), []).append(rec)

    out = {}
    for curve_id, curve_row in curves.items():
        fingerprint = basis_fingerprint_from_curve_row(curve_row)
        evidence_rows = rows_by_curve.get(curve_id, [])
        states = []
        for method, budget in method_budgets:
            relevant = []
            for rec in evidence_rows:
                if str(rec["status"] or "") not in EXHAUSTION_STATUSES:
                    continue
                options = stage_options(rec)
                if str(options.get("method") or "") != str(method):
                    continue
                if str(options.get("basis_fingerprint") or "") != str(fingerprint or ""):
                    continue
                relevant.append((rec, dict(options.get("budget") or {})))

            dominating = None
            stronger_available = False
            normalized = []
            for rec, prior_budget in relevant:
                if dominating is None and budget_dominates(prior_budget, budget):
                    dominating = rec
                if (
                    budget_dominates(budget, prior_budget)
                    and not budget_dominates(prior_budget, budget)
                ):
                    stronger_available = True
                normalized.append({
                    "evidence_id": int(rec["id"]),
                    "status": str(rec["status"] or ""),
                    "budget": prior_budget,
                    "created_at": rec["created_at"],
                })

            states.append({
                "method": str(method),
                "basis_fingerprint": fingerprint,
                "requested_budget": dict(budget or {}),
                "attempted": bool(relevant),
                "attempt_count": len(relevant),
                "exhausted_at_budget": dominating is not None,
                "dominating_evidence_id": (
                    None if dominating is None else int(dominating["id"])
                ),
                "dominating_status": (
                    None if dominating is None else str(dominating["status"] or "")
                ),
                "stronger_budget_available": bool(
                    relevant and dominating is None and stronger_available
                ),
                "retry_available": dominating is None,
                "attempts": normalized,
            })

        family = method_family_state(states)
        family["basis_fingerprint"] = fingerprint
        out[curve_id] = family
    return out


def upper_bound_method_history(
    db,
    *,
    curve_id,
    curve_row,
    method_budgets=UPPER_BOUND_METHOD_BUDGETS,
):
    """Summarize Hard-Case upper-bound history for Planner consumption."""
    fingerprint = basis_fingerprint_from_curve_row(curve_row)
    states = [
        method_budget_state(
            db,
            curve_id=int(curve_id),
            method=str(method),
            basis_fingerprint=fingerprint,
            budget=dict(budget),
            point_scoped=False,
        )
        for method, budget in method_budgets
    ]
    family = method_family_state(states)
    family["basis_fingerprint"] = fingerprint
    return family
