"""Resumable rational-torsion backfill for retained research curves."""
from __future__ import annotations

import argparse
import time

from rank42.db import connect
from rank42.torsion import ensure_torsion_schema, enrich_retained_curve_torsion, retained_research_curve


def parse_args():
    ap = argparse.ArgumentParser(description="Backfill rational torsion for retained Rank Hunter curves")
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--limit", type=int, default=0, help="maximum eligible rows to process; 0 means all")
    ap.add_argument("--retry-errors", action="store_true", help="retry rows with a previous torsion error")
    ap.add_argument("--force", action="store_true", help="recompute even successful stored torsion")
    return ap.parse_args()


def eligible_rows(db, *, retry_errors=False, force=False):
    ensure_torsion_schema(db)
    rows = db.execute(
        """
        SELECT id, torsion_label, torsion_computed_at, torsion_error
        FROM curves
        ORDER BY id
        """
    ).fetchall()
    out = []
    for row in rows:
        if not retained_research_curve(db, int(row["id"])):
            continue
        if force:
            out.append(row)
            continue
        if row["torsion_label"] is not None:
            continue
        if row["torsion_error"] and not retry_errors:
            continue
        out.append(row)
    return out


def main():
    args = parse_args()
    db = connect(args.db)
    ensure_torsion_schema(db)
    rows = eligible_rows(db, retry_errors=args.retry_errors, force=args.force)
    if args.limit > 0:
        rows = rows[: int(args.limit)]

    total = len(rows)
    processed = successful = failed = skipped = 0
    started = time.monotonic()
    print(f"TORSION BACKFILL · eligible {total}", flush=True)
    for row in rows:
        curve_id = int(row["id"])
        result = enrich_retained_curve_torsion(db, curve_id, force=args.force)
        processed += 1
        if result in {"stored", "cached"}:
            successful += 1
        elif result == "error":
            failed += 1
        else:
            skipped += 1
        elapsed = max(time.monotonic() - started, 1e-9)
        rate = processed / elapsed
        remaining = total - processed
        print(
            f"torsion {processed}/{total} · ok {successful} · failed {failed} · "
            f"skipped {skipped} · remaining {remaining} · {rate:.2f} curves/s",
            flush=True,
        )

    elapsed = time.monotonic() - started
    rate = processed / elapsed if elapsed > 0 else 0.0
    print(
        f"DONE · processed {processed} · successful {successful} · failed {failed} · "
        f"skipped {skipped} · {rate:.2f} curves/s",
        flush=True,
    )


if __name__ == "__main__":
    main()
