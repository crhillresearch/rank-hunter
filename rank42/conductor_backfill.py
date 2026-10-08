"""Conductor-first backfill for stored Rank Hunter curves.

This job is intentionally separate from the archimedean size-metric backfill.
For ICARM scouting, conductor is often the most decision-relevant size measure,
and recomputing Faltings/naive heights just to recover N is wasted work.

Strategy, in priority order:

1. Reuse conductor from an exact synchronized ICARM match when available.
2. Reuse stored bad primes and compute only local conductor exponents.
3. Otherwise factor the already-stored *minimal discriminant* in a killable
   Sage subprocess, then compute local conductor exponents at those primes.

The worker never calls ``global_minimal_model()``.  Each curve has a hard
wall-clock bound, successful rows commit immediately, and the queue is ordered
by rigorous rank descending and then by discriminant size ascending so the most
interesting record candidates get N first.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

from rank42.db import connect, now, update_curve


_ATTEMPTS_SQL = """
CREATE TABLE IF NOT EXISTS conductor_backfill_attempts (
    curve_id INTEGER PRIMARY KEY,
    status TEXT NOT NULL,
    method TEXT,
    detail TEXT,
    attempted_at TEXT NOT NULL,
    FOREIGN KEY(curve_id) REFERENCES curves(id) ON DELETE CASCADE
)
"""

_WORKER_MARKER = "RANK42_CONDUCTOR_WORKER="


def ensure_schema(db):
    db.execute(_ATTEMPTS_SQL)
    db.commit()


def _json_list(value):
    try:
        out = json.loads(value or "[]")
    except Exception:
        return None
    return out if isinstance(out, list) else None


def _five_ainvs(value):
    out = _json_list(value)
    if not isinstance(out, list) or len(out) != 5:
        return None
    return [str(x) for x in out]


def _bad_primes(value):
    out = _json_list(value)
    if not out:
        return []
    return [str(x) for x in out]


def _record_attempt(db, curve_id: int, *, status: str, method=None, detail=None):
    db.execute(
        """
        INSERT INTO conductor_backfill_attempts(curve_id,status,method,detail,attempted_at)
        VALUES(?,?,?,?,?)
        ON CONFLICT(curve_id) DO UPDATE SET
            status=excluded.status,
            method=excluded.method,
            detail=excluded.detail,
            attempted_at=excluded.attempted_at
        """,
        (int(curve_id), str(status), method, detail, now()),
    )
    db.commit()


def _worker_source() -> str:
    return r'''
import json, sys
from sage.all import EllipticCurve, QQ, ZZ, factor

p=json.load(sys.stdin)
raw=p.get("ainvs") or []
if len(raw) != 5:
    raise ValueError("curve requires five a-invariants")
E=EllipticCurve(QQ,[QQ(str(x)) for x in raw])
if E.discriminant() == 0:
    raise ValueError("stored model is singular")

# Local-data calculations do not need a global minimal model.  An integral
# representative avoids denominator noise while preserving the Q-isomorphism
# class and therefore the conductor.
try:
    Elocal=E.integral_model()
except Exception:
    Elocal=E

minimal=p.get("minimal_ainvs")
if isinstance(minimal,list) and len(minimal)==5:
    try:
        Elocal=EllipticCurve(QQ,[QQ(str(x)) for x in minimal])
    except Exception:
        pass

bad=[]
method=None
stored_bad=p.get("bad_primes") or []
if stored_bad:
    bad=[ZZ(str(x)) for x in stored_bad]
    method="stored_bad_primes"
else:
    disc_raw=p.get("minimal_discriminant")
    if disc_raw in (None, ""):
        raise ValueError("missing minimal discriminant and bad-prime support")
    disc=abs(ZZ(str(disc_raw)))
    if disc == 0:
        raise ValueError("zero minimal discriminant")
    if disc == 1:
        bad=[]
    else:
        bad=[ZZ(prime) for prime,_exp in factor(disc)]
    method="factor_minimal_discriminant"

N=ZZ(1)
for prime in bad:
    if prime <= 1:
        raise ValueError("invalid bad prime")
    # Supplying p explicitly keeps Sage in a local computation; it does not need
    # to discover/factor the global discriminant support again.
    local=Elocal.local_data(prime)
    f=int(local.conductor_valuation())
    if f < 0:
        raise ValueError("negative conductor valuation")
    N *= prime ** f

result={
    "conductor":str(N),
    "bad_primes":[str(x) for x in bad],
    "method":method,
}
print("RANK42_CONDUCTOR_WORKER="+json.dumps(result,sort_keys=True),flush=True)
'''


def _run_worker(payload: dict, *, timeout_seconds: int):
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _worker_source()],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=max(1, int(timeout_seconds)),
            check=False,
            start_new_session=True,
        )
    except subprocess.TimeoutExpired:
        return None, f"timeout after {max(1, int(timeout_seconds))}s", True

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "conductor worker failed").strip()
        return None, detail[-1200:], False

    for line in reversed((proc.stdout or "").splitlines()):
        if line.startswith(_WORKER_MARKER):
            try:
                return json.loads(line[len(_WORKER_MARKER):]), None, False
            except Exception as exc:
                return None, f"invalid worker result: {exc}", False
    return None, "conductor worker returned no result marker", False


def _rows_to_process(
    db,
    *,
    rank_at_least: int,
    limit: int | None,
    retry: bool,
    curve_ids=None,
):
    sql = """
        SELECT c.*,
               MAX(COALESCE(c.exact_rank,0), COALESCE(c.descent_lower,0), COALESCE(c.generic_lower,0)) AS proven_rank,
               a.status AS attempt_status,
               (
                   SELECT re.minimal_model_a_invariants_json
                   FROM rank_evidence re
                   WHERE re.curve_id=c.id
                     AND re.minimal_model_a_invariants_json IS NOT NULL
                     AND TRIM(re.minimal_model_a_invariants_json) <> ''
                   ORDER BY re.id DESC LIMIT 1
               ) AS evidence_minimal_ainvs,
               (
                   SELECT ec.conductor
                   FROM curve_catalog_checks cc
                   JOIN external_curves ec
                     ON ec.source='icarm'
                    AND ec.source_id=cc.source_label
                    AND ec.active=1
                   WHERE cc.curve_id=c.id
                     AND cc.source='icarm'
                     AND cc.status='known'
                     AND ec.conductor IS NOT NULL
                   LIMIT 1
               ) AS icarm_conductor,
               (
                   SELECT ec.source_id
                   FROM curve_catalog_checks cc
                   JOIN external_curves ec
                     ON ec.source='icarm'
                    AND ec.source_id=cc.source_label
                    AND ec.active=1
                   WHERE cc.curve_id=c.id
                     AND cc.source='icarm'
                     AND cc.status='known'
                   LIMIT 1
               ) AS icarm_source_id
        FROM curves c
        LEFT JOIN conductor_backfill_attempts a ON a.curve_id=c.id
        WHERE c.conductor IS NULL
          AND c.a_invariants_json IS NOT NULL
          AND TRIM(c.a_invariants_json) <> ''
          AND MAX(COALESCE(c.exact_rank,0), COALESCE(c.descent_lower,0), COALESCE(c.generic_lower,0)) >= ?
    """
    params = [max(0, int(rank_at_least))]
    selected_ids = sorted({int(value) for value in (curve_ids or ())})
    if selected_ids:
        marks = ",".join("?" for _ in selected_ids)
        sql += f" AND c.id IN ({marks})"
        params.extend(selected_ids)
    if not retry:
        sql += " AND (a.curve_id IS NULL OR a.status NOT IN ('timeout','error'))"
    # Decimal digit count is a cheap monotone proxy for log|Delta| and keeps the
    # most promising high-rank/small-size records at the front of the queue.
    sql += """
        ORDER BY proven_rank DESC,
                 CASE WHEN c.discriminant IS NULL THEN 1 ELSE 0 END ASC,
                 LENGTH(REPLACE(COALESCE(c.discriminant,''),'-','')) ASC,
                 c.id ASC
    """
    if limit is not None:
        sql += " LIMIT ?"
        params.append(max(0, int(limit)))
    return db.execute(sql, tuple(params)).fetchall()


def backfill_conductors(
    db,
    *,
    timeout_seconds: int = 20,
    rank_at_least: int = 0,
    limit: int | None = None,
    retry: bool = False,
    curve_ids=None,
) -> dict:
    ensure_schema(db)
    rows = _rows_to_process(
        db,
        rank_at_least=int(rank_at_least),
        limit=limit,
        retry=bool(retry),
        curve_ids=curve_ids,
    )
    updated = copied_icarm = factored = stored_bad = timeouts = failed = 0
    bound = max(1, int(timeout_seconds))

    for index, row in enumerate(rows, 1):
        curve_id = int(row["id"])
        rank = int(row["proven_rank"] or 0)

        icarm_n = row["icarm_conductor"]
        if icarm_n not in (None, ""):
            value = str(icarm_n)
            update_curve(db, curve_id, conductor=value)
            _record_attempt(
                db, curve_id, status="done", method="icarm_exact_match",
                detail=f"ICARM curve {row['icarm_source_id']}",
            )
            updated += 1
            copied_icarm += 1
            print(
                f"[{index}/{len(rows)}] curve #{curve_id} rank>={rank}: N={value} "
                f"(copied exact ICARM match {row['icarm_source_id']})",
                flush=True,
            )
            continue

        raw = _five_ainvs(row["a_invariants_json"])
        if raw is None:
            failed += 1
            _record_attempt(db, curve_id, status="error", method="input", detail="invalid a-invariants")
            print(f"[{index}/{len(rows)}] curve #{curve_id}: FAILED invalid a-invariants", flush=True)
            continue

        minimal = _five_ainvs(row["evidence_minimal_ainvs"])
        bad = _bad_primes(row["bad_primes_json"])
        print(
            f"[{index}/{len(rows)}] curve #{curve_id} rank>={rank}: computing N "
            f"(timeout {bound}s, bad_primes={'stored' if bad else 'factor Delta'})...",
            flush=True,
        )
        payload = {
            "ainvs": raw,
            "minimal_ainvs": minimal,
            "minimal_discriminant": row["discriminant"],
            "bad_primes": bad,
        }
        result, error, timed_out = _run_worker(payload, timeout_seconds=bound)
        if timed_out:
            timeouts += 1
            _record_attempt(db, curve_id, status="timeout", method="bounded_worker", detail=error)
            print(f"[{index}/{len(rows)}] curve #{curve_id}: TIMEOUT after {bound}s; skipped", flush=True)
            continue
        if result is None:
            failed += 1
            _record_attempt(db, curve_id, status="error", method="bounded_worker", detail=error)
            print(f"[{index}/{len(rows)}] curve #{curve_id}: FAILED: {error}", flush=True)
            continue

        conductor = str(result.get("conductor") or "").strip()
        if not conductor or int(conductor) <= 0:
            failed += 1
            _record_attempt(db, curve_id, status="error", method=result.get("method"), detail="invalid conductor")
            print(f"[{index}/{len(rows)}] curve #{curve_id}: FAILED invalid conductor", flush=True)
            continue

        fields = {"conductor": conductor}
        result_bad = result.get("bad_primes") or []
        if (not bad) and result_bad:
            fields["bad_primes_json"] = json.dumps([str(x) for x in result_bad])
        update_curve(db, curve_id, **fields)
        method = str(result.get("method") or "local_data")
        _record_attempt(db, curve_id, status="done", method=method, detail=None)
        updated += 1
        if method == "stored_bad_primes":
            stored_bad += 1
        elif method == "factor_minimal_discriminant":
            factored += 1
        print(
            f"[{index}/{len(rows)}] curve #{curve_id} rank>={rank}: DONE N={conductor} method={method}",
            flush=True,
        )

    return {
        "selected": len(rows),
        "updated": updated,
        "copied_icarm": copied_icarm,
        "stored_bad_primes": stored_bad,
        "factored_discriminant": factored,
        "timeouts": timeouts,
        "failed": failed,
        "timeout_seconds": bound,
        "rank_at_least": max(0, int(rank_at_least)),
        "retry": bool(retry),
    }


def parse_args():
    ap = argparse.ArgumentParser(description="Backfill conductors with ICARM-scouting priority")
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--timeout", type=int, default=20, help="hard wall-clock seconds per unresolved curve")
    ap.add_argument("--rank-at-least", type=int, default=0, help="only process curves with rigorous rank lower bound >= this")
    ap.add_argument("--limit", type=int, help="process only the first N priority curves")
    ap.add_argument("--retry", action="store_true", help="retry rows previously marked timeout/error")
    return ap.parse_args()


def main():
    args = parse_args()
    db = connect(args.db)
    try:
        result = backfill_conductors(
            db,
            timeout_seconds=max(1, int(args.timeout)),
            rank_at_least=max(0, int(args.rank_at_least)),
            limit=args.limit,
            retry=bool(args.retry),
        )
        print("RANK42_CONDUCTOR_BACKFILL=" + json.dumps(result, sort_keys=True), flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
