"""Bounded, resumable arithmetic backfill for durable Curves inventory rows.

This orchestrator deliberately reuses Rank Hunter's existing arithmetic engines:
* rank42.curve_metadata --core-only for minimal discriminant, bad primes, root number
* rank42.conductor_backfill for conductor
* rank42.curve_size_metrics for naive/Faltings height
* rank42.torsion for exact rational torsion

Every scientific stage has a hard per-curve timeout. Timeouts and failures remain
unresolved research state; they are never converted into zero/empty invariants.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from rank42.conductor_backfill import backfill_conductors
from rank42.curve_research_state import curve_rank_summary_map
from rank42.curve_size_metrics import backfill_curve_size_metrics
from rank42.db import connect, get_curve, now
from rank42.torsion import enrich_retained_curve_torsion, ensure_torsion_schema


CORE_STAGE = "core"
_CORE_ATTEMPT_SQL = """
CREATE TABLE IF NOT EXISTS curve_arithmetic_backfill_attempts (
    curve_id INTEGER NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT,
    attempted_at TEXT NOT NULL,
    PRIMARY KEY(curve_id, stage),
    FOREIGN KEY(curve_id) REFERENCES curves(id) ON DELETE CASCADE
)
"""


def _table_exists(db, name: str) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (str(name),),
    ).fetchone() is not None


def ensure_schema(db):
    db.execute(_CORE_ATTEMPT_SQL)
    db.commit()


def _manual_import_ids(db) -> set[int]:
    if not _table_exists(db, "events"):
        return set()
    return {
        int(row["curve_id"])
        for row in db.execute(
            """SELECT DISTINCT curve_id
               FROM events
               WHERE curve_id IS NOT NULL
                 AND message LIKE 'Manual external curve import%'"""
        ).fetchall()
    }


def _eligible_rows(db) -> list[dict]:
    rows = db.execute(
        """SELECT id,status,a_invariants_json,discriminant,bad_primes_json,
                  root_number,conductor,torsion_label,torsion_error
           FROM curves
           WHERE a_invariants_json IS NOT NULL
             AND TRIM(a_invariants_json) <> ''
             AND COALESCE(status,'') <> 'pruned'
           ORDER BY id"""
    ).fetchall()
    if not rows:
        return []

    ids = [int(row["id"]) for row in rows]
    states = curve_rank_summary_map(db, ids)
    manual = _manual_import_ids(db)
    out = []
    for raw in rows:
        curve_id = int(raw["id"])
        state = states[curve_id]
        if (
            int(state.get("rigorous_lower") or 0) <= 0
            and state.get("exact_rank") is None
            and curve_id not in manual
        ):
            continue
        row = dict(raw)
        row["rigorous_lower"] = int(state.get("rigorous_lower") or 0)
        row["exact_rank"] = state.get("exact_rank")
        out.append(row)

    out.sort(
        key=lambda row: (
            -int(row.get("rigorous_lower") or 0),
            row.get("exact_rank") is None,
            int(row["id"]),
        )
    )
    return out


def _sidecar_maps(db):
    core_attempts = {}
    if _table_exists(db, "curve_arithmetic_backfill_attempts"):
        core_attempts = {
            int(row["curve_id"]): dict(row)
            for row in db.execute(
                """SELECT curve_id,stage,status,detail,attempted_at
                   FROM curve_arithmetic_backfill_attempts
                   WHERE stage=?""",
                (CORE_STAGE,),
            ).fetchall()
        }

    conductor_attempts = {}
    if _table_exists(db, "conductor_backfill_attempts"):
        conductor_attempts = {
            int(row["curve_id"]): dict(row)
            for row in db.execute(
                """SELECT curve_id,status,method,detail,attempted_at
                   FROM conductor_backfill_attempts"""
            ).fetchall()
        }

    sizes = {}
    if _table_exists(db, "curve_size_metrics"):
        sizes = {
            int(row["curve_id"]): dict(row)
            for row in db.execute(
                """SELECT curve_id,naive_height,faltings_height,method,computed_at
                   FROM curve_size_metrics"""
            ).fetchall()
        }
    return core_attempts, conductor_attempts, sizes


def _present(value) -> bool:
    return value not in (None, "")


def _stage_state(row, *, core_attempt=None, conductor_attempt=None, size=None):
    core_complete = (
        _present(row.get("discriminant"))
        and _present(row.get("bad_primes_json"))
        and row.get("root_number") in (-1, 1)
    )
    conductor_complete = _present(row.get("conductor"))
    size = dict(size or {})
    size_complete = (
        _present(size.get("naive_height"))
        and _present(size.get("faltings_height"))
    )
    torsion_complete = _present(row.get("torsion_label"))

    core_status = str((core_attempt or {}).get("status") or "")
    conductor_status = str((conductor_attempt or {}).get("status") or "")
    size_method = str(size.get("method") or "")
    torsion_error = str(row.get("torsion_error") or "")

    blocked = {
        "core": (not core_complete and core_status in {"timeout", "error", "partial"}),
        "conductor": (
            not conductor_complete
            and conductor_status in {"timeout", "error"}
        ),
        "size": (
            not size_complete
            and (
                size_method.startswith("timeout:")
                or size_method.startswith("error:")
            )
        ),
        "torsion": (not torsion_complete and bool(torsion_error)),
    }
    missing = {
        "core": not core_complete,
        "conductor": not conductor_complete,
        "size": not size_complete,
        "torsion": not torsion_complete,
    }
    complete = not any(missing.values())
    actionable = any(
        missing[name] and not blocked[name]
        for name in missing
    )
    return {
        "complete": complete,
        "missing": missing,
        "blocked": blocked,
        "actionable": actionable,
        "core_status": core_status,
        "conductor_status": conductor_status,
        "size_method": size_method,
        "torsion_error": torsion_error,
    }


def arithmetic_backfill_status(db) -> dict:
    rows = _eligible_rows(db)
    core_attempts, conductor_attempts, sizes = _sidecar_maps(db)
    summary = {
        "eligible": len(rows),
        "complete": 0,
        "unresolved": 0,
        "actionable": 0,
        "blocked": 0,
        "timeout_stages": 0,
        "missing_core": 0,
        "missing_conductor": 0,
        "missing_size": 0,
        "missing_torsion": 0,
    }

    for row in rows:
        curve_id = int(row["id"])
        state = _stage_state(
            row,
            core_attempt=core_attempts.get(curve_id),
            conductor_attempt=conductor_attempts.get(curve_id),
            size=sizes.get(curve_id),
        )
        if state["complete"]:
            summary["complete"] += 1
        else:
            summary["unresolved"] += 1
            if state["actionable"]:
                summary["actionable"] += 1
            else:
                summary["blocked"] += 1

        for name in ("core", "conductor", "size", "torsion"):
            if state["missing"][name]:
                summary["missing_" + name] += 1

        summary["timeout_stages"] += int(state["core_status"] == "timeout")
        summary["timeout_stages"] += int(state["conductor_status"] == "timeout")
        summary["timeout_stages"] += int(state["size_method"].startswith("timeout:"))
        summary["timeout_stages"] += int(
            "timeout" in state["torsion_error"].lower()
            or "timed out" in state["torsion_error"].lower()
        )
    return summary


def _select_batch_ids(db, *, limit: int | None, retry_unresolved: bool) -> list[int]:
    rows = _eligible_rows(db)
    core_attempts, conductor_attempts, sizes = _sidecar_maps(db)
    selected = []
    for row in rows:
        curve_id = int(row["id"])
        state = _stage_state(
            row,
            core_attempt=core_attempts.get(curve_id),
            conductor_attempt=conductor_attempts.get(curve_id),
            size=sizes.get(curve_id),
        )
        if state["complete"]:
            continue
        if not retry_unresolved and not state["actionable"]:
            continue
        selected.append(curve_id)
        if limit is not None and len(selected) >= max(0, int(limit)):
            break
    return selected


def _record_core_attempt(db, curve_id: int, *, status: str, detail=None):
    ensure_schema(db)
    db.execute(
        """INSERT INTO curve_arithmetic_backfill_attempts(
               curve_id,stage,status,detail,attempted_at
           ) VALUES(?,?,?,?,?)
           ON CONFLICT(curve_id,stage) DO UPDATE SET
               status=excluded.status,
               detail=excluded.detail,
               attempted_at=excluded.attempted_at""",
        (int(curve_id), CORE_STAGE, str(status), detail, now()),
    )
    db.commit()


def _run_core_worker(
    db_path,
    curve_id: int,
    *,
    timeout_seconds: int,
    executable=None,
    runner=None,
):
    cmd = [
        str(executable or sys.executable),
        "-m",
        "rank42.curve_metadata",
        "--db",
        str(Path(db_path).resolve()),
        "--id",
        str(int(curve_id)),
        "--core-only",
    ]
    run = runner or subprocess.run
    try:
        proc = run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout_seconds)),
            check=False,
            start_new_session=True,
        )
    except subprocess.TimeoutExpired:
        return "timeout", f"timeout after {max(1, int(timeout_seconds))}s"
    except OSError as exc:
        return "error", f"could not start core metadata worker: {exc}"

    if int(getattr(proc, "returncode", 1) or 0) != 0:
        detail = (
            getattr(proc, "stderr", "")
            or getattr(proc, "stdout", "")
            or "core metadata worker failed"
        ).strip()
        return "error", detail[-1200:]
    return "done", None


def _backfill_core(
    db,
    db_path,
    curve_ids,
    *,
    timeout_seconds: int,
    retry_unresolved: bool,
):
    ensure_schema(db)
    attempts = {
        int(row["curve_id"]): dict(row)
        for row in db.execute(
            """SELECT curve_id,status,detail
               FROM curve_arithmetic_backfill_attempts
               WHERE stage=?""",
            (CORE_STAGE,),
        ).fetchall()
    }
    result = {
        "selected": 0,
        "updated": 0,
        "timeouts": 0,
        "failed": 0,
        "partial": 0,
        "skipped_blocked": 0,
    }

    for index, curve_id in enumerate(curve_ids, 1):
        row = get_curve(db, int(curve_id))
        if row is None:
            continue
        complete = (
            _present(row["discriminant"])
            and _present(row["bad_primes_json"])
            and row["root_number"] in (-1, 1)
        )
        if complete:
            continue

        prior = str((attempts.get(int(curve_id)) or {}).get("status") or "")
        if (
            not retry_unresolved
            and prior in {"timeout", "error", "partial"}
        ):
            result["skipped_blocked"] += 1
            continue

        result["selected"] += 1
        print(
            f"[core {index}/{len(curve_ids)}] curve #{curve_id}: "
            f"minimal discriminant / bad primes / root number "
            f"(timeout {max(1, int(timeout_seconds))}s)...",
            flush=True,
        )
        status, detail = _run_core_worker(
            db_path,
            int(curve_id),
            timeout_seconds=timeout_seconds,
        )
        if status == "timeout":
            result["timeouts"] += 1
            _record_core_attempt(db, curve_id, status="timeout", detail=detail)
            print(f"[core] curve #{curve_id}: TIMEOUT; unresolved", flush=True)
            continue
        if status == "error":
            result["failed"] += 1
            _record_core_attempt(db, curve_id, status="error", detail=detail)
            print(f"[core] curve #{curve_id}: FAILED: {detail}", flush=True)
            continue

        refreshed = get_curve(db, int(curve_id))
        missing = []
        if refreshed is None or not _present(refreshed["discriminant"]):
            missing.append("discriminant")
        if refreshed is None or not _present(refreshed["bad_primes_json"]):
            missing.append("bad_primes")
        if refreshed is None or refreshed["root_number"] not in (-1, 1):
            missing.append("root_number")

        if missing:
            result["partial"] += 1
            detail = "still missing: " + ", ".join(missing)
            _record_core_attempt(db, curve_id, status="partial", detail=detail)
            print(f"[core] curve #{curve_id}: PARTIAL; {detail}", flush=True)
        else:
            result["updated"] += 1
            _record_core_attempt(db, curve_id, status="done", detail=None)
            print(f"[core] curve #{curve_id}: DONE", flush=True)
    return result


def _backfill_torsion(
    db,
    curve_ids,
    *,
    timeout_seconds: int,
    retry_unresolved: bool,
):
    result = {
        "selected": 0,
        "stored": 0,
        "timeouts": 0,
        "failed": 0,
        "skipped_blocked": 0,
    }
    for index, curve_id in enumerate(curve_ids, 1):
        row = get_curve(db, int(curve_id))
        if row is None or _present(row["torsion_label"]):
            continue
        if (
            not retry_unresolved
            and _present(row["torsion_error"])
        ):
            result["skipped_blocked"] += 1
            continue

        result["selected"] += 1
        print(
            f"[torsion {index}/{len(curve_ids)}] curve #{curve_id}: "
            f"exact E(Q)_tors (timeout {max(1, int(timeout_seconds))}s)...",
            flush=True,
        )
        status = enrich_retained_curve_torsion(
            db,
            int(curve_id),
            force=bool(retry_unresolved),
            timeout=max(1, int(timeout_seconds)),
            require_retained=False,
        )
        if status == "stored":
            result["stored"] += 1
        elif status == "timeout":
            result["timeouts"] += 1
        elif status in {"error", "missing_model", "ineligible"}:
            result["failed"] += 1
        print(f"[torsion] curve #{curve_id}: {status}", flush=True)
    return result


def backfill_curve_arithmetic(
    db,
    *,
    db_path,
    limit: int | None = 25,
    retry_unresolved: bool = False,
    core_timeout: int = 20,
    conductor_timeout: int = 20,
    size_timeout: int = 20,
    torsion_timeout: int = 20,
) -> dict:
    """Run the missing-only arithmetic pipeline on a bounded Curves batch."""
    ensure_torsion_schema(db)
    ensure_schema(db)
    curve_ids = _select_batch_ids(
        db,
        limit=limit,
        retry_unresolved=bool(retry_unresolved),
    )
    print(
        f"[arithmetic] selected {len(curve_ids)} Curves inventory row(s); "
        f"retry_unresolved={bool(retry_unresolved)}",
        flush=True,
    )
    if not curve_ids:
        return {
            "selected": 0,
            "core": {},
            "conductor": {},
            "size": {},
            "torsion": {},
            "status": arithmetic_backfill_status(db),
        }

    core = _backfill_core(
        db,
        db_path,
        curve_ids,
        timeout_seconds=max(1, int(core_timeout)),
        retry_unresolved=bool(retry_unresolved),
    )

    print("[arithmetic] conductor stage", flush=True)
    conductor = backfill_conductors(
        db,
        timeout_seconds=max(1, int(conductor_timeout)),
        rank_at_least=0,
        retry=bool(retry_unresolved),
        curve_ids=curve_ids,
    )

    print("[arithmetic] size-metric stage", flush=True)
    size = backfill_curve_size_metrics(
        db,
        missing_only=True,
        fill_conductor=False,
        factor_conductor=False,
        curve_timeout=max(1, int(size_timeout)),
        retry_timeouts=bool(retry_unresolved),
        precision_bits=128,
        curve_ids=curve_ids,
    )

    print("[arithmetic] torsion stage", flush=True)
    torsion = _backfill_torsion(
        db,
        curve_ids,
        timeout_seconds=max(1, int(torsion_timeout)),
        retry_unresolved=bool(retry_unresolved),
    )

    status = arithmetic_backfill_status(db)
    return {
        "selected": len(curve_ids),
        "curve_ids": curve_ids,
        "core": core,
        "conductor": conductor,
        "size": size,
        "torsion": torsion,
        "status": status,
    }


def parse_args():
    ap = argparse.ArgumentParser(
        description="Bounded missing-only arithmetic backfill for retained Curves"
    )
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--retry-unresolved", action="store_true")
    ap.add_argument("--core-timeout", type=int, default=20)
    ap.add_argument("--conductor-timeout", type=int, default=20)
    ap.add_argument("--size-timeout", type=int, default=20)
    ap.add_argument("--torsion-timeout", type=int, default=20)
    return ap.parse_args()


def main():
    args = parse_args()
    db_path = Path(args.db).resolve()
    db = connect(db_path)
    try:
        result = backfill_curve_arithmetic(
            db,
            db_path=db_path,
            limit=max(1, int(args.limit)) if args.limit is not None else None,
            retry_unresolved=bool(args.retry_unresolved),
            core_timeout=max(1, int(args.core_timeout)),
            conductor_timeout=max(1, int(args.conductor_timeout)),
            size_timeout=max(1, int(args.size_timeout)),
            torsion_timeout=max(1, int(args.torsion_timeout)),
        )
        print(
            "RANK42_CURVE_ARITHMETIC_BACKFILL="
            + json.dumps(result, sort_keys=True),
            flush=True,
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
