"""Research-controlled rigorous upper-bound attacks for hard cases.

This is intentionally separate from the standard escalator. It exposes stronger
budgets/backends without changing rank state unless a rigorous backend returns
a certified upper bound or exact rank.
"""
from __future__ import annotations

import argparse
import hashlib
import json

from rank42.classical_descent import (
    ClassicalDescentFailure,
    ClassicalDescentTimeout,
    run_classical_descent,
)
from rank42.db import connect, get_curve, update_curve
from rank42.hard_case_escalator import _close_from_exact_ceiling
from rank42.points import rigorous_witness_basis, upsert_point
from rank42.rank_cert import (
    RankCertificationFailure,
    RankCertificationTimeout,
    run_rank_certification,
)
from rank42.rank_evidence import apply_reduced_rank_state, record_rank_evidence

ENGINE = "rank42.advanced_upper_bound"
ENGINE_VERSION = "1"
MARKER = "RANK42_ADVANCED_UPPER_RESULT="


def parse_args():
    ap = argparse.ArgumentParser(description="Advanced rigorous upper-bound attack")
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--curve-id", type=int, required=True)
    ap.add_argument("--point-id", type=int, action="append", default=[])
    ap.add_argument(
        "--backend",
        choices=["simon_strong", "sage_proof_rank"],
        default="simon_strong",
    )
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--lim1", type=int, default=10)
    ap.add_argument("--lim3", type=int, default=160)
    ap.add_argument("--limtriv", type=int, default=5)
    ap.add_argument("--maxprob", type=int, default=40)
    ap.add_argument("--limbigprime", type=int, default=60)
    return ap.parse_args()




