"""
Isolated descent worker.

Reads one JSON object from stdin. Supported modes:

quick
    Sage E.rank_bound(algorithm="mwrank").

quick_pari
    Sage/PARI 2-descent rank bound. This is a genuinely different engine from
    the default mwrank quick path and is the preferred heavy/error fallback.

quick_selmer
    Direct eclib/mwrank two_descent with selmer_only=True. This remains
    available as a third strategy with a different mwrank search profile.

strong
    Full mwrank two-descent plus generators, height screen, and saturation.

The final machine-readable result is emitted on a line beginning RANK42_JSON=.
Keeping descent in a subprocess lets the controller enforce hard timeouts.
"""

import json
import sys

from sage.all import QQ, EllipticCurve
from sage.libs.eclib.interface import mwrank_EllipticCurve

from rank42.mathutil import height_screen, try_saturation

MARKER = "RANK42_JSON="


def runtime_meta(engine):
    sage_version = None
    engine_version = None
    try:
        from sage.version import version as sage_version_value
        sage_version = str(sage_version_value)
    except Exception:
        pass
    if engine == "pari":
        try:
            from sage.all import pari
            try:
                engine_version = str(pari("version()"))
            except Exception:
                engine_version = sage_version
        except Exception:
            pass
    else:
        engine_version = sage_version
    return {"sage_version": sage_version, "engine_version": engine_version}

def affine_point(E, g):
    P = E(g)
    if P.is_zero():
        return None
    return P

def as_mwrank_curve(E):
    # eclib/mwrank requires integer a-invariants.
    if any(x.denominator() != 1 for x in E.a_invariants()):
        E = E.global_minimal_model()
    M = mwrank_EllipticCurve(list(E.a_invariants()), verbose=False)
    return E, M

def main():
    payload = json.loads(sys.stdin.read())
    ainvs = [QQ(str(x)) for x in payload["a_invariants"]]
    E = EllipticCurve(QQ, ainvs)
    mode = payload.get("mode", "quick")

    if mode == "quick":
        E, _ = as_mwrank_curve(E)
        upper = int(E.rank_bound(algorithm="mwrank"))
        result = {
            "mode": "quick",
            "strategy": "sage_rank_bound_mwrank",
            "a_invariants": [str(x) for x in E.a_invariants()],
            "upper": upper,
            **runtime_meta("mwrank"),
        }
        print(MARKER + json.dumps(result, sort_keys=True))
        return

    if mode == "quick_pari":
        upper = int(E.rank_bound(algorithm="pari"))
        result = {
            "mode": "quick",
            "strategy": "sage_rank_bound_pari",
            "a_invariants": [str(x) for x in E.a_invariants()],
            "upper": upper,
            **runtime_meta("pari"),
        }
        print(MARKER + json.dumps(result, sort_keys=True))
        return

    if mode == "quick_selmer":
        E, M = as_mwrank_curve(E)
        M.two_descent(
            verbose=False,
            selmer_only=True,
            first_limit=int(payload.get("first_limit", 20)),
            second_limit=int(payload.get("second_limit", 10)),
            second_descent=bool(payload.get("second_descent", False)),
        )
        upper = int(M.rank_bound())
        result = {
            "mode": "quick",
            "strategy": "mwrank_selmer_only",
            "a_invariants": [str(x) for x in E.a_invariants()],
            "upper": upper,
            **runtime_meta("mwrank"),
        }
        print(MARKER + json.dumps(result, sort_keys=True))
        return

    if mode != "strong":
        raise SystemExit(f"unknown mode: {mode}")

    E, M = as_mwrank_curve(E)
    M.two_descent(
        verbose=False,
        selmer_only=False,
        first_limit=int(payload.get("first_limit", 40)),
        second_limit=int(payload.get("second_limit", 20)),
        second_descent=bool(payload.get("second_descent", True)),
    )

    lower = int(M.rank())
    upper = int(M.rank_bound())
    certain = bool(M.certain())

    points = []
    for g in M.gens():
        P = affine_point(E, g)
        if P is not None:
            # Construction through E() is an exact membership check.
            points.append(P)

    precision = int(payload.get("precision", 256))
    screen = height_screen(E, points, precision=precision)

    saturation = None
    sat_to = int(payload.get("saturate_to", 0))
    if sat_to > 0 and points:
        saturation = try_saturation(E, points, max_prime=sat_to)

    try:
        regulator = str(M.regulator())
    except Exception:
        regulator = screen.get("determinant")

    result = {
        "mode": "strong",
        "strategy": "mwrank_full_two_descent",
        "a_invariants": [str(x) for x in E.a_invariants()],
        "lower": lower,
        "upper": upper,
        "certain": certain,
        "regulator": regulator,
        "points": [[str(P[0]), str(P[1])] for P in points],
        "height_screen": screen,
        "saturation": saturation,
        **runtime_meta("mwrank"),
    }
    print(MARKER + json.dumps(result, sort_keys=True))

if __name__ == "__main__":
    main()
