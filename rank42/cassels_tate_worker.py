"""Isolated PARI 2-descent and Cassels--Tate refinement worker."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from sage.all import QQ, EllipticCurve, pari

RESULT_MARKER = "RANK42_CASSELS_TATE_RESULT="


def _version(module, attribute):
    try:
        return str(getattr(__import__(module, fromlist=[attribute]), attribute))
    except Exception:
        return None


def _emit(payload):
    print(RESULT_MARKER + json.dumps(payload, sort_keys=True), flush=True)


def main():
    payload = json.loads(sys.stdin.read())
    effort = int(payload.get("effort", 0))
    if effort < 0:
        raise ValueError("effort must be non-negative")

    E = EllipticCurve(
        QQ,
        [QQ(str(x)) for x in payload["a_invariants"]],
    ).global_minimal_model()

    started = datetime.now(timezone.utc).isoformat()
    raw = E.pari_curve().ellrank(effort)
    finished = datetime.now(timezone.utc).isoformat()

    lower = int(raw[0])
    refined_upper = int(raw[1])
    pairing_rank = int(raw[2])
    if lower < 0 or refined_upper < lower:
        raise RuntimeError("PARI returned an invalid Mordell-Weil rank interval")
    if pairing_rank < 0 or pairing_rank % 2:
        raise RuntimeError("PARI returned an invalid Cassels pairing rank")

    points = []
    for point in raw[3]:
        points.append([str(point[0]), str(point[1])])

    _emit({
        "status": "completed",
        "engine": "pari.ellrank_cassels_pairing",
        "engine_version": str(pari("version()")),
        "sage_version": _version("sage.version", "version"),
        "minimal_model_a_invariants": [str(x) for x in E.a_invariants()],
        "effort": effort,
        "mordell_weil_rank_lower_diagnostic": lower,
        "mordell_weil_rank_upper": refined_upper,
        "pre_pairing_selmer_upper": refined_upper + pairing_rank,
        "cassels_tate_pairing_rank": pairing_rank,
        "refinement_amount": pairing_rank,
        "pari_points_diagnostic": points,
        "pairing_computed": True,
        "unconditional": True,
        "assumptions": [],
        "started_at": started,
        "finished_at": finished,
    })


if __name__ == "__main__":
    main()
