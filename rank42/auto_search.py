"""Core Auto Hunter orchestration for high-rank family hunting.

Installed family plugins supply mathematics. Core Auto Hunter owns the durable
campaign: Nagao candidates, exact family-section baselines, adaptive affine
ratpoints charts, exact independence promotion, selective PARI upper bounds,
and resumable per-chart progress.

No eclib-rh search path is used.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from sage.all import QQ, EllipticCurve

from rank42.auto_point_search import certify_ledger_growth, run_auto_point_tier
from rank42.auto_search_policy import (
    auto_search_policy,
    baseline_certificate_timeout,
    classical_covering_should_run,
    discard_fresh_curve_for_retention,
    point_search_mode,
    pointed_quartic_policy,
    pointed_quartic_should_run,
    rigorous_goal_reached,
    tier_chart_budget,
    tier_should_run,
)
from rank42.auto_search_state import (
    campaign_progress,
    create_campaign,
    ensure_auto_search_schema,
    get_campaign,
    get_trial,
    terminal_trial_parameters,
    update_campaign,
    upsert_trial,
)
from rank42.candidates import candidate_rows, get_pool
from rank42.db import (
    connect,
    get_curve,
    log_event,
    now,
    proven_lower,
    update_curve,
)
from rank42.family_loader import load_family
from rank42.plugins import (
    get_plugin,
    get_variant,
    plugin_fingerprints,
    search_presets_for_variant,
    variant_candidate_defaults,
)
from rank42.search_family_baseline import (
    FamilyPointLoadError,
    attach_family_baseline as _attach_family_baseline,
    attach_family_specialization_seed as _attach_family_specialization_seed,
    family_named_points_on_stored_model as _family_named_points_on_stored_model,
    family_points_on_stored_model as _family_points_on_stored_model,
)
from rank42.search_classical_covering import (
    run_classical_covering_escalation as _run_classical_covering_escalation,
)
from rank42.search_materialization import (
    discard_unpromoted_search_curve as _discard_unpromoted_auto_curve,
    materialize_family_curve as _stored_curve,
)
from rank42.search_plugin_geometry import (
    bounded_geometry_options as _bounded_geometry_options,
    geometry_rank_selected as _geometry_rank_selected,
    run_plugin_geometry_target as _run_plugin_geometry_target,
)
from rank42.search_science import (
    research_rank_state as _research_rank_state,
    try_pari_upper as _try_pari_upper,
)
from rank42.pointed_quartic import run_pointed_quartic_escalation
from rank42.rank_evidence import apply_reduced_rank_state, record_rank_evidence
from rank42.torsion import (
    TorsionFailure,
    TorsionTimeout,
    canonical_torsion_label,
    persist_curve_torsion,
    require_torsion_schema,
)


MARKER = "RANK42_AUTO_SEARCH_RESULT="
SEARCH_REVISION = 7


def _research_lower(db, curve_id, *, specialization=False):
    state = _research_rank_state(db, curve_id)
    key = (
        "specialization_rigorous_lower"
        if specialization
        else "rigorous_lower"
    )
    return int(state.get(key) or 0)

DEFAULT_TIERS = (
    {"id": "scout", "charts": 8, "heights": (1000,), "timeout": 3},
    {"id": "structural", "charts": 24, "heights": (10000, 100000), "timeout": 5},
    {"id": "deep", "charts": 64, "heights": (100000, 1000000), "timeout": 15},
    {"id": "priority", "charts": 128, "heights": (1000000, 10000000), "timeout": 60},
)


def parse_args():
    ap = argparse.ArgumentParser(description="Adaptive high-rank Auto Hunter")
    ap.add_argument("--project-root", default=".")
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--campaign-id", type=int)
    ap.add_argument("--plugin", required=True)
    ap.add_argument("--variant")
    ap.add_argument("--target-rank", type=int, default=20)
    ap.add_argument("--torsion-group")
    ap.add_argument("--retention-floor", type=int, default=0)
    ap.add_argument(
        "--stop-on-target",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="stop this provider campaign as soon as the rigorous lower bound reaches the goal",
    )
    ap.add_argument("--pool-size", type=int, default=50)
    ap.add_argument("--deep-keep", type=int, default=12)
    ap.add_argument("--upper-timeout", type=int, default=8)
    ap.add_argument("--final-upper-timeout", type=int, default=30)
    ap.add_argument("--certificate-timeout", type=int, default=120)
    ap.add_argument("--exact-candidates", type=int, default=48)
    ap.add_argument("--ratpoints")
    ap.add_argument("--geometry-first", action=argparse.BooleanOptionalAction, default=False)
    ap.add_argument("--geometry-keep", type=int, default=48)
    ap.add_argument("--geometry-triage-timeout", type=int, default=2)
    ap.add_argument("--geometry-timeout", type=int, default=180)
    return ap.parse_args()


def _csvish(value, default):
    if value is None:
        return str(default)
    if isinstance(value, (list, tuple)):
        return ",".join(str(x) for x in value)
    return str(value)


def _candidate_config(plugin, variant, pool_size):
    values = variant_candidate_defaults(plugin, variant)
    for preset in search_presets_for_variant(plugin, variant):
        if str(preset.get("id") or "") == "scan":
            values.update(dict(preset.get("candidate") or {}))
            break

    top = max(1, min(int(pool_size), int(values.get("top", pool_size))))
    return {
        "a_min": int(values.get("a_min", -1000)),
        "a_max": int(values.get("a_max", 1000)),
        "b_min": max(1, int(values.get("b_min", 1))),
        "b_max": max(1, int(values.get("b_max", 500))),
        "stage_bounds": _csvish(values.get("stage_bounds"), "200,500,1000"),
        "stage_keeps": _csvish(values.get("stage_keeps"), ""),
        "engine": str(values.get("engine", "sieve")),
        "sample_count": max(1, int(values.get("sample_count", 200000))),
        "sample_seed": int(values.get("sample_seed", 42)),
        "top": top,
    }


def _generate_pool(args, db, campaign_id, plugin, variant):
    campaign = get_campaign(db, campaign_id)
    if campaign is not None and campaign["pool_id"]:
        pool = get_pool(db, int(campaign["pool_id"]))
        if pool is not None:
            print(f"[auto] resume pool #{int(pool['id'])} {pool['name']}", flush=True)
            return pool

    config = _candidate_config(plugin, variant, args.pool_size)
    pool_name = f"auto-search-{int(campaign_id)}-{plugin.id}-{variant.id}"
    cmd = [
        sys.executable,
        "-m",
        "rank42.candidate_generate",
        "--project-root",
        str(Path(args.project_root).resolve()),
        "--db",
        str(Path(args.db).resolve()),
        "--plugin",
        plugin.id,
        "--variant",
        variant.id,
        "--pool-name",
        pool_name,
        "--a-min",
        str(config["a_min"]),
        "--a-max",
        str(config["a_max"]),
        "--b-min",
        str(config["b_min"]),
        "--b-max",
        str(config["b_max"]),
        "--stage-bounds",
        config["stage_bounds"],
        "--engine",
        config["engine"],
        "--top",
        str(config["top"]),
        "--corpus-policy",
        "off",
    ]
    if config["stage_keeps"].strip():
        cmd += ["--stage-keeps", config["stage_keeps"]]
    if config["engine"] == "sampled":
        cmd += [
            "--sample-count",
            str(config["sample_count"]),
            "--sample-seed",
            str(config["sample_seed"]),
        ]

    print("[auto] generating Nagao-ranked candidate pool", flush=True)
    rc = subprocess.run(cmd, check=False).returncode
    if rc:
        raise RuntimeError(f"candidate generation failed with exit={rc}")

    pool = db.execute(
        "SELECT * FROM candidate_pools WHERE name=?",
        (pool_name,),
    ).fetchone()
    if pool is None:
        raise RuntimeError("candidate generation completed without a candidate pool")
    update_campaign(
        db,
        campaign_id,
        pool_id=int(pool["id"]),
        candidates_total=int(pool["candidate_count"] or 0),
    )
    return pool


def _is_torsion_filtered_campaign(campaign):
    if campaign is None:
        return False
    return bool(campaign["torsion_group"]) and str(campaign["search_mode"] or "") in {
        "torsion_provider",
        "geometry_torsion",
        "geometry_torsion_provider",
    }


def _preferred_quartic_anchor_vectors(family, *, limit):
    getter = getattr(family, "pointed_quartic_anchor_vectors", None)
    if not callable(getter):
        return ()
    try:
        records = list(getter(limit=max(1, int(limit))) or ())
    except Exception as exc:
        print(
            f"[auto:pointed-quartic] family anchor portfolio unavailable: {exc}",
            flush=True,
        )
        return ()
    if records:
        print(
            f"[auto:pointed-quartic] family supplied {len(records)} "
            "Mordell-Weil anchor vector(s)",
            flush=True,
        )
    return tuple(records)


def _geometry_policy(policy, args):
    """Core geometry defaults layered under any stronger plugin policy."""
    out = dict(policy or {})
    defaults = {
        "pointed_quartic_enabled": True,
        "pointed_quartic_min_rank": 1,
        "pointed_quartic_top_fresh": int(args.geometry_keep),
        "pointed_quartic_anchors": 16,
        "pointed_quartic_pool_size": 160,
        "pointed_quartic_rounds": 1,
        "pointed_quartic_deep_keep": 8,
        "pointed_quartic_timeout": 6,
        "pointed_quartic_reduce_timeout": 15,
        "pointed_quartic_heights": (10000, 100000),
        "classical_covering_enabled": True,
        "classical_covering_engine": "simon_known",
        "classical_covering_min_rank": 0,
        "classical_covering_top_fresh": min(8, int(args.geometry_keep)),
        "classical_covering_timeout": min(90, int(args.geometry_timeout)),
        "classical_covering_lim1": 5,
        "classical_covering_lim3": 80,
    }
    for key, value in defaults.items():
        out.setdefault(key, value)
    # These are core Geometry capabilities, not plugin opt-ins.  Keep
    # them deliberately bounded even when ordinary Auto Hunter has deeper
    # family-specific escalation settings.
    out["pointed_quartic_enabled"] = True
    out["pointed_quartic_min_rank"] = 1
    out["pointed_quartic_top_fresh"] = min(
        int(args.geometry_keep),
        max(1, int(out.get("pointed_quartic_top_fresh", args.geometry_keep))),
    )
    out["pointed_quartic_anchors"] = min(
        16, max(1, int(out.get("pointed_quartic_anchors", 16)))
    )
    out["pointed_quartic_pool_size"] = min(
        160, max(1, int(out.get("pointed_quartic_pool_size", 160)))
    )
    out["pointed_quartic_rounds"] = 1
    out["pointed_quartic_deep_keep"] = min(
        8, max(1, int(out.get("pointed_quartic_deep_keep", 8)))
    )
    out["pointed_quartic_timeout"] = min(
        6, max(1, int(out.get("pointed_quartic_timeout", 6)))
    )
    out["pointed_quartic_reduce_timeout"] = min(
        15, max(0, int(out.get("pointed_quartic_reduce_timeout", 15)))
    )
    out["pointed_quartic_heights"] = (10000, 100000)

    out["classical_covering_enabled"] = True
    out["classical_covering_min_rank"] = 0
    out["classical_covering_top_fresh"] = min(
        8,
        int(args.geometry_keep),
        max(1, int(out.get("classical_covering_top_fresh", 8))),
    )
    out["classical_covering_timeout"] = min(
        90,
        int(args.geometry_timeout),
        max(1, int(out.get("classical_covering_timeout", 90))),
    )
    out["classical_covering_lim3"] = min(
        80, max(1, int(out.get("classical_covering_lim3", 80)))
    )
    return out


def _trial_snapshot(db, curve_id):
    row = get_curve(db, curve_id)
    state = apply_reduced_rank_state(db, curve_id)
    point_count = db.execute(
        "SELECT COUNT(*) AS n FROM points WHERE curve_id=? AND exact_verified=1",
        (int(curve_id),),
    ).fetchone()["n"]
    return {
        "rigorous_lower": int(state["rigorous_lower"]),
        "rigorous_upper": state["rigorous_upper"],
        "exact_rank": state["exact_rank"],
        "exact_points": int(point_count or 0),
        "curve_status": str(row["status"] or ""),
    }


def _existing_seed_candidates(db, family_name):
    """Return retained positive-rank family fibers before fresh Nagao survivors."""
    rows = db.execute(
        """
        SELECT id AS curve_id, parameter, score,
               MAX(COALESCE(exact_rank,0),COALESCE(descent_lower,0),COALESCE(generic_lower,0)) AS rigorous_lower,
               exact_rank
        FROM curves
        WHERE family=?
          AND MAX(COALESCE(exact_rank,0),COALESCE(descent_lower,0),COALESCE(generic_lower,0))>0
        ORDER BY rigorous_lower DESC, COALESCE(score,-1e99) DESC, id DESC
        """,
        (str(family_name),),
    ).fetchall()
    out = []
    for index, row in enumerate(rows, 1):
        if row["exact_rank"] is not None:
            continue
        out.append(
            {
                "id": None,
                "existing_curve_id": int(row["curve_id"]),
                "parameter": str(row["parameter"]),
                "score": float(row["score"] or 0.0),
                "rank_order": 0,
                "seed_order": index,
            }
        )
    return out


def _candidate_value(candidate, key, default=None):
    try:
        value = candidate[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def _trial_metadata(row):
    if row is None:
        return {}
    try:
        return json.loads(row["metadata_json"] or "{}")
    except Exception:
        return {}


def _invalidate_stale_exhaustions(db, campaign_id):
    """Reopen fibers exhausted by an older Auto Hunter geometry revision."""
    rows = db.execute(
        """
        SELECT id,metadata_json FROM auto_search_trials
        WHERE campaign_id=? AND status='exhausted'
        """,
        (int(campaign_id),),
    ).fetchall()
    reopened = 0
    for row in rows:
        try:
            meta = json.loads(row["metadata_json"] or "{}")
        except Exception:
            meta = {}
        if int(meta.get("search_revision") or 0) >= SEARCH_REVISION:
            continue
        db.execute(
            "UPDATE auto_search_trials SET status='queued',updated_at=? WHERE id=?",
            (now(), int(row["id"])),
        )
        reopened += 1
    if reopened:
        db.commit()
        print(
            f"[auto] reopened {reopened} stale exhausted fiber(s) for search revision {SEARCH_REVISION}",
            flush=True,
        )
    return reopened


def _infer_campaign_created_curve(db, campaign_id, curve_id, candidate_id):
    """Conservatively identify a legacy Auto Hunter materialization.

    A candidate-linked curve must have been created after this campaign started
    and must not carry durable manual-import provenance.
    """
    if candidate_id is None:
        return False
    campaign = get_campaign(db, campaign_id)
    curve = get_curve(db, curve_id)
    if campaign is None or curve is None or not campaign["started_at"]:
        return False
    if str(curve["created_at"] or "") < str(campaign["started_at"]):
        return False
    imported = db.execute(
        """
        SELECT 1 FROM events
        WHERE curve_id=? AND message LIKE 'Manual external curve import%'
        LIMIT 1
        """,
        (int(curve_id),),
    ).fetchone()
    return imported is None


def _run_candidate(args, db, campaign_id, plugin, variant, family, candidate, fingerprints):
    parameter = str(_candidate_value(candidate, "parameter"))
    score = float(_candidate_value(candidate, "score", 0.0) or 0.0)
    rank_order = int(_candidate_value(candidate, "rank_order", 0) or 0)
    candidate_id = _candidate_value(candidate, "id")

    prior_trial = get_trial(db, campaign_id, parameter)
    raw_prior_meta = _trial_metadata(prior_trial)
    prior_meta = dict(raw_prior_meta)
    if int(prior_meta.get("search_revision") or 0) < SEARCH_REVISION:
        prior_meta = {}
    curve_id, E, source_E, created_now = _stored_curve(
        db,
        plugin=plugin,
        variant=variant,
        family=family,
        parameter=parameter,
        score=score,
        fingerprints=fingerprints,
    )
    if curve_id is None:
        upsert_trial(
            db,
            campaign_id=campaign_id,
            candidate_id=candidate_id,
            parameter=parameter,
            score=score,
            status="singular",
            metadata={"rank_order": rank_order},
        )
        print(f"[candidate done] singular t={parameter}", flush=True)
        return "singular", None

    created_by_campaign = bool(raw_prior_meta.get("created_by_campaign", False) or created_now)
    if not created_by_campaign:
        created_by_campaign = _infer_campaign_created_curve(
            db, campaign_id, curve_id, candidate_id
        )
    if candidate_id is not None:
        db.execute(
            "UPDATE candidates SET curve_id=?, updated_at=? WHERE id=?",
            (curve_id, now(), int(candidate_id)),
        )
        db.commit()

    if args.geometry_first and rank_order > int(args.geometry_keep):
        upsert_trial(
            db,
            campaign_id=campaign_id,
            candidate_id=candidate_id,
            curve_id=curve_id,
            parameter=parameter,
            score=score,
            status="screened_out",
            tier="nagao-shortlist",
            rigorous_lower=int(proven_lower(get_curve(db, curve_id))),
            metadata={
                "search_revision": SEARCH_REVISION,
                "rank_order": rank_order,
                "created_by_campaign": created_by_campaign,
                "geometry_keep": int(args.geometry_keep),
                "reason": "below geometry shortlist after medium Nagao rescore",
            },
        )
        if created_by_campaign:
            _discard_unpromoted_auto_curve(db, curve_id)
            curve_id = None
        print(
            f"[candidate done] screened out after Nagao shortlist t={parameter} "
            f"rank_order={rank_order} geometry_keep={int(args.geometry_keep)}",
            flush=True,
        )
        return "screened_out", curve_id

    exact_torsion = None
    if args.torsion_group:
        try:
            torsion_data = persist_curve_torsion(
                db, curve_id, E, timeout=30
            )
            exact_torsion = str(torsion_data["torsion_label"])
        except TorsionTimeout as exc:
            upsert_trial(
                db,
                campaign_id=campaign_id,
                candidate_id=candidate_id,
                curve_id=curve_id,
                parameter=parameter,
                score=score,
                status="inconclusive",
                tier="exact-torsion",
                rigorous_lower=int(proven_lower(get_curve(db, curve_id))),
                metadata={
                    "search_revision": SEARCH_REVISION,
                    "rank_order": rank_order,
                    "created_by_campaign": created_by_campaign,
                    "target_torsion": str(args.torsion_group),
                    "reason": "exact_torsion_timeout",
                    "error": str(exc),
                },
            )
            log_event(
                db,
                curve_id,
                "warn",
                "exact torsion timed out; candidate retained for later verification",
            )
            return "inconclusive", curve_id
        except TorsionFailure as exc:
            upsert_trial(
                db,
                campaign_id=campaign_id,
                candidate_id=candidate_id,
                curve_id=curve_id,
                parameter=parameter,
                score=score,
                status="inconclusive",
                tier="exact-torsion",
                rigorous_lower=int(proven_lower(get_curve(db, curve_id))),
                metadata={
                    "search_revision": SEARCH_REVISION,
                    "rank_order": rank_order,
                    "created_by_campaign": created_by_campaign,
                    "target_torsion": str(args.torsion_group),
                    "reason": "exact_torsion_error",
                    "error": str(exc),
                },
            )
            log_event(
                db,
                curve_id,
                "warn",
                "exact torsion failed; candidate retained for later verification",
            )
            return "inconclusive", curve_id
        if exact_torsion != str(args.torsion_group):
            mismatch_meta = {
                "search_revision": SEARCH_REVISION,
                "rank_order": rank_order,
                "created_by_campaign": created_by_campaign,
                "target_torsion": str(args.torsion_group),
                "exact_torsion": exact_torsion,
                "model_a_invariants": [str(x) for x in E.a_invariants()],
            }
            upsert_trial(
                db,
                campaign_id=campaign_id,
                candidate_id=candidate_id,
                curve_id=curve_id,
                parameter=parameter,
                score=score,
                status="torsion_mismatch",
                tier="torsion-filter",
                rigorous_lower=int(proven_lower(get_curve(db, curve_id))),
                metadata=mismatch_meta,
            )
            if created_by_campaign:
                _discard_unpromoted_auto_curve(db, curve_id)
                curve_id = None
            print(
                f"[candidate done] torsion mismatch t={parameter} "
                f"wanted={args.torsion_group} exact={exact_torsion}",
                flush=True,
            )
            return "torsion_mismatch", curve_id

    policy = auto_search_policy(plugin, variant)
    baseline_timeout = baseline_certificate_timeout(policy, args.certificate_timeout)
    baseline = _attach_family_baseline(
        db,
        curve_id,
        family,
        parameter,
        source_E,
        E,
        baseline_timeout,
        args.exact_candidates,
    )
    start = _trial_snapshot(db, curve_id)
    baseline_lower = int(prior_meta.get("baseline_lower", start["rigorous_lower"]))
    novel_points = int(prior_meta.get("novel_points", 0))
    previous_tiers = list(prior_meta.get("tiers") or [])
    print(
        f"\n[auto] candidate #{rank_order} t={parameter} score={score:.6f} "
        f"curve=#{curve_id} baseline_rank>={baseline_lower} "
        f"current_rank>={start['rigorous_lower']} sections={baseline['section_points']}",
        flush=True,
    )

    common_meta = {
        "search_revision": SEARCH_REVISION,
        "rank_order": rank_order,
        "baseline": baseline,
        "baseline_lower": baseline_lower,
        "created_by_campaign": created_by_campaign,
        "model_a_invariants": [str(x) for x in E.a_invariants()],
        "target_torsion": str(args.torsion_group) if args.torsion_group else None,
        "exact_torsion": exact_torsion,
        "retention_floor": int(args.retention_floor),
        "tiers": previous_tiers,
        "geometry": [],
        "novel_points": novel_points,
    }
    upsert_trial(
        db,
        campaign_id=campaign_id,
        candidate_id=candidate_id,
        curve_id=curve_id,
        parameter=parameter,
        score=score,
        status="searching",
        tier=str(prior_trial["tier"] if prior_trial is not None and prior_trial["tier"] else "baseline"),
        rigorous_lower=start["rigorous_lower"],
        rigorous_upper=start["rigorous_upper"],
        exact_rank=start["exact_rank"],
        exact_points=start["exact_points"],
        rank_growth=max(0, int(start["rigorous_lower"]) - baseline_lower),
        metadata=common_meta,
    )

    if start["exact_rank"] is not None:
        upsert_trial(
            db,
            campaign_id=campaign_id,
            candidate_id=candidate_id,
            curve_id=curve_id,
            parameter=parameter,
            score=score,
            status="exact",
            tier="baseline",
            rigorous_lower=start["rigorous_lower"],
            rigorous_upper=start["rigorous_upper"],
            exact_rank=start["exact_rank"],
            exact_points=start["exact_points"],
            rank_growth=max(0, int(start["rigorous_lower"]) - baseline_lower),
            metadata=common_meta,
        )
        if discard_fresh_curve_for_retention(
            created_by_campaign=created_by_campaign,
            retention_floor=args.retention_floor,
            rigorous_lower=start["rigorous_lower"],
            status="exact",
            growth=max(0, int(start["rigorous_lower"]) - baseline_lower),
            exact_rank=start["exact_rank"],
        ):
            _discard_unpromoted_auto_curve(db, curve_id)
            curve_id = None
        print(
            f"[candidate done] exact rank {start['exact_rank']} "
            f"curve=#{curve_id or 'campaign-only'}",
            flush=True,
        )
        return "exact", curve_id

    geometry_results = []
    if (
        args.geometry_first
        and _geometry_rank_selected(rank_order, args.geometry_keep)
        and int(args.geometry_triage_timeout) > 0
        and start["rigorous_lower"] < int(args.target_rank)
    ):
        triage = _try_pari_upper(
            db, curve_id, E, int(args.geometry_triage_timeout), phase="geometry-triage"
        )
        triage_upper = triage.get("rigorous_upper")
        current_lower = int(_trial_snapshot(db, curve_id)["rigorous_lower"])
        if (
            triage_upper is not None
            and current_lower <= int(triage_upper) < int(args.target_rank)
        ):
            final = _trial_snapshot(db, curve_id)
            common_meta["geometry"] = [{
                "scheduler_tier": "geometry-triage",
                "status": "rigorous-upper-below-target",
                "rigorous_upper": int(triage_upper),
            }]
            upsert_trial(
                db,
                campaign_id=campaign_id,
                candidate_id=candidate_id,
                curve_id=curve_id,
                parameter=parameter,
                score=score,
                status="exhausted",
                tier="geometry-triage",
                rigorous_lower=final["rigorous_lower"],
                rigorous_upper=final["rigorous_upper"],
                exact_rank=final["exact_rank"],
                exact_points=final["exact_points"],
                rank_growth=max(0, int(final["rigorous_lower"]) - baseline_lower),
                metadata=common_meta,
            )
            print(
                f"[candidate done] geometry triage rejects t={parameter}: "
                f"upper={int(triage_upper)} target={int(args.target_rank)}",
                flush=True,
            )
            return "exhausted", curve_id

    geometry_policy = _geometry_policy(policy, args) if args.geometry_first else policy

    if args.geometry_first and _geometry_rank_selected(rank_order, args.geometry_keep):
        # Geometry seed: one exact global-minimal integral chart.  This is cheap
        # enough to run broadly and can supply the first free point needed by
        # point-centred quartics and known-point coverings.
        seed = run_auto_point_tier(
            db,
            curve_id=curve_id,
            tier="geometry-seed",
            heights=(1000, 10000),
            chart_budget=1,
            timeout=4,
            executable=args.ratpoints,
            certificate_timeout=args.certificate_timeout,
            exact_candidates=args.exact_candidates,
            campaign_id=campaign_id,
            parameter=parameter,
            native_only=True,
            minimal_model=True,
            denominator_low=1,
            denominator_high=1,
            model_prep_timeout=8,
        )
        seed["scheduler_tier"] = "geometry-seed"
        geometry_results.append(seed)
        novel_points += int(seed.get("exact_points") or 0)
        common_meta["geometry"] = geometry_results
        start = _trial_snapshot(db, curve_id)

    geometry = _run_plugin_geometry_target(
        args,
        db,
        curve_id=curve_id,
        plugin=plugin,
        variant=variant,
        rank_order=rank_order,
        E=E,
    )
    if geometry is not None:
        geometry_results.append(geometry)
        common_meta["geometry"] = geometry_results
        start = _trial_snapshot(db, curve_id)
        upsert_trial(
            db,
            campaign_id=campaign_id,
            candidate_id=candidate_id,
            curve_id=curve_id,
            parameter=parameter,
            score=score,
            status="searching",
            tier="plugin-geometry",
            rigorous_lower=start["rigorous_lower"],
            rigorous_upper=start["rigorous_upper"],
            exact_rank=start["exact_rank"],
            exact_points=start["exact_points"],
            rank_growth=max(0, int(start["rigorous_lower"]) - baseline_lower),
            metadata=common_meta,
        )

    if args.geometry_first and _geometry_rank_selected(rank_order, args.geometry_keep):
        current = _trial_snapshot(db, curve_id)
        geom_growth = max(0, int(current["rigorous_lower"]) - baseline_lower)
        if pointed_quartic_should_run(
            geometry_policy,
            rank_order=rank_order,
            lower=current["rigorous_lower"],
            target_rank=args.target_rank,
            growth=geom_growth,
            created_by_campaign=created_by_campaign,
        ):
            pq_cfg = pointed_quartic_policy(geometry_policy)
            preferred_anchor_vectors = _preferred_quartic_anchor_vectors(
                family, limit=pq_cfg["pool_size"]
            )
            pointed = run_pointed_quartic_escalation(
                db,
                curve_id=curve_id,
                E=E,
                heights=pq_cfg["heights"],
                anchors=pq_cfg["anchors"],
                pool_size=pq_cfg["pool_size"],
                rounds=pq_cfg["rounds"],
                deep_keep=pq_cfg["deep_keep"],
                timeout=pq_cfg["timeout"],
                reduce_timeout=pq_cfg["reduce_timeout"],
                executable=args.ratpoints,
                certificate_timeout=args.certificate_timeout,
                exact_candidates=args.exact_candidates,
                campaign_id=campaign_id,
                parameter=parameter,
                target_rank=args.target_rank,
                preferred_anchor_vectors=preferred_anchor_vectors,
            )
            pointed["scheduler_tier"] = "geometry-pointed-quartic"
            geometry_results.append(pointed)
            novel_points += int(pointed.get("exact_points") or 0)

        current = _trial_snapshot(db, curve_id)
        geom_growth = max(0, int(current["rigorous_lower"]) - baseline_lower)
        if classical_covering_should_run(
            geometry_policy,
            rank_order=rank_order,
            lower=current["rigorous_lower"],
            target_rank=args.target_rank,
            growth=geom_growth,
            created_by_campaign=created_by_campaign,
        ):
            covering = _run_classical_covering_escalation(
                db,
                curve_id=curve_id,
                E=E,
                policy=geometry_policy,
                certificate_timeout=args.certificate_timeout,
                exact_candidates=args.exact_candidates,
            )
            covering["scheduler_tier"] = "geometry-classical-covering"
            geometry_results.append(covering)
            novel_points += int(covering.get("exact_points") or 0)

        common_meta["geometry"] = geometry_results
        common_meta["novel_points"] = novel_points
        start = _trial_snapshot(db, curve_id)
        upsert_trial(
            db,
            campaign_id=campaign_id,
            candidate_id=candidate_id,
            curve_id=curve_id,
            parameter=parameter,
            score=score,
            status="searching",
            tier="core-geometry",
            rigorous_lower=start["rigorous_lower"],
            rigorous_upper=start["rigorous_upper"],
            exact_rank=start["exact_rank"],
            exact_points=start["exact_points"],
            rank_growth=max(0, int(start["rigorous_lower"]) - baseline_lower),
            metadata=common_meta,
        )

    previous_growth = 0
    tier_results = previous_tiers
    completed_tiers = {
        str(rec.get("scheduler_tier") or rec.get("tier"))
        for rec in previous_tiers
        if isinstance(rec, dict) and (rec.get("scheduler_tier") or rec.get("tier"))
    }
    last_tier = (
        "core-geometry"
        if args.geometry_first and geometry_results
        else str(prior_trial["tier"] if prior_trial is not None and prior_trial["tier"] else "baseline")
    )

    direct_affine_enabled = bool(policy.get("direct_affine_enabled", True))
    if not direct_affine_enabled:
        print(
            "[auto] family policy: default affine tier ladder disabled; "
            "using family/covering geometry instead",
            flush=True,
        )

    for tier in DEFAULT_TIERS:
        if not direct_affine_enabled:
            continue
        if args.geometry_first and rank_order > int(args.deep_keep):
            continue
        if tier["id"] in completed_tiers:
            print(f"[auto] resume-skip completed tier={tier['id']} t={parameter}", flush=True)
            continue
        current = _trial_snapshot(db, curve_id)
        growth = max(0, int(current["rigorous_lower"]) - baseline_lower)
        if current["rigorous_lower"] >= int(args.target_rank):
            break
        if not tier_should_run(
            tier["id"],
            rank_order=rank_order,
            deep_keep=args.deep_keep,
            lower=current["rigorous_lower"],
            baseline_lower=baseline_lower,
            target_rank=args.target_rank,
            growth=growth,
            novel_points=novel_points,
            previous_growth=previous_growth,
        ):
            continue

        before_lower = int(current["rigorous_lower"])
        mode = point_search_mode(policy, current["rigorous_lower"])
        effective_tier = f"{tier['id']}:{mode['id_suffix']}"
        print(f"[auto] discovery policy: {mode['description']}", flush=True)
        result = run_auto_point_tier(
            db,
            curve_id=curve_id,
            tier=effective_tier,
            heights=tier["heights"],
            chart_budget=tier_chart_budget(tier, growth=growth),
            timeout=tier["timeout"],
            executable=args.ratpoints,
            certificate_timeout=args.certificate_timeout,
            exact_candidates=args.exact_candidates,
            campaign_id=campaign_id,
            parameter=parameter,
            native_only=mode["native_only"],
            minimal_model=mode["minimal_model"],
            denominator_low=mode["denominator_low"],
            denominator_high=mode["denominator_high"],
        )
        result["scheduler_tier"] = tier["id"]
        result["discovery_policy"] = mode["description"]
        tier_results.append(result)
        last_tier = effective_tier
        novel_points += int(result["exact_points"])
        snap = _trial_snapshot(db, curve_id)
        previous_growth = max(0, int(snap["rigorous_lower"]) - before_lower)
        growth = max(0, int(snap["rigorous_lower"]) - baseline_lower)

        common_meta.update({
            "tiers": tier_results,
            "novel_points": novel_points,
        })
        upsert_trial(
            db,
            campaign_id=campaign_id,
            candidate_id=candidate_id,
            curve_id=curve_id,
            parameter=parameter,
            score=score,
            status="searching",
            tier=last_tier,
            rigorous_lower=snap["rigorous_lower"],
            rigorous_upper=snap["rigorous_upper"],
            exact_rank=snap["exact_rank"],
            exact_points=snap["exact_points"],
            rank_growth=growth,
            metadata=common_meta,
        )

        if snap["exact_rank"] is not None or snap["rigorous_lower"] >= int(args.target_rank):
            break

    final = _trial_snapshot(db, curve_id)
    growth = max(0, int(final["rigorous_lower"]) - baseline_lower)

    if (
        not args.geometry_first
        and "pointed-quartic" not in completed_tiers
        and pointed_quartic_should_run(
            policy,
            rank_order=rank_order,
            lower=final["rigorous_lower"],
            target_rank=args.target_rank,
            growth=growth,
            created_by_campaign=created_by_campaign,
        )
    ):
        pq_cfg = pointed_quartic_policy(policy)
        preferred_anchor_vectors = _preferred_quartic_anchor_vectors(
            family, limit=pq_cfg["pool_size"]
        )
        pointed = run_pointed_quartic_escalation(
            db,
            curve_id=curve_id,
            E=E,
            heights=pq_cfg["heights"],
            anchors=pq_cfg["anchors"],
            pool_size=pq_cfg["pool_size"],
            rounds=pq_cfg["rounds"],
            deep_keep=pq_cfg["deep_keep"],
            timeout=pq_cfg["timeout"],
            reduce_timeout=pq_cfg["reduce_timeout"],
            executable=args.ratpoints,
            certificate_timeout=args.certificate_timeout,
            exact_candidates=args.exact_candidates,
            campaign_id=campaign_id,
            parameter=parameter,
            target_rank=args.target_rank,
            preferred_anchor_vectors=preferred_anchor_vectors,
        )
        tier_results.append(pointed)
        last_tier = "pointed-quartic"
        novel_points += int(pointed.get("exact_points") or 0)
        common_meta.update({
            "tiers": tier_results,
            "novel_points": novel_points,
        })
        final = _trial_snapshot(db, curve_id)
        growth = max(0, int(final["rigorous_lower"]) - baseline_lower)
        upsert_trial(
            db,
            campaign_id=campaign_id,
            candidate_id=candidate_id,
            curve_id=curve_id,
            parameter=parameter,
            score=score,
            status="searching",
            tier=last_tier,
            rigorous_lower=final["rigorous_lower"],
            rigorous_upper=final["rigorous_upper"],
            exact_rank=final["exact_rank"],
            exact_points=final["exact_points"],
            rank_growth=growth,
            metadata=common_meta,
        )

    if (
        not args.geometry_first
        and "classical-covering" not in completed_tiers
        and classical_covering_should_run(
            policy,
            rank_order=rank_order,
            lower=final["rigorous_lower"],
            target_rank=args.target_rank,
            growth=growth,
            created_by_campaign=created_by_campaign,
        )
    ):
        covering = _run_classical_covering_escalation(
            db,
            curve_id=curve_id,
            E=E,
            policy=policy,
            certificate_timeout=args.certificate_timeout,
            exact_candidates=args.exact_candidates,
        )
        tier_results.append(covering)
        last_tier = "classical-covering"
        novel_points += int(covering.get("exact_points") or 0)
        common_meta.update({
            "tiers": tier_results,
            "novel_points": novel_points,
        })
        final = _trial_snapshot(db, curve_id)
        growth = max(0, int(final["rigorous_lower"]) - baseline_lower)
        upsert_trial(
            db,
            campaign_id=campaign_id,
            candidate_id=candidate_id,
            curve_id=curve_id,
            parameter=parameter,
            score=score,
            status="searching",
            tier=last_tier,
            rigorous_lower=final["rigorous_lower"],
            rigorous_upper=final["rigorous_upper"],
            exact_rank=final["exact_rank"],
            exact_points=final["exact_points"],
            rank_growth=growth,
            metadata=common_meta,
        )

    # Record hunting is discovery-first. Spend upper-bound time only on fibers
    # that grew, or on the very top fresh Nagao candidates.
    should_try_upper = (
        final["exact_rank"] is None
        and final["rigorous_lower"] < int(args.target_rank)
        and (growth > 0 or (0 < rank_order <= 3))
    )
    if should_try_upper:
        timeout = args.final_upper_timeout if growth > 0 else args.upper_timeout
        _try_pari_upper(
            db,
            curve_id,
            E,
            timeout,
            phase="promoted-final" if growth > 0 else "top-scout-final",
        )
        final = _trial_snapshot(db, curve_id)
        growth = max(0, int(final["rigorous_lower"]) - baseline_lower)

    if final["exact_rank"] is not None:
        status = "exact"
        update_curve(db, curve_id, status="exact", error=None)
    elif final["rigorous_lower"] >= int(args.target_rank):
        status = "target_hit"
        update_curve(db, curve_id, status="proven_lower", error=None)
        log_event(
            db,
            curve_id,
            "best",
            f"Auto Hunter target hit: rigorous rank >= {final['rigorous_lower']}",
        )
    else:
        status = "exhausted"
        if growth > 0 or not created_by_campaign:
            update_curve(db, curve_id, status="proven_lower", error=None)
        log_event(
            db,
            curve_id,
            "info",
            (
                "Geometry exhausted applicable stages"
                if args.geometry_first
                else "Auto Hunter campaign exhausted applicable tiers"
            )
            + f" through {last_tier}; baseline={baseline_lower}, "
            f"rigorous lower={final['rigorous_lower']}",
        )

    common_meta.update({
        "tiers": tier_results,
        "novel_points": novel_points,
        "exhaustion_scope": (
            "Geometry: medium-Nagao shortlist, rigorous triage, minimal integral "
            "seed, optional plugin geometry, core quartic/covering stages, shortlist affine fallback"
            if args.geometry_first
            else "configured Auto Hunter direct tiers plus eligible pointed-quartic and classical covering escalation"
        ),
        "promoted": bool(growth > 0),
    })
    upsert_trial(
        db,
        campaign_id=campaign_id,
        candidate_id=candidate_id,
        curve_id=curve_id,
        parameter=parameter,
        score=score,
        status=status,
        tier=last_tier,
        rigorous_lower=final["rigorous_lower"],
        rigorous_upper=final["rigorous_upper"],
        exact_rank=final["exact_rank"],
        exact_points=final["exact_points"],
        rank_growth=growth,
        metadata=common_meta,
    )

    # Retention floor is an inventory rule, never a rank claim.  Campaign
    # history keeps the exact result even when a fresh curve row is discarded.
    discard_fresh = discard_fresh_curve_for_retention(
        created_by_campaign=created_by_campaign,
        retention_floor=args.retention_floor,
        rigorous_lower=final["rigorous_lower"],
        status=status,
        growth=growth,
        exact_rank=final["exact_rank"],
    )
    if discard_fresh:
        _discard_unpromoted_auto_curve(db, curve_id)
        curve_id = None

    print(
        f"[candidate done] status={status} curve=#{curve_id or 'campaign-only'} "
        f"baseline={baseline_lower} rank>={final['rigorous_lower']} "
        f"growth={growth} novel_points={novel_points} upper={final['rigorous_upper']}",
        flush=True,
    )
    return status, curve_id


def _update_progress_counters(db, campaign_id, *, best_curve_id=None, best_lower=None):
    progress = campaign_progress(db, campaign_id)
    campaign = get_campaign(db, campaign_id)
    torsion_provider = (
        campaign is not None
        and _is_torsion_filtered_campaign(campaign)
    )
    fields = {
        "candidates_done": progress["done"],
        "exact_count": progress["exact_count"],
        "exhausted_count": progress["exhausted_count"],
        "target_hit_count": progress["target_hit_count"],
        "torsion_mismatch_count": progress["torsion_mismatch_count"],
        "best_lower": (
            int(progress["best_lower"] or 0)
            if torsion_provider
            else max(int(best_lower or 0), int(progress["best_lower"] or 0))
        ),
    }
    if torsion_provider:
        # Torsion-filtered campaign rank state is derived exclusively from
        # matching trials. This also repairs older rows polluted by a mismatch.
        fields["best_curve_id"] = progress["best_curve_id"]
    elif best_curve_id is not None:
        fields["best_curve_id"] = int(best_curve_id)
    update_campaign(db, campaign_id, **fields)
    return progress


def main():
    args = parse_args()
    if args.campaign_id is None:
        raise SystemExit(
            "legacy Auto is resume-only; start new work from the Pipeline Auto page"
        )
    if args.target_rank <= 0 or args.pool_size <= 0 or args.deep_keep < 0:
        raise SystemExit("target rank/pool size must be positive and deep-keep nonnegative")
    if args.geometry_keep < 0 or args.geometry_triage_timeout < 0 or args.geometry_timeout <= 0:
        raise SystemExit("invalid geometry-search limits")
    if args.geometry_first and args.geometry_keep <= 0:
        raise SystemExit("geometry-first search requires geometry-keep > 0")
    if args.retention_floor < 0 or args.retention_floor > args.target_rank:
        raise SystemExit("retention floor must satisfy 0 <= floor <= target rank")
    if args.torsion_group:
        args.torsion_group = canonical_torsion_label(args.torsion_group)

    project = Path(args.project_root).resolve()
    db_path = Path(args.db).resolve()
    db = connect(db_path)
    ensure_auto_search_schema(db)
    if args.torsion_group:
        require_torsion_schema(db)

    plugin = get_plugin(project, args.plugin)
    variant = get_variant(plugin, args.variant)
    family = load_family(variant.family_spec)
    policy = auto_search_policy(plugin, variant)
    fingerprints = plugin_fingerprints(plugin, variant)

    campaign_id = args.campaign_id
    if campaign_id is None:
        campaign_id = create_campaign(
            db,
            plugin_id=plugin.id,
            plugin_version=plugin.version,
            variant_id=variant.id,
            family_spec=variant.family_spec,
            family_name=family.name(),
            target_rank=args.target_rank,
            search_mode=(
                "geometry_torsion_provider"
                if args.geometry_first and args.torsion_group
                else "geometry"
                if args.geometry_first
                else "torsion_provider"
                if args.torsion_group
                else "family"
            ),
            torsion_group=args.torsion_group,
            retention_floor=args.retention_floor,
            stop_on_target=args.stop_on_target,
            config={
                "pool_size": args.pool_size,
                "deep_keep": args.deep_keep,
                "torsion_group": args.torsion_group,
                "retention_floor": args.retention_floor,
                "stop_on_target": bool(args.stop_on_target),
                "tiers": list(DEFAULT_TIERS),
                "geometry_first": bool(args.geometry_first),
                "geometry_keep": int(args.geometry_keep),
                "geometry_triage_timeout": int(args.geometry_triage_timeout),
                "geometry_timeout": int(args.geometry_timeout),
            },
        )
    campaign = get_campaign(db, campaign_id)
    if campaign is None:
        raise SystemExit(f"Auto Hunter campaign #{campaign_id} not found")

    update_campaign(
        db,
        campaign_id,
        status="running",
        started_at=campaign["started_at"] or now(),
        finished_at=None,
        error=None,
    )
    print(
        "RANK HUNTER GEOMETRY EXACT PHASE"
        if args.geometry_first
        else "RANK HUNTER AUTO HUNTER",
        flush=True,
    )
    print("=" * 72, flush=True)
    print(f"campaign = #{campaign_id}", flush=True)
    print(f"family = {plugin.name} / {variant.name}", flush=True)
    print(f"target = rigorous rank >= {int(args.target_rank)}", flush=True)
    if args.torsion_group:
        print(f"torsion target = exact E(Q)_tors {args.torsion_group}", flush=True)
    print(f"retention floor = rigorous rank >= {int(args.retention_floor)}" if args.retention_floor else "retention floor = default promotion policy", flush=True)
    print(f"stop on target = {bool(args.stop_on_target)}", flush=True)
    print(
        "policy = geometry-first shortlist / exact target hit / configured campaign exhausted"
        if args.geometry_first
        else "policy = exact / target hit / configured campaign exhausted",
        flush=True,
    )
    print("point engine = classical policy-driven ratpoints", flush=True)
    print("upper engine = selective PARI", flush=True)
    if policy:
        print(f"auto policy = {json.dumps(policy, sort_keys=True)}", flush=True)

    try:
        pool = _generate_pool(args, db, campaign_id, plugin, variant)
        _invalidate_stale_exhaustions(db, campaign_id)
        fresh_rows = list(candidate_rows(db, int(pool["id"]), limit=args.pool_size))
        seed_rows = _existing_seed_candidates(db, family.name())
        fresh_parameters = {str(row["parameter"]) for row in fresh_rows}
        seed_rows = [row for row in seed_rows if str(row["parameter"]) not in fresh_parameters]
        rows = [*seed_rows, *fresh_rows]

        terminal = terminal_trial_parameters(db, campaign_id)
        update_campaign(db, campaign_id, candidates_total=len(rows))
        print(
            f"[auto] queue seeds={len(seed_rows)} fresh={len(fresh_rows)} "
            f"total={len(rows)} already_done={len(terminal)}",
            flush=True,
        )

        torsion_provider = _is_torsion_filtered_campaign(campaign)
        best_lower = 0 if torsion_provider else int(campaign["best_lower"] or 0)
        best_curve_id = None if torsion_provider else campaign["best_curve_id"]
        initial_progress = _update_progress_counters(
            db,
            campaign_id,
            best_curve_id=best_curve_id,
            best_lower=best_lower,
        )
        if torsion_provider:
            best_lower = int(initial_progress["best_lower"] or 0)
            best_curve_id = initial_progress["best_curve_id"]
        else:
            best_lower = max(best_lower, int(initial_progress["best_lower"] or 0))

        for candidate in rows:
            if rigorous_goal_reached(
                best_lower,
                args.target_rank,
                stop_on_target=args.stop_on_target,
            ):
                print(
                    f"[auto] resume goal already satisfied at rigorous rank >= {best_lower}; "
                    "stopping before the next fiber",
                    flush=True,
                )
                break
            parameter = str(_candidate_value(candidate, "parameter"))
            if parameter in terminal:
                print(f"[auto] resume-skip completed fiber t={parameter}", flush=True)
                continue

            status, curve_id = _run_candidate(
                args,
                db,
                campaign_id,
                plugin,
                variant,
                family,
                candidate,
                fingerprints,
            )
            terminal.add(parameter)

            if curve_id is not None:
                lower = int(proven_lower(get_curve(db, curve_id)))
                if lower > best_lower:
                    best_lower = lower
                    best_curve_id = int(curve_id)

            progress_now = _update_progress_counters(
                db,
                campaign_id,
                best_curve_id=best_curve_id,
                best_lower=best_lower,
            )
            best_lower = max(best_lower, int(progress_now["best_lower"] or 0))
            if rigorous_goal_reached(
                best_lower,
                args.target_rank,
                stop_on_target=args.stop_on_target,
            ):
                print(
                    f"[auto] goal reached: rigorous rank >= {best_lower}; stopping campaign",
                    flush=True,
                )
                break

        progress = _update_progress_counters(
            db,
            campaign_id,
            best_curve_id=best_curve_id,
            best_lower=best_lower,
        )
        achieved = int(max(best_lower, int(progress["best_lower"] or 0))) >= int(args.target_rank)
        update_campaign(
            db,
            campaign_id,
            status="target_hit" if achieved and args.stop_on_target else "completed",
            finished_at=now(),
            best_lower=max(best_lower, int(progress["best_lower"] or 0)),
            best_curve_id=best_curve_id,
        )
        result = {
            "campaign_id": int(campaign_id),
            "candidates": len(rows),
            "completed": int(progress["done"]),
            "exact": int(progress["exact_count"]),
            "exhausted": int(progress["exhausted_count"]),
            "target_hits": int(progress["target_hit_count"]),
            "torsion_mismatches": int(progress["torsion_mismatch_count"]),
            "goal_met": bool(achieved),
            "stopped_on_target": bool(args.stop_on_target and achieved),
            "best_lower": max(best_lower, int(progress["best_lower"] or 0)),
            "best_curve_id": best_curve_id,
        }
        print(MARKER + json.dumps(result, sort_keys=True), flush=True)
    except BaseException as exc:
        latest = get_campaign(db, campaign_id)
        status = str(latest["status"] if latest is not None else "")
        if status not in {"paused", "killed"}:
            update_campaign(
                db,
                campaign_id,
                status="failed",
                error=str(exc),
                finished_at=now(),
            )
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
