"""Pure helpers for Rank Hunter's Nagao-prefiltered General Hunt funnel.

General Hunt is a control/discovery lane for short
Weierstrass curves, but unlike the v0.7.1 warm-up it does not spend expensive
point-search/proof work uniformly.  It first ranks a reproducible pool with a
Nagao/Mestre-style finite-field score, keeps a shortlist, and only then searches
for rational points.

Nothing in this module proves Mordell--Weil rank.  Nagao scores are heuristic,
and numerical Gram-matrix selection is only a screen.  Rigorous promotion lives
in :mod:`rank42.general_hunt` and requires the exact lower-bound
certificate.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from fractions import Fraction
from pathlib import Path


SCORE_ALGORITHM = "short-nagao-v1"
SCORE_ALGORITHM_VERSION = 1
SCORE_FORMULA = "sum_log_cardinality_over_p"


def primes_below(bound: int) -> list[int]:
    """Return primes ``p < bound`` without requiring Sage."""
    bound = int(bound)
    if bound <= 2:
        return []
    sieve = bytearray(b"\x01") * bound
    sieve[0:2] = b"\x00\x00"
    for p in range(2, int(bound**0.5) + 1):
        if sieve[p]:
            start = p * p
            sieve[start:bound:p] = b"\x00" * (((bound - 1 - start) // p) + 1)
    return [i for i in range(2, bound) if sieve[i]]


def short_discriminant_factor(A: int, B: int) -> int:
    """Return the short-model discriminant factor ``4A^3+27B^2``.

    The actual discriminant is ``-16`` times this value.  For odd primes the
    factor is enough to decide good reduction.
    """
    A, B = int(A), int(B)
    return 4 * A**3 + 27 * B**2


def _legendre_symbol(value: int, p: int) -> int:
    value %= p
    if value == 0:
        return 0
    r = pow(value, (p - 1) // 2, p)
    return 1 if r == 1 else -1


def short_curve_point_count_mod_p(A: int, B: int, p: int) -> int | None:
    """Count ``#E(F_p)`` for ``y^2=x^3+Ax+B`` by exact character sum.

    ``None`` is returned at bad reduction.  This routine is intended for the
    small prime bounds used by the General Hunt prefilter, not for certification.
    """
    A, B, p = int(A), int(B), int(p)
    if p <= 3 or short_discriminant_factor(A, B) % p == 0:
        return None
    s = 0
    Am, Bm = A % p, B % p
    for x in range(p):
        rhs = (x * x * x + Am * x + Bm) % p
        s += _legendre_symbol(rhs, p)
    return p + 1 + s


def short_curve_nagao_score_details(A: int, B: int, prime_bound: int):
    """Return the historical RH short-model score plus exact prime provenance."""
    total = 0.0
    primes_used = []
    for p in primes_below(int(prime_bound)):
        if p in (2, 3):
            continue
        n = short_curve_point_count_mod_p(A, B, p)
        if n is None:
            continue
        total += math.log(n / p)
        primes_used.append(int(p))
    provenance = {
        "algorithm": SCORE_ALGORITHM,
        "algorithm_version": int(SCORE_ALGORITHM_VERSION),
        "formula": SCORE_FORMULA,
        "scorer": "short_weierstrass_exact_character_sum",
        "scorer_version": int(SCORE_ALGORITHM_VERSION),
        "source_mode": "general_short_weierstrass",
        "prime_bound": int(prime_bound),
        "primes_used": primes_used,
        "terms_used": len(primes_used),
        "prime_convention": "p < bound; skip 2 and 3; good reduction only",
        "published_mestre_nagao_formula": False,
    }
    return total, len(primes_used), provenance


def short_curve_nagao_score(A: int, B: int, prime_bound: int) -> tuple[float, int]:
    """Return the project-specific Nagao-style score and good-prime term count."""
    total, used, _provenance = short_curve_nagao_score_details(
        A, B, prime_bound
    )
    return total, used

def sample_unique_pairs(lo1: int, hi1: int, lo2: int, hi2: int, count: int, *, seed: int = 42) -> list[tuple[int, int]]:
    """Reproducibly sample distinct integer pairs from a rectangle.

    When the requested count covers the whole rectangle, all pairs are returned
    in deterministic lexicographic order.  Otherwise a local PRNG performs
    rejection sampling; global random state is never touched.
    """
    lo1, hi1, lo2, hi2, count = map(int, (lo1, hi1, lo2, hi2, count))
    if lo1 > hi1 or lo2 > hi2:
        raise ValueError("lower bound exceeds upper bound")
    total = (hi1 - lo1 + 1) * (hi2 - lo2 + 1)
    if count <= 0:
        return []
    count = min(count, total)
    if count == total:
        return [(a, b) for a in range(lo1, hi1 + 1) for b in range(lo2, hi2 + 1)]
    rng = random.Random(int(seed))
    picked: set[tuple[int, int]] = set()
    while len(picked) < count:
        picked.add((rng.randint(lo1, hi1), rng.randint(lo2, hi2)))
    return sorted(picked)


def parse_height_stages(text: str | list[int] | tuple[int, ...]) -> list[int]:
    if isinstance(text, (list, tuple)):
        values = [int(x) for x in text]
    else:
        values = [int(x.strip()) for x in str(text).split(",") if x.strip()]
    if not values or any(x <= 0 for x in values):
        raise ValueError("ratpoints height stages must be positive integers")
    values = sorted(set(values))
    return values


def rational_height_key(x) -> tuple[int, int, int]:
    """Small-first key for a rational coordinate."""
    q = x if isinstance(x, Fraction) else Fraction(str(x))
    h = max(abs(q.numerator), q.denominator)
    return h, q.denominator, abs(q.numerator)


def canonical_affine_key(x, y) -> tuple[str, str]:
    """Identify a finite point up to the elliptic involution ``P -> -P``."""
    qx = x if isinstance(x, Fraction) else Fraction(str(x))
    qy = y if isinstance(y, Fraction) else Fraction(str(y))
    if qy < 0:
        qy = -qy
    return str(qx), str(qy)


def _positive_definite(matrix: list[list[float]], *, rel_tol: float = 1e-10) -> bool:
    """Numerical Cholesky test used only for basis *screening*."""
    n = len(matrix)
    if n == 0:
        return True
    scale = max(1.0, max(abs(float(matrix[i][i])) for i in range(n)))
    tol = float(rel_tol) * scale
    L = [[0.0] * n for _ in range(n)]
    for i in range(n):
        pivot = float(matrix[i][i]) - sum(L[i][k] * L[i][k] for k in range(i))
        if not math.isfinite(pivot) or pivot <= tol:
            return False
        L[i][i] = math.sqrt(pivot)
        for j in range(i + 1, n):
            numer = float(matrix[j][i]) - sum(L[j][k] * L[i][k] for k in range(i))
            L[j][i] = numer / L[i][i]
    return True


def numerical_independent_indices(gram, *, max_size: int | None = None, rel_tol: float = 1e-10) -> list[int]:
    """Greedily select a positive-definite principal submatrix.

    Input may contain numeric strings from the isolated Sage height worker.
    The returned subset is a numerical screen only and must never be promoted
    to a rank claim without an exact certificate.
    """
    M = [[float(v) for v in row] for row in gram]
    n = len(M)
    if any(len(row) != n for row in M):
        raise ValueError("Gram matrix must be square")
    limit = n if max_size is None else max(0, min(n, int(max_size)))
    chosen: list[int] = []
    for idx in range(n):
        trial = chosen + [idx]
        sub = [[M[i][j] for j in trial] for i in trial]
        if _positive_definite(sub, rel_tol=rel_tol):
            chosen.append(idx)
            if len(chosen) >= limit:
                break
    return chosen


def score_cache_key(config: dict) -> str:
    payload = {"algorithm": SCORE_ALGORITHM, **config}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()[:20]


def score_cache_path(project_root: str | Path, config: dict) -> Path:
    key = score_cache_key(config)
    return Path(project_root) / ".rank42-cache" / "general-hunt" / f"scores-{key}.jsonl"
