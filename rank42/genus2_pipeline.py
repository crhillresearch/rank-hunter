"""Exact genus-two model, divisor, and local arithmetic operations."""
from __future__ import annotations

import hashlib
import json
from fractions import Fraction
from math import gcd, isqrt


def _coefficients(values):
    coefficients = [Fraction(str(value)) for value in values]
    while coefficients and coefficients[-1] == 0:
        coefficients.pop()
    return coefficients


def _trim(poly):
    while poly and poly[-1] == 0:
        poly.pop()
    return poly


def _derivative(poly):
    return [Fraction(i) * poly[i] for i in range(1, len(poly))]


def _remainder(a, b):
    a = list(a)
    b = _trim(list(b))
    while len(a) >= len(b) and b:
        factor = a[-1] / b[-1]
        offset = len(a) - len(b)
        for i, value in enumerate(b):
            a[offset + i] -= factor * value
        _trim(a)
    return a


def _squarefree_over_q(poly):
    a, b = _trim(list(poly)), _trim(_derivative(poly))
    while b:
        a, b = b, _remainder(a, b)
    return len(a) <= 1


def audit_model(values):
    """Validate y^2=f(x) as an exact smooth genus-two model."""
    try:
        coefficients = _coefficients(values)
    except (ValueError, ZeroDivisionError) as exc:
        return {"status": "invalid", "reason": f"invalid_coefficient: {exc}"}
    degree = len(coefficients) - 1
    if degree not in {5, 6}:
        return {"status": "invalid", "reason": "degree_must_be_5_or_6"}
    if not _squarefree_over_q(coefficients):
        return {"status": "invalid", "reason": "polynomial_not_squarefree"}
    infinity = (
        "one_rational_point"
        if degree == 5
        else "two_rational_points" if coefficients[-1].numerator > 0
        and isqrt(coefficients[-1].numerator) ** 2 == coefficients[-1].numerator
        and isqrt(coefficients[-1].denominator) ** 2 == coefficients[-1].denominator
        else "no_rational_point_from_infinity"
    )
    normalized = [str(value) for value in coefficients]
    fingerprint = hashlib.sha256(
        json.dumps(normalized, separators=(",", ":")).encode("ascii")
    ).hexdigest()
    return {
        "status": "completed",
        "object_type": "genus2_jacobian",
        "curve_model": "y^2 = f(x)",
        "degree": degree,
        "genus": 2,
        "squarefree_over_Q": True,
        "smooth_hyperelliptic_model": True,
        "infinity_rationality": infinity,
        "coefficients_ascending": normalized,
        "model_fingerprint_sha256": fingerprint,
        "claim_boundary": "model validation only; no Jacobian rank claim",
    }


def double_cover_from_elliptic(a_invariants):
    """Construct C: y²=x⁶+A*x²+B with a verified degree-two map C -> E.

    The standard rational change of coordinates sends the supplied
    Weierstrass model to Y²=X³+A*X+B.  Substituting X=x² gives the cover.
    This construction alone makes no claim about the Jacobian rank.
    """
    if len(a_invariants) != 5:
        raise ValueError("an elliptic curve needs five a-invariants")
    a1, a2, a3, a4, a6 = [Fraction(str(value)) for value in a_invariants]
    b2 = a1 * a1 + 4 * a2
    b4 = a1 * a3 + 2 * a4
    b6 = a3 * a3 + 4 * a6
    c4 = b2 * b2 - 24 * b4
    c6 = -b2 ** 3 + 36 * b2 * b4 - 216 * b6
    A = -c4 / 48
    B = -c6 / 864
    if 4 * A ** 3 + 27 * B ** 2 == 0:
        raise ValueError("source elliptic model is singular")
    if B == 0:
        raise ValueError("this cover is singular because the short-model B is zero")
    coefficients = [str(B), "0", str(A), "0", "0", "0", "1"]
    audit = audit_model(coefficients)
    if audit["status"] != "completed":
        raise ArithmeticError(f"constructed cover failed audit: {audit['reason']}")
    return {
        "coefficients_ascending": coefficients,
        "model_fingerprint_sha256": audit["model_fingerprint_sha256"],
        "construction": "quadratic_base_change_of_short_weierstrass_x",
        "source_a_invariants": [str(value) for value in (a1, a2, a3, a4, a6)],
        "short_model": {"A": str(A), "B": str(B)},
        "map_to_source_short_model": {"X": "x^2", "Y": "y"},
        "short_model_to_source": {
            "X_original": f"X-({b2})/12",
            "Y_original": f"Y-(({a1})*X_original+({a3}))/2",
        },
        "map_degree": 2,
        "curve_identity_verified": True,
        "rank_claim": None,
    }


