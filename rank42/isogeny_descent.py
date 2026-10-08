"""Process-isolated rational 2-isogeny descent."""
from __future__ import annotations

import json
import subprocess
import sys
import time

RESULT_MARKER = "RANK42_ISOGENY_DESCENT_RESULT="


class IsogenyDescentTimeout(RuntimeError):
    def __init__(self, message, *, runtime=None):
        super().__init__(message)
        self.runtime = runtime


class IsogenyDescentFailure(RuntimeError):
    def __init__(self, message, *, runtime=None, returncode=None, tail=None):
        super().__init__(message)
        self.runtime = runtime
        self.returncode = returncode
        self.tail = tail


def validate_isogeny_descent_result(result, *, E=None):
    status = str(result.get("status") or "")
    if status == "unsupported":
        return result
    if status != "completed":
        raise ValueError("isogeny-descent worker did not complete")
    if str(result.get("engine") or "") != "eclib.mwrank_2_isogeny_descent":
        raise ValueError("wrong isogeny-descent engine attestation")
    if int(result.get("degree") or 0) != 2:
        raise ValueError("isogeny-descent result is not degree 2")
    if not bool(result.get("unconditional")) or result.get("assumptions"):
        raise ValueError("isogeny-descent result is not unconditional")
    routes = list(result.get("routes") or [])
    if not routes or len(routes) != int(result.get("isogeny_count") or 0):
        raise ValueError("isogeny-descent route count is inconsistent")
    first_bounds = []
    for route in routes:
        if not bool(route.get("map_verified")) or int(route.get("degree") or 0) != 2:
            raise ValueError("isogeny-descent route lacks exact map verification")
        phi_dim = int(route["phi_selmer_dimension"])
        dual_dim = int(route["dual_phi_selmer_dimension"])
        first_upper = int(route["first_descent_rank_upper"])
        if phi_dim < 0 or dual_dim < 0 or first_upper < 0:
            raise ValueError("negative isogeny Selmer dimension or upper")
        if route.get("phi_selmer_relation") != "=" or route.get("dual_phi_selmer_relation") != "=":
            raise ValueError("isogeny Selmer dimensions are not exact")
        if first_upper != phi_dim + dual_dim - 2:
            raise ValueError("phi/dual Selmer rank identity is inconsistent")
        first_bounds.append(first_upper)
    upper = int(result["mordell_weil_rank_upper"])
    selmer = int(result["two_selmer_rank"])
    torsion = int(result["two_torsion_rank"])
    if upper < 0 or selmer < torsion or upper > selmer - torsion:
        raise ValueError("invalid final isogeny-descent upper")
    if int(result["first_descent_combined_upper"]) != min(first_bounds):
        raise ValueError("combined first-descent upper is inconsistent")
    if upper > min(first_bounds):
        raise ValueError("final upper exceeds the first-descent upper")
    if E is not None:
        from sage.all import QQ, EllipticCurve

        Em = E.global_minimal_model()
        maps = list(Em.isogenies_prime_degree(2))
        if len(maps) != len(routes):
            raise ValueError("worker route count does not match exact Sage maps")
        unused = set(range(len(maps)))
        for route in routes:
            target = EllipticCurve(
                QQ,
                [QQ(str(x)) for x in route["isogenous_model_a_invariants"]],
            )
            matches = [
                index for index in sorted(unused)
                if maps[index].codomain().is_isomorphic(target)
                and str(maps[index].kernel_polynomial())
                == str(route.get("kernel_polynomial"))
            ]
            if not matches:
                raise ValueError("worker route does not match an exact Sage isogeny")
            unused.remove(matches[0])
    return result


def run_isogeny_descent(
    E,
    *,
    degree=2,
    first_limit=20,
    second_limit=8,
    second_descent=True,
    timeout=300,
):
    timeout = max(1, int(timeout))
    payload = {
        "a_invariants": [str(x) for x in E.a_invariants()],
        "degree": int(degree),
        "first_limit": int(first_limit),
        "second_limit": int(second_limit),
        "second_descent": bool(second_descent),
    }
    started = time.monotonic()
    try:
        cp = subprocess.run(
            [sys.executable, "-m", "rank42.isogeny_descent_worker"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        runtime = time.monotonic() - started
        raise IsogenyDescentTimeout(
            f"isogeny descent exceeded {timeout}s",
            runtime=runtime,
        ) from exc

    runtime = time.monotonic() - started
    marker = None
    for line in reversed((cp.stdout or "").splitlines()):
        if line.startswith(RESULT_MARKER):
            marker = line[len(RESULT_MARKER):]
            break
    tail = "\n".join(
        ((cp.stdout or "") + "\n" + (cp.stderr or "")).splitlines()[-40:]
    )
    if cp.returncode != 0 or marker is None:
        raise IsogenyDescentFailure(
            f"isogeny-descent worker failed with exit={cp.returncode}"
            + (f"; tail:\n{tail}" if tail else ""),
            runtime=runtime,
            returncode=cp.returncode,
            tail=tail,
        )
    try:
        result = json.loads(marker)
        validate_isogeny_descent_result(result, E=E)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise IsogenyDescentFailure(
            f"isogeny-descent worker returned invalid evidence: {exc}",
            runtime=runtime,
            returncode=cp.returncode,
            tail=tail,
        ) from exc
    result["_runtime_seconds"] = runtime
    return result
