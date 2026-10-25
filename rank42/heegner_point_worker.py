"""Isolated PARI ellheegner call; no database access or rank promotion."""
from __future__ import annotations

import json
import sys

from sage.all import EllipticCurve, QQ

from rank42.heegner_point import MARKER


def main():
    payload = json.load(sys.stdin)
    E = EllipticCurve(QQ, [QQ(a) for a in payload["a_invariants"]])
    try:
        raw = E.pari_curve().ellheegner()
    except Exception as exc:
        message = str(exc)
        status = "not_applicable" if (
            "analytic rank" in message.lower() or "even analytic" in message.lower()
        ) else "error"
        result = {"status": status, "reason": message[:500], "engine": "PARI ellheegner"}
    else:
        try:
            P = E(QQ(str(raw[0])), QQ(str(raw[1])))
            result = {"status": "point", "point": [str(P[0]), str(P[1])],
                      "engine": "PARI ellheegner"}
        except Exception as exc:
            result = {"status": "error", "reason": str(exc)[:500],
                      "engine": "PARI ellheegner"}
    print(MARKER + json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
