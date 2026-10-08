"""Sage worker for a rigorous exact-rank attempt."""

from __future__ import annotations

import json
import sys

from sage.all import QQ, EllipticCurve, proof

MARKER = "RANK42_RANK_CERT="


def main():
    payload = json.loads(sys.stdin.read())
    ainvs = [QQ(x) for x in payload["a_invariants"]]
    proof.all(True)
    E = EllipticCurve(QQ, ainvs)
    # With proof mode enabled, Sage must not silently return a conjectural
    # analytic rank.  If the underlying exact machinery cannot finish, the
    # parent process timeout/failure leaves exact_rank unset.
    rank = int(E.rank(proof=True))
    try:
        from sage.version import version as sage_version_value
        sage_version = str(sage_version_value)
    except Exception:
        sage_version = None
    result = {
        "exact_rank": rank,
        "proof_mode": True,
        "a_invariants": [str(a) for a in E.a_invariants()],
        "sage_version": sage_version,
    }
    print(MARKER + json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