def _basis_fingerprint(points):
    raw = json.dumps(
        [[str(P[0]), str(P[1])] for P in points],
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _options(rec):
    try:
        value = json.loads(rec["options_json"] or "{}")
    except Exception:
        value = {}
    return value if isinstance(value, dict) else {}


def _prior_attempt(db, args, *, basis_fingerprint=None):
    rows = db.execute(
        """
        SELECT * FROM rank_evidence
        WHERE curve_id=? AND engine=?
        ORDER BY id DESC LIMIT 500
        """,
        (int(args.curve_id), ENGINE),
    ).fetchall()
    for rec in rows:
        options = _options(rec)
        if str(options.get("backend") or "") != str(args.backend):
            continue
        if str(args.backend) == "simon_strong":
            if str(options.get("basis_fingerprint") or "") != str(basis_fingerprint or ""):
                continue
        try:
            if int(options.get("timeout") or 0) < int(args.timeout):
                continue
        except Exception:
            continue
        if str(args.backend) == "simon_strong":
            stronger = True
            for key in ("lim1", "lim3", "limtriv", "maxprob", "limbigprime"):
                try:
                    if int(options.get(key) or 0) < int(getattr(args, key)):
                        stronger = False
                        break
                except Exception:
                    stronger = False
                    break
            if not stronger:
                continue
        if str(rec["status"] or "") in {"completed", "timeout", "error"}:
            return rec
    return None


def _store_returned_points(db, curve_id, result):
    ids = []
    for xy in result.get("points") or []:
        if not isinstance(xy, (list, tuple)) or len(xy) < 2:
            continue
        existing = db.execute(
            "SELECT metadata_json FROM points WHERE curve_id=? AND x=? AND y=?",
            (int(curve_id), str(xy[0]), str(xy[1])),
        ).fetchone()
        try:
            metadata = json.loads(existing["metadata_json"] or "{}") if existing is not None else {}
        except Exception:
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        metadata["advanced_upper_bound"] = True
        metadata["advanced_upper_bound_backend"] = "simon_strong"
        rec = upsert_point(
            db,
            curve_id=int(curve_id),
            x=xy[0],
            y=xy[1],
            source="advanced_upper_bound:simon_known",
            role="candidate_extra",
            exact_verified=True,
            independence_status="unknown",
            rigorous_independent=False,
            search_ref="analysis:advanced-upper-bound",
            metadata=metadata,
        )
        if rec is not None:
            ids.append(int(rec["id"]))
    return ids


def _record_simon(db, row, args, result, *, basis_fingerprint):
    upper = result.get("rigorous_upper")
    return record_rank_evidence(
        db,
        curve_id=int(args.curve_id),
        model=json.loads(row["a_invariants_json"] or "[]"),
        data={
            "engine": ENGINE,
            "engine_version": ENGINE_VERSION,
            "evidence_type": "descent",
            "status": "completed",
            "rigorous": True,
            "rigorous_lower": None,
            "rigorous_upper": None if upper is None else int(upper),
            "exact_rank": None,
            "assumptions": [],
            "points_found": list(result.get("points") or []),
            "options": {
                "backend": "simon_strong",
                "basis_fingerprint": str(basis_fingerprint),
                "timeout": int(args.timeout),
                "lim1": int(args.lim1),
                "lim3": int(args.lim3),
                "limtriv": int(args.limtriv),
                "maxprob": int(args.maxprob),
                "limbigprime": int(args.limbigprime),
            },
            "elapsed_seconds": result.get("runtime_seconds"),
            "stdout_summary": json.dumps(result, sort_keys=True),
        },
    )


def _record_proof_rank(db, row, args, result):
    exact = int(result["exact_rank"])
    return record_rank_evidence(
        db,
        curve_id=int(args.curve_id),
        model=json.loads(row["a_invariants_json"] or "[]"),
        data={
            "engine": ENGINE,
            "engine_version": ENGINE_VERSION,
            "sage_version": result.get("sage_version"),
            "evidence_type": "exact_certificate",
            "status": "completed",
            "rigorous": True,
            "rigorous_lower": exact,
            "rigorous_upper": exact,
            "exact_rank": exact,
            "assumptions": [],
            "points_found": [],
            "options": {
                "backend": "sage_proof_rank",
                "timeout": int(args.timeout),
            },
            "elapsed_seconds": result.get("_runtime_seconds"),
            "stdout_summary": json.dumps(result, sort_keys=True),
        },
    )


def _record_failure(db, row, args, status, error, *, basis_fingerprint=None):
    return record_rank_evidence(
        db,
        curve_id=int(args.curve_id),
        model=json.loads(row["a_invariants_json"] or "[]"),
        data={
            "engine": ENGINE,
            "engine_version": ENGINE_VERSION,
            "evidence_type": "advanced_upper_bound_attempt",
            "status": str(status),
            "rigorous": False,
            "rigorous_lower": None,
            "rigorous_upper": None,
            "exact_rank": None,
            "assumptions": [],
            "points_found": [],
            "options": {
                "backend": str(args.backend),
                "basis_fingerprint": (
                    str(basis_fingerprint)
                    if str(args.backend) == "simon_strong" and basis_fingerprint
                    else None
                ),
                "timeout": int(args.timeout),
                "lim1": int(args.lim1),
                "lim3": int(args.lim3),
                "limtriv": int(args.limtriv),
                "maxprob": int(args.maxprob),
                "limbigprime": int(args.limbigprime),
            },
            "stderr_summary": str(error),
        },
    )


def main():
    args = parse_args()

    from sage.all import QQ, EllipticCurve

    db = connect(args.db)
    try:
        row = get_curve(db, int(args.curve_id))
        if row is None or not row["a_invariants_json"]:
            raise SystemExit("curve unavailable or missing model")
        E = EllipticCurve(
            QQ, [QQ(str(x)) for x in json.loads(row["a_invariants_json"])]
        )
        basis, required, complete = rigorous_witness_basis(
            db, int(args.curve_id), E
        )
        if not complete:
            raise SystemExit(
                f"rigorous basis incomplete: replayed {len(basis)}/{int(required)}"
            )

        basis_fingerprint = _basis_fingerprint(basis)
        print(
            f"[advanced upper] backend={args.backend} · curve #{int(args.curve_id)} · known basis={len(basis)}",
            flush=True,
        )
        prior = _prior_attempt(
            db,
            args,
            basis_fingerprint=basis_fingerprint,
        )
        if prior is not None:
            payload = {
                "curve_id": int(args.curve_id),
                "backend": str(args.backend),
                "status": "skipped",
                "prior_evidence_id": int(prior["id"]),
                "reason": (
                    "same backend already ran with equal-or-stronger research budget "
                    f"({str(prior['status'] or 'unknown')})"
                ),
            }
            print(
                f"[advanced upper] SKIP · {payload['reason']} · evidence #{int(prior['id'])}",
                flush=True,
            )
            print(MARKER + json.dumps(payload, sort_keys=True), flush=True)
            return
        try:
            if args.backend == "simon_strong":
                result = run_classical_descent(
                    E.a_invariants(),
                    basis,
                    mode="simon_known",
                    timeout=int(args.timeout),
                    lim1=int(args.lim1),
                    lim3=int(args.lim3),
                    limtriv=int(args.limtriv),
                    maxprob=int(args.maxprob),
                    limbigprime=int(args.limbigprime),
                    heartbeat_label=f"strong Simon upper curve #{int(args.curve_id)}",
                    heartbeat_seconds=30,
                )
                evidence_id = _record_simon(
                    db,
                    row,
                    args,
                    result,
                    basis_fingerprint=basis_fingerprint,
                )
                returned_ids = _store_returned_points(db, int(args.curve_id), result)
            else:
                result = run_rank_certification(
                    E.a_invariants(),
                    timeout=int(args.timeout),
                    heartbeat_label=f"advanced proof-rank curve #{int(args.curve_id)}",
                    heartbeat_seconds=30,
                )
                evidence_id = _record_proof_rank(db, row, args, result)
                returned_ids = []
        except (ClassicalDescentTimeout, RankCertificationTimeout) as exc:
            evidence_id = _record_failure(
                db,
                row,
                args,
                "timeout",
                exc,
                basis_fingerprint=basis_fingerprint,
            )
            payload = {
                "curve_id": int(args.curve_id),
                "backend": str(args.backend),
                "status": "timeout",
                "error": str(exc),
                "evidence_id": int(evidence_id),
            }
            print(MARKER + json.dumps(payload, sort_keys=True), flush=True)
            return
        except Exception as exc:
            evidence_id = _record_failure(
                db,
                row,
                args,
                "error",
                exc,
                basis_fingerprint=basis_fingerprint,
            )
            payload = {
                "curve_id": int(args.curve_id),
                "backend": str(args.backend),
                "status": "error",
                "error": str(exc),
                "evidence_id": int(evidence_id),
            }
            print(MARKER + json.dumps(payload, sort_keys=True), flush=True)
            return

        state = apply_reduced_rank_state(db, int(args.curve_id))
        if state.get("exact_rank") is not None:
            update_curve(
                db,
                int(args.curve_id),
                exact_rank=int(state["exact_rank"]),
                certain=1,
                status="exact",
                error=None,
            )
        closed = {}
        if state.get("exact_rank") is not None:
            for pid in dict.fromkeys(int(x) for x in args.point_id):
                closed[str(pid)] = _close_from_exact_ceiling(
                    db, int(args.curve_id), int(pid)
                )

        payload = {
            "curve_id": int(args.curve_id),
            "backend": str(args.backend),
            "status": "completed",
            "evidence_id": int(evidence_id),
            "rigorous_lower": state.get("rigorous_lower"),
            "rigorous_upper": state.get("rigorous_upper"),
            "exact_rank": state.get("exact_rank"),
            "returned_point_ids": returned_ids,
            "closed_points": closed,
        }
        print(
            f"[advanced upper] completed · upper={state.get('rigorous_upper')} · exact={state.get('exact_rank')}",
            flush=True,
        )
        print(MARKER + json.dumps(payload, sort_keys=True), flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
