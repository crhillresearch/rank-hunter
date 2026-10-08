"""Execute an exact covering quartic and screen mapped points for rank growth."""

from __future__ import annotations

import argparse
import json
import time

from sage.all import QQ, EllipticCurve

from rank42.coverings import load_covering_file, map_quartic_point, validate_covering
from rank42.db import connect, get_curve, log_event
from rank42.lattice_store import (
    get_covering, get_lattice, store_covering, store_extra_point, update_covering,
)
from rank42.mathutil import height_screen
from rank42.quartic_store import (
    create_or_get_search, finish_search, mark_running, store_points,
)
from rank42.ratpoints import (
    RatpointsFailure, RatpointsNotFound, RatpointsTimeout,
    normalize_polynomial, probe_version, run_ratpoints,
)


def _row_to_covering_data(row):
    return validate_covering({
        "schema": row["schema_version"],
        "curve_id": row["curve_id"],
        "lattice_id": row["lattice_id"],
        "hole_id": row["hole_id"],
        "quartic": json.loads(row["quartic_json"]),
        "map": {"x": row["map_x"], "y": row["map_y"]},
        "metadata": json.loads(row["metadata_json"] or "{}"),
    })


def _curve_from_row(row):
    return EllipticCurve(QQ, [QQ(str(x)) for x in json.loads(row["a_invariants_json"])])


def _basis_from_lattice(E, lattice):
    raw = json.loads(lattice["basis_json"])
    return [E(QQ(str(x)), QQ(str(y))) for x, y in raw]


def _canonical_key(P):
    Q = -P
    return min((QQ(P[0]), QQ(P[1])), (QQ(Q[0]), QQ(Q[1])))


def parse_args():
    ap = argparse.ArgumentParser(
        description="Search one exact covering and screen mapped E(Q) points."
    )
    ap.add_argument("--db", default="rank42.db")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", help="rank42.covering.v1 JSON")
    src.add_argument("--covering-id", type=int)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--ratpoints")
    ap.add_argument("--precision", type=int, default=256,
                    help="height-screen precision for mapped rank-growth candidates")
    ap.add_argument("--force-search", action="store_true")
    return ap.parse_args()


