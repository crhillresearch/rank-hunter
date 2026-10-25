"""Certify generic independence using exact independent specialization witnesses."""
from __future__ import annotations

import hashlib
import json

from sage.all import QQ

from rank42.exact_lb import (
    ExactCertificateFailure, ExactCertificateTimeout, run_exact_certificate,
)
from rank42.formula_family import FormulaFamily


def certify_discovered_sections(
    family, parameter, stored_curve, search_result, *, timeout=120, max_sections=12,
):
    if not isinstance(family, FormulaFamily):
        return {"status": "unsupported", "reason": "requires_json_formula_family"}
    if search_result.get("status") != "completed" or search_result.get("evidence") != "exact_function_field_identity":
        return {"status": "inconclusive", "reason": "no_completed_exact_section_search"}
    family_json = json.dumps(family.data, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(family_json.encode()).hexdigest()
    if search_result.get("source_sha256") != digest:
        return {"status": "error", "reason": "family_source_changed"}
    sections = list(search_result.get("sections") or [])[:max(1, int(max_sections))]
    if not sections:
        return {"status": "completed", "generic_lower": 0, "reason": "no_sections_found"}
    if any(not isinstance(section, dict) or "x" not in section or "y" not in section
           for section in sections):
        return {"status": "error", "reason": "malformed_section_record"}
    # A second, independent function-field check protects the certificate from
    # stale or corrupted search artifacts before any generic claim is made.
    try:
        check_data = dict(family.data)
        check_data["sections"] = sections
        checked_family = FormulaFamily(check_data)
        checked_family.validate_symbolically()
        source_curve = checked_family.curve(QQ(str(parameter)))
        if source_curve is None or not source_curve.is_isomorphic(stored_curve):
            return {"status": "inconclusive", "reason": "invalid_or_unmatched_specialization"}
        specialized = checked_family.generic_section_points(QQ(str(parameter)))
        transport = source_curve.isomorphism_to(stored_curve)
        points = [transport(P) for P in specialized]
    except Exception as exc:
        return {"status": "error", "reason": "section_identity_or_fiber_check_failed",
                "detail": str(exc)[:500]}
    basis = []
    accepted = []
    certificate = None
    inconclusive = 0
    for index, P in enumerate(points):
        if P.is_zero() or P in basis or -P in basis:
            continue
        try:
            trial = run_exact_certificate(
                stored_curve.a_invariants(), basis + [P],
                timeout=max(1, int(timeout)),
            )
        except (ExactCertificateFailure, ExactCertificateTimeout):
            inconclusive += 1
            continue
        if trial.get("independent") is True:
            basis.append(P)
            accepted.append(index)
            certificate = trial
        elif trial.get("status") != "dependent":
            inconclusive += 1
    return {
        "status": "partial" if inconclusive else "completed",
        "generic_lower": len(basis),
        "verified_section_count": len(sections),
        "independent_section_indices": accepted,
        "independent_sections": [sections[index] for index in accepted],
        "specialization_parameter": str(QQ(str(parameter))),
        "specialized_points": [[str(P[0]), str(P[1])] for P in basis],
        "specialization_certificate": certificate,
        "inconclusive_candidates": inconclusive,
        "proof_method": "exact_Qt_identities_and_independent_specialization",
        "source_sha256": digest,
    }
