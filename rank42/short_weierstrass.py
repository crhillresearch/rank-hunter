"""Pure short-Weierstrass helpers shared by General Hunt and optional tools.

This module contains exact model generation, candidate normalization, and
deduplication helpers only.  It makes no rank claims; proof promotion remains
in the bounded certification backends that consume these helpers.
"""

from __future__ import annotations

from fractions import Fraction
from math import isqrt


GENERAL_FAMILY = "Rank Hunter playground, integral short Weierstrass"
GENERAL_TRIAL_SCHEMA = "general-short-v1"


def nonsingular_short(A: int, B: int) -> bool:
    """Return whether ``y^2 = x^3 + A*x + B`` is nonsingular."""
    A = int(A)
    B = int(B)
    return 4 * A**3 + 27 * B**2 != 0


def integral_point_candidates(A: int, B: int, x_bound: int, *, limit: int | None = None):
    """Find one representative from each nonzero integral ``±P`` pair.

    Only positive ``y`` is returned.  ``y=0`` points are 2-torsion and are not
    useful for a free-rank lower bound.  This is an exact integer screen.
    """
    A = int(A)
    B = int(B)
    x_bound = max(0, int(x_bound))
    out: list[tuple[int, int]] = []
    for x in range(-x_bound, x_bound + 1):
        rhs = x**3 + A * x + B
        if rhs <= 0:
            continue
        y = isqrt(rhs)
        if y and y * y == rhs:
            out.append((x, y))
            if limit is not None and len(out) >= int(limit):
                break
    return out


def seeded_short_curve(u: int, v: int):
    """Construct a short curve with two exact seed points.

    ``B=u^2`` and ``A=v^2-u^2-1`` force ``(0,u)`` and ``(1,v)`` onto
    ``y^2=x^3+A*x+B``.  Independence is *not* implied and must be certified.
    """
    u = int(u)
    v = int(v)
    A = v * v - u * u - 1
    B = u * u
    return A, B, ((0, u), (1, v))


def ordered_integer_pairs(lo1: int, hi1: int, lo2: int, hi2: int):
    """Deterministic small-first ordering for a rectangular integer grid."""
    lo1, hi1, lo2, hi2 = map(int, (lo1, hi1, lo2, hi2))
    if lo1 > hi1 or lo2 > hi2:
        raise ValueError("lower bound exceeds upper bound")
    pairs = [(a, b) for a in range(lo1, hi1 + 1) for b in range(lo2, hi2 + 1)]
    pairs.sort(key=lambda ab: (abs(ab[0]) + abs(ab[1]), max(abs(ab[0]), abs(ab[1])), abs(ab[0]), abs(ab[1]), ab[0], ab[1]))
    return pairs


def trial_key(*, mode: str, source_a: int, source_b: int, A: int, B: int, x_bound: int, target: int) -> str:
    """Stable deduplication key for one short-Weierstrass attempt."""
    return (
        f"{GENERAL_TRIAL_SCHEMA}:{str(mode)}:{int(source_a)}:{int(source_b)}:"
        f"A={int(A)}:B={int(B)}:x={int(x_bound)}:target={int(target)}"
    )


def parameter_label(A: int, B: int) -> str:
    return f"A={int(A)}, B={int(B)}"


def normalize_candidate_parameter(row) -> str | None:
    """Canonicalize a rational candidate ``t`` without importing Sage."""
    if row.get("t") is not None:
        value = row.get("t")
    elif row.get("a") is not None and row.get("b") is not None:
        try:
            return str(Fraction(int(row["a"]), int(row["b"])))
        except Exception:
            return f"{row['a']}/{row['b']}"
    else:
        return None
    try:
        return str(Fraction(str(value)))
    except Exception:
        return str(value)


def candidate_state(db_row) -> str:
    """Map persisted curve state to the four operator-facing pool states."""
    if db_row is None:
        return "Unsearched"
    status = str(db_row["status"] or "")
    if status == "pruned":
        return "Pruned"
    interesting_statuses = {
        "extra_candidate", "extra_review", "promising", "strong_done", "exact",
        "proven_lower", "record_candidate",
    }
    if status in interesting_statuses:
        return "Interesting"
    try:
        generic = int(db_row["generic_lower"] or 0)
        lower = max(int(db_row["descent_lower"] or 0), int(db_row["exact_rank"] or 0))
        if lower > generic:
            return "Interesting"
    except Exception:
        pass
    return "Searched"
