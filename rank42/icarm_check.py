"""Manual one-curve ICARM check for the Rank Hunter UI.

This is a source-specific catalog lookup, not a novelty proof.  The public
ICARM snapshot is refreshed only when stale, then a bounded exact Q-isomorphism
check is performed against the selected local curve.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rank42.db import connect, get_curve, log_event
from rank42.novelty import check_icarm_on_write


def parse_args():
    ap = argparse.ArgumentParser(description="Check one local curve against ICARM")
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--curve-id", type=int, required=True)
    ap.add_argument("--project-root", default=".")
    ap.add_argument("--timeout", type=int, default=30,
                    help="Hard timeout in seconds for ICARM network and exact-match stages")
    return ap.parse_args()


def main():
    args = parse_args()
    from sage.all import QQ, EllipticCurve

    db = connect(args.db)
    try:
        row = get_curve(db, int(args.curve_id))
        if row is None:
            raise SystemExit(f"curve #{int(args.curve_id)} not found")
        try:
            ainvs = json.loads(row["a_invariants_json"] or "[]")
        except Exception as exc:
            raise SystemExit(f"curve #{int(args.curve_id)} has invalid stored a-invariants: {exc}")
        if len(ainvs) != 5:
            raise SystemExit(f"curve #{int(args.curve_id)} has no complete stored a-invariants")

        E = EllipticCurve(QQ, [QQ(x) for x in ainvs])
        cache_dir = Path(args.project_root).resolve() / ".rank42-cache" / "catalogs" / "icarm"
        result = check_icarm_on_write(
            db,
            int(args.curve_id),
            E,
            cache_dir=str(cache_dir),
            timeout=int(args.timeout),
            max_age_hours=24.0,
        )
        check = db.execute(
            "SELECT status,source_label,source_rank,source_url,checked_at FROM curve_catalog_checks WHERE curve_id=? AND source='icarm'",
            (int(args.curve_id),),
        ).fetchone()
        payload = {
            "curve_id": int(args.curve_id),
            "status": str(check["status"]) if check is not None else str(result.get("status") or "unknown"),
            "icarm_id": str(check["source_label"]) if check is not None and check["source_label"] else None,
            "icarm_rank_lower": int(check["source_rank"]) if check is not None and check["source_rank"] is not None else None,
            "source_url": check["source_url"] if check is not None else None,
            "checked_at": check["checked_at"] if check is not None else None,
            "snapshot": result.get("snapshot"),
        }
        log_event(
            db, int(args.curve_id), "info",
            f"manual ICARM check: {payload['status']}"
            + (f" #{payload['icarm_id']} rank >= {payload['icarm_rank_lower']}" if payload['icarm_id'] else ""),
        )
        print("RANK42_ICARM_CHECK=" + json.dumps(payload, sort_keys=True), flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
