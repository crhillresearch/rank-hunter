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
                             "x": str(x), "y": str(y), "verification": "exact_identity",
                             **({"source_point_id": int(rec["source_point_id"])}
                                if isinstance(rec, dict) and rec.get("source_point_id") is not None else {})})
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


def _seed_basis_certificate(anchor, ainvs, known, cover, parameter):
    """Check that three global sections specialize into one certified basis."""
    from sage.all import QQ, sage_eval

    evidence = anchor.get("evidence") or {}
    if (not evidence.get("rigorous") or evidence.get("status") != "completed"
            or int(evidence.get("rigorous_lower") or 0) < 3):
        return None
    if cover.get("cover_degree") != 1 or not cover.get("derived_family"):
        return None
    seed = cover.get("seed_fiber") or {}
    if not seed.get("source_fiber_retained") or not seed.get("derived_parameter"):
        return None
    t0 = QQ(anchor["parameter"])
    u0 = QQ(seed["derived_parameter"])
    if QQ(sage_eval(cover["parameter_map"], locals={"u": u0})) != t0:
        return None
    model = tuple(QQ(value) for value in evidence.get("model_a_invariants") or [])
    if model != tuple(QQ(a(t0)) for a in ainvs):
        return None
    derived = cover["derived_family"]
    if model != tuple(QQ(sage_eval(a, locals={"u": u0}))
                      for a in derived["a_invariants"]):
        return None
    basis = {tuple(QQ(v) for v in pair[:2]) for pair in evidence.get("points") or []
             if isinstance(pair, (list, tuple)) and len(pair) >= 2}
    if len(basis) < 3:
        return None
    selected = []
    for section in known:
        x = QQ(sage_eval(section["x"], locals={parameter: t0}))
        y = QQ(sage_eval(section["y"], locals={parameter: t0}))
        if (x, y) in basis:
            selected.append((x, y))
    new_point = tuple(QQ(value) for value in anchor["point"])
    new_section = derived["sections"][-1]
    if new_point != (QQ(sage_eval(new_section["x"], locals={"u": u0})),
                     QQ(sage_eval(new_section["y"], locals={"u": u0}))):
        return None
    if new_point not in basis or len(set(selected + [new_point])) < 3:
        return None
    return {"method": "three_sections_specialize_into_certified_seed_basis",
            "curve_id": int(anchor["curve_id"]), "parameter": str(t0),
            "derived_parameter": str(u0), "point_ids": [int(anchor["point_id"]),
                                                *[int(s.get("source_point_id")) for s in known
                                                  if s.get("source_point_id") is not None]],
            "rank_evidence_id": int(evidence["id"]),
            "source_rigorous_lower": int(evidence["rigorous_lower"]),
            "generic_rank_lower_bound": 3}


def main():
    from sage.all import QQ, Integer, PolynomialRing, sage_eval

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
    elif mode == "rank_jump_constructor":
        from rank_jump_constructor import discover

        parameter = str(family.get("parameter") or "t")
        polynomial = PolynomialRing(QQ, parameter)
        field = polynomial.fraction_field()
        variable = field.gen()
        ainvs, known, invalid = _parse_family(family, field, variable)
        search = discover(ainvs, variable, field, known, config)
        certified_source = bool(family.get("rank_lower_certificate")) and not invalid
        source_lower = int(family.get("generic_rank") or 0) if certified_source else None
        for cover in search["covers"]:
            seed_certificate = (_seed_basis_certificate(config["seed_anchor"], ainvs, known, cover, parameter)
                                if isinstance(config.get("seed_anchor"), dict) else None)
            if seed_certificate:
                cover["seed_fiber_rank_certificate"] = seed_certificate
            if cover.get("independence") == "certified_over_quadratic_cover" and cover.get("derived_family"):
                cover["derived_family"]["name"] = f"{family.get('name') or 'Research family'} · quadratic rank jump"
                if source_lower is not None:
                    cover["rank_lower_bound_growth_certified"] = True
                    cover["derived_family"]["generic_rank"] = source_lower + 1
                    cover["derived_family"]["rank_lower_certificate"] = {
                        "method": "source_certificate_plus_anti_invariant_non_torsion_section",
                        "source_certificate": family["rank_lower_certificate"],
                        "new_section_witness": cover["non_torsion_witness"],
                    }
                else:
                    cover["rank_lower_bound_growth_certified"] = False
                    cover["derived_family"]["generic_rank"] = 1
                    cover["derived_family"]["rank_lower_certificate"] = {
                        "method": "anti_invariant_non_torsion_section_only",
                        "new_section_witness": cover["non_torsion_witness"],
                    }
            if seed_certificate and cover.get("derived_family"):
                cover["derived_family"]["name"] = f"{family.get('name') or 'Research family'} · seed-anchored rank jump"
                cover["derived_family"]["generic_rank"] = max(
                    int(cover["derived_family"].get("generic_rank") or 0), 3)
                cover["derived_family"]["rank_lower_certificate"] = seed_certificate
        result = {"parameter": parameter, "a_invariants": [str(a) for a in ainvs],
                  "known_sections_verified": known, "known_sections_invalid": invalid,
                  "source_rank_lower_certified": source_lower, **search}
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

    certified_covers = ([row for row in result.get("covers", [])
                         if row.get("independence") == "certified_over_quadratic_cover"]
                        if mode == "rank_jump_constructor" else [])
    anchored_covers = ([row for row in result.get("covers", [])
                        if row.get("seed_fiber_rank_certificate")]
                       if mode == "rank_jump_constructor" else [])
    def json_scalar(value):
        if isinstance(value, Integer):
            return int(value)
        raise TypeError(f"unsupported geometry result value {type(value).__name__}")

    json.dump({"schema": SCHEMA, "mode": mode, "family_name": str(family.get("name") or "Unnamed family"),
               "source_sha256": source_hash,
               "rank_claim": bool(anchored_covers or
                                  (certified_covers and result.get("source_rank_lower_certified") is not None)),
               "independence_certified": bool(certified_covers or anchored_covers), "result": result},
              sys.stdout, sort_keys=True, separators=(",", ":"), default=json_scalar)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
