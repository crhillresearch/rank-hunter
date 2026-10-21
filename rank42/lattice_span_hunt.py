"""Screen exact point ledgers against a certified Mordell--Weil lattice.

Canonical-height orthogonal residuals cheaply reject points already in the
known rational span.  A point is added to the working basis only after the
exact finite-field independence certificate succeeds.  Numerical heights
are a sieve; they are never written as a proof of independence.

Run with the Rank Hunter Sage environment, for example:

  sage -python -m rank42.lattice_span_hunt --db rank42.db --curve-id 2904 \
      --source x1152_lattice_hole_ratpoints --target-rank 20
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from pathlib import Path

from sage.all import EllipticCurve, QQ, vector

from rank42.db import connect
from rank42.exact_lb import run_exact_certificate
from rank42.point_promotion import promote_certified_subgroup


def _point_key(P):
    Q = -P
    return min((str(P[0]), str(P[1])), (str(Q[0]), str(Q[1])))


def _write_report(path, report):
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    os.replace(temp, target)


def _load_curve_and_points(args):
    db = sqlite3.connect(args.db)
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM curves WHERE id=?", (args.curve_id,)).fetchone()
    if row is None:
        raise ValueError(f"curve #{args.curve_id} not found")
    ainvs = json.loads(row["a_invariants_json"] or "[]")
    if len(ainvs) != 5:
        raise ValueError("stored curve must have five a-invariants")
    E = EllipticCurve(QQ, [QQ(str(x)) for x in ainvs])

    required = max(int(row["generic_lower"] or 0), int(row["descent_lower"] or 0))
    raw_basis = json.loads(row["generators_json"] or "[]")
    basis = []
    seen = set()
    for xy in raw_basis:
        if len(xy) < 2:
            continue
        P = E(QQ(str(xy[0])), QQ(str(xy[1])))
        key = _point_key(P)
        if key not in seen:
            seen.add(key)
            basis.append(P)
    if len(basis) < required:
        raise ValueError(
            f"curve lower bound is {required}, but generators_json only replays "
            f"{len(basis)} distinct points; repair the witness basis first"
        )

    query = """SELECT id,x,y,source,search_ref,metadata_json FROM points
               WHERE curve_id=? AND exact_verified=1 AND role='candidate_extra'
                 AND independence_status NOT IN ('dependent','rigorous_independent')"""
    values: list[object] = [args.curve_id]
    if args.source:
        query += " AND source=?"
        values.append(args.source)
    query += " ORDER BY id"
    if args.candidate_limit:
        query += " LIMIT ?"
        values.append(int(args.candidate_limit))
    candidates = db.execute(query, values).fetchall()
    db.close()

    deduped = []
    candidate_seen = set(seen)
    for rec in candidates:
        P = E(QQ(str(rec["x"])), QQ(str(rec["y"])))
        key = _point_key(P)
        if key in candidate_seen:
            continue
        candidate_seen.add(key)
        deduped.append((rec, P))
    return row, ainvs, E, basis, deduped, required


def _independent_residual(H, active_indices, candidate_index):
    block = H.matrix_from_rows_and_columns(active_indices, active_indices)
    cross = vector([H[candidate_index, j] for j in active_indices])
    coeff = block.solve_right(cross)
    return H[candidate_index, candidate_index] - cross.dot_product(coeff)


def run(args):
    row, ainvs, E, basis, candidates, required = _load_curve_and_points(args)
    if len(basis) > 64:
        raise ValueError("exact certificate worker supports at most 64 points")
    if not basis:
        raise ValueError("no replayable witness basis is stored")

    baseline_cert = run_exact_certificate(
        ainvs,
        [[str(P[0]), str(P[1])] for P in basis],
        timeout=args.certificate_timeout,
    )
    if not baseline_cert.get("independent"):
        raise ValueError(
            "stored generator basis did not reproduce its exact independence "
            f"certificate (status={baseline_cert.get('status')})"
        )

    H0 = E.height_pairing_matrix(basis, precision=args.precision)
    if not H0.is_positive_definite():
        raise ValueError("certified basis height Gram is numerically non-positive")

    active = list(basis)
    active_records = []
    rejected = []
    start = time.monotonic()
    report = {
        "curve_id": int(args.curve_id),
        "parameter": str(row["parameter"]),
        "source": args.source,
        "baseline_rank": len(basis),
        "baseline_certificate": baseline_cert,
        "candidate_count": len(candidates),
        "height_precision_bits": int(args.precision),
        "height_threshold_relative": float(args.height_threshold),
        "selected": [],
        "height_rejected": 0,
        "certificate_rejected": [],
        "status": "screening",
    }
    _write_report(args.output, report)
    print(
        f"curve #{args.curve_id} · exact baseline rank ≥{len(basis)} · "
        f"{len(candidates)} exact points to sieve · target {args.target_rank}",
        flush=True,
    )

    for offset in range(0, len(candidates), args.batch_size):
        batch = candidates[offset : offset + args.batch_size]
        batch_points = [P for _, P in batch]
        batch_active_count = len(active)
        H = E.height_pairing_matrix(active + batch_points, precision=args.precision)
        # Selected points within this batch are included by their row indices.
        current_indices = list(range(batch_active_count))
        selected_this_batch = []
        for j, (rec, P) in enumerate(batch):
            # Candidate rows stay after the active prefix even as `active`
            # grows in the Python list.
            candidate_index = batch_active_count + j
            residual = _independent_residual(H, current_indices, candidate_index)
            diagonal = abs(H[candidate_index, candidate_index])
            threshold = max(1, diagonal) * args.height_threshold
            if residual <= threshold:
                report["height_rejected"] += 1
                continue

            new_basis = active + [P]
            if len(new_basis) > 64:
                report["status"] = "point_limit_reached"
                _write_report(args.output, report)
                return report
            cert = run_exact_certificate(
                ainvs,
                [[str(Q[0]), str(Q[1])] for Q in new_basis],
                timeout=args.certificate_timeout,
            )
            if not cert.get("independent"):
                rejected.append({
                    "point_id": int(rec["id"]),
                    "height_residual": str(residual),
                    "certificate_status": cert.get("status"),
                    "certificate": cert,
                })
                report["certificate_rejected"] = rejected[-50:]
                continue

            active.append(P)
            active_records.append({
                "point_id": int(rec["id"]),
                "height_residual": str(residual),
                "exact_certificate": cert,
            })
            current_indices.append(candidate_index)
            selected_this_batch.append(int(rec["id"]))
            report["selected"] = active_records
            report["rank_lower_bound"] = len(active)
            report["last_processed_candidate"] = int(rec["id"])
            # Persist only after the exact certificate has accepted the
            # enlarged subgroup.  Height residuals alone never change DB rank.
            writer = connect(args.db)
            promote_certified_subgroup(
                writer,
                curve_id=args.curve_id,
                E=E,
                points=active,
                source="x1152_lattice_span_hunt",
                search_ref=f"lattice-span-hunt:curve:{args.curve_id}",
                certificate=cert,
                metadata={
                    "candidate_source": args.source,
                    "method": "canonical-height orthogonal sieve + exact quadratic-character certificate",
                    "height_residual": str(residual),
                    "candidate_point_id": int(rec["id"]),
                },
                engine="x1152_lattice_span_hunt",
            )
            writer.close()
            _write_report(args.output, report)
            if len(active) >= args.target_rank:
                report["status"] = "target_certified"
                report["elapsed_seconds"] = time.monotonic() - start
                _write_report(args.output, report)
                print(f"TARGET CERTIFIED: rank ≥{len(active)}", flush=True)
                return report

        report["last_batch_end"] = offset + len(batch)
        report["selected_this_batch"] = selected_this_batch
        report["elapsed_seconds"] = time.monotonic() - start
        _write_report(args.output, report)
        print(
            f"screened {offset + len(batch)}/{len(candidates)} · "
            f"height-span rejects={report['height_rejected']} · "
            f"certified rank ≥{len(active)}",
            flush=True,
        )

    report["status"] = "no_new_certified_direction"
    report["elapsed_seconds"] = time.monotonic() - start
    _write_report(args.output, report)
    return report


def parse_args():
    parser = argparse.ArgumentParser(
        description="Height-lattice sieve followed by exact point independence certificates"
    )
    parser.add_argument("--db", default="rank42.db")
    parser.add_argument("--curve-id", type=int, required=True)
    parser.add_argument("--source", default="x1152_lattice_hole_ratpoints")
    parser.add_argument("--target-rank", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--candidate-limit", type=int, default=0,
                        help="screen only the first N candidates; 0 means all")
    parser.add_argument("--precision", type=int, default=128)
    parser.add_argument("--height-threshold", type=float, default=1e-12)
    parser.add_argument("--certificate-timeout", type=int, default=45)
    parser.add_argument("--output", default=None)
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
