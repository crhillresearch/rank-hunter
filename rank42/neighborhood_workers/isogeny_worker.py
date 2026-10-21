"""Sage worker for exact rational isogeny-class arithmetic."""
from __future__ import annotations

import json
import sys


SCHEMA = "rank42/arithmetic-neighborhood/isogeny-class-v1"


def _qtext(value):
    try:
        return str(value)
    except Exception:
        return repr(value)


def main():
    try:
        from sage.all import EllipticCurve, QQ, ZZ
    except Exception as exc:
        raise SystemExit(f"Sage import failed: {exc}")

    try:
        request = json.load(sys.stdin)
    except Exception as exc:
        raise SystemExit(f"invalid request JSON: {exc}")

    if request.get("schema") != "rank42/arithmetic-neighborhood/isogeny-request-v1":
        raise SystemExit("unsupported request schema")
    raw = request.get("ainvs")
    if not isinstance(raw, list) or len(raw) != 5:
        raise SystemExit("ainvs must contain exactly five coefficients")

    E = EllipticCurve([QQ(value) for value in raw])
    iso = E.isogeny_class()
    curves = list(iso)
    matrix = iso.matrix()

    nodes = []
    source_index = None
    for index, curve in enumerate(curves):
        if source_index is None and curve.is_isomorphic(E):
            source_index = index
        try:
            torsion = [int(value) for value in curve.torsion_subgroup().invariants()]
        except Exception:
            torsion = []
        try:
            root_number = int(curve.root_number())
        except Exception:
            root_number = None
        nodes.append(
            {
                "index": index,
                "ainvs": [_qtext(value) for value in curve.a_invariants()],
                "conductor": _qtext(curve.conductor()),
                "discriminant": _qtext(curve.discriminant()),
                "j_invariant": _qtext(curve.j_invariant()),
                "root_number": root_number,
                "torsion_invariants": torsion,
            }
        )

    edges = []
    n = len(curves)
    for i in range(n):
        for j in range(i + 1, n):
            degree = ZZ(matrix[i, j])
            if degree > 1 and degree.is_prime():
                edges.append(
                    {
                        "source": i,
                        "target": j,
                        "degree": int(degree),
                    }
                )

    json.dump(
        {
            "schema": SCHEMA,
            "source_index": 0 if source_index is None else int(source_index),
            "nodes": nodes,
            "edges": edges,
        },
        sys.stdout,
        sort_keys=True,
        separators=(",", ":"),
    )


if __name__ == "__main__":
    main()
