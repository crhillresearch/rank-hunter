"""Isolated numerical Mordell--Weil height-lattice worker."""
from __future__ import annotations

import hashlib
import json
import sys
import time

from sage.all import QQ, EllipticCurve

from rank42.lattice import build_lattice

MARKER = "RANK42_LATTICE_RESULT="


def _point_payload(P):
    return [str(P[0]), str(P[1])]


def _basis_fingerprint(points):
    payload = json.dumps(
        [_point_payload(P) for P in points],
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _runtime_meta():
    try:
        from sage.version import version as sage_version
        version = str(sage_version)
    except Exception:
        version = None
    return {"sage_version": version, "engine_version": version}


def main():
    payload = json.loads(sys.stdin.read())
    ainvs = [QQ(str(x)) for x in payload["a_invariants"]]
    if len(ainvs) != 5:
        raise ValueError("a_invariants must have length 5")
    E = EllipticCurve(QQ, ainvs)

    points = []
    for idx, raw in enumerate(payload.get("points") or []):
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            raise ValueError(f"point {idx} is malformed")
        P = E(QQ(str(raw[0])), QQ(str(raw[1])))
        if not P.is_zero():
            points.append(P)
    if not points:
        raise ValueError("no nonzero exact points supplied")

    precision = max(64, int(payload.get("precision_bits") or 256))
    lll_reduce = bool(payload.get("lll_reduce", True))
    started = time.monotonic()
    result = build_lattice(
        E,
        points,
        precision=precision,
        lll_reduce=lll_reduce,
    )
    runtime = time.monotonic() - started

    output = {
        "status": "completed",
        "precision_bits": precision,
        "runtime_seconds": runtime,
        "basis_fingerprint": _basis_fingerprint(points),
        "points": [_point_payload(P) for P in result["points"]],
        "gram": result["gram"],
        "determinant": result["determinant"],
        "min_eigenvalue": result["min_eigenvalue"],
        "max_eigenvalue": result["max_eigenvalue"],
        "eigenvalues": result["eigenvalues"],
        "positive_definite_screen": result["positive_definite_screen"],
        "lll_transform": result["lll_transform"],
        "input_points": [_point_payload(P) for P in result["input_points"]],
        "input_gram": result["input_gram"],
        "input_determinant": result["input_determinant"],
        "input_eigenvalues": result["input_eigenvalues"],
        **_runtime_meta(),
    }
    print(MARKER + json.dumps(output, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:
        print(
            MARKER
            + json.dumps(
                {"status": "error", "error": repr(exc)},
                sort_keys=True,
            )
        )
        raise
