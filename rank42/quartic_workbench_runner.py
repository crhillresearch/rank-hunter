"""CLI adapter for researcher-directed Quartics workbench actions.

The adapter does not implement quartic arithmetic.  It invokes the same pointed
quartic and stored-covering services used by automated Pipeline execution.
"""
from __future__ import annotations

import argparse
import json

from sage.all import EllipticCurve, QQ

from rank42.auto_point_search import certify_ledger_growth
from rank42.covering_local_height import plan_covering_searches
from rank42.covering_search import search_stored_covering
from rank42.db import connect, get_curve
from rank42.pointed_quartic import run_pointed_quartic_escalation


MARKER = "RANK42_QUARTICS_WORKBENCH_RESULT="


def parse_args():
    ap = argparse.ArgumentParser(description="Run one persisted Quartics workbench action")
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--mode", choices=("pointed", "covering", "covering-local"), required=True)
    ap.add_argument("--search-id", type=int)
    ap.add_argument("--covering-id", type=int)
    ap.add_argument("--curve-id", type=int)
    ap.add_argument("--max-coverings", type=int, default=100)
    ap.add_argument("--height", type=int, default=10000)
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--anchors", type=int, default=8)
    ap.add_argument("--alternate-anchors", action="store_true")
    ap.add_argument("--ratpoints")
    ap.add_argument("--one-point", action="store_true")
    ap.add_argument("--certificate-timeout", type=int, default=120)
    ap.add_argument("--exact-candidates", type=int, default=64)
    return ap.parse_args()


def _curve(db, curve_id):
    row = get_curve(db, int(curve_id))
    if row is None:
        raise SystemExit(f"curve #{int(curve_id)} not found")
    try:
        ainvs = json.loads(row["a_invariants_json"] or "[]")
    except Exception:
        ainvs = []
    if len(ainvs) != 5:
        raise SystemExit(f"curve #{int(curve_id)} has no usable stored a-invariants")
    E = EllipticCurve(QQ, [QQ(str(x)) for x in ainvs])
    return row, E


def _pointed(db, args):
    if args.search_id is None:
        raise SystemExit("--search-id is required for pointed mode")
    search = db.execute(
        "SELECT * FROM quartic_searches WHERE id=?",
        (int(args.search_id),),
    ).fetchone()
    if search is None:
        raise SystemExit(f"quartic search #{int(args.search_id)} not found")
    if search["curve_id"] is None:
        raise SystemExit("selected quartic search is not attached to a retained curve")
    curve, E = _curve(db, int(search["curve_id"]))
    try:
        metadata = json.loads(search["metadata_json"] or "{}")
    except Exception:
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}

    preferred = ()
    vector = metadata.get("anchor_vector")
    if not args.alternate_anchors and isinstance(vector, list) and vector:
        preferred = ({
            "vector": list(vector),
            "workbench_parent_search_id": int(search["id"]),
            "source": "quartics_workbench",
        },)

    result = run_pointed_quartic_escalation(
        db,
        curve_id=int(search["curve_id"]),
        E=E,
        heights=(max(1, int(args.height)),),
        anchors=max(1, int(args.anchors)),
        pool_size=max(32, max(1, int(args.anchors)) * 8),
        rounds=1,
        deep_keep=max(1, min(8, int(args.anchors))),
        timeout=max(1, int(args.timeout)),
        reduce_timeout=min(30, max(5, int(args.timeout))),
        executable=args.ratpoints,
        certificate_timeout=max(1, int(args.certificate_timeout)),
        exact_candidates=max(1, int(args.exact_candidates)),
        parameter=curve["parameter"],
        preferred_anchor_vectors=preferred,
    )
    result["workbench_mode"] = "pointed"
    result["parent_search_id"] = int(search["id"])
    result["alternate_anchors"] = bool(args.alternate_anchors)
    return result


def _covering(db, args):
    if args.covering_id is None:
        raise SystemExit("--covering-id is required for covering mode")
    covering = db.execute(
        "SELECT * FROM coverings WHERE id=?",
        (int(args.covering_id),),
    ).fetchone()
    if covering is None:
        raise SystemExit(f"covering #{int(args.covering_id)} not found")
    _curve_row, E = _curve(db, int(covering["curve_id"]))
    branch = search_stored_covering(
        db,
        row=covering,
        E=E,
        height=max(1, int(args.height)),
        timeout=max(1, int(args.timeout)),
        ratpoints=args.ratpoints,
        one_point=bool(args.one_point),
        run_id=None,
        stage_index=None,
        stage_id="analysis_quartics",
        applied_plan=None,
        resume_completed=False,
        source="analysis_quartics_covering",
    )
    cert = certify_ledger_growth(
        db,
        curve_id=int(covering["curve_id"]),
        E=E,
        source="analysis_quartics_covering",
        search_ref=f"covering:{int(covering['id'])}:exact",
        certificate_timeout=max(1, int(args.certificate_timeout)),
        max_candidates=max(1, int(args.exact_candidates)),
    )
    return {
        "workbench_mode": "covering",
        "covering_id": int(covering["id"]),
        "branch": branch,
        "certificate": cert,
    }


def _covering_local(db, args):
    if args.curve_id is None:
        raise SystemExit("--curve-id is required for covering-local mode")
    curve_id = int(args.curve_id)
    if get_curve(db, curve_id) is None:
        raise SystemExit(f"curve #{curve_id} not found")
    result = plan_covering_searches(
        db,
        curve_id=curve_id,
        max_coverings=max(1, int(args.max_coverings)),
        timeout=max(1, int(args.timeout)),
        base_height=max(1, int(args.height)),
    )
    result["workbench_mode"] = "covering-local"
    result["curve_id"] = curve_id
    return result


def main():
    args = parse_args()
    db = connect(args.db)
    try:
        if args.mode == "pointed":
            result = _pointed(db, args)
        elif args.mode == "covering":
            result = _covering(db, args)
        else:
            result = _covering_local(db, args)
        print(MARKER + json.dumps(result, sort_keys=True, default=str), flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
