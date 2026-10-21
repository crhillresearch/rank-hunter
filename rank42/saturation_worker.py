"""Isolated exact subgroup-saturation worker."""
from __future__ import annotations

import json
import sys

from sage.all import QQ, EllipticCurve

MARKER = "RANK42_SATURATION_RESULT="


def _point_payload(P):
    return [str(P[0]), str(P[1])]


def _runtime_meta():
    try:
        from sage.version import version as sage_version
        version = str(sage_version)
    except Exception:
        version = None
    return {"sage_version": version, "engine_version": version}


def main():
    payload = json.loads(sys.stdin.read())
    ainvs = [QQ(str(x)) for x in payload["a_invariants"]]
    if len(ainvs) != 5:
        raise ValueError("a_invariants must have length 5")
    E = EllipticCurve(QQ, ainvs)
    points = []
    for idx, raw in enumerate(payload.get("points") or []):
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            raise ValueError(f"point {idx} is malformed")
        P = E(QQ(str(raw[0])), QQ(str(raw[1])))
        if not P.is_zero():
            points.append(P)
    if not points:
        raise ValueError("no nonzero exact points supplied")

    max_prime = max(2, int(payload.get("max_prime") or 100))
    min_prime = max(2, int(payload.get("min_prime") or 2))
    if min_prime > max_prime:
        raise ValueError("min_prime cannot exceed max_prime")
    model_used = "stored"
    model_warning = None
    work_curve = E
    work_points = points
    to_stored = lambda P: E(P)

    try:
        Em = E.global_minimal_model()
        if list(Em.a_invariants()) != list(E.a_invariants()):
            to_minimal = E.isomorphism_to(Em)
            from_minimal = Em.isomorphism_to(E)
            work_points = [to_minimal(P) for P in points]
            work_curve = Em
            to_stored = lambda P: from_minimal(P)
            model_used = "global_minimal"
        else:
            work_curve = Em
            model_used = "global_minimal"
    except Exception as exc:
        model_warning = repr(exc)

    sat, index, regulator = work_curve.saturation(
        work_points,
        max_prime=max_prime,
        min_prime=min_prime,
    )
    stored_sat = []
    for P in sat:
        Q = to_stored(P)
        Q = E(Q)
        if Q.is_zero():
            continue
        stored_sat.append(Q)

    result = {
        "status": "completed",
        "attempted": True,
        "max_prime": max_prime,
        "min_prime": min_prime,
        "index": str(index),
        "regulator": str(regulator),
        "saturated_points": [_point_payload(P) for P in stored_sat],
        "model_used": model_used,
        "model_warning": model_warning,
        "minimal_model_a_invariants": [str(x) for x in work_curve.a_invariants()],
        **_runtime_meta(),
    }
    print(MARKER + json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:
        print(
            MARKER
            + json.dumps(
                {"status": "error", "attempted": True, "error": repr(exc)},
                sort_keys=True,
            )
        )
        raise
