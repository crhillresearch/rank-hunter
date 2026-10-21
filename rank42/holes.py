"""Enumerate and prioritize half-lattice cosets L/2L from a height Gram matrix.

For a basis P_1,...,P_r and bit vector e, the half-coset is represented by

    e/2 + Z^r.

The relevant geometric quantity is the minimum of v^T G v in that coset.
Computing every exact closest vector in a rank-17 real height lattice is itself
nontrivial, so v0.4.2 offers two clearly labelled *heuristics*:

* raw: use e/2 directly (fast, basis dependent);
* babai: nearest-plane in a Cholesky realization, followed by integer
  coordinate descent (better, still not a proof of the exact coset minimum).

The largest approximate minimum norms are prioritized as candidate holes.
"""

from __future__ import annotations

import argparse
import heapq
import json
import math

import numpy as np

from rank42.db import connect
from rank42.lattice_store import get_lattice, list_holes, store_holes


def parse_gram(gram_json):
    raw = json.loads(gram_json) if isinstance(gram_json, str) else gram_json
    G = np.array([[float(x) for x in row] for row in raw], dtype=float)
    if G.ndim != 2 or G.shape[0] != G.shape[1] or G.shape[0] == 0:
        raise ValueError("Gram matrix must be nonempty and square")
    if not np.allclose(G, G.T, rtol=1e-10, atol=1e-12):
        raise ValueError("Gram matrix is not numerically symmetric")
    eig = np.linalg.eigvalsh(G)
    if eig[0] <= 0:
        raise ValueError("Gram matrix is not positive definite")
    return G


def bits_for(mask, rank):
    return np.array([(mask >> i) & 1 for i in range(rank)], dtype=float)


def bits_string(bits):
    return "".join("1" if int(x) else "0" for x in bits)


def _babai_nearest_integer(R, coeff_target):
    """Babai nearest-plane coefficients for a row-basis Cholesky lattice."""
    y = R @ coeff_target
    r = len(coeff_target)
    z = np.zeros(r, dtype=float)
    for i in range(r - 1, -1, -1):
        rhs = y[i]
        if i + 1 < r:
            rhs -= float(R[i, i + 1:] @ z[i + 1:])
        z[i] = float(np.rint(rhs / R[i, i]))
    return z


def _coordinate_refine(G, v, passes=4):
    v = np.array(v, dtype=float, copy=True)
    gv = G @ v
    r = len(v)
    for _ in range(int(passes)):
        changed = False
        for i in range(r):
            d = int(np.rint(-gv[i] / G[i, i]))
            if d:
                v[i] += d
                gv += d * G[:, i]
                changed = True
        if not changed:
            break
    return v


def coset_representative(G, bits, *, method="babai"):
    half = 0.5 * np.array(bits, dtype=float)
    if method == "raw":
        v = half
    elif method == "babai":
        # G = B B^T.  With row basis B, column basis A=B^T and QR(A)=QR.
        B = np.linalg.cholesky(G)
        A = B.T
        _, R = np.linalg.qr(A)
        target_coeff = -half
        n = _babai_nearest_integer(R, target_coeff)
        v = _coordinate_refine(G, n + half)
    else:
        raise ValueError(f"unknown hole method: {method}")
    norm2 = float(v @ G @ v)
    return v, norm2


def top_half_cosets(G, *, top=256, method="babai", max_cosets=None):
    rank = G.shape[0]
    if rank > 24 and max_cosets is None:
        raise ValueError(
            f"rank {rank} has {2**rank - 1:,} nonzero half-cosets; "
            "set max_cosets explicitly"
        )
    total = (1 << rank) - 1
    if max_cosets is not None:
        total = min(total, int(max_cosets))
    top = max(1, int(top))

    # Factor once for Babai rather than once per coset.
    if method == "babai":
        B = np.linalg.cholesky(G)
        _, R = np.linalg.qr(B.T)
    else:
        R = None

    heap = []
    for mask in range(1, total + 1):
        bits = bits_for(mask, rank)
        half = 0.5 * bits
        if method == "raw":
            v = half
        elif method == "babai":
            n = _babai_nearest_integer(R, -half)
            v = _coordinate_refine(G, n + half)
        else:
            raise ValueError(f"unknown hole method: {method}")
        norm2 = float(v @ G @ v)
        rec = (norm2, mask, v)
        if len(heap) < top:
            heapq.heappush(heap, rec)
        elif norm2 > heap[0][0]:
            heapq.heapreplace(heap, rec)

    rows = []
    for norm2, mask, v in sorted(heap, key=lambda x: (-x[0], x[1])):
        bits = bits_for(mask, rank)
        rows.append(
            {
                "bits": bits_string(bits),
                "representative": [f"{x:.17g}" for x in v],
                "norm2": norm2,
                "metadata": {
                    "mask": int(mask),
                    "cosets_examined": int(total),
                    "score_interpretation": "approximate upper bound on coset minimum norm",
                },
            }
        )
    return rows


def parse_args():
    ap = argparse.ArgumentParser(description="Prioritize half-lattice cosets from a stored MW lattice.")
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--lattice-id", type=int, required=True)
    ap.add_argument("--top", type=int, default=256)
    ap.add_argument("--method", choices=("raw", "babai"), default="babai")
    ap.add_argument("--max-cosets", type=int)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--limit", type=int, default=30)
    return ap.parse_args()


def main():
    args = parse_args()
    db = connect(args.db)
    lattice = get_lattice(db, args.lattice_id)
    if lattice is None:
        raise SystemExit(f"lattice #{args.lattice_id} not found")

    if args.list:
        rows = list_holes(db, args.lattice_id, limit=args.limit)
        if not rows:
            print("no stored half-lattice holes")
            return
        print("HALF-LATTICE HOLES")
        print("=" * 78)
        for r in rows:
            print(
                f"#{r['id']:<5} norm2~{r['norm2']:<14.7g} "
                f"bits={r['bits']} method={r['method']} status={r['status']}"
            )
        return

    if not lattice["positive_definite_screen"]:
        raise SystemExit("refusing hole enumeration: lattice height screen is not positive definite")
    G = parse_gram(lattice["gram_json"])
    rank = G.shape[0]
    total = (1 << rank) - 1
    examined = min(total, args.max_cosets) if args.max_cosets else total
    print("RANK HUNTER HALF-LATTICE PRIORITIZER")
    print("=" * 62)
    print("lattice id          =", lattice["id"])
    print("rank                =", rank)
    print("nonzero cosets      =", f"{total:,}")
    print("examining           =", f"{examined:,}")
    print("method              =", args.method)
    print("persist top         =", args.top)
    print("NOTE: scores are heuristic prioritization, not exact Voronoi radii")

    holes = top_half_cosets(
        G, top=args.top, method=args.method, max_cosets=args.max_cosets
    )
    store_holes(
        db,
        lattice["id"],
        holes,
        method=args.method,
        metadata={
            "rank": rank,
            "precision_bits": lattice["precision_bits"],
            "heuristic": True,
        },
    )
    print("stored              =", len(holes))
    for h in holes[:10]:
        print(f"  norm2~{h['norm2']:.9g} bits={h['bits']}")


if __name__ == "__main__":
    main()
