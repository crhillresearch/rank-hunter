#!/usr/bin/env python3
"""Isolated PARI quartic-reduction worker for pointed-quartic Auto Search.

The worker reduces integral square quartics with PARI hyperellred and emits the
exact inverse fractional-linear map. Every returned transform is checked by
polynomial identities before it is exposed to the search process.
"""
from __future__ import annotations

import json
import math
import sys
from fractions import Fraction

from sage.all import pari


MARKER = "RANK42_POINTED_REDUCTION="


def _q(value):
    return value if isinstance(value, Fraction) else Fraction(str(value))


def _lcm_denominators(values):
    scale = 1
    for value in values:
        scale = math.lcm(scale, _q(value).denominator)
    return scale


def _integer_square_model(coefficients):
    coeffs = [_q(x) for x in coefficients]
    scale = _lcm_denominators(coeffs)
    integers = []
    for coefficient in coeffs:
        value = coefficient * scale * scale
        if value.denominator != 1:
            raise ValueError("failed to clear pointed-quartic denominators")
        integers.append(int(value.numerator))
    return scale, integers


def _s(expr):
    return str(pari(expr))


def reduce_one(record):
    key = str(record["key"])
    scale, coefficients = _integer_square_model(record["coefficients"])
    vector = ",".join(str(x) for x in coefficients)
    pari(f"F=Polrev([{vector}])")
    minimized = bool(record.get("minimize", False))
    if minimized:
        pari("M=hyperellminimalmodel(F,&mm); C=hyperellred(M,&mr)")
        # Compose final -> minimal (mr) with minimal -> original (mm).
        # For genus one, H = e_mm*H_mr + d_mr^2*H_mm(x_mr).
        pari("dr=mr[2][2,1]*x+mr[2][2,2]")
        pari("tr=(mr[2][1,1]*x+mr[2][1,2])/dr")
        pari(
            "m=[mm[1]*mr[1],mm[2]*mr[2],"
            "mm[1]*mr[3]+dr^2*subst(mm[3],x,tr)]"
        )
        if int(pari("hyperellchangecurve(F,m)==C")) != 1:
            raise RuntimeError("PARI minimal/reduced transform composition failed")
    else:
        pari("C=hyperellred(F,&m)")
    pari("dd=m[2][2,1]*x+m[2][2,2]")
    pari("tt=(m[2][1,1]*x+m[2][1,2])/dd")
    first_ok = int(pari(
        "dd^4*subst(F,x,tt)-m[3]^2 == m[1]^2*C[1]"
    ))
    second_ok = int(pari(
        "2*m[1]*m[3] == m[1]^2*C[2]"
    ))
    if first_ok != 1 or second_ok != 1:
        raise RuntimeError("PARI hyperellred inverse-map identity failed")

    f = [_s(f"polcoef(C[1],{i})") for i in range(5)]
    q = [_s(f"polcoef(C[2],{i})") for i in range(3)]
    transform = {
        "e": _s("m[1]"),
        "matrix": [
            _s("m[2][1,1]"), _s("m[2][1,2]"),
            _s("m[2][2,1]"), _s("m[2][2,2]"),
        ],
        "h": [_s(f"polcoef(m[3],{i})") for i in range(3)],
    }
    return {
        "key": key,
        "status": "completed",
        "scale": str(scale),
        "minimalized": minimized,
        "f": f,
        "q": q,
        "transform": transform,
    }


def main():
    payload = json.loads(sys.stdin.read())
    for record in payload.get("models") or []:
        key = str(record.get("key") or "")
        try:
            result = reduce_one(record)
        except BaseException as exc:
            result = {"key": key, "status": "error", "error": repr(exc)}
        print(MARKER + json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
