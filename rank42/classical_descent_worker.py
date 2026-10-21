#!/usr/bin/env python3
"""Isolated classical 2-descent worker for record-hunt escalation.

No eclib-rh path exists here. Supported operations are:
* simon_known      — Denis Simon two-descent with supplied known points;
* mwrank_selmer    — standard eclib/mwrank selmer-only 2-descent, returning rank_bound();
* mwrank_coverings — standard eclib/mwrank full 2-descent / quartic search.

Every point emitted by the worker is reconstructed exactly on the input curve.
"""
from __future__ import annotations

import json
import sys
import warnings

from sage.all import QQ, EllipticCurve

MARKER = "RANK42_CLASSICAL_DESCENT_RESULT="


def _point_payload(P):
    return [str(P[0]), str(P[1])]


def _curve(payload):
    ainvs = [QQ(str(x)) for x in payload["a_invariants"]]
    if len(ainvs) != 5:
        raise ValueError("a_invariants must have length 5")
    E = EllipticCurve(QQ, ainvs)
    known = []
    for idx, xy in enumerate(payload.get("known_points") or []):
        if not isinstance(xy, (list, tuple)) or len(xy) < 2:
            raise ValueError(f"known point {idx} is malformed")
        P = E(QQ(str(xy[0])), QQ(str(xy[1])))
        if not P.is_zero():
            known.append(P)
    return E, known


def _mwrank_curve(E):
    from sage.libs.eclib.interface import mwrank_EllipticCurve

    Em = E.global_minimal_model()
    if any(a.denominator() != 1 for a in Em.a_invariants()):
        raise RuntimeError("mwrank minimal model is not integral")
    M = mwrank_EllipticCurve([int(a) for a in Em.a_invariants()], verbose=False)
    iso = None if list(Em.a_invariants()) == list(E.a_invariants()) else Em.isomorphism_to(E)
    return Em, M, iso


def _map_mwrank_gens(Em, M, iso):
    out = []
    seen = set()
    for g in M.gens():
        Pm = Em(g)
        P = Pm if iso is None else iso(Pm)
        if P.is_zero():
            continue
        key = (QQ(P[0]), QQ(P[1]))
        if key in seen:
            continue
        seen.add(key)
        out.append(P)
    return out


def run_mwrank(payload, *, selmer_only):
    E, _known = _curve(payload)
    Em, M, iso = _mwrank_curve(E)
    M.two_descent(
        verbose=bool(payload.get("verbose", False)),
        selmer_only=bool(selmer_only),
        first_limit=int(payload.get("first_limit", 20)),
        second_limit=int(payload.get("second_limit", 10)),
        n_aux=int(payload.get("n_aux", -1)),
        second_descent=bool(payload.get("second_descent", True)),
    )
    upper = int(M.rank_bound())
    lower = int(M.rank()) if not selmer_only else None
    points = [] if selmer_only else _map_mwrank_gens(Em, M, iso)
    return {
        "engine": "mwrank_selmer" if selmer_only else "mwrank_coverings",
        "status": "completed",
        "rigorous_upper": upper,
        "bound_kind": "mwrank_rank_bound",
        "raw_selmer_rank": None,
        "descent_lower": lower,
        "certain": bool(M.certain()) if not selmer_only else None,
        "points": [_point_payload(P) for P in points],
        "minimal_model_a_invariants": [str(a) for a in Em.a_invariants()],
        "options": {
            "first_limit": int(payload.get("first_limit", 20)),
            "second_limit": int(payload.get("second_limit", 10)),
            "n_aux": int(payload.get("n_aux", -1)),
            "second_descent": bool(payload.get("second_descent", True)),
            "selmer_only": bool(selmer_only),
        },
    }


def run_simon(payload):
    E, known = _curve(payload)
    kwargs = {
        "verbose": int(payload.get("verbose", 0)),
        "lim1": int(payload.get("lim1", 5)),
        "lim3": int(payload.get("lim3", 80)),
        "limtriv": int(payload.get("limtriv", 3)),
        "maxprob": int(payload.get("maxprob", 20)),
        "limbigprime": int(payload.get("limbigprime", 0)),
        "known_points": known,
    }
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        lower, upper, points = E.simon_two_descent(**kwargs)

    exact = []
    seen = set()
    for raw in points:
        P = E(raw)
        if P.is_zero():
            continue
        key = (QQ(P[0]), QQ(P[1]))
        if key in seen:
            continue
        seen.add(key)
        exact.append(P)
    deterministic_upper = int(kwargs["limbigprime"]) == 0
    return {
        "engine": "simon_known",
        "status": "completed",
        "reported_upper": int(upper),
        "rigorous_upper": int(upper) if deterministic_upper else None,
        "upper_bound_rigorous": bool(deterministic_upper),
        "upper_bound_scope": (
            "deterministic_simon_local_tests"
            if deterministic_upper
            else "probabilistic_simon_large_prime_local_tests"
        ),
        "deprecated_engine": True,
        "descent_lower": int(lower),
        "points": [_point_payload(P) for P in exact],
        "known_points_supplied": len(known),
        "options": {k: v for k, v in kwargs.items() if k != "known_points"},
    }


def main():
    payload = json.loads(sys.stdin.read())
    mode = str(payload.get("mode") or "simon_known")
    if mode == "simon_known":
        result = run_simon(payload)
    elif mode == "mwrank_selmer":
        result = run_mwrank(payload, selmer_only=True)
    elif mode == "mwrank_coverings":
        result = run_mwrank(payload, selmer_only=False)
    else:
        raise ValueError(f"unsupported mode {mode!r}")
    print(MARKER + json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:
        print(MARKER + json.dumps({"status": "error", "error": repr(exc)}, sort_keys=True))
        raise
