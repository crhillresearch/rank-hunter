"""CLI for persistent ratpoints searches on quartic/hyperelliptic covers."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from rank42.db import connect, get_curve
from rank42.quartic_store import (
    create_or_get_search, finish_search, list_searches, mark_running, store_points,
)
from rank42.ratpoints import (
    RatpointsFailure, RatpointsNotFound, RatpointsTimeout,
    normalize_polynomial, probe_version, run_ratpoints,
)


def parse_coefficients(text):
    return [x.strip() for x in text.split(",") if x.strip()]


def parse_args():
    ap = argparse.ArgumentParser(
        description="Search y^2=f(x) with ratpoints and persist exact results."
    )
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--coefficients", help="comma-separated a0,a1,...,an")
    ap.add_argument("--input", help="JSON job with coefficients/height/options")
    ap.add_argument("--height", type=int)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--curve-id", type=int)
    ap.add_argument("--family")
    ap.add_argument("--parameter")
    ap.add_argument("--hole-label")
    ap.add_argument("--denominator-low", type=int)
    ap.add_argument("--denominator-high", type=int)
    ap.add_argument("--ratpoints")
    ap.add_argument("--one-point", action="store_true")
    ap.add_argument("--extra-arg", action="append", default=[])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--source-search-id", type=int, help="copy persisted provenance metadata from a prior quartic search")
    ap.add_argument("--check", action="store_true", help="probe ratpoints and exit")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--list-status")
    ap.add_argument("--limit", type=int, default=30)
    return ap.parse_args()


def load_job(args):
    data = {}
    if args.input:
        data = json.loads(Path(args.input).read_text())
    coeffs = parse_coefficients(args.coefficients) if args.coefficients else data.get("coefficients")
    height = args.height if args.height is not None else data.get("height")
    if not coeffs:
        raise SystemExit("provide --coefficients or --input")
    if height is None:
        raise SystemExit("provide --height or height in --input")
    return {
        "coefficients": coeffs,
        "height": int(height),
        "curve_id": args.curve_id if args.curve_id is not None else data.get("curve_id"),
        "family": args.family if args.family is not None else data.get("family"),
        "parameter": args.parameter if args.parameter is not None else data.get("parameter"),
        "hole_label": args.hole_label if args.hole_label is not None else data.get("hole_label"),
        "denominator_low": args.denominator_low if args.denominator_low is not None else data.get("denominator_low"),
        "denominator_high": args.denominator_high if args.denominator_high is not None else data.get("denominator_high"),
        "extra_args": list(data.get("extra_args") or []) + list(args.extra_arg or []),
        "metadata": dict(data.get("metadata") or {}),
    }


def show_list(db, args):
    rows = list_searches(db, status=args.list_status, limit=args.limit)
    if not rows:
        print("no quartic searches")
        return
    print("QUARTIC SEARCHES")
    print("=" * 78)
    for r in rows:
        print(
            f"#{r['id']:<4} {r['status']:<7} points={r['point_count'] or 0:<4} "
            f"H={r['height_bound']:<9} curve={r['curve_id'] or '-':<5} "
            f"hole={r['hole_label'] or '-'}"
        )


def main():
    args = parse_args()
    if args.check:
        try:
            info = probe_version(args.ratpoints)
        except (RatpointsNotFound, RatpointsTimeout) as exc:
            raise SystemExit(str(exc))
        print(json.dumps(info, indent=2, sort_keys=True))
        return

    db = connect(args.db)
    if args.list:
        show_list(db, args)
        return

    job = load_job(args)
    if args.source_search_id is not None:
        source = db.execute(
            "SELECT * FROM quartic_searches WHERE id=?",
            (int(args.source_search_id),),
        ).fetchone()
        if source is None:
            raise SystemExit(f"quartic search #{int(args.source_search_id)} not found")
        try:
            source_metadata = json.loads(source["metadata_json"] or "{}")
        except Exception:
            source_metadata = {}
        if not isinstance(source_metadata, dict):
            source_metadata = {}
        source_metadata["workbench_parent_search_id"] = int(args.source_search_id)
        source_metadata.update(dict(job.get("metadata") or {}))
        job["metadata"] = source_metadata
    if job["curve_id"] is not None:
        curve = get_curve(db, int(job["curve_id"]))
        if curve is None:
            raise SystemExit(f"curve #{job['curve_id']} not found")
        if job["family"] is None:
            job["family"] = curve["family"]
        if job["parameter"] is None:
            job["parameter"] = curve["parameter"]

    norm = normalize_polynomial(job["coefficients"])
    row = create_or_get_search(
        db,
        curve_id=job["curve_id"], family=job["family"], parameter=job["parameter"],
        hole_label=job["hole_label"], coefficients=norm.rational_coefficients,
        integer_coefficients=norm.integer_coefficients, y_scale=norm.y_scale,
        degree=norm.degree, height_bound=job["height"],
        denominator_low=job["denominator_low"], denominator_high=job["denominator_high"],
        extra_args=job["extra_args"], metadata=job["metadata"],
    )
    if row["status"] == "done" and not args.force:
        print(f"quartic search #{row['id']} already done with {row['point_count'] or 0} point(s)")
        return

    try:
        info = probe_version(args.ratpoints)
        mark_running(db, row["id"], executable=info["executable"])
    except RatpointsNotFound as exc:
        finish_search(db, row["id"], status="error", error=str(exc))
        raise SystemExit(str(exc))

    print("RANK HUNTER QUARTIC SEARCH")
    print("=" * 62)
    print("search id            =", row["id"])
    print("degree               =", norm.degree)
    print("coefficients         =", [str(x) for x in norm.rational_coefficients])
    print("integral coefficients=", list(norm.integer_coefficients))
    print("Y scale              =", norm.y_scale)
    print("height               =", job["height"])
    print("ratpoints            =", info["executable"])
    print("ratpoints version    =", info["version"] or "unknown")

    started = time.monotonic()
    try:
        result = run_ratpoints(
            norm.rational_coefficients,
            job["height"],
            executable=info["executable"], timeout=args.timeout,
            denominator_low=job["denominator_low"], denominator_high=job["denominator_high"],
            one_point=args.one_point, extra_args=job["extra_args"],
        )
    except RatpointsTimeout as exc:
        runtime = time.monotonic() - started
        finish_search(db, row["id"], status="timeout", runtime=runtime, error=str(exc))
        print("RESULT: TIMEOUT")
        return
    except RatpointsFailure as exc:
        runtime = time.monotonic() - started
        finish_search(db, row["id"], status="error", runtime=runtime, error=str(exc))
        print("RESULT: ERROR")
        print(exc)
        return

    store_points(db, row["id"], result["points"])
    finish_search(
        db, row["id"], status="done", runtime=result["runtime"],
        point_count=len(result["points"]), error=None,
    )
    print("runtime              =", f"{result['runtime']:.3f}s")
    print("finite points        =", len(result["points"]))
    for P in result["points"][:20]:
        print("   ", str(P.x), str(P.y))
    if len(result["points"]) > 20:
        print(f"    ... {len(result['points']) - 20} more persisted in SQLite")


if __name__ == "__main__":
    main()
