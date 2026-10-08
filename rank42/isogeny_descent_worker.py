"""Isolated eclib/mwrank descent through rational 2-isogenies."""
from __future__ import annotations

import ast
import ctypes
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

from sage.all import QQ, EllipticCurve
from sage.libs.eclib.interface import mwrank_EllipticCurve

RESULT_MARKER = "RANK42_ISOGENY_DESCENT_RESULT="


def _version(module, attribute):
    try:
        return str(getattr(__import__(module, fromlist=[attribute]), attribute))
    except Exception:
        return None


def _emit(payload):
    print(RESULT_MARKER + json.dumps(payload, sort_keys=True), flush=True)


def _flush_all():
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    finally:
        try:
            ctypes.CDLL(None).fflush(None)
        except Exception:
            pass


def _run_with_native_output_capture(callback):
    """Capture Python and C++ writes to stdout/stderr for certificate parsing."""
    with tempfile.TemporaryFile(mode="w+b") as captured:
        saved_stdout = os.dup(1)
        saved_stderr = os.dup(2)
        try:
            _flush_all()
            os.dup2(captured.fileno(), 1)
            os.dup2(captured.fileno(), 2)
            callback()
            _flush_all()
        finally:
            os.dup2(saved_stdout, 1)
            os.dup2(saved_stderr, 2)
            os.close(saved_stdout)
            os.close(saved_stderr)
        captured.seek(0)
        return captured.read().decode("utf-8", errors="replace")


def _integer_line(segment, prefix, *, required=True):
    for raw in segment.splitlines():
        line = raw.strip().replace("\t", " ")
        if line.startswith(prefix):
            values = re.findall(r"-?\d+", line)
            if values:
                return int(values[-1]), ("<=" if "<=" in line else "=")
    if required:
        raise RuntimeError(f"mwrank certificate omitted {prefix!r}")
    return None, None


def _model(text):
    values = ast.literal_eval("[" + text + "]")
    if len(values) != 5:
        raise RuntimeError("mwrank emitted a malformed isogenous model")
    return [str(QQ(value)) for value in values]


def _parse_routes(transcript):
    pattern = re.compile(
        r"Using 2-isogenous curve \[([^\]]+)\]"
        r"(?: \(minimal model \[([^\]]+)\]\))?"
    )
    matches = list(pattern.finditer(transcript))
    routes = []
    for index, match in enumerate(matches, 1):
        end = matches[index].start() if index < len(matches) else len(transcript)
        segment = transcript[match.start():end]
        first_bound, first_relation = _integer_line(
            segment, "After first local descent, rank bound ="
        )
        phi_dim, phi_relation = _integer_line(
            segment, "rk(S^{phi}(E'))="
        )
        dual_dim, dual_relation = _integer_line(
            segment, "rk(S^{phi'}(E))="
        )
        second_bound, second_relation = _integer_line(
            segment, "After second local descent, rank bound =", required=False
        )
        phi_two_dim, phi_two_relation = _integer_line(
            segment, "rk(phi'(S^{2}(E)))", required=False
        )
        dual_two_dim, dual_two_relation = _integer_line(
            segment, "rk(phi(S^{2}(E')))", required=False
        )
        source_two_dim, source_two_relation = _integer_line(
            segment, "rk(S^{2}(E))", required=False
        )
        target_two_dim, target_two_relation = _integer_line(
            segment, "rk(S^{2}(E'))", required=False
        )
        routes.append({
            "route_index": index,
            "isogenous_model_a_invariants": _model(match.group(1)),
            "isogenous_minimal_model_a_invariants": (
                _model(match.group(2)) if match.group(2) else _model(match.group(1))
            ),
            "phi_selmer_dimension": phi_dim,
            "phi_selmer_relation": phi_relation,
            "dual_phi_selmer_dimension": dual_dim,
            "dual_phi_selmer_relation": dual_relation,
            "first_descent_rank_upper": first_bound,
            "first_descent_relation": first_relation,
            "phi_image_two_selmer_dimension": phi_two_dim,
            "phi_image_two_selmer_relation": phi_two_relation,
            "dual_phi_image_two_selmer_dimension": dual_two_dim,
            "dual_phi_image_two_selmer_relation": dual_two_relation,
            "source_two_selmer_dimension": source_two_dim,
            "source_two_selmer_relation": source_two_relation,
            "target_two_selmer_dimension": target_two_dim,
            "target_two_selmer_relation": target_two_relation,
            "second_descent_rank_upper": second_bound,
            "second_descent_relation": second_relation,
        })
    return routes


