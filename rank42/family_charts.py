"""Exact Family parameter-chart helpers owned by Rank Hunter core.

Charts are deliberately rational and exact.  A manifest matrix ``[A,B,C,D]``
means ``native = (A*chart + B)/(C*chart + D)`` over QQ.  No floating-point
parameter inversion or identity decisions are permitted here.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from fractions import Fraction


def _q(value) -> Fraction:
    if isinstance(value, Fraction):
        return value
    return Fraction(str(value))


def qstr(value) -> str:
    q = _q(value)
    return str(q.numerator) if q.denominator == 1 else f"{q.numerator}/{q.denominator}"


def mobius_apply(matrix, parameter) -> Fraction:
    if len(matrix) != 4:
        raise ValueError("PGL2 matrix requires exactly four coefficients [A,B,C,D]")
    A, B, C, D = (_q(x) for x in matrix)
    if A * D - B * C == 0:
        raise ValueError("PGL2 matrix is singular")
    s = _q(parameter)
    den = C * s + D
    if den == 0:
        raise ZeroDivisionError("chart parameter maps to infinity")
    return (A * s + B) / den


def mobius_inverse(matrix, parameter) -> Fraction:
    A, B, C, D = (_q(x) for x in matrix)
    # inverse matrix is [D,-B,-C,A], up to scalar
    return mobius_apply((D, -B, -C, A), parameter)


def matrix_fingerprint(matrix) -> str:
    normalized = [qstr(x) for x in matrix]
    payload = json.dumps(normalized, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonicalize_orbit(parameter, matrices) -> tuple[Fraction, dict]:
    """Choose a deterministic exact representative from a finite proven orbit."""
    start = _q(parameter)
    orbit = {start}
    frontier = [start]
    maps = [tuple(m) for m in (matrices or [])]
    while frontier:
        value = frontier.pop()
        for matrix in maps:
            try:
                image = mobius_apply(matrix, value)
            except ZeroDivisionError:
                continue
            if image not in orbit:
                orbit.add(image)
                frontier.append(image)
        # finite declared symmetry groups should close quickly; reject bad data
        if len(orbit) > 4096:
            raise ValueError("declared chart symmetry orbit did not close (over 4096 elements)")
    # stable complexity-first ordering, then signed numerator
    rep = min(orbit, key=lambda q: (max(abs(q.numerator), q.denominator), q.denominator, abs(q.numerator), q.numerator))
    return rep, {"orbit_size": len(orbit), "input": qstr(start), "canonical": qstr(rep)}


@dataclass(frozen=True)
class FamilyChart:
    id: str
    label: str
    variant_id: str
    native_variant_id: str
    native_family_spec: str
    native_family_key: str
    matrix: tuple[str, str, str, str]
    candidate_defaults: dict
    symmetry_matrices: tuple[tuple[str, str, str, str], ...] = ()
    metadata: dict | None = None

    def __post_init__(self):
        A, B, C, D = (_q(x) for x in self.matrix)
        if A * D - B * C == 0:
            raise ValueError(f"chart {self.id!r} has singular PGL2 matrix")
        for matrix in self.symmetry_matrices:
            a, b, c, d = (_q(x) for x in matrix)
            if a * d - b * c == 0:
                raise ValueError(f"chart {self.id!r} has singular symmetry matrix")

    @property
    def fingerprint(self):
        return matrix_fingerprint(self.matrix)

    def forward(self, parameter):
        return mobius_apply(self.matrix, parameter)

    def inverse(self, parameter):
        return mobius_inverse(self.matrix, parameter)

    def canonicalize(self, parameter):
        return canonicalize_orbit(parameter, self.symmetry_matrices)
