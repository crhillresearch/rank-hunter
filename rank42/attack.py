import argparse
import json

from rank42.db import (
    connect,
    get_curve,
    update_curve,
    log_event,
    best_lower_bound,
)
from rank42.descent import run_descent, DescentTimeout, DescentFailure
from rank42.exact_lb import ExactCertificateFailure, ExactCertificateTimeout, run_exact_certificate
from rank42.point_promotion import promote_mwrank_strong_result
from rank42.rank_evidence import record_rank_evidence, reduce_rank_state
from rank42.saturation import persist_saturation_evidence, saturate_curve

def _authoritative_curve_rank_state(db, curve_id):
    state = reduce_rank_state(db, int(curve_id))
    return {
        "rigorous_lower": int(state["rigorous_lower"] or 0),
        "rigorous_upper": state["rigorous_upper"],
        "exact_rank": state["exact_rank"],
        "inconsistent": bool(state["inconsistent"]),
    }


def parse_args():
    ap = argparse.ArgumentParser(
        description="Run strong mwrank descent/search on one stored curve."
    )
    ap.add_argument("--id", type=int, required=True, help="curve id from rank42.db")
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--first-limit", type=int, default=40)
    ap.add_argument("--second-limit", type=int, default=20)
    ap.add_argument("--precision", type=int, default=512)
    ap.add_argument("--saturate-to", type=int, default=100)
    ap.add_argument("--certificate-timeout", type=int, default=120)
    return ap.parse_args()


