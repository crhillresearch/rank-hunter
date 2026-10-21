"""Isolated Sage worker for rational prime-degree isogeny discovery."""
from __future__ import annotations

import json
import sys
import time
import traceback

from sage.all import EllipticCurve, QQ

MARKER = "RANK42_ISOGENY_RESULT="


def _point_json(P):
    if P.is_zero():
        return None
    return [str(P[0]), str(P[1])]


def main():
    started = time.monotonic()
    try:
        request = json.load(sys.stdin)
        degree = int(request["degree"])
        if degree <= 1:
            raise ValueError("isogeny degree must be > 1")
        E = EllipticCurve(
            QQ, [QQ(str(x)) for x in request["a_invariants"]]
        )
        basis = [
            E(QQ(str(xy[0])), QQ(str(xy[1])))
            for xy in (request.get("points") or [])
        ]
        maps = list(E.isogenies_prime_degree(degree))
        children = []
        for index, phi in enumerate(maps, 1):
            raw_child = phi.codomain()
            mapped = []
            failures = []
            for point_index, P in enumerate(basis, 1):
                try:
                    Q = phi(P)
                    xy = _point_json(Q)
                    if xy is not None:
                        mapped.append({
                            "parent_basis_index": int(point_index),
                            "point": xy,
                        })
                except Exception as exc:
                    if len(failures) < 5:
                        failures.append({
                            "parent_basis_index": int(point_index),
                            "error_class": type(exc).__name__,
                            "error": str(exc)[:240],
                        })
            children.append({
                "edge_index": int(index),
                "degree": int(degree),
                "codomain_a_invariants": [
                    str(x) for x in raw_child.a_invariants()
                ],
                "mapped_points": mapped,
                "mapping_complete": len(failures) == 0,
                "mapping_failures": len(failures),
                "mapping_failure_samples": failures,
            })
        result = {
            "status": "completed",
            "degree": int(degree),
            "children": children,
            "child_count": len(children),
            "basis_points_received": len(basis),
            "runtime_seconds": time.monotonic() - started,
        }
        print(MARKER + json.dumps(result, sort_keys=True), flush=True)
        return 0
    except Exception as exc:
        result = {
            "status": "error",
            "reason": "isogeny discovery worker failed",
            "error": repr(exc),
            "traceback_tail": traceback.format_exc().splitlines()[-20:],
            "runtime_seconds": time.monotonic() - started,
        }
        print(MARKER + json.dumps(result, sort_keys=True), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
