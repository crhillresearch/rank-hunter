"""Shared Family baseline/seed certification for Search and Pipeline.

This module owns exact Family section transport, preserved specialization-seed
recertification, and baseline promotion. It is independent of legacy Auto
campaign/trial orchestration.
"""
from __future__ import annotations

from sage.all import QQ

from rank42.auto_point_search import certify_ledger_growth
from rank42.db import log_event
from rank42.exact_lb import (
    ExactCertificateFailure,
    ExactCertificateTimeout,
    run_exact_certificate,
)
from rank42.point_promotion import promote_certified_subgroup
from rank42.points import upsert_point
from rank42.search_science import research_rank_state


class FamilyPointLoadError(RuntimeError):
    """Typed Family point-loading failure that must not look like no data."""

    def __init__(self, reason, *, getter_name, detail=None, point_index=None):
        self.reason = str(reason)
        self.getter_name = str(getter_name)
        self.detail = None if detail is None else str(detail)
        self.point_index = (
            None if point_index is None else int(point_index)
        )
        message = f"{self.getter_name}: {self.reason}"
        if self.point_index is not None:
            message += f" at point {self.point_index}"
        if self.detail:
            message += f" ({self.detail})"
        super().__init__(message)

    def as_dict(self):
        return {
            "reason": self.reason,
            "getter_name": self.getter_name,
            "point_index": self.point_index,
            "detail": self.detail,
        }


def _research_lower(db, curve_id, *, specialization=False):
    state = research_rank_state(db, curve_id)
    key = (
        "specialization_rigorous_lower"
        if specialization
        else "rigorous_lower"
    )
    return int(state.get(key) or 0)


def family_named_points_on_stored_model(
    family, getter_name, parameter, source_E, stored_E
):
    getter_name = str(getter_name)
    getter = getattr(family, getter_name, None)
    if not callable(getter):
        return []

    try:
        raw_points = getter(QQ(str(parameter)))
        raw_points = [] if raw_points is None else list(raw_points)
    except Exception as exc:
        raise FamilyPointLoadError(
            "plugin_exception",
            getter_name=getter_name,
            detail=repr(exc),
        ) from exc

    points = []
    for index, raw in enumerate(raw_points):
        try:
            P = source_E(raw)
            if P.is_zero():
                continue
        except Exception as exc:
            raise FamilyPointLoadError(
                "invalid_plugin_point",
                getter_name=getter_name,
                point_index=index,
                detail=repr(exc),
            ) from exc
        points.append(P)

    if not points:
        return []

    if list(source_E.a_invariants()) == list(stored_E.a_invariants()):
        out = []
        for index, P in enumerate(points):
            try:
                out.append(stored_E(P))
            except Exception as exc:
                raise FamilyPointLoadError(
                    "invalid_plugin_point",
                    getter_name=getter_name,
                    point_index=index,
                    detail=repr(exc),
                ) from exc
        return out

    try:
        phi = source_E.isomorphism_to(stored_E)
    except Exception as exc:
        raise FamilyPointLoadError(
            "model_transport_error",
            getter_name=getter_name,
            detail=repr(exc),
        ) from exc

    out = []
    for index, P in enumerate(points):
        try:
            out.append(phi(P))
        except Exception as exc:
            raise FamilyPointLoadError(
                "model_transport_error",
                getter_name=getter_name,
                point_index=index,
                detail=repr(exc),
            ) from exc
    return out


def family_points_on_stored_model(family, parameter, source_E, stored_E):
    return family_named_points_on_stored_model(
        family, "generic_section_points", parameter, source_E, stored_E
    )


