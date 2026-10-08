"""Operator-facing inventory helpers for local elliptic curves.

This module contains no search mathematics.  It summarizes rigorous local rank
state together with already-recorded catalog checks and determines whether an
ICARM submission action is appropriate.  Catalog *absence* is always labelled
as source-specific and never promoted to a global novelty claim.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone

from rank42.curve_arithmetic_state import curve_arithmetic_state_map
from rank42.curve_research_state import list_curve_research_states
from rank42.db import proven_lower
from rank42.submission import certified_witness_lower, icarm_api_body, stored_bad_primes


def _parse_time(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def rank_display(row) -> str:
    if row["exact_rank"] is not None:
        return f"= {int(row['exact_rank'])}"
    if "rigorous_lower" in row.keys():
        lower = int(row["rigorous_lower"] or 0)
    else:
        lower = int(proven_lower(row))
    return f">= {lower}" if lower > 0 else "—"


def witness_count(row) -> int:
    try:
        return len(json.loads(row["generators_json"] or "[]"))
    except Exception:
        return 0



def log_conductor(row):
    """Natural logarithm of the stored conductor N, or None if unavailable."""
    try:
        value = int(str(row["conductor"]))
        if value <= 0:
            return None
        return math.log(value)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def arithmetic_source_label(state: dict, field: str) -> str:
    """Compact provenance label for operator-facing inventory columns."""
    info = (state.get("provenance") or {}).get(field) or {}
    kind = str(info.get("kind") or "missing")
    source = str(info.get("source") or "").strip()
    if kind == "external_reference":
        label = f"{source.upper()} reference" if source else "external reference"
    elif kind == "external_reference_cached_local":
        label = f"cached {source.upper()} reference" if source else "cached external reference"
    elif kind == "local_computed":
        label = str(info.get("method") or "local computed")
    elif kind == "local_persisted":
        label = "local"
    elif kind == "derived":
        label = "derived"
    else:
        label = "—"
    if field in set(state.get("conflicts") or []):
        label += " · conflict"
    return label

def load_checks_by_curve(db) -> dict[int, dict[str, dict]]:
    rows = db.execute(
        "SELECT * FROM curve_catalog_checks ORDER BY curve_id, source"
    ).fetchall()
    out: dict[int, dict[str, dict]] = {}
    for row in rows:
        out.setdefault(int(row["curve_id"]), {})[str(row["source"])] = dict(row)
    return out


def source_summary(check: dict | None, source: str) -> str:
    if not check:
        return "Not checked"
    status = str(check.get("status") or "unknown")
    if source == "icarm":
        if status == "known":
            rank = check.get("source_rank")
            suffix = f" · >= {int(rank)}" if rank is not None else ""
            return f"ICARM #{check.get('source_label') or '?'}{suffix}"
        if status == "not_found_synced":
            return "No ICARM match"
        if status == "not_synced":
            return "ICARM not synced"
    if source == "lmfdb":
        if status == "known":
            rank = check.get("source_rank")
            suffix = f" · rank {int(rank)}" if rank is not None else ""
            return f"Known · {check.get('source_label') or 'match'}{suffix}"
        if status == "not_found_complete_range":
            return "Not in LMFDB · complete range"
        if status == "not_found":
            return "Not found in checked LMFDB data"
        if status == "check_failed":
            return "Lookup failed"
    return status.replace("_", " ")


def icarm_check_is_current(db, check: dict | None, *, snapshot_fetched_at=None) -> bool:
    if not check or check.get("status") not in {"known", "not_found_synced"}:
        return False
    if snapshot_fetched_at is None:
        snapshot = db.execute(
            "SELECT fetched_at FROM external_catalogs WHERE source='icarm'"
        ).fetchone()
        snapshot_fetched_at = snapshot["fetched_at"] if snapshot is not None else None
    if not snapshot_fetched_at:
        return False
    checked = _parse_time(check.get("checked_at"))
    fetched = _parse_time(snapshot_fetched_at)
    return bool(checked and fetched and checked >= fetched)


def icarm_action_state(db, row, check: dict | None, *, snapshot_fetched_at=None) -> dict:
    """Return the safe operator action for one local curve.

    ``ready_create`` means the latest locally synchronized ICARM snapshot was
    checked by exact Q-isomorphism and contained no match. ``ready_improve``
    means the curve is already present but the local explicit certified witness
    basis proves a strictly stronger lower bound.
    """
    try:
        payload = icarm_api_body(row, require_bad_primes=False)
        witness_lower = len(payload["points"])
        bad_primes_ready = stored_bad_primes(row) is not None
        ready = True
        reason = None
    except Exception as exc:
        payload = None
        witness_lower = certified_witness_lower(row)
        bad_primes_ready = False
        ready = False
        reason = str(exc)

    if not ready:
        return {
            "state": "not_ready",
            "label": "ICARM submission not ready",
            "reason": reason,
            "witness_lower": int(witness_lower or 0),
            "payload": payload,
        }

    if not icarm_check_is_current(db, check, snapshot_fetched_at=snapshot_fetched_at):
        return {
            "state": "needs_check",
            "label": "Check ICARM first",
            "reason": "A current synchronized ICARM Q-isomorphism check is required before submission.",
            "witness_lower": witness_lower,
            "payload": payload,
        }

    status = str(check.get("status") or "")
    if status == "known":
        source_rank = int(check.get("source_rank") or 0)
        source_id = str(check.get("source_label") or "")
        if witness_lower > source_rank:
            if not bad_primes_ready:
                return {
                    "state": "needs_metadata",
                    "label": "COMPUTE BAD PRIMES",
                    "reason": "Bad-prime metadata is missing. Compute it from the global minimal model before improving ICARM.",
                    "next_state": "ready_improve",
                    "source_id": source_id,
                    "source_rank": source_rank,
                    "witness_lower": witness_lower,
                    "payload": payload,
                }
            return {
                "state": "ready_improve",
                "label": f"IMPROVE ICARM TO >= {witness_lower}",
                "reason": f"ICARM #{source_id} currently records rank >= {source_rank}.",
                "source_id": source_id,
                "source_rank": source_rank,
                "witness_lower": witness_lower,
                "payload": payload,
            }
        return {
            "state": "already_current",
            "label": f"ICARM #{source_id} ALREADY >= {source_rank}",
            "reason": "The local witness set does not improve ICARM's recorded lower bound.",
            "source_id": source_id,
            "source_rank": source_rank,
            "witness_lower": witness_lower,
            "payload": payload,
        }

    if status == "not_found_synced":
        if not bad_primes_ready:
            return {
                "state": "needs_metadata",
                "label": "COMPUTE BAD PRIMES",
                "reason": "Bad-prime metadata is missing. Compute it from the global minimal model before submitting to ICARM.",
                "next_state": "ready_create",
                "witness_lower": witness_lower,
                "payload": payload,
            }
        return {
            "state": "ready_create",
            "label": "SUBMIT TO ICARM",
            "reason": "No Q-isomorphic curve was found in the current synchronized ICARM leaderboard snapshot.",
            "witness_lower": witness_lower,
            "payload": payload,
        }

    return {
        "state": "needs_check",
        "label": "Check ICARM first",
        "reason": "ICARM status is not current enough for a submission decision.",
        "witness_lower": witness_lower,
        "payload": payload,
    }


def inventory_rows(db, *, family: str | None = None, min_lower: int = 0) -> list[dict]:
    """Operator inventory using authoritative rank and arithmetic read models.

    ICARM submission readiness intentionally still receives the raw local curve
    row so external arithmetic fallback cannot satisfy local submission
    evidence requirements.
    """
    checks = load_checks_by_curve(db)
    states = list_curve_research_states(
        db,
        family=str(family) if family else None,
        minimum_lower=int(min_lower),
    )
    # Preserve the legacy inventory tie-break (rank, then newest curve id)
    # while replacing only the source of rank truth.
    states.sort(
        key=lambda state: (
            -int(state["rigorous_lower"] or 0),
            -int(state["curve_id"]),
        )
    )
    curve_ids = [int(state["curve_id"]) for state in states]
    arithmetic = curve_arithmetic_state_map(db, curve_ids)
    snapshot = db.execute("SELECT fetched_at FROM external_catalogs WHERE source='icarm'").fetchone()
    snapshot_fetched_at = snapshot["fetched_at"] if snapshot is not None else None

    out = []
    for state in states:
        row = state["curve"]
        cid = int(state["curve_id"])
        lower = int(state["rigorous_lower"] or 0)
        exact = state["exact_rank"]
        arithmetic_state = arithmetic[cid]
        cks = checks.get(cid, {})

        icarm_action = icarm_action_state(
            db,
            row,
            cks.get("icarm"),
            snapshot_fetched_at=snapshot_fetched_at,
        )
        action_text = {
            "ready_create": "SUBMIT",
            "ready_improve": f"IMPROVE >= {icarm_action.get('witness_lower')}",
            "already_current": "CURRENT",
            "needs_check": "CHECK FIRST",
            "not_ready": "NOT READY",
            "needs_metadata": "COMPUTE PRIMES",
        }.get(icarm_action.get("state"), str(icarm_action.get("state") or ""))

        rank_row = {
            "exact_rank": exact,
            "rigorous_lower": lower,
        }
        log_n = arithmetic_state["log_conductor"]
        out.append(
            {
                "ID": cid,
                "Family": str(state["family"]),
                "Parameter": str(state["parameter"]),
                "Rank": rank_display(rank_row),
                "Proven lower": lower,
                "Exact rank": int(exact) if exact is not None else None,
                "Rank conflict": bool(state["rank_inconsistent"]),
                "Witnesses": witness_count(row),
                "log N": round(log_n, 6) if log_n is not None else None,
                "N source": arithmetic_source_label(arithmetic_state, "conductor"),
                "N conflict": "conductor" in set(arithmetic_state.get("conflicts") or []),
                "LMFDB": source_summary(cks.get("lmfdb"), "lmfdb"),
                "ICARM": source_summary(cks.get("icarm"), "icarm"),
                "ICARM action": action_text,
                "Status": str(state["status"]),
                "Updated": str(row["updated_at"] or ""),
            }
        )
    return out
