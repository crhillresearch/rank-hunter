"""Bounded exact-math worker for manual ICARM matching.

The parent process supplies one local Q-curve and a finite ICARM candidate list.
This worker performs an exact j-invariant prefilter followed by exact Sage
Q-isomorphism.  It deliberately does not compute the conductor: conductor is
only a search accelerator, not required for correctness of the exact match.
"""
from __future__ import annotations

import json
import sys


def main():
    payload = json.load(sys.stdin)
    local_ainvs = payload.get("local_ainvs") or []
    rows = payload.get("rows") or []
    if len(local_ainvs) != 5:
        raise SystemExit("local curve requires five a-invariants")

    from sage.all import QQ, EllipticCurve

    E = EllipticCurve(QQ, [QQ(x) for x in local_ainvs])
    ej = E.j_invariant()
    j_candidates = 0

    for row in rows:
        try:
            ainvs = row.get("a_invariants") or []
            if len(ainvs) != 5:
                continue
            F = EllipticCurve(QQ, [QQ(x) for x in ainvs])
            if F.j_invariant() != ej:
                continue
            j_candidates += 1
            if E.is_isomorphic(F):
                print(json.dumps({
                    "status": "known",
                    "source_id": str(row.get("source_id")),
                    "j_candidates": j_candidates,
                }, sort_keys=True), flush=True)
                return
        except Exception:
            continue

    print(json.dumps({
        "status": "not_found",
        "source_id": None,
        "j_candidates": j_candidates,
    }, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
