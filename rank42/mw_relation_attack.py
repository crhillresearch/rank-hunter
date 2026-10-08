"""Global exact Mordell-Weil relation attack for hard point cases.

The attack is family-agnostic. Numerical canonical-height linear algebra only
proposes rational coefficients; a point is closed as dependent only after an
exact group-law identity is verified modulo exact rational torsion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from rank42.db import connect, get_curve, proven_lower
from rank42.points import rigorous_witness_basis, set_point_hard_flag, upsert_point
from rank42.rank_evidence import record_rank_evidence

ENGINE = "rank42.mw_relation_attack"
ENGINE_VERSION = "1"
MARKER = "RANK42_MW_RELATION_RESULT="
WORKER_MARKER = "RANK42_MW_RELATION_WORKER="
WORKER = Path(__file__).with_name("mw_relation_worker.py")


def parse_args():
    ap = argparse.ArgumentParser(description="Exact MW relation attack")
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--curve-id", type=int, required=True)
    ap.add_argument("--point-id", type=int, action="append", default=[])
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--precision-bits", type=int, default=256)
    ap.add_argument("--max-denominator", type=int, default=64)
    return ap.parse_args()


def _point_key(P):
    return (str(P[0]), str(P[1]))


def _basis_fingerprint(points):
    raw = json.dumps([_point_key(P) for P in points], separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _metadata(row):
    try:
        value = json.loads(row["metadata_json"] or "{}")
    except Exception:
        value = {}
    return value if isinstance(value, dict) else {}


def _heartbeat(label, stop, started):
    while not stop.wait(30):
        elapsed = int(time.monotonic() - started)
        print(f"[heartbeat] {label} still running · {elapsed}s elapsed", flush=True)


def _run_worker(payload, *, timeout, label):
    started = time.monotonic()
    stop = threading.Event()
    thread = threading.Thread(
        target=_heartbeat, args=(label, stop, started), daemon=True
    )
    thread.start()
    try:
        cp = subprocess.run(
            [sys.executable, str(WORKER)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=max(1, int(timeout)),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "timeout",
            "error": f"relation worker exceeded {int(timeout)}s",
            "elapsed_seconds": time.monotonic() - started,
        }
    finally:
        stop.set()
        thread.join(timeout=0.2)

    marker = None
    for line in reversed((cp.stdout or "").splitlines()):
        if line.startswith(WORKER_MARKER):
            marker = line[len(WORKER_MARKER):]
            break
    tail = "\n".join(
        ((cp.stdout or "") + "\n" + (cp.stderr or "")).splitlines()[-40:]
    )
    if cp.returncode != 0 or marker is None:
        return {
            "status": "error",
            "error": f"relation worker failed exit={cp.returncode}; tail:\n{tail}",
            "elapsed_seconds": time.monotonic() - started,
        }
    try:
        result = json.loads(marker)
    except Exception as exc:
        return {
            "status": "error",
            "error": f"invalid worker result: {exc!r}",
            "elapsed_seconds": time.monotonic() - started,
        }
    result["elapsed_seconds"] = time.monotonic() - started
    return result


def _prior_attempt(db, *, curve_id, point_id, basis_fingerprint, timeout, precision_bits, max_denominator):
    rows = db.execute(
        """
        SELECT * FROM rank_evidence
        WHERE curve_id=? AND engine=? AND evidence_type='point_relation'
        ORDER BY id DESC LIMIT 500
        """,
        (int(curve_id), ENGINE),
    ).fetchall()
    for rec in rows:
        try:
            options = json.loads(rec["options_json"] or "{}")
        except Exception:
            options = {}
        if int(options.get("point_id") or -1) != int(point_id):
            continue
        if str(options.get("basis_fingerprint") or "") != str(basis_fingerprint):
            continue
        if int(options.get("timeout") or 0) < int(timeout):
            continue
        if int(options.get("precision_bits") or 0) < int(precision_bits):
            continue
        if int(options.get("max_denominator") or 0) < int(max_denominator):
            continue
        if str(rec["status"] or "") in {"completed", "inconclusive", "timeout", "error"}:
            return rec
    return None


def _record(db, *, curve_id, model, point_id, basis_fingerprint, args, result):
    status = str(result.get("status") or "error")
    rigorous = status == "dependent" and bool(result.get("verified"))
    evidence_status = "completed" if rigorous else status
    return record_rank_evidence(
        db,
        curve_id=int(curve_id),
        model=model,
        data={
            "engine": ENGINE,
            "engine_version": ENGINE_VERSION,
            "evidence_type": "point_relation",
            "status": evidence_status,
            "rigorous": rigorous,
            "rigorous_lower": None,
            "rigorous_upper": None,
            "exact_rank": None,
            "assumptions": [],
            "points_found": [],
            "options": {
                "point_id": int(point_id),
                "basis_fingerprint": str(basis_fingerprint),
                "timeout": int(args.timeout),
                "precision_bits": int(args.precision_bits),
                "max_denominator": int(args.max_denominator),
            },
            "stdout_summary": json.dumps(result, sort_keys=True),
            "stderr_summary": result.get("error"),
            "elapsed_seconds": result.get("elapsed_seconds"),
        },
    )



def _relation_entry(result, evidence_id):
    return {
        "engine": ENGINE,
        "evidence_id": int(evidence_id),
        "status": str(result.get("status") or "error"),
        "verified": bool(result.get("verified")),
        "coefficients": result.get("coefficients"),
        "integer_coefficients": result.get("integer_coefficients"),
        "common_denominator": result.get("common_denominator"),
        "torsion_exponent": result.get("torsion_exponent"),
        "torsion_residual": result.get("torsion_residual"),
        "numerical_residual": result.get("numerical_residual"),
        "error": result.get("error"),
    }


def _persist_attempt_metadata(db, rec, result, evidence_id):
    meta = _metadata(rec)
    entry = _relation_entry(result, evidence_id)
    history = meta.get("mw_relation_history")
    if not isinstance(history, list):
        history = []
    history.append(entry)
    meta["mw_relation_history"] = history[-20:]
    meta["last_mw_relation"] = entry
    return upsert_point(
        db,
        curve_id=int(rec["curve_id"]),
        x=rec["x"],
        y=rec["y"],
        source=rec["source"],
        role=rec["role"],
        exact_verified=True,
        independence_status=rec["independence_status"],
        rigorous_independent=bool(rec["rigorous_independent"]),
        search_ref=rec["search_ref"] or "analysis:mw-relation",
        plugin_id=rec["plugin_id"],
        metadata=meta,
    )


def _close_dependent(db, rec, result, evidence_id):
    meta = _metadata(rec)
    entry = _relation_entry(result, evidence_id)
    entry["status"] = "dependent"
    entry["verified"] = True
    history = meta.get("mw_relation_history")
    if not isinstance(history, list):
        history = []
    history.append(entry)
    meta["mw_relation_history"] = history[-20:]
    meta["last_mw_relation"] = entry
    updated = upsert_point(
        db,
        curve_id=int(rec["curve_id"]),
        x=rec["x"],
        y=rec["y"],
        source=rec["source"],
        role=rec["role"],
        exact_verified=True,
        independence_status="dependent",
        rigorous_independent=False,
        search_ref=rec["search_ref"] or "analysis:mw-relation",
        plugin_id=rec["plugin_id"],
        metadata=meta,
    )
    if updated is not None:
        set_point_hard_flag(db, int(updated["id"]), False)


def main():
    args = parse_args()
    if not args.point_id:
        raise SystemExit("at least one --point-id is required")

    from sage.all import QQ, EllipticCurve

    db = connect(args.db)
    try:
        row = get_curve(db, int(args.curve_id))
        if row is None or not row["a_invariants_json"]:
            raise SystemExit("curve unavailable or missing model")
        E = EllipticCurve(QQ, [QQ(str(x)) for x in json.loads(row["a_invariants_json"])])
        basis, required, complete = rigorous_witness_basis(db, int(args.curve_id), E)
        if not complete:
            raise SystemExit(
                f"rigorous basis incomplete: replayed {len(basis)}/{int(required)}"
            )
        fingerprint = _basis_fingerprint(basis)
        model = [str(x) for x in E.a_invariants()]
        results = []

        for pid in dict.fromkeys(int(x) for x in args.point_id):
            rec = db.execute(
                "SELECT * FROM points WHERE id=? AND curve_id=?",
                (pid, int(args.curve_id)),
            ).fetchone()
            if rec is None:
                raise SystemExit(f"point #{pid} does not belong to curve #{int(args.curve_id)}")
            if not int(rec["exact_verified"] or 0):
                raise SystemExit(f"point #{pid} is not exact-verified")
            if str(rec["independence_status"] or "") == "dependent":
                results.append({"point_id": pid, "status": "dependent", "already_closed": True})
                continue

            prior = _prior_attempt(
                db,
                curve_id=args.curve_id,
                point_id=pid,
                basis_fingerprint=fingerprint,
                timeout=args.timeout,
                precision_bits=args.precision_bits,
                max_denominator=args.max_denominator,
            )
            if prior is not None:
                print(
                    f"[relation] SKIP point #{pid} · same basis already tested with equal-or-stronger search · evidence #{int(prior['id'])}",
                    flush=True,
                )
                results.append({
                    "point_id": pid,
                    "status": "skipped",
                    "prior_evidence_id": int(prior["id"]),
                })
                continue

            print(
                f"[relation] point #{pid} · basis={len(basis)} · max denominator={int(args.max_denominator)} · precision={int(args.precision_bits)} bits",
                flush=True,
            )
            payload = {
                "a_invariants": model,
                "basis": [[str(P[0]), str(P[1])] for P in basis],
                "candidate": [str(rec["x"]), str(rec["y"])],
                "precision_bits": int(args.precision_bits),
                "max_denominator": int(args.max_denominator),
            }
            result = _run_worker(
                payload,
                timeout=int(args.timeout),
                label=f"MW relation point #{pid}",
            )
            evidence_id = _record(
                db,
                curve_id=args.curve_id,
                model=model,
                point_id=pid,
                basis_fingerprint=fingerprint,
                args=args,
                result=result,
            )
            if str(result.get("status")) == "dependent" and bool(result.get("verified")):
                _close_dependent(db, rec, result, evidence_id)
                print(
                    f"[relation] EXACT DEPENDENCE · point #{pid} · denominator={result.get('common_denominator')} · evidence #{evidence_id}",
                    flush=True,
                )
            else:
                _persist_attempt_metadata(db, rec, result, evidence_id)
                print(
                    f"[relation] {str(result.get('status') or 'error').upper()} · point #{pid} · evidence #{evidence_id}",
                    flush=True,
                )
            results.append({
                "point_id": pid,
                "status": result.get("status"),
                "evidence_id": int(evidence_id),
                "verified": bool(result.get("verified")),
                "coefficients": result.get("coefficients"),
                "common_denominator": result.get("common_denominator"),
                "error": result.get("error"),
            })

        payload = {
            "curve_id": int(args.curve_id),
            "basis_size": len(basis),
            "rigorous_lower": int(proven_lower(row)),
            "basis_fingerprint": fingerprint,
            "results": results,
            "dependent_point_ids": [
                int(r["point_id"]) for r in results if r.get("status") == "dependent"
            ],
        }
        print(MARKER + json.dumps(payload, sort_keys=True), flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
