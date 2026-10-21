import argparse
import json
from pathlib import Path

from sage.all import QQ

from rank42.curve_arithmetic_state import compute_and_publish_curve_arithmetic
from rank42.db import (
    connect,
    upsert_curve,
    update_curve,
    log_event,
    best_lower_bound,
    get_curve_by_key,
)
from rank42.descent import run_descent, DescentTimeout, DescentFailure
from rank42.screen import run_fast_screen, ScreenTimeout, ScreenFailure
from rank42.family_loader import load_family, resolve_family_spec
from rank42.exact_lb import ExactCertificateFailure, ExactCertificateTimeout, run_exact_certificate
from rank42.point_promotion import promote_mwrank_strong_result
from rank42.points import upsert_point
from rank42.model_points import map_points_exact
from rank42.rank_evidence import record_rank_evidence, apply_reduced_rank_state
from rank42.saturation import saturate_curve

TERMINAL_STATUSES = {"pruned", "exact", "strong_done"}

def parse_args():
    ap = argparse.ArgumentParser(
        description="Automatically screen candidate specializations with 2-descent."
    )
    ap.add_argument("--input", required=True, help="candidates.jsonl")
    ap.add_argument("--family", default=None, help="family alias/module, or json:/path/to/family.json")
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--quick-timeout", type=int, default=300)
    ap.add_argument(
        "--quick-strategy", choices=["mwrank", "pari"], default="pari",
        help="primary quick rank-bound engine (PARI by default); the other engine is used only after a process failure",
    )
    ap.add_argument("--strong-timeout", type=int, default=900)
    ap.add_argument("--first-limit", type=int, default=40)
    ap.add_argument("--second-limit", type=int, default=20)
    ap.add_argument("--precision", type=int, default=512)
    ap.add_argument("--saturate-to", type=int, default=100)
    ap.add_argument("--force", action="store_true")
    ap.add_argument(
        "--fast-screen", action="store_true",
        help=("defer generic-section height screening, conductor/root-number computation, "
              "and bad-prime factorization until a curve survives the quick upper-bound gate"),
    )
    ap.add_argument(
        "--quick-only",
        action="store_true",
        help="run only quick rank-bound screening; survivors are marked promising",
    )
    ap.add_argument(
        "--strong-on-timeout",
        action="store_true",
        help="try strong descent immediately after a quick timeout",
    )
    ap.add_argument(
        "--no-generic-witness", action="store_true",
        help="do not use the family's known generic sections; search each specialization as a free curve",
    )
    ap.add_argument(
        "--generic-certificate-timeout", type=int, default=120,
        help="hard timeout for exact independence certification of specialized generic sections",
    )
    ap.add_argument(
        "--strong-certificate-timeout", type=int, default=120,
        help="hard timeout for exact independence certification of generators returned by strong mwrank",
    )
    ap.add_argument("--legacy-resume", action="store_true", help=argparse.SUPPRESS)
    return ap.parse_args()

def load_candidates(path):
    rows = []
    with Path(path).open() as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows


def run_quick_with_error_fallback(ainvs, timeout, strategy="pari"):
    """Run one quick engine, using the other only after a process failure.

    Timeouts are not retried unless --strong-on-timeout is explicitly requested.
    """
    primary = "quick_pari" if strategy == "pari" else "quick"
    fallback_mode = "quick" if strategy == "pari" else "quick_pari"
    try:
        return run_descent(
            {"mode": primary, "a_invariants": ainvs},
            timeout=timeout,
        )
    except DescentFailure as first:
        fallback = run_descent(
            {"mode": fallback_mode, "a_invariants": ainvs},
            timeout=timeout,
        )
        fallback["_fallback_from"] = first.failure_class
        return fallback


