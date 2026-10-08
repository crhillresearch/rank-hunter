"""Isolated exact lower-bound certificate worker.

The certificate is the Cremona/Brumer quadratic-character method used by the
ICARM Elliptic Curve Rank Leaderboard.  This implementation is adapted from
ICARM's Apache-2.0 ``src/verify.ts`` (retrieved 2026-08-21) and executes the
same exact PARI/GP arithmetic inside Sage's PARI interface.

Only exact arithmetic enters the independence decision.  The worker proves
that the supplied points are independent modulo torsion when the stacked
quadratic-character images have the required F_2 rank.  It may perform exact
halving replacements as in the Cremona height-descent argument.  Failure to
certify is not evidence of dependence unless ``status == 'dependent'``.
"""

from __future__ import annotations

import json
import sys

from sage.all import QQ, EllipticCurve, pari

MARKER = "RANK42_EXACT_LB_JSON="
MAX_POINTS = 64
DEFAULT_MAX_HALVINGS = 40

# Adapted from https://github.com/icarm/elliptic-rank/blob/main/src/verify.ts
# under Apache License 2.0.  See THIRD_PARTY_NOTICES.md in the Rank Hunter package.
GP_CERT = [
    "erk_psi(a, p) = (1 - kronecker(a, p)) / 2;",
    "erk_eps(A, pts, p, rs) = {my(k = min(#rs, 2), m = matrix(#pts, k)); "
    "for(i = 1, #pts, if(#pts[i] == 1, next); "
    "my(u = numerator(pts[i][1]), w2 = denominator(pts[i][1])); "
    "for(j = 1, k, my(th = rs[j], a = (u - th*w2) % p); "
    "if(a == 0, a = (3*th^2 + A) % p); "
    "m[i, j] = erk_psi(a, p))); m}",
    "erk_cert(F, pts0, maxhalve, maxprime, maxcols) = {"
    "my(A = F.a4, B = F.a6, D = F.disc, n = #pts0, tor = elltors(F), "
    "   tg = [], tors = [[0]], work = pts0, halved = 0, t); "
    "for(i = 1, #tor[2], if(tor[2][i] % 2 == 0, tg = concat(tg, [tor[3][i]]))); "
    "for(i = 1, #tor[2], my(g = tor[3][i], cur = List()); "
    "  for(k = 0, tor[2][i] - 1, my(gk = ellmul(F, g, k)); "
    "    for(j = 1, #tors, listput(cur, elladd(F, tors[j], gk)))); "
    "  tors = Vec(cur)); "
    "t = #tg; "
    "while(1, "
    "  my(all = concat(work, tg), rows = vector(n + t, i, []), used = List(), "
    "     p = 3, ncols = 0, target = n + t + 10, rall = 0, rt = 0, acted = 0); "
    "  while(!acted, "
    "    while(ncols < target && p < maxprime, "
    "      p = nextprime(p + 1); "
    "      if(D % p == 0, next); "
    "      my(rs = lift(Vec(polrootsmod('x^3 + A*'x + B, p)))); "
    "      if(#rs == 0, next); "
    "      my(blk = erk_eps(A, all, p, rs)); "
    "      for(i = 1, n + t, rows[i] = concat(rows[i], blk[i, ])); "
    "      listput(used, p); "
    "      ncols += min(#rs, 2)); "
    "    my(M = Mod(matrix(n + t, ncols, i, j, rows[i][j]), 2)); "
    "    rall = matrank(M); "
    "    rt = if(t, matrank(Mod(matrix(t, ncols, i, j, rows[n + i][j]), 2)), 0); "
    "    if(rall - rt == n, return([1, Vec(used), rall, rt, halved, []])); "
    "    my(K = lift(matker(M~))); "
    "    for(j = 1, matsize(K)[2], "
    "      my(v = K[, j], c = select(i -> v[i] == 1, [1..n])); "
    "      if(#c == 0, next); "
    "      my(Q = [0]); "
    "      for(i = 1, #c, Q = elladd(F, Q, work[c[i]])); "
    "      if(#Q == 1 || ellorder(F, Q), return([-1, Vec(used), rall, rt, halved, c])); "
    "      my(R = 0, hit = 0); "
    "      for(k = 1, #tors, if(ellisdivisible(F, elladd(F, Q, tors[k]), 2, &R), hit = 1; break)); "
    "      if(hit, "
    "        halved++; "
    "        if(halved > maxhalve, return([0, Vec(used), rall, rt, halved, []])); "
    "        my(ri = 0, rh = -1); "
    "        for(i = 1, #c, "
    "          my(s = ellsub(F, work[c[i]], R), a2 = elladd(F, work[c[i]], R)); "
    "          if(#s == 1 || #a2 == 1 || ellorder(F, s) || ellorder(F, a2), next); "
    "          my(h = exponent(max(1, abs(numerator(work[c[i]][1])))) + exponent(max(1, denominator(work[c[i]][1])))); "
    "          if(h > rh, rh = h; ri = c[i])); "
    "        if(!ri, ri = c[1]); "
    "        work[ri] = R; "
    "        for(i = 1, n, if(i != ri, "
    "          my(s = ellsub(F, work[i], R), a2 = elladd(F, work[i], R)); "
    "          if(#s == 1 || #a2 == 1 || ellorder(F, s) || ellorder(F, a2), "
    "            return([-1, Vec(used), rall, rt, halved, vecsort([i, ri])])))); "
    "        acted = 1; break)); "
    "    if(!acted, "
    "      if(p >= maxprime || ncols >= maxcols, return([0, Vec(used), rall, rt, halved, []])); "
    "      target += n + t + 10)));}",
]


