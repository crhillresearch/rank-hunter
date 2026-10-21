#!/usr/bin/env python3
"""Isolated exact rational-torsion worker.

The parent process owns timeout enforcement and persistence. This worker only
reconstructs the Q-curve, runs Sage's exact torsion computation, and emits one
machine-readable result marker.
"""
from __future__ import annotations

import json
import sys

from sage.all import EllipticCurve, QQ

from rank42.torsion import compute_torsion_data


MARKER = "RANK42_TORSION_RESULT="


def main():
    try:
        payload = json.load(sys.stdin)
        ainvs = payload.get("a_invariants") or []
        if len(ainvs) != 5:
            raise ValueError("exact torsion worker requires five a-invariants")
        E = EllipticCurve(QQ, [QQ(str(value)) for value in ainvs])
        result = {
            "status": "completed",
            **compute_torsion_data(E),
        }
        print(MARKER + json.dumps(result, sort_keys=True), flush=True)
        return 0
    except Exception as exc:
        print(
            MARKER
            + json.dumps(
                {"status": "error", "error": repr(exc)},
                sort_keys=True,
            ),
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