def store_quick_rank_evidence(db, curve_id, ainvs, result, *, timeout, status="completed"):
    strategy = str((result or {}).get("strategy") or "")
    engine = "pari" if "pari" in strategy else "mwrank"
    upper = (result or {}).get("upper") if status == "completed" else None
    data = {
        "engine": engine, "engine_version": (result or {}).get("engine_version"),
        "sage_version": (result or {}).get("sage_version"), "evidence_type": "rank_bounds",
        "status": status, "rigorous": True, "rigorous_lower": None,
        "rigorous_upper": None if upper is None else int(upper), "exact_rank": None,
        "conditional_analytic_upper": None, "numerical_rank_signal": None,
        "assumptions": [], "points_found": [], "timed_out": status == "timeout",
        "options": {"timeout": int(timeout), "source": "auto_analyze_quick"},
        "minimal_model_a_invariants": [str(x) for x in ainvs] if ainvs and status == "completed" else None,
    }
    record_rank_evidence(db, curve_id=int(curve_id), model=ainvs, data=data)
    return apply_reduced_rank_state(db, int(curve_id))


def enrich_candidate(
    db, curve_id, family, t, E, Em, *, use_generic=True, certificate_timeout=120
):
    """Persist exact metadata and, optionally, rigorously certify known sections.

    Exact on-curve specialization is recorded in the point ledger. A family
    section set changes ``generic_lower`` only after the exact quadratic-
    character certificate succeeds; numerical height screens are not proof.
    """
    arithmetic = compute_and_publish_curve_arithmetic(
        db,
        curve_id,
        Em,
        source="auto_analyze",
        method="sage_global_minimal_model_invariants",
    )
    if arithmetic["publication"]["conflicts"]:
        log_event(
            db,
            curve_id,
            "warn",
            (
                "Auto Analyze arithmetic materialization conflicts with existing "
                "local/reference arithmetic state: "
                + ", ".join(arithmetic["publication"]["conflicts"])
            ),
        )
    fields = {}
    generic_lower = None
    if use_generic:
        generic_source = family.generic_section_points(t)
        try:
            generic = map_points_exact(E, Em, generic_source) if generic_source else []
        except Exception as exc:
            generic = []
            log_event(db, curve_id, "warn", f"generic-section minimal-model mapping failed: {exc}")
        for idx, P in enumerate(generic):
            if P.is_zero():
                continue
            upsert_point(
                db, curve_id=curve_id, x=P[0], y=P[1],
                source="family_generic_section", role="generic_section",
                exact_verified=True, independence_status="unknown",
                metadata={"section_index": idx, "family": family.name(), "stored_model": "global_minimal_model"},
            )
        generic = [P for P in generic if not P.is_zero()]
        if generic:
            try:
                cert = run_exact_certificate(
                    Em.a_invariants(),
                    [[str(P[0]), str(P[1])] for P in generic],
                    timeout=int(certificate_timeout),
                )
            except (ExactCertificateTimeout, ExactCertificateFailure) as exc:
                log_event(db, curve_id, "warn", f"generic-section exact certificate inconclusive: {exc}")
            else:
                if cert.get("independent"):
                    generic_lower = int(cert.get("rank_lower_bound") or len(generic))
                    fields["generic_lower"] = generic_lower
                    for idx, P in enumerate(generic):
                        upsert_point(
                            db, curve_id=curve_id, x=P[0], y=P[1],
                            source="family_generic_section", role="generic_section",
                            exact_verified=True, independence_status="rigorous_independent",
                            rigorous_independent=True, search_ref="family:exact-baseline",
                            metadata={"section_index": idx, "family": family.name(), "certificate": cert.get("certificate"), "stored_model": "global_minimal_model"},
                        )
                    log_event(db, curve_id, "info", f"exact specialized family-section lower bound {generic_lower}")
                elif cert.get("status") == "dependent":
                    log_event(db, curve_id, "warn", "specialized family sections are exactly dependent")
                else:
                    log_event(db, curve_id, "warn", "generic-section exact certificate inconclusive")
    update_curve(db, curve_id, **fields)
    return generic_lower