def main():
    args = parse_args()
    db = connect(args.db)
    row = get_curve(db, args.id)
    if row is None:
        raise SystemExit(f"curve #{args.id} not found")

    if not row["a_invariants_json"]:
        raise SystemExit(f"curve #{args.id} has no stored a-invariants")

    ainvs = json.loads(row["a_invariants_json"])
    incumbent_before = best_lower_bound(db)

    print("RANK HUNTER ATTACK")
    print("=" * 62)
    print("curve id             =", row["id"])
    print("parameter            =", row["parameter"])
    print("score                =", row["score"])
    print("generic lower        =", row["generic_lower"])
    print("quick upper          =", row["quick_upper"])
    print("current incumbent    =", incumbent_before)
    print("a-invariants         =", ainvs)
    print()
    print(f"Running strong descent/search with hard timeout {args.timeout}s...")

    update_curve(db, row["id"], status="strong_running", error=None)
    log_event(db, row["id"], "info", "manual strong attack started")

    try:
        result = run_descent(
            {
                "mode": "strong",
                "a_invariants": ainvs,
                "first_limit": args.first_limit,
                "second_limit": args.second_limit,
                "second_descent": True,
                "precision": args.precision,
                "saturate_to": args.saturate_to,
            },
            timeout=args.timeout,
        )
    except DescentTimeout as exc:
        update_curve(db, row["id"], status="strong_timeout", error=str(exc))
        record_rank_evidence(db, curve_id=int(row["id"]), model=ainvs, data={
            "engine": "mwrank", "evidence_type": "descent", "status": "timeout",
            "rigorous": True, "rigorous_lower": None, "rigorous_upper": None, "exact_rank": None,
            "assumptions": [], "points_found": [], "timed_out": True,
            "options": {"timeout": int(args.timeout), "source": "manual_attack"},
            "minimal_model_a_invariants": ainvs,
        })
        log_event(db, row["id"], "warn", "manual strong attack timed out")
        print("RESULT: TIMEOUT")
        return
    except DescentFailure as exc:
        msg = f"{exc.failure_class}: {exc}"
        inconclusive = exc.failure_class in {"MWRANK_SIZE_LIMIT"}
        update_curve(
            db,
            row["id"],
            status="strong_inconclusive" if inconclusive else "error",
            error=msg,
        )
        record_rank_evidence(db, curve_id=int(row["id"]), model=ainvs, data={
            "engine": "mwrank", "evidence_type": "descent", "status": "inconclusive" if inconclusive else "error",
            "rigorous": True, "rigorous_lower": None, "rigorous_upper": None, "exact_rank": None,
            "assumptions": [], "points_found": [],
            "options": {
                "timeout": int(args.timeout),
                "source": "manual_attack",
                "failure_class": exc.failure_class,
            },
            "minimal_model_a_invariants": ainvs, "stderr_summary": str(exc),
        })
        log_event(
            db,
            row["id"],
            "warn" if inconclusive else "error",
            (
                "manual strong attack unsupported for this coefficient size"
                if inconclusive
                else f"manual strong attack failed [{exc.failure_class}]"
            ),
        )
        print(
            f"RESULT: INCONCLUSIVE [{exc.failure_class}]"
            if inconclusive
            else f"RESULT: ERROR [{exc.failure_class}]"
        )
        print(exc)
        if exc.failure_class == "MWRANK_SIZE_LIMIT":
            print()
            print("Large-curve fallback: attempting standalone saturation of the rigorous point basis...")
            sat_fallback = saturate_curve(
                db,
                int(row["id"]),
                max_prime=int(args.saturate_to),
                timeout=int(args.timeout),
                source="mwrank_size_limit_fallback",
            )
            print(
                "standalone saturation  =",
                sat_fallback.get("status"),
                "index",
                sat_fallback.get("index"),
            )
        return

    raw_lower = int(result["lower"])
    upper = int(result["upper"])
    certain = bool(result["certain"])
    points = result.get("points", [])
    screen = result.get("height_screen") or {}
    sat = result.get("saturation") or {}

    mwrank_cert_lower = raw_lower if (certain and raw_lower == upper) else None
    point_certificate = None
    if mwrank_cert_lower is None and points:
        try:
            point_certificate = run_exact_certificate(ainvs, points, timeout=int(args.certificate_timeout))
        except (ExactCertificateTimeout, ExactCertificateFailure) as exc:
            log_event(db, row["id"], "warn", f"manual mwrank generator exact certificate inconclusive: {exc}")
        else:
            if point_certificate.get("independent"):
                mwrank_cert_lower = int(point_certificate.get("rank_lower_bound") or len(points))

    baseline_state = _authoritative_curve_rank_state(db, row["id"])
    baseline_lower = int(baseline_state["rigorous_lower"])
    promoted = promote_mwrank_strong_result(
        db,
        curve_id=int(row["id"]),
        model=ainvs,
        result=result,
        rigorous_lower=mwrank_cert_lower,
        point_certificate=point_certificate,
        source="manual_attack",
        search_ref="analysis:manual-attack:mwrank",
        options={
            "timeout": int(args.timeout),
            "first_limit": int(args.first_limit),
            "second_limit": int(args.second_limit),
            "saturate_to": int(args.saturate_to),
        },
        metadata={
            "raw_lower": raw_lower,
            "certain": certain,
            "certificate_timeout": int(args.certificate_timeout),
        },
    )
    if sat and sat.get("attempted"):
        persist_saturation_evidence(
            db,
            curve_id=int(row["id"]),
            model=ainvs,
            result={
                **sat,
                "engine_version": result.get("engine_version"),
                "sage_version": result.get("sage_version"),
                "minimal_model_a_invariants": result.get("a_invariants") or ainvs,
            },
            source="manual_attack",
        )

    verified_lower = int(promoted["rigorous_lower"])
    exact_rank = promoted.get("exact_rank")
    fields = {
        "regulator": str(result.get("regulator")) if result.get("regulator") is not None else None,
        "error": None,
    }
    if exact_rank is None:
        fields["status"] = "strong_done"
    update_curve(db, row["id"], **fields)

    if exact_rank is not None:
        log_event(
            db,
            row["id"],
            "best" if exact_rank > incumbent_before else "info",
            f"manual attack exact rank {exact_rank}",
        )
    else:
        log_event(
            db,
            row["id"],
            "best" if verified_lower > incumbent_before else "info",
            (
                f"manual attack descent [{verified_lower},{upper}] "
                f"(prior rigorous lower ≥{baseline_lower})"
            ),
        )

    print()
    print("================ RESULTS ================")
    print("prior rigorous lower  >=", baseline_lower)
    print("raw mwrank lower      >=", raw_lower)
    print("verified point lower  >=", verified_lower)
    print("2-descent upper       <=", upper)
    print("certain                =", certain)
    print("generators returned    =", len(points))
    print("height positive        =", screen.get("positive_definite_screen"))
    print("height determinant     =", screen.get("determinant"))
    print("min eigenvalue         =", screen.get("min_eigenvalue"))
    if sat:
        print("saturation index       =", sat.get("index"))
        print("saturation regulator   =", sat.get("regulator"))
    if exact_rank is not None:
        print()
        print("*** EXACT RANK =", exact_rank, "***")

    incumbent_after = best_lower_bound(db)
    if incumbent_after > incumbent_before:
        print()
        print("*" * 62)
        print(f"NEW BEST PROVEN LOWER BOUND: rank >= {incumbent_after}")
        print("*" * 62)

if __name__ == "__main__":
    main()
