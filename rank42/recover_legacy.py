"""Guarded CLI for legacy Rank42 database recovery.

This is the user-facing entry point.  It refuses to stage a legacy database if
an existing (family, parameter) collision has a different stored Weierstrass
model, preventing legacy witnesses from being attached to the wrong curve.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from rank42.legacy_recovery import merge, report, stage, validate


def _norm_model(value):
    try:
        data = json.loads(value or "[]")
    except Exception:
        return None
    if not isinstance(data, list) or len(data) != 5:
        return None
    return tuple(str(x) for x in data)


def collision_preflight(live: Path, legacy: Path):
    db = sqlite3.connect(str(live))
    db.row_factory = sqlite3.Row
    db.execute("ATTACH DATABASE ? AS legacy", (str(legacy),))
    try:
        rows = db.execute(
            """
            SELECT n.id AS live_id, o.id AS old_id,
                   n.family, n.parameter,
                   n.a_invariants_json AS live_model,
                   o.a_invariants_json AS old_model
            FROM legacy.curves o
            JOIN main.curves n
              ON n.family=o.family AND n.parameter=o.parameter
            ORDER BY o.id
            """
        ).fetchall()
        mismatches = []
        matches = []
        for row in rows:
            item = {
                "live_id": int(row["live_id"]),
                "old_id": int(row["old_id"]),
                "family": row["family"],
                "parameter": row["parameter"],
            }
            if _norm_model(row["live_model"]) == _norm_model(row["old_model"]):
                matches.append(item)
            else:
                mismatches.append(item)
        return {"collisions": len(rows), "model_matches": matches, "model_mismatches": mismatches}
    finally:
        try:
            db.execute("DETACH DATABASE legacy")
        except Exception:
            pass
        db.close()


def main():
    ap = argparse.ArgumentParser(
        description="Safely recover an old Rank42 database through a disposable staging DB"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("preflight")
    p.add_argument("--live", required=True)
    p.add_argument("--legacy", required=True)

    s = sub.add_parser("stage")
    s.add_argument("--live", required=True)
    s.add_argument("--legacy", required=True)
    s.add_argument("--work", required=True)

    v = sub.add_parser("validate")
    v.add_argument("--work", required=True)
    v.add_argument("--min-legacy-lower", type=int, default=12)
    v.add_argument("--timeout", type=int, default=120)
    v.add_argument("--max-halvings", type=int, default=40)
    v.add_argument("--limit", type=int)

    r = sub.add_parser("report")
    r.add_argument("--work", required=True)

    m = sub.add_parser("merge")
    m.add_argument("--live", required=True)
    m.add_argument("--work", required=True)
    m.add_argument("--backup", required=True)
    m.add_argument("--certified-only", action="store_true")

    args = ap.parse_args()

    if args.cmd == "preflight":
        out = collision_preflight(Path(args.live), Path(args.legacy))
    elif args.cmd == "stage":
        live, legacy, work = Path(args.live), Path(args.legacy), Path(args.work)
        check = collision_preflight(live, legacy)
        if check["model_mismatches"]:
            raise SystemExit(
                "REFUSING STAGE: family/parameter collision has a different curve model:\n"
                + json.dumps(check, indent=2, sort_keys=True)
            )
        out = {"preflight": check, "stage": stage(live, legacy, work)}
    elif args.cmd == "validate":
        out = validate(Path(args.work), args.min_legacy_lower, args.timeout, args.max_halvings, args.limit)
    elif args.cmd == "report":
        out = report(Path(args.work))
    else:
        out = merge(Path(args.live), Path(args.work), Path(args.backup), args.certified_only)

    print(json.dumps(out, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