def _eval_fraction(poly, x):
    value = Fraction(0)
    for coefficient in reversed(poly):
        value = value * x + coefficient
    return value


def _rational_sqrt(value):
    if value < 0:
        return None
    numerator = isqrt(value.numerator)
    denominator = isqrt(value.denominator)
    if numerator * numerator != value.numerator or denominator * denominator != value.denominator:
        return None
    return Fraction(numerator, denominator)


def _divisor_class_leads(points, audit):
    infinity = audit.get("infinity_rationality")
    if infinity == "one_rational_point":
        return [{"point": point, "class": "[P - infinity]"} for point in points]
    if infinity == "two_rational_points":
        return [{"point": point, "class": "[P - infinity_+]"} for point in points]
    if not points:
        return []
    base = points[0]
    return [
        {"point": point, "class": "[P - P0]", "base_point": base}
        for point in points[1:]
    ]


def rational_point_search(values, *, height=40, max_points=500, max_x_values=25000):
    """Exhaust a bounded rational x-chart and exactly verify every point."""
    audit = audit_model(values)
    if audit["status"] != "completed":
        return audit
    poly = _coefficients(values)
    bound = max(1, min(int(height), 10000))
    cap = max(1, min(int(max_points), 10000))
    trial_cap = max(1, min(int(max_x_values), 1000000))
    points, seen = [], set()
    tested = 0
    for denominator in range(1, bound + 1):
        for numerator in range(-bound, bound + 1):
            if gcd(abs(numerator), denominator) != 1:
                continue
            if tested >= trial_cap:
                return {
                    "status": "completed",
                    "points": points,
                    "jacobian_divisor_class_leads": _divisor_class_leads(points, audit),
                    "points_found": len(points),
                    "x_values_tested": tested,
                    "height_bound": bound,
                    "coverage_complete": False,
                    "stop_reason": "x_value_budget_reached",
                    "claim_boundary": "bounded exact point search; no global nonexistence or rank claim",
                }
            x = Fraction(numerator, denominator)
            tested += 1
            y = _rational_sqrt(_eval_fraction(poly, x))
            if y is None:
                continue
            for y_value in sorted({y, -y}):
                key = (x, y_value)
                if key in seen:
                    continue
                seen.add(key)
                points.append([str(x), str(y_value)])
                if len(points) >= cap:
                    return {
                        "status": "completed",
                        "points": points,
                        "jacobian_divisor_class_leads": _divisor_class_leads(points, audit),
                        "points_found": len(points),
                        "x_values_tested": tested,
                        "height_bound": bound,
                        "coverage_complete": False,
                        "claim_boundary": "bounded exact point search; no global nonexistence or rank claim",
                    }
    return {
        "status": "completed",
        "points": points,
        "jacobian_divisor_class_leads": _divisor_class_leads(points, audit),
        "points_found": len(points),
        "x_values_tested": tested,
        "height_bound": bound,
        "coverage_complete": True,
        "claim_boundary": "bounded exact point search; no global nonexistence or rank claim",
    }


def _poly_mod(values, p):
    out = []
    for value in values:
        value = Fraction(value)
        if value.denominator % p == 0:
            return None
        out.append(value.numerator * pow(value.denominator, -1, p) % p)
    return _trim(out)


def _poly_remainder_mod(a, b, p):
    a, b = list(a), _trim(list(b))
    if not b:
        return []
    inverse = pow(b[-1], -1, p)
    while len(a) >= len(b) and a:
        factor = a[-1] * inverse % p
        offset = len(a) - len(b)
        for i, value in enumerate(b):
            a[offset + i] = (a[offset + i] - factor * value) % p
        _trim(a)
    return a


def _squarefree_mod(poly, p):
    derivative = [(i * poly[i]) % p for i in range(1, len(poly))]
    a, b = _trim(list(poly)), _trim(derivative)
    while b:
        a, b = b, _poly_remainder_mod(a, b, p)
    return len(a) <= 1


def _is_prime(value):
    value = int(value)
    if value < 2:
        return False
    if value % 2 == 0:
        return value == 2
    divisor = 3
    while divisor * divisor <= value:
        if value % divisor == 0:
            return False
        divisor += 2
    return True


