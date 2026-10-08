"""Exact independence analysis for selected point-ledger records.

The default workflow is deliberately point-by-point.  Starting from the stored
rigorous witness basis, each selected exact point is tested against the current
certified subgroup.  A dependent verdict therefore applies to that candidate
relative to an already independent working basis; one dependent point can no
longer cause every point in a multi-selection to be mislabeled.
"""
from __future__ import annotations

import argparse
import hashlib
import json

from rank42.curve_research_state import get_curve_research_state
from rank42.db import connect, get_curve, log_event, update_curve
from rank42.exact_lb import ExactCertificateFailure, ExactCertificateTimeout, run_exact_certificate
from rank42.point_promotion import promote_certified_subgroup
from rank42.points import rigorous_witness_basis, set_point_hard_flag, upsert_point
from rank42.saturation import SaturationFailure, SaturationTimeout, persist_saturation_evidence, run_saturation
from rank42.rank_evidence import record_rank_evidence, reduce_rank_state

MARKER = "RANK42_INDEPENDENCE_RESULT="
ENGINE = "rank42.independence_check"
ENGINE_VERSION = "5"


def parse_args():
    ap = argparse.ArgumentParser(description="Exact independence test for stored rational points")
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--curve-id", type=int, required=True)
    ap.add_argument("--point-id", type=int, action="append", default=[])
    ap.add_argument("--timeout", type=int, default=120, help="hard timeout per candidate certificate")
    ap.add_argument("--max-halvings", type=int, default=40)
    ap.add_argument("--max-prime", type=int, default=1000000)
    ap.add_argument("--max-columns", type=int, default=640)
    ap.add_argument("--strategy", choices=["standard", "saturation", "trial-saturation"], default="standard")
    ap.add_argument("--saturation-timeout", type=int, default=900)
    ap.add_argument("--saturation-prime", type=int, default=2)
    ap.add_argument("--no-full-trial-fallback", action="store_true")
    return ap.parse_args()


def _point_key(P):
    return (str(P[0]), str(P[1]))


