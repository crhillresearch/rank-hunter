"""Exact point-centred quartic search for high-rank escalation.

For a Weierstrass curve

    y^2 + a1*x*y + a3*y = x^3 + a2*x^2 + a4*x + a6

write V = 2*y + a1*x + a3. Projection through a known rational point
P=(x0,y0) gives an exact quartic in the line slope t. Rational quartic
points map back to E(Q) by exact formulas and are verified again by Sage
before entering Rank Hunter's point ledger.

The underlying projection is classical. The bounded/diverse anchor-portfolio
strategy is inspired by Valery Asiryan's MIT-licensed elliptic-rank-search
project (2025-2026); Rank Hunter's implementation is independent and uses its
own ratpoints, persistence, campaign, and exact-certificate machinery.
"""
from __future__ import annotations

import hashlib
import json
import random
import subprocess
import sys
import uuid
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace


REDUCTION_WORKER = Path(__file__).with_name("pointed_quartic_reduce_worker.py")
REDUCTION_MARKER = "RANK42_POINTED_REDUCTION="


def _q(value):
    return value if isinstance(value, Fraction) else Fraction(str(value))


def _ainvs(values):
    result = tuple(_q(x) for x in values)
    if len(result) != 5:
        raise ValueError("expected five Weierstrass a-invariants")
    return result


def _point(values):
    if values is None or len(values) < 2:
        raise ValueError("expected affine [x,y] point")
    return _q(values[0]), _q(values[1])


def on_curve(a_invariants, point):
    a1, a2, a3, a4, a6 = _ainvs(a_invariants)
    x, y = _point(point)
    return y * y + a1 * x * y + a3 * y == x**3 + a2 * x * x + a4 * x + a6


def completed_v(a_invariants, point):
    a1, _a2, a3, _a4, _a6 = _ainvs(a_invariants)
    x, y = _point(point)
    return 2 * y + a1 * x + a3


def pointed_quartic_coefficients(a_invariants, anchor):
    """Return a0..a4 for the exact pointed quartic z^2=D_P(t)."""
    a1, a2, a3, a4, a6 = _ainvs(a_invariants)
    x0, y0 = _point(anchor)
    if not on_curve((a1, a2, a3, a4, a6), (x0, y0)):
        raise ValueError("anchor is not on the curve")
    b2 = a1 * a1 + 4 * a2
    b4 = a1 * a3 + 2 * a4
    v0 = 2 * y0 + a1 * x0 + a3
    return (
        b2 * b2 - 8 * b2 * x0 - 48 * x0 * x0 - 32 * b4,
        32 * v0,
        -2 * (12 * x0 + b2),
        Fraction(0),
        Fraction(1),
    )


def evaluate_polynomial(coefficients, x):
    x = _q(x)
    total = Fraction(0)
    for coefficient in reversed(tuple(_q(c) for c in coefficients)):
        total = total * x + coefficient
    return total


def completed_square_polynomial(f_coefficients, q_coefficients):
    """Return coefficients of W^2=q(x)^2+4*f(x)."""
    f = [_q(x) for x in f_coefficients]
    q = [_q(x) for x in q_coefficients]
    out = [Fraction(0)] * max(len(f), 2 * len(q) - 1)
    for index, value in enumerate(f):
        out[index] += 4 * value
    for i, left in enumerate(q):
        for j, right in enumerate(q):
            out[i + j] += left * right
    while len(out) > 1 and out[-1] == 0:
        out.pop()
    return tuple(out)


