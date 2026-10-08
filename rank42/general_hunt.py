"""Nagao-prefiltered rational-point hunt for Rank Hunter General Hunt.

Scientific funnel
-----------------

1. Build a reproducible pool of short Weierstrass curves.
2. Rank the pool with a Nagao/Mestre-style finite-field score (heuristic only).
3. Keep the best *unsearched* candidates.
4. Search exact rational points with ``ratpoints`` at bounded height stages.
5. Use one bounded canonical-height Gram computation to select a numerical
   independent subset (screen only).
6. Promote ``rank >= r`` only when Rank Hunter's exact quadratic-character
   certificate proves the selected rational points independent modulo torsion.

Every expensive external stage has a hard timeout.  Score pools are cached by
scientific configuration so repeated jobs can continue through the ranked pool
without recomputing finite-field scores.
"""

from __future__ import annotations

import argparse
import itertools
import json
import time
from fractions import Fraction
from pathlib import Path

from sage.all import QQ, EllipticCurve

from rank42.db import connect
from rank42.exact_lb import ExactCertificateFailure, ExactCertificateTimeout, run_exact_certificate
from rank42.hunt_store import (
    _record_trial,
    _store_hit,
    _trial_row,
    _try_catalog_check,
    _try_exact_rank,
)
from rank42.short_weierstrass import nonsingular_short, seeded_short_curve, trial_key
from rank42.height_bounded import HeightScreenFailure, HeightScreenTimeout, run_height_screen
from rank42.general_hunt_core import (
    canonical_affine_key,
    numerical_independent_indices,
    parse_height_stages,
    rational_height_key,
    sample_unique_pairs,
    score_cache_path,
    short_curve_nagao_score,
)
from rank42.ratpoints import RatpointsFailure, RatpointsNotFound, RatpointsTimeout, run_ratpoints


RESULT_MARKER = "RANK42_GENERAL_HUNT_RESULT="


def parse_args():
    ap = argparse.ArgumentParser(description="Nagao-prefiltered General Hunt")
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--pool-mode", choices=["seeded", "open"], default="seeded")
    ap.add_argument("--target", type=int, default=5)
    ap.add_argument("--pool-size", type=int, default=5000, help="cheap models to Nagao-score")
    ap.add_argument("--shortlist", type=int, default=100, help="best unsearched models to point-search")
    ap.add_argument("--nagao-bound", type=int, default=100)
    ap.add_argument("--random-seed", type=int, default=42)
    ap.add_argument("--ratpoints-stages", default="100,1000,10000")
    ap.add_argument("--ratpoints", help="ratpoints-compatible executable (CPU or GPU)")
    ap.add_argument("--ratpoints-timeout", type=int, default=5, help="hard timeout per curve/height stage")
    ap.add_argument("--max-point-candidates", type=int, default=32)
    ap.add_argument("--precision", type=int, default=160)
    ap.add_argument("--height-timeout", type=int, default=10)
    ap.add_argument("--certificate-timeout", type=int, default=30)
    ap.add_argument("--combination-limit", type=int, default=8)
    ap.add_argument("--extend-limit", type=int, default=4)
    ap.add_argument("--stop-after", type=int, default=1)
    ap.add_argument("--rank-cert-timeout", type=int, default=0)
    ap.add_argument("--catalog-timeout", type=int, default=0)
    ap.add_argument("--rerun", action="store_true")
    ap.add_argument("--no-score-cache", action="store_true")
    ap.add_argument("--legacy-resume", action="store_true", help=argparse.SUPPRESS)

    ap.add_argument("--u-min", type=int, default=1)
    ap.add_argument("--u-max", type=int, default=250)
    ap.add_argument("--v-min", type=int, default=1)
    ap.add_argument("--v-max", type=int, default=250)
    ap.add_argument("--a-min", type=int, default=-5000)
    ap.add_argument("--a-max", type=int, default=5000)
    ap.add_argument("--b-min", type=int, default=-5000)
    ap.add_argument("--b-max", type=int, default=5000)
    return ap.parse_args()


def _pool_config(args):
    cfg = {
        "pool_mode": args.pool_mode,
        "pool_size": int(args.pool_size),
        "nagao_bound": int(args.nagao_bound),
        "seed": int(args.random_seed),
    }
    if args.pool_mode == "seeded":
        cfg.update(u_min=int(args.u_min), u_max=int(args.u_max), v_min=int(args.v_min), v_max=int(args.v_max))
    else:
        cfg.update(a_min=int(args.a_min), a_max=int(args.a_max), b_min=int(args.b_min), b_max=int(args.b_max))
    return cfg


