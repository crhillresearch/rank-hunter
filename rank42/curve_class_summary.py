"""Read-only exact Q-isomorphism counts without Sage or integer factorization.

In characteristic zero, short Weierstrass models are Q-isomorphic iff their
(c4,c6) differ by (u^4,u^6) for u in Q*. A shared j only narrows comparisons;
exact rational power checks retain non-isomorphic quadratic/sextic/quartic twists.
This summary does not merge records or change scientific identity persistence.
"""
from collections import defaultdict
from fractions import Fraction
from functools import lru_cache
import json


@lru_cache(maxsize=16384)
def _invariants(raw):
    try:
        model = json.loads(raw)
        if not isinstance(model, list) or len(model) != 5:
            return None
        a1, a2, a3, a4, a6 = (Fraction(str(value)) for value in model)
        b2 = a1*a1 + 4*a2
        b4 = a1*a3 + 2*a4
        b6 = a3*a3 + 4*a6
        c4 = b2*b2 - 24*b4
        c6 = -b2*b2*b2 + 36*b2*b4 - 216*b6
        if c4**3 == c6**2:
            return None
        return c4, c6
    except (ValueError, TypeError, ZeroDivisionError):
        return None


def _integer_power(value, exponent):
    if value < 2:
        return True
    root = 1 << ((value.bit_length() + exponent - 1) // exponent)
    while True:
        next_root = ((exponent - 1)*root + value // root**(exponent - 1)) // exponent
        if next_root >= root:
            return root**exponent == value
        root = next_root


def _rational_power(value, exponent):
    return value > 0 and _integer_power(value.numerator, exponent) and _integer_power(value.denominator, exponent)


def _same_class(left, right):
    c4, c6 = left
    d4, d6 = right
    if not c4:
        return _rational_power(d6/c6, 6)
    if not c6:
        return _rational_power(d4/c4, 4)
    u_squared = (d6/c6)/(d4/c4)
    return (u_squared**2 == d4/c4 and u_squared**3 == d6/c6
            and _rational_power(u_squared, 2))


def distinct_curve_classes(states):
    buckets = defaultdict(list)
    seen = set()
    modeled_count = 0
    excluded_count = 0
    for state in states:
        raw = state['curve'].get('a_invariants_json')
        pair = _invariants(raw) if isinstance(raw, str) else None
        if pair is None:
            excluded_count += 1
            continue
        modeled_count += 1
        if pair in seen:
            continue
        seen.add(pair)
        c4, c6 = pair
        j = 1728*c4**3/(c4**3-c6**2)
        representatives = buckets[j]
        if not any(_same_class(pair, representative) for representative in representatives):
            representatives.append(pair)
    count = sum(len(representatives) for representatives in buckets.values())
    return {'count': count, 'modeled_count': modeled_count,
            'duplicate_rows': modeled_count-count, 'excluded_count': excluded_count}