def attach_family_specialization_seed(
    db,
    curve_id,
    family,
    parameter,
    source_E,
    stored_E,
    certificate_timeout,
):
    try:
        certified_points = family_named_points_on_stored_model(
            family,
            "certified_specialization_points",
            parameter,
            source_E,
            stored_E,
        )
    except FamilyPointLoadError as exc:
        return {
            "specialization_seed_points": 0,
            "specialization_seed_verified": False,
            "specialization_seed_certificate": None,
            "family_point_status": exc.reason,
            "family_point_error": exc.as_dict(),
            "certificate_status": "not_attempted",
            "attempts": 0,
            "growth": 0,
            "rigorous_lower": _research_lower(db, curve_id, specialization=True),
            "historical_rank_metadata_is_not_proof": True,
        }

    certified_meta_getter = getattr(
        family, "certified_specialization_metadata", None
    )
    certified_meta = {}
    certified_meta_status = "capability_absent"
    certified_meta_error = None
    if callable(certified_meta_getter):
        certified_meta_status = "completed"
        try:
            raw = certified_meta_getter(QQ(str(parameter)))
            if isinstance(raw, dict):
                certified_meta = dict(raw)
        except Exception as exc:
            certified_meta_status = "plugin_exception"
            certified_meta_error = repr(exc)

    for index, P in enumerate(certified_points):
        provenance = dict(certified_meta)
        provenance.update({
            "certificate_point_index": index + 1,
            "claim": (
                "externally preserved exact specialization point; "
                "independence certified independently by Rank Hunter"
            ),
            "auto_search": True,
        })
        upsert_point(
            db,
            curve_id=curve_id,
            x=P[0],
            y=P[1],
            source="family_specialization_certificate",
            role="candidate",
            exact_verified=True,
            independence_status="unknown",
            rigorous_independent=False,
            search_ref="auto:family-specialization-certificate",
            metadata=provenance,
        )

    before = _research_lower(db, curve_id, specialization=True)
    certified_bundle = None
    certified_verified = False
    certificate_status = (
        "no_points" if not certified_points else "not_needed"
    )
    if certified_points and before < len(certified_points):
        certificate_status = "inconclusive"
        try:
            certified_bundle = run_exact_certificate(
                stored_E.a_invariants(),
                certified_points,
                timeout=max(1, int(certificate_timeout)),
            )
        except ExactCertificateTimeout:
            certificate_status = "timeout"
            certified_bundle = None
        except ExactCertificateFailure:
            certificate_status = "error"
            certified_bundle = None
        else:
            if certified_bundle.get("independent") is True:
                certificate_status = "certified"
            else:
                certificate_status = str(
                    certified_bundle.get("status") or "inconclusive"
                )

    if (
        certified_bundle is not None
        and certified_bundle.get("independent") is True
    ):
        lower = len(certified_points)
        specialization_point_metadata = []
        for index, _P in enumerate(certified_points):
            provenance = dict(certified_meta)
            provenance.update({
                "certificate_point_index": index + 1,
                "claim": (
                    "externally preserved exact specialization point; "
                    "independence certified independently by Rank Hunter"
                ),
                "auto_search": True,
                "rank_hunter_recertified": True,
            })
            specialization_point_metadata.append(provenance)
        promoted = promote_certified_subgroup(
            db,
            curve_id=curve_id,
            E=stored_E,
            points=certified_points,
            source="family_specialization_certificate",
            search_ref="auto:family-specialization-certificate:exact",
            certificate=certified_bundle.get("certificate"),
            metadata={
                "auto_search": True,
                "rank_hunter_recertified": True,
                "baseline_kind": "preserved_specialization",
            },
            point_metadata=specialization_point_metadata,
            engine="auto_family_specialization_certificate",
        )
        certified_verified = True
        if promoted["rank_inconsistent"]:
            log_event(
                db,
                curve_id,
                "error",
                (
                    "Rank Hunter independently recertified preserved specialization "
                    "points but the resulting rigorous lower bound conflicts with "
                    "existing rigorous rank evidence"
                ),
            )
        else:
            log_event(
                db,
                curve_id,
                "best",
                (
                    "Rank Hunter independently recertified preserved specialization "
                    f"points and proved rigorous rank >= {lower}"
                ),
            )

    after_state = research_rank_state(db, curve_id)
    after = int(after_state["specialization_rigorous_lower"])
    return {
        "specialization_seed_points": len(certified_points),
        "specialization_seed_verified": bool(certified_verified),
        "specialization_seed_certificate": (
            None if certified_bundle is None
            else certified_bundle.get("certificate")
        ),
        "family_point_status": (
            "completed" if certified_points else "no_points_for_parameter"
        ),
        "family_point_error": None,
        "metadata_status": certified_meta_status,
        "metadata_error": certified_meta_error,
        "certificate_status": certificate_status,
        "attempts": (
            0
            if certificate_status in {"no_points", "not_needed"}
            else 1
        ),
        "growth": max(0, after - before),
        "rigorous_lower": after,
        "specialization_rigorous_lower": after,
        "research_rigorous_lower": int(after_state["rigorous_lower"]),
        "historical_rank_metadata_is_not_proof": True,
    }