def _build_pool(args):
    if args.pool_mode == "seeded":
        pairs = sample_unique_pairs(args.u_min, args.u_max, args.v_min, args.v_max, args.pool_size, seed=args.random_seed)
        out = []
        seen = set()
        for u, v in pairs:
            if u == 0 or v == 0:
                continue
            A, B, seeds = seeded_short_curve(u, v)
            if not nonsingular_short(A, B) or (A, B) in seen:
                continue
            seen.add((A, B))
            out.append({"source_a": u, "source_b": v, "A": A, "B": B, "seeds": list(seeds), "source": f"u={u},v={v}"})
        return out
    pairs = sample_unique_pairs(args.a_min, args.a_max, args.b_min, args.b_max, args.pool_size, seed=args.random_seed)
    out = []
    for A, B in pairs:
        if nonsingular_short(A, B):
            out.append({"source_a": A, "source_b": B, "A": A, "B": B, "seeds": [], "source": f"A={A},B={B}"})
    return out


def _read_score_cache(path: Path):
    rows = []
    try:
        with path.open() as f:
            for line in f:
                if line.strip():
                    rows.append(json.loads(line))
    except Exception:
        return None
    return rows or None


def _write_score_cache(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    tmp.replace(path)


def _score_pool(args, pool):
    cfg = _pool_config(args)
    cache = score_cache_path(Path.cwd(), cfg)
    if not args.no_score_cache and cache.exists():
        cached = _read_score_cache(cache)
        if cached is not None:
            print(f"[general-hunt cache] loaded {len(cached)} scored candidates from {cache}", flush=True)
            return cached, cache, True

    scored = []
    total = len(pool)
    best = None
    for i, rec in enumerate(pool, 1):
        score, used = short_curve_nagao_score(rec["A"], rec["B"], args.nagao_bound)
        row = {**rec, "nagao_score": float(score), "nagao_primes": int(used)}
        scored.append(row)
        if best is None or score > best:
            best = score
        if i == 1 or i == total or i % 100 == 0:
            print(f"[general-hunt score] {i}/{total} best={best:.8f}", flush=True)
    scored.sort(key=lambda r: (-r["nagao_score"], -r["nagao_primes"], abs(r["A"]) + abs(r["B"]), r["A"], r["B"]))
    if not args.no_score_cache:
        _write_score_cache(cache, scored)
        print(f"[general-hunt cache] wrote {len(scored)} scored candidates to {cache}", flush=True)
    return scored, cache, False


def _finite_point(E, x, y):
    try:
        P = E(QQ(str(x)), QQ(str(y)))
    except Exception:
        return None
    try:
        if P.has_finite_order():
            return None
    except Exception:
        pass
    return P


def _merge_points(E, existing, additions, max_points):
    points = []
    seen = set()
    for x, y in list(existing) + list(additions):
        key = canonical_affine_key(Fraction(str(x)), Fraction(str(y)))
        if key in seen or Fraction(str(y)) == 0:
            continue
        P = _finite_point(E, x, y)
        if P is None:
            continue
        seen.add(key)
        points.append(P)
    points.sort(key=lambda P: (rational_height_key(P[0]), rational_height_key(P[1])))
    return points[: int(max_points)]


def _screen_points(E, points, args):
    if len(points) < int(args.target):
        return [], 0, None, "insufficient_points", None
    try:
        screen = run_height_screen(E, points, precision=args.precision, timeout=args.height_timeout)
    except HeightScreenTimeout:
        return [], 0, None, "height_timeout", None
    except HeightScreenFailure as exc:
        return [], 0, None, "height_error", repr(exc)

    indices = numerical_independent_indices(screen.get("gram") or [], max_size=min(len(points), int(args.target) + int(args.extend_limit) + 8))
    screened = len(indices)
    if screened < int(args.target):
        return [], screened, None, "screened_below_target", None

    selected = [points[i] for i in indices]
    combos = itertools.islice(itertools.combinations(selected, int(args.target)), int(args.combination_limit))
    last_error = None
    cert_attempts = 0
    cert_timeouts = 0
    for combo in combos:
        cert_attempts += 1
        payload = [[str(P[0]), str(P[1])] for P in combo]
        try:
            cert = run_exact_certificate([str(a) for a in E.a_invariants()], payload, timeout=args.certificate_timeout)
        except ExactCertificateTimeout as exc:
            cert_timeouts += 1
            last_error = repr(exc)
            continue
        except ExactCertificateFailure as exc:
            last_error = repr(exc)
            continue
        if not cert.get("independent"):
            continue
        basis = list(combo)
        best_cert = cert
        growth = 0
        for P in selected:
            if P in basis or growth >= int(args.extend_limit):
                continue
            growth += 1
            trial = basis + [P]
            try:
                cert2 = run_exact_certificate(
                    [str(a) for a in E.a_invariants()],
                    [[str(Q[0]), str(Q[1])] for Q in trial],
                    timeout=args.certificate_timeout,
                )
            except (ExactCertificateTimeout, ExactCertificateFailure):
                continue
            if cert2.get("independent"):
                basis = trial
                best_cert = cert2
        return basis, screened, best_cert, "certified", None
    if cert_attempts and cert_timeouts == cert_attempts:
        return [], screened, None, "certificate_timeout", last_error
    return [], screened, None, "not_certified", last_error


def _candidate_trial_key(args, rec, max_height):
    return trial_key(
        mode=f"general_hunt_{args.pool_mode}",
        source_a=rec["source_a"], source_b=rec["source_b"],
        A=rec["A"], B=rec["B"], x_bound=max_height, target=args.target,
    )


def main():
    args = parse_args()
    if not args.legacy_resume:
        raise SystemExit(
            "standalone General Hunt is retired; start new work from Search, "
            "or resume an existing native job from Jobs"
        )
    if not (2 <= int(args.target) <= 12):
        raise SystemExit("--target must be between 2 and 12")
    if args.pool_size <= 0 or args.shortlist <= 0:
        raise SystemExit("--pool-size and --shortlist must be positive")
    if args.nagao_bound < 7:
        raise SystemExit("--nagao-bound must be at least 7")
    if args.max_point_candidates < args.target:
        raise SystemExit("--max-point-candidates must be at least --target")
    stages = parse_height_stages(args.ratpoints_stages)
    max_height = max(stages)

    db = connect(args.db)
    pool = _build_pool(args)
    print("RANK HUNTER GENERAL HUNT", flush=True)
    print("=" * 72, flush=True)
    print("pool mode            =", args.pool_mode, flush=True)
    print("target lower         =", args.target, "(exact certificate required)", flush=True)
    print("score pool           =", len(pool), flush=True)
    print("nagao bound          =", args.nagao_bound, "(heuristic ranking only)", flush=True)
    print("shortlist requested  =", args.shortlist, flush=True)
    print("ratpoints stages     =", ",".join(map(str, stages)), flush=True)
    print("ratpoints timeout    =", args.ratpoints_timeout, "seconds/stage", flush=True)
    print("height matrices      = numerical basis screen only", flush=True)
    print("rank promotion       = exact quadratic-character certificate only", flush=True)

    started = time.monotonic()
    scored, cache_path, cache_hit = _score_pool(args, pool)
    pending = []
    for rec in scored:
        key = _candidate_trial_key(args, rec, max_height)
        rec["trial_key"] = key
        if not args.rerun and _trial_row(db, key) is not None:
            continue
        pending.append(rec)
        if len(pending) >= int(args.shortlist):
            break

    best_score = scored[0]["nagao_score"] if scored else None
    cutoff = pending[-1]["nagao_score"] if pending else None
    print(
        f"[general-hunt shortlist] kept={len(pending)} best={best_score if best_score is not None else 'none'} cutoff={cutoff if cutoff is not None else 'none'}",
        flush=True,
    )
    print("GENERAL-HUNT planned =", len(pending), flush=True)

    hits = 0
    best_rigorous = 0
    best_screened = 0
    best_point_count = 0
    searched = 0
    stage_timeouts = 0

    for i, rec in enumerate(pending, 1):
        A, B = int(rec["A"]), int(rec["B"])
        score = float(rec["nagao_score"])
        print(
            f"[general-hunt {i}/{len(pending)}] mode={args.pool_mode} source={rec['source']} A={A} B={B} nagao={score:.8f}",
            flush=True,
        )
        searched += 1
        E = EllipticCurve(QQ, [0, 0, 0, A, B])
        seed_xy = [(x, y) for x, y in rec.get("seeds", [])]
        points = _merge_points(E, [], seed_xy, args.max_point_candidates)
        last_status = "insufficient_points"
        last_error = None
        screened = 0
        basis = []
        cert = None
        searched_height = 0
        timed_out = False

        for height in stages:
            searched_height = int(height)
            try:
                rr = run_ratpoints([B, A, 0, 1], height, executable=args.ratpoints, timeout=args.ratpoints_timeout)
            except RatpointsTimeout:
                stage_timeouts += 1
                timed_out = True
                last_status = "ratpoints_timeout"
                print(f"[general-hunt stage] {i}/{len(pending)} H={height} timeout", flush=True)
                break
            except (RatpointsNotFound, RatpointsFailure) as exc:
                last_status = "ratpoints_error"
                last_error = repr(exc)
                print(f"[general-hunt stage] {i}/{len(pending)} H={height} error={exc!r}", flush=True)
                break

            additions = [(rp.x, rp.y) for rp in rr["points"]]
            points = _merge_points(E, [(P[0], P[1]) for P in points], additions, args.max_point_candidates)
            best_point_count = max(best_point_count, len(points))
            print(f"[general-hunt stage] {i}/{len(pending)} H={height} rational_points={len(points)} runtime={rr['runtime']:.3f}", flush=True)

            if len(points) >= int(args.target):
                basis, screened, cert, last_status, last_error = _screen_points(E, points, args)
                best_screened = max(best_screened, int(screened))
                if basis and cert is not None:
                    break
                if last_status in {"height_timeout", "height_error", "certificate_timeout"}:
                    # A deeper rational-height stage is unlikely to repair a bounded
                    # height/proof bottleneck; move wide to the next curve.
                    break

        rigorous = len(basis) if cert is not None else 0
        curve_id = None
        exact_rank = None
        catalog_info = None
        if rigorous >= int(args.target):
            curve_id = _store_hit(db, E, A, B, basis, rigorous, cert, score=score)
            hits += 1
            best_rigorous = max(best_rigorous, rigorous)
            print(f"[general-hunt hit] curve #{curve_id} rigorous_lower={rigorous} A={A} B={B} points={len(basis)} nagao={score:.8f}", flush=True)
            exact_rank = _try_exact_rank(db, curve_id, E, rigorous, args.rank_cert_timeout)
            catalog_info = _try_catalog_check(db, curve_id, E, args.catalog_timeout)

        _record_trial(
            db,
            key=rec["trial_key"], mode=f"general_hunt_{args.pool_mode}",
            source_a=rec["source_a"], source_b=rec["source_b"], A=A, B=B,
            x_bound=searched_height or max_height, target=args.target, status=last_status,
            point_candidates=len(points), screened_rank=screened, rigorous_lower=rigorous,
            curve_id=curve_id, certificate=cert, error=last_error,
            metadata={
                "source": rec["source"],
                "nagao_score": score,
                "nagao_primes": int(rec["nagao_primes"]),
                "nagao_role": "heuristic_ranking_only",
                "ratpoints_height_stages": stages,
                "ratpoints_height_reached": searched_height,
                "ratpoints_timed_out": bool(timed_out),
                "rational_point_candidates": [[str(P[0]), str(P[1])] for P in points],
                "height_screen_role": "numerical_prefilter_only",
                "screened_rank": int(screened),
                "exact_rank": exact_rank,
                "catalog_check": catalog_info,
                "score_cache": str(cache_path),
                "score_cache_hit": bool(cache_hit),
            },
        )
        print(
            f"[general-hunt done] {i}/{len(pending)} rational_points={len(points)} screened={screened} rigorous={rigorous} status={last_status}",
            flush=True,
        )

        if int(args.stop_after) > 0 and hits >= int(args.stop_after):
            print(f"[general-hunt stop] reached {hits} rigorous hit(s)", flush=True)
            break

    result = {
        "status": "GENERAL HUNT HIT" if hits else "NO PROVEN HIT",
        "mode": f"general_hunt_{args.pool_mode}",
        "target_lower": int(args.target),
        "pool_scored": len(scored),
        "nagao_bound": int(args.nagao_bound),
        "best_nagao_score": best_score,
        "shortlist_planned": len(pending),
        "shortlist_searched": searched,
        "ratpoints_stages": stages,
        "ratpoints_timeouts": stage_timeouts,
        "best_rational_point_count": best_point_count,
        "best_screened_rank": best_screened,
        "rigorous_hits": hits,
        "best_rigorous_lower": best_rigorous,
        "score_cache": str(cache_path),
        "score_cache_hit": bool(cache_hit),
        "runtime_seconds": time.monotonic() - started,
        "proof_note": "Nagao scores rank candidates heuristically; ratpoints hits are exact rational points; height Gram ranks are numerical screens; only the exact quadratic-character certificate is promoted to a rigorous lower bound.",
    }
    print(RESULT_MARKER + json.dumps(result, sort_keys=True), flush=True)
    db.close()


if __name__ == "__main__":
    main()
