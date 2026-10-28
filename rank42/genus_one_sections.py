"""Exact bounded section search over a quadratic genus-one parameter base."""

from __future__ import annotations

import itertools


DEFAULT_SCALES = (
    "-8", "-5", "-4", "-3", "-2", "-1", "-1/2", "-1/3", "-1/4", "-1/5", "-1/8",
    "1/8", "1/5", "1/4", "1/3", "1/2", "1", "2", "3", "4", "5", "8",
)


def _square_root_fraction(value, field):
    product = value.numerator() * value.denominator()
    if not product.is_square():
        return None
    root = field(product.sqrt()) / value.denominator()
    return root if root*root == value else None


def run(family, config):
    from sage.all import EllipticCurve, FunctionField, PolynomialRing, QQ, sage_eval

    name = str(family.get("parameter") or "u")
    if not name.isidentifier() or name == "v":
        raise ValueError("parameter must be an identifier other than v")
    ring = PolynomialRing(QQ, name)
    field = ring.fraction_field()
    u = field.gen()

    def parse(expr):
        return field(sage_eval(str(expr), locals={name: u}))

    raw_ainvs = family.get("a_invariants")
    if not isinstance(raw_ainvs, list) or len(raw_ainvs) != 5:
        raise ValueError("genus-one family needs five Weierstrass coefficients")
    ainvs = [parse(item) for item in raw_ainvs]
    F = parse(family.get("base_quartic") or "")
    if F.denominator() != 1 or F.numerator().degree() not in (3, 4):
        raise ValueError("base must be a degree-3 or degree-4 polynomial v^2=F(u)")
    if F.numerator().gcd(F.numerator().derivative()) != 1:
        raise ValueError("base polynomial has a repeated root")
    base_field = FunctionField(QQ, name)
    base_u = base_field.gen()
    extension_ring = PolynomialRing(base_field, "z")
    v = base_field.extension(
        extension_ring.gen()**2 - base_field(F.numerator()), "v"
    ).gen()
    extension = v.parent()

    def parse_on_cover(expr):
        return extension(sage_eval(str(expr), locals={name: base_u, "v": v}))

    E = EllipticCurve(extension, [parse_on_cover(item) for item in raw_ainvs])
    known = []
    invalid = []
    for index, item in enumerate(family.get("sections") or [], 1):
        try:
            x = parse_on_cover(item["x"])
            y = parse_on_cover(item["y"])
            point = E(x, y)
            known.append((str(item.get("label") or f"S{index}"), point))
        except Exception as exc:
            invalid.append({"index": index, "reason": str(exc)[:240]})

    manual = None
    candidate_x = str(config.get("candidate_x") or "").strip()
    candidate_y = str(config.get("candidate_y") or "").strip()
    if candidate_x or candidate_y:
        if not candidate_x or not candidate_y:
            raise ValueError("candidate section needs both x(u,v) and y(u,v)")
        point = E(parse_on_cover(candidate_x), parse_on_cover(candidate_y))
        manual = {"x": str(point[0]), "y": str(point[1]),
                  "relationship": _relationship(point, known, E),
                  "verification": "exact_identity"}

    factors_raw = family.get("x_factors") or []
    if not isinstance(factors_raw, list) or not 1 <= len(factors_raw) <= 6:
        raise ValueError("supply one to six rational x factors")
    factors = [parse(item) for item in factors_raw]
    if any(f == 0 for f in factors):
        raise ValueError("x factors must be nonzero")
    ranges_raw = family.get("x_exponent_ranges")
    if ranges_raw is None:
        ranges_raw = [[-1, 3] for _ in factors]
    if len(ranges_raw) != len(factors):
        raise ValueError("each x factor needs an exponent range")
    ranges = []
    for pair in ranges_raw:
        lo, hi = int(pair[0]), int(pair[1])
        if lo < -3 or hi > 12 or hi < lo:
            raise ValueError("x exponent ranges must lie within -3..12")
        ranges.append(range(lo, hi + 1))
    scales = [QQ(value) for value in (family.get("x_scales") or DEFAULT_SCALES)]
    max_trials = max(1, min(int(config.get("max_trials", 100000)), 250000))
    max_results = max(1, min(int(config.get("max_results", 32)), 64))
    sample_bound = max(2, min(int(config.get("sample_bound", 40)), 80))
    sample_count = max(1, min(int(config.get("sample_count", 4)), 8))
    max_degree = max(0, min(int(config.get("max_degree", 12)), 30))
    max_pole = max(0, min(int(config.get("max_pole", 6)), 20))
    samples = _samples(ainvs, F, factors, sample_bound, sample_count)
    found = []
    trials = survivors = 0
    seen_x = set()
    stopped = False
    for exponents in itertools.product(*ranges):
        degree = sum(exponent * max(0, factor.numerator().degree() - factor.denominator().degree())
                     for factor, exponent in zip(factors, exponents))
        pole = sum(max(0, -exponent) * factor.numerator().degree()
                   for factor, exponent in zip(factors, exponents))
        if degree < -2 or degree > max_degree or pole > max_pole:
            continue
        values = [tuple(factor(t) for factor in factors) for t, _a, _b in samples]
        monomials = [QQ(1)] * len(samples)
        for index, factor_values in enumerate(values):
            for value, exponent in zip(factor_values, exponents):
                monomials[index] *= value**exponent
        for scale in scales:
            trials += 1
            if trials > max_trials:
                stopped = True
                break
            for (t, aa, ff), value in zip(samples, monomials):
                x = scale*value
                delta = (aa[0]*x+aa[2])**2 + 4*(x**3+aa[1]*x*x+aa[3]*x+aa[4])
                if not delta.is_square():
                    break
            else:
                survivors += 1
                x = scale
                for factor, exponent in zip(factors, exponents):
                    x *= factor**exponent
                if str(x) in seen_x:
                    continue
                seen_x.add(str(x))
                a1, a2, a3, a4, a6 = ainvs
                delta = (a1*x+a3)**2 + 4*(x**3+a2*x*x+a4*x+a6)
                root = _square_root_fraction(delta/F, field)
                if root is None:
                    continue
                x_ext = extension(base_field(x))
                y_ext = (-extension(base_field(a1*x+a3)) + v*base_field(root))/2
                point = E(x_ext, y_ext)
                relation = _relationship(point, known, E)
                found.append({
                    "x": str(x), "y": str(point[1]),
                    "verification": "exact_identity",
                    "relationship": relation,
                    "exponents": list(exponents), "scale": str(scale),
                    "independence": "not_certified" if relation == "unresolved" else "dependent_or_torsion",
                })
                if len(found) >= max_results:
                    stopped = True
                    break
        if stopped:
            break
    return {
        "parameter": name, "base_quartic": str(F),
        "known_sections_verified": [{"label": label, "x": str(P[0]), "y": str(P[1])}
                                    for label, P in known],
        "known_sections_invalid": invalid,
        "manual_candidate": manual,
        "sections": found,
        "x_candidates_checked": min(trials, max_trials),
        "screen_survivors": survivors,
        "screen_parameters": [str(t) for t, _a, _f in samples],
        "coverage": "bounded_partial" if stopped else "bounded_complete",
        "independence_certified": False,
    }