def _gp_vector(values):
    return "[" + ",".join(str(v) for v in values) + "]"


def _point_gp(points):
    return "[" + ",".join(f"[{P[0]},{P[1]}]" for P in points) + "]"


def _parse_vector(text):
    text = str(text).strip()
    if not (text.startswith("[") and text.endswith("]")):
        raise ValueError(f"unexpected GP vector: {text[:100]}")
    inner = text[1:-1].strip()
    if not inner:
        return []
    return [x.strip() for x in inner.split(",")]


def _install_helpers():
    for definition in GP_CERT:
        pari(definition)


def certify(payload):
    ainvs = [QQ(str(x)) for x in payload["a_invariants"]]
    if len(ainvs) == 2:
        ainvs = [QQ(0), QQ(0), QQ(0), ainvs[0], ainvs[1]]
    if len(ainvs) != 5:
        raise ValueError("a_invariants must have two or five entries")
    raw_points = payload.get("points") or []
    if not raw_points:
        raise ValueError("at least one point is required")
    if len(raw_points) > MAX_POINTS:
        raise ValueError(f"at most {MAX_POINTS} points are supported")

    E = EllipticCurve(QQ, ainvs)
    if E.discriminant() == 0:
        raise ValueError("singular curve")
    points = []
    for i, xy in enumerate(raw_points):
        if len(xy) != 2:
            raise ValueError(f"point {i} must be [x,y]")
        P = E(QQ(str(xy[0])), QQ(str(xy[1])))
        if P.is_zero():
            raise ValueError(f"point {i} is the identity; affine [x,y] expected")
        points.append(P)

    # PARI performs the exact global-minimal and short-model transformations.
    pari(f"E = ellinit({_gp_vector(ainvs)})")
    if int(pari("#E")) == 0:
        raise ValueError("PARI reports a singular curve")
    pari("Emin = ellminimalmodel(E, &EminChange)")
    pari(
        "erkV = [1/6, -Emin.b2/12, -Emin.a1/2, "
        "-(Emin.a3 - Emin.b2*Emin.a1/12)/2]"
    )
    pari("erkF = ellinit(ellchangecurve(Emin, erkV))")
    short_ok = int(
        pari(
            'erkF.a1 == 0 && erkF.a2 == 0 && erkF.a3 == 0 && '
            'type(erkF.a4) == "t_INT" && type(erkF.a6) == "t_INT"'
        )
    )
    if short_ok != 1:
        raise RuntimeError("transformation to integral short model failed")

    pts_pairs = [(str(P[0]), str(P[1])) for P in points]
    pari(f"pts = {_point_gp(pts_pairs)}")
    n = len(points)
    pari(
        f"erkPts = vector({n}, i, "
        "ellchangepoint(ellchangepoint(pts[i], EminChange), erkV))"
    )
    if int(pari(f"sum(i=1,{n},ellisoncurve(erkF,erkPts[i]))")) != n:
        raise RuntimeError("points failed to transport to short model")

    _install_helpers()
    max_halvings = int(payload.get("max_halvings", DEFAULT_MAX_HALVINGS))
    max_prime = int(payload.get("max_prime", 1000000))
    max_columns = int(payload.get("max_columns", 640))
    if max_prime < 5:
        raise ValueError("max_prime must be at least 5")
    if max_columns < 1:
        raise ValueError("max_columns must be positive")
    pari(
        f"erkRes = erk_cert(erkF, erkPts, {max_halvings}, "
        f"{max_prime}, {max_columns})"
    )

    status_code = int(pari("erkRes[1]"))
    primes = [int(x) for x in _parse_vector(str(pari("erkRes[2]")))]
    matrix_rank = int(pari("erkRes[3]"))
    torsion_rank = int(pari("erkRes[4]"))
    halvings = int(pari("erkRes[5]"))
    relation = [int(x) - 1 for x in _parse_vector(str(pari("erkRes[6]")))]
    torsion = str(pari("elltors(erkF)[2]"))
    exact_lb = n if status_code == 1 else max(0, matrix_rank - torsion_rank)

    if status_code == 1:
        status = "certified_independent"
    elif status_code == -1:
        status = "dependent"
    else:
        status = "inconclusive"

    return {
        "status": status,
        "independent": status_code == 1,
        "rank_lower_bound": exact_lb,
        "point_count": n,
        "certificate": {
            "method": "exact 2-descent quadratic-character certificate (Cremona/Brumer)",
            "primes": primes,
            "matrix_rank": matrix_rank,
            "torsion_rank": torsion_rank,
            "halvings": halvings,
            "torsion": torsion,
            "max_halvings": max_halvings,
            "max_prime": max_prime,
            "max_columns": max_columns,
        },
        "dependence_subset_indices": relation if status_code == -1 else [],
        "short_model": {
            "a_invariants": [str(pari(f"erkF.a{i}")) for i in (1, 2, 3, 4, 6)],
            "discriminant": str(pari("erkF.disc")),
        },
        "minimal_model": {
            "a_invariants": [str(pari(f"Emin.a{i}")) for i in (1, 2, 3, 4, 6)],
            "c4": str(pari("Emin.c4")),
            "c6": str(pari("Emin.c6")),
            "discriminant": str(pari("Emin.disc")),
        },
        "proof_note": (
            "A certified result proves the supplied points independent modulo torsion. "
            "An inconclusive result is not evidence of dependence."
        ),
    }


def main():
    payload = json.loads(sys.stdin.read())
    try:
        result = certify(payload)
    except BaseException as exc:
        result = {"status": "error", "error": repr(exc)}
        print(MARKER + json.dumps(result, sort_keys=True))
        raise
    print(MARKER + json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
