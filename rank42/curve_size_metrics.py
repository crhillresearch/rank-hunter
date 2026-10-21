"""Arithmetic size metrics for local elliptic curves.

The UI runs in a lightweight Python environment, while these quantities are
computed in the configured Sage/science environment. Keep expensive arithmetic
out of Streamlit and persist only display/provenance metadata keyed by the
authoritative local curve row.

Conventions intentionally match the ICARM elliptic-rank leaderboard:

* naive height = log max(|c4|^3, |c6|^2) on the global minimal model;
* Faltings height = -1/2 log(A), where A is the period-lattice covolume.

Both logarithms are natural logarithms.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

from rank42.db import connect, now, update_curve


_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS curve_size_metrics (
    curve_id INTEGER PRIMARY KEY,
    naive_height TEXT,
    faltings_height TEXT,
    precision_bits INTEGER NOT NULL DEFAULT 128,
    method TEXT NOT NULL DEFAULT 'sage_global_minimal_model',
    computed_at TEXT NOT NULL,
    FOREIGN KEY(curve_id) REFERENCES curves(id) ON DELETE CASCADE
)
"""

_WORKER_MARKER = "RANK42_SIZE_WORKER="


def ensure_curve_size_metrics_schema(db):
    """Create the additive size-metadata table when needed."""
    db.execute(_TABLE_SQL)
    db.commit()


def compute_curve_size_metrics(Em, *, precision_bits: int = 128) -> dict:
    """Compute ICARM-compatible size metrics from a verified minimal model.

    Do not call ``faltings_height()`` here: some Sage versions may internally
    revisit global reduction. The ICARM normalization is computed directly from
    the period-lattice covolume instead.
    """
    from sage.all import QQ, RealField

    bits = max(53, int(precision_bits))
    RR = RealField(bits)
    c4q, c6q = (QQ(x) for x in Em.c_invariants())
    if c4q.denominator() != 1 or c6q.denominator() != 1:
        raise ValueError("minimal model has non-integral c-invariants")
    c4 = c4q.numerator()
    c6 = c6q.numerator()
    scale = max(abs(c4) ** 3, abs(c6) ** 2)
    if scale <= 0:
        raise ValueError("nonsingular elliptic curve has invalid naive-height scale")
    naive = RR(scale).log()

    basis = Em.period_lattice().basis(prec=bits)
    if len(basis) != 2:
        raise ValueError("period lattice did not return a two-element basis")
    w1, w2 = basis
    area = abs((w1.conjugate() * w2).imag())
    if area <= 0:
        raise ValueError("period lattice has non-positive covolume")
    faltings = -(RR(1) / 2) * RR(area).log()

    return {
        "naive_height": str(naive),
        "faltings_height": str(faltings),
        "precision_bits": bits,
        "method": "sage_global_minimal_model",
    }


def store_curve_size_metrics(db, curve_id: int, metrics: dict):
    """Persist one curve's size metrics or a durable attempt marker."""
    ensure_curve_size_metrics_schema(db)
    db.execute(
        """
        INSERT INTO curve_size_metrics(
            curve_id, naive_height, faltings_height, precision_bits, method, computed_at
        ) VALUES(?,?,?,?,?,?)
        ON CONFLICT(curve_id) DO UPDATE SET
            naive_height=excluded.naive_height,
            faltings_height=excluded.faltings_height,
            precision_bits=excluded.precision_bits,
            method=excluded.method,
            computed_at=excluded.computed_at
        """,
        (
            int(curve_id),
            metrics.get("naive_height"),
            metrics.get("faltings_height"),
            int(metrics.get("precision_bits") or 128),
            str(metrics.get("method") or "sage_global_minimal_model"),
            now(),
        ),
    )
    db.commit()


def _stored_bad_primes(row):
    try:
        bad = json.loads(row["bad_primes_json"] or "[]")
    except Exception:
        return []
    return bad if isinstance(bad, list) else []


def _json_ainvs(value):
    try:
        raw = json.loads(value or "[]")
    except Exception:
        return None
    if not isinstance(raw, list) or len(raw) != 5:
        return None
    return [str(x) for x in raw]


def _latest_minimal_evidence(row):
    value = row["evidence_minimal_ainvs"] if "evidence_minimal_ainvs" in row.keys() else None
    return _json_ainvs(value)