def _attach_exact_maps(E, routes):
    maps = list(E.isogenies_prime_degree(2))
    unused = set(range(len(maps)))
    for route in routes:
        target = EllipticCurve(
            QQ,
            [QQ(x) for x in route["isogenous_model_a_invariants"]],
        )
        matches = [
            index for index in sorted(unused)
            if maps[index].codomain().is_isomorphic(target)
        ]
        if not matches:
            raise RuntimeError("mwrank isogeny route did not match an exact Sage map")
        map_index = matches[0]
        unused.remove(map_index)
        phi = maps[map_index]
        dual = phi.dual()
        route.update({
            "sage_map_index": map_index + 1,
            "map_verified": True,
            "degree": int(phi.degree()),
            "kernel_polynomial": str(phi.kernel_polynomial()),
            "phi_rational_maps": [str(value) for value in phi.rational_maps()],
            "dual_rational_maps": [str(value) for value in dual.rational_maps()],
            "sage_codomain_a_invariants": [
                str(x) for x in phi.codomain().a_invariants()
            ],
        })
    if unused:
        raise RuntimeError("mwrank omitted one or more rational 2-isogeny routes")
    return maps


def main():
    payload = json.loads(sys.stdin.read())
    degree = int(payload.get("degree", 2))
    if degree != 2:
        _emit({
            "status": "unsupported",
            "reason": "core_isogeny_descent_v1_supports_degree_2_only",
            "degree": degree,
        })
        return

    first_limit = int(payload.get("first_limit", 20))
    second_limit = int(payload.get("second_limit", 8))
    second_descent = bool(payload.get("second_descent", True))
    if first_limit <= 0 or second_limit <= 0:
        raise ValueError("descent search limits must be positive")

    E = EllipticCurve(
        QQ,
        [QQ(str(x)) for x in payload["a_invariants"]],
    ).global_minimal_model()
    sage_maps = list(E.isogenies_prime_degree(2))
    if not sage_maps:
        _emit({
            "status": "unsupported",
            "reason": "no_rational_2_isogeny",
            "degree": 2,
            "minimal_model_a_invariants": [str(x) for x in E.a_invariants()],
            "two_torsion_rank": int(E.two_torsion_rank()),
        })
        return

    M = mwrank_EllipticCurve(list(E.a_invariants()), verbose=False)
    started = datetime.now(timezone.utc).isoformat()
    transcript = _run_with_native_output_capture(lambda: M.two_descent(
        verbose=True,
        selmer_only=True,
        first_limit=first_limit,
        second_limit=second_limit,
        second_descent=second_descent,
    ))
    finished = datetime.now(timezone.utc).isoformat()

    routes = _parse_routes(transcript)
    if not routes:
        raise RuntimeError("mwrank emitted no 2-isogeny descent routes")
    maps = _attach_exact_maps(E, routes)
    for route in routes:
        expected = (
            int(route["phi_selmer_dimension"])
            + int(route["dual_phi_selmer_dimension"])
            - 2
        )
        if expected != int(route["first_descent_rank_upper"]):
            raise RuntimeError("mwrank phi/dual Selmer dimensions are inconsistent")

    rank_upper = int(M.rank_bound())
    two_selmer_rank = int(M.selmer_rank())
    torsion_rank = int(E.two_torsion_rank())
    if rank_upper < 0 or rank_upper > two_selmer_rank - torsion_rank:
        raise RuntimeError("mwrank returned an inconsistent isogeny-descent upper")
    if rank_upper > min(int(route["first_descent_rank_upper"]) for route in routes):
        raise RuntimeError("mwrank final upper exceeds a first-descent upper")

    transcript_bytes = transcript.encode("utf-8", errors="replace")
    _emit({
        "status": "completed",
        "engine": "eclib.mwrank_2_isogeny_descent",
        "engine_version": _version("sage.version", "version"),
        "sage_version": _version("sage.version", "version"),
        "degree": 2,
        "minimal_model_a_invariants": [str(x) for x in E.a_invariants()],
        "two_torsion_rank": torsion_rank,
        "isogeny_count": len(maps),
        "routes": routes,
        "first_descent_combined_upper": min(
            int(route["first_descent_rank_upper"]) for route in routes
        ),
        "mordell_weil_rank_lower_diagnostic": int(M.rank()),
        "mordell_weil_rank_upper": rank_upper,
        "two_selmer_rank": two_selmer_rank,
        "mwrank_certain": bool(M.certain()),
        "second_descent_requested": second_descent,
        "first_limit": first_limit,
        "second_limit": second_limit,
        "map_verification": "exact_sage_rational_2_isogenies",
        "unconditional": True,
        "assumptions": [],
        "transcript_sha256": hashlib.sha256(transcript_bytes).hexdigest(),
        "transcript_tail": transcript.splitlines()[-120:],
        "started_at": started,
        "finished_at": finished,
    })


if __name__ == "__main__":
    main()
