"""Isolated Sage worker for GRH-conditional Mestre-Bober analytic-rank bounds."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from sage.all import QQ, EllipticCurve

RESULT_MARKER = "RANK42_ANALYTIC_UPPER_RESULT="


def _sage_version():
    try:
        from sage.version import version
        return str(version)
    except Exception:
        return None


def main():
    payload = json.loads(sys.stdin.read())
    E = EllipticCurve(QQ, [QQ(str(x)) for x in payload["a_invariants"]])
    Em = E.global_minimal_model()
    max_delta = float(payload["max_delta"])
    adaptive = bool(payload.get("adaptive", True))
    ncpus = int(payload.get("ncpus") or 1)

    started = datetime.now(timezone.utc).isoformat()
    upper = int(
        Em.analytic_rank_upper_bound(
            max_Delta=max_delta,
            adaptive=adaptive,
            root_number="compute",
            ncpus=ncpus,
        )
    )
    finished = datetime.now(timezone.utc).isoformat()

    result = {
        "status": "completed",
        "engine": "sage.mestre_bober_zero_sum",
        "engine_version": _sage_version(),
        "sage_version": _sage_version(),
        "evidence_type": "conditional_analytic_upper",
        "rigorous": False,
        "rigorous_lower": None,
        "rigorous_upper": None,
        "exact_rank": None,
        "conditional_analytic_upper": upper,
        "numerical_rank_signal": None,
        "assumptions": ["Generalized Riemann Hypothesis for L(E,s)"],
        "points_found": [],
        "minimal_model_a_invariants": [str(x) for x in Em.a_invariants()],
        "options": {
            "method": "sage_analytic_rank_upper_bound",
            "max_delta": max_delta,
            "adaptive": adaptive,
            "root_number": "compute",
            "ncpus": ncpus,
        },
        "started_at": started,
        "finished_at": finished,
    }
    print(RESULT_MARKER + json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