def main():
    args = parse_args()
    db = connect(args.db)

    if args.input:
        data = load_covering_file(args.input)
        covering = store_covering(db, data)
    else:
        covering = get_covering(db, args.covering_id)
        if covering is None:
            raise SystemExit(f"covering #{args.covering_id} not found")
        data = _row_to_covering_data(covering)

    curve_row = get_curve(db, int(data["curve_id"]))
    if curve_row is None or not curve_row["a_invariants_json"]:
        raise SystemExit(f"curve #{data['curve_id']} is unavailable or lacks a-invariants")
    E = _curve_from_row(curve_row)

    lattice = None
    basis = []
    if data.get("lattice_id") is not None:
        lattice = get_lattice(db, int(data["lattice_id"]))
        if lattice is None:
            raise SystemExit(f"lattice #{data['lattice_id']} not found")
        if int(lattice["curve_id"]) != int(data["curve_id"]):
            raise SystemExit("covering lattice and target curve do not match")
        basis = _basis_from_lattice(E, lattice)

    quartic = data["quartic"]
    norm = normalize_polynomial(quartic["coefficients"])
    search = create_or_get_search(
        db,
        curve_id=data["curve_id"],
        family=curve_row["family"],
        parameter=curve_row["parameter"],
        hole_label=(f"hole:{data.get('hole_id')}" if data.get("hole_id") else f"covering:{covering['id']}"),
        coefficients=norm.rational_coefficients,
        integer_coefficients=norm.integer_coefficients,
        y_scale=norm.y_scale,
        degree=norm.degree,
        height_bound=int(quartic["height"]),
        denominator_low=quartic.get("denominator_low"),
        denominator_high=quartic.get("denominator_high"),
        extra_args=list(quartic.get("extra_args") or []),
        metadata={
            "covering_id": covering["id"],
            "lattice_id": data.get("lattice_id"),
            "hole_id": data.get("hole_id"),
        },
    )
    update_covering(db, covering["id"], quartic_search_id=search["id"])

    if search["status"] != "done" or args.force_search:
        try:
            info = probe_version(args.ratpoints)
            mark_running(db, search["id"], executable=info["executable"])
        except RatpointsNotFound as exc:
            finish_search(db, search["id"], status="error", error=str(exc))
            update_covering(db, covering["id"], status="error", error=str(exc))
            raise SystemExit(str(exc))

        print("[ratpoints] searching exact covering quartic...")
        started = time.monotonic()
        try:
            result = run_ratpoints(
                norm.rational_coefficients,
                int(quartic["height"]),
                executable=info["executable"],
                timeout=args.timeout,
                denominator_low=quartic.get("denominator_low"),
                denominator_high=quartic.get("denominator_high"),
                one_point=bool(quartic.get("one_point", False)),
                extra_args=list(quartic.get("extra_args") or []),
            )
        except RatpointsTimeout as exc:
            runtime = time.monotonic() - started
            finish_search(db, search["id"], status="timeout", runtime=runtime, error=str(exc))
            update_covering(db, covering["id"], status="timeout", error=str(exc))
            print("RESULT: TIMEOUT")
            return
        except RatpointsFailure as exc:
            runtime = time.monotonic() - started
            finish_search(db, search["id"], status="error", runtime=runtime, error=str(exc))
            update_covering(db, covering["id"], status="error", error=str(exc))
            print("RESULT: ERROR")
            print(exc)
            return
        store_points(db, search["id"], result["points"])
        finish_search(
            db, search["id"], status="done", runtime=result["runtime"],
            point_count=len(result["points"]), error=None,
        )
    else:
        print(f"[ratpoints] reusing completed quartic search #{search['id']}")

    qrows = db.execute(
        "SELECT * FROM quartic_points WHERE search_id=? ORDER BY id",
        (search["id"],),
    ).fetchall()
    print("[map] quartic points =", len(qrows))

    working = list(basis)
    seen = {_canonical_key(P) for P in working}
    accepted = 0
    mapped = 0
    for qrow in qrows:
        try:
            P = map_quartic_point(E, data, qrow["x"], qrow["y"])
        except Exception as exc:
            old_meta = json.loads(qrow["metadata_json"] or "{}")
            old_meta["map_error"] = repr(exc)
            db.execute(
                "UPDATE quartic_points SET metadata_json=? WHERE id=?",
                (json.dumps(old_meta, sort_keys=True), qrow["id"]),
            )
            db.commit()
            continue

        mapped += 1
        db.execute(
            "UPDATE quartic_points SET mapped_point_json=? WHERE id=?",
            (json.dumps([str(P[0]), str(P[1])]), qrow["id"]),
        )
        db.commit()

        key = _canonical_key(P)
        before = len(working)
        independent = False
        screen = None
        if key not in seen:
            print(f"[height] screening mapped point {mapped}/{len(qrows)} against {before} basis points...")
            try:
                screen = height_screen(E, working + [P], precision=args.precision)
                independent = bool(screen["positive_definite_screen"])
            except Exception as exc:
                screen = {"error": repr(exc), "positive_definite_screen": False}
            if independent:
                working.append(P)
                seen.add(key)
                accepted += 1

        store_extra_point(
            db,
            covering_id=covering["id"], quartic_point_id=qrow["id"],
            curve_id=data["curve_id"], x=P[0], y=P[1],
            exact_verified=True, independence_screen=independent,
            basis_count_before=before, basis_count_after=len(working),
            determinant=(screen or {}).get("determinant"),
            min_eigenvalue=(screen or {}).get("min_eigenvalue"),
            metadata={
                "precision_bits": args.precision,
                "screen_only": True,
                "note": "positive-definite numerical height matrix; not formal proof",
            },
        )

    update_covering(db, covering["id"], status="mapped")
    if accepted:
        log_event(
            db,
            data["curve_id"],
            "prom",
            f"covering #{covering['id']} produced {accepted} mapped point(s) passing rank-growth height screen",
        )

    print()
    print("RANK HUNTER EXTRA-POINT RESULT")
    print("=" * 62)
    print("covering id          =", covering["id"])
    print("quartic search id    =", search["id"])
    print("mapped exactly       =", mapped)
    print("basis before         =", len(basis))
    print("height-screen extras =", accepted)
    print("screened basis after =", len(working))
    print("NOTE: rank-growth result is a numerical height screen, not yet a formal rank proof")


if __name__ == "__main__":
    main()
