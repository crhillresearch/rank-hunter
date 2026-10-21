"""Bounded exact section and base-change experiments over Q(t).

This is a standalone Sage worker. It does not infer Mordell-Weil independence
from an equation identity or from the absence of a point in a supplied list.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import sys


SCHEMA = "rank42.geometry_research.v1"


def _identity(ainvs, x, y):
    a1, a2, a3, a4, a6 = ainvs
    return y * y + a1 * x * y + a3 * y == x**3 + a2 * x * x + a4 * x + a6


def _parse_family(family, ring, variable):
    from sage.all import sage_eval

    name = str(family.get("parameter") or "t")
    if not name.isidentifier():
        raise ValueError("family parameter must be an identifier")

    def parse(expr):
        return ring(sage_eval(str(expr), locals={name: variable}))

    raw = family.get("a_invariants")
    if not isinstance(raw, list) or len(raw) != 5:
        raise ValueError("family needs five a-invariants")
    ainvs = [parse(expr) for expr in raw]
    a1, a2, a3, a4, a6 = ainvs
    b2 = a1*a1 + 4*a2
    b4 = a1*a3 + 2*a4
    b6 = a3*a3 + 4*a6
    b8 = a1*a1*a6 + 4*a2*a6 - a1*a3*a4 + a2*a3*a3 - a4*a4
    discriminant = -(b2*b2*b8 + 8*b4*b4*b4 + 27*b6*b6 - 9*b2*b4*b6)
    if discriminant == 0:
        raise ValueError("the generic Weierstrass equation is singular")
    sections = []
    invalid = []
    for index, rec in enumerate(family.get("sections") or [], 1):
        try:
            x_expr, y_expr = (rec["x"], rec["y"]) if isinstance(rec, dict) else rec
            x, y = parse(x_expr), parse(y_expr)
            if not _identity(ainvs, x, y):
                raise ValueError("point identity fails")
            sections.append({"label": str(rec.get("label") or f"S{index}") if isinstance(rec, dict) else f"S{index}",
                             "x": str(x), "y": str(y), "verification": "exact_identity"})
        except Exception as exc:
            invalid.append({"index": index, "reason": str(exc)[:240]})
    return ainvs, sections, invalid


def _square_root_fraction(value, ring):
    """Return an exact square root in QQ(t), when one exists."""
    num, den = value.numerator(), value.denominator()
    product = num * den
    if not product.is_square():
        return None
    root = ring(product.sqrt()) / den
    return root if root * root == value else None


def _section_key(x, y):
    return (str(x), str(y))


def _manual_candidate(config, ainvs, ring, variable, parameter):
    from sage.all import sage_eval

    x_expr = str(config.get("candidate_x") or "").strip()
    y_expr = str(config.get("candidate_y") or "").strip()
    if not x_expr and not y_expr:
        return None
    if not x_expr or not y_expr:
        raise ValueError("candidate section requires both x and y")
    x = ring(sage_eval(x_expr, locals={parameter: variable}))
    y = ring(sage_eval(y_expr, locals={parameter: variable}))
    if not _identity(ainvs, x, y):
        raise ValueError("candidate section does not satisfy the generic Weierstrass equation")
    return {"x": str(x), "y": str(y), "verification": "exact_identity",
            "independence": "not_certified"}


def _search_sections(ainvs, variable, ring, known, config):
    from sage.all import ZZ, sage_eval

    degree = max(0, min(int(config.get("degree", 1)), 3))
    coefficient_bound = max(1, min(int(config.get("coefficient_bound", 2)), 6))
    max_trials = max(1, min(int(config.get("max_trials", 2000)), 20000))
    max_results = max(1, min(int(config.get("max_results", 24)), 64))
    rational_x = bool(config.get("rational_x", True))
    a1, a2, a3, a4, a6 = ainvs
    known_keys = set()
    for rec in known:
        x = ring(sage_eval(rec["x"], locals={str(variable): variable}))
        y = ring(sage_eval(rec["y"], locals={str(variable): variable}))
        known_keys.add(_section_key(x, y))
        known_keys.add(_section_key(x, -y-a1*x-a3))
    found = []
    seen = set(known_keys)
    trials = 0
    stopped = False
    range_values = range(-coefficient_bound, coefficient_bound + 1)
    for denominator in ([ring(1)] + ([variable + c for c in range_values] if rational_x else [])):
        for d in range(degree + 1):
            for coeffs in itertools.product(range_values, repeat=d + 1):
                if d and coeffs[-1] == 0:
                    continue
                numerator = sum((ZZ(c) * variable**i for i, c in enumerate(coeffs)), ring(0))
                x = numerator / denominator
                if denominator != 1 and x.denominator() == 1:
                    continue
                trials += 1
                delta = (a1*x+a3)**2 + 4*(x**3+a2*x*x+a4*x+a6)
                root = _square_root_fraction(delta, ring)
                if root is not None:
                    for sign in (1, -1):
                        y = (-a1*x-a3+sign*root)/2
                        key = _section_key(x, y)
                        if key in seen or not _identity(ainvs, x, y):
                            continue
                        seen.add(key)
                        seen.add(_section_key(x, -y-a1*x-a3))
                        found.append({"x": str(x), "y": str(y),
                                      "verification": "exact_identity",
                                      "independence": "not_certified"})
                        if len(found) >= max_results:
                            stopped = True
                            break
                if stopped or trials >= max_trials:
                    stopped = True
                    break
            if stopped:
                break
        if stopped:
            break
    return {"sections": found, "x_candidates_checked": trials,
            "coverage": "bounded_partial" if stopped else "bounded_complete",
            "degree_limit": degree, "coefficient_bound": coefficient_bound,
            "rational_x": rational_x}


def main():
    from sage.all import QQ, PolynomialRing, sage_eval

    request = json.load(sys.stdin)
    if request.get("schema") != SCHEMA:
        raise ValueError("unsupported geometry research request")
    mode = str(request.get("mode") or "")
    family = request.get("family")
    if not isinstance(family, dict):
        raise ValueError("family must be an object")
    source_hash = hashlib.sha256(json.dumps(family, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    config = dict(request.get("config") or {})

    if mode == "sections":
        parameter = str(family.get("parameter") or "t")
        polynomial = PolynomialRing(QQ, parameter)
        field = polynomial.fraction_field()
        variable = field.gen()
        ainvs, known, invalid = _parse_family(family, field, variable)
        manual = _manual_candidate(config, ainvs, field, variable, parameter)
        search = _search_sections(ainvs, variable, field, known, config)
        result = {"parameter": parameter, "a_invariants": [str(a) for a in ainvs],
                  "known_sections_verified": known, "known_sections_invalid": invalid,
                  "manual_candidate": manual,
                  **search}
    elif mode == "base_change":
        polynomial = PolynomialRing(QQ, "u")
        field = polynomial.fraction_field()
        u = field.gen()
        parameter_map = field(sage_eval(str(config.get("parameter_map") or "u^2"), locals={"u": u}))
        map_degree = max(parameter_map.numerator().degree(), parameter_map.denominator().degree())
        if map_degree < 2 or map_degree > 8:
            raise ValueError("base-change map must have degree from 2 to 8")
        ainvs, inherited, invalid = _parse_family(family, field, parameter_map)
        manual = _manual_candidate(config, ainvs, field, u, "u")
        search = _search_sections(ainvs, u, field, inherited, config) if config.get("search_new", True) else None
        result = {"parameter": "u", "parameter_map": str(parameter_map),
                  "map_degree": int(map_degree),
                  "a_invariants": [str(a) for a in ainvs],
                  "inherited_sections_verified": inherited,
                  "inherited_sections_invalid": invalid,
                  "manual_candidate": manual,
                  "new_section_search": search}
    else:
        raise ValueError("unsupported geometry research mode")

    json.dump({"schema": SCHEMA, "mode": mode, "family_name": str(family.get("name") or "Unnamed family"),
               "source_sha256": source_hash, "rank_claim": False,
               "independence_certified": False, "result": result},
              sys.stdout, sort_keys=True, separators=(",", ":"))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
