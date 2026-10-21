"""Generic classical point-discovery tiers for Auto Search.

Search geometry is exact. Each chart has the form x = scale*X + center on the
completed-square cubic. ratpoints searches in X; every hit is mapped back to the
stored Weierstrass model and verified exactly before it reaches the point ledger.

When campaign identity is supplied, each (tier,center,scale,height) attempt is
persisted so a resumed Auto Search skips completed work and retries only an
interrupted running step.
"""
from __future__ import annotations

import json

from sage.all import QQ, EllipticCurve

from rank42.auto_search_state import get_step, upsert_step
from rank42.db import get_curve, log_event, proven_lower
from rank42.exact_lb import ExactCertificateFailure, ExactCertificateTimeout, run_exact_certificate
from rank42.model_prep import ModelPrepFailure, ModelPrepTimeout, run_global_minimal_model
from rank42.pipeline_state import get_pipeline_point_attempt, upsert_pipeline_point_attempt
from rank42.point_promotion import promote_certified_subgroup
from rank42.points import record_point_discovery, rigorous_witness_basis, upsert_point
from rank42.ratpoints import RatpointsFailure, RatpointsTimeout, run_ratpoints
from rank42.search_models import affine_cubic_polynomial, completed_square_polynomial, recover_weierstrass_y


TIER_SCALE_BANK = {
    "scout": ("1", "2", "1/2"),
    "structural": ("1", "2", "1/2", "3", "1/3", "5", "1/5"),
    "deep": ("1", "2", "1/2", "3", "1/3", "5", "1/5", "7", "1/7", "11", "1/11"),
    "priority": (
        "1", "2", "1/2", "3", "1/3", "5", "1/5", "7", "1/7",
        "11", "1/11", "13", "1/13", "17", "1/17", "19", "1/19",
    ),
}
STEP_TERMINAL = {"completed", "timeout", "error"}


def _point_key(P):
    Q = -P
    return min((str(P[0]), str(P[1])), (str(Q[0]), str(Q[1])))


def _ratpoint_projective_payload(hit):
    try:
        payload = hit.as_json()
        projective = payload.get("projective")
        return projective if isinstance(projective, list) else None
    except Exception:
        values = []
        for name in ("projective_x", "projective_y", "projective_z"):
            if not hasattr(hit, name):
                return None
            values.append(str(getattr(hit, name)))
        return values


def _point_discovery_pipeline_fields(pipeline_checkpoint):
    if not isinstance(pipeline_checkpoint, dict):
        return {}
    return {
        "pipeline_run_id": pipeline_checkpoint.get("run_id"),
        "pipeline_candidate_id": pipeline_checkpoint.get("candidate_id"),
        "pipeline_stage_index": pipeline_checkpoint.get("stage_index"),
        "pipeline_stage_id": pipeline_checkpoint.get("stage_id"),
    }


def _compact_engine_identity(provenance):
    data = dict(provenance or {})
    return {
        key: data.get(key)
        for key in (
            "backend",
            "executable",
            "version",
            "source",
            "vendored_revision",
            "version_probe_error",
        )
        if data.get(key) is not None
    }


def _remember_engine_identity(identities, provenance):
    compact = _compact_engine_identity(provenance)
    if not compact:
        return
    key = json.dumps(compact, sort_keys=True)
    if key not in identities:
        identities[key] = compact


def _complexity(q):
    q = QQ(q)
    return (
        int(abs(q.numerator())).bit_length() + int(q.denominator()).bit_length(),
        int(q.denominator()).bit_length(),
        abs(int(q.numerator())),
    )


def search_centers(db, curve_id, E, *, limit=12):
    """Choose exact x-centers from family sections, witnesses, and exact hits."""
    values = {QQ(0)}
    rows = db.execute(
        """
        SELECT x,rigorous_independent,role,id
        FROM points
        WHERE curve_id=? AND exact_verified=1
        ORDER BY rigorous_independent DESC,
                 CASE role WHEN 'generic_section' THEN 0 WHEN 'rigorous_witness' THEN 1 ELSE 2 END,
                 id ASC
        """,
        (int(curve_id),),
    ).fetchall()
    for row in rows:
        try:
            values.add(QQ(str(row["x"])))
        except Exception:
            continue
    ordered = sorted(values, key=lambda x: (0 if x == 0 else 1, _complexity(x)))
    return ordered[: max(1, int(limit))]