def _worker_source() -> str:
    return r'''
import json, sys
from sage.all import EllipticCurve, QQ, ZZ
from rank42.curve_size_metrics import compute_curve_size_metrics

p=json.load(sys.stdin)
raw=p["ainvs"]
E=EllipticCurve(QQ,[QQ(str(x)) for x in raw])
model_disc=QQ(E.discriminant())
if model_disc == 0:
    raise ValueError("stored model is singular")
model_c4,model_c6=(QQ(x) for x in E.c_invariants())
model_integral=(
    model_disc.denominator() == 1
    and model_c4.denominator() == 1
    and model_c6.denominator() == 1
)

source=None
evidence=p.get("evidence_minimal_ainvs")
if model_integral and isinstance(evidence,list) and len(evidence)==5:
    if [QQ(str(x)) for x in evidence] == [QQ(str(x)) for x in raw]:
        Em=E
        source="rank_evidence_minimal_model"

if source is None and model_integral and p.get("stored_discriminant") not in (None, ""):
    try:
        if model_disc == QQ(str(p["stored_discriminant"])):
            Em=E
            source="stored_minimal_discriminant_match"
    except Exception:
        pass

if source is None:
    Em=E.global_minimal_model()
    source="bounded_global_minimal_model"

minimal_disc=QQ(Em.discriminant())
if minimal_disc.denominator() != 1:
    raise ValueError("global minimal model has non-integral discriminant")

metrics=compute_curve_size_metrics(Em,precision_bits=int(p.get("precision_bits") or 128))
metrics["method"]="sage_global_minimal_model:"+source
result={
    "metrics":metrics,
    "discriminant":str(minimal_disc.numerator()),
    "model_source":source,
    "conductor":None,
}

if p.get("fill_conductor") and not p.get("stored_conductor"):
    bad=p.get("bad_primes") or []
    if bad:
        try:
            N=ZZ(1)
            for raw_p in bad:
                prime=ZZ(raw_p)
                if prime <= 1:
                    raise ValueError("invalid bad prime")
                N *= prime ** int(Em.local_data(prime).conductor_valuation())
            result["conductor"]=str(N)
        except Exception:
            result["conductor"]=None
    elif p.get("factor_conductor"):
        result["conductor"]=str(Em.conductor())

print("RANK42_SIZE_WORKER="+json.dumps(result,sort_keys=True),flush=True)
'''


def _run_curve_worker(payload: dict, *, timeout_seconds: int):
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _worker_source()],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=max(1, int(timeout_seconds)),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, f"timeout after {max(1, int(timeout_seconds))}s", True

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "curve-size worker failed").strip()
        return None, detail[-1000:], False

    for line in reversed((proc.stdout or "").splitlines()):
        if line.startswith(_WORKER_MARKER):
            try:
                return json.loads(line[len(_WORKER_MARKER):]), None, False
            except Exception as exc:
                return None, f"invalid worker result: {exc}", False
    return None, "curve-size worker returned no result marker", False


