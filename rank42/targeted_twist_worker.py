"""Isolated Sage worker for one Targeted Twist trial."""
from __future__ import annotations

import json
import sys
import time
import traceback

from sage.all import EllipticCurve, QQ, ZZ

from rank42.prime_local import direct_nagao_log_cardinality_score

MARKER = "RANK42_TARGETED_TWIST_RESULT="


def main():
    started = time.monotonic()
    try:
        request = json.load(sys.stdin)
        E = EllipticCurve(
            QQ, [QQ(str(x)) for x in request["a_invariants"]]
        )
        d = int(request["twist_d"])
        child = E.quadratic_twist(ZZ(d))
        sign = int(child.root_number())
        requested_sign = str(request.get("root_sign") or "any")
        if (
            (requested_sign == "+1" and sign != 1)
            or (requested_sign == "-1" and sign != -1)
        ):
            result = {
                "status": "filtered",
                "reason": "root_sign_mismatch",
                "twist_d": d,
                "root_number": sign,
                "runtime_seconds": time.monotonic() - started,
            }
        else:
            score = direct_nagao_log_cardinality_score(
                child,
                prime_bound=int(request["prime_bound"]),
            )
            result = {
                "status": str(score.get("status") or "inconclusive"),
                "twist_d": d,
                "root_number": sign,
                "a_invariants": [str(x) for x in child.a_invariants()],
                "score": score,
                "runtime_seconds": time.monotonic() - started,
            }
        print(MARKER + json.dumps(result, sort_keys=True), flush=True)
        return 0
    except Exception as exc:
        result = {
            "status": "error",
            "reason": "targeted twist worker failed",
            "error": repr(exc),
            "traceback_tail": traceback.format_exc().splitlines()[-20:],
            "runtime_seconds": time.monotonic() - started,
        }
        print(MARKER + json.dumps(result, sort_keys=True), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
