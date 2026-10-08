"""Shared persistence/certification helpers for short-Weierstrass hunt lanes.

This module is core infrastructure used by General Hunt and optional search
plugins.  It contains no UI and no plugin-specific search loop.
"""
from __future__ import annotations

import json

from rank42.db import best_lower_bound, log_event, now, upsert_curve, update_curve
from rank42.curve_arithmetic_state import compute_and_publish_curve_arithmetic
from rank42.short_weierstrass import GENERAL_FAMILY, parameter_label
from rank42.rank_cert import RankCertificationFailure, RankCertificationTimeout, run_rank_certification
from rank42.novelty import check_curve_catalogs
from rank42.point_promotion import promote_certified_subgroup, promote_exact_rank_certificate


def trial_row(db, key):
    return db.execute("SELECT * FROM general_hunt_trials WHERE trial_key=?", (key,)).fetchone()


def record_trial(
    db,
    *,
    key,
    mode,
    source_a,
    source_b,
    A,
    B,
    x_bound,
    target,
    status,
    point_candidates=0,
    screened_rank=0,
    rigorous_lower=0,
    curve_id=None,
    certificate=None,
    metadata=None,
    error=None,
):
    ts = now()
    db.execute(
        """
        INSERT INTO general_hunt_trials(
            trial_key,mode,source_a,source_b,short_a,short_b,x_bound,target_lower,
            status,point_candidates,screened_rank,rigorous_lower,curve_id,
            certificate_json,metadata_json,error,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(trial_key) DO UPDATE SET
            status=excluded.status,
            point_candidates=excluded.point_candidates,
            screened_rank=excluded.screened_rank,
            rigorous_lower=excluded.rigorous_lower,
            curve_id=excluded.curve_id,
            certificate_json=excluded.certificate_json,
            metadata_json=excluded.metadata_json,
            error=excluded.error,
            updated_at=excluded.updated_at
        """,
        (
            key, str(mode), int(source_a), int(source_b), int(A), int(B), int(x_bound), int(target),
            str(status), int(point_candidates), int(screened_rank), int(rigorous_lower), curve_id,
            json.dumps(certificate, sort_keys=True) if certificate is not None else None,
            json.dumps(metadata or {}, sort_keys=True), error, ts, ts,
        ),
    )
    db.commit()


def store_short_curve_hit(db, E, A, B, basis, rigorous_lower, certificate, *, score=None):
    """Persist one exact short-Weierstrass hit using the shared General Hunt family identity."""
    basis = list(basis)
    if int(rigorous_lower) != len(basis):
        raise ValueError(
            "General Hunt rigorous lower must equal the certified subgroup basis size"
        )
    incumbent_before = best_lower_bound(db)
    parameter = parameter_label(A, B)
    curve_id = upsert_curve(db, family=GENERAL_FAMILY, parameter=parameter, score=score)
    update_curve(
        db,
        curve_id,
        a_invariants_json=json.dumps([str(a) for a in E.a_invariants()]),
    )
    arithmetic = compute_and_publish_curve_arithmetic(
        db,
        curve_id,
        E,
        source="general_hunt",
        method="sage_exact_global_invariants",
    )
    if arithmetic["publication"]["conflicts"]:
        log_event(
            db,
            curve_id,
            "warn",
            (
                "General Hunt arithmetic materialization conflicts with existing "
                "local/reference arithmetic state: "
                + ", ".join(arithmetic["publication"]["conflicts"])
            ),
        )

    promoted = promote_certified_subgroup(
        db,
        curve_id=curve_id,
        E=E,
        points=basis,
        source="general_hunt",
        search_ref="general_hunt:exact-certificate",
        certificate=certificate,
        metadata={
            "general_hunt": True,
            "short_A": int(A),
            "short_B": int(B),
        },
        engine="general_hunt_exact_certificate",
    )
    if promoted["rank_inconsistent"]:
        log_event(
            db,
            curve_id,
            "error",
            (
                "General Hunt exact subgroup certificate conflicts with existing "
                "rigorous rank evidence"
            ),
        )
    else:
        log_event(
            db,
            curve_id,
            "best" if int(rigorous_lower) > incumbent_before else "info",
            f"Exact certificate proves rank >= {int(rigorous_lower)}",
        )
    return curve_id


def try_exact_rank(db, curve_id, E, rigorous_lower, timeout):
    if int(timeout) <= 0:
        return None
    try:
        result = run_rank_certification([str(a) for a in E.a_invariants()], timeout=int(timeout))
    except RankCertificationTimeout:
        log_event(db, curve_id, "info", f"Exact-rank attempt timed out after {int(timeout)}s")
        return None
    except RankCertificationFailure as exc:
        log_event(db, curve_id, "warn", f"Exact-rank attempt failed: {exc}")
        return None
    exact = int(result["exact_rank"])
    if exact < int(rigorous_lower):
        log_event(db, curve_id, "error", f"rank certification inconsistency: exact {exact} < proven lower {int(rigorous_lower)}")
        return None

    promoted = promote_exact_rank_certificate(
        db,
        curve_id=curve_id,
        model=E.a_invariants(),
        exact_rank=exact,
        certificate=result,
        engine="general_hunt_sage_rank",
        source="general_hunt",
        metadata={"proof_mode": bool(result.get("proof_mode"))},
    )
    if promoted["rank_inconsistent"] or not promoted["projected"]:
        log_event(
            db,
            curve_id,
            "error",
            (
                f"rank certification inconsistency: exact {exact} conflicts with "
                "existing rigorous rank evidence"
            ),
        )
        return None

    log_event(db, curve_id, "best", f"Sage proof-mode certification establishes exact rank {exact}")
    return exact


def try_catalog_check(db, curve_id, E, timeout):
    if int(timeout) <= 0:
        return None
    try:
        return check_curve_catalogs(db, curve_id, E, lmfdb_timeout=int(timeout))
    except Exception as exc:
        log_event(db, curve_id, "warn", f"catalog cross-check failed: {exc!r}")
        return None


# Compatibility aliases for existing plugin/search code during the v0.8.x ->
# v0.8.7 transition.  New code should use the public names above.
_trial_row = trial_row
_record_trial = record_trial
_store_hit = store_short_curve_hit
_try_exact_rank = try_exact_rank
_try_catalog_check = try_catalog_check
