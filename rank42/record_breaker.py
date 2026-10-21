"""Target-driven record-breaking mode for exact elliptic-curve rank witnesses.

The objective is deliberately narrower than exact-rank certification:

    exact rational points -> exact independence -> rank >= target -> export

Heuristics may schedule work, but only persisted rigorous evidence can satisfy
the target.  The exported FrontierMath-compatible witness is re-certified by
default before it is written.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sage.all import EllipticCurve, QQ, ZZ

from rank42.db import connect, get_curve, proven_lower
from rank42.exact_lb import (
    ExactCertificateFailure,
    ExactCertificateTimeout,
    run_exact_certificate,
)
from rank42.pipeline_catalog import template_stages, validate_pipeline
from rank42.pipeline_state import create_pipeline_run


MARKER = "RANK42_RECORD_BREAKER="


def _point_key(P):
    Q = -P
    return min(
        (str(P[0]), str(P[1])),
        (str(Q[0]), str(Q[1])),
    )


def _point_from_record(E, rec):
    if isinstance(rec, dict):
        if "x" not in rec or "y" not in rec:
            return None
        raw = (rec["x"], rec["y"])
    elif isinstance(rec, (list, tuple)) and len(rec) >= 2:
        raw = (rec[0], rec[1])
    else:
        return None
    try:
        P = E(QQ(str(raw[0])), QQ(str(raw[1])))
    except Exception:
        return None
    return None if P.is_zero() else P


def _stored_rigorous_basis(db, curve_id, E):
    """Collect the strongest stored exact witness basis without inventing rank."""
    row = get_curve(db, int(curve_id))
    if row is None:
        raise ValueError(f"curve #{curve_id} not found")

    out = []
    seen = set()

    # generators_json is updated when exact certification grows the basis.
    try:
        generators = json.loads(row["generators_json"] or "[]")
    except Exception:
        generators = []
    if isinstance(generators, list):
        for rec in generators:
            P = _point_from_record(E, rec)
            if P is None:
                continue
            key = _point_key(P)
            if key in seen:
                continue
            seen.add(key)
            out.append(P)

    # Fill from first-class rigorous ledger witnesses when needed.
    records = db.execute(
        """SELECT x,y FROM points
           WHERE curve_id=? AND exact_verified=1 AND rigorous_independent=1
           ORDER BY CASE role
                      WHEN 'generic_section' THEN 0
                      WHEN 'rigorous_witness' THEN 1
                      WHEN 'basis' THEN 2
                      ELSE 3
                    END,
                    id ASC""",
        (int(curve_id),),
    ).fetchall()
    for rec in records:
        P = _point_from_record(E, rec)
        if P is None:
            continue
        key = _point_key(P)
        if key in seen:
            continue
        seen.add(key)
        out.append(P)
    return out


def curve_frontier(db, *, target_rank=30, limit=50):
    """Return the current record-hunt frontier, ordered by rigorous progress."""
    target_rank = max(1, int(target_rank))
    rows = db.execute(
        """SELECT c.*,
                  COALESCE(p.exact_points,0) AS exact_points,
                  COALESCE(p.rigorous_points,0) AS rigorous_points
           FROM curves c
           LEFT JOIN (
             SELECT curve_id,
                    SUM(exact_verified=1) AS exact_points,
                    SUM(exact_verified=1 AND rigorous_independent=1) AS rigorous_points
             FROM points
             GROUP BY curve_id
           ) p ON p.curve_id=c.id"""
    ).fetchall()

    frontier = []
    for row in rows:
        lower = int(proven_lower(row))
        exact_points = int(row["exact_points"] or 0)
        rigorous_points = int(row["rigorous_points"] or 0)
        generator_count = 0
        try:
            stored_generators = json.loads(row["generators_json"] or "[]")
            if isinstance(stored_generators, list):
                generator_count = len(stored_generators)
        except Exception:
            generator_count = 0
        witness_count = max(rigorous_points, generator_count)
        frontier.append({
            "curve_id": int(row["id"]),
            "family": str(row["family"]),
            "parameter": str(row["parameter"]),
            "plugin_id": row["plugin_id"],
            "rigorous_lower": lower,
            "target_rank": target_rank,
            "rank_gap": max(0, target_rank - lower),
            "witness_count": witness_count,
            "witness_gap": max(0, target_rank - witness_count),
            "exact_points": exact_points,
            "rigorous_points": rigorous_points,
            "target_reached": bool(
                lower >= target_rank and witness_count >= target_rank
            ),
            "export_candidate": bool(
                lower >= target_rank and witness_count >= target_rank
            ),
            "score": None if row["score"] is None else float(row["score"]),
        })

    frontier.sort(
        key=lambda rec: (
            -int(rec["rigorous_lower"]),
            -int(rec["rigorous_points"]),
            -int(rec["exact_points"]),
            -float(rec["score"] if rec["score"] is not None else -1e99),
            int(rec["curve_id"]),
        )
    )
    return frontier[: max(1, int(limit))]


def best_submission_curve(db, *, target_rank=30):
    for rec in curve_frontier(db, target_rank=target_rank, limit=100000):
        if rec["export_candidate"]:
            return int(rec["curve_id"])
    return None


def _integral_model_and_points(E, points):
    """Map exact points to one integral Q-isomorphic Weierstrass model."""
    try:
        Ei = E.integral_model()
        if list(Ei.a_invariants()) == list(E.a_invariants()):
            mapped = [Ei(P) for P in points]
        else:
            phi = E.isomorphism_to(Ei)
            mapped = [phi(P) for P in points]
    except Exception:
        if not all(QQ(a).denominator() == 1 for a in E.a_invariants()):
            raise
        Ei = E
        mapped = [E(P) for P in points]

    ainvs = [QQ(a) for a in Ei.a_invariants()]
    if any(a.denominator() != 1 for a in ainvs):
        raise ArithmeticError("integral model conversion did not produce integer a-invariants")
    if Ei.discriminant() == 0:
        raise ArithmeticError("submission model is singular")
    return Ei, mapped


def build_submission(
    db,
    *,
    curve_id,
    target_rank=30,
    certificate_timeout=600,
    verify_independence=True,
):
    """Build an exact FrontierMath-compatible witness for one stored curve."""
    target_rank = max(1, int(target_rank))
    row = get_curve(db, int(curve_id))
    if row is None:
        raise ValueError(f"curve #{curve_id} not found")
    if not row["a_invariants_json"]:
        raise ValueError(f"curve #{curve_id} has no exact stored model")

    lower = int(proven_lower(row))
    if lower < target_rank:
        raise ValueError(
            f"curve #{curve_id} has rigorous lower {lower}, below target {target_rank}"
        )

    ainvs = [QQ(str(x)) for x in json.loads(row["a_invariants_json"])]
    E = EllipticCurve(QQ, ainvs)
    if E.discriminant() == 0:
        raise ArithmeticError("stored curve is singular")

    basis = _stored_rigorous_basis(db, int(curve_id), E)
    if len(basis) < target_rank:
        raise ValueError(
            f"curve #{curve_id} has rank>={lower} but only {len(basis)} exact "
            "stored witness points; refusing to fabricate a submission"
        )
    basis = basis[:target_rank]

    Ei, mapped = _integral_model_and_points(E, basis)
    seen_x = set()
    for P in mapped:
        if P.is_zero() or P not in Ei:
            raise ArithmeticError("mapped submission point failed exact curve verification")
        x = str(P[0])
        if x in seen_x:
            raise ArithmeticError(
                "submission basis contains repeated x-coordinate/inverse points"
            )
        seen_x.add(x)

    cert = None
    if verify_independence:
        try:
            cert = run_exact_certificate(
                Ei.a_invariants(),
                mapped,
                timeout=max(1, int(certificate_timeout)),
            )
        except (ExactCertificateFailure, ExactCertificateTimeout) as exc:
            raise RuntimeError(
                f"submission independence re-certification failed: {exc}"
            ) from exc
        if cert.get("independent") is not True:
            raise ArithmeticError(
                "submission points did not re-certify as linearly independent"
            )

    payload = {
        "curve": [str(ZZ(a)) for a in Ei.a_invariants()],
        "x_coords": [str(P[0]) for P in mapped],
    }
    if set(payload) != {"curve", "x_coords"}:
        raise AssertionError("submission payload contract changed")
    if len(payload["curve"]) != 5 or len(payload["x_coords"]) != target_rank:
        raise AssertionError("submission payload has wrong dimensions")

    return payload, {
        "curve_id": int(curve_id),
        "target_rank": target_rank,
        "rigorous_lower": lower,
        "certificate": None if cert is None else cert.get("certificate"),
        "independence_recertified": bool(verify_independence),
    }


def write_submission(
    db,
    *,
    output,
    curve_id=None,
    target_rank=30,
    certificate_timeout=600,
    verify_independence=True,
):
    if curve_id is None:
        curve_id = best_submission_curve(db, target_rank=target_rank)
    if curve_id is None:
        raise ValueError(
            f"no stored curve currently has an exportable rigorous rank>={int(target_rank)} witness"
        )
    payload, metadata = build_submission(
        db,
        curve_id=int(curve_id),
        target_rank=target_rank,
        certificate_timeout=certificate_timeout,
        verify_independence=verify_independence,
    )
    path = Path(output).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=True),
        encoding="utf-8",
    )
    return path, metadata


def create_record_breaker_run(
    db,
    *,
    target_mode,
    target,
    target_rank=30,
    project_root=".",
    pipeline_name=None,
    run_config=None,
):
    target_rank = max(1, int(target_rank))
    stages = template_stages("frontiermath_record_breaker", target_mode)
    errors = validate_pipeline(target_mode, stages)
    if errors:
        raise ValueError("invalid record-breaker preset: " + "; ".join(errors))

    config = {
        "project_root": str(Path(project_root).resolve()),
        "target_rank": target_rank,
        "record_breaker_mode": True,
        "certificate_timeout": 300,
        "exact_candidates": 256,
        "retention_floor": 0,
    }
    config.update(dict(run_config or {}))
    config["target_rank"] = target_rank
    config["record_breaker_mode"] = True

    return create_pipeline_run(
        db,
        pipeline_name=pipeline_name or f"Record Breaker >= {target_rank}",
        target_mode=str(target_mode),
        target=dict(target or {}),
        stages=stages,
        run_config=config,
    )


def parse_args():
    ap = argparse.ArgumentParser(
        description="Target-driven exact independent-point record breaker"
    )
    ap.add_argument("--db", default="rank42.db")
    sub = ap.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status")
    status.add_argument("--target-rank", type=int, default=30)
    status.add_argument("--limit", type=int, default=20)

    create = sub.add_parser("create-run")
    create.add_argument(
        "--target-mode",
        choices=("family", "torsion", "general", "curve"),
        required=True,
    )
    create.add_argument("--target-json", required=True)
    create.add_argument("--target-rank", type=int, default=30)
    create.add_argument("--project-root", default=".")

    export = sub.add_parser("export")
    export.add_argument("--curve-id", type=int)
    export.add_argument("--target-rank", type=int, default=30)
    export.add_argument("--output", required=True)
    export.add_argument("--certificate-timeout", type=int, default=600)
    export.add_argument("--no-recertify", action="store_true")
    return ap.parse_args()


def main():
    args = parse_args()
    db = connect(args.db)
    try:
        if args.command == "status":
            result = {
                "target_rank": max(1, int(args.target_rank)),
                "frontier": curve_frontier(
                    db,
                    target_rank=args.target_rank,
                    limit=args.limit,
                ),
            }
        elif args.command == "create-run":
            target = json.loads(args.target_json)
            if not isinstance(target, dict):
                raise ValueError("--target-json must decode to an object")
            run_id = create_record_breaker_run(
                db,
                target_mode=args.target_mode,
                target=target,
                target_rank=args.target_rank,
                project_root=args.project_root,
            )
            result = {
                "run_id": int(run_id),
                "target_rank": max(1, int(args.target_rank)),
                "record_breaker_mode": True,
                "run_command": (
                    f"python -m rank42.pipeline_runner --db {args.db} "
                    f"--project-root {args.project_root} --run-id {int(run_id)}"
                ),
            }
        else:
            path, metadata = write_submission(
                db,
                output=args.output,
                curve_id=args.curve_id,
                target_rank=args.target_rank,
                certificate_timeout=args.certificate_timeout,
                verify_independence=not args.no_recertify,
            )
            result = {
                **metadata,
                "output": str(path),
                "format": "frontiermath_elliptic_rank",
            }
        print(MARKER + json.dumps(result, sort_keys=True), flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