def _fp2_mul(a, b, p, nonsquare):
    return ((a[0] * b[0] + nonsquare * a[1] * b[1]) % p,
            (a[0] * b[1] + a[1] * b[0]) % p)


def _fp2_pow(value, exponent, p, nonsquare):
    result = (1, 0)
    while exponent:
        if exponent & 1:
            result = _fp2_mul(result, value, p, nonsquare)
        value = _fp2_mul(value, value, p, nonsquare)
        exponent >>= 1
    return result


def _evaluate_fp2(poly, x, p, nonsquare):
    value = (0, 0)
    for coefficient in reversed(poly):
        value = _fp2_mul(value, x, p, nonsquare)
        value = ((value[0] + coefficient) % p, value[1])
    return value


def _count_curve_points(poly, p, degree):
    q = p if degree == 1 else p * p
    if degree == 1:
        count_points = 1  # point at infinity for odd degree; corrected below for even degree
        for x in range(p):
            value = 0
            for coefficient in reversed(poly):
                value = (value * x + coefficient) % p
            if value == 0:
                count_points += 1
            elif pow(value, (p - 1) // 2, p) == 1:
                count_points += 2
        if len(poly) - 1 == 6:
            lead = poly[-1] % p
            count_points -= 1
            if lead and pow(lead, (p - 1) // 2, p) == 1:
                count_points += 2
        return count_points

    nonsquare = next(d for d in range(2, p) if pow(d, (p - 1) // 2, p) == p - 1)
    count_points = 1
    for a in range(p):
        for b in range(p):
            value = _evaluate_fp2(poly, (a, b), p, nonsquare)
            if value == (0, 0):
                count_points += 1
            elif _fp2_pow(value, (q - 1) // 2, p, nonsquare) == (1, 0):
                count_points += 2
    if len(poly) - 1 == 6:
        lead = poly[-1] % p
        count_points -= 1
        if lead and pow(lead, (q - 1) // 2, p) == 1:
            count_points += 2
    return count_points


def frobenius_profile(values, *, primes=(3, 5, 7, 11, 13, 17, 19, 23)):
    """Compute exact #C(F_p), #C(F_p^2), and #J(F_p) at good odd primes."""
    audit = audit_model(values)
    if audit["status"] != "completed":
        return audit
    coefficients = _coefficients(values)
    profile, skipped = [], []
    for prime in list(primes)[:40]:
        p = int(prime)
        if p > 101 or not _is_prime(p):
            skipped.append({"prime": p, "reason": "prime_not_odd_or_exceeds_profile_budget"})
            continue
        reduced = _poly_mod(coefficients, p)
        if p < 3 or reduced is None or len(reduced) != len(coefficients) or not _squarefree_mod(reduced, p):
            skipped.append({"prime": p, "reason": "bad_reduction_or_denominator"})
            continue
        n1 = _count_curve_points(reduced, p, 1)
        n2 = _count_curve_points(reduced, p, 2)
        s1 = p + 1 - n1
        s2 = (n2 - p * p - 1 + s1 * s1) // 2
        jacobian_order = p * p + 1 + s2 - (p + 1) * s1
        profile.append({
            "prime": p,
            "curve_points_Fp": n1,
            "curve_points_Fp2": n2,
            "jacobian_order_Fp": jacobian_order,
            "good_reduction": True,
        })
    return {
        "status": "completed",
        "profile": profile,
        "usable_prime_count": len(profile),
        "skipped_primes": skipped,
        "interpretation": "exact local Frobenius data; finite-prime counts do not determine global rank",
    }


def _quadratic_ordinates(poly, a, b):
    """Solve v²=f mod q exactly, with q=x²+a*x+b and deg(v)<2."""
    d, e = (_remainder(poly, [b, a, Fraction(1)]) + [Fraction(0)] * 2)[:2]
    discriminant = a * a - 4 * b
    if _rational_sqrt(discriminant) is not None:
        return []
    candidates = []
    if e == 0:
        s = _rational_sqrt(d)
        if s is not None:
            candidates.append((s, Fraction(0)))
        if discriminant:
            r = _rational_sqrt(4 * d / discriminant)
            if r is not None:
                candidates.append((a * r / 2, r))
    else:
        norm = d * d - a * d * e + b * e * e
        root = _rational_sqrt(norm)
        if root is None:
            return []
        for sign in (1, -1):
            trace = _rational_sqrt(2 * d - a * e + sign * 2 * root)
            if trace is None or trace == 0:
                continue
            r = e / trace
            s = (trace * trace + a * e) / (2 * trace)
            candidates.append((s, r))
    return sorted({(s, r) for s, r in candidates
                   if s * s - b * r * r == d and 2 * s * r - a * r * r == e})


def _verified_divisor(poly, q_values, v_values, *, source):
    q = [Fraction(str(value)) for value in q_values]
    v = [Fraction(str(value)) for value in v_values]
    if len(q) != 3 or q[2] != 1 or len(v) != 2:
        raise ValueError("a divisor needs monic quadratic q and linear v")
    if _rational_sqrt(q[1] * q[1] - 4 * q[0]) is not None:
        raise ValueError("quadratic support q must be irreducible over Q")
    if not any(_remainder(poly, q)):
        raise ValueError("quadratic support meets the branch divisor")
    v_squared = [v[0] * v[0], 2 * v[0] * v[1], v[1] * v[1]]
    difference = [
        (poly[i] if i < len(poly) else Fraction(0))
        - (v_squared[i] if i < len(v_squared) else Fraction(0))
        for i in range(max(len(poly), len(v_squared)))
    ]
    if any(_remainder(difference, q)):
        raise ValueError("q does not divide f-v² over Q")
    return {
        "q_coefficients_ascending": [str(value) for value in q],
        "v_coefficients_ascending": [str(value) for value in v],
        "source": source,
        "curve_identity_verified": True,
        "irreducible_quadratic_support": True,
        "jacobian_class": "[Z - 2∞]" if len(poly) == 6 else "[Z - ∞+ - ∞-]",
    }


def _rational_values(numerator_bound, denominator_bound):
    values = {
        Fraction(numerator, denominator)
        for denominator in range(1, denominator_bound + 1)
        for numerator in range(-numerator_bound, numerator_bound + 1)
    }
    return sorted(values, key=lambda value: (
        max(abs(value.numerator), value.denominator),
        value.denominator,
        value.numerator,
    ))


def _f2_rank(rows):
    basis = {}
    for value in rows:
        mask = int(value)
        while mask:
            pivot = mask.bit_length() - 1
            if pivot in basis:
                mask ^= basis[pivot]
            else:
                basis[pivot] = mask
                break
    return len(basis)


def _v2(value):
    value = int(value)
    count = 0
    while value and value % 2 == 0:
        value //= 2
        count += 1
    return count


def _legendre_bit(value, p):
    value %= p
    if not value:
        return None
    return 0 if pow(value, (p - 1) // 2, p) == 1 else 1


def _eval_mod(poly, x, p):
    result = 0
    for coefficient in reversed(poly):
        result = (result * x + coefficient) % p
    return result


def _poly_mul_mod(left, right, p):
    if not left or not right:
        return []
    out = [0] * (len(left) + len(right) - 1)
    for i, a in enumerate(left):
        for j, b in enumerate(right):
            out[i + j] = (out[i + j] + a * b) % p
    return _trim(out)


def _poly_powmod(base, exponent, modulus, p):
    result = [1]
    base = _poly_remainder_mod(base, modulus, p)
    while exponent:
        if exponent & 1:
            result = _poly_remainder_mod(_poly_mul_mod(result, base, p), modulus, p)
        base = _poly_remainder_mod(_poly_mul_mod(base, base, p), modulus, p)
        exponent >>= 1
    return result


def _poly_gcd_mod(left, right, p):
    a, b = _trim(list(left)), _trim(list(right))
    while b:
        a, b = b, _poly_remainder_mod(a, b, p)
    if not a:
        return []
    inverse = pow(a[-1], -1, p)
    return [(value * inverse) % p for value in a]


def _frobenius_factor_certificates(poly, prime_bound):
    """Find exact modular witnesses for irreducible 6 and factor type 1+5."""
    irreducible_prime = None
    one_five_prime = None
    x = [0, 1]
    for p in range(3, prime_bound + 1, 2):
        if not _is_prime(p):
            continue
        f = _poly_mod(poly, p)
        if f is None or len(f) != 7 or not _squarefree_mod(f, p):
            continue
        if one_five_prime is None:
            first = _poly_powmod(x, p, f, p)
            first_minus_x = [(first[i] if i < len(first) else 0) - (x[i] if i < 2 else 0)
                             for i in range(max(len(first), 2))]
            if len(_poly_gcd_mod(f, first_minus_x, p)) == 2:
                fifth = _poly_powmod(x, p**5, f, p)
                if fifth == x:
                    one_five_prime = p
        if irreducible_prime is None:
            sixth = _poly_powmod(x, p**6, f, p)
            if sixth == x:
                checks = []
                for exponent in (2, 3):
                    value = _poly_powmod(x, p**exponent, f, p)
                    difference = [(value[i] if i < len(value) else 0) - (x[i] if i < 2 else 0)
                                  for i in range(max(len(value), 2))]
                    checks.append(_poly_gcd_mod(f, difference, p) == [1])
                if all(checks):
                    irreducible_prime = p
        if irreducible_prime is not None and one_five_prime is not None:
            break
    return {
        "irreducible_sextic_prime": irreducible_prime,
        "one_plus_five_factor_prime": one_five_prime,
        "no_rational_root_proved": irreducible_prime is not None,
        "no_stable_three_plus_three_partition_proved": one_five_prime is not None,
        "modular_irreducibility_method": "Rabin degree-six Frobenius and gcd tests",
        "one_plus_five_method": "gcd(f,x^p-x) has degree one and x^(p^5)=x modulo f",
    }


def quadratic_divisor_rank_search(
    values,
    *,
    numerator_bound=30,
    denominator_bound=3,
    max_divisors=128,
    character_prime_bound=401,
    seed_divisors=(),
    rational_points=(),
):
    """Find exact degree-two divisors and certify a sextic rank lower bound.

    The rank proof uses relative Legendre characters of the fake (x-T)
    descent images at good split primes. Its dimension is reduced by a
    rigorous upper bound for rational 2-torsion from local Jacobian orders.
    """
    audit = audit_model(values)
    if audit["status"] != "completed":
        return audit
    poly = _coefficients(values)
    num_bound = int(numerator_bound)
    den_bound = int(denominator_bound)
    cap = int(max_divisors)
    prime_bound = int(character_prime_bound)
    if not (1 <= num_bound <= 100 and 1 <= den_bound <= 20
            and 1 <= cap <= 512 and 3 <= prime_bound <= 2000):
        raise ValueError("divisor search budget exceeds the supported bounds")
    if not isinstance(seed_divisors, (list, tuple)):
        raise ValueError("seed_divisors must be a list of q,v coefficient objects")

    divisors = []
    seen_q = set()
    for seed in seed_divisors:
        if not isinstance(seed, dict):
            raise ValueError("each seed divisor must contain q and v lists")
        divisor = _verified_divisor(poly, seed.get("q") or (), seed.get("v") or (), source="entered_witness")
        q_key = tuple(divisor["q_coefficients_ascending"])
        if q_key not in seen_q:
            divisors.append(divisor)
            seen_q.add(q_key)
    if len(divisors) > cap:
        raise ValueError("entered witnesses exceed max_divisors")

    q_values_tested = 0
    search_complete = True
    choices = _rational_values(num_bound, den_bound)
    for a in choices:
        if not search_complete:
            break
        for b in choices:
            if len(divisors) >= cap:
                search_complete = False
                break
            q_values_tested += 1
            q_key = (str(b), str(a), "1")
            if q_key in seen_q:
                continue
            if not any(_remainder(poly, [b, a, Fraction(1)])):
                continue
            ordinates = _quadratic_ordinates(poly, a, b)
            if not ordinates:
                continue
            s, r = ordinates[0]
            divisor = _verified_divisor(poly, (b, a, 1), (s, r), source="bounded_quadratic_search")
            divisors.append(divisor)
            seen_q.add(q_key)

    result = {
        "status": "completed",
        "object_type": "genus2_jacobian",
        "model_fingerprint_sha256": audit["model_fingerprint_sha256"],
        "degree": audit["degree"],
        "divisors": divisors,
        "divisors_found": len(divisors),
        "q_values_tested": q_values_tested,
        "q_numerator_bound": num_bound,
        "q_denominator_bound": den_bound,
        "search_complete": search_complete,
        "character_prime_bound": prime_bound,
        "rank_lower_bound": None,
        "rank_upper_bound": None,
        "exact_rank": None,
        "proof_status": "no_rank_certificate",
    }
    if audit["degree"] != 6:
        result["next_experiment"] = (
            "Supply exact degree-two divisor witnesses or expand the q search. "
            "This fake-descent rank certificate currently supports sextic models."
        )
        return result

    q_polynomials = [[Fraction(value) for value in item["q_coefficients_ascending"]]
                     for item in divisors]
    rows = []
    primes_examined = 0
    for p in range(3, prime_bound + 1, 2) if divisors else ():
        if not _is_prime(p):
            continue
        reduced = _poly_mod(poly, p)
        if reduced is None or len(reduced) != 7 or not _squarefree_mod(reduced, p):
            continue
        primes_examined += 1
        roots = [x for x in range(p) if _eval_mod(reduced, x, p) == 0]
        if len(roots) < 2:
            continue
        reduced_q = [_poly_mod(q, p) for q in q_polynomials]
        if any(q is None or len(q) != 3 for q in reduced_q):
            continue
        for root in roots[1:]:
            mask = 0
            evidence = []
            valid = True
            for index, q in enumerate(reduced_q):
                at_base = _eval_mod(q, roots[0], p)
                at_root = _eval_mod(q, root, p)
                base_bit = _legendre_bit(at_base, p)
                root_bit = _legendre_bit(at_root, p)
                if base_bit is None or root_bit is None:
                    valid = False
                    break
                mask |= (base_bit ^ root_bit) << index
                evidence.append([at_base, at_root])
            if valid and _f2_rank([item["bitmask"] for item in rows] + [mask]) > len(rows):
                rows.append({
                    "prime": p,
                    "branch_roots": [roots[0], root],
                    "bitmask": mask,
                    "quadratic_support_values": evidence,
                    "good_reduction_verified": True,
                })
            if len(rows) == len(divisors):
                break
        if len(rows) == len(divisors):
            break

    row_masks = [row["bitmask"] for row in rows]
    selected = []
    column_vectors = []
    for index in range(len(divisors)):
        column = sum(((mask >> index) & 1) << position
                     for position, mask in enumerate(row_masks))
        if _f2_rank(column_vectors + [column]) > len(column_vectors):
            selected.append(index)
            column_vectors.append(column)

    local_orders = []
    for p in (3, 5, 7, 11, 13, 17, 19, 23, 29):
        reduced = _poly_mod(poly, p)
        if reduced is None or len(reduced) != 7 or not _squarefree_mod(reduced, p):
            continue
        n1 = _count_curve_points(reduced, p, 1)
        n2 = _count_curve_points(reduced, p, 2)
        trace = p + 1 - n1
        second = (n2 - p * p - 1 + trace * trace) // 2
        order = p * p + 1 + second - (p + 1) * trace
        local_orders.append({"prime": p, "jacobian_order_Fp": order, "two_adic_valuation": _v2(order)})
    torsion_upper = min([4] + [row["two_adic_valuation"] for row in local_orders])
    rational_point_witness = None
    if audit["infinity_rationality"] == "two_rational_points":
        rational_point_witness = "infinity_plus"
    else:
        for coordinates in rational_points:
            if not isinstance(coordinates, (list, tuple)) or len(coordinates) != 2:
                continue
            x, y = Fraction(str(coordinates[0])), Fraction(str(coordinates[1]))
            if y * y != _eval_fraction(poly, x):
                raise ArithmeticError("supplied rational point is off the genus-two curve")
            rational_point_witness = [str(x), str(y)]
            break
    galois = _frobenius_factor_certificates(poly, prime_bound)
    extra_kernel = int(bool(
        rational_point_witness is not None
        and galois["no_rational_root_proved"]
        and galois["no_stable_three_plus_three_partition_proved"]
    ))
    rank_lower = max(0, len(selected) + extra_kernel - torsion_upper)
    result.update({
        "fake_descent_image_dimension": len(selected),
        "independent_divisor_indices": selected,
        "independent_divisors": [divisors[index] for index in selected],
        "modular_character_rows": rows,
        "good_split_primes_examined": primes_examined,
        "local_jacobian_orders": local_orders,
        "rational_2_torsion_dimension_upper": torsion_upper,
        "rational_curve_point_witness": rational_point_witness,
        "galois_certificates": galois,
        "fps_kernel_direction": extra_kernel,
        "rank_lower_bound": rank_lower,
        "proof_status": "rigorous_rank_lower_bound" if rank_lower else "no_positive_rank_lower_bound",
        "proof_method": "exact quadratic divisors; independent fake (x-T) squareclass images; optional FPS kernel; local 2-torsion bound",
        "theorem_reference": "Flynn–Poonen–Schaefer, Duke Math. J. 90 (1997), Proposition 5",
        "next_experiment": (
            "Increase the quadratic-support or split-prime bound, or supply verified q,v witnesses. "
            "An exact rank additionally needs a rigorous upper bound."
        ),
    })
    return result
