"""Shared prime/local arithmetic for Build Your Own pipelines.

This module deliberately separates exact local arithmetic from heuristic prime
scores.  Cached Frobenius/local data may be reused by many stages, but a score
never becomes rank evidence.  Local-solubility rejection is one-way: failure
to find an F_p point on an integral binary quartic is a rigorous Q_p
obstruction, while finding an F_p point is only "not obstructed by this test".
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from functools import reduce
from operator import mul
from pathlib import Path

from sage.all import GF, QQ, ZZ, prime_range

from rank42.curve_arithmetic_state import compare_curve_arithmetic_values
from rank42.db import now
from rank42.prime_schema import PRIME_SCHEMA, ensure_prime_schema, require_prime_schema
from rank42.ratpoints import normalize_polynomial
from rank42.torsion import canonical_torsion_label, torsion_invariants_from_label


BAD_PRIME_WORKER = Path(__file__).with_name("prime_local_worker.py")
BAD_PRIME_MARKER = "RANK42_PRIME_LOCAL_RESULT="


def _model_key(E):
    raw = json.dumps(
        [str(QQ(a)) for a in E.a_invariants()],
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def _minimal_curve(E):
    try:
        return E.global_minimal_model()
    except Exception:
        try:
            return E.minimal_model()
        except Exception:
            return E


def _has_good_reduction(E, p):
    p = int(p)
    try:
        if QQ(E.discriminant()).valuation(p) == 0:
            return True
    except Exception:
        pass
    try:
        return bool(E.local_data(p).has_good_reduction())
    except Exception:
        return None


def _factor_all_bad_primes(Em, *, timeout):
    payload = {"a_invariants": [str(a) for a in Em.a_invariants()]}
    try:
        cp = subprocess.run(
            [sys.executable, str(BAD_PRIME_WORKER)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=max(1, int(timeout)),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return (), False, f"bad-prime factorization exceeded {int(timeout)}s"

    marker = None
    for line in reversed((cp.stdout or "").splitlines()):
        if line.startswith(BAD_PRIME_MARKER):
            marker = line[len(BAD_PRIME_MARKER):]
            break
    if marker is None:
        tail = "\n".join(
            ((cp.stdout or "") + "\n" + (cp.stderr or "")).splitlines()[-12:]
        )
        detail = f": {tail}" if tail else ""
        return (), False, (
            f"bad-prime worker returned no result marker (exit={cp.returncode})"
            + detail
        )
    try:
        result = json.loads(marker)
    except Exception as exc:
        return (), False, f"bad-prime worker returned invalid JSON: {exc!r}"
    if cp.returncode != 0 or result.get("status") != "completed":
        return (), False, str(result.get("error") or f"worker exit={cp.returncode}")
    return tuple(sorted(int(p) for p in result.get("bad_primes") or [])), True, None


def prime_cache_state(db, curve_id):
    require_prime_schema(db)
    return db.execute(
        "SELECT * FROM curve_prime_cache_state WHERE curve_id=?",
        (int(curve_id),),
    ).fetchone()


def _valuation_integer(value, p):
    try:
        q = QQ(value)
        return int(q.valuation(int(p)))
    except Exception:
        return None


def _local_data_record(Em, p, *, good):
    out = {
        "discriminant_valuation": 0 if good else _valuation_integer(Em.discriminant(), p),
        "conductor_valuation": 0 if good else None,
        "tamagawa_number": 1 if good else None,
        "kodaira_symbol": "I0" if good else None,
        "local_root_number": 1 if good else None,
    }
    if good:
        return out
    ld = None
    try:
        ld = Em.local_data(int(p))
    except Exception:
        ld = None
    if ld is not None:
        for key, method in (
            ("discriminant_valuation", "discriminant_valuation"),
            ("conductor_valuation", "conductor_valuation"),
            ("tamagawa_number", "tamagawa_number"),
        ):
            try:
                out[key] = int(getattr(ld, method)())
            except Exception:
                pass
        try:
            out["kodaira_symbol"] = str(ld.kodaira_symbol())
        except Exception:
            pass
        try:
            out["local_root_number"] = int(ld.root_number())
        except Exception:
            pass
    if out["local_root_number"] is None:
        try:
            out["local_root_number"] = int(Em.root_number(int(p)))
        except Exception:
            pass
    return out


def cache_curve_primes(
    db,
    *,
    curve_id,
    E,
    prime_bound=2000,
    include_all_bad=True,
    bad_prime_timeout=20,
):
    """Populate reusable exact prime/local data for one stored curve.

    Reduction below the requested bound is decided prime-by-prime, without
    factoring the discriminant. Enumerating every bad prime is isolated behind
    a hard timeout. If that timeout is hit, bounded Frobenius data remains
    usable and the global bad-prime list is explicitly marked incomplete.
    """
    require_prime_schema(db)
    curve_id = int(curve_id)
    prime_bound = max(3, int(prime_bound))
    key = _model_key(E)
    state = prime_cache_state(db, curve_id)
    if state is not None and str(state["model_key"]) != key:
        db.execute("DELETE FROM curve_prime_data WHERE curve_id=?", (curve_id,))
        db.execute("DELETE FROM curve_prime_cache_state WHERE curve_id=?", (curve_id,))
        db.commit()
        state = None

    bad = set()
    bad_complete = False
    bad_error = None
    if state is not None:
        try:
            bad.update(int(p) for p in json.loads(state["bad_primes_json"] or "[]"))
        except Exception:
            pass
        bad_complete = bool(state["bad_primes_complete"])
        bad_error = state["error"]

    if include_all_bad and not bad_complete:
        all_bad, bad_complete, factor_error = _factor_all_bad_primes(
            E,
            timeout=max(1, int(bad_prime_timeout)),
        )
        if bad_complete:
            bad = set(all_bad)
            bad_error = None
        else:
            bad_error = factor_error

    primes = {int(p) for p in prime_range(2, prime_bound)}
    if bad_complete:
        primes.update(bad)

    existing = {
        int(row["p"]): row
        for row in db.execute(
            "SELECT * FROM curve_prime_data WHERE curve_id=?",
            (curve_id,),
        ).fetchall()
    }
    computed = 0
    reused = 0
    unknown_reduction = []
    ts = now()
    for p in sorted(primes):
        row = existing.get(p)
        if row is not None and str(row["model_key"]) == key:
            row_good = bool(row["good_reduction"])
            row_complete = (
                row["ap"] is not None and row["cardinality"] is not None
                if row_good
                else (
                    row["conductor_valuation"] is not None
                    and row["tamagawa_number"] is not None
                    and row["kodaira_symbol"] is not None
                    and row["local_root_number"] is not None
                )
            )
            if row_complete:
                reused += 1
                if not row_good:
                    bad.add(p)
                continue

        if bad_complete:
            good = p not in bad
        else:
            good = _has_good_reduction(E, p)
            if good is None:
                unknown_reduction.append(p)
                continue
            if not good:
                bad.add(p)

        ap = None
        cardinality = None
        normalized_ap = None
        if good:
            try:
                ap = int(E.ap(p))
                cardinality = int(p + 1 - ap)
                normalized_ap = float(ap) / math.sqrt(float(p))
            except Exception:
                ap = cardinality = normalized_ap = None
        local = _local_data_record(E, p, good=bool(good))
        db.execute(
            """INSERT INTO curve_prime_data(
                   curve_id,p,model_key,good_reduction,ap,cardinality,
                   normalized_ap,discriminant_valuation,conductor_valuation,
                   tamagawa_number,kodaira_symbol,local_root_number,computed_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(curve_id,p) DO UPDATE SET
                   model_key=excluded.model_key,
                   good_reduction=excluded.good_reduction,
                   ap=excluded.ap,
                   cardinality=excluded.cardinality,
                   normalized_ap=excluded.normalized_ap,
                   discriminant_valuation=excluded.discriminant_valuation,
                   conductor_valuation=excluded.conductor_valuation,
                   tamagawa_number=excluded.tamagawa_number,
                   kodaira_symbol=excluded.kodaira_symbol,
                   local_root_number=excluded.local_root_number,
                   computed_at=excluded.computed_at""",
            (
                curve_id,
                str(p),
                key,
                1 if good else 0,
                ap,
                cardinality,
                normalized_ap,
                local["discriminant_valuation"],
                local["conductor_valuation"],
                local["tamagawa_number"],
                local["kodaira_symbol"],
                local["local_root_number"],
                ts,
            ),
        )
        computed += 1

    prior_bound = (
        int(state["good_prime_bound"] or 0)
        if state is not None and str(state["model_key"]) == key
        else 0
    )

    # Recompute the durable Frobenius coverage boundary from actual rows rather
    # than trusting the prior requested bound. Older builds advanced
    # good_prime_bound even when a good-prime E.ap(p) computation failed.
    coverage_target = max(prior_bound, prime_bound)
    coverage_rows = {
        int(row["p"]): row
        for row in db.execute(
            """SELECT * FROM curve_prime_data
               WHERE curve_id=? AND model_key=?""",
            (curve_id, key),
        ).fetchall()
    }
    coverage_issue = None
    for p in (int(p0) for p0 in prime_range(2, coverage_target)):
        row = coverage_rows.get(p)
        if row is None:
            coverage_issue = p
            break
        if bool(row["good_reduction"]) and (
            row["ap"] is None or row["cardinality"] is None
        ):
            coverage_issue = p
            break
    frobenius_complete_through = (
        coverage_target if coverage_issue is None else int(coverage_issue)
    )

    requested_good_primes = []
    computed_good_primes = []
    frobenius_failed_primes = []
    for p in sorted(int(p0) for p0 in prime_range(2, prime_bound)):
        row = coverage_rows.get(p)
        if row is None or not bool(row["good_reduction"]):
            continue
        requested_good_primes.append(p)
        if row["ap"] is not None and row["cardinality"] is not None:
            computed_good_primes.append(p)
        else:
            frobenius_failed_primes.append(p)

    db.execute(
        """INSERT INTO curve_prime_cache_state(
               curve_id,model_key,good_prime_bound,bad_primes_complete,
               bad_primes_json,error,updated_at
           ) VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(curve_id) DO UPDATE SET
               model_key=excluded.model_key,
               good_prime_bound=excluded.good_prime_bound,
               bad_primes_complete=excluded.bad_primes_complete,
               bad_primes_json=excluded.bad_primes_json,
               error=excluded.error,
               updated_at=excluded.updated_at""",
        (
            curve_id,
            key,
            int(frobenius_complete_through),
            1 if bad_complete else 0,
            json.dumps(sorted(bad)),
            bad_error,
            ts,
        ),
    )
    db.commit()
    status = "completed"
    if unknown_reduction or (include_all_bad and not bad_complete):
        status = "inconclusive"
    elif frobenius_failed_primes:
        status = "partial"
    return {
        "status": status,
        "prime_bound": prime_bound,
        "primes_requested": len(primes),
        "computed": computed,
        "reused": reused,
        "requested_good_primes": requested_good_primes,
        "requested_good_prime_count": len(requested_good_primes),
        "computed_good_primes": computed_good_primes,
        "computed_good_prime_count": len(computed_good_primes),
        "frobenius_failed_primes": frobenius_failed_primes,
        "frobenius_complete_through": int(frobenius_complete_through),
        "bad_primes": sorted(bad),
        "bad_primes_complete": bool(bad_complete),
        "bad_prime_error": bad_error,
        "unknown_reduction_primes": unknown_reduction,
        "model_key": key,
    }

def curve_prime_rows(db, curve_id, *, good_only=False, max_p=None):
    require_prime_schema(db)
    sql = "SELECT * FROM curve_prime_data WHERE curve_id=?"
    args = [int(curve_id)]
    if good_only:
        sql += " AND good_reduction=1"
    if max_p is not None:
        # p is canonical positive-decimal TEXT so it can represent primes above
        # SQLite's signed 64-bit integer range. Compare by decimal length then
        # lexicographically, which is exact for positive integers.
        bound = str(int(max_p))
        sql += (
            " AND (length(p)<length(?) "
            "OR (length(p)=length(?) AND p<?))"
        )
        args.extend([bound, bound, bound])
    sql += " ORDER BY length(p),p"
    return db.execute(sql, args).fetchall()


def _prime_score_domain(cache, *, algorithm, bounds, used_primes):
    """Compact, auditable prime domain for a heuristic score."""
    requested = [int(p) for p in (cache.get("requested_good_primes") or [])]
    used = sorted({int(p) for p in used_primes})
    failed = [int(p) for p in (cache.get("frobenius_failed_primes") or [])]
    unknown = [int(p) for p in (cache.get("unknown_reduction_primes") or [])]
    cache_status = str(cache.get("status") or "inconclusive")
    return {
        "algorithm": str(algorithm),
        "prime_bounds": [int(x) for x in bounds],
        "good_primes_requested": requested,
        "good_primes_requested_count": len(requested),
        "good_primes_used": used,
        "good_primes_used_count": len(used),
        "failed_primes": failed,
        "unknown_reduction_primes": unknown,
        "cache_status": cache_status,
        "frobenius_complete_through": int(
            cache.get("frobenius_complete_through") or 0
        ),
        "complete": bool(
            cache_status == "completed"
            and not failed
            and not unknown
            and used == requested
        ),
    }


NAGAO_LOG_CARDINALITY_ALGORITHM = "cached-nagao-log-cardinality-v1"


def _prime_heuristic_status(cache, *, has_signal, intrinsic_complete=True):
    """Preserve incomplete cache state instead of upgrading it to completed."""
    if not has_signal:
        return "inconclusive"
    if str(cache.get("status") or "") != "completed":
        return "partial"
    return "completed" if intrinsic_complete else "inconclusive"


def direct_nagao_log_cardinality_score(E, *, prime_bound):
    """Rank Hunter historical Nagao-style score without a persistent cache.

    This is the same log-cardinality statistic as Multi-Scale Frobenius. It is
    intended for ephemeral exact child models, such as Targeted Twist trials,
    where persisting every scanned curve merely to populate the cache would be
    undesirable. Provenance explicitly records that no cache was used.
    """
    prime_bound = max(3, int(prime_bound))
    Em = _minimal_curve(E)
    model_key = _model_key(Em)
    requested_good = []
    used_primes = []
    failed = []
    total = 0.0
    try:
        discriminant = QQ(Em.discriminant())
    except Exception:
        discriminant = None

    for p0 in prime_range(2, prime_bound):
        p = int(p0)
        if discriminant is not None:
            try:
                if discriminant.valuation(p) > 0:
                    continue
            except Exception:
                pass
        else:
            good = _has_good_reduction(Em, p)
            if good is False:
                continue
            if good is None:
                failed.append(p)
                continue

        requested_good.append(p)
        try:
            ap = int(Em.ap(p))
            cardinality = int(p + 1 - ap)
            if cardinality <= 0:
                raise ValueError("nonpositive finite-field cardinality")
        except Exception:
            failed.append(p)
            continue
        total += math.log(float(cardinality) / float(p))
        used_primes.append(p)

    status = (
        "inconclusive"
        if not used_primes
        else ("partial" if failed else "completed")
    )
    domain = {
        "algorithm": NAGAO_LOG_CARDINALITY_ALGORITHM,
        "prime_bounds": [prime_bound],
        "good_primes_requested": requested_good,
        "good_primes_requested_count": len(requested_good),
        "good_primes_used": used_primes,
        "good_primes_used_count": len(used_primes),
        "failed_primes": failed,
        "unknown_reduction_primes": [],
        "cache_status": "not_used",
        "cache_used": False,
        "source": "direct_exact_frobenius",
        "model_key": model_key,
        "frobenius_complete_through": (
            prime_bound if not failed else min(failed)
        ),
        "complete": bool(used_primes and not failed),
    }
    return {
        "status": status,
        "algorithm": NAGAO_LOG_CARDINALITY_ALGORITHM,
        "heuristic_kind": "rank_hunter_historical_nagao_style",
        "published_mestre_nagao_formula": False,
        "prime_bound": prime_bound,
        "pipeline_score": total if used_primes else None,
        "primes_used": len(used_primes),
        "score_domain": domain,
        "source_provenance": {
            "source": "direct_exact_frobenius",
            "cache_used": False,
            "model_key": model_key,
        },
        "heuristic_only": True,
    }


def multi_scale_frobenius_score(db, *, curve_id, E, bounds):
    """Return cumulative and incremental Nagao-style scores from cached primes."""
    bounds = sorted({max(3, int(x)) for x in bounds})
    if not bounds:
        raise ValueError("at least one Frobenius prime bound is required")
    cache = cache_curve_primes(
        db,
        curve_id=curve_id,
        E=E,
        prime_bound=max(bounds),
        include_all_bad=False,
    )
    rows = curve_prime_rows(db, curve_id, good_only=True, max_p=max(bounds))
    cumulative = []
    previous_total = 0.0
    previous_used = 0
    for bound in bounds:
        total = 0.0
        used = 0
        for row in rows:
            p = int(row["p"])
            if p >= bound:
                break
            n = row["cardinality"]
            if n is None or int(n) <= 0:
                continue
            total += math.log(float(n) / float(p))
            used += 1
        cumulative.append(
            {
                "prime_bound": bound,
                "score": total,
                "primes_used": used,
                "band_score": total - previous_total,
                "band_primes": used - previous_used,
            }
        )
        previous_total = total
        previous_used = used
    used_primes = [
        int(row["p"])
        for row in rows
        if row["cardinality"] is not None and int(row["cardinality"]) > 0
    ]
    algorithm = NAGAO_LOG_CARDINALITY_ALGORITHM
    return {
        "status": _prime_heuristic_status(
            cache, has_signal=bool(used_primes), intrinsic_complete=True
        ),
        "algorithm": algorithm,
        "heuristic_kind": "rank_hunter_historical_nagao_style",
        "published_mestre_nagao_formula": False,
        "bounds": bounds,
        "cumulative": cumulative,
        "pipeline_score": cumulative[-1]["score"] if used_primes else None,
        "primes_used": cumulative[-1]["primes_used"],
        "score_domain": _prime_score_domain(
            cache, algorithm=algorithm, bounds=bounds, used_primes=used_primes
        ),
    }



def mestre_nagao_ensemble_score(db, *, curve_id, E, prime_bound=5000):
    """Consensus of three cached prime statistics; scheduling heuristic only."""
    prime_bound = max(3, int(prime_bound))
    cache = cache_curve_primes(
        db, curve_id=curve_id, E=E, prime_bound=prime_bound, include_all_bad=False,
    )
    rows = curve_prime_rows(db, curve_id, good_only=True, max_p=prime_bound)
    log_cardinality = 0.0
    nagao_trace = 0.0
    normalized_trace = 0.0
    used = 0
    used_primes = []
    for row in rows:
        p = int(row["p"])
        n = row["cardinality"]
        ap = row["ap"]
        normalized_ap = row["normalized_ap"]
        if n is None or int(n) <= 0 or ap is None or normalized_ap is None:
            continue
        log_cardinality += math.log(float(n) / float(p))
        nagao_trace += -float(ap) * math.log(float(p)) / float(p)
        normalized_trace += -float(normalized_ap)
        used += 1
        used_primes.append(p)
    if used <= 0:
        algorithm = "mestre-nagao-ensemble-v1"
        return {
            "status": "inconclusive",
            "algorithm": algorithm,
            "heuristic_kind": "rank_hunter_experimental_prime_ensemble",
            "published_multi_value_mestre_nagao": False,
            "trace_sign_convention": "negative_ap_log_p_over_p",
            "prime_bound": prime_bound,
            "primes_used": 0,
            "pipeline_score": None,
            "heuristic_only": True,
            "score_domain": _prime_score_domain(
                cache, algorithm=algorithm, bounds=[prime_bound], used_primes=[]
            ),
        }
    log_signal = log_cardinality / math.sqrt(float(used))
    trace_signal = nagao_trace / max(1.0, math.log(float(prime_bound)))
    normalized_signal = normalized_trace / math.sqrt(float(used))
    consensus = (log_signal + trace_signal + normalized_signal) / 3.0
    algorithm = "mestre-nagao-ensemble-v1"
    return {
        "status": _prime_heuristic_status(
            cache, has_signal=bool(used_primes), intrinsic_complete=True
        ),
        "algorithm": algorithm,
        "heuristic_kind": "rank_hunter_experimental_prime_ensemble",
        "published_multi_value_mestre_nagao": False,
        "trace_sign_convention": "negative_ap_log_p_over_p",
        "prime_bound": prime_bound,
        "primes_used": used,
        "components": {
            "log_cardinality_sum": log_cardinality,
            "log_cardinality_signal": log_signal,
            "nagao_trace_sum": nagao_trace,
            "nagao_trace_signal": trace_signal,
            "normalized_trace_sum": normalized_trace,
            "normalized_trace_signal": normalized_signal,
        },
        "pipeline_score": consensus,
        "heuristic_only": True,
        "score_domain": _prime_score_domain(
            cache,
            algorithm=algorithm,
            bounds=[prime_bound],
            used_primes=used_primes,
        ),
    }


def frobenius_persistence_score(db, *, curve_id, E, bounds):
    """Reward a prime signal that persists across disjoint prime bands."""
    bounds = [int(x) for x in bounds]
    if len(bounds) < 2:
        raise ValueError("at least two Frobenius persistence bounds are required")
    cache = cache_curve_primes(
        db, curve_id=curve_id, E=E, prime_bound=max(bounds), include_all_bad=False,
    )
    rows = curve_prime_rows(db, curve_id, good_only=True, max_p=max(bounds))
    bands = []
    low = 2
    signals = []
    for high in bounds:
        total = 0.0
        used = 0
        for row in rows:
            p = int(row["p"])
            if p < low or p >= high:
                continue
            n = row["cardinality"]
            if n is None or int(n) <= 0:
                continue
            total += math.log(float(n) / float(p))
            used += 1
        signal = None if used == 0 else total / math.sqrt(float(used))
        bands.append({
            "low": low,
            "high": high,
            "score": total,
            "primes_used": used,
            "normalized_signal": signal,
        })
        if signal is not None:
            signals.append(signal)
        low = high
    complete = len(signals) == len(bands)
    pipeline_score = min(signals) if complete and signals else None
    positive = sum(1 for value in signals if value > 0)
    used_primes = [
        int(row["p"])
        for row in rows
        if row["cardinality"] is not None and int(row["cardinality"]) > 0
    ]
    algorithm = "frobenius-persistence-worst-band-v1"
    return {
        "status": _prime_heuristic_status(
            cache, has_signal=bool(signals), intrinsic_complete=complete
        ),
        "algorithm": algorithm,
        "heuristic_kind": "rank_hunter_experimental_scheduling_heuristic",
        "published_standard_statistic": False,
        "rank_evidence": False,
        "bounds": bounds,
        "bands": bands,
        "positive_band_fraction": float(positive) / float(len(signals)) if signals else None,
        "pipeline_score": pipeline_score,
        "aggregation": "minimum normalized disjoint-band signal",
        "heuristic_only": True,
        "score_domain": _prime_score_domain(
            cache, algorithm=algorithm, bounds=bounds, used_primes=used_primes
        ),
    }


def explicit_formula_indicator_score(
    db, *, curve_id, E, prime_bound=5000, max_prime_power=4,
):
    """Smoothed good-prime side of an explicit-formula calculation.

    Conductor, archimedean, and bad-prime corrections are deliberately omitted,
    so this is a scheduling indicator rather than an analytic-rank bound.
    """
    prime_bound = max(3, int(prime_bound))
    max_prime_power = max(1, int(max_prime_power))
    cache = cache_curve_primes(
        db, curve_id=curve_id, E=E, prime_bound=prime_bound, include_all_bad=False,
    )
    rows = curve_prime_rows(db, curve_id, good_only=True, max_p=prime_bound)
    log_bound = math.log(float(prime_bound))
    total = 0.0
    prime_terms = 0
    primes_used = 0
    used_primes = []
    for row in rows:
        p = int(row["p"])
        ap = row["ap"]
        if ap is None:
            continue
        primes_used += 1
        used_primes.append(p)
        previous_previous = 2.0
        previous = float(ap)
        for m in range(1, max_prime_power + 1):
            q = p ** m
            if q >= prime_bound:
                break
            if m == 1:
                bm = previous
            else:
                bm = float(ap) * previous - float(p) * previous_previous
                previous_previous, previous = previous, bm
            support = max(0.0, 1.0 - math.log(float(q)) / log_bound)
            total += (
                -bm * math.log(float(p))
                / (float(p) ** (0.5 * float(m))) * support
            )
            prime_terms += 1
    algorithm = "smoothed-good-prime-explicit-formula-proxy-v1"
    return {
        "status": _prime_heuristic_status(
            cache, has_signal=bool(prime_terms), intrinsic_complete=bool(prime_terms)
        ),
        "algorithm": algorithm,
        "prime_bound": prime_bound,
        "max_prime_power": max_prime_power,
        "primes_used": primes_used,
        "prime_power_terms": prime_terms,
        "raw_prime_sum": total,
        "pipeline_score": total / max(1.0, log_bound) if prime_terms else None,
        "heuristic_only": True,
        "analytic_rank_bound": False,
        "omitted_terms": ["conductor", "archimedean", "bad-prime corrections"],
        "score_domain": _prime_score_domain(
            cache,
            algorithm=algorithm,
            bounds=[prime_bound],
            used_primes=used_primes,
        ),
    }

def local_root_number_profile(
    db,
    *,
    curve_id,
    E,
    prime_bound=200,
    bad_prime_timeout=20,
    include_all_bad=True,
):
    """Return exact finite local root numbers when the bad-prime list completes."""
    cache = cache_curve_primes(
        db,
        curve_id=curve_id,
        E=E,
        prime_bound=max(3, int(prime_bound)),
        include_all_bad=bool(include_all_bad),
        bad_prime_timeout=bad_prime_timeout,
    )
    rows = [
        row for row in curve_prime_rows(db, curve_id)
        if not bool(row["good_reduction"])
    ]
    locals_out = []
    finite_product = 1
    local_complete = bool(include_all_bad and cache.get("bad_primes_complete"))
    for row in rows:
        value = row["local_root_number"]
        if value is None:
            local_complete = False
        else:
            finite_product *= int(value)
        locals_out.append(
            {
                "p": int(row["p"]),
                "local_root_number": None if value is None else int(value),
                "kodaira_symbol": row["kodaira_symbol"],
                "conductor_valuation": row["conductor_valuation"],
            }
        )
    global_root = -finite_product if local_complete else None
    authority_check = None
    if global_root is not None:
        authority_check = compare_curve_arithmetic_values(
            db,
            curve_id,
            {"root_number": int(global_root)},
        )
    authority_conflicts = (
        list(authority_check.get("conflicts") or [])
        if authority_check is not None
        else []
    )
    return {
        "status": (
            "inconclusive"
            if authority_conflicts
            else ("completed" if local_complete else "inconclusive")
        ),
        "global_root_number": global_root,
        "archimedean_root_number": -1,
        "finite_product": finite_product if local_complete else None,
        "bad_prime_count": len(rows),
        "bad_primes_complete": bool(cache.get("bad_primes_complete")),
        "bounded_only": not bool(include_all_bad),
        "bad_prime_error": cache.get("bad_prime_error"),
        "local_factors": locals_out,
        "complete": bool(local_complete),
        "arithmetic_authority_check": authority_check,
        "arithmetic_conflicts": authority_conflicts,
    }


def bad_prime_fingerprint(
    db,
    *,
    curve_id,
    E,
    prime_bound=200,
    bad_prime_timeout=20,
    include_all_bad=True,
):
    cache = cache_curve_primes(
        db,
        curve_id=curve_id,
        E=E,
        prime_bound=max(3, int(prime_bound)),
        include_all_bad=bool(include_all_bad),
        bad_prime_timeout=bad_prime_timeout,
    )
    rows = [
        row for row in curve_prime_rows(db, curve_id)
        if not bool(row["good_reduction"])
    ]
    local = []
    tamagawa_product = 1
    tamagawa_complete = bool(include_all_bad and cache.get("bad_primes_complete"))
    for row in rows:
        value = row["tamagawa_number"]
        if value is None:
            tamagawa_complete = False
        else:
            tamagawa_product *= int(value)
        local.append(
            {
                "p": int(row["p"]),
                "v_delta": row["discriminant_valuation"],
                "conductor_exponent": row["conductor_valuation"],
                "kodaira_symbol": row["kodaira_symbol"],
                "tamagawa_number": value,
                "local_root_number": row["local_root_number"],
            }
        )
    conductor = None
    if cache.get("bad_primes_complete") and all(
        rec["conductor_exponent"] is not None for rec in local
    ):
        value = ZZ(1)
        for rec in local:
            value *= ZZ(rec["p"]) ** int(rec["conductor_exponent"])
        conductor = str(value)
    bad_primes_complete = bool(include_all_bad and cache.get("bad_primes_complete"))
    local_data_complete = bool(
        bad_primes_complete
        and all(
            rec["v_delta"] is not None
            and rec["conductor_exponent"] is not None
            and rec["kodaira_symbol"] is not None
            and rec["tamagawa_number"] is not None
            and rec["local_root_number"] is not None
            for rec in local
        )
    )
    discriminant = None
    if bad_primes_complete and all(rec["v_delta"] is not None for rec in local):
        magnitude = ZZ(1)
        for rec in local:
            magnitude *= ZZ(rec["p"]) ** int(rec["v_delta"])
        try:
            sign = -1 if QQ(E.discriminant()) < 0 else 1
        except Exception:
            sign = 1
        discriminant = str(sign * magnitude)
    proposed_globals = {}
    if bad_primes_complete:
        proposed_globals["bad_primes"] = [rec["p"] for rec in local]
    if conductor is not None:
        proposed_globals["conductor"] = conductor
    if discriminant is not None:
        proposed_globals["discriminant"] = discriminant
    authority_check = (
        compare_curve_arithmetic_values(db, curve_id, proposed_globals)
        if proposed_globals
        else None
    )
    authority_conflicts = (
        list(authority_check.get("conflicts") or [])
        if authority_check is not None
        else []
    )
    return {
        "status": (
            "inconclusive"
            if authority_conflicts
            else ("completed" if local_data_complete else "inconclusive")
        ),
        "bad_primes": [rec["p"] for rec in local],
        "bad_prime_count": len(local),
        "bad_primes_complete": bad_primes_complete,
        "bounded_only": not bool(include_all_bad),
        "local_data_complete": local_data_complete,
        "bad_prime_error": cache.get("bad_prime_error"),
        "local_data": local,
        "minimal_discriminant": discriminant,
        "conductor": conductor,
        "tamagawa_product": tamagawa_product if tamagawa_complete else None,
        "tamagawa_complete": bool(tamagawa_complete),
        "arithmetic_authority_check": authority_check,
        "arithmetic_conflicts": authority_conflicts,
    }

def _finite_group_invariants(E, p):
    """Exact invariant factors of E(F_p), or an explicit computation failure."""
    try:
        group = E.change_ring(GF(int(p))).abelian_group()
        invariants = tuple(
            int(value) for value in group.invariants() if int(value) > 1
        )
        return invariants, None
    except Exception as exc:
        return None, repr(exc)


def _finite_abelian_embedding_possible(target_invariants, ambient_invariants):
    """Whether a finite abelian target can embed in an ambient finite group.

    The criterion is checked prime-primary: for every prime ell and power ell^k,
    the target may not require more cyclic factors divisible by ell^k than the
    ambient group supplies. This is equivalent to subgroup embeddability for
    finite abelian groups and does not depend on how invariant factors are
    padded or displayed.
    """
    target = tuple(int(value) for value in target_invariants if int(value) > 1)
    ambient = tuple(int(value) for value in ambient_invariants if int(value) > 1)
    if not target:
        return True

    target_order = reduce(mul, target, 1)
    ambient_order = reduce(mul, ambient, 1)
    if ambient_order % target_order != 0:
        return False

    for ell in ZZ(target_order).prime_divisors():
        ell = int(ell)
        target_vals = [int(ZZ(value).valuation(ell)) for value in target]
        ambient_vals = [int(ZZ(value).valuation(ell)) for value in ambient]
        for power in range(1, max(target_vals) + 1):
            target_rank = sum(value >= power for value in target_vals)
            ambient_rank = sum(value >= power for value in ambient_vals)
            if target_rank > ambient_rank:
                return False
    return True


def torsion_mod_p_sieve(
    db,
    *,
    curve_id,
    E,
    torsion_group,
    prime_bound=100,
    max_primes=12,
):
    """Rigorous necessary-condition sieve using good reduction modulo p.

    If p is a good prime not dividing the target torsion order, rational torsion
    injects into E(F_p). The sieve first checks target-order divisibility and,
    when Sage can compute E(F_p)'s exact invariant factors, also checks whether
    the requested finite abelian group can embed. Either failure is a rigorous
    obstruction. Passing remains only a necessary condition, never a proof of
    rational torsion.
    """
    label = canonical_torsion_label(torsion_group)
    invariants = torsion_invariants_from_label(label)
    order = reduce(mul, invariants, 1)
    prime_bound = max(3, int(prime_bound))
    max_primes = max(1, int(max_primes))
    cache_curve_primes(
        db,
        curve_id=curve_id,
        E=E,
        prime_bound=prime_bound,
        include_all_bad=False,
    )
    checked = []
    obstruction = None
    structure_attempts = 0
    structure_failures = 0
    for row in curve_prime_rows(db, curve_id, good_only=True, max_p=prime_bound):
        p = int(row["p"])
        if order > 1 and order % p == 0:
            continue
        n = row["cardinality"]
        if n is None:
            continue

        divisible = (int(n) % int(order) == 0)
        rec = {
            "p": p,
            "cardinality": int(n),
            "target_order": int(order),
            "target_invariants": list(invariants),
            "divisible": divisible,
            "order_divisible": divisible,
            "finite_group_invariants": None,
            "structure_checked": False,
            "structure_compatible": None,
            "structure_error": None,
            "obstruction_kind": None,
        }
        checked.append(rec)

        if not divisible:
            rec["obstruction_kind"] = "order_divisibility"
            obstruction = rec
            break

        if order > 1:
            structure_attempts += 1
            ambient_invariants, structure_error = _finite_group_invariants(E, p)
            if ambient_invariants is None:
                structure_failures += 1
                rec["structure_error"] = structure_error
            else:
                compatible = _finite_abelian_embedding_possible(
                    invariants, ambient_invariants
                )
                rec["finite_group_invariants"] = list(ambient_invariants)
                rec["structure_checked"] = True
                rec["structure_compatible"] = bool(compatible)
                if not compatible:
                    rec["obstruction_kind"] = "group_structure"
                    obstruction = rec
                    break

        if len(checked) >= max_primes:
            break

    sieve_status = (
        "obstructed" if obstruction is not None
        else ("passed" if checked else "inconclusive")
    )
    structure_complete = bool(
        order <= 1
        or (
            structure_attempts > 0
            and structure_failures == 0
            and all(
                rec["structure_checked"]
                for rec in checked
                if rec["order_divisible"]
            )
        )
    )
    return {
        "status": sieve_status,
        "sieve_status": sieve_status,
        "torsion_group": label,
        "target_invariants": list(invariants),
        "target_order": int(order),
        "prime_bound": prime_bound,
        "primes_checked": len(checked),
        "checks": checked,
        "obstruction": obstruction,
        "obstruction_kind": (
            obstruction.get("obstruction_kind") if obstruction is not None else None
        ),
        "rigorous_rejection": obstruction is not None,
        "screening_contract": "finite_group_embedding_when_available",
        "structure_attempts": int(structure_attempts),
        "structure_failures": int(structure_failures),
        "structure_complete": structure_complete,
        "passing_is_proof": False,
    }


def _square_residues(p):
    p = int(p)
    return {int((y * y) % p) for y in range(p)}


def quartic_mod_p_has_projective_point(coefficients, p):
    """Necessary F_p test for y^2=f(x), degree exactly four.

    Returns True/False, or None for unsupported/non-prime input. False is a
    rigorous local obstruction to a Q_p point on this integral quartic model.
    """
    p = int(p)
    if p < 2 or not ZZ(p).is_prime():
        return None
    norm = normalize_polynomial(coefficients)
    if int(norm.degree) != 4:
        return None
    coeffs = [int(x) % p for x in norm.integer_coefficients]
    squares = _square_residues(p)

    # Affine projective classes [x:1].
    for x in range(p):
        value = 0
        power = 1
        for a in coeffs:
            value = (value + a * power) % p
            power = (power * x) % p
        if value in squares:
            return True

    # The remaining projective class [1:0]; for a binary quartic this is the
    # leading coefficient.
    leading = coeffs[4] % p
    return leading in squares


def record_quartic_local_test(db, *, search_id, p, has_point):
    require_prime_schema(db)
    if has_point is None:
        status = "unsupported"
    else:
        status = "not_obstructed" if has_point else "obstructed"
    detail = {
        "test": "projective_mod_p_binary_quartic",
        "one_way": True,
        "has_projective_Fp_point": has_point,
    }
    db.execute(
        """INSERT INTO quartic_local_tests(search_id,p,status,detail_json,computed_at)
           VALUES(?,?,?,?,?)
           ON CONFLICT(search_id,p) DO UPDATE SET
               status=excluded.status,
               detail_json=excluded.detail_json,
               computed_at=excluded.computed_at""",
        (
            int(search_id),
            int(p),
            status,
            json.dumps(detail, sort_keys=True),
            now(),
        ),
    )
    db.commit()
    return status


def first_quartic_local_obstruction(
    db,
    *,
    search_id,
    coefficients,
    primes,
):
    """Return the first rigorous mod-p obstruction, else None."""
    require_prime_schema(db)
    tested = []
    for p in sorted({int(x) for x in primes if int(x) >= 2}):
        cached = db.execute(
            "SELECT * FROM quartic_local_tests WHERE search_id=? AND p=?",
            (int(search_id), int(p)),
        ).fetchone()
        if cached is not None:
            status = str(cached["status"])
            tested.append({"p": p, "status": status, "cached": True})
            if status == "obstructed":
                return {
                    "p": p,
                    "status": "obstructed",
                    "test": "projective_mod_p_binary_quartic",
                    "tested": tested,
                }
            continue
        has_point = quartic_mod_p_has_projective_point(coefficients, p)
        status = record_quartic_local_test(
            db,
            search_id=search_id,
            p=p,
            has_point=has_point,
        )
        tested.append({"p": p, "status": status, "cached": False})
        if status == "obstructed":
            return {
                "p": p,
                "status": "obstructed",
                "test": "projective_mod_p_binary_quartic",
                "tested": tested,
            }
    return None


def local_sieve_prime_set(db, *, curve_id, explicit_primes, include_bad, max_prime):
    max_prime = max(2, int(max_prime))
    values = {
        int(p)
        for p in explicit_primes
        if int(p) >= 2 and int(p) <= max_prime and ZZ(int(p)).is_prime()
    }
    if include_bad:
        for row in curve_prime_rows(db, curve_id):
            if bool(row["good_reduction"]):
                continue
            p = int(row["p"])
            if p <= max_prime:
                values.add(p)
    return tuple(sorted(values))


def sieve_stored_quartics(
    db,
    *,
    curve_id,
    primes,
):
    """Apply the one-way exact local obstruction test to stored degree-4 searches."""
    require_prime_schema(db)
    rows = db.execute(
        """SELECT * FROM quartic_searches
           WHERE curve_id=? AND degree=4
           ORDER BY id""",
        (int(curve_id),),
    ).fetchall()
    obstructed = []
    tested = 0
    for row in rows:
        try:
            coefficients = json.loads(row["polynomial_json"] or "[]")
        except Exception:
            continue
        obstruction = first_quartic_local_obstruction(
            db,
            search_id=int(row["id"]),
            coefficients=coefficients,
            primes=primes,
        )
        tested += 1
        if obstruction is not None:
            obstructed.append(
                {
                    "search_id": int(row["id"]),
                    "p": int(obstruction["p"]),
                    "hole_label": row["hole_label"],
                }
            )
    return {
        "status": "completed",
        "quartics_tested": tested,
        "obstructed": obstructed,
        "obstructed_count": len(obstructed),
        "primes": [int(p) for p in primes],
        "passing_is_proof_of_local_solubility": False,
    }