def attach_family_baseline(
    db,
    curve_id,
    family,
    parameter,
    source_E,
    stored_E,
    certificate_timeout,
    exact_candidates,
):
    try:
        points = family_points_on_stored_model(
            family, parameter, source_E, stored_E
        )
    except FamilyPointLoadError as exc:
        return {
            "section_points": 0,
            "family_point_status": exc.reason,
            "family_point_error": exc.as_dict(),
            "bundle_certificate_status": "not_attempted",
            "attempts": 0,
            "growth": 0,
            "rigorous_lower": _research_lower(db, curve_id, specialization=True),
            "basis_complete": False,
        }

    metadata_getter = getattr(family, "generic_section_metadata", None)
    section_meta = []
    section_meta_status = "capability_absent"
    section_meta_error = None
    if callable(metadata_getter):
        section_meta_status = "completed"
        try:
            section_meta = list(metadata_getter(QQ(str(parameter))) or [])
        except Exception as exc:
            section_meta_status = "plugin_exception"
            section_meta_error = repr(exc)

    for index, P in enumerate(points):
        provenance = (
            dict(section_meta[index])
            if index < len(section_meta) and isinstance(section_meta[index], dict)
            else {}
        )
        provenance.update({
            "section_index": index,
            "claim": "exact specialized family section; independence certified separately",
            "auto_search": True,
        })
        upsert_point(
            db,
            curve_id=curve_id,
            x=P[0],
            y=P[1],
            source="auto_family_section",
            role="generic_section",
            exact_verified=True,
            independence_status="unknown",
            rigorous_independent=False,
            search_ref="auto:family-baseline",
            metadata=provenance,
        )

    before = _research_lower(db, curve_id, specialization=True)
    specialization = attach_family_specialization_seed(
        db,
        curve_id,
        family,
        parameter,
        source_E,
        stored_E,
        certificate_timeout,
    )

    before_generic = _research_lower(db, curve_id, specialization=True)
    bundle = None
    bundle_certificate_status = (
        "no_sections" if not points else "not_needed"
    )
    if points and before_generic < len(points):
        bundle_certificate_status = "inconclusive"
        try:
            bundle = run_exact_certificate(
                stored_E.a_invariants(),
                points,
                timeout=max(1, int(certificate_timeout)),
            )
        except ExactCertificateTimeout:
            bundle_certificate_status = "timeout"
            bundle = None
        except ExactCertificateFailure:
            bundle_certificate_status = "error"
            bundle = None
        else:
            if bundle.get("independent") is True:
                bundle_certificate_status = "certified"
            else:
                bundle_certificate_status = str(
                    bundle.get("status") or "inconclusive"
                )

    if bundle is not None and bundle.get("independent") is True:
        lower = len(points)
        section_point_metadata = []
        for index, _P in enumerate(points):
            provenance = (
                dict(section_meta[index])
                if index < len(section_meta) and isinstance(section_meta[index], dict)
                else {}
            )
            provenance.update({
                "section_index": index,
                "claim": "exact specialized family section; independence certified separately",
                "auto_search": True,
            })
            section_point_metadata.append(provenance)
        promoted = promote_certified_subgroup(
            db,
            curve_id=curve_id,
            E=stored_E,
            points=points,
            source="auto_family_section",
            search_ref="auto:family-baseline:bundle",
            certificate=bundle.get("certificate"),
            metadata={
                "auto_search": True,
                "baseline_kind": "generic_family_section_bundle",
            },
            point_metadata=section_point_metadata,
            engine="auto_family_section_bundle",
        )
        if promoted["rank_inconsistent"]:
            log_event(
                db,
                curve_id,
                "error",
                (
                    "Auto Hunter exact family-section bundle conflicts with existing "
                    "rigorous rank evidence"
                ),
            )
        else:
            log_event(
                db,
                curve_id,
                "best",
                f"Auto Hunter exact family-section bundle certifies rigorous rank >= {lower}",
            )
        after_state = research_rank_state(db, curve_id)
        specialization_after = int(
            after_state["specialization_rigorous_lower"]
        )
        return {
            "section_points": len(points),
            "family_point_status": (
                "completed" if points else "no_points_for_parameter"
            ),
            "family_point_error": None,
            "metadata_status": section_meta_status,
            "metadata_error": section_meta_error,
            "bundle_certificate_status": bundle_certificate_status,
            "specialization_seed_points": int(specialization["specialization_seed_points"]),
            "specialization_seed_verified": bool(specialization["specialization_seed_verified"]),
            "specialization_seed_certificate": specialization["specialization_seed_certificate"],
            "specialization_seed_status": specialization.get("family_point_status"),
            "specialization_seed_error": specialization.get("family_point_error"),
            "specialization_certificate_status": specialization.get("certificate_status"),
            "attempts": 1,
            "growth": max(0, specialization_after - before),
            "rigorous_lower": specialization_after,
            "specialization_rigorous_lower": specialization_after,
            "research_rigorous_lower": int(after_state["rigorous_lower"]),
            "basis_complete": True,
            "bundle_independent": True,
        }

    cert = certify_ledger_growth(
        db,
        curve_id=curve_id,
        E=stored_E,
        source="auto_family_section",
        search_ref="auto:family-baseline:exact",
        certificate_timeout=certificate_timeout,
        max_candidates=exact_candidates,
    )
    cert_state = research_rank_state(db, curve_id)
    persisted_specialization_after = int(
        cert_state["specialization_rigorous_lower"]
    )
    certified_growth = max(0, int(cert.get("growth") or 0))
    specialization_after = max(
        persisted_specialization_after,
        before_generic + certified_growth,
    )
    cert = {
        **cert,
        "rigorous_lower": specialization_after,
        "specialization_rigorous_lower": specialization_after,
        "research_rigorous_lower": max(
            int(cert_state["rigorous_lower"]),
            specialization_after,
        ),
    }
    return {
        "section_points": len(points),
        "family_point_status": (
            "completed" if points else "no_points_for_parameter"
        ),
        "family_point_error": None,
        "metadata_status": section_meta_status,
        "metadata_error": section_meta_error,
        "bundle_certificate_status": bundle_certificate_status,
        "specialization_seed_points": int(specialization["specialization_seed_points"]),
        "specialization_seed_verified": bool(specialization["specialization_seed_verified"]),
        "specialization_seed_certificate": specialization["specialization_seed_certificate"],
        "bundle_independent": False if bundle is not None else None,
        **cert,
    }


__all__ = [
    "FamilyPointLoadError",
    "attach_family_baseline",
    "attach_family_specialization_seed",
    "family_named_points_on_stored_model",
    "family_points_on_stored_model",
]
