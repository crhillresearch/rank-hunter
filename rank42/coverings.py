"""Exact, data-driven 2-covering handoff objects.

v0.4.2 deliberately separates *deriving* a covering from *executing* it.  A
family-specific derivation must provide an exact quartic y^2=f(u) and exact
rational map formulas X(u,v), Y(u,v) to the target elliptic curve.  The generic
engine validates, stores, searches, maps, and screens those data.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rank42.db import connect, get_curve
from rank42.lattice_store import get_covering, store_covering

SCHEMA = "rank42.covering.v1"


def validate_covering(data):
    if data.get("schema") != SCHEMA:
        raise ValueError(f"unsupported covering schema: {data.get('schema')!r}")
    if data.get("curve_id") is None:
        raise ValueError("covering requires curve_id")
    quartic = data.get("quartic")
    if not isinstance(quartic, dict):
        raise ValueError("covering requires quartic object")
    coeffs = quartic.get("coefficients")
    if not isinstance(coeffs, list) or len(coeffs) < 2:
        raise ValueError("quartic.coefficients must be a coefficient list")
    if len(coeffs) > 5:
        raise ValueError("v0.4.2 covering handoff expects degree <= 4")
    if int(quartic.get("height", 0)) <= 0:
        raise ValueError("quartic.height must be positive")
    mapping = data.get("map")
    if not isinstance(mapping, dict) or not mapping.get("x") or not mapping.get("y"):
        raise ValueError("covering requires map.x and map.y expressions")
    return data


def load_covering_file(path):
    return validate_covering(json.loads(Path(path).read_text()))


def map_quartic_point(E, data, u, v):
    """Map one affine quartic point exactly to E(Q).

    Expressions are trusted local Sage expressions and may use ``u`` and ``v``.
    Exact membership in E is checked by constructing E(X,Y).
    """
    validate_covering(data)
    from sage.all import QQ, sage_eval

    uq = QQ(str(u))
    vq = QQ(str(v))
    X = QQ(sage_eval(str(data["map"]["x"]), locals={"u": uq, "v": vq}))
    Y = QQ(sage_eval(str(data["map"]["y"]), locals={"u": uq, "v": vq}))
    return E(X, Y)


def parse_args():
    ap = argparse.ArgumentParser(description="Validate/import exact covering JSON.")
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--input")
    ap.add_argument("--show", type=int, help="display a stored covering")
    return ap.parse_args()


def main():
    args = parse_args()
    db = connect(args.db)
    if args.show is not None:
        row = get_covering(db, args.show)
        if row is None:
            raise SystemExit(f"covering #{args.show} not found")
        print(json.dumps(dict(row), indent=2, sort_keys=True))
        return
    if not args.input:
        raise SystemExit("provide --input or --show")
    data = load_covering_file(args.input)
    if get_curve(db, int(data["curve_id"])) is None:
        raise SystemExit(f"curve #{data['curve_id']} not found")
    row = store_covering(db, data)
    print("covering id =", row["id"])
    print("curve id    =", row["curve_id"])
    print("lattice id  =", row["lattice_id"] or "-")
    print("hole id     =", row["hole_id"] or "-")
    print("status      =", row["status"])


if __name__ == "__main__":
    main()
