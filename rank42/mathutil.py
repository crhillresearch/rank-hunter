import math
from sage.all import ZZ, QQ, prime_divisors

def curve_summary(E):
    Em = E.minimal_model()
    disc = ZZ(Em.discriminant())
    out = {
        "a_invariants": [str(x) for x in Em.a_invariants()],
        "discriminant": str(disc),
        "bad_primes": [int(p) for p in prime_divisors(abs(disc))],
    }
    try:
        out["conductor"] = str(Em.conductor())
    except Exception as exc:
        out["conductor_error"] = repr(exc)
    try:
        out["root_number"] = int(Em.root_number())
    except Exception as exc:
        out["root_number_error"] = repr(exc)
    return out

def find_small_points(E, x_bound):
    """
    Correct but intentionally basic point search:
    try integral x in [-x_bound, x_bound] and ask Sage to lift x.

    This is NOT the final record-scale point finder. It is useful for smoke tests,
    regression tests, and detecting easy rank jumps.
    """
    pts = []
    seen = set()
    for x in range(-int(x_bound), int(x_bound) + 1):
        try:
            lifted = E.lift_x(QQ(x), all=True)
        except Exception:
            continue
        for P in lifted:
            if P.is_zero():
                continue
            key = (QQ(P[0]), QQ(P[1]))
            if key not in seen:
                seen.add(key)
                pts.append(P)
    return pts

def height_screen(E, points, precision=256):
    """
    Numerical high-precision independence screen.

    A positive-definite Neron-Tate height matrix strongly supports independence.
    This is a screening/certification aid, not a claim of a machine-formal proof.
    """
    if not points:
        return {
            "count": 0,
            "precision_bits": precision,
            "determinant": "1",
            "min_eigenvalue": None,
            "positive_definite_screen": True,
        }
    H = E.height_pairing_matrix(points, precision=precision)
    det = H.det()
    eigs = H.eigenvalues()
    mineig = min(eigs)
    return {
        "count": len(points),
        "precision_bits": precision,
        "determinant": str(det),
        "min_eigenvalue": str(mineig),
        "positive_definite_screen": bool(mineig > 0),
    }

def independent_subset_greedy(E, points, precision=192):
    """
    Greedily retain P if adding it leaves the height matrix numerically
    positive definite. This is intentionally conservative.
    """
    chosen = []
    last = height_screen(E, chosen, precision)
    for P in points:
        trial = chosen + [P]
        try:
            info = height_screen(E, trial, precision)
        except Exception:
            continue
        if info["positive_definite_screen"]:
            chosen = trial
            last = info
    return chosen, last

def try_saturation(E, points, max_prime=50):
    if not points or int(max_prime) <= 0:
        return {"attempted": False}
    try:
        sat, index, reg = E.saturation(points, max_prime=max_prime)
        return {
            "attempted": True,
            "max_prime": max_prime,
            "index": str(index),
            "regulator": str(reg),
            "saturated_points": [[str(P[0]), str(P[1])] for P in sat],
        }
    except Exception as exc:
        return {
            "attempted": True,
            "max_prime": max_prime,
            "error": repr(exc),
        }
