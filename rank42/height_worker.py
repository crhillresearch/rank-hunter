"""Isolated numerical Neron--Tate height-screen worker.

This worker exists only to put a hard process boundary around Sage height
pairing computations.  Its output is a numerical independence screen, never a
rigorous Mordell--Weil rank certificate.
"""

from __future__ import annotations

import json
import sys

from sage.all import QQ, EllipticCurve

MARKER = "RANK42_HEIGHT_JSON="


def main():
    payload = json.loads(sys.stdin.read())
    ainvs = [QQ(str(x)) for x in payload["a_invariants"]]
    E = EllipticCurve(QQ, ainvs)
    if E.discriminant() == 0:
        raise ValueError("singular curve")
    points = []
    for i, xy in enumerate(payload.get("points") or []):
        if len(xy) != 2:
            raise ValueError(f"point {i} must be [x,y]")
        points.append(E(QQ(str(xy[0])), QQ(str(xy[1]))))
    precision = int(payload.get("precision", 128))
    if not points:
        result = {
            "count": 0,
            "precision_bits": precision,
            "gram": [],
            "determinant": "1",
            "min_eigenvalue": None,
            "positive_definite_screen": True,
        }
    else:
        H = E.height_pairing_matrix(points, precision=precision)
        det = H.det()
        eigs = H.eigenvalues()
        mineig = min(eigs)
        result = {
            "count": len(points),
            "precision_bits": precision,
            "gram": [[str(H[i, j]) for j in range(H.ncols())] for i in range(H.nrows())],
            "determinant": str(det),
            "min_eigenvalue": str(mineig),
            "positive_definite_screen": bool(mineig > 0),
        }
    print(MARKER + json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:
        print(MARKER + json.dumps({"status": "error", "error": repr(exc)}, sort_keys=True), flush=True)
        raise
