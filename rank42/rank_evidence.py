"""Scientific rank-evidence ledger and conservative reducer.

The ledger deliberately separates rigorous algebraic bounds from conditional
Mordell-Weil bounds, conditional analytic-rank bounds, and numerical/heuristic
signals. An exact rank is derived only from matching rigorous lower and upper
bounds.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from rank42.db import get_curve, now, proven_lower, update_curve


RIGOROUS_TYPES = {
    "rank_bounds",
    "descent",
    "exact_certificate",
    "certified_subgroup",
    "brumer_kramer_2selmer",
    "cassels_tate_refinement",
    "isogeny_descent_2",
}


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def evidence_key(*, curve_id: int, model, engine: str, engine_version=None, evidence_type="rank_bounds", options=None) -> str:
    payload = {
        "curve_id": int(curve_id),
        "model": [str(x) for x in model],
        "engine": str(engine),
        "engine_version": None if engine_version is None else str(engine_version),
        "evidence_type": str(evidence_type),
        "options": options or {},
    }
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()


def normalize_evidence(data: Mapping[str, Any]) -> dict:
    out = dict(data)
    out["engine"] = str(out.get("engine") or "unknown")
    out["evidence_type"] = str(out.get("evidence_type") or "rank_bounds")
    out["status"] = str(out.get("status") or "completed")
    out["rigorous"] = bool(out.get("rigorous", out["evidence_type"] in RIGOROUS_TYPES))
    for key in ("rigorous_lower", "rigorous_upper", "exact_rank", "conditional_analytic_upper", "conditional_mw_upper", "numerical_rank_signal"):
        value = out.get(key)
        out[key] = None if value is None else int(value)
        if out[key] is not None and out[key] < 0:
            raise ValueError(f"{key} must be non-negative or null")
    if out["rigorous_lower"] is not None and out["rigorous_upper"] is not None:
        if out["rigorous_lower"] > out["rigorous_upper"]:
            raise ValueError("rigorous lower bound exceeds rigorous upper bound")
    # Exact claims are never trusted merely because an engine emitted an integer.
    # They are retained only when the evidence itself is rigorous and the bounds meet.
    if not (
        out["rigorous"]
        and out["rigorous_lower"] is not None
        and out["rigorous_upper"] is not None
        and out["rigorous_lower"] == out["rigorous_upper"]
    ):
        out["exact_rank"] = None
    else:
        out["exact_rank"] = int(out["rigorous_lower"])
    out["timed_out"] = bool(out.get("timed_out", out["status"] == "timeout"))
    out["partial"] = bool(out.get("partial", False))
    out["assumptions"] = list(out.get("assumptions") or [])
    out["points_found"] = list(out.get("points_found") or [])
    out["options"] = dict(out.get("options") or {})
    return out


def record_rank_evidence(db, *, curve_id: int, model, data: Mapping[str, Any], key: str | None = None) -> int:
    rec = normalize_evidence(data)
    model = [str(x) for x in model]
    key = key or evidence_key(
        curve_id=int(curve_id), model=model, engine=rec["engine"],
        engine_version=rec.get("engine_version"), evidence_type=rec["evidence_type"], options=rec["options"],
    )
    ts = now()
    db.execute(
        """
        INSERT INTO rank_evidence(
            curve_id,evidence_key,engine,engine_version,sage_version,evidence_type,rigorous,
            rigorous_lower,rigorous_upper,exact_rank,conditional_analytic_upper,conditional_mw_upper,numerical_rank_signal,
            assumptions_json,status,timed_out,partial,model_a_invariants_json,minimal_model_a_invariants_json,
            options_json,points_json,stdout_summary,stderr_summary,started_at,finished_at,elapsed_seconds,
            created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(evidence_key) DO UPDATE SET
            status=excluded.status,rigorous=excluded.rigorous,rigorous_lower=excluded.rigorous_lower,
            rigorous_upper=excluded.rigorous_upper,exact_rank=excluded.exact_rank,
            conditional_analytic_upper=excluded.conditional_analytic_upper,
            conditional_mw_upper=excluded.conditional_mw_upper,
            numerical_rank_signal=excluded.numerical_rank_signal,assumptions_json=excluded.assumptions_json,
            timed_out=excluded.timed_out,partial=excluded.partial,
            minimal_model_a_invariants_json=excluded.minimal_model_a_invariants_json,
            points_json=excluded.points_json,stdout_summary=excluded.stdout_summary,
            stderr_summary=excluded.stderr_summary,started_at=excluded.started_at,
            finished_at=excluded.finished_at,elapsed_seconds=excluded.elapsed_seconds,updated_at=excluded.updated_at
        """,
        (
            int(curve_id), key, rec["engine"], rec.get("engine_version"), rec.get("sage_version"),
            rec["evidence_type"], 1 if rec["rigorous"] else 0, rec["rigorous_lower"], rec["rigorous_upper"],
            rec["exact_rank"], rec["conditional_analytic_upper"], rec["conditional_mw_upper"], rec["numerical_rank_signal"],
            _json(rec["assumptions"]), rec["status"], 1 if rec["timed_out"] else 0, 1 if rec["partial"] else 0,
            _json(model), _json(rec.get("minimal_model_a_invariants")) if rec.get("minimal_model_a_invariants") else None,
            _json(rec["options"]), _json(rec["points_found"]), rec.get("stdout_summary"), rec.get("stderr_summary"),
            rec.get("started_at"), rec.get("finished_at"), rec.get("elapsed_seconds"), ts, ts,
        ),
    )
    db.commit()
    row = db.execute("SELECT id FROM rank_evidence WHERE evidence_key=?", (key,)).fetchone()
    return int(row["id"])


def list_rank_evidence(db, curve_id: int, *, limit=100):
    return db.execute(
        "SELECT * FROM rank_evidence WHERE curve_id=? ORDER BY id DESC LIMIT ?",
        (int(curve_id), int(limit)),
    ).fetchall()


def cached_rank_evidence(db, *, key: str):
    return db.execute("SELECT * FROM rank_evidence WHERE evidence_key=?", (str(key),)).fetchone()


def reduce_rank_records(curve_row, evidence) -> dict:
    """Reduce one curve row plus rank-evidence rows into authoritative rank state.

    This pure record reducer is the shared semantic boundary for both single-curve
    and bulk Curve Research State queries.  Legacy curve columns remain valid
    compatibility inputs; structured evidence can strengthen them, but incomplete
    or non-rigorous evidence cannot create a rigorous upper/exact rank.
    """
    if curve_row is None:
        raise ValueError("curve row is required")

    curve_id = int(curve_row["id"])
    stored_lower = int(proven_lower(curve_row))
    rigorous_lowers = [stored_lower]
    specialization_lowers = []
    if curve_row["exact_rank"] is not None:
        specialization_lowers.append(int(curve_row["exact_rank"]))
    if curve_row["descent_lower"] is not None:
        specialization_lowers.append(int(curve_row["descent_lower"]))
    rigorous_uppers = []

    # Preserve legacy rigorous curve state after the additive evidence migration.
    if curve_row["exact_rank"] is not None:
        rigorous_uppers.append(int(curve_row["exact_rank"]))
    elif curve_row["descent_upper"] is not None:
        rigorous_uppers.append(int(curve_row["descent_upper"]))

    conditional_analytic_uppers = []
    conditional_mw_uppers = []
    numerical_signals = []
    for rec in evidence:
        if int(rec["rigorous"] or 0):
            if rec["rigorous_lower"] is not None:
                value = int(rec["rigorous_lower"])
                rigorous_lowers.append(value)
                specialization_lowers.append(value)
            if rec["rigorous_upper"] is not None and str(rec["status"]) == "completed":
                rigorous_uppers.append(int(rec["rigorous_upper"]))
        if rec["conditional_analytic_upper"] is not None:
            conditional_analytic_uppers.append(int(rec["conditional_analytic_upper"]))
        if rec["conditional_mw_upper"] is not None:
            conditional_mw_uppers.append(int(rec["conditional_mw_upper"]))
        if rec["numerical_rank_signal"] is not None:
            numerical_signals.append(int(rec["numerical_rank_signal"]))

    lower = max(rigorous_lowers) if rigorous_lowers else 0
    specialization_lower = max(specialization_lowers) if specialization_lowers else 0
    upper = min(rigorous_uppers) if rigorous_uppers else None
    inconsistent = upper is not None and upper < lower
    exact = lower if upper is not None and lower == upper and not inconsistent else None
    if exact is not None:
        specialization_lower = max(specialization_lower, int(exact))
    return {
        "curve_id": curve_id,
        "rigorous_lower": lower,
        "specialization_rigorous_lower": specialization_lower,
        "rigorous_upper": upper,
        "exact_rank": exact,
        "conditional_analytic_upper": min(conditional_analytic_uppers) if conditional_analytic_uppers else None,
        "conditional_mw_upper": min(conditional_mw_uppers) if conditional_mw_uppers else None,
        "numerical_rank_signal": max(numerical_signals) if numerical_signals else None,
        "inconsistent": inconsistent,
    }


def reduce_rank_state(db, curve_id: int) -> dict:
    row = get_curve(db, int(curve_id))
    if row is None:
        raise ValueError(f"curve #{int(curve_id)} not found")
    evidence = list_rank_evidence(db, int(curve_id), limit=10000)
    return reduce_rank_records(row, evidence)


def apply_reduced_rank_state(db, curve_id: int) -> dict:
    """Project rigorous evidence back into legacy curve columns conservatively."""
    state = reduce_rank_state(db, int(curve_id))
    row = get_curve(db, int(curve_id))
    if state["inconsistent"]:
        return state
    fields = {}
    current_descent_lower = int(row["descent_lower"] or 0)
    current_generic_lower = int(row["generic_lower"] or 0)
    if state["rigorous_lower"] > max(current_descent_lower, current_generic_lower):
        fields["descent_lower"] = int(state["rigorous_lower"])
    if state["rigorous_upper"] is not None:
        old_upper = row["descent_upper"]
        if old_upper is None or int(state["rigorous_upper"]) < int(old_upper):
            fields["descent_upper"] = int(state["rigorous_upper"])
    if state["exact_rank"] is not None:
        old_exact = row["exact_rank"]
        if old_exact is None or int(old_exact) == int(state["exact_rank"]):
            fields["exact_rank"] = int(state["exact_rank"])
            fields["certain"] = 1
            fields["status"] = "exact"
    if fields:
        update_curve(db, int(curve_id), **fields)
    return state