def backfill_curve_size_metrics(
    db,
    *,
    missing_only: bool = True,
    fill_conductor: bool = True,
    factor_conductor: bool = False,
    conductor_timeout: int = 30,
    curve_timeout: int = 20,
    retry_timeouts: bool = False,
    precision_bits: int = 128,
    limit: int | None = None,
    curve_ids=None,
) -> dict:
    """Backfill stored curves with a hard per-curve wall-clock bound.

    Every scientific row runs out-of-process. That bounds global minimal-model
    reduction, period computation, local conductor work, and native PARI calls;
    a single record-scale discriminant can therefore never block the whole
    inventory. Successful rows are committed immediately. Timed-out rows are
    recorded as attempt markers and skipped on later missing-only runs unless
    ``retry_timeouts`` is requested.
    """
    ensure_curve_size_metrics_schema(db)
    sql = """
        SELECT curves.*,
               sizes.naive_height AS stored_naive_height,
               sizes.faltings_height AS stored_faltings_height,
               sizes.method AS size_method,
               (
                   SELECT re.minimal_model_a_invariants_json
                   FROM rank_evidence re
                   WHERE re.curve_id=curves.id
                     AND re.minimal_model_a_invariants_json IS NOT NULL
                   ORDER BY re.id DESC LIMIT 1
               ) AS evidence_minimal_ainvs
        FROM curves
        LEFT JOIN curve_size_metrics sizes ON sizes.curve_id=curves.id
        WHERE curves.a_invariants_json IS NOT NULL
          AND TRIM(curves.a_invariants_json) <> ''
    """
    selected_ids = sorted({int(value) for value in (curve_ids or ())})
    if selected_ids:
        marks = ",".join("?" for _ in selected_ids)
        sql += f" AND curves.id IN ({marks})"

    if missing_only:
        missing = [
            "sizes.curve_id IS NULL",
            "sizes.naive_height IS NULL",
            "sizes.faltings_height IS NULL",
            "curves.discriminant IS NULL",
        ]
        if fill_conductor:
            if factor_conductor:
                missing.append("curves.conductor IS NULL")
            else:
                missing.append(
                    "(curves.conductor IS NULL AND curves.bad_primes_json IS NOT NULL "
                    "AND TRIM(curves.bad_primes_json) NOT IN ('', '[]', 'null'))"
                )
        expr = "(" + " OR ".join(missing) + ")"
        if not retry_timeouts:
            expr += (
                " AND (sizes.curve_id IS NULL OR "
                "(sizes.method NOT LIKE 'timeout:%' AND sizes.method NOT LIKE 'error:%'))"
            )
        sql += " AND (" + expr + ")"
    sql += " ORDER BY curves.id"
    if limit is not None:
        sql += f" LIMIT {max(0, int(limit))}"

    rows = (
        db.execute(sql, tuple(selected_ids)).fetchall()
        if selected_ids
        else db.execute(sql).fetchall()
    )
    done = failed = timeouts = conductor_missing = 0
    bits = max(53, int(precision_bits))
    base_timeout = max(1, int(curve_timeout))
    conductor_bound = max(1, int(conductor_timeout))
    worker_timeout = max(base_timeout, conductor_bound) if factor_conductor else base_timeout

    for index, row in enumerate(rows, 1):
        curve_id = int(row["id"])
        print(
            f"[{index}/{len(rows)}] curve #{curve_id}: computing size metrics "
            f"(timeout {worker_timeout}s)...",
            flush=True,
        )
        raw = _json_ainvs(row["a_invariants_json"])
        if raw is None:
            failed += 1
            print(f"[{index}/{len(rows)}] curve #{curve_id}: FAILED invalid stored a-invariants", flush=True)
            continue

        payload = {
            "ainvs": raw,
            "stored_discriminant": row["discriminant"],
            "stored_conductor": row["conductor"],
            "evidence_minimal_ainvs": _latest_minimal_evidence(row),
            "bad_primes": _stored_bad_primes(row),
            "fill_conductor": bool(fill_conductor),
            "factor_conductor": bool(factor_conductor),
            "precision_bits": bits,
        }
        result, error, timed_out = _run_curve_worker(payload, timeout_seconds=worker_timeout)
        if timed_out:
            timeouts += 1
            store_curve_size_metrics(
                db,
                curve_id,
                {
                    "naive_height": None,
                    "faltings_height": None,
                    "precision_bits": bits,
                    "method": f"timeout:{worker_timeout}s",
                },
            )
            print(
                f"[{index}/{len(rows)}] curve #{curve_id}: TIMEOUT after {worker_timeout}s; skipped",
                flush=True,
            )
            continue
        if result is None:
            failed += 1
            store_curve_size_metrics(
                db,
                curve_id,
                {
                    "naive_height": None,
                    "faltings_height": None,
                    "precision_bits": bits,
                    "method": "error:" + str(error or "curve-size worker failed")[:700],
                },
            )
            print(f"[{index}/{len(rows)}] curve #{curve_id}: FAILED: {error}", flush=True)
            continue

        metrics = dict(result.get("metrics") or {})
        store_curve_size_metrics(db, curve_id, metrics)
        fields = {}
        if row["discriminant"] is None and result.get("discriminant") is not None:
            fields["discriminant"] = str(result["discriminant"])
        if fill_conductor and row["conductor"] is None:
            if result.get("conductor") is not None:
                fields["conductor"] = str(result["conductor"])
            else:
                conductor_missing += 1
        if fields:
            update_curve(db, curve_id, **fields)

        done += 1
        conductor_display = fields.get("conductor") or row["conductor"] or "missing"
        print(
            f"[{index}/{len(rows)}] curve #{curve_id}: DONE "
            f"naive={metrics.get('naive_height')} faltings={metrics.get('faltings_height')} "
            f"conductor={conductor_display} source={result.get('model_source')}",
            flush=True,
        )

    return {
        "selected": len(rows),
        "updated": done,
        "failed": failed,
        "timeouts": timeouts,
        "conductor_missing": conductor_missing,
        "missing_only": bool(missing_only),
        "retry_timeouts": bool(retry_timeouts),
        "fill_conductor": bool(fill_conductor),
        "factor_conductor": bool(factor_conductor),
        "curve_timeout": base_timeout,
        "conductor_timeout": conductor_bound,
        "precision_bits": bits,
    }


def parse_args():
    ap = argparse.ArgumentParser(description="Backfill arithmetic size metrics for stored Rank Hunter curves")
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--all", action="store_true", help="recompute rows that already have size metrics")
    ap.add_argument("--no-conductor", action="store_true", help="do not try to fill missing conductors")
    ap.add_argument(
        "--factor-conductor",
        action="store_true",
        help="for missing N without stored bad primes, allow conductor computation inside the bounded worker",
    )
    ap.add_argument(
        "--conductor-timeout",
        type=int,
        default=30,
        help="compatibility bound used when --factor-conductor is enabled (default: 30)",
    )
    ap.add_argument(
        "--curve-timeout",
        type=int,
        default=20,
        help="hard wall-clock seconds for each curve worker (default: 20)",
    )
    ap.add_argument(
        "--retry-timeouts",
        action="store_true",
        help="retry rows previously marked as timed out",
    )
    ap.add_argument("--precision", type=int, default=128)
    ap.add_argument("--limit", type=int)
    return ap.parse_args()


def main():
    args = parse_args()
    db = connect(args.db)
    try:
        result = backfill_curve_size_metrics(
            db,
            missing_only=not bool(args.all),
            fill_conductor=not bool(args.no_conductor),
            factor_conductor=bool(args.factor_conductor),
            conductor_timeout=max(1, int(args.conductor_timeout)),
            curve_timeout=max(1, int(args.curve_timeout)),
            retry_timeouts=bool(args.retry_timeouts),
            precision_bits=int(args.precision),
            limit=args.limit,
        )
        print("RANK42_CURVE_SIZE_BACKFILL=" + json.dumps(result, sort_keys=True), flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
