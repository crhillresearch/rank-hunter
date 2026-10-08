"""Isolated worker for potentially expensive exact prime/local preparation."""
from __future__ import annotations

import json
import sys

from sage.all import EllipticCurve, QQ, ZZ


MARKER = "RANK42_PRIME_LOCAL_RESULT="


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        ainvs = [QQ(str(x)) for x in payload.get("a_invariants") or []]
        if len(ainvs) != 5:
            raise ValueError("expected five a-invariants")
        E = EllipticCurve(QQ, ainvs)
        try:
            Em = E.global_minimal_model()
        except Exception:
            Em = E.minimal_model()
        delta = ZZ(Em.discriminant())
        bad = sorted(int(p) for p in abs(delta).prime_divisors())
        result = {
            "status": "completed",
            "minimal_a_invariants": [str(a) for a in Em.a_invariants()],
            "minimal_discriminant": str(delta),
            "bad_primes": bad,
        }
    except BaseException as exc:
        result = {
            "status": "error",
            "error": repr(exc),
        }
    print(MARKER + json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
