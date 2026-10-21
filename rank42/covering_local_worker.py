#!/usr/bin/env python3
"""Isolated full local-solubility worker for integral binary quartics."""
from __future__ import annotations

import ctypes
import ctypes.util
import json
import math
import os
import re
import sys
from fractions import Fraction
from pathlib import Path


MARKER = "RANK42_COVERING_LOCAL="


def _q(value):
    return value if isinstance(value, Fraction) else Fraction(str(value))


def _integral_square_model(values):
    coefficients = [_q(value) for value in values]
    scale = 1
    for value in coefficients:
        scale = math.lcm(scale, value.denominator)
    integers = []
    for value in coefficients:
        integral = value * scale * scale
        if integral.denominator != 1:
            raise ValueError("failed to clear covering denominators by a square")
        integers.append(int(integral))
    while len(integers) > 1 and integers[-1] == 0:
        integers.pop()
    return scale, integers


def _library_path():
    names = []
    prefix = Path(sys.prefix)
    names.extend((prefix / "lib" / "libpari.so", prefix / "lib" / "libpari.dylib"))
    conda = os.environ.get("CONDA_PREFIX")
    if conda:
        names.extend((Path(conda) / "lib" / "libpari.so", Path(conda) / "lib" / "libpari.dylib"))
    found = ctypes.util.find_library("pari")
    if found:
        names.append(Path(found))
    for name in names:
        if str(name) == found or name.exists():
            return str(name)
    raise RuntimeError("libpari was not found")


class PariLocal:
    def __init__(self):
        self.path = _library_path()
        self.lib = ctypes.CDLL(self.path)
        self.lib.pari_init.argtypes = [ctypes.c_size_t, ctypes.c_ulong]
        self.lib.gp_read_str.argtypes = [ctypes.c_char_p]
        self.lib.gp_read_str.restype = ctypes.c_void_p
        self.lib.GENtostr.argtypes = [ctypes.c_void_p]
        self.lib.GENtostr.restype = ctypes.c_char_p
        self.lib.pari_init(128_000_000, 500_000)
        escaped = self.path.replace("\\", "\\\\").replace('"', '\\"')
        self.eval(
            'install("hyperell_locally_soluble","lGG","rank42_hls",'
            f'"{escaped}")'
        )

    def eval(self, expression):
        result = self.lib.gp_read_str(str(expression).encode())
        if not result:
            raise RuntimeError(f"PARI failed to evaluate {expression!r}")
        return self.lib.GENtostr(result).decode()

    def close(self):
        self.lib.pari_close()


def _polynomial(coefficients):
    terms = []
    for exponent, coefficient in enumerate(coefficients):
        if coefficient:
            terms.append(f"({coefficient})*x^{exponent}")
    return "+".join(terms) or "0"


def _is_square_integer(value):
    if value < 0:
        return False
    root = math.isqrt(value)
    return root * root == value


def analyze(coefficients):
    scale, integral = _integral_square_model(coefficients)
    degree = len(integral) - 1
    if degree not in {3, 4}:
        return {
            "status": "unsupported",
            "reason": "full_local_solver_supports_degree_3_or_4",
            "degree": degree,
        }

    pari = PariLocal()
    try:
        pari.eval(f"F={_polynomial(integral)}")
        discriminant = int(pari.eval("poldisc(F)"))
        if discriminant == 0:
            return {
                "status": "unsupported",
                "reason": "singular_covering_model",
                "degree": degree,
                "discriminant": "0",
            }

        real_roots = int(pari.eval("polsturm(F)"))
        real_soluble = bool(
            degree % 2
            or integral[-1] > 0
            or real_roots > 0
        )
        checks = [{
            "place": "infinity",
            "locally_soluble": real_soluble,
            "method": "exact_real_sign_and_sturm",
        }]
        if not real_soluble:
            return {
                "status": "locally_insoluble",
                "obstruction": "infinity",
                "degree": degree,
                "scale": str(scale),
                "integral_coefficients": [str(x) for x in integral],
                "discriminant": str(discriminant),
                "relevant_primes": [],
                "checks": checks,
                "all_places_checked": True,
            }

        # Odd degree, or a square leading coefficient, gives a rational point
        # at infinity and therefore points over every completion.
        rational_infinity = bool(
            degree == 3 or _is_square_integer(integral[-1])
        )
        if rational_infinity:
            return {
                "status": "everywhere_locally_soluble",
                "degree": degree,
                "scale": str(scale),
                "integral_coefficients": [str(x) for x in integral],
                "discriminant": str(discriminant),
                "relevant_primes": [],
                "checks": checks,
                "all_places_checked": True,
                "rational_point_at_infinity": True,
            }

        factor_text = pari.eval("Vec(factor(abs(2*poldisc(F)))[,1])")
        relevant = sorted({int(x) for x in re.findall(r"\d+", factor_text)})
        obstruction = None
        for prime in relevant:
            soluble = bool(int(pari.eval(f"rank42_hls(F,{prime})")))
            checks.append({
                "place": str(prime),
                "p": prime,
                "locally_soluble": soluble,
                "method": "PARI_hyperell_locally_soluble",
            })
            if not soluble:
                obstruction = prime
                break
        return {
            "status": (
                "locally_insoluble"
                if obstruction is not None
                else "everywhere_locally_soluble"
            ),
            "obstruction": obstruction,
            "degree": degree,
            "scale": str(scale),
            "integral_coefficients": [str(x) for x in integral],
            "discriminant": str(discriminant),
            "relevant_primes": relevant,
            "checks": checks,
            "all_places_checked": True,
            "rational_point_at_infinity": False,
        }
    finally:
        pari.close()


def main():
    payload = json.loads(sys.stdin.read())
    try:
        result = analyze(payload["coefficients"])
    except BaseException as exc:
        result = {"status": "error", "error": repr(exc)}
    print(MARKER + json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
