"""Sage worker for a bounded exact quadratic-twist neighborhood."""
from __future__ import annotations

import json
import sys


SCHEMA = "rank42/arithmetic-neighborhood/twist-neighborhood-v1"


def _qtext(value):
    return str(value)


def _curve_payload(curve, *, d):
    try:
        minimal = curve.global_minimal_model()
    except Exception:
        minimal = curve
    try:
        torsion = [int(value) for value in minimal.torsion_subgroup().invariants()]
    except Exception:
        torsion = []
    try:
        root_number = int(minimal.root_number())
    except Exception:
        root_number = None
    try:
        conductor = minimal.conductor()
        bad_primes = [int(p) for p in conductor.prime_divisors()]
        conductor_text = _qtext(conductor)
    except Exception:
        bad_primes = []
        conductor_text = None

    return {
        "d": int(d),
        "ainvs": [_qtext(value) for value in curve.a_invariants()],
        "minimal_ainvs": [_qtext(value) for value in minimal.a_invariants()],
        "conductor": conductor_text,
        "discriminant": _qtext(minimal.discriminant()),
        "j_invariant": _qtext(minimal.j_invariant()),
        "root_number": root_number,
        "torsion_invariants": torsion,
        "bad_primes": bad_primes,
    }


def main():
    try:
        from sage.all import EllipticCurve, QQ, ZZ
    except Exception as exc:
        raise SystemExit(f"Sage import failed: {exc}")

    try:
        request = json.load(sys.stdin)
    except Exception as exc:
        raise SystemExit(f"invalid request JSON: {exc}")

    if request.get("schema") != "rank42/arithmetic-neighborhood/twist-request-v1":
        raise SystemExit("unsupported request schema")
    raw = request.get("ainvs")
    if not isinstance(raw, list) or len(raw) != 5:
        raise SystemExit("ainvs must contain exactly five coefficients")

    try:
        bound = max(1, min(int(request.get("bound", 50)), 250))
    except Exception as exc:
        raise SystemExit(f"invalid twist bound: {exc}")

    E = EllipticCurve([QQ(value) for value in raw])
    ds = [
        d
        for d in range(-bound, bound + 1)
        if d != 0 and ZZ(d).is_squarefree()
    ]
    ds.sort(key=lambda d: (0 if d == 1 else 1, abs(d), 0 if d > 0 else 1, d))

    twists = []
    for d in ds:
        curve = E if d == 1 else E.quadratic_twist(ZZ(d))
        twists.append(_curve_payload(curve, d=d))

    json.dump(
        {
            "schema": SCHEMA,
            "bound": bound,
            "source_ainvs": [_qtext(value) for value in E.a_invariants()],
            "twists": twists,
        },
        sys.stdout,
        sort_keys=True,
        separators=(",", ":"),
    )


if __name__ == "__main__":
    main()
