"""Isolated Sage worker for bounded elliptic-curve model preparation."""
from __future__ import annotations

import json
import sys

from sage.all import QQ, EllipticCurve

MARKER = "RANK42_MODEL_PREP="


def main():
    payload = json.loads(sys.stdin.read())
    ainvs = [QQ(str(x)) for x in payload["a_invariants"]]
    E = EllipticCurve(QQ, ainvs)
    Em = E.global_minimal_model()
    print(
        MARKER + json.dumps(
            {"minimal_a_invariants": [str(x) for x in Em.a_invariants()]},
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
