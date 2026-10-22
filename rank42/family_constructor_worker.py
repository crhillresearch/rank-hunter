"""Exact Sage worker for constant-x conic family construction.

Given a prescribed-torsion family E_t and rational x=c, the point equation
is quadratic in y. Its discriminant defines a double cover z^2=D(t). When the
square-free part has degree at most two and a rational point, this worker
parametrizes that cover and pulls the resulting section back to Q(u).
"""
from __future__ import annotations

import json
import sys


SCHEMA = "rank42.family_constructor.v1"


def _rational_points_for_quadratic(poly, QQ, numerator_bound=30, denominator_bound=12):
    for denominator in range(1, int(denominator_bound) + 1):
        for numerator in range(-int(numerator_bound), int(numerator_bound) + 1):
            t0 = QQ(numerator) / denominator
            value = QQ(poly(t0))
            if value >= 0 and value.is_square():
                z0 = value.sqrt()
                yield t0, z0
                if z0:
                    yield t0, -z0


def _construct(family, x_value, options):
    from sage.all import QQ, PolynomialRing, EllipticCurve, sage_eval

    parameter = str(family.get("parameter") or "t")
    if not parameter.isidentifier():
        raise ValueError("family parameter must be a valid identifier")
    Rt = PolynomialRing(QQ, parameter)
    t = Rt.gen()
    Kt = Rt.fraction_field()
    tv = Kt(t)
    x0 = QQ(x_value)

    def parse(expr):
        return Kt(sage_eval(str(expr), locals={parameter: tv}))

    raw = family.get("a_invariants")
    if not isinstance(raw, list) or len(raw) != 5:
        raise ValueError("source family must have five a-invariants")
    a1, a2, a3, a4, a6 = [parse(expr) for expr in raw]
    discriminant = (a1*x0 + a3)**2 + 4*(x0**3 + a2*x0**2 + a4*x0 + a6)
    numerator, denominator = discriminant.numerator(), discriminant.denominator()
    squarefree = numerator * denominator
    if squarefree == 0:
        raise ValueError("the chosen x-coordinate gives a repeated-root identity, not a conic")
    factors = squarefree.factor()
    square_part = Rt(1)
    residual = factors.unit()
    for factor, exponent in factors:
        square_part *= factor ** (int(exponent) // 2)
        if int(exponent) % 2:
            residual *= factor
    if residual.degree() not in (1, 2):
        raise ValueError(
            f"after removing polynomial squares, the cover has degree {residual.degree()}; "
            "this constructor currently handles degree 1 or 2"
        )

    numerator_bound = max(1, min(int(options.get("basepoint_numerator_bound", 30)), 100))
    denominator_bound = max(1, min(int(options.get("basepoint_denominator_bound", 12)), 40))
    basepoint = next(_rational_points_for_quadratic(
        residual, QQ, numerator_bound=numerator_bound,
        denominator_bound=denominator_bound,
    ), None)
    if basepoint is None:
        raise ValueError("no rational point found on the bounded search for the conic basepoint")
    t0, z0 = basepoint

    Rv = PolynomialRing(QQ, "u")
    u = Rv.gen()
    Kv = Rv.fraction_field()
    # Divide the secant intersection polynomial by its known root t=t0.
    RT = PolynomialRing(Kv, "T")
    T = RT.gen()
    h = RT([Kv(c) for c in residual.list()])
    line = Kv(z0) + Kv(u) * (T - Kv(t0))
    q, rem = (h - line**2).quo_rem(T - Kv(t0))
    if rem != 0 or q.degree() != 1 or q[1] == 0:
        raise ValueError("conic parametrization did not produce a second rational intersection")
    t_map = -q[0] / q[1]
    z_map = Kv(z0) + Kv(u) * (t_map - Kv(t0))

    def at_map(expr):
        return Kv(expr.numerator()(t_map) / expr.denominator()(t_map))

    mapped_ainvs = [at_map(value) for value in (a1, a2, a3, a4, a6)]
    mapped_sqrt = at_map(Kt(square_part) / denominator) * z_map
    mapped_y = (-mapped_ainvs[0]*x0 - mapped_ainvs[2] + mapped_sqrt) / 2
    E = EllipticCurve(Kv, mapped_ainvs)
    mapped_sections = []
    forced_index = None
    for index, record in enumerate(family.get("sections") or [], 1):
        if isinstance(record, dict):
            sx_expr, sy_expr = record["x"], record["y"]
            label = str(record.get("label") or f"Source section {index}")
        else:
            sx_expr, sy_expr = record
            label = f"Source section {index}"
        sx, sy = parse(sx_expr), parse(sy_expr)
        # Check the declared source section before pulling it back.
        source_curve = EllipticCurve(Kt, [a1, a2, a3, a4, a6])
        source_curve(sx, sy)
        msx, msy = at_map(sx), at_map(sy)
        E(msx, msy)
        mapped_sections.append({"label": label, "x": str(msx), "y": str(msy),
                                "construction": "inherited_by_base_change",
                                "verification": "exact_identity"})

    inherited_count = len(mapped_sections)
    section = {"label": "Conic-forced section", "x": str(x0), "y": str(mapped_y),
               "construction": "constant_x_conic", "verification": "exact_identity"}
    derived = {
        "name": f"{family.get('name') or 'Prescribed torsion'} conic construction",
        "parameter": "u",
        "a_invariants": [str(value) for value in mapped_ainvs],
        "sections": mapped_sections,
        "generic_rank": None,
    }
    # Keep the forced section as an additional generator unless it is exactly
    # an inherited section or its Weierstrass inverse.
    for index, inherited in enumerate(mapped_sections):
        ix = Kv(sage_eval(inherited["x"], locals={"u": u}))
        iy = Kv(sage_eval(inherited["y"], locals={"u": u}))
        if ix == x0 and (iy == mapped_y or iy == -mapped_y-mapped_ainvs[0]*x0-mapped_ainvs[2]):
            forced_index = index
            break
    if forced_index is None:
        forced_index = len(mapped_sections)
        mapped_sections.append(section)
    derived["sections"] = mapped_sections
    # Verify the cover identity and point equation independently in Q(u).
    mapped_residual = at_map(Kt(residual))
    mapped_den = at_map(Kt(denominator))
    cover_check = (z_map * square_part(t_map) / mapped_den)**2
    source_disc = (mapped_ainvs[0]*x0 + mapped_ainvs[2])**2 + 4*(
        x0**3 + mapped_ainvs[1]*x0**2 + mapped_ainvs[3]*x0 + mapped_ainvs[4]
    )
    if source_disc != cover_check:
        raise ValueError("exact conic pullback identity failed")
    ma1, ma2, ma3, ma4, ma6 = mapped_ainvs
    lhs = mapped_y**2 + ma1*x0*mapped_y + ma3*mapped_y
    rhs = x0**3 + ma2*x0**2 + ma4*x0 + ma6
    if lhs != rhs:
        raise ValueError("exact section identity failed")
    return derived, {
        "source_parameter": parameter,
        "source_x": str(x0),
        "cover_equation_squarefree": str(residual),
        "cover_basepoint": [str(t0), str(z0)],
        "parameter_map": str(t_map),
        "square_factor": str(square_part),
        "discriminant_denominator": str(denominator),
        "section": section,
        "section_index": forced_index,
        "inherited_sections": inherited_count,
        "exact_identity": True,
    }


def _specialization_evidence(derived, construction, torsion_label, depth="quick"):
    from sage.all import QQ, PolynomialRing, EllipticCurve, Infinity, sage_eval

    labels = {
        "Trivial": (), "C2": (2,), "C3": (3,), "C4": (4,), "C5": (5,),
        "C6": (6,), "C7": (7,), "C8": (8,), "C9": (9,), "C10": (10,),
        "C12": (12,), "C2 × C2": (2, 2), "C2 × C4": (2, 4),
        "C2 × C6": (2, 6), "C2 × C8": (2, 8),
    }
    expected = labels.get(str(torsion_label))
    if expected is None:
        raise ValueError("unsupported target torsion label")
    R = PolynomialRing(QQ, "u")
    u = R.gen()
    K = R.fraction_field()
    ainvs = [K(sage_eval(str(expr), locals={"u": K(u)})) for expr in derived["a_invariants"]]
    section_index = int(construction.get("section_index", 0))
    selected_section = derived["sections"][section_index]
    x_expr = K(sage_eval(str(selected_section["x"]), locals={"u": K(u)}))
    y_expr = K(sage_eval(str(selected_section["y"]), locals={"u": K(u)}))
    values = [QQ(0), QQ(1), QQ(-1), QQ(2), QQ(-2), QQ(1)/2, QQ(-1)/2,
              QQ(3), QQ(-3), QQ(1)/3, QQ(-1)/3, QQ(4), QQ(-4)]
    if str(depth).lower() == "expanded":
        for denominator in range(2, 7):
            for numerator in range(-8, 9):
                value = QQ(numerator) / denominator
                if value not in values:
                    values.append(value)
    checked = []
    for value in values:
        try:
            specialized = [QQ(expr(value)) for expr in ainvs]
            E = EllipticCurve(QQ, specialized)
            if E.discriminant() == 0:
                continue
            actual = tuple(int(n) for n in E.torsion_subgroup().invariants())
            if actual != expected:
                continue
            P = E(QQ(x_expr(value)), QQ(y_expr(value)))
            order = P.order()
            record = {"parameter": str(value), "torsion_invariants": list(actual),
                      "point_order": str(order)}
            checked.append(record)
            construction["validation_parameter"] = str(value)
            if order == Infinity:
                derived["generic_rank"] = 1
                derived["rank_lower_certificate"] = {
                    "specialization": str(value),
                    "section_index": section_index,
                    "method": "exact infinite-order specialization",
                }
                construction["generic_rank_lower_bound"] = 1
                construction["non_torsion_specialization"] = record
                return construction
        except (ArithmeticError, TypeError, ValueError, ZeroDivisionError):
            continue
    construction["generic_rank_lower_bound"] = None
    construction["torsion_match_specializations_checked"] = checked
    construction["rank_status"] = "section identity verified; non-torsion not established"
    # Keep the plugin validation point usable when only exact torsion was found.
    if checked:
        construction["validation_parameter"] = checked[0]["parameter"]
    else:
        raise ValueError(
            "constructed family has no tested specialization with the selected exact torsion; "
            "construction is valid but cannot be registered as a torsion-specific search family"
        )
    return construction


def main():
    request = json.load(sys.stdin)
    if request.get("schema") != SCHEMA:
        raise ValueError("unsupported constructor request")
    family = request.get("family")
    if not isinstance(family, dict):
        raise ValueError("source family must be an object")
    options = dict(request.get("options") or {})
    x_values = request.get("x_values") or [request.get("x_value", "1")]
    if not isinstance(x_values, list) or not x_values:
        raise ValueError("x_values must be a nonempty list")
    errors = []
    for x_value in x_values[:40]:
        try:
            result, proof = _construct(family, x_value, options)
            proof = _specialization_evidence(
                result, proof, request.get("torsion_label", ""),
                depth=options.get("specialization_depth", "quick"),
            )
            proof["x_values_attempted"] = [str(value) for value in x_values[:x_values.index(x_value) + 1]]
            proof["search_options"] = options
            break
        except Exception as exc:
            errors.append(f"x={x_value}: {str(exc)[:240]}")
    else:
        raise ValueError("No valid torsion-aware construction found. " + "; ".join(errors[-8:]))
    print(json.dumps({"schema": SCHEMA, "family": result, "construction": proof}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"schema": SCHEMA, "error": str(exc)}))
