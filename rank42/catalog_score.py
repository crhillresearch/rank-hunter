"""Nagao/Mestre-style calibration scores for external fixed curves.

These scores are heuristic only.  They are stored in the external catalog
namespace and are intended to calibrate candidate-ranking choices against
curves with known rigorous lower bounds, not to alter those lower bounds.
"""

from __future__ import annotations

import argparse
import json
import math
import time

from sage.all import GF, QQ, EllipticCurve, prime_range

from rank42.catalog import list_external_curves
from rank42.db import connect, now


def parse_bounds(text):
    vals = sorted(set(int(x.strip()) for x in str(text).split(",") if x.strip()))
    if not vals or vals[0] <= 3:
        raise ValueError("prime bounds must be > 3")
    return vals


def score_fixed_curve(ainvs, bounds):
    bounds = sorted(set(map(int, bounds)))
    max_bound = max(bounds)
    aq = [QQ(str(x)) for x in ainvs]
    E = EllipticCurve(QQ, aq)
    disc = E.discriminant()
    totals = 0.0
    used = 0
    out = {}
    bound_iter = iter(bounds)
    current = next(bound_iter)
    remaining = list(bounds)
    idx = 0
    for p0 in prime_range(5, max_bound):
        p = int(p0)
        while idx < len(bounds) and p >= bounds[idx]:
            out[bounds[idx]] = {"score": totals, "primes_used": used}
            idx += 1
        if idx >= len(bounds):
            break
        # Integral external catalog curves dominate, but denominator checks keep
        # this helper correct for hand-imported rational models too.
        if any(x.denominator() % p == 0 for x in aq):
            continue
        try:
            coeffs = [GF(p)(x) for x in aq]
            Ep = EllipticCurve(GF(p), coeffs)
            if Ep.discriminant() == 0:
                continue
            n = int(Ep.cardinality())
        except Exception:
            continue
        totals += math.log(n / p)
        used += 1
    while idx < len(bounds):
        out[bounds[idx]] = {"score": totals, "primes_used": used}
        idx += 1
    return out


def store_scores(db, row, scores, runtime):
    ts = now()
    for bound, info in scores.items():
        db.execute(
            """
            INSERT INTO external_curve_scores(
                source,source_id,prime_bound,score,primes_used,runtime,
                metadata_json,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(source,source_id,prime_bound) DO UPDATE SET
                score=excluded.score,
                primes_used=excluded.primes_used,
                runtime=excluded.runtime,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            (
                row["source"], row["source_id"], int(bound), float(info["score"]),
                int(info["primes_used"]), float(runtime),
                json.dumps({"heuristic": True, "formula": "sum log(#E(F_p)/p) over usable p<B"}, sort_keys=True),
                ts, ts,
            ),
        )
    db.commit()


def parse_args():
    ap = argparse.ArgumentParser(description="Calibrate Nagao scores on external high-rank curves")
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--source", default="icarm")
    ap.add_argument("--rank-at-least", type=int, default=15)
    ap.add_argument("--bounds", default="200,500")
    ap.add_argument("--limit", type=int, default=100)
    return ap.parse_args()


def main():
    args = parse_args()
    bounds = parse_bounds(args.bounds)
    db = connect(args.db)
    try:
        rows = list_external_curves(
            db, source=args.source, rank_at_least=args.rank_at_least, limit=args.limit
        )
        if not rows:
            raise SystemExit("no external curves match; sync catalog first")
        print(f"scoring {len(rows)} external curve(s) at B={','.join(map(str,bounds))}")
        for i, row in enumerate(rows, 1):
            ainvs = json.loads(row["a_invariants_json"])
            started = time.monotonic()
            scores = score_fixed_curve(ainvs, bounds)
            runtime = time.monotonic() - started
            store_scores(db, row, scores, runtime)
            summary = " ".join(
                f"B={B}:{scores[B]['score']:.6f}/{scores[B]['primes_used']}p" for B in bounds
            )
            print(
                f"[{i}/{len(rows)}] {row['source']}:{row['source_id']} rank>={row['rank_lower_bound']} "
                f"{summary} {runtime:.2f}s"
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