def main():
    args = parse_args()
    if not args.legacy_resume:
        raise SystemExit(
            "standalone FREE Family Search is retired; start new work from "
            "Search, or resume an existing native job from Jobs"
        )
    db = connect(args.db)
    args.family = resolve_family_spec(args.family)
    family = load_family(args.family, need_sections=True)
    all_rows = load_candidates(args.input)
    declared = {r.get("family") for r in all_rows if r.get("family")}
    if declared and declared != {family.name()}:
        raise SystemExit(
            f"candidate file family {sorted(declared)!r} does not match loaded family {family.name()!r}"
        )
    rows = all_rows[:args.limit]

    print(f"[db] {args.db}")
    print(f"[family] {family.name()}")
    print(f"[incumbent] proven rank >= {best_lower_bound(db)}")
    print(f"[queue] {len(rows)} candidates")

    for i, row in enumerate(rows, 1):
        if i > 1:
            print(f"[candidate done] {i-1}/{len(rows)}", flush=True)
        t = QQ(row["a"]) / QQ(row["b"])
        param = str(t)
        existing = get_curve_by_key(db, family.name(), param)
        incumbent_now = best_lower_bound(db)

        if existing is not None and not args.force:
            if existing["status"] in TERMINAL_STATUSES:
                print(f"[{i}/{len(rows)}] t={param} already {existing['status']}; skip")
                continue
            if existing["status"] in {"quick_timeout", "strong_timeout", "error"}:
                print(f"[{i}/{len(rows)}] t={param} already {existing['status']}; use --force to retry")
                continue
            if (
                args.quick_only
                and existing["status"] == "promising"
                and existing["quick_upper"] is not None
                and int(existing["quick_upper"]) > incumbent_now
            ):
                print(
                    f"[{i}/{len(rows)}] t={param} already promising "
                    f"(upper {existing['quick_upper']} > {incumbent_now}); skip"
                )
                continue

        print(f"\n[{i}/{len(rows)}] t={param} score={row['score']:.6f}")
        curve_id = None

        try:
            # In fast-screen mode, put *specialization construction plus the
            # rank bound* behind one subprocess timeout.  This is essential for
            # large families such as Kihara's, where building/minimalizing the
            # rational model can cost more than the descent itself.
            if args.fast_screen:
                curve_id = upsert_curve(
                    db,
                    family=family.name(),
                    parameter=param,
                    score=float(row["score"]),
                )
                update_curve(db, curve_id, status="quick_running", error=None)
                print(
                    f"    bounded fast screen ({args.quick_strategy}, total timeout "
                    f"{args.quick_timeout}s)..."
                )
                try:
                    screen = run_fast_screen(
                        family=args.family,
                        parameter=param,
                        strategy=args.quick_strategy,
                        timeout=args.quick_timeout,
                    )
                except ScreenTimeout as exc:
                    fields = {"status": "quick_timeout", "error": str(exc)}
                    if exc.a_invariants:
                        fields["a_invariants_json"] = json.dumps(exc.a_invariants)
                    update_curve(db, curve_id, **fields)
                    if exc.a_invariants:
                        store_quick_rank_evidence(db, curve_id, exc.a_invariants, {"strategy": "sage_rank_bound_" + args.quick_strategy}, timeout=args.quick_timeout, status="timeout")
                    log_event(db, curve_id, "warn", "bounded fast screen timed out")
                    print("    fast screen TIMEOUT")
                    if not exc.a_invariants:
                        print("    timeout occurred before a curve model was emitted")
                    continue
                except ScreenFailure as exc:
                    # A quick rank-bound backend failure is computationally
                    # inconclusive, not a mathematical failure of the curve.
                    # Try the alternate Sage backend once before giving up.
                    fallback_strategy = "pari" if args.quick_strategy == "mwrank" else "mwrank"
                    log_event(
                        db, curve_id, "warn",
                        f"bounded fast screen {args.quick_strategy} failed; trying {fallback_strategy}",
                    )
                    print(
                        f"    fast screen {args.quick_strategy} FAILED; "
                        f"trying {fallback_strategy} fallback..."
                    )
                    try:
                        screen = run_fast_screen(
                            family=args.family,
                            parameter=param,
                            strategy=fallback_strategy,
                            timeout=args.quick_timeout,
                        )
                    except (ScreenFailure, ScreenTimeout) as fallback_exc:
                        ainvs = (
                            getattr(fallback_exc, "a_invariants", None)
                            or getattr(exc, "a_invariants", None)
                        )
                        fields = {
                            "status": "quick_inconclusive",
                            "error": (
                                f"{args.quick_strategy} screen failed: {exc}; "
                                f"{fallback_strategy} fallback inconclusive: {fallback_exc}"
                            ),
                        }
                        if ainvs:
                            fields["a_invariants_json"] = json.dumps(ainvs)
                        update_curve(db, curve_id, **fields)
                        log_event(
                            db, curve_id, "warn",
                            "quick rank-bound screen inconclusive on both backends",
                        )
                        print("    fast screen INCONCLUSIVE on both backends; rank unknown")
                        continue
                    else:
                        print(f"    fallback {fallback_strategy} screen completed")

                upper = int(screen["upper"])
                strategy = screen.get("strategy", args.quick_strategy)
                screen_ainvs = screen.get("a_invariants") or []
                update_curve(
                    db, curve_id,
                    a_invariants_json=json.dumps(screen_ainvs) if screen_ainvs else None,
                    quick_upper=upper,
                    status="quick_done",
                    error=None,
                )
                if screen_ainvs:
                    state = store_quick_rank_evidence(db, curve_id, screen_ainvs, screen, timeout=args.quick_timeout)
                    if state.get("exact_rank") is not None:
                        print(f"    exact rank = {state['exact_rank']} from rigorous lower/upper equality")
                log_event(db, curve_id, "info", f"quick upper bound {upper} via {strategy}")
                if screen_ainvs and state.get("exact_rank") is not None:
                    update_curve(db, curve_id, status="exact", error=None)
                    log_event(db, curve_id, "best", f"PARI-first quick bounds certify exact rank {state['exact_rank']}")
                    continue
                print(
                    f"    quick upper <= {upper} via {strategy} "
                    f"({screen.get('_runtime_seconds', 0):.2f}s total)"
                )
                incumbent = best_lower_bound(db)
                if upper <= incumbent:
                    update_curve(db, curve_id, status="pruned")
                    log_event(
                        db, curve_id,
                        "prune",
                        f"upper bound {upper} cannot beat incumbent {incumbent}",
                    )
                    print(f"    PRUNE: {upper} <= incumbent {incumbent}")
                    continue

                # A quick-only scout should stop here. We deliberately avoid
                # constructing the expensive full family model until the
                # specialization can still beat the current incumbent.
                if args.quick_only:
                    update_curve(db, curve_id, status="promising")
                    log_event(
                        db, curve_id, "prom",
                        f"bounded fast-screen upper {upper} exceeds incumbent {incumbent}",
                    )
                    print(f"    PROMISING: upper {upper} > incumbent {incumbent}")
                    continue

                print("    survivor: constructing full family model...")

            E = family.curve(t)
            if E is None:
                print("    singular specialization")
                continue
            Em = E.global_minimal_model()

            ainvs = [str(x) for x in Em.a_invariants()]

            curve_id = upsert_curve(
                db,
                family=family.name(),
                parameter=param,
                score=float(row["score"]),
            )
            current = get_curve_by_key(db, family.name(), param)
            resume_strong = (
                current is not None
                and not args.quick_only
                and current["quick_upper"] is not None
                and int(current["quick_upper"]) > best_lower_bound(db)
                and current["status"] in {"promising", "quick_done", "error"}
            )

            update_curve(
                db,
                curve_id,
                a_invariants_json=json.dumps(ainvs),
                error=None if not resume_strong else current["error"],
            )

            generic_lower = None
            if current is not None and current["generic_lower"] is not None:
                generic_lower = int(current["generic_lower"])
            if not args.fast_screen and generic_lower is None:
                generic_lower = enrich_candidate(db, curve_id, family, t, E, Em, use_generic=not args.no_generic_witness, certificate_timeout=args.generic_certificate_timeout)

            incumbent = best_lower_bound(db)
            if generic_lower is None:
                print("    family-section witness  deferred (--fast-screen)" if not args.no_generic_witness else "    family witness           disabled (FREE)")
            else:
                print(f"    exact family-section lower >= {generic_lower}" if generic_lower is not None else ("    family witness disabled (FREE)" if args.no_generic_witness else "    family-section exact certificate unavailable"))
            print(f"    current incumbent     >= {incumbent}")

            if resume_strong:
                upper = int(current["quick_upper"])
                print(f"    reuse stored quick upper <= {upper}")
            else:
                update_curve(db, curve_id, status="quick_running", error=None)
                print(f"    quick 2-descent upper bound (timeout {args.quick_timeout}s)...")

                try:
                    quick = run_quick_with_error_fallback(ainvs, args.quick_timeout, args.quick_strategy)
                    upper = int(quick["upper"])
                    strategy = quick.get("strategy", "unknown")
                    update_curve(db, curve_id, quick_upper=upper, status="quick_done", error=None)
                    state = store_quick_rank_evidence(db, curve_id, ainvs, quick, timeout=args.quick_timeout)
                    if state.get("exact_rank") is not None:
                        print(f"    exact rank = {state['exact_rank']} from rigorous lower/upper equality")
                    log_event(
                        db,
                        curve_id,
                        "info",
                        f"quick upper bound {upper} via {strategy}",
                    )
                    print(f"    quick upper <= {upper} via {strategy}")
                    if state.get("exact_rank") is not None:
                        update_curve(db, curve_id, status="exact", error=None)
                        log_event(db, curve_id, "best", f"PARI-first quick bounds certify exact rank {state['exact_rank']}")
                        continue

                    incumbent = best_lower_bound(db)
                    if upper <= incumbent:
                        update_curve(db, curve_id, status="pruned")
                        log_event(
                            db,
                            curve_id,
                            "prune",
                            f"upper bound {upper} cannot beat incumbent {incumbent}",
                        )
                        print(f"    PRUNE: {upper} <= incumbent {incumbent}")
                        continue

                    if args.fast_screen and generic_lower is None:
                        print("    survivor: computing deferred generic witness/metadata...")
                        generic_lower = enrich_candidate(db, curve_id, family, t, E, Em, use_generic=not args.no_generic_witness, certificate_timeout=args.generic_certificate_timeout)
                        print(f"    exact family-section lower >= {generic_lower}" if generic_lower is not None else ("    family witness disabled (FREE)" if args.no_generic_witness else "    family-section exact certificate unavailable"))

                    if args.quick_only:
                        update_curve(db, curve_id, status="promising")
                        log_event(
                            db,
                            curve_id,
                            "prom",
                            f"upper bound {upper} exceeds incumbent {incumbent}",
                        )
                        print(f"    PROMISING: upper {upper} > incumbent {incumbent}")
                        continue

                except DescentTimeout as exc:
                    update_curve(db, curve_id, status="quick_timeout", error=str(exc))
                    store_quick_rank_evidence(db, curve_id, ainvs, {"strategy": "sage_rank_bound_" + args.quick_strategy}, timeout=args.quick_timeout, status="timeout")
                    log_event(db, curve_id, "warn", "quick descent timed out")
                    print("    quick descent TIMEOUT")
                    if not args.strong_on_timeout:
                        continue
                    print("    --strong-on-timeout set; attempting strong path now")

                except DescentFailure as exc:
                    # This means both the default implementation and its local
                    # PARI fallback failed.
                    update_curve(
                        db,
                        curve_id,
                        status="error",
                        error=f"{exc.failure_class}: {exc}",
                    )
                    log_event(
                        db,
                        curve_id,
                        "error",
                        f"quick descent fallback failed [{exc.failure_class}]",
                    )
                    print(
                        f"    quick descent ERROR [{exc.failure_class}]"
                    )
                    continue

            if args.fast_screen and generic_lower is None:
                print("    strong candidate: computing deferred generic witness/metadata...")
                generic_lower = enrich_candidate(db, curve_id, family, t, E, Em, use_generic=not args.no_generic_witness, certificate_timeout=args.generic_certificate_timeout)
                print(f"    exact family-section lower >= {generic_lower}" if generic_lower is not None else ("    family witness disabled (FREE)" if args.no_generic_witness else "    family-section exact certificate unavailable"))

            print(f"    strong descent/search (timeout {args.strong_timeout}s)...")
            update_curve(db, curve_id, status="strong_running", error=None)

            try:
                strong = run_descent(
                    {
                        "mode": "strong",
                        "a_invariants": ainvs,
                        "first_limit": args.first_limit,
                        "second_limit": args.second_limit,
                        "second_descent": True,
                        "precision": args.precision,
                        "saturate_to": args.saturate_to,
                    },
                    timeout=args.strong_timeout,
                )
            except DescentTimeout as exc:
                update_curve(db, curve_id, status="strong_timeout", error=str(exc))
                record_rank_evidence(db, curve_id=curve_id, model=ainvs, data={
                    "engine": "mwrank", "evidence_type": "descent", "status": "timeout",
                    "rigorous": True, "rigorous_lower": None, "rigorous_upper": None,
                    "exact_rank": None, "assumptions": [], "points_found": [], "timed_out": True,
                    "options": {"timeout": int(args.strong_timeout), "source": "auto_analyze_strong"},
                    "minimal_model_a_invariants": ainvs,
                })
                log_event(db, curve_id, "warn", "strong descent timed out")
                print("    strong descent TIMEOUT")
                continue
            except DescentFailure as exc:
                size_limited = exc.failure_class == "MWRANK_SIZE_LIMIT"
                update_curve(
                    db,
                    curve_id,
                    status="strong_inconclusive" if size_limited else "error",
                    error=f"{exc.failure_class}: {exc}",
                )
                record_rank_evidence(db, curve_id=curve_id, model=ainvs, data={
                    "engine": "mwrank", "evidence_type": "descent",
                    "status": "inconclusive" if size_limited else "error",
                    "rigorous": True, "rigorous_lower": None, "rigorous_upper": None,
                    "exact_rank": None, "assumptions": [], "points_found": [],
                    "options": {
                        "timeout": int(args.strong_timeout),
                        "source": "auto_analyze_strong",
                        "failure_class": exc.failure_class,
                    },
                    "minimal_model_a_invariants": ainvs, "stderr_summary": str(exc),
                })
                log_event(
                    db,
                    curve_id,
                    "warn" if size_limited else "error",
                    (
                        "strong descent unsupported for this coefficient size"
                        if size_limited
                        else f"strong descent failed [{exc.failure_class}]"
                    ),
                )
                print(
                    f"    strong descent INCONCLUSIVE [{exc.failure_class}]"
                    if size_limited
                    else f"    strong descent ERROR [{exc.failure_class}]"
                )
                if size_limited:
                    sat_fallback = saturate_curve(
                        db,
                        int(curve_id),
                        max_prime=int(args.saturate_to),
                        timeout=int(args.strong_timeout),
                        source="auto_analyze_mwrank_size_limit",
                    )
                    print(
                        "    standalone saturation",
                        sat_fallback.get("status"),
                        "index",
                        sat_fallback.get("index"),
                    )
                continue

            raw_lower = int(strong["lower"])
            upper = int(strong["upper"])
            certain = bool(strong["certain"])
            points = strong.get("points", [])
            screen = strong.get("height_screen") or {}
            sat = strong.get("saturation") or {}

            # Height/Gram positivity is useful scheduling evidence, not an exact
            # independence proof.  Promote a mwrank lower bound only from its
            # own exact/certain result or from Rank Hunter's exact certificate.
            mwrank_cert_lower = raw_lower if (certain and raw_lower == upper) else None
            point_certificate = None
            if mwrank_cert_lower is None and points:
                try:
                    point_certificate = run_exact_certificate(
                        Em.a_invariants(), points, timeout=int(args.strong_certificate_timeout)
                    )
                except (ExactCertificateTimeout, ExactCertificateFailure) as exc:
                    log_event(db, curve_id, "warn", f"mwrank generator exact certificate inconclusive: {exc}")
                else:
                    if point_certificate.get("independent"):
                        mwrank_cert_lower = int(point_certificate.get("rank_lower_bound") or len(points))

            current_curve = get_curve_by_key(db, family.name(), param)
            baseline_lower = max(
                int(current_curve["generic_lower"] or 0),
                int(current_curve["descent_lower"] or 0),
                int(current_curve["exact_rank"] or 0),
            )
            verified_lower = max(baseline_lower, int(mwrank_cert_lower or 0))

            promoted = promote_mwrank_strong_result(
                db,
                curve_id=curve_id,
                model=ainvs,
                result=strong,
                rigorous_lower=mwrank_cert_lower,
                point_certificate=point_certificate,
                source="auto_analyze_strong",
                search_ref="auto:strong:mwrank",
                options={
                    "timeout": int(args.strong_timeout),
                    "first_limit": int(args.first_limit),
                    "second_limit": int(args.second_limit),
                    "saturate_to": int(args.saturate_to),
                },
                metadata={
                    "raw_lower": raw_lower,
                    "certain": certain,
                    "certificate_timeout": int(args.strong_certificate_timeout),
                },
            )

            if sat and sat.get("attempted"):
                sat_error = sat.get("error")
                sat_status = "error" if sat_error else "completed"
                sat_options = {
                    "source": "auto_analyze_strong",
                    "max_prime": int(sat.get("max_prime") or args.saturate_to),
                    "index": sat.get("index"),
                    "regulator": sat.get("regulator"),
                }
                record_rank_evidence(db, curve_id=curve_id, model=ainvs, data={
                    "engine": "sage_saturation",
                    "engine_version": strong.get("engine_version"),
                    "sage_version": strong.get("sage_version"),
                    "evidence_type": "saturation",
                    "status": sat_status,
                    "rigorous": True,
                    "rigorous_lower": None,
                    "rigorous_upper": None,
                    "exact_rank": None,
                    "assumptions": [],
                    "points_found": [] if sat_error else list(sat.get("saturated_points") or []),
                    "options": sat_options,
                    "minimal_model_a_invariants": ainvs,
                    "stderr_summary": str(sat_error) if sat_error else None,
                })
                if sat_error:
                    log_event(db, curve_id, "warn", f"saturation failed: {sat_error}")
                else:
                    log_event(
                        db,
                        curve_id,
                        "info",
                        f"saturation completed through prime {sat_options['max_prime']} (index {sat_options['index']})",
                    )

            exact_rank = promoted.get("exact_rank")

            fields = {
                "regulator": str(strong.get("regulator")) if strong.get("regulator") is not None else None,
                "error": None,
            }
            if exact_rank is None:
                fields["status"] = "strong_done"
            update_curve(db, curve_id, **fields)

            msg = (
                f"exact rank {exact_rank}"
                if exact_rank is not None
                else f"descent [{verified_lower},{upper}]"
            )
            log_event(
                db,
                curve_id,
                "best" if verified_lower > incumbent else "info",
                msg,
            )

            print(f"    raw mwrank lower  >= {raw_lower}")
            print(f"    verified lower    >= {verified_lower}")
            print(f"    descent upper     <= {upper}")
            print(f"    certain              {certain}")
            print(f"    generators           {len(points)}")
            if sat:
                print(f"    saturation index     {sat.get('index', '?')}")

            new_best = best_lower_bound(db)
            if new_best > incumbent:
                print()
                print("    " + "*" * 48)
                print(f"    NEW BEST PROVEN LOWER BOUND: rank >= {new_best}")
                if exact_rank is not None:
                    print(f"    EXACT RANK: {exact_rank}")
                print("    " + "*" * 48)

        except KeyboardInterrupt:
            raise
        except Exception as exc:
            if curve_id is None:
                curve_id = upsert_curve(
                    db,
                    family=family.name(),
                    parameter=param,
                    score=float(row["score"]),
                )
            update_curve(db, curve_id, status="error", error=repr(exc))
            log_event(db, curve_id, "error", repr(exc))
            print(f"    ERROR: {exc!r}")

    if rows:
        print(f"[candidate done] {len(rows)}/{len(rows)}", flush=True)
    print()
    print(f"[done] best proven rank >= {best_lower_bound(db)}")
    print(f"[next] python -m rank42.status --db {args.db}")

if __name__ == "__main__":
    main()