def search_scales(centers, tier):
    """Return exact generic + point-gap-derived affine scales."""
    base_tier = str(tier).split(":", 1)[0]
    values = {QQ(x) for x in TIER_SCALE_BANK.get(base_tier, TIER_SCALE_BANK["structural"])}
    finite = [QQ(x) for x in centers]
    gap_candidates = set()
    for i, left in enumerate(finite):
        for right in finite[i + 1:]:
            gap = abs(right - left)
            if gap == 0:
                continue
            gap_candidates.add(gap)
            gap_candidates.add(1 / gap)
    for value in sorted(gap_candidates, key=_complexity)[:8]:
        values.add(value)
    values.discard(QQ(0))
    return sorted(values, key=lambda s: (0 if s == 1 else 1, _complexity(s)))


def search_charts(db, curve_id, E, *, tier, budget):
    """Build a balanced deterministic exact affine chart bank.

    Small budgets must not be consumed entirely by rescalings around x=0.
    Known exact section/witness x-coordinates are genuine geometric search
    centers, so reserve roughly half the chart budget for translated charts
    before filling the remainder with pure rescalings and center+scale mixes.
    """
    budget = max(1, int(budget))
    center_limit = max(4, min(24, budget))
    centers = search_centers(db, curve_id, E, limit=center_limit)
    scales = search_scales(centers, tier)

    candidates = []
    seen = set()

    def add(center, scale):
        if len(candidates) >= budget:
            return False
        key = (str(QQ(center)), str(QQ(scale)))
        if key in seen:
            return True
        seen.add(key)
        candidates.append((QQ(center), QQ(scale)))
        return len(candidates) < budget

    add(QQ(0), QQ(1))
    if len(candidates) >= budget:
        return candidates

    nonzero_centers = [QQ(center) for center in centers if QQ(center) != 0]
    nonunit_scales = [QQ(scale) for scale in scales if QQ(scale) != 1]

    # Reserve about half of a small chart bank for point-centered translations.
    translation_slots = min(
        len(nonzero_centers),
        max(1, (budget - 1) // 2) if budget > 1 else 0,
    )
    for center in nonzero_centers[:translation_slots]:
        if not add(center, QQ(1)):
            return candidates

    # Fill next with simple/global rescalings.
    for scale in nonunit_scales:
        if not add(QQ(0), scale):
            return candidates

    # Then combine the most useful known centers with scale changes.
    for center in nonzero_centers:
        for scale in nonunit_scales:
            if not add(center, scale):
                return candidates

    # If there were fewer centers/scales than expected, include any remaining
    # native translations last.
    for center in nonzero_centers[translation_slots:]:
        if not add(center, QQ(1)):
            break

    return candidates[:budget]


def _ledger_candidates(db, curve_id, E, known):
    rows = db.execute(
        """
        SELECT * FROM points
        WHERE curve_id=? AND exact_verified=1 AND independence_status!='dependent'
        ORDER BY rigorous_independent DESC, id ASC
        """,
        (int(curve_id),),
    ).fetchall()
    out = []
    seen = set(known)
    for row in rows:
        try:
            P = E(QQ(str(row["x"])), QQ(str(row["y"])))
        except Exception:
            continue
        if P.is_zero():
            continue
        key = _point_key(P)
        if key in seen:
            continue
        seen.add(key)
        out.append(P)
    return out


def certify_ledger_growth(
    db,
    *,
    curve_id,
    E,
    source,
    search_ref,
    certificate_timeout=120,
    max_candidates=32,
):
    basis, required, complete = rigorous_witness_basis(db, curve_id, E)
    if not complete:
        return {
            "attempts": 0,
            "growth": 0,
            "rigorous_lower": int(proven_lower(get_curve(db, curve_id))),
            "basis_complete": False,
        }

    known = {_point_key(P) for P in basis}
    candidates = _ledger_candidates(db, curve_id, E, known)
    attempts = 0
    growth = 0
    for P in candidates[: max(0, int(max_candidates))]:
        attempts += 1
        trial = basis + [P]
        try:
            cert = run_exact_certificate(
                E.a_invariants(),
                trial,
                timeout=max(1, int(certificate_timeout)),
            )
        except (ExactCertificateFailure, ExactCertificateTimeout):
            continue

        if cert.get("independent") is True:
            promoted = promote_certified_subgroup(
                db,
                curve_id=curve_id,
                E=E,
                points=trial,
                source=source,
                search_ref=search_ref,
                certificate=cert.get("certificate"),
                metadata={"auto_search": True},
                engine="auto_search_exact_certificate",
            )
            if promoted["rank_inconsistent"]:
                log_event(
                    db,
                    curve_id,
                    "error",
                    (
                        "Auto Search exact certificate conflicts with existing rigorous "
                        f"rank state at lower {promoted['rigorous_lower']}"
                    ),
                )
                continue
            basis = trial
            growth += 1
            log_event(
                db,
                curve_id,
                "best",
                f"Auto Search exact certificate improves rigorous lower bound to {len(basis)}",
            )
        elif cert.get("status") == "dependent":
            upsert_point(
                db,
                curve_id=curve_id,
                x=P[0],
                y=P[1],
                source=source,
                role="candidate_extra",
                exact_verified=True,
                independence_status="dependent",
                rigorous_independent=False,
                search_ref=search_ref,
                metadata={"auto_search": True},
            )

    return {
        "attempts": attempts,
        "growth": growth,
        "rigorous_lower": int(proven_lower(get_curve(db, curve_id))),
        "basis_complete": True,
    }


def _completed_step(db, *, campaign_id, parameter, tier, center, scale, height):
    if campaign_id is None or parameter is None:
        return None
    row = get_step(
        db,
        campaign_id=campaign_id,
        parameter=parameter,
        tier=tier,
        center=center,
        scale=scale,
        height=height,
    )
    if row is not None and str(row["status"]) in STEP_TERMINAL:
        return row
    return None


def _record_step(db, *, campaign_id, parameter, tier, center, scale, height, **fields):
    if campaign_id is None or parameter is None:
        return None
    return upsert_step(
        db,
        campaign_id=campaign_id,
        parameter=parameter,
        tier=tier,
        center=center,
        scale=scale,
        height=height,
        **fields,
    )


def _denominator_range_possible(height, denominator_low):
    if denominator_low is None:
        return True
    try:
        return int(height) >= int(denominator_low)
    except Exception:
        return True


def _denominator_region(height, denominator_low, denominator_high):
    """Return the exact chart-X denominator interval searched at this height."""
    H = max(1, int(height))
    requested_low = None if denominator_low is None else int(denominator_low)
    requested_high = None if denominator_high is None else int(denominator_high)
    effective_low = 1 if requested_low is None else requested_low
    effective_high = H if requested_high is None else min(H, requested_high)
    return {
        "requested_denominator_low": requested_low,
        "requested_denominator_high": requested_high,
        "effective_denominator_low": effective_low,
        "effective_denominator_high": effective_high,
    }


def _pipeline_point_attempt(
    db, *, checkpoint, tier, center, scale, height
):
    if not isinstance(checkpoint, dict):
        return None
    required = ("run_id", "candidate_id", "stage_index", "stage_id")
    if any(checkpoint.get(key) is None for key in required):
        return None
    return get_pipeline_point_attempt(
        db,
        run_id=checkpoint["run_id"],
        candidate_id=checkpoint["candidate_id"],
        stage_index=checkpoint["stage_index"],
        tier=tier,
        center=center,
        scale=scale,
        height=height,
    )


def _record_pipeline_point_attempt(
    db,
    *,
    checkpoint,
    curve_id,
    tier,
    center,
    scale,
    height,
    denominator_low,
    denominator_high,
    effective_denominator_low,
    effective_denominator_high,
    timeout_seconds,
    retry_policy,
    engine=None,
    **fields,
):
    if not isinstance(checkpoint, dict):
        return None
    required = ("run_id", "candidate_id", "stage_index", "stage_id")
    if any(checkpoint.get(key) is None for key in required):
        return None
    return upsert_pipeline_point_attempt(
        db,
        run_id=checkpoint["run_id"],
        candidate_id=checkpoint["candidate_id"],
        curve_id=curve_id,
        stage_index=checkpoint["stage_index"],
        stage_id=checkpoint["stage_id"],
        tier=tier,
        center=center,
        scale=scale,
        height=height,
        denominator_low=denominator_low,
        denominator_high=denominator_high,
        effective_denominator_low=effective_denominator_low,
        effective_denominator_high=effective_denominator_high,
        timeout_seconds=timeout_seconds,
        retry_policy=retry_policy,
        engine=engine,
        **fields,
    )


def _pipeline_retry_decision(
    row, *, retry_policy, timeout_seconds, retry_timeout
):
    """Return (skip, effective_timeout, reason) for a persisted attempt."""
    if row is None:
        return False, int(timeout_seconds), "new"
    status = str(row["status"] or "")
    if status == "completed":
        return True, int(timeout_seconds), "completed"
    if status not in {"timeout", "error"}:
        # running means the previous process died/interrupted; always retry it.
        return False, int(timeout_seconds), "interrupted"

    policy = str(retry_policy or "manual")
    if policy == "manual":
        return True, int(timeout_seconds), "manual"
    if policy == "automatic":
        return False, int(timeout_seconds), "automatic"
    if policy == "escalated":
        target = max(1, int(retry_timeout or timeout_seconds))
        prior_timeout = max(
            1, int(row["timeout_seconds"] or timeout_seconds)
        )
        if prior_timeout >= target:
            return True, prior_timeout, "escalated_budget_already_attempted"
        return False, target, "escalated"
    raise ValueError(
        "point-search retry_policy must be manual, automatic, or escalated"
    )


def point_search_outcome(payload):
    """Reduce bounded point-search counters to one research outcome."""
    payload = dict(payload or {})
    explicit = payload.get("search_outcome")
    if explicit is not None:
        return str(explicit)

    counter_keys = {
        "completed_searches", "resume_skipped", "resume_completed",
        "resume_timeouts", "resume_failures", "timeouts", "failures",
        "map_back_failures", "planned_searches",
    }
    has_counters = any(key in payload for key in counter_keys)
    if not has_counters:
        status = str(payload.get("status") or "")
        if status in {"completed", "partial", "timeout", "error", "inconclusive"}:
            return status

    completed = int(payload.get("completed_searches") or 0)
    if "resume_completed" in payload:
        resumed_completed = int(payload.get("resume_completed") or 0)
    else:
        resumed_completed = int(payload.get("resume_skipped") or 0)
    timeouts = (
        int(payload.get("timeouts") or 0)
        + int(payload.get("resume_timeouts") or 0)
    )
    failures = (
        int(payload.get("failures") or 0)
        + int(payload.get("resume_failures") or 0)
    )
    map_back_failures = int(payload.get("map_back_failures") or 0)
    usable = completed + resumed_completed
    planned_raw = payload.get("planned_searches")
    planned = None if planned_raw is None else max(0, int(planned_raw))

    if failures:
        return "partial" if usable > 0 or timeouts > 0 else "error"
    if map_back_failures:
        return "partial" if usable > 0 else "error"
    if timeouts:
        return "partial" if usable > 0 else "timeout"
    if planned is not None:
        if planned == 0:
            return "inconclusive"
        if usable < planned:
            return "partial"
    return "completed"


def point_search_coverage(payload):
    """Return comparable completed-region coverage for one bounded search plan."""
    payload = dict(payload or {})
    completed = int(payload.get("completed_searches") or 0)
    if "resume_completed" in payload:
        resumed_completed = int(payload.get("resume_completed") or 0)
    else:
        resumed_completed = int(payload.get("resume_skipped") or 0)
    timeouts = (
        int(payload.get("timeouts") or 0)
        + int(payload.get("resume_timeouts") or 0)
    )
    failures = (
        int(payload.get("failures") or 0)
        + int(payload.get("resume_failures") or 0)
    )
    completed_regions = completed + resumed_completed
    planned_raw = payload.get("planned_searches")
    planned = (
        completed_regions + timeouts + failures
        if planned_raw is None
        else max(0, int(planned_raw))
    )
    fraction = (
        float(completed_regions) / float(planned)
        if planned > 0
        else 0.0
    )
    outcome = point_search_outcome(payload)
    return {
        "planned_searches": planned,
        "completed_regions": completed_regions,
        "incomplete_regions": timeouts + failures,
        "timeout_regions": timeouts,
        "failure_regions": failures,
        "map_back_failures": int(payload.get("map_back_failures") or 0),
        "coverage_fraction": fraction,
        "complete": outcome == "completed",
        "comparable": outcome == "completed",
    }


def prepare_point_search_model(
    db,
    *,
    curve_id,
    minimal_model=False,
    model_prep_timeout=10,
):
    """Prepare one exact search coordinate system for reuse across bounded rounds."""
    row = get_curve(db, curve_id)
    if row is None or not row["a_invariants_json"]:
        raise ValueError(f"curve #{curve_id} has no stored model")

    ainvs = [QQ(str(x)) for x in json.loads(row["a_invariants_json"])]
    E = EllipticCurve(QQ, ainvs)
    search_curve = E
    search_to_stored = lambda P: E(P)
    search_model_label = "stored"
    model_prep_error = None
    if minimal_model:
        try:
            prepared = run_global_minimal_model(
                E.a_invariants(),
                timeout=max(1, int(model_prep_timeout)),
            )
            Em = EllipticCurve(
                QQ,
                [QQ(str(x)) for x in prepared["a_invariants"]],
            )
            phi = Em.isomorphism_to(E)
            search_curve = Em
            search_to_stored = lambda P: phi(P)
            search_model_label = "global_minimal"
        except (ModelPrepTimeout, ModelPrepFailure, Exception) as exc:
            # Minimalization is only a coordinate optimization.  Fix the
            # fallback once so every round in a reused context stays comparable.
            model_prep_error = str(exc)
            search_curve = E
            search_to_stored = lambda P: E(P)
            search_model_label = "stored_exact_fallback"

    search_ainvs = list(search_curve.a_invariants())
    return {
        "curve_id": int(curve_id),
        "stored_curve": E,
        "search_curve": search_curve,
        "search_to_stored": search_to_stored,
        "search_model": search_model_label,
        "model_prep_error": model_prep_error,
        "search_ainvs": search_ainvs,
        "polynomial": completed_square_polynomial(search_ainvs),
        "minimal_model_requested": bool(minimal_model),
    }


def run_auto_point_tier(
    db,
    *,
    curve_id,
    tier,
    heights,
    chart_budget,
    timeout,
    executable=None,
    certificate_timeout=120,
    exact_candidates=32,
    campaign_id=None,
    parameter=None,
    native_only=False,
    minimal_model=False,
    denominator_low=None,
    denominator_high=None,
    model_prep_timeout=10,
    certify_after_search=True,
    pipeline_checkpoint=None,
    retry_policy="manual",
    retry_timeout=None,
    prepared_model=None,
):
    prepared_model_reused = prepared_model is not None
    if prepared_model is None:
        prepared_model = prepare_point_search_model(
            db,
            curve_id=curve_id,
            minimal_model=minimal_model,
            model_prep_timeout=model_prep_timeout,
        )
    elif int(prepared_model.get("curve_id", -1)) != int(curve_id):
        raise ValueError("prepared point-search model belongs to a different curve")

    E = prepared_model["stored_curve"]
    search_curve = prepared_model["search_curve"]
    search_to_stored = prepared_model["search_to_stored"]
    search_model_label = str(prepared_model["search_model"])
    model_prep_error = prepared_model.get("model_prep_error")
    search_ainvs = list(prepared_model["search_ainvs"])
    poly = prepared_model["polynomial"]
    charts = (
        [(QQ(0), QQ(1))]
        if native_only
        else search_charts(db, curve_id, E, tier=tier, budget=chart_budget)
    )

    existing_keys = set()
    for rec in db.execute(
        "SELECT x,y FROM points WHERE curve_id=? AND exact_verified=1",
        (int(curve_id),),
    ).fetchall():
        try:
            existing_keys.add(_point_key(E(QQ(str(rec["x"])), QQ(str(rec["y"])))))
        except Exception:
            continue

    seen = set()
    seen_point_ids = {}
    exact_found = 0
    completed = 0
    skipped = 0
    resume_completed = 0
    resume_timeouts = 0
    resume_failures = 0
    timeouts = 0
    failures = 0
    map_back_failures = 0
    discovery_observations = 0
    planned_searches = 0
    attempted_regions = []
    engine_identities = {}
    print(
        f"[auto:{tier}] model={search_model_label} charts={len(charts)} "
        f"heights={','.join(str(int(h)) for h in heights)} "
        f"denominators={denominator_low or '*'}..{denominator_high or '*'}",
        flush=True,
    )
    if model_prep_error is not None:
        print(
            f"[auto:{tier}] MODEL PREP SKIPPED: {model_prep_error}; "
            f"continuing on exact stored model",
            flush=True,
        )

    for chart_index, (center, scale) in enumerate(charts, 1):
        transformed = affine_cubic_polynomial(poly, scale=scale, center=center)
        for height in heights:
            height = int(height)
            region = _denominator_region(
                height, denominator_low, denominator_high
            )
            if not _denominator_range_possible(height, denominator_low):
                print(
                    f"[auto:{tier}] SKIP empty denominator range "
                    f"center={center} scale={scale} H={height} "
                    f"low={denominator_low}",
                    flush=True,
                )
                continue
            planned_searches += 1
            completed_step = _completed_step(
                db,
                campaign_id=campaign_id,
                parameter=parameter,
                tier=tier,
                center=center,
                scale=scale,
                height=height,
            )
            if completed_step is not None:
                skipped += 1
                prior_status = str(completed_step["status"] or "")
                if prior_status == "completed":
                    resume_completed += 1
                    exact_found += int(completed_step["exact_points"] or 0)
                elif prior_status == "timeout":
                    resume_timeouts += 1
                else:
                    resume_failures += 1
                attempted_regions.append({
                    "chart_index": chart_index,
                    "center": str(center),
                    "scale": str(scale),
                    "height": height,
                    **region,
                    "status": prior_status or "unknown",
                    "resume": True,
                })
                print(
                    f"[auto:{tier}] resume-skip chart={chart_index}/{len(charts)} "
                    f"center={center} scale={scale} H={height} "
                    f"status={prior_status or 'unknown'}",
                    flush=True,
                )
                continue

            pipeline_step = _pipeline_point_attempt(
                db,
                checkpoint=pipeline_checkpoint,
                tier=tier,
                center=center,
                scale=scale,
                height=height,
            )
            skip_pipeline, attempt_timeout, retry_reason = _pipeline_retry_decision(
                pipeline_step,
                retry_policy=retry_policy,
                timeout_seconds=max(1, int(timeout)),
                retry_timeout=retry_timeout,
            )
            if skip_pipeline:
                skipped += 1
                prior_status = str(pipeline_step["status"] or "")
                if prior_status == "completed":
                    resume_completed += 1
                    exact_found += int(pipeline_step["exact_points"] or 0)
                elif prior_status == "timeout":
                    resume_timeouts += 1
                else:
                    resume_failures += 1
                attempted_regions.append({
                    "chart_index": chart_index,
                    "center": str(center),
                    "scale": str(scale),
                    "height": height,
                    **region,
                    "status": prior_status,
                    "resume": True,
                })
                print(
                    f"[auto:{tier}] pipeline-resume-skip "
                    f"chart={chart_index}/{len(charts)} center={center} "
                    f"scale={scale} H={height} status={prior_status} "
                    f"retry_policy={retry_reason}",
                    flush=True,
                )
                continue

            print(
                f"[auto:{tier}] chart={chart_index}/{len(charts)} center={center} "
                f"scale={scale} H={height} timeout={attempt_timeout}s",
                flush=True,
            )
            _record_step(
                db,
                campaign_id=campaign_id,
                parameter=parameter,
                tier=tier,
                center=center,
                scale=scale,
                height=height,
                status="running",
            )
            _record_pipeline_point_attempt(
                db,
                checkpoint=pipeline_checkpoint,
                curve_id=curve_id,
                tier=tier,
                center=center,
                scale=scale,
                height=height,
                denominator_low=denominator_low,
                denominator_high=denominator_high,
                effective_denominator_low=region["effective_denominator_low"],
                effective_denominator_high=region["effective_denominator_high"],
                timeout_seconds=attempt_timeout,
                retry_policy=retry_policy,
                status="running",
            )
            try:
                result = run_ratpoints(
                    transformed,
                    height,
                    executable=executable,
                    timeout=attempt_timeout,
                    denominator_low=denominator_low,
                    denominator_high=denominator_high,
                )
            except RatpointsTimeout as exc:
                timeouts += 1
                engine_provenance = dict(
                    getattr(exc, "engine_provenance", {}) or {}
                )
                _remember_engine_identity(engine_identities, engine_provenance)
                error = f"ratpoints exceeded {int(attempt_timeout)}s"
                _record_step(
                    db,
                    campaign_id=campaign_id,
                    parameter=parameter,
                    tier=tier,
                    center=center,
                    scale=scale,
                    height=height,
                    status="timeout",
                    error=error,
                )
                _record_pipeline_point_attempt(
                    db,
                    checkpoint=pipeline_checkpoint,
                    curve_id=curve_id,
                    tier=tier,
                    center=center,
                    scale=scale,
                    height=height,
                    denominator_low=denominator_low,
                    denominator_high=denominator_high,
                    effective_denominator_low=region["effective_denominator_low"],
                    effective_denominator_high=region["effective_denominator_high"],
                    timeout_seconds=attempt_timeout,
                    retry_policy=retry_policy,
                    status="timeout",
                    runtime=engine_provenance.get("runtime_seconds"),
                    engine=engine_provenance,
                    error=error,
                )
                attempted_regions.append({
                    "chart_index": chart_index,
                    "center": str(center),
                    "scale": str(scale),
                    "height": height,
                    **region,
                    "status": "timeout",
                    "resume": False,
                })
                print(
                    f"[auto:{tier}] TIMEOUT center={center} scale={scale} H={height}",
                    flush=True,
                )
                continue
            except RatpointsFailure as exc:
                failures += 1
                engine_provenance = dict(
                    getattr(exc, "engine_provenance", {}) or {}
                )
                _remember_engine_identity(engine_identities, engine_provenance)
                _record_step(
                    db,
                    campaign_id=campaign_id,
                    parameter=parameter,
                    tier=tier,
                    center=center,
                    scale=scale,
                    height=height,
                    status="error",
                    error=str(exc),
                )
                _record_pipeline_point_attempt(
                    db,
                    checkpoint=pipeline_checkpoint,
                    curve_id=curve_id,
                    tier=tier,
                    center=center,
                    scale=scale,
                    height=height,
                    denominator_low=denominator_low,
                    denominator_high=denominator_high,
                    effective_denominator_low=region["effective_denominator_low"],
                    effective_denominator_high=region["effective_denominator_high"],
                    timeout_seconds=attempt_timeout,
                    retry_policy=retry_policy,
                    status="error",
                    runtime=engine_provenance.get("runtime_seconds"),
                    engine=engine_provenance,
                    error=str(exc),
                )
                attempted_regions.append({
                    "chart_index": chart_index,
                    "center": str(center),
                    "scale": str(scale),
                    "height": height,
                    **region,
                    "status": "error",
                    "resume": False,
                })
                print(
                    f"[auto:{tier}] ERROR center={center} scale={scale} H={height}: {exc}",
                    flush=True,
                )
                continue

            completed += 1
            engine_provenance = dict(result.get("engine_provenance") or {})
            _remember_engine_identity(engine_identities, engine_provenance)
            engine_identity = _compact_engine_identity(engine_provenance)
            step_points = 0
            for hit in result["points"]:
                raw_chart_x = str(getattr(hit, "x", ""))
                raw_chart_w = str(getattr(hit, "y", ""))
                projective = _ratpoint_projective_payload(hit)
                search_ref = (
                    f"auto:{tier}:center={center}:scale={scale}:H={height}"
                )
                observation_common = {
                    "curve_id": curve_id,
                    "source": "auto_search_ratpoints",
                    "tier": str(tier),
                    "search_ref": search_ref,
                    "campaign_id": campaign_id,
                    "parameter": parameter,
                    "chart_index": chart_index,
                    "center": str(center),
                    "scale": str(scale),
                    "height": height,
                    "requested_denominator_low": region["requested_denominator_low"],
                    "requested_denominator_high": region["requested_denominator_high"],
                    "effective_denominator_low": region["effective_denominator_low"],
                    "effective_denominator_high": region["effective_denominator_high"],
                    "chart_x": raw_chart_x,
                    "chart_w": raw_chart_w,
                    "projective": projective,
                    **_point_discovery_pipeline_fields(pipeline_checkpoint),
                }
                try:
                    X = QQ(raw_chart_x)
                    x = QQ(scale) * X + QQ(center)
                    w = QQ(raw_chart_w)
                    y = QQ(str(recover_weierstrass_y(search_ainvs, x, w)))
                    Ps = search_curve(x, y)
                    P = search_to_stored(Ps)
                except Exception as exc:
                    map_back_failures += 1
                    discovery_observations += 1
                    record_point_discovery(
                        db,
                        outcome="map_back_error",
                        exact_verified=False,
                        error_class=exc.__class__.__name__,
                        error=str(exc),
                        metadata={
                            "search_model": search_model_label,
                            "native_only": bool(native_only),
                            "minimal_model": bool(minimal_model),
                            "ratpoints_engine": engine_identity,
                        },
                        **observation_common,
                    )
                    print(
                        f"[auto:{tier}] MAP-BACK ERROR center={center} "
                        f"scale={scale} H={height}: {exc.__class__.__name__}: {exc}",
                        flush=True,
                    )
                    continue
                if P.is_zero():
                    continue
                key = _point_key(P)
                point_id = seen_point_ids.get(key)
                if key not in seen:
                    seen.add(key)
                    is_new = key not in existing_keys
                    if is_new:
                        existing_keys.add(key)
                        exact_found += 1
                        step_points += 1
                    point_row = upsert_point(
                        db,
                        curve_id=curve_id,
                        x=P[0],
                        y=P[1],
                        source="auto_search_ratpoints",
                        role="candidate_extra",
                        exact_verified=True,
                        independence_status="unknown",
                        rigorous_independent=False,
                        search_ref=search_ref,
                        metadata={
                            "tier": str(tier),
                            "center": str(center),
                            "scale": str(scale),
                            "height": height,
                            "denominator_low": denominator_low,
                            "denominator_high": denominator_high,
                            "requested_denominator_low": region["requested_denominator_low"],
                            "requested_denominator_high": region["requested_denominator_high"],
                            "effective_denominator_low": region["effective_denominator_low"],
                            "effective_denominator_high": region["effective_denominator_high"],
                            "chart_x_denominator": int(X.denominator()),
                            "stored_x_denominator": int(QQ(P[0]).denominator()),
                            "native_only": bool(native_only),
                            "minimal_model": bool(minimal_model),
                            "search_model": search_model_label,
                            "auto_search": True,
                        },
                    )
                    point_id = (
                        None if point_row is None else int(point_row["id"])
                    )
                    if point_id is not None:
                        seen_point_ids[key] = point_id
                discovery_observations += 1
                record_point_discovery(
                    db,
                    outcome="mapped",
                    point_id=point_id,
                    stored_x=P[0],
                    stored_y=P[1],
                    exact_verified=True,
                    metadata={
                        "search_model": search_model_label,
                        "native_only": bool(native_only),
                        "minimal_model": bool(minimal_model),
                        "chart_x_denominator": int(X.denominator()),
                        "stored_x_denominator": int(QQ(P[0]).denominator()),
                        "direction_key": list(key),
                        "ratpoints_engine": engine_identity,
                    },
                    **observation_common,
                )
            _record_step(
                db,
                campaign_id=campaign_id,
                parameter=parameter,
                tier=tier,
                center=center,
                scale=scale,
                height=height,
                status="completed",
                exact_points=step_points,
                runtime=result.get("runtime"),
            )
            _record_pipeline_point_attempt(
                db,
                checkpoint=pipeline_checkpoint,
                curve_id=curve_id,
                tier=tier,
                center=center,
                scale=scale,
                height=height,
                denominator_low=denominator_low,
                denominator_high=denominator_high,
                effective_denominator_low=region["effective_denominator_low"],
                effective_denominator_high=region["effective_denominator_high"],
                timeout_seconds=attempt_timeout,
                retry_policy=retry_policy,
                status="completed",
                exact_points=step_points,
                runtime=result.get("runtime"),
                engine=engine_provenance,
            )
            attempted_regions.append({
                "chart_index": chart_index,
                "center": str(center),
                "scale": str(scale),
                "height": height,
                **region,
                "status": "completed",
                "resume": False,
            })

    if certify_after_search:
        cert = certify_ledger_growth(
            db,
            curve_id=curve_id,
            E=E,
            source="auto_search_ratpoints",
            search_ref=f"auto:{tier}:exact",
            certificate_timeout=certificate_timeout,
            max_candidates=exact_candidates,
        )
    else:
        cert = {
            "attempts": 0,
            "growth": 0,
            "rigorous_lower": int(proven_lower(get_curve(db, curve_id))),
            "basis_complete": True,
        }
    print(
        f"[auto:{tier}] exact_points={exact_found} rank_growth={cert['growth']} "
        f"rigorous_lower={cert['rigorous_lower']} skipped={skipped}",
        flush=True,
    )
    payload = {
        "tier": str(tier),
        "charts": [
            {"center": str(center), "scale": str(scale)}
            for center, scale in charts
        ],
        "heights": [int(x) for x in heights],
        "completed_searches": completed,
        "resume_skipped": skipped,
        "timeouts": timeouts,
        "failures": failures,
        "map_back_failures": map_back_failures,
        "discovery_observations": discovery_observations,
        "exact_points": exact_found,
        "certificate_attempts": cert["attempts"],
        "rank_growth": cert["growth"],
        "rigorous_lower": cert["rigorous_lower"],
        "basis_complete": cert["basis_complete"],
        "native_only": bool(native_only),
        "minimal_model": bool(minimal_model),
        "certify_after_search": bool(certify_after_search),
        "search_model": search_model_label,
        "model_prep_error": model_prep_error,
        "prepared_model_reused": bool(prepared_model_reused),
        "denominator_low": denominator_low,
        "denominator_high": denominator_high,
        "planned_searches": planned_searches,
        "resume_completed": resume_completed,
        "resume_timeouts": resume_timeouts,
        "resume_failures": resume_failures,
        "retry_policy": str(retry_policy or "manual"),
        "retry_timeout": (
            None if retry_timeout is None else max(1, int(retry_timeout))
        ),
        "pipeline_checkpointed": isinstance(pipeline_checkpoint, dict),
        "attempted_regions": attempted_regions,
        "ratpoints_engines": list(engine_identities.values()),
    }
    payload["search_outcome"] = point_search_outcome(payload)
    payload["search_coverage"] = point_search_coverage(payload)
    return payload