def _basis_fingerprint(points):
    raw = json.dumps([_point_key(P) for P in points], separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _metadata(row):
    try:
        value = json.loads(row["metadata_json"] or "{}")
    except Exception:
        value = {}
    return value if isinstance(value, dict) else {}


def _load_rigorous_basis(db, row, E):
    """Load the shared authoritative rigorous witness basis."""
    return rigorous_witness_basis(db, int(row["id"]), E)



def _cached_basis_saturation(db, curve_id, current_fingerprint, expected_count, point_factory):
    """Return a previously persisted hard-case basis saturation when reusable."""
    rows = db.execute(
        """
        SELECT * FROM rank_evidence
        WHERE curve_id=? AND engine='sage_saturation'
          AND evidence_type='saturation' AND status='completed'
        ORDER BY id DESC
        LIMIT 100
        """,
        (int(curve_id),),
    ).fetchall()
    for rec in rows:
        try:
            options = json.loads(rec["options_json"] or "{}")
        except Exception:
            options = {}
        if not isinstance(options, dict):
            continue
        if str(options.get("source") or "") != "hard_case_basis_precondition":
            continue
        if int(options.get("max_prime") or 0) != 2:
            continue
        before_fp = str(options.get("basis_fingerprint_before") or "")
        after_fp = str(options.get("basis_fingerprint_after") or "")
        if str(current_fingerprint) not in {before_fp, after_fp}:
            continue
        try:
            raw = json.loads(rec["points_json"] or "[]")
        except Exception:
            raw = []
        if not isinstance(raw, list) or len(raw) != int(expected_count):
            continue
        try:
            points = [point_factory(xy) for xy in raw]
        except Exception:
            continue
        actual_after = _basis_fingerprint(points)
        if after_fp and actual_after != after_fp:
            continue
        return points, {
            "cache_hit": True,
            "evidence_id": int(rec["id"]),
            "index": options.get("index"),
            "basis_fingerprint_before": before_fp,
            "basis_fingerprint_after": actual_after,
            "elapsed_seconds": rec["elapsed_seconds"],
        }
    return None, None


def _authoritative_rank_baseline(db, curve_id):
    state = reduce_rank_state(db, int(curve_id))
    exact = state["exact_rank"]
    return {
        "rigorous_lower": int(state["rigorous_lower"] or 0),
        "rigorous_upper": state["rigorous_upper"],
        "exact_rank": None if exact is None else int(exact),
        "inconsistent": bool(state["inconsistent"]),
    }


def _prepare_saturated_basis(
    db,
    *,
    curve_id,
    E,
    basis,
    model,
    saturate_basis,
    point_factory,
):
    """2-saturate the rigorous basis once, persist it, and reuse it thereafter."""
    current_fp = _basis_fingerprint(basis)
    cached, cache_meta = _cached_basis_saturation(
        db,
        curve_id,
        current_fp,
        len(basis),
        point_factory,
    )
    if cached is not None:
        print(
            f"[stage 1/3] cached saturated basis reused · "
            f"points={len(cached)} · index={cache_meta.get('index', '—')} · "
            f"evidence=#{cache_meta.get('evidence_id')}",
            flush=True,
        )
        if _basis_fingerprint(cached) != current_fp:
            update_curve(
                db,
                int(curve_id),
                generators_json=json.dumps([_point_key(P) for P in cached]),
            )
        return cached, cache_meta

    print(
        f"[stage 1/3] rigorous-basis 2-saturation started · points={len(basis)}",
        flush=True,
    )
    result = saturate_basis(basis)
    raw = list(result.get("saturated_points") or [])
    try:
        saturated = [point_factory(xy) for xy in raw]
    except Exception as exc:
        raise SaturationFailure(
            f"could not reconstruct saturated rigorous basis: {exc!r}"
        ) from exc
    if len(saturated) != len(basis):
        raise SaturationFailure(
            f"basis saturation returned {len(saturated)} point(s) "
            f"for a rank-{len(basis)} rigorous basis"
        )

    after_fp = _basis_fingerprint(saturated)
    result["witness_count"] = len(basis)
    result["witness_required"] = len(basis)
    evidence_id = persist_saturation_evidence(
        db,
        curve_id=int(curve_id),
        model=model,
        result=result,
        source="hard_case_basis_precondition",
        extra_options={
            "basis_fingerprint_before": current_fp,
            "basis_fingerprint_after": after_fp,
            "stage": "rigorous_basis_precondition",
        },
    )
    update_curve(
        db,
        int(curve_id),
        generators_json=json.dumps([_point_key(P) for P in saturated]),
    )
    meta = {
        "cache_hit": False,
        "evidence_id": int(evidence_id) if evidence_id is not None else None,
        "index": result.get("index"),
        "basis_fingerprint_before": current_fp,
        "basis_fingerprint_after": after_fp,
        "elapsed_seconds": result.get("elapsed_seconds"),
    }
    print(
        f"[stage 1/3] rigorous-basis 2-saturation completed · "
        f"elapsed={float(result.get('elapsed_seconds') or 0):.1f}s · "
        f"index={result.get('index', '—')} · active basis={len(saturated)}",
        flush=True,
    )
    print(
        "[stage 1/3] saturated basis persisted for reuse by remaining hard cases",
        flush=True,
    )
    return saturated, meta


def _item_point_label(item):
    rec = item.get("record")
    if rec is not None:
        try:
            return f"point #{int(rec['id'])}"
        except Exception:
            pass
    try:
        return f"point #{int(item.get('id'))}"
    except Exception:
        return "selected point"


def run_basis_first_independence(
    basis,
    candidates,
    certify,
    saturate_trial,
    point_factory,
    *,
    basis_preconditioning=None,
    allow_full_trial_fallback=True,
):
    """Try a candidate against a cached/prepared basis before full-trial saturation."""
    working = list(basis)
    outcomes = []
    for item in candidates:
        before = len(working)
        trial = list(working) + [item["point"]]
        point_label = _item_point_label(item)
        print(
            f"[hard case] {point_label} · trial points={len(trial)} · "
            f"prepared working basis={before}",
            flush=True,
        )
        print(
            "[stage 2/3] exact certificate on prepared basis started",
            flush=True,
        )

        pre_cert = None
        pre_error = None
        try:
            pre_cert = certify(trial)
        except ExactCertificateTimeout as exc:
            pre_error = str(exc)
            print(
                f"[stage 2/3] prepared-basis certificate TIMEOUT · {exc}",
                flush=True,
            )
        except ExactCertificateFailure as exc:
            pre_error = str(exc)
            print(
                f"[stage 2/3] prepared-basis certificate ERROR · {exc}",
                flush=True,
            )
        else:
            detail = (
                pre_cert.get("certificate")
                if isinstance(pre_cert.get("certificate"), dict)
                else {}
            )
            print(
                f"[stage 2/3] prepared-basis certificate finished · "
                f"status={pre_cert.get('status', 'unknown')} · "
                f"elapsed={float(pre_cert.get('runtime_seconds') or 0):.1f}s · "
                f"matrix_rank={detail.get('matrix_rank', '—')} · "
                f"halvings={detail.get('halvings', '—')}",
                flush=True,
            )
            if pre_cert.get("independent"):
                working.append(item["point"])
                outcomes.append({
                    **item,
                    "status": "independent",
                    "certificate": pre_cert,
                    "before": before,
                    "after": len(working),
                    "trial": trial,
                    "strategy": "basis_first_saturation",
                    "resolution_path": "prepared_basis_certificate",
                    "basis_preconditioning": basis_preconditioning,
                })
                continue
            if str(pre_cert.get("status") or "") == "dependent":
                outcomes.append({
                    **item,
                    "status": "dependent",
                    "certificate": pre_cert,
                    "before": before,
                    "after": before,
                    "trial": trial,
                    "strategy": "basis_first_saturation",
                    "resolution_path": "prepared_basis_certificate",
                    "basis_preconditioning": basis_preconditioning,
                })
                continue

        if not allow_full_trial_fallback:
            outcomes.append({
                **item,
                "status": "inconclusive",
                "error": pre_error,
                "before": before,
                "after": before,
                "trial": trial,
                "pre_saturation_certificate": pre_cert,
                "pre_saturation_error": pre_error,
                "strategy": "basis_first_saturation",
                "resolution_path": "prepared_basis_unresolved",
                "basis_preconditioning": basis_preconditioning,
            })
            print(
                "[stage 2/3] prepared basis unresolved · full-trial fallback deferred to escalator",
                flush=True,
            )
            continue

        print(
            "[stage 3/3] prepared basis did not resolve candidate; "
            "full-trial 2-saturation fallback started",
            flush=True,
        )
        try:
            saturation = saturate_trial(trial)
        except SaturationTimeout as exc:
            print(f"[stage 3/3] full-trial 2-saturation TIMEOUT · {exc}", flush=True)
            outcomes.append({
                **item,
                "status": "timeout",
                "error": str(exc),
                "before": before,
                "after": before,
                "trial": trial,
                "pre_saturation_certificate": pre_cert,
                "pre_saturation_error": pre_error,
                "strategy": "basis_first_saturation",
                "resolution_path": "full_trial_saturation",
                "basis_preconditioning": basis_preconditioning,
            })
            continue
        except SaturationFailure as exc:
            print(f"[stage 3/3] full-trial 2-saturation ERROR · {exc}", flush=True)
            outcomes.append({
                **item,
                "status": "error",
                "error": str(exc),
                "before": before,
                "after": before,
                "trial": trial,
                "pre_saturation_certificate": pre_cert,
                "pre_saturation_error": pre_error,
                "strategy": "basis_first_saturation",
                "resolution_path": "full_trial_saturation",
                "basis_preconditioning": basis_preconditioning,
            })
            continue

        raw = list(saturation.get("saturated_points") or [])
        print(
            f"[stage 3/3] full-trial 2-saturation completed · "
            f"elapsed={float(saturation.get('elapsed_seconds') or 0):.1f}s · "
            f"index={saturation.get('index', '—')} · returned basis={len(raw)}",
            flush=True,
        )
        try:
            saturated_trial = [point_factory(xy) for xy in raw]
        except Exception as exc:
            outcomes.append({
                **item,
                "status": "error",
                "error": f"could not reconstruct saturated trial basis: {exc!r}",
                "before": before,
                "after": before,
                "trial": trial,
                "saturation": saturation,
                "pre_saturation_certificate": pre_cert,
                "pre_saturation_error": pre_error,
                "strategy": "basis_first_saturation",
                "resolution_path": "full_trial_saturation",
                "basis_preconditioning": basis_preconditioning,
            })
            continue
        if len(saturated_trial) != len(trial):
            outcomes.append({
                **item,
                "status": "inconclusive",
                "error": (
                    f"full-trial saturation returned {len(saturated_trial)} point(s) "
                    f"for a {len(trial)}-point trial"
                ),
                "before": before,
                "after": before,
                "trial": trial,
                "saturation": saturation,
                "pre_saturation_certificate": pre_cert,
                "pre_saturation_error": pre_error,
                "strategy": "basis_first_saturation",
                "resolution_path": "full_trial_saturation",
                "basis_preconditioning": basis_preconditioning,
            })
            continue

        print(
            "[stage 3/3] exact certificate on saturated full trial started",
            flush=True,
        )
        try:
            cert = certify(saturated_trial)
        except ExactCertificateTimeout as exc:
            print(f"[stage 3/3] fallback exact certificate TIMEOUT · {exc}", flush=True)
            outcomes.append({
                **item,
                "status": "timeout",
                "error": str(exc),
                "before": before,
                "after": before,
                "trial": saturated_trial,
                "saturation": saturation,
                "pre_saturation_certificate": pre_cert,
                "pre_saturation_error": pre_error,
                "strategy": "basis_first_saturation",
                "resolution_path": "full_trial_saturation",
                "basis_preconditioning": basis_preconditioning,
            })
            continue
        except ExactCertificateFailure as exc:
            print(f"[stage 3/3] fallback exact certificate ERROR · {exc}", flush=True)
            outcomes.append({
                **item,
                "status": "error",
                "error": str(exc),
                "before": before,
                "after": before,
                "trial": saturated_trial,
                "saturation": saturation,
                "pre_saturation_certificate": pre_cert,
                "pre_saturation_error": pre_error,
                "strategy": "basis_first_saturation",
                "resolution_path": "full_trial_saturation",
                "basis_preconditioning": basis_preconditioning,
            })
            continue

        detail = cert.get("certificate") if isinstance(cert.get("certificate"), dict) else {}
        print(
            f"[stage 3/3] fallback exact certificate finished · "
            f"status={cert.get('status', 'unknown')} · "
            f"elapsed={float(cert.get('runtime_seconds') or 0):.1f}s · "
            f"matrix_rank={detail.get('matrix_rank', '—')} · "
            f"halvings={detail.get('halvings', '—')}",
            flush=True,
        )
        if cert.get("independent") and len(saturated_trial) == before + 1:
            working = list(saturated_trial)
            status = "independent"
        elif str(cert.get("status") or "") == "dependent":
            status = "dependent"
        else:
            status = "inconclusive"
        outcomes.append({
            **item,
            "status": status,
            "certificate": cert,
            "before": before,
            "after": len(working),
            "trial": saturated_trial,
            "original_trial": trial,
            "saturation": saturation,
            "pre_saturation_certificate": pre_cert,
            "pre_saturation_error": pre_error,
            "strategy": "basis_first_saturation",
            "resolution_path": "full_trial_saturation",
            "basis_preconditioning": basis_preconditioning,
            "working_after": list(working),
        })
    return working, outcomes


def run_maximal_independence(basis, candidates, certify):
    """Greedily extract an exact independent subset from ordered candidates.

    ``candidates`` contains dictionaries with ``point`` and optional metadata.
    ``certify`` receives the entire trial basis and returns the exact certificate
    dictionary.  Timeouts/errors are preserved as inconclusive outcomes and do
    not mutate the working basis.
    """
    working = list(basis)
    outcomes = []
    for item in candidates:
        before = len(working)
        trial = working + [item["point"]]
        try:
            cert = certify(trial)
        except ExactCertificateTimeout as exc:
            outcomes.append({**item, "status": "timeout", "error": str(exc), "before": before, "after": before, "trial": trial})
            continue
        except ExactCertificateFailure as exc:
            outcomes.append({**item, "status": "error", "error": str(exc), "before": before, "after": before, "trial": trial})
            continue

        if cert.get("independent"):
            working.append(item["point"])
            status = "independent"
        elif str(cert.get("status") or "") == "dependent":
            status = "dependent"
        else:
            status = "inconclusive"
        outcomes.append({**item, "status": status, "certificate": cert, "before": before, "after": len(working), "trial": trial})
    return working, outcomes


def run_saturation_independence(basis, candidates, saturate, certify, point_factory, *, saturation_label="2-saturation"):
    """Resolve hard candidates through exact p-saturation preconditioning.

    Saturation itself never promotes rank. The returned exact points are fed
    into the ordinary exact independence certificate; only that successful
    certificate may enlarge the rigorous working basis.
    """
    working = list(basis)
    outcomes = []
    for item in candidates:
        before = len(working)
        original_trial = list(working) + [item["point"]]
        rec = item.get("record")
        point_id = None
        if rec is not None:
            try:
                point_id = int(rec["id"])
            except Exception:
                point_id = None
        if point_id is None:
            try:
                point_id = int(item.get("id"))
            except Exception:
                point_id = None
        point_label = f"point #{point_id}" if point_id is not None else "selected point"

        print(
            f"[hard case] {point_label} · trial points={len(original_trial)} · "
            f"working basis={before}",
            flush=True,
        )
        print(
            f"[stage 1/2] exact {saturation_label} started",
            flush=True,
        )
        try:
            saturation = saturate(original_trial)
        except SaturationTimeout as exc:
            print(f"[stage 1/2] {saturation_label} TIMEOUT · {exc}", flush=True)
            outcomes.append({**item, "status": "timeout", "error": str(exc),
                             "before": before, "after": before, "trial": original_trial,
                             "strategy": "saturation"})
            continue
        except SaturationFailure as exc:
            print(f"[stage 1/2] {saturation_label} ERROR · {exc}", flush=True)
            outcomes.append({**item, "status": "error", "error": str(exc),
                             "before": before, "after": before, "trial": original_trial,
                             "strategy": "saturation"})
            continue

        raw_points = list(saturation.get("saturated_points") or [])
        print(
            f"[stage 1/2] {saturation_label} completed · "
            f"elapsed={float(saturation.get('elapsed_seconds') or 0):.1f}s · "
            f"index={saturation.get('index', '—')} · "
            f"returned basis={len(raw_points)}",
            flush=True,
        )
        try:
            saturated_trial = [point_factory(xy) for xy in raw_points]
        except Exception as exc:
            outcomes.append({**item, "status": "error",
                             "error": f"could not reconstruct saturated basis: {exc!r}",
                             "before": before, "after": before, "trial": original_trial,
                             "saturation": saturation, "strategy": "saturation"})
            continue

        if len(saturated_trial) != len(original_trial):
            outcomes.append({**item, "status": "inconclusive",
                             "error": (f"saturation returned {len(saturated_trial)} point(s) "
                                       f"for a {len(original_trial)}-point trial"),
                             "before": before, "after": before, "trial": original_trial,
                             "saturation": saturation, "strategy": "saturation"})
            continue

        print(
            f"[stage 2/2] exact independence certificate started · "
            f"trial points={len(saturated_trial)}",
            flush=True,
        )
        try:
            cert = certify(saturated_trial)
        except ExactCertificateTimeout as exc:
            print(f"[stage 2/2] exact certificate TIMEOUT · {exc}", flush=True)
            outcomes.append({**item, "status": "timeout", "error": str(exc),
                             "before": before, "after": before, "trial": saturated_trial,
                             "saturation": saturation, "strategy": "saturation"})
            continue
        except ExactCertificateFailure as exc:
            print(f"[stage 2/2] exact certificate ERROR · {exc}", flush=True)
            outcomes.append({**item, "status": "error", "error": str(exc),
                             "before": before, "after": before, "trial": saturated_trial,
                             "saturation": saturation, "strategy": "saturation"})
            continue

        cert_detail = cert.get("certificate") if isinstance(cert.get("certificate"), dict) else {}
        print(
            f"[stage 2/2] exact certificate finished · "
            f"status={cert.get('status', 'unknown')} · "
            f"elapsed={float(cert.get('runtime_seconds') or 0):.1f}s · "
            f"matrix_rank={cert_detail.get('matrix_rank', '—')} · "
            f"halvings={cert_detail.get('halvings', '—')}",
            flush=True,
        )

        if cert.get("independent") and len(saturated_trial) == before + 1:
            working = list(saturated_trial)
            status = "independent"
        elif str(cert.get("status") or "") == "dependent":
            # Exact saturation changes the finite-index generating set, not the
            # rational rank. A rigorous dependence certificate on the saturated
            # full trial therefore proves the original basis+candidate trial
            # also has rank < before+1.
            status = "dependent"
        else:
            status = "inconclusive"
        outcomes.append({**item, "status": status, "certificate": cert,
                         "before": before, "after": len(working),
                         "trial": saturated_trial, "original_trial": original_trial,
                         "saturation": saturation, "strategy": "saturation",
                         "working_after": list(working)})
    return working, outcomes

def _record_attempt_evidence(db, *, curve_id, model, rec, outcome, max_halvings, max_prime, max_columns, strategy="standard", saturation_timeout=None):
    cert = outcome.get("certificate") or {}
    status = str(outcome["status"])
    existing_meta = _metadata(rec)
    existing_history = existing_meta.get("independence_history")
    attempt_number = len(existing_history) + 1 if isinstance(existing_history, list) else 1
    rigorous = status in {"independent", "dependent"}
    rigorous_lower = int(outcome["after"]) if status == "independent" else None
    evidence_status = {
        "independent": "completed",
        "dependent": "dependent",
        "inconclusive": "inconclusive",
        "timeout": "timeout",
        "error": "error",
    }[status]
    options = {
        "mode": "maximal_subset",
        "candidate_point_id": int(rec["id"]),
        "basis_size_before": int(outcome["before"]),
        "basis_fingerprint_before": str(outcome["basis_fingerprint_before"]),
        "attempt_number": int(attempt_number),
        "max_halvings": int(max_halvings),
        "max_prime": int(max_prime),
        "max_columns": int(max_columns),
        "strategy": str(strategy),
        "saturation_timeout": None if saturation_timeout is None else int(saturation_timeout),
        "saturation_index": (outcome.get("saturation") or {}).get("index"),
        "basis_saturation_index": (outcome.get("basis_preconditioning") or {}).get("index"),
        "basis_saturation_cache_hit": (outcome.get("basis_preconditioning") or {}).get("cache_hit"),
        "resolution_path": outcome.get("resolution_path"),
    }
    payload = {
        "engine": ENGINE,
        "engine_version": ENGINE_VERSION,
        "evidence_type": "exact_certificate",
        "rigorous": rigorous,
        "rigorous_lower": rigorous_lower,
        "status": evidence_status,
        "timed_out": status == "timeout",
        "partial": status in {"inconclusive", "timeout", "error"},
        "assumptions": [],
        "options": options,
        "points_found": [[str(P[0]), str(P[1])] for P in outcome.get("trial") or []],
        "minimal_model_a_invariants": (cert.get("minimal_model") or {}).get("a_invariants"),
        "stdout_summary": json.dumps({
            "outcome": status,
            "candidate_point_id": int(rec["id"]),
            "basis_size_before": int(outcome["before"]),
            "basis_size_after": int(outcome["after"]),
            "certificate": cert,
            "pre_saturation_certificate": outcome.get("pre_saturation_certificate"),
            "basis_preconditioning": outcome.get("basis_preconditioning"),
            "saturation": outcome.get("saturation"),
            "resolution_path": outcome.get("resolution_path"),
            "strategy": str(strategy),
            "error": outcome.get("error"),
        }, sort_keys=True),
        "stderr_summary": outcome.get("error"),
        "elapsed_seconds": cert.get("runtime_seconds"),
    }
    return record_rank_evidence(db, curve_id=int(curve_id), model=model, data=payload)


def _promote_working_basis(
    db,
    *,
    curve_id,
    E,
    baseline_basis,
    working,
    outcomes,
    strategy,
    basis_preconditioning=None,
    saturation_prime=None,
    promotion_source=None,
    promotion_search_ref=None,
    promotion_engine=None,
    certificate_chain_kind=None,
    promotion_metadata=None,
):
    """Promote the final certified subgroup using the persisted attempt chain."""
    baseline_basis = list(baseline_basis)
    working = list(working)
    old_explicit = len(baseline_basis)
    new_explicit = len(working)
    if new_explicit <= old_explicit:
        return {
            "promoted": False,
            "rank_inconsistent": False,
            "old_explicit": old_explicit,
            "new_explicit": new_explicit,
            "rigorous_lower": None,
        }

    independent = [
        outcome
        for outcome in outcomes
        if str(outcome.get("status")) == "independent"
    ]
    evidence_ids = [
        int(outcome["evidence_id"])
        for outcome in independent
        if outcome.get("evidence_id") is not None
    ]
    effective_saturation_prime = (
        2 if str(strategy) == "saturation"
        else (None if saturation_prime is None else int(saturation_prime))
    )
    certificate_chain = {
        "kind": str(certificate_chain_kind or "independence_workbench_chain"),
        "engine": ENGINE,
        "engine_version": ENGINE_VERSION,
        "strategy": str(strategy),
        "baseline_witness_count": old_explicit,
        "baseline_basis_fingerprint": _basis_fingerprint(baseline_basis),
        "final_witness_count": new_explicit,
        "final_basis_fingerprint": _basis_fingerprint(working),
        "independent_evidence_ids": evidence_ids,
        "basis_preconditioning": basis_preconditioning,
        "saturation_prime": effective_saturation_prime,
    }
    per_witness_metadata = [
        {
            "active_basis_index": index,
            "strategy": str(strategy),
            "saturation_prime": effective_saturation_prime,
            "promotion_chain_evidence_ids": evidence_ids,
        }
        for index, _P in enumerate(working)
    ]
    witness_source = promotion_source or (
        "hard_case_2_saturation"
        if str(strategy) == "saturation"
        else "independence_workbench"
    )
    witness_search_ref = promotion_search_ref or (
        "analysis:hard-saturation"
        if str(strategy) == "saturation"
        else "analysis:independence:promotion"
    )
    metadata = {
        "independence_workbench": promotion_source is None,
        "strategy": str(strategy),
        "baseline_explicit_basis": old_explicit,
        "new_explicit_basis": new_explicit,
        "saturation_prime": effective_saturation_prime,
    }
    if promotion_metadata:
        metadata.update(dict(promotion_metadata))
    promoted = promote_certified_subgroup(
        db,
        curve_id=int(curve_id),
        E=E,
        points=working,
        source=witness_source,
        search_ref=witness_search_ref,
        certificate=certificate_chain,
        metadata=metadata,
        point_metadata=per_witness_metadata,
        engine=str(promotion_engine or "independence_workbench_chain"),
    )
    return {
        "promoted": True,
        "rank_inconsistent": bool(promoted["rank_inconsistent"]),
        "old_explicit": old_explicit,
        "new_explicit": new_explicit,
        "rigorous_lower": int(promoted["rigorous_lower"]),
        "evidence_id": int(promoted["evidence_id"]),
        "certificate_chain": certificate_chain,
    }


def _persist_point_outcome(db, rec, outcome, *, evidence_id, max_halvings, max_prime, max_columns, strategy="standard", saturation_timeout=None):
    meta = _metadata(rec)
    cert = outcome.get("certificate") or {}
    entry = {
        "evidence_id": int(evidence_id),
        "status": str(outcome["status"]),
        "basis_size_before": int(outcome["before"]),
        "basis_size_after": int(outcome["after"]),
        "basis_fingerprint_before": str(outcome["basis_fingerprint_before"]),
        "certificate": cert,
        "pre_saturation_certificate": outcome.get("pre_saturation_certificate"),
        "basis_preconditioning": outcome.get("basis_preconditioning"),
        "saturation": outcome.get("saturation"),
        "resolution_path": outcome.get("resolution_path"),
        "strategy": str(strategy),
        "error": outcome.get("error"),
        "options": {
            "max_halvings": int(max_halvings),
            "max_prime": int(max_prime),
            "max_columns": int(max_columns),
            "saturation_timeout": None if saturation_timeout is None else int(saturation_timeout),
        },
    }
    history = meta.get("independence_history")
    if not isinstance(history, list):
        history = []
    history.append(entry)
    meta["independence_history"] = history[-20:]
    meta["last_independence"] = entry

    status = str(outcome["status"])
    if status == "independent":
        independence_status = "rigorous_independent"
        rigorous_independent = True
        role = "rigorous_witness"
    elif status == "dependent":
        independence_status = "dependent"
        rigorous_independent = False
        role = rec["role"]
    else:
        # Preserve a useful numerical scheduling label after an inconclusive
        # exact attempt.  The exact attempt is still visible in metadata/history.
        independence_status = rec["independence_status"]
        rigorous_independent = bool(rec["rigorous_independent"])
        role = rec["role"]

    updated = upsert_point(
        db,
        curve_id=int(rec["curve_id"]),
        x=rec["x"],
        y=rec["y"],
        source=rec["source"],
        role=role,
        exact_verified=True,
        independence_status=independence_status,
        rigorous_independent=rigorous_independent,
        search_ref=rec["search_ref"] or "analysis:independence",
        plugin_id=rec["plugin_id"],
        metadata=meta,
    )
    if status in {"independent", "dependent"} and updated is not None:
        set_point_hard_flag(db, int(updated["id"]), False)


def certify_stored_independence(
    db,
    *,
    curve_id,
    E,
    source="pipeline_independence",
    search_ref="pipeline:independence",
    certificate_timeout=120,
    max_candidates=64,
    max_halvings=40,
    max_prime=1000000,
    max_columns=640,
    candidate_point_ids=None,
):
    """Run durable exact independence attempts for stored candidate points.

    This is the shared standard-strategy certificate path used by Pipeline
    Evidence. It persists the same per-candidate evidence/history as the
    Independence Workbench and promotes only through the same certified
    subgroup service.
    """
    from sage.all import QQ

    curve_id = int(curve_id)
    row = get_curve(db, curve_id)
    if row is None:
        raise ValueError(f"curve #{curve_id} not found")

    before_state = get_curve_research_state(db, curve_id)
    before_lower = int(before_state["rigorous_lower"])
    allowed_point_ids = (
        None
        if candidate_point_ids is None
        else {int(point_id) for point_id in candidate_point_ids}
    )
    basis, required, complete = rigorous_witness_basis(db, curve_id, E)
    if not complete:
        return {
            "status": "inconclusive",
            "reason": "incomplete_rigorous_witness_basis",
            "basis_complete": False,
            "basis_count": len(basis),
            "basis_required": int(required),
            "selected": 0,
            "exact_attempts": 0,
            "independent": 0,
            "dependent": 0,
            "inconclusive": 0,
            "timeouts": 0,
            "errors": 0,
            "not_attempted": 0,
            "rank_growth": 0,
            "rigorous_lower": before_lower,
            "attempt_outcomes": [],
            "evidence_ids": [],
            "candidate_point_ids": (
                None
                if allowed_point_ids is None
                else sorted(allowed_point_ids)
            ),
        }

    known = {
        min(_point_key(P), _point_key(-P))
        for P in basis
    }
    candidate_items = []
    candidate_records = db.execute(
        """
        SELECT * FROM points
        WHERE curve_id=?
          AND exact_verified=1
          AND rigorous_independent=0
          AND independence_status!='dependent'
        ORDER BY CASE independence_status
            WHEN 'numerical_novel' THEN 0
            WHEN 'inconclusive' THEN 1
            WHEN 'unknown' THEN 2
            ELSE 3 END,
            id ASC
        """,
        (curve_id,),
    ).fetchall()
    for rec in candidate_records:
        if (
            allowed_point_ids is not None
            and int(rec["id"]) not in allowed_point_ids
        ):
            continue
        try:
            P = E(QQ(str(rec["x"])), QQ(str(rec["y"])))
        except Exception:
            continue
        if P.is_zero():
            continue
        key = min(_point_key(P), _point_key(-P))
        if key in known:
            continue
        known.add(key)
        candidate_items.append({"record": rec, "point": P})

    limit = max(0, int(max_candidates))
    selected_items = candidate_items[:limit]
    not_attempted_items = candidate_items[limit:]

    def certify(trial):
        return run_exact_certificate(
            E.a_invariants(),
            trial,
            timeout=max(1, int(certificate_timeout)),
            max_halvings=max(1, int(max_halvings)),
            max_prime=max(2, int(max_prime)),
            max_columns=max(1, int(max_columns)),
        )

    working, outcomes = run_maximal_independence(
        basis,
        selected_items,
        certify,
    )

    model = [str(x) for x in E.a_invariants()]
    actual_working = list(basis)
    for outcome in outcomes:
        outcome["basis_fingerprint_before"] = _basis_fingerprint(actual_working)
        rec = outcome["record"]
        evidence_id = _record_attempt_evidence(
            db,
            curve_id=curve_id,
            model=model,
            rec=rec,
            outcome=outcome,
            max_halvings=max_halvings,
            max_prime=max_prime,
            max_columns=max_columns,
            strategy="standard",
        )
        outcome["evidence_id"] = int(evidence_id)
        _persist_point_outcome(
            db,
            rec,
            outcome,
            evidence_id=evidence_id,
            max_halvings=max_halvings,
            max_prime=max_prime,
            max_columns=max_columns,
            strategy="standard",
        )
        if outcome["status"] == "independent":
            actual_working.append(outcome["point"])

    promotion = _promote_working_basis(
        db,
        curve_id=curve_id,
        E=E,
        baseline_basis=basis,
        working=actual_working,
        outcomes=outcomes,
        strategy="standard",
        promotion_source=str(source),
        promotion_search_ref=str(search_ref),
        promotion_engine="pipeline_independence_chain",
        certificate_chain_kind="pipeline_independence_chain",
        promotion_metadata={"pipeline_independence": True},
    )

    counts = {
        key: sum(str(outcome.get("status")) == key for outcome in outcomes)
        for key in ("independent", "dependent", "inconclusive", "timeout", "error")
    }
    attempted = len(outcomes)
    not_attempted = len(not_attempted_items)
    resolved = counts["independent"] + counts["dependent"]

    if promotion.get("rank_inconsistent"):
        status = "error"
        reason = "rank_evidence_conflict"
    elif attempted == 0:
        status = "partial" if not_attempted else "completed"
        reason = "candidate_limit_reached" if not_attempted else "no_exact_candidates"
    elif counts["timeout"] == attempted:
        status = "timeout"
        reason = "all_certificate_attempts_timed_out"
    elif counts["error"] == attempted:
        status = "error"
        reason = "all_certificate_attempts_failed"
    elif resolved == attempted and not_attempted == 0:
        status = "completed"
        reason = None
    elif resolved > 0:
        status = "partial"
        reason = "mixed_certificate_outcomes"
    elif counts["inconclusive"] == attempted and not_attempted == 0:
        status = "inconclusive"
        reason = "all_certificate_attempts_inconclusive"
    else:
        status = "partial" if not_attempted or resolved else "inconclusive"
        reason = (
            "candidate_limit_reached"
            if not_attempted
            else "mixed_unresolved_certificate_outcomes"
        )

    after_state = get_curve_research_state(db, curve_id)
    after_lower = int(after_state["rigorous_lower"])
    compact = []
    for outcome in outcomes:
        compact.append({
            "point_id": int(outcome["record"]["id"]),
            "status": str(outcome["status"]),
            "evidence_id": int(outcome["evidence_id"]),
            "basis_size_before": int(outcome["before"]),
            "basis_size_after": int(outcome["after"]),
            "error": outcome.get("error"),
        })

    return {
        "status": status,
        "reason": reason,
        "basis_complete": True,
        "basis_count": len(basis),
        "basis_required": int(required),
        "available_candidates": len(candidate_items),
        "selected": len(selected_items),
        "exact_attempts": attempted,
        "independent": counts["independent"],
        "dependent": counts["dependent"],
        "inconclusive": counts["inconclusive"],
        "timeouts": counts["timeout"],
        "errors": counts["error"],
        "not_attempted": not_attempted,
        "not_attempted_point_ids": [
            int(item["record"]["id"]) for item in not_attempted_items
        ],
        "rank_growth": max(0, after_lower - before_lower),
        "rigorous_lower": after_lower,
        "attempt_outcomes": compact,
        "evidence_ids": [int(outcome["evidence_id"]) for outcome in outcomes],
        "promotion_evidence_id": promotion.get("evidence_id"),
        "rank_inconsistent": bool(promotion.get("rank_inconsistent")),
        "certificate_service": ENGINE,
        "candidate_point_ids": (
            None
            if allowed_point_ids is None
            else sorted(allowed_point_ids)
        ),
    }


def main():
    args = parse_args()
    if not args.point_id:
        raise SystemExit("at least one --point-id is required")

    # Sage stays inside the CLI path so orchestration helpers remain unit-testable
    # in the lightweight UI/test environment.
    from sage.all import QQ, EllipticCurve

    db = connect(args.db)
    row = get_curve(db, args.curve_id)
    if row is None or not row["a_invariants_json"]:
        raise SystemExit("curve unavailable or missing model")
    E = EllipticCurve(QQ, [QQ(str(x)) for x in json.loads(row["a_invariants_json"])])

    basis, required, complete = _load_rigorous_basis(db, row, E)
    if not complete:
        raise SystemExit(
            f"stored rigorous witness basis incomplete: current rigorous lower is {required}, "
            f"but only {len(basis)} exact witness point(s) could be replayed"
        )

    candidate_items = []
    seen_ids = set()
    for pid in args.point_id:
        pid = int(pid)
        if pid in seen_ids:
            continue
        seen_ids.add(pid)
        rec = db.execute("SELECT * FROM points WHERE id=? AND curve_id=?", (pid, args.curve_id)).fetchone()
        if rec is None:
            raise SystemExit(f"point #{pid} does not belong to curve #{args.curve_id}")
        if not int(rec["exact_verified"] or 0):
            raise SystemExit(f"point #{pid} is not exact-verified")
        P = E(QQ(str(rec["x"])), QQ(str(rec["y"])))
        if P.is_zero():
            raise SystemExit(f"point #{pid} is the identity")
        candidate_items.append({"record": rec, "point": P})

    model = [str(x) for x in E.a_invariants()]

    def certify(trial):
        return run_exact_certificate(
            E.a_invariants(),
            trial,
            timeout=int(args.timeout),
            max_halvings=int(args.max_halvings),
            max_prime=int(args.max_prime),
            max_columns=int(args.max_columns),
            heartbeat_label=f"exact certificate curve #{int(args.curve_id)}",
            heartbeat_seconds=30,
        )

    basis_preconditioning = None
    if args.strategy == "saturation":
        def point_factory(xy):
            return E(QQ(str(xy[0])), QQ(str(xy[1])))

        def saturate_basis(points):
            return run_saturation(
                E.a_invariants(),
                points,
                max_prime=2,
                timeout=int(args.saturation_timeout),
                heartbeat_label=f"basis 2-saturation curve #{int(args.curve_id)}",
                heartbeat_seconds=30,
            )

        def saturate_trial(points):
            return run_saturation(
                E.a_invariants(),
                points,
                max_prime=2,
                timeout=int(args.saturation_timeout),
                heartbeat_label=f"full-trial 2-saturation curve #{int(args.curve_id)}",
                heartbeat_seconds=30,
            )

        try:
            basis, basis_preconditioning = _prepare_saturated_basis(
                db,
                curve_id=int(args.curve_id),
                E=E,
                basis=basis,
                model=model,
                saturate_basis=saturate_basis,
                point_factory=point_factory,
            )
        except SaturationTimeout as exc:
            print(f"[stage 1/3] rigorous-basis 2-saturation TIMEOUT · {exc}", flush=True)
            working = list(basis)
            outcomes = [
                {
                    **item,
                    "status": "timeout",
                    "error": str(exc),
                    "before": len(basis),
                    "after": len(basis),
                    "trial": list(basis) + [item["point"]],
                    "strategy": "basis_first_saturation",
                    "resolution_path": "basis_precondition",
                }
                for item in candidate_items
            ]
        except SaturationFailure as exc:
            print(f"[stage 1/3] rigorous-basis 2-saturation ERROR · {exc}", flush=True)
            working = list(basis)
            outcomes = [
                {
                    **item,
                    "status": "error",
                    "error": str(exc),
                    "before": len(basis),
                    "after": len(basis),
                    "trial": list(basis) + [item["point"]],
                    "strategy": "basis_first_saturation",
                    "resolution_path": "basis_precondition",
                }
                for item in candidate_items
            ]
        else:
            working, outcomes = run_basis_first_independence(
                basis,
                candidate_items,
                certify,
                saturate_trial,
                point_factory,
                basis_preconditioning=basis_preconditioning,
                allow_full_trial_fallback=not bool(args.no_full_trial_fallback),
            )
    elif args.strategy == "trial-saturation":
        prime = max(2, int(args.saturation_prime))

        def point_factory(xy):
            return E(QQ(str(xy[0])), QQ(str(xy[1])))

        def saturate_trial(points):
            return run_saturation(
                E.a_invariants(),
                points,
                max_prime=prime,
                min_prime=prime,
                timeout=int(args.saturation_timeout),
                heartbeat_label=f"p={prime} trial saturation curve #{int(args.curve_id)}",
                heartbeat_seconds=30,
            )

        working, outcomes = run_saturation_independence(
            basis,
            candidate_items,
            saturate_trial,
            certify,
            point_factory,
            saturation_label=f"p={prime} saturation",
        )
        for outcome in outcomes:
            outcome["strategy"] = "trial_saturation"
            outcome["saturation_prime"] = prime
            outcome["resolution_path"] = f"trial_p_{prime}_saturation"
    else:
        working, outcomes = run_maximal_independence(basis, candidate_items, certify)

    # Attach fingerprints from the actual certified sequence for persistence.
    actual_working = list(basis)
    for outcome in outcomes:
        outcome["basis_fingerprint_before"] = _basis_fingerprint(actual_working)
        rec = outcome["record"]
        evidence_id = _record_attempt_evidence(
            db,
            curve_id=args.curve_id,
            model=model,
            rec=rec,
            outcome=outcome,
            max_halvings=args.max_halvings,
            max_prime=args.max_prime,
            max_columns=args.max_columns,
            strategy=args.strategy,
            saturation_timeout=args.saturation_timeout,
        )
        outcome["evidence_id"] = evidence_id
        _persist_point_outcome(
            db,
            rec,
            outcome,
            evidence_id=evidence_id,
            max_halvings=args.max_halvings,
            max_prime=args.max_prime,
            max_columns=args.max_columns,
            strategy=args.strategy,
            saturation_timeout=args.saturation_timeout,
        )
        if outcome["status"] == "independent":
            if outcome.get("working_after") is not None:
                actual_working = list(outcome["working_after"])
            else:
                actual_working.append(outcome["point"])

    working = actual_working
    baseline_rank_state = _authoritative_rank_baseline(db, args.curve_id)
    old_lower = int(baseline_rank_state["rigorous_lower"])
    old_explicit = len(basis)
    new_explicit = len(working)
    exact_rank = baseline_rank_state["exact_rank"]

    promotion = _promote_working_basis(
        db,
        curve_id=args.curve_id,
        E=E,
        baseline_basis=basis,
        working=working,
        outcomes=outcomes,
        strategy=args.strategy,
        basis_preconditioning=basis_preconditioning,
        saturation_prime=args.saturation_prime,
    )
    conflict = bool(promotion["rank_inconsistent"])

    if new_explicit > old_explicit and not conflict:
        if new_explicit > old_lower:
            log_event(
                db,
                args.curve_id,
                "best",
                f"Independence workbench exact certificates improve rigorous lower bound {old_lower} -> {new_explicit}",
            )
        else:
            log_event(
                db,
                args.curve_id,
                "info",
                f"Independence workbench enlarged the explicit certified subgroup {old_explicit} -> {new_explicit}",
            )
    elif conflict:
        if exact_rank is not None and new_explicit > exact_rank:
            message = (
                f"Independence certificate produced explicit lower {new_explicit} "
                f"above stored exact rank {exact_rank}; review rank evidence before mutation"
            )
        else:
            message = (
                f"Independence certificate produced explicit lower {new_explicit} "
                "that conflicts with existing rigorous rank evidence; review rank evidence before mutation"
            )
        log_event(db, args.curve_id, "error", message)

    counts = {key: 0 for key in ("independent", "dependent", "inconclusive", "timeout", "error")}
    for outcome in outcomes:
        counts[outcome["status"]] += 1

    current_lower = (
        old_lower
        if conflict
        else int(promotion["rigorous_lower"] or old_lower)
    )
    result = {
        "curve_id": int(args.curve_id),
        "status": "evidence_conflict" if conflict else ("rank_growth" if new_explicit > old_lower else "complete"),
        "baseline_rigorous_lower": old_lower,
        "baseline_explicit_basis": old_explicit,
        "selected": len(candidate_items),
        "exact_attempts": len(outcomes),
        "new_independent": counts["independent"],
        "exact_dependent": counts["dependent"],
        "exact_inconclusive": counts["inconclusive"] + counts["timeout"] + counts["error"],
        "timeouts": counts["timeout"],
        "errors": counts["error"],
        "new_explicit_basis": new_explicit,
        "best_rigorous_lower": current_lower,
        "independent_point_ids": [int(o["record"]["id"]) for o in outcomes if o["status"] == "independent"],
        "dependent_point_ids": [int(o["record"]["id"]) for o in outcomes if o["status"] == "dependent"],
        "inconclusive_point_ids": [
            int(o["record"]["id"])
            for o in outcomes
            if o["status"] in {"inconclusive", "timeout", "error"}
        ],
        "evidence_ids": [int(o["evidence_id"]) for o in outcomes],
        "strategy": str(args.strategy),
        "basis_preconditioning": basis_preconditioning,
        "proof_note": (
            (
                "Only successful exact independence certificates enlarge the rigorous subgroup. "
                "The hard-case saturation strategy first 2-saturates and caches the existing rigorous basis "
                "without changing rank, then tests the candidate against that prepared basis. "
                "Full-trial saturation is used only as a fallback when the prepared-basis certificate is unresolved."
            )
            if args.strategy == "saturation"
            else (
                "Only successful exact independence certificates enlarge the rigorous subgroup. "
                f"This run directly p={int(args.saturation_prime)}-saturates the full basis+candidate trial, "
                "then asks the ordinary exact certificate to resolve the saturated trial."
            )
            if args.strategy == "trial-saturation"
            else (
                "Only successful exact independence certificates enlarge the rigorous subgroup. "
                "This run applies the ordinary exact certificate directly to the active rigorous basis plus candidate."
            )
        ),
    }
    print(MARKER + json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
