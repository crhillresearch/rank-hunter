"""Exact one-prime rational-isogeny probe for large source curves."""
from __future__ import annotations

import json
import sys


SCHEMA = "rank42/arithmetic-neighborhood/isogeny-probe-v1"
ALLOWED = {2, 3, 5, 7, 11, 13, 17, 19, 37, 43, 67, 163}


def _qtext(value):
    return str(value)


def main():
    try:
        from sage.all import EllipticCurve, QQ, ZZ
    except Exception as exc:
        raise SystemExit(f"Sage import failed: {exc}")

    try:
        request = json.load(sys.stdin)
    except Exception as exc:
        raise SystemExit(f"invalid request JSON: {exc}")

    if request.get("schema") != "rank42/arithmetic-neighborhood/isogeny-probe-request-v1":
        raise SystemExit("unsupported request schema")

    raw = request.get("ainvs")
    if not isinstance(raw, list) or len(raw) != 5:
        raise SystemExit("ainvs must contain exactly five coefficients")

    try:
        degree = int(request.get("degree"))
    except Exception as exc:
        raise SystemExit(f"invalid degree: {exc}")
    if degree not in ALLOWED:
        raise SystemExit(f"unsupported rational prime isogeny degree: {degree}")

    sage_degree = ZZ(degree)
    E = EllipticCurve([QQ(value) for value in raw])
    isogenies = E.isogenies_prime_degree(sage_degree)

    rows = []
    for phi in isogenies:
        codomain = phi.codomain()
        try:
            codomain = codomain.global_minimal_model()
        except Exception:
            pass
        rows.append(
            {
                "degree": degree,
                "domain_ainvs": [_qtext(value) for value in E.a_invariants()],
                "codomain_ainvs": [
                    _qtext(value) for value in codomain.a_invariants()
                ],
                "codomain_conductor": _qtext(codomain.conductor()),
                "codomain_discriminant": _qtext(codomain.discriminant()),
                "codomain_j_invariant": _qtext(codomain.j_invariant()),
            }
        )

    json.dump(
        {
            "schema": SCHEMA,
            "degree": degree,
            "isogenies": rows,
        },
        sys.stdout,
        sort_keys=True,
        separators=(",", ":"),
    )


if __name__ == "__main__":
    main()