def _raw_search_model(model):
    result = dict(model)
    result["search_coefficients"] = tuple(model["coefficients"])
    result["reduction"] = None
    result["model_key"] = hashlib.sha256(
        json.dumps(
            {
                "anchor": model["anchor_key"],
                "kind": "raw",
                "coefficients": [str(x) for x in model["coefficients"]],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return result


def prepare_reduced_models(models, *, timeout=20):
    """Reduce pointed quartics in one isolated Sage/PARI worker.

    Completed reductions are kept even if the batch later times out. Any model
    not returned with an exact checked transform falls back to its raw quartic.
    """
    models = list(models)
    if not models or int(timeout) <= 0:
        return [_raw_search_model(model) for model in models]
    payload = {
        "models": [
            {
                "key": model["anchor_key"],
                "coefficients": [str(x) for x in model["coefficients"]],
            }
            for model in models
        ]
    }
    stdout = ""
    try:
        cp = subprocess.run(
            [sys.executable, str(REDUCTION_WORKER)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=max(1, int(timeout)),
            check=False,
        )
        stdout = cp.stdout or ""
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")

    reductions = {}
    for line in stdout.splitlines():
        if not line.startswith(REDUCTION_MARKER):
            continue
        try:
            record = json.loads(line[len(REDUCTION_MARKER):])
        except Exception:
            continue
        if record.get("status") == "completed" and record.get("key"):
            reductions[str(record["key"])] = record

    prepared = []
    for model in models:
        record = reductions.get(model["anchor_key"])
        if record is None:
            prepared.append(_raw_search_model(model))
            continue
        f_coefficients = tuple(_q(x) for x in record["f"])
        q_coefficients = tuple(_q(x) for x in record["q"])
        result = dict(model)
        result["reduction"] = record
        result["search_coefficients"] = completed_square_polynomial(
            f_coefficients, q_coefficients
        )
        result["model_key"] = hashlib.sha256(
            json.dumps(
                {
                    "anchor": model["anchor_key"],
                    "kind": "pari-hyperellred",
                    "scale": record["scale"],
                    "f": record["f"],
                    "q": record["q"],
                    "transform": record["transform"],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        prepared.append(result)
    return prepared


def map_pointed_quartic_point(a_invariants, anchor, t, z):
    """Map one exact affine point on z^2=D_P(t) back to E(Q)."""
    a1, a2, a3, a4, a6 = _ainvs(a_invariants)
    x0, y0 = _point(anchor)
    t, z = _q(t), _q(z)
    coeffs = pointed_quartic_coefficients((a1, a2, a3, a4, a6), (x0, y0))
    if z * z != evaluate_polynomial(coeffs, t):
        raise ValueError("point is not on the pointed quartic")
    b2 = a1 * a1 + 4 * a2
    v0 = 2 * y0 + a1 * x0 + a3
    x = (t * t - b2 - 4 * x0 + z) / 8
    y = (v0 + t * (x - x0) - a1 * x - a3) / 2
    if not on_curve((a1, a2, a3, a4, a6), (x, y)):
        raise ValueError("pointed quartic inverse map missed the elliptic curve")
    return x, y


def project_curve_point(a_invariants, anchor, point):
    """Project Q to the pointed quartic; P,-P and O are intentionally excluded."""
    a1, a2, a3, a4, a6 = _ainvs(a_invariants)
    x0, y0 = _point(anchor)
    x, y = _point(point)
    if x == x0:
        raise ValueError("projection slope is undefined when x(Q)=x(P)")
    v0 = 2 * y0 + a1 * x0 + a3
    v = 2 * y + a1 * x + a3
    t = (v - v0) / (x - x0)
    b2 = a1 * a1 + 4 * a2
    z = 8 * x - t * t + b2 + 4 * x0
    coeffs = pointed_quartic_coefficients((a1, a2, a3, a4, a6), (x0, y0))
    if z * z != evaluate_polynomial(coeffs, t):
        raise ValueError("elliptic point failed pointed-quartic projection identity")
    return t, z


def _fraction_bits(value):
    value = _q(value)
    return max(
        max(1, abs(int(value.numerator)).bit_length()),
        max(1, int(value.denominator).bit_length()),
    )


def quartic_complexity(coefficients):
    values = [_fraction_bits(c) for c in coefficients]
    return (max(values), sum(values))


def anchor_fingerprint(a_invariants, anchor):
    payload = {
        "a": [str(_q(x)) for x in a_invariants],
        "p": [str(_q(x)) for x in anchor],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _canonical_sage_point(E, P):
    if P is None or P.is_zero():
        return None
    a1, _a2, a3, _a4, _a6 = E.a_invariants()
    v = 2 * P[1] + a1 * P[0] + a3
    if v < 0:
        return -P
    if v == 0:
        Q = -P
        left = (str(P[0]), str(P[1]))
        right = (str(Q[0]), str(Q[1]))
        return P if left <= right else Q
    return P


def _sage_point_key(E, P):
    P = _canonical_sage_point(E, P)
    if P is None:
        return None
    return str(P[0]), str(P[1])


def _combine(E, basis, vector):
    R = E(0)
    for coefficient, P in zip(vector, basis):
        if coefficient:
            R += int(coefficient) * P
    return R


def build_anchor_pool(
    E,
    basis,
    observations,
    *,
    limit=32,
    pool_size=256,
    preferred_vectors=(),
):
    """Build a deterministic diverse portfolio of exact point anchors.

    preferred_vectors lets a family inject mathematically meaningful Mordell-Weil
    combinations (for example deep half-lattice-hole representatives).  These
    remain search heuristics only; all resulting points still pass exact mapping
    and independence certification.
    """
    limit = max(1, int(limit))
    pool_size = max(limit, int(pool_size))
    basis = [E(P) for P in basis if not P.is_zero()]
    observations = [E(P) for P in observations if not P.is_zero()]
    rank = len(basis)
    candidates = {}

    def add(P, source, vector=None, metadata=None):
        if len(candidates) >= pool_size:
            return
        P = _canonical_sage_point(E, E(P))
        if P is None:
            return
        key = _sage_point_key(E, P)
        if key in candidates:
            old = candidates[key]
            if old["vector"] is None and source == "observed":
                old["source"] = "observed"
            return
        anchor = [str(P[0]), str(P[1])]
        coefficients = pointed_quartic_coefficients(E.a_invariants(), anchor)
        support = sum(1 for value in vector if value) if vector is not None else 0
        candidates[key] = {
            "point": P,
            "anchor": anchor,
            "anchor_key": anchor_fingerprint(E.a_invariants(), anchor),
            "source": str(source),
            "vector": list(vector) if vector is not None else None,
            "support": int(support),
            "coefficients": coefficients,
            "complexity": quartic_complexity(coefficients),
            "metadata": dict(metadata or {}),
        }

    preferred_cap = max(0, min(len(tuple(preferred_vectors)), max(0, pool_size - rank)))
    for record in tuple(preferred_vectors)[:preferred_cap]:
        if isinstance(record, dict):
            vector = list(record.get("vector") or ())
            metadata = {k: v for k, v in record.items() if k != "vector"}
        else:
            vector = list(record)
            metadata = {}
        if len(vector) != rank:
            continue
        try:
            P = _combine(E, basis, vector)
        except Exception:
            continue
        add(P, "lattice_hole", vector, metadata)

    for index, P in enumerate(basis):
        vector = [0] * rank
        vector[index] = 1
        add(P, "basis", vector)
    for P in observations:
        add(P, "observed")

    for i in range(rank):
        for j in range(i + 1, rank):
            if len(candidates) >= pool_size:
                break
            plus = [0] * rank
            plus[i] = plus[j] = 1
            add(basis[i] + basis[j], "pair", plus)
            if len(candidates) >= pool_size:
                break
            minus = [0] * rank
            minus[i] = 1
            minus[j] = -1
            add(basis[i] - basis[j], "pair", minus)
        if len(candidates) >= pool_size:
            break

    if rank >= 3 and len(candidates) < pool_size:
        seed_material = "|".join(str(x) for x in E.a_invariants())
        seed = int(hashlib.sha256(seed_material.encode()).hexdigest()[:16], 16)
        rng = random.Random(seed)
        supports = list(range(3, min(8, rank) + 1))
        attempts = 0
        attempt_limit = max(64, pool_size * 16)
        while len(candidates) < pool_size and attempts < attempt_limit:
            support = supports[attempts % len(supports)]
            attempts += 1
            vector = [0] * rank
            for index in rng.sample(range(rank), support):
                vector[index] = rng.choice((-1, 1))
            first = next((v for v in vector if v), 1)
            if first < 0:
                vector = [-v for v in vector]
            add(_combine(E, basis, vector), "wide", vector)

    rows = sorted(
        candidates.values(),
        key=lambda rec: (
            rec["complexity"],
            0 if rec["source"] == "observed" else 1,
            rec["support"],
            rec["anchor_key"],
        ),
    )
    if len(rows) <= limit:
        return rows

    selected = []
    selected_keys = set()

    def take(rec):
        if rec["anchor_key"] in selected_keys or len(selected) >= limit:
            return
        selected_keys.add(rec["anchor_key"])
        selected.append(rec)

    observed_rows = [row for row in rows if row["source"] == "observed"]
    for row in observed_rows[: max(2, limit // 4)]:
        take(row)

    lattice_rows = [row for row in rows if row["source"] == "lattice_hole"]
    lattice_rows.sort(
        key=lambda rec: (
            -float(rec.get("metadata", {}).get("half_lattice_norm") or 0.0),
            rec["complexity"],
            rec["support"],
            rec["anchor_key"],
        )
    )
    for row in lattice_rows[: max(1, limit // 2)]:
        take(row)

    for row in rows:
        if len(selected) >= max(1, limit // 2):
            break
        take(row)

    groups = {}
    for row in rows:
        groups.setdefault(row["support"], []).append(row)
    support_order = sorted(groups, key=lambda s: (s == 0, s))
    offsets = {support: 0 for support in support_order}
    while len(selected) < limit:
        progressed = False
        for support in support_order:
            group = groups[support]
            index = offsets[support]
            while index < len(group) and group[index]["anchor_key"] in selected_keys:
                index += 1
            offsets[support] = index + 1
            if index < len(group):
                take(group[index])
                progressed = True
            if len(selected) >= limit:
                break
        if not progressed:
            break
    return selected


def _load_exact_observations(db, curve_id, E, *, limit=512):
    rows = db.execute(
        """
        SELECT x,y,id FROM points
        WHERE curve_id=? AND exact_verified=1
        ORDER BY rigorous_independent DESC,id ASC
        LIMIT ?
        """,
        (int(curve_id), int(limit)),
    ).fetchall()
    out = []
    seen = set()
    for row in rows:
        try:
            P = E(row["x"], row["y"])
        except Exception:
            continue
        key = _sage_point_key(E, P)
        if key is None or key in seen:
            continue
        seen.add(key)
        out.append(P)
    return out


def _existing_exact_keys(db, curve_id, E):
    return {
        _sage_point_key(E, P)
        for P in _load_exact_observations(db, curve_id, E, limit=100000)
    }


def _pointed_search_progress(
    *,
    round_index,
    rounds,
    height_index,
    height_count,
    model_index,
    wave_count,
    height,
    anchor_key,
    timeout,
    cached=False,
):
    source = "cached" if cached else f"timeout≤{int(timeout)}s"
    return (
        "[auto:pointed-quartic] search "
        f"round={int(round_index)}/{int(rounds)} "
        f"height={int(height_index)}/{int(height_count)} H={int(height)} "
        f"model={int(model_index)}/{int(wave_count)} "
        f"anchor={str(anchor_key)[:10]} {source}"
    )


def _map_hits(E, model, hits, *, search_id=None):
    mapped = []
    failures = []
    seen = set()
    reduction = model.get("reduction")
    for hit in hits:
        for square_y in (hit.y, -hit.y):
            try:
                if reduction is None:
                    t = _q(hit.x)
                    z = _q(square_y)
                else:
                    u = _q(hit.x)
                    q_value = evaluate_polynomial(reduction["q"], u)
                    reduced_y = (_q(square_y) - q_value) / 2
                    transform = reduction["transform"]
                    e = _q(transform["e"])
                    a, b, c, d = (_q(x) for x in transform["matrix"])
                    h = tuple(_q(x) for x in transform["h"])
                    denominator = c * u + d
                    if denominator == 0:
                        raise ZeroDivisionError(
                            "quartic reduction map denominator is zero"
                        )
                    t = (a * u + b) / denominator
                    old_square_y = (
                        e * reduced_y + evaluate_polynomial(h, u)
                    ) / (denominator * denominator)
                    z = old_square_y / _q(reduction["scale"])
                x, y = map_pointed_quartic_point(
                    E.a_invariants(), model["anchor"], t, z
                )
                P = E(str(x), str(y))
                key = _sage_point_key(E, P)
                if key is None:
                    raise ValueError("mapped point has no affine Sage key")
            except Exception as exc:
                failures.append({
                    "quartic_search_id": (
                        None if search_id is None else int(search_id)
                    ),
                    "anchor_key": str(model.get("anchor_key") or ""),
                    "model_key": str(model.get("model_key") or ""),
                    "hit": {
                        "x": str(hit.x),
                        "y": str(hit.y),
                        "square_y": str(square_y),
                    },
                    "reduction_transform": (
                        None
                        if reduction is None
                        else reduction.get("transform")
                    ),
                    "error_class": exc.__class__.__name__,
                    "error": str(exc)[:500],
                })
                continue
            if key in seen:
                continue
            seen.add(key)
            mapped.append(P)
    return mapped, failures


def pointed_quartic_coverage_summary(
    *,
    planned_searches,
    completed_searches,
    cached_completions,
    timeouts,
    errors,
    local_obstructions,
    new_exact_points=0,
    dry_round_min_coverage=1.0,
):
    """Reduce one pointed-quartic search wave to honest coverage semantics."""
    planned = max(0, int(planned_searches))
    completed = max(0, int(completed_searches))
    cached = max(0, int(cached_completions))
    timed_out = max(0, int(timeouts))
    failed = max(0, int(errors))
    obstructed = max(0, int(local_obstructions))
    resolved = completed + cached + obstructed
    coverage = float(resolved) / float(planned) if planned else 0.0
    threshold = float(dry_round_min_coverage)
    if not (0.0 < threshold <= 1.0):
        raise ValueError("dry_round_min_coverage must be in (0,1]")

    if planned == 0:
        status = "inconclusive"
    elif timed_out == 0 and failed == 0 and resolved == planned:
        status = "completed"
    elif resolved > 0:
        status = "partial"
    elif timed_out > 0 and failed == 0:
        status = "timeout"
    elif failed > 0 and timed_out == 0:
        status = "error"
    else:
        status = "inconclusive"

    dry_completed = bool(
        planned > 0
        and int(new_exact_points or 0) == 0
        and coverage >= threshold
    )
    return {
        "status": status,
        "planned_searches": planned,
        "completed_searches": completed,
        "cached_completions": cached,
        "timeouts": timed_out,
        "errors": failed,
        "local_obstructions": obstructed,
        "resolved_searches": resolved,
        "incomplete_searches": max(0, planned - resolved),
        "coverage_fraction": coverage,
        "dry_round_min_coverage": threshold,
        "dry_completed": dry_completed,
        "new_exact_points": int(new_exact_points or 0),
    }


def run_pointed_quartic_escalation(
    db,
    *,
    curve_id,
    E,
    heights,
    anchors=32,
    pool_size=256,
    rounds=2,
    deep_keep=8,
    timeout=8,
    reduce_timeout=20,
    executable=None,
    certificate_timeout=120,
    exact_candidates=64,
    campaign_id=None,
    parameter=None,
    pipeline_run_id=None,
    pipeline_candidate_id=None,
    pipeline_stage_index=None,
    pipeline_stage_id=None,
    target_rank=None,
    local_sieve_primes=(),
    preferred_anchor_vectors=(),
    dry_round_min_coverage=1.0,
):
    """Search a feedback portfolio of exact point-centred quartics."""
    from rank42.auto_point_search import certify_ledger_growth
    from rank42.auto_search_state import upsert_step
    from rank42.curve_research_state import get_curve_research_state
    from rank42.db import get_curve, log_event
    from rank42.points import record_point_discovery, rigorous_witness_basis, upsert_point
    from rank42.prime_local import first_quartic_local_obstruction
    from rank42.quartic_store import (
        create_or_get_search,
        finish_search,
        mark_running,
        store_points,
        record_map_back_failures,
    )
    from rank42.ratpoints import (
        RatpointsFailure,
        RatpointsNotFound,
        RatpointsTimeout,
        normalize_polynomial,
        resolve_executable,
        run_ratpoints,
    )

    def current_rigorous_lower():
        state = get_curve_research_state(db, int(curve_id))
        return int(state["rigorous_lower"])

    basis, required, complete = rigorous_witness_basis(db, curve_id, E)
    if not complete or not basis:
        return {
            "scheduler_tier": "pointed-quartic",
            "tier": "pointed-quartic",
            "status": "inconclusive",
            "reason": "rigorous witness basis incomplete",
            "known_basis": len(basis),
            "required_basis": int(required),
            "models": 0,
            "searches": 0,
            "timeouts": 0,
            "failures": 0,
            "exact_points": 0,
            "rank_growth": 0,
            "rigorous_lower": current_rigorous_lower(),
        }

    heights = tuple(sorted({max(1, int(h)) for h in heights}))
    if not heights:
        raise ValueError("pointed quartic search requires at least one height")
    anchors = max(1, int(anchors))
    pool_size = max(anchors, int(pool_size))
    rounds = max(1, int(rounds))
    deep_keep = max(1, int(deep_keep))
    timeout = max(1, int(timeout))
    reduce_timeout = max(0, int(reduce_timeout))
    dry_round_min_coverage = float(dry_round_min_coverage)
    if not (0.0 < dry_round_min_coverage <= 1.0):
        raise ValueError("dry_round_min_coverage must be in (0,1]")
    target_rank = None if target_rank is None else int(target_rank)
    before_lower = current_rigorous_lower()
    known_keys = _existing_exact_keys(db, curve_id, E)
    total_new = 0
    total_searches = 0
    total_planned = 0
    total_completed = 0
    total_cached_completions = 0
    total_timeouts = 0
    total_failures = 0
    total_local_obstructions = 0
    total_map_back_failures = 0
    map_back_failure_samples = []
    total_models = 0
    round_summaries = []
    searched_models = set()
    stop_reason = "max_rounds"

    try:
        exe = resolve_executable(executable)
    except RatpointsNotFound as exc:
        return {
            "scheduler_tier": "pointed-quartic",
            "tier": "pointed-quartic",
            "status": "error",
            "reason": str(exc),
            "known_basis": len(basis),
            "models": 0,
            "searches": 0,
            "timeouts": 0,
            "failures": 1,
            "exact_points": 0,
            "rank_growth": 0,
            "rigorous_lower": before_lower,
        }

    for round_index in range(1, rounds + 1):
        observations = _load_exact_observations(db, curve_id, E)
        portfolio = build_anchor_pool(
            E,
            basis,
            observations,
            limit=anchors,
            pool_size=pool_size,
            preferred_vectors=preferred_anchor_vectors,
        )
        portfolio = [
            model for model in portfolio
            if model["anchor_key"] not in searched_models
        ]
        if not portfolio:
            stop_reason = "no_new_models"
            break
        portfolio = prepare_reduced_models(
            portfolio,
            timeout=reduce_timeout,
        )
        total_models += len(portfolio)
        round_new = 0
        round_searches = 0
        round_planned = 0
        round_completed = 0
        round_cached_completions = 0
        round_timeouts = 0
        round_failures = 0
        round_local_obstructions = 0
        round_map_back_failures = 0
        reduced_count = sum(1 for model in portfolio if model.get("reduction") is not None)
        lattice_anchor_count = sum(
            1 for model in portfolio if model.get("source") == "lattice_hole"
        )

        print(
            f"[auto:pointed-quartic] round={round_index} "
            f"basis={len(basis)} observations={len(observations)} "
            f"anchors={len(portfolio)} lattice_holes={lattice_anchor_count} "
            f"reduced={reduced_count} "
            f"heights={','.join(str(h) for h in heights)}",
            flush=True,
        )

        promoted_models = set()
        for height_index, height in enumerate(heights):
            if height_index == 0:
                wave = list(portfolio)
            else:
                ordered = sorted(
                    portfolio,
                    key=lambda model: (
                        quartic_complexity(model["search_coefficients"]),
                        model["anchor_key"],
                    ),
                )
                promoted = [
                    model for model in ordered
                    if model["anchor_key"] in promoted_models
                ]
                fallback = ordered[:deep_keep]
                seen_wave = set()
                wave = []
                for model in [*promoted, *fallback]:
                    if model["anchor_key"] in seen_wave:
                        continue
                    seen_wave.add(model["anchor_key"])
                    wave.append(model)
            for model_index, model in enumerate(wave, 1):
                if target_rank is not None:
                    current_lower = current_rigorous_lower()
                    if current_lower >= target_rank:
                        break

                step_tier = f"pointed-quartic:r{round_index}"
                # Durable quartic-search state below is the search-result cache.
                # Prior timeout/error campaign steps are intentionally retried;
                # only a durable done/local-obstruction search is resolved.
                round_planned += 1
                total_planned += 1

                norm = normalize_polynomial(model["search_coefficients"])
                curve_row = get_curve(db, curve_id)
                qsearch = create_or_get_search(
                    db,
                    curve_id=curve_id,
                    family=curve_row["family"] if curve_row is not None else None,
                    parameter=parameter,
                    hole_label=f"pointed:{model['anchor_key'][:12]}",
                    coefficients=norm.rational_coefficients,
                    integer_coefficients=norm.integer_coefficients,
                    y_scale=norm.y_scale,
                    degree=norm.degree,
                    height_bound=height,
                    metadata={
                        "schema": "rank42.pointed_quartic.v1",
                        "anchor": model["anchor"],
                        "anchor_key": model["anchor_key"],
                        "anchor_source": model["source"],
                        "anchor_support": model["support"],
                        "anchor_vector": model["vector"],
                        "complexity": list(model["complexity"]),
                        "search_complexity": list(quartic_complexity(model["search_coefficients"])),
                        "round": round_index,
                        "reduction": model.get("reduction"),
                        "map": {
                            "type": "classical_point_projection",
                            "verified_exactly_on_map_back": True,
                        },
                    },
                )

                if str(qsearch["status"]) == "locally_obstructed":
                    total_local_obstructions += 1
                    round_local_obstructions += 1
                    if campaign_id is not None and parameter is not None:
                        upsert_step(
                            db,
                            campaign_id=campaign_id,
                            parameter=parameter,
                            tier=step_tier,
                            center=model["anchor_key"],
                            scale=model["model_key"],
                            height=height,
                            status="completed",
                            exact_points=0,
                            error="cached rigorous local obstruction",
                        )
                    continue

                obstruction = None
                if local_sieve_primes:
                    obstruction = first_quartic_local_obstruction(
                        db,
                        search_id=int(qsearch["id"]),
                        coefficients=model["search_coefficients"],
                        primes=local_sieve_primes,
                    )
                if obstruction is not None:
                    total_local_obstructions += 1
                    round_local_obstructions += 1
                    finish_search(
                        db,
                        qsearch["id"],
                        status="locally_obstructed",
                        error=(
                            "rigorous projective mod-p obstruction at "
                            f"p={int(obstruction['p'])}"
                        ),
                        point_count=0,
                    )
                    if campaign_id is not None and parameter is not None:
                        upsert_step(
                            db,
                            campaign_id=campaign_id,
                            parameter=parameter,
                            tier=step_tier,
                            center=model["anchor_key"],
                            scale=model["model_key"],
                            height=height,
                            status="completed",
                            exact_points=0,
                            error=(
                                "locally obstructed at "
                                f"p={int(obstruction['p'])}"
                            ),
                        )
                    print(
                        f"[auto:pointed-quartic] local obstruction "
                        f"p={int(obstruction['p'])} "
                        f"anchor={model['anchor_key'][:10]} H={height}; "
                        "ratpoints skipped",
                        flush=True,
                    )
                    continue

                if (
                    campaign_id is not None
                    and parameter is not None
                    and str(qsearch["status"]) != "done"
                ):
                    upsert_step(
                        db,
                        campaign_id=campaign_id,
                        parameter=parameter,
                        tier=step_tier,
                        center=model["anchor_key"],
                        scale=model["model_key"],
                        height=height,
                        status="running",
                    )

                total_searches += 1
                round_searches += 1
                print(
                    _pointed_search_progress(
                        round_index=round_index,
                        rounds=rounds,
                        height_index=height_index + 1,
                        height_count=len(heights),
                        model_index=model_index,
                        wave_count=len(wave),
                        height=height,
                        anchor_key=model["anchor_key"],
                        timeout=timeout,
                        cached=str(qsearch["status"]) == "done",
                    ),
                    flush=True,
                )
                quartic_attempt_id = None
                try:
                    if str(qsearch["status"]) == "done":
                        round_cached_completions += 1
                        total_cached_completions += 1
                        cached_rows = db.execute(
                            "SELECT x,y FROM quartic_points WHERE search_id=? ORDER BY id",
                            (int(qsearch["id"]),),
                        ).fetchall()
                        result = {
                            "points": [
                                SimpleNamespace(x=_q(row["x"]), y=_q(row["y"]))
                                for row in cached_rows
                            ],
                            "runtime": 0.0,
                            "cached": True,
                        }
                    else:
                        quartic_attempt_id = (
                            f"quartic:{int(qsearch['id'])}:{uuid.uuid4().hex}"
                        )
                        mark_running(db, qsearch["id"], executable=exe)
                        result = run_ratpoints(
                            model["search_coefficients"],
                            height,
                            executable=exe,
                            timeout=timeout,
                        )
                        round_completed += 1
                        total_completed += 1
                        store_points(db, qsearch["id"], result["points"])
                        finish_search(
                            db,
                            qsearch["id"],
                            status="done",
                            runtime=result["runtime"],
                            point_count=len(result["points"]),
                        )
                except RatpointsTimeout as exc:
                    total_timeouts += 1
                    round_timeouts += 1
                    print(
                        f"[auto:pointed-quartic] timeout "
                        f"round={round_index}/{rounds} "
                        f"height={height_index + 1}/{len(heights)} H={height} "
                        f"model={model_index}/{len(wave)} "
                        f"anchor={model['anchor_key'][:10]}",
                        flush=True,
                    )
                    finish_search(db, qsearch["id"], status="timeout", error=str(exc))
                    if campaign_id is not None and parameter is not None:
                        upsert_step(
                            db,
                            campaign_id=campaign_id,
                            parameter=parameter,
                            tier=step_tier,
                            center=model["anchor_key"],
                            scale=model["model_key"],
                            height=height,
                            status="timeout",
                            error=str(exc),
                        )
                    continue
                except (RatpointsFailure, RatpointsNotFound) as exc:
                    total_failures += 1
                    round_failures += 1
                    print(
                        f"[auto:pointed-quartic] error "
                        f"round={round_index}/{rounds} "
                        f"height={height_index + 1}/{len(heights)} H={height} "
                        f"model={model_index}/{len(wave)} "
                        f"anchor={model['anchor_key'][:10]}: {exc}",
                        flush=True,
                    )
                    finish_search(db, qsearch["id"], status="error", error=str(exc))
                    if campaign_id is not None and parameter is not None:
                        upsert_step(
                            db,
                            campaign_id=campaign_id,
                            parameter=parameter,
                            tier=step_tier,
                            center=model["anchor_key"],
                            scale=model["model_key"],
                            height=height,
                            status="error",
                            error=str(exc),
                        )
                    continue

                print(
                    f"[auto:pointed-quartic] done "
                    f"round={round_index}/{rounds} "
                    f"height={height_index + 1}/{len(heights)} H={height} "
                    f"model={model_index}/{len(wave)} "
                    f"anchor={model['anchor_key'][:10]} "
                    f"quartic_hits={len(result['points'])} "
                    f"runtime={float(result.get('runtime') or 0.0):.2f}s",
                    flush=True,
                )
                mapped, map_failures = _map_hits(
                    E,
                    model,
                    result["points"],
                    search_id=int(qsearch["id"]),
                )
                if map_failures:
                    round_map_back_failures += len(map_failures)
                    total_map_back_failures += len(map_failures)
                    persisted = record_map_back_failures(
                        db,
                        int(qsearch["id"]),
                        map_failures,
                    )
                    for failure in map_failures:
                        cached_replay = bool(result.get("cached"))
                        record_point_discovery(
                            db,
                            curve_id=curve_id,
                            source="auto_pointed_quartic",
                            outcome=(
                                "cached_map_failure"
                                if cached_replay
                                else "map_failure"
                            ),
                            point_id=None,
                            search_ref=f"quartic:{int(qsearch['id'])}",
                            campaign_id=campaign_id,
                            parameter=parameter,
                            pipeline_run_id=pipeline_run_id,
                            pipeline_candidate_id=pipeline_candidate_id,
                            pipeline_stage_index=pipeline_stage_index,
                            pipeline_stage_id=pipeline_stage_id,
                            height=int(height),
                            requested_denominator_low=qsearch["denominator_low"],
                            requested_denominator_high=qsearch["denominator_high"],
                            exact_verified=False,
                            error_class=failure.get("error_class"),
                            error=failure.get("error"),
                            metadata={
                                "quartic_search_id": int(qsearch["id"]),
                                "quartic_attempt_id": quartic_attempt_id,
                                "cached_replay": cached_replay,
                                "historical_origin_unresolved": cached_replay,
                                "anchor": model["anchor"],
                                "anchor_key": model["anchor_key"],
                                "model_key": model["model_key"],
                                "round": int(round_index),
                                "hit": failure.get("hit"),
                                "reduction_transform": failure.get(
                                    "reduction_transform"
                                ),
                            },
                        )
                    for sample in persisted.get("samples") or []:
                        if len(map_back_failure_samples) >= 16:
                            break
                        if sample not in map_back_failure_samples:
                            map_back_failure_samples.append(sample)
                new_here = 0
                for P in mapped:
                    key = _sage_point_key(E, P)
                    existing = db.execute(
                        "SELECT * FROM points WHERE curve_id=? AND x=? AND y=?",
                        (int(curve_id), str(P[0]), str(P[1])),
                    ).fetchone()
                    preexisting_point = key in known_keys or existing is not None

                    point_row = existing
                    if not preexisting_point:
                        known_keys.add(key)
                        new_here += 1
                        total_new += 1
                        round_new += 1
                        point_row = upsert_point(
                            db,
                            curve_id=curve_id,
                            x=P[0],
                            y=P[1],
                            source="auto_pointed_quartic",
                            role="candidate_extra",
                            exact_verified=True,
                            independence_status="unknown",
                            rigorous_independent=False,
                            search_ref=f"quartic:{int(qsearch['id'])}",
                            metadata={
                                "auto_search": True,
                                "pointed_quartic": True,
                                "quartic_search_id": int(qsearch["id"]),
                                "anchor": model["anchor"],
                                "anchor_key": model["anchor_key"],
                                "height": int(height),
                                "round": int(round_index),
                            },
                        )

                    cached_replay = bool(result.get("cached"))
                    if not (cached_replay and preexisting_point):
                        record_point_discovery(
                            db,
                            curve_id=curve_id,
                            source="auto_pointed_quartic",
                            outcome=(
                                "cached_mapping"
                                if cached_replay
                                else (
                                    "rediscovered"
                                    if preexisting_point
                                    else "new_point"
                                )
                            ),
                        point_id=(
                            None
                            if point_row is None
                            else int(point_row["id"])
                        ),
                        search_ref=f"quartic:{int(qsearch['id'])}",
                        campaign_id=campaign_id,
                        parameter=parameter,
                        pipeline_run_id=pipeline_run_id,
                        pipeline_candidate_id=pipeline_candidate_id,
                        pipeline_stage_index=pipeline_stage_index,
                        pipeline_stage_id=pipeline_stage_id,
                        height=int(height),
                        requested_denominator_low=qsearch["denominator_low"],
                        requested_denominator_high=qsearch["denominator_high"],
                        stored_x=P[0],
                        stored_y=P[1],
                        exact_verified=True,
                            metadata={
                                "quartic_search_id": int(qsearch["id"]),
                                "quartic_attempt_id": quartic_attempt_id,
                                "cached_replay": cached_replay,
                                "historical_origin_unresolved": cached_replay,
                                "preexisting_point": bool(preexisting_point),
                                "anchor": model["anchor"],
                                "anchor_key": model["anchor_key"],
                                "model_key": model["model_key"],
                                "height": int(height),
                                "round": int(round_index),
                            },
                        )

                if campaign_id is not None and parameter is not None:
                    upsert_step(
                        db,
                        campaign_id=campaign_id,
                        parameter=parameter,
                        tier=step_tier,
                        center=model["anchor_key"],
                        scale=model["model_key"],
                        height=height,
                        status="completed",
                        exact_points=new_here,
                        runtime=result["runtime"],
                    )

                if new_here:
                    promoted_models.add(model["anchor_key"])
                    cert = certify_ledger_growth(
                        db,
                        curve_id=curve_id,
                        E=E,
                        source="auto_pointed_quartic",
                        search_ref=f"quartic:{int(qsearch['id'])}",
                        certificate_timeout=certificate_timeout,
                        max_candidates=exact_candidates,
                    )
                    basis, required, complete = rigorous_witness_basis(db, curve_id, E)
                    current_lower = current_rigorous_lower()
                    print(
                        f"[auto:pointed-quartic] anchor={model['anchor_key'][:10]} "
                        f"H={height} new={new_here} cert_growth={cert.get('growth', 0)} "
                        f"rank>={current_lower}",
                        flush=True,
                    )
                    if target_rank is not None and current_lower >= target_rank:
                        break
            if target_rank is not None and current_rigorous_lower() >= target_rank:
                break

        searched_models.update(model["anchor_key"] for model in portfolio)
        round_summary = pointed_quartic_coverage_summary(
            planned_searches=round_planned,
            completed_searches=round_completed,
            cached_completions=round_cached_completions,
            timeouts=round_timeouts,
            errors=round_failures,
            local_obstructions=round_local_obstructions,
            new_exact_points=round_new,
            dry_round_min_coverage=dry_round_min_coverage,
        )
        round_summary.update({
            "round": round_index,
            "models": len(portfolio),
            "searches": round_searches,
            "new_exact_points": round_new,
            "reduced_models": reduced_count,
            "map_back_failures": round_map_back_failures,
        })
        round_summaries.append(round_summary)

        if target_rank is not None and current_rigorous_lower() >= target_rank:
            stop_reason = "rank_goal"
            break
        if round_summary["dry_completed"]:
            stop_reason = "dry_completed_round"
            break
        if round_new == 0:
            if round_summary["status"] == "timeout":
                stop_reason = "timeout_budget_exhausted"
            elif round_summary["status"] == "error":
                stop_reason = "engine_errors"
            else:
                stop_reason = "incomplete_round"
            break

    after_lower = current_rigorous_lower()
    growth = max(0, after_lower - before_lower)
    if total_new or growth:
        log_event(
            db,
            curve_id,
            "best" if growth else "info",
            f"Pointed quartic escalation found {total_new} new exact point(s); "
            f"certified rank growth {growth}; rigorous lower {after_lower}",
        )
    aggregate = pointed_quartic_coverage_summary(
        planned_searches=total_planned,
        completed_searches=total_completed,
        cached_completions=total_cached_completions,
        timeouts=total_timeouts,
        errors=total_failures,
        local_obstructions=total_local_obstructions,
        new_exact_points=total_new,
        dry_round_min_coverage=dry_round_min_coverage,
    )
    rank_goal_reached = bool(
        target_rank is not None and after_lower >= target_rank
    )
    outer_status = "completed" if rank_goal_reached else aggregate["status"]
    if rank_goal_reached:
        stop_reason = "rank_goal"

    return {
        "scheduler_tier": "pointed-quartic",
        "tier": "pointed-quartic",
        "status": outer_status,
        "reason": None if outer_status == "completed" else stop_reason,
        "stop_reason": stop_reason,
        "known_basis": len(basis),
        "models": total_models,
        "searches": total_searches,
        "planned_searches": total_planned,
        "completed_searches": total_completed,
        "cached_completions": total_cached_completions,
        "timeouts": total_timeouts,
        "failures": total_failures,
        "local_obstructions": total_local_obstructions,
        "map_back_failures": total_map_back_failures,
        "map_back_failure_samples": map_back_failure_samples,
        "map_back_diagnostics_truncated": (
            total_map_back_failures > len(map_back_failure_samples)
        ),
        "coverage_fraction": aggregate["coverage_fraction"],
        "resolved_searches": aggregate["resolved_searches"],
        "incomplete_searches": aggregate["incomplete_searches"],
        "dry_round_min_coverage": dry_round_min_coverage,
        "dry_completed": stop_reason == "dry_completed_round",
        "rank_goal_reached": rank_goal_reached,
        "retryable": outer_status in {
            "partial", "timeout", "error", "inconclusive"
        },
        "local_sieve_primes": [int(p) for p in local_sieve_primes],
        "exact_points": total_new,
        "rank_growth": growth,
        "rigorous_lower": after_lower,
        "rounds": round_summaries,
        "heights": list(heights),
        "anchors": anchors,
        "pool_size": pool_size,
        "deep_keep": deep_keep,
        "reduce_timeout": reduce_timeout,
        "backend": exe,
    }
