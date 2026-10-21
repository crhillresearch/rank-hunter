"""Bounded exact-rank certification for a stored local curve."""

from __future__ import annotations

import argparse
import json

from rank42.db import connect, get_curve, log_event, proven_lower
from rank42.rank_cert import RankCertificationFailure, RankCertificationTimeout, run_rank_certification
from rank42.point_promotion import promote_exact_rank_certificate


def certify_stored_curve(db, curve_id: int, *, timeout: int = 20) -> dict:
    row = get_curve(db, int(curve_id))
    if row is None:
        raise ValueError(f"curve #{int(curve_id)} not found")
    ainvs = json.loads(row["a_invariants_json"] or "[]")
    if len(ainvs) != 5:
        raise ValueError("curve has no stored five-term Weierstrass model")
    lower = int(proven_lower(row))
    result = run_rank_certification(ainvs, timeout=int(timeout))
    exact = int(result["exact_rank"])
    if exact < lower:
        raise RuntimeError(f"rank inconsistency: exact {exact} < stored proven lower {lower}")
    promoted = promote_exact_rank_certificate(
        db,
        curve_id=int(curve_id),
        model=ainvs,
        exact_rank=exact,
        certificate=result,
        engine="sage_proof_rank",
        source="certify_curve",
        metadata={
            "timeout": int(timeout),
            "sage_version": result.get("sage_version"),
            "minimal_model_a_invariants": result.get("a_invariants") or ainvs,
            "elapsed_seconds": result.get("_runtime_seconds"),
        },
    )
    if not promoted["projected"] or promoted["exact_rank"] != exact:
        raise RuntimeError(
            f"rank evidence reducer did not accept exact rank {exact}: {promoted}"
        )
    log_event(db, int(curve_id), "best", f"Sage proof-mode certification establishes exact rank {exact}")
    return {"curve_id": int(curve_id), "exact_rank": exact, "proven_lower": lower, "runtime_seconds": result.get("_runtime_seconds")}


def parse_args():
    ap = argparse.ArgumentParser(description="Certify exact rank of one stored Rank Hunter curve")
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--id", type=int, required=True)
    ap.add_argument("--timeout", type=int, default=20)
    return ap.parse_args()


def main():
    args = parse_args()
    db = connect(args.db)
    try:
        try:
            result = certify_stored_curve(db, args.id, timeout=args.timeout)
        except RankCertificationTimeout as exc:
            print(json.dumps({"curve_id": args.id, "status": "timeout", "error": str(exc)}, sort_keys=True))
            return
        except RankCertificationFailure as exc:
            print(json.dumps({"curve_id": args.id, "status": "failed", "error": str(exc)}, sort_keys=True))
            return
        print(json.dumps({"status": "exact", **result}, sort_keys=True))
    finally:
        db.close()


if __name__ == "__main__":
    main()
