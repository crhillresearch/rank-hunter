"""Core verification for plugin-supplied rigorous rank-upper certificates."""
from __future__ import annotations

from rank42.classical_descent import (
    ClassicalDescentFailure,
    ClassicalDescentTimeout,
    run_classical_descent,
)

SCHEMA = "rank42.plugin_rigorous_upper.v1"
SUPPORTED_VERIFIERS = frozenset({"core_mwrank_full_two_descent"})


def verify_plugin_rigorous_upper_certificate(
    E,
    certificate,
    *,
    known_points,
    timeout,
):
    """Verify a typed plugin upper-bound certificate using a core-owned engine.

    Plugin declarations are metadata until this function returns verified=True.
    The first supported verifier intentionally uses Rank Hunter's own isolated
    mwrank full two-descent path; arbitrary plugin booleans/integers are never
    accepted as proof.
    """
    if not isinstance(certificate, dict):
        return {"verified": False, "reason": "missing_typed_certificate"}
    if str(certificate.get("schema") or "") != SCHEMA:
        return {"verified": False, "reason": "unsupported_certificate_schema"}
    if str(certificate.get("status") or "") != "completed":
        return {"verified": False, "reason": "certificate_not_completed"}

    verifier = str(certificate.get("verifier") or "")
    if verifier not in SUPPORTED_VERIFIERS:
        return {
            "verified": False,
            "reason": "unsupported_certificate_verifier",
            "verifier": verifier or None,
        }
    if list(certificate.get("assumptions") or []):
        return {
            "verified": False,
            "reason": "certificate_has_unverified_assumptions",
            "verifier": verifier,
        }

    try:
        claimed_upper = int(certificate["rigorous_upper"])
    except Exception:
        return {
            "verified": False,
            "reason": "certificate_missing_rigorous_upper",
            "verifier": verifier,
        }
    if claimed_upper < 0:
        return {
            "verified": False,
            "reason": "certificate_invalid_rigorous_upper",
            "verifier": verifier,
        }

    options = certificate.get("options") or {}
    if not isinstance(options, dict):
        return {
            "verified": False,
            "reason": "certificate_options_must_be_object",
            "verifier": verifier,
        }

    if verifier == "core_mwrank_full_two_descent":
        try:
            result = run_classical_descent(
                E.a_invariants(),
                list(known_points or []),
                mode="mwrank_coverings",
                timeout=max(1, int(timeout)),
                first_limit=max(1, int(options.get("first_limit") or 20)),
                second_limit=max(1, int(options.get("second_limit") or 10)),
            )
        except ClassicalDescentTimeout as exc:
            return {
                "verified": False,
                "reason": "core_certificate_verification_timeout",
                "verifier": verifier,
                "error": str(exc),
            }
        except ClassicalDescentFailure as exc:
            return {
                "verified": False,
                "reason": "core_certificate_verification_error",
                "verifier": verifier,
                "error": str(exc),
            }

        if str(result.get("status") or "") != "completed":
            return {
                "verified": False,
                "reason": "core_certificate_verifier_incomplete",
                "verifier": verifier,
            }
        if str(result.get("engine") or "") != "mwrank_coverings":
            return {
                "verified": False,
                "reason": "core_certificate_verifier_engine_mismatch",
                "verifier": verifier,
            }
        upper = result.get("rigorous_upper")
        if upper is None:
            return {
                "verified": False,
                "reason": "core_certificate_verifier_no_upper",
                "verifier": verifier,
            }
        upper = int(upper)
        if upper != claimed_upper:
            return {
                "verified": False,
                "reason": "certificate_upper_mismatch",
                "verifier": verifier,
                "claimed_upper": claimed_upper,
                "verified_upper": upper,
            }
        return {
            "verified": True,
            "verifier": verifier,
            "rigorous_upper": upper,
            "engine": "eclib_mwrank_full_two_descent",
            "engine_version": result.get("engine_version"),
            "certificate": {
                "schema": SCHEMA,
                "status": "completed",
                "verifier": verifier,
                "rigorous_upper": upper,
                "assumptions": [],
                "plugin_certificate": dict(certificate),
                "core_verification": {
                    "engine": result.get("engine"),
                    "rigorous_upper": upper,
                    "minimal_model_a_invariants": result.get(
                        "minimal_model_a_invariants"
                    ),
                    "runtime_seconds": result.get("runtime_seconds"),
                    "worker_exit_code": result.get("worker_exit_code"),
                    "options": result.get("options"),
                },
            },
            "elapsed_seconds": result.get("runtime_seconds"),
        }

    return {
        "verified": False,
        "reason": "unsupported_certificate_verifier",
        "verifier": verifier,
    }
