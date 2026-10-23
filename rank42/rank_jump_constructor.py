"""Exact bounded search for quadratic multisections of a formula family.

The conjugate of the constructed point is its elliptic negative. A non-torsion
specialization therefore proves that it adds a direction independent of every
section defined over the original function field.
"""
from __future__ import annotations

import itertools


MAX_RATIONAL_TORSION_ORDER = 12  # Mazur's theorem, including noncyclic groups.


def _rational_samples(QQ, bound):
    seen = set()
    for denominator in range(1, bound + 1):
        for numerator in range(-bound, bound + 1):
            value = QQ(numerator) / denominator
            if value not in seen:
                seen.add(value)
                yield value


def _squarefree_part(polynomial, R):
    if polynomial == 0:
        return R(0), R(0)
    decomposition = polynomial.squarefree_decomposition()
    square = R(1)
    branch = R(decomposition.unit())
    for factor, exponent in decomposition:
        square *= factor ** (int(exponent) // 2)
        if int(exponent) % 2:
            branch *= factor
    return branch, square


def _cover_point(branch, QQ, bound):
    return next(_cover_points(branch, QQ, bound), None)


def _cover_points(branch, QQ, bound):
    degree = branch.degree()
    seen = set()
    if degree == 1:
        # Every nonconstant linear cover is rational. Scanning small t misses
        # points when the coefficients of the source family are large.
        for w in _rational_samples(QQ, bound):
            t = (w*w - branch[0]) / branch[1]
            if (t, w) not in seen:
                seen.add((t, w))
                yield t, w
    elif degree == 2:
        # A line w = constant intersects this conic at rational t precisely
        # when the following discriminant is a rational square.
        for w in _rational_samples(QQ, bound):
            discriminant = branch[1]**2 - 4*branch[2]*(branch[0] - w*w)
            if discriminant >= 0 and discriminant.is_square():
                root = discriminant.sqrt()
                for sign in (1, -1):
                    t = (-branch[1] + sign*root)/(2*branch[2])
                    if (t, w) not in seen:
                        seen.add((t, w))
                        yield t, w
    for t in _rational_samples(QQ, bound):
        value = QQ(branch(t))
        if value >= 0 and value.is_square():
            w = value.sqrt()
            if (t, w) not in seen:
                seen.add((t, w))
                yield t, w


def _non_torsion_prime_witness(E, P):
    from sage.all import GF, prime_range

    for p in prime_range(17, 101):
        try:
            finite_curve = E.change_ring(GF(p))
            if finite_curve.discriminant() == 0:
                continue
            residue = finite_curve(GF(p)(P[0]), GF(p)(P[1]))
            order = int(residue.order())
            if order > MAX_RATIONAL_TORSION_ORDER:
                return {"prime": int(p), "reduction_order": order,
                        "method": "good_reduction_order_exceeds_rational_torsion_maximum"}
        except (ArithmeticError, ValueError, TypeError, ZeroDivisionError):
            continue
    return None


def _map_from_point(branch, point, U, *, anchor_at_zero=False):
    degree = branch.degree()
    if degree == 1:
        w = U + point[1] if anchor_at_zero else U
        return (w * w - branch[0]) / branch[1], w
    t0, w0 = point
    slope = U
    tmap = t0 + (branch[1] + 2 * branch[2] * t0 - 2 * w0 * slope) / (slope * slope - branch[2])
    return tmap, w0 + slope * (tmap - t0)


def discover(ainvs, t, K, known, config):
    from sage.all import QQ, EllipticCurve, PolynomialRing, sage_eval

    R = PolynomialRing(QQ, str(t))
    degree = max(0, min(int(config.get("degree", 1)), 3))
    coefficient_bound = max(1, min(int(config.get("coefficient_bound", 2)), 6))
    max_trials = max(1, min(int(config.get("max_trials", 1000)), 20000))
    max_covers = max(1, min(int(config.get("max_results", 8)), 32))
    point_bound = max(1, min(int(config.get("point_bound", 8)), 32))
    allowed_degree = max(2, min(int(config.get("cover_degree", 4)), 4))
    include_rational = bool(config.get("rational_x", True))
    values = range(-coefficient_bound, coefficient_bound + 1)
    denominators = [R(1)] + ([t + c for c in values] if include_rational else [])
    a1, a2, a3, a4, a6 = ainvs
    covers = []
    seen = set()
    trials = 0
    anchor = config.get("seed_anchor") if isinstance(config.get("seed_anchor"), dict) else None
    def candidate_coordinates():
        if anchor:
            yield K(QQ(anchor["point"][0])), "certified_seed_point"
        for denominator in denominators:
            for d in range(degree + 1):
                for coefficients in itertools.product(values, repeat=d + 1):
                    if d and coefficients[-1] == 0:
                        continue
                    x = K(sum((QQ(c) * t ** i for i, c in enumerate(coefficients)), R(0))) / denominator
                    yield x, "bounded_ansatz"

    for x, origin in candidate_coordinates():
        if trials >= max_trials or len(covers) >= max_covers:
            break
        trials += 1
        delta = (a1*x+a3)**2 + 4*(x**3+a2*x*x+a4*x+a6)
        if delta == 0:
            continue
        # delta = (square / denominator)^2 * branch, exactly in Q(t).
        product = R(delta.numerator() * delta.denominator())
        branch, square = _squarefree_part(product, R)
        if branch.degree() < 1 or branch.degree() > allowed_degree:
            continue
        key = tuple(map(str, branch.list()))
        if key in seen:
            continue
        multiplier = K(square) / delta.denominator()
        if multiplier**2 * branch != delta:
            raise ArithmeticError("quadratic discriminant factorization failed")
        seen.add(key)
        genus = int((branch.degree() - 1) // 2)
        point = _cover_point(branch, QQ, point_bound)
        rec = {
            "x": str(x), "candidate_origin": origin,
            "branch_polynomial": str(branch),
            "square_multiplier": str(multiplier),
            "cover_equation": f"w^2 = {branch}",
            "cover_genus": genus, "cover_degree": int(branch.degree()),
            "identity_verified": True,
            "rational_cover_point": [str(v) for v in point] if point else None,
            "independence": "not_certified",
        }
        if anchor and origin == "certified_seed_point":
            t0 = QQ(anchor["parameter"])
            xp, yp = (QQ(value) for value in anchor["point"])
            if x(t0) != xp:
                raise ValueError("seed x-coordinate does not match the source fiber")
            at_seed = [QQ(a(t0)) for a in ainvs]
            seed_curve = EllipticCurve(QQ, at_seed)
            seed_curve(xp, yp)
            multiplier_at_seed = QQ(multiplier(t0))
            if multiplier_at_seed == 0:
                raise ValueError("seed cover multiplier vanishes at the source fiber")
            w0 = (2*yp + at_seed[0]*xp + at_seed[2]) / multiplier_at_seed
            if w0*w0 != branch(t0):
                raise ArithmeticError("the selected point does not lift to the cover")
            rec["seed_fiber"] = {"curve_id": int(anchor["curve_id"]),
                                 "parameter": str(t0), "cover_point": [str(t0), str(w0)],
                                 "source_point_id": int(anchor["point_id"]),
                                 "source_fiber_retained": True}
            point = (t0, w0)
            rec["rational_cover_point"] = [str(t0), str(w0)]
        if genus == 0 and point:
            S = PolynomialRing(QQ, "u")
            L = S.fraction_field()
            u = L.gen()
            parameter_map, wmap = _map_from_point(
                branch, point, u, anchor_at_zero=bool(rec.get("seed_fiber")))
            source_parameter = str(t)
            def pull(value):
                expression = str(value).replace("^", "**")
                return L(sage_eval(expression, locals={source_parameter: parameter_map}))
            new_ainvs = [pull(a) for a in ainvs]
            new_x = pull(x)
            new_y = (-new_ainvs[0]*new_x-new_ainvs[2]+pull(multiplier)*wmap)/2
            derived = EllipticCurve(L, new_ainvs)
            new_section = derived(new_x, new_y)
            if new_section.is_zero():
                raise ArithmeticError("constructed section is zero")
            inherited = []
            for old in known:
                old_x, old_y = pull(old["x"]), pull(old["y"])
                derived(old_x, old_y)
                inherited.append({"label": old["label"], "x": str(old_x),
                                  "y": str(old_y), "verification": "exact_identity"})
            rec["parameter_map"] = str(parameter_map)
            rec["cover_square_root"] = str(wmap)
            rec["derived_family"] = {
                "parameter": "u", "a_invariants": [str(a) for a in new_ainvs],
                "sections": inherited + [{"label": "Quadratic multisection", "x": str(new_x),
                                          "y": str(new_y), "verification": "exact_identity"}],
            }
            if rec.get("seed_fiber") and branch.degree() == 1:
                rec["seed_fiber"]["derived_parameter"] = "0"
            for sample in (QQ(0), QQ(1), QQ(-1), QQ(2), QQ(-2), QQ(1)/2, QQ(-1)/2):
                try:
                    specialized = EllipticCurve(QQ, [QQ(a(sample)) for a in new_ainvs])
                    specialized(QQ(new_x(sample)), QQ(new_y(sample)))
                    rec["derived_family"]["validation_parameter"] = str(sample)
                    break
                except (ArithmeticError, ValueError, ZeroDivisionError):
                    continue
        # One non-torsion specialization proves this anti-invariant
        # section is independent of every Q(t)-section. This does not
        # certify independence among the supplied old sections.
        if point:
            for witness_index, (tp, wp) in enumerate(_cover_points(branch, QQ, point_bound)):
                if witness_index >= 12:
                    break
                try:
                    specialized = [QQ(a(tp)) for a in ainvs]
                    E = EllipticCurve(QQ, specialized)
                    xp = QQ(x(tp))
                    yp = (-specialized[0]*xp-specialized[2]+QQ(multiplier(tp))*wp)/2
                    P = E(xp, yp)
                    proof = _non_torsion_prime_witness(E, P)
                    if proof:
                        rec["independence"] = "certified_over_quadratic_cover"
                        rec["non_torsion_witness"] = {
                            "t": str(tp), "w": str(wp), "point": [str(xp), str(yp)],
                            **proof,
                        }
                        break
                except (ZeroDivisionError, ValueError, ArithmeticError):
                    pass
        covers.append(rec)
        if len(covers) >= max_covers:
            break
    return {"covers": covers, "x_candidates_checked": trials,
            "coverage": "bounded_partial" if trials >= max_trials or len(covers) >= max_covers else "bounded_complete",
            "degree_limit": degree, "coefficient_bound": coefficient_bound,
            "point_search_bound": point_bound}