def _samples(ainvs, F, factors, bound, count):
    from sage.all import EllipticCurve, QQ

    preferred = []
    for denominator in range(1, bound + 1):
        for numerator in range(-bound, bound + 1):
            preferred.append(QQ(numerator) / denominator)
    samples = []
    seen = set()
    for t in preferred:
        if t in seen:
            continue
        seen.add(t)
        try:
            value = QQ(F(t))
            if value == 0 or not value.is_square() or any(f(t) == 0 for f in factors):
                continue
            aa = [QQ(a(t)) for a in ainvs]
            if EllipticCurve(QQ, aa).discriminant() == 0:
                continue
            samples.append((t, aa, value))
            if len(samples) >= count:
                return samples
        except (ArithmeticError, TypeError, ValueError, ZeroDivisionError):
            continue
    raise ValueError(f"found only {len(samples)} nonsingular rational base fibers within height {bound}")


def _relationship(point, known, curve):
    if point.is_zero():
        return "identity"
    for order in range(1, 13):
        if order*point == curve(0):
            return f"torsion_order_divides_{order}"
    for label, old in known:
        for sign, difference in (("plus", point+old), ("minus", point-old)):
            for order in range(1, 13):
                if order*difference == curve(0):
                    return f"{sign}_{label}_torsion_order_divides_{order}"
    return "unresolved"
