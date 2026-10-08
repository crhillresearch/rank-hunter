"""Execute a user-defined Builder search pipeline.

The pipeline engine is intentionally evidence-conservative.  Heuristic stages
only rank/screen work.  Exact point-producing stages write exact points to the
ordinary Rank Hunter ledger.  Rigorous lower bounds rise only through the same
exact independence/certificate paths used elsewhere in core.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from sage.all import QQ, EllipticCurve

from rank42.auto_point_search import (
    certify_ledger_growth,
    point_search_coverage,
    point_search_outcome,
    prepare_point_search_model,
    run_auto_point_tier,
)
from rank42.search_plugin_geometry import (
    run_plugin_geometry_target as _run_plugin_geometry_target,
)
from rank42.search_plugin_family import (
    run_plugin_family_search as _run_plugin_family_search,
)
from rank42.search_classical_covering import (
    run_classical_covering_escalation as _run_classical_covering_escalation,
)
from rank42.search_family_baseline import (
    attach_family_baseline as _attach_family_baseline,
    attach_family_specialization_seed as _attach_family_specialization_seed,
)
from rank42.search_materialization import (
    discard_unpromoted_search_curve as _discard_unpromoted_auto_curve,
    materialize_family_curve as _stored_curve,
)
from rank42.search_science import try_pari_upper as _try_pari_upper
from rank42.analytic_upper import (
    AnalyticUpperFailure,
    AnalyticUpperTimeout,
    run_conditional_analytic_upper,
)
from rank42.brumer_kramer import (
    BrumerKramerFailure,
    BrumerKramerTimeout,
    run_brumer_kramer_bound,
)
from rank42.cassels_tate import (
    CasselsTateFailure,
    CasselsTateTimeout,
    run_cassels_tate_refinement,
    validate_cassels_tate_result,
)
from rank42.candidate_ranking_state import pool_candidate_ranking_state
from rank42.candidates import candidate_rows
from rank42.corpus import apply_candidate_corpus_policy
from rank42.curve_arithmetic_state import publish_curve_root_number
from rank42.curve_research_state import get_curve_research_state
from rank42.classical_descent import (
    ClassicalDescentFailure,
    ClassicalDescentTimeout,
    run_classical_descent,
)
from rank42.constructive_family import (
    forced_bisection_constructor,
    section_height_shell,
    trace_section_constructor,
)
from rank42.descent import DescentFailure, DescentTimeout, run_descent
from rank42.db import (
    connect,
    discard_transient_zero_evidence_curve,
    get_curve,
    get_curve_by_key,
    now,
    proven_lower,
    update_curve,
    upsert_curve,
)
from rank42.family_loader import load_family
from rank42.family_evidence import (
    family_evidence_key,
    record_family_evidence,
    reduce_family_evidence,
)
from rank42.exact_lb import (
    ExactCertificateFailure,
    ExactCertificateTimeout,
    run_exact_certificate,
)
from rank42.specialization_injectivity import (
    SpecializationInjectivityFailure,
    SpecializationInjectivityTimeout,
    run_specialization_injectivity,
)
from rank42.general_hunt_core import sample_unique_pairs, short_curve_nagao_score_details
from rank42.independence_check import certify_stored_independence
from rank42.isogeny_discovery import run_isogeny_degree_discovery
from rank42.isogeny_descent import (
    IsogenyDescentFailure,
    IsogenyDescentTimeout,
    run_isogeny_descent,
    validate_isogeny_descent_result,
)
from rank42.covering_reduce import minimize_reduce_coverings
from rank42.covering_local_height import plan_covering_searches
from rank42.pipeline_catalog import (
    deep_strategy_budget,
    normalize_pipeline,
    stage_spec,
    template_stages,
    validate_pipeline,
)
from rank42.pipeline_state import (
    ensure_pipeline_schema,
    get_pipeline_candidate,
    get_pipeline_run,
    get_pipeline_strategy_step,
    pipeline_candidates,
    pipeline_strategy_steps,
    pipeline_derivations,
    pipeline_run_payload,
    record_pipeline_derivation,
    run_progress,
    update_pipeline_candidate,
    update_pipeline_run,
    upsert_pipeline_candidate,
    upsert_pipeline_strategy_step,
)
from rank42.pipeline_transforms import FANOUT_STAGE_IDS, derive_transform_children
from rank42.point_promotion import promote_rigorous_rank_interval
from rank42.plugins import (
    apply_search_command_features,
    get_plugin,
    load_adapter,
    get_variant,
    plugin_fingerprints,
    search_presets_for_variant,
    variant_candidate_defaults,
)
from rank42.pointed_quartic import run_pointed_quartic_escalation
from rank42.prime_local import (
    bad_prime_fingerprint,
    cache_curve_primes,
    explicit_formula_indicator_score,
    frobenius_persistence_score,
    local_root_number_profile,
    local_sieve_prime_set,
    mestre_nagao_ensemble_score,
    multi_scale_frobenius_score,
    sieve_stored_quartics,
    torsion_mod_p_sieve,
)
from rank42.rank_evidence import apply_reduced_rank_state, record_rank_evidence
from rank42.points import rigorous_witness_basis, upsert_point
from rank42.research_modules import (
    height_lattice_reduction,
    higher_descent_ladder,
    padic_covering_point_search,
    saturation_index_recovery,
    selmer_element_fanout,
)
from rank42.saturation import saturate_curve
from rank42.short_weierstrass import nonsingular_short, seeded_short_curve
from rank42.torsion import (
    TorsionFailure,
    TorsionTimeout,
    canonical_torsion_label,
    persist_curve_torsion,
)
from rank42.torsion_auto_search import discover_providers


MARKER = "RANK42_PIPELINE_RESULT="
POPULATION_STAGE_IDS = frozenset({"select_survivors", "adaptive_ladder"}) | FANOUT_STAGE_IDS
TERMINAL_CANDIDATE_STATES = frozenset({
    "completed", "filtered", "constraint_filtered", "torsion_mismatch",
    "goal_filtered", "funnel_pruned", "transformed", "inconclusive", "error",
})


def parse_args():
    ap = argparse.ArgumentParser(description="Run one saved Builder pipeline")
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--project-root", default=".")
    ap.add_argument("--run-id", required=True, type=int)
    ap.add_argument(
        "--replan-preset",
        default=None,
        help=(
            "Replace the remaining run plan with a current Builder preset while "
            "preserving compatible completed stage results and the candidate pool."
        ),
    )
    return ap.parse_args()


def _safe(value):
    return json.loads(json.dumps(value, default=str))


def _candidate_stage_records(stages):
    out = []
    for rec in stages:
        if stage_spec(rec["id"]).category == "Candidates":
            out.append(rec)
        else:
            break
    return out


def _per_curve_stage_records(stages):
    return [
        (index, rec)
        for index, rec in enumerate(stages, 1)
        if stage_spec(rec["id"]).category != "Candidates"
    ]


def _family_candidate_defaults(plugin, variant):
    values = variant_candidate_defaults(plugin, variant)
    for preset in search_presets_for_variant(plugin, variant):
        if str(preset.get("id") or "") == "scan":
            values.update(dict(preset.get("candidate") or {}))
            break
    return values


def _screening_vectors(stages):
    candidate = [
        rec for rec in _candidate_stage_records(stages)
        if rec["id"] in {"nagao_screen", "nagao_rescore"}
    ]
    bounds = []
    keeps = []
    for rec in candidate:
        cfg = rec["config"]
        bounds.append(max(7, int(cfg.get("prime_bound") or 523)))
        keeps.append(max(1, int(cfg.get("keep") or 250)))
    if not bounds:
        raise ValueError("pipeline has no candidate screening stage")
    for i in range(1, len(bounds)):
        if bounds[i] <= bounds[i - 1]:
            raise ValueError("candidate prime bounds must strictly increase")
        if keeps[i] > keeps[i - 1]:
            raise ValueError("candidate survivor counts must be nonincreasing")
    return bounds, keeps


def _corpus_policy(stages):
    for rec in _candidate_stage_records(stages):
        if rec["id"] == "corpus_filter":
            policy = str(rec["config"].get("policy") or "annotate")
            if policy not in {"annotate", "exclude-known", "known-only", "off"}:
                raise ValueError(f"unsupported corpus policy {policy!r}")
            return policy
    return "off"


def _restrict_torsion_providers(providers, target):
    """Restrict a torsion run to the family/variant that owns a stored pool."""
    wanted_plugin = str((target or {}).get("plugin_id") or "").strip()
    wanted_variant = str((target or {}).get("variant_id") or "").strip()
    if not wanted_plugin:
        return list(providers)
    return [
        provider
        for provider in providers
        if str(provider.get("plugin_id") or "") == wanted_plugin
        and (
            not wanted_variant
            or str(provider.get("variant_id") or "") == wanted_variant
        )
    ]


def _family_candidates(
    db, project_root, run_id, plugin, variant, stages, run_config, target=None
):
    target = dict(target or {})
    source_pool_id = target.get("candidate_pool_id")
    if source_pool_id is not None:
        rows = candidate_rows(
            db,
            int(source_pool_id),
            limit=None,
            offset=0,
            only_unsearched=bool(target.get("only_unsearched", False)),
        )
        wanted_ids = {
            int(x) for x in (target.get("candidate_ids") or [])
        }
        if wanted_ids:
            rows = [row for row in rows if int(row["id"]) in wanted_ids]
        rows, corpus_summary = apply_candidate_corpus_policy(
            project_root,
            plugin,
            variant,
            rows,
            _corpus_policy(stages),
        )
        if corpus_summary["policy"] != "off" and corpus_summary["declared"]:
            print(
                "[pipeline corpus] " + json.dumps(corpus_summary, sort_keys=True),
                flush=True,
            )
        return [
            {
                "parameter": str(row["parameter"]),
                "score": float(row["score"] or 0.0),
                "rank_order": int(row["rank_order"] or index),
                "candidate_id": int(row["id"]),
                "score_provenance": pool_candidate_ranking_state(row)["score_provenance"],
                "corpus_summary": row.get("corpus_summary"),
                "corpus_matches": list(row.get("corpus_matches") or []),
            }
            for index, row in enumerate(rows, 1)
        ]

    bounds, keeps = _screening_vectors(stages)
    defaults = _family_candidate_defaults(plugin, variant)
    generation_overrides = dict(target.get("candidate_generation") or {})
    defaults.update(generation_overrides)
    pool_name = str(
        target.get("output_pool_name")
        or f"pipeline-run-{int(run_id)}-{plugin.id}-{variant.id}"
    ).strip()
    if not pool_name:
        raise ValueError("candidate output pool name must be non-empty")
    requested_top = max(1, int(defaults.get("top") or keeps[-1]))
    # Pipeline screening stages own the actual survivor funnel.  A Family's
    # standalone candidate preset may request a larger final --top than the
    # selected Builder recipe keeps (for example 500 vs Geometry Grinder's
    # final keep of 400).  candidate_generate correctly rejects that impossible
    # contract, so reconcile the pipeline request at this boundary.
    top = min(requested_top, int(keeps[-1]))
    if top != requested_top:
        print(
            f"[pipeline candidates] final top clipped {requested_top}->{top} "
            f"to match final survivor keep={keeps[-1]}",
            flush=True,
        )
    engine = str(defaults.get("engine") or "sieve")
    if engine not in {"sieve", "scalar", "sampled"}:
        raise ValueError(f"unsupported candidate engine {engine!r}")
    existing = db.execute(
        "SELECT * FROM candidate_pools WHERE name=?",
        (pool_name,),
    ).fetchone()
    existing_run_id = None
    if existing is not None:
        try:
            existing_generation = json.loads(existing["generation_json"] or "{}")
        except Exception:
            existing_generation = {}
        existing_run_id = existing_generation.get("pipeline_run_id")
    reusable = (
        existing is not None
        and str(existing["status"] or "") == "ready"
        and int(existing_run_id or -1) == int(run_id)
    )
    if not reusable:
        command = [
            sys.executable,
            "-m",
            "rank42.candidate_generate",
            "--project-root",
            str(Path(project_root).resolve()),
            "--db",
            str(Path(db.execute("PRAGMA database_list").fetchone()[2]).resolve()),
            "--plugin",
            plugin.id,
            "--variant",
            variant.id,
            "--pool-name",
            pool_name,
            "--pipeline-run-id",
            str(int(run_id)),
            "--a-min",
            str(int(defaults.get("a_min", -3000))),
            "--a-max",
            str(int(defaults.get("a_max", 3000))),
            "--b-min",
            str(max(1, int(defaults.get("b_min", 1)))),
            "--b-max",
            str(max(1, int(defaults.get("b_max", 300)))),
            "--stage-bounds",
            ",".join(str(x) for x in bounds),
            "--stage-keeps",
            ",".join(str(x) for x in keeps),
            "--engine",
            engine,
            "--top",
            str(top),
        ]
        chart_id = str(defaults.get("chart_id") or "").strip()
        if chart_id:
            command += ["--chart", chart_id]
        if engine == "sampled":
            command += [
                "--sample-count",
                str(max(1, int(defaults.get("sample_count", 200000)))),
                "--sample-seed",
                str(int(defaults.get("sample_seed", 42))),
            ]
        command += [
            "--corpus-policy",
            _corpus_policy(stages),
        ]
        command, audit = apply_search_command_features(
            project_root,
            db,
            command,
            family_plugin=plugin,
            kind="pipeline_candidate_generate",
            metadata={
                "pipeline_run_id": int(run_id),
                "plugin_id": plugin.id,
                "variant_id": variant.id,
            },
            plugin_ids=(
                None
                if run_config.get("feature_plugin_ids") is None
                else list(run_config.get("feature_plugin_ids") or [])
            ),
        )
        if audit:
            print("[pipeline features] " + json.dumps(audit, sort_keys=True), flush=True)
        print(
            f"[pipeline candidates] {plugin.id}/{variant.id} "
            f"bounds={bounds} keeps={keeps}",
            flush=True,
        )
        rc = subprocess.run(command, check=False).returncode
        if rc:
            raise RuntimeError(f"candidate generation failed with exit={rc}")
        existing = db.execute(
            "SELECT * FROM candidate_pools WHERE name=?",
            (pool_name,),
        ).fetchone()
    if existing is None:
        raise RuntimeError("candidate generator returned without a pool")
    rows = candidate_rows(
        db,
        int(existing["id"]),
        limit=top,
        offset=0,
        only_unsearched=False,
    )
    return [
        {
            "parameter": str(row["parameter"]),
            "score": float(row["score"] or 0.0),
            "rank_order": int(row["rank_order"] or index),
            "candidate_id": int(row["id"]),
            "score_provenance": pool_candidate_ranking_state(row)["score_provenance"],
        }
        for index, row in enumerate(rows, 1)
    ]


def _run_candidate_generation_lane(
    db, *, run, run_config, stages, target, plugin, variant
):
    """Generate one durable candidate pool and stop before curve materialization."""
    candidates = _family_candidates(
        db,
        run_config["project_root"],
        run["id"],
        plugin,
        variant,
        stages,
        run_config,
        target=target,
    )
    pool_name = str(
        target.get("output_pool_name")
        or f"pipeline-run-{int(run['id'])}-{plugin.id}-{variant.id}"
    ).strip()
    pool = db.execute(
        "SELECT * FROM candidate_pools WHERE name=?",
        (pool_name,),
    ).fetchone()
    if pool is None:
        raise RuntimeError("candidate-only Pipeline completed without a durable pool")

    for candidate in candidates:
        upsert_pipeline_candidate(
            db,
            run_id=run["id"],
            provider_key=f"{plugin.id}:{variant.id}",
            parameter=candidate["parameter"],
            score=candidate.get("score"),
            score_provenance=candidate.get("score_provenance"),
            status="completed",
        )
    progress = run_progress(db, run["id"])
    update_pipeline_run(
        db,
        run["id"],
        candidates_total=progress["total"],
        candidates_done=progress["done"],
    )
    return {
        "candidate_pool_id": int(pool["id"]),
        "candidate_pool_name": str(pool["name"]),
        "candidate_count": int(pool["candidate_count"] or len(candidates)),
        "plugin_id": plugin.id,
        "variant_id": variant.id,
    }


def _general_source_candidates(target):
    mode = str(target.get("pool_mode") or "seeded")
    count = max(10, int(target.get("pool_size") or 5000))
    seed = int(target.get("random_seed") or 42)
    out = []
    if mode == "seeded":
        pairs = sample_unique_pairs(
            int(target.get("u_min", 1)),
            int(target.get("u_max", 250)),
            int(target.get("v_min", 1)),
            int(target.get("v_max", 250)),
            count,
            seed=seed,
        )
        seen = set()
        for u, v in pairs:
            if u == 0 or v == 0:
                continue
            A, B, seeds = seeded_short_curve(u, v)
            if not nonsingular_short(A, B) or (A, B) in seen:
                continue
            seen.add((A, B))
            out.append({
                "A": int(A), "B": int(B), "seeds": list(seeds),
                "parameter": f"u={u},v={v};A={A},B={B}",
            })
    else:
        pairs = sample_unique_pairs(
            int(target.get("a_min", -5000)),
            int(target.get("a_max", 5000)),
            int(target.get("b_min", -5000)),
            int(target.get("b_max", 5000)),
            count,
            seed=seed,
        )
        for A, B in pairs:
            if nonsingular_short(A, B):
                out.append({
                    "A": int(A), "B": int(B), "seeds": [],
                    "parameter": f"A={A},B={B}",
                })
    return out


def _general_candidates(target, stages):
    rows = _general_source_candidates(target)
    candidate_stages = [
        rec for rec in _candidate_stage_records(stages)
        if rec["id"] in {"nagao_screen", "nagao_rescore"}
    ]
    for stage_index, rec in enumerate(candidate_stages, 1):
        bound = max(7, int(rec["config"].get("prime_bound") or 523))
        keep = max(1, int(rec["config"].get("keep") or 250))
        rescored = []
        best = None
        for index, row in enumerate(rows, 1):
            score, used, provenance = short_curve_nagao_score_details(
                row["A"], row["B"], bound
            )
            candidate = dict(row)
            candidate["score"] = float(score)
            candidate["nagao_primes"] = int(used)
            candidate["score_provenance"] = provenance
            rescored.append(candidate)
            if best is None or score > best:
                best = score
            if index == 1 or index == len(rows) or index % 250 == 0:
                print(
                    f"[pipeline general Nagao {stage_index}] {index}/{len(rows)} "
                    f"p<{bound} best={best:.6f}",
                    flush=True,
                )
        rescored.sort(
            key=lambda r: (
                -float(r["score"]),
                abs(int(r["A"])) + abs(int(r["B"])),
                int(r["A"]),
                int(r["B"]),
            )
        )
        rows = rescored[:keep]
        print(
            f"[pipeline general Nagao {stage_index}] retained={len(rows)} p<{bound}",
            flush=True,
        )
    for index, row in enumerate(rows, 1):
        row["rank_order"] = index
    return rows


def _materialize_general(db, candidate):
    family_name = "Builder · General"
    parameter = str(candidate["parameter"])
    existing = get_curve_by_key(db, family_name, parameter)
    E = EllipticCurve(
        QQ,
        [0, 0, 0, int(candidate["A"]), int(candidate["B"])],
    )
    created = existing is None
    curve_id = upsert_curve(
        db,
        family=family_name,
        parameter=parameter,
        score=float(candidate.get("score") or 0.0),
        a_invariants_json=json.dumps([str(x) for x in E.a_invariants()]),
        status="pipeline_searching",
    )
    for idx, xy in enumerate(candidate.get("seeds") or []):
        try:
            P = E(QQ(str(xy[0])), QQ(str(xy[1])))
        except Exception:
            continue
        if P.is_zero():
            continue
        upsert_point(
            db,
            curve_id=int(curve_id),
            x=P[0],
            y=P[1],
            source="pipeline_general_seed",
            role="candidate_extra",
            exact_verified=True,
            independence_status="unknown",
            rigorous_independent=False,
            search_ref="pipeline:general-seed",
            metadata={"seed_index": idx},
        )
    return int(curve_id), E, E, created


def _record_breaker_goal_hit(run_config, snapshot):
    """Require consistent rigorous rank state and an exportable witness basis."""
    if not bool((run_config or {}).get("record_breaker_mode", False)):
        return False
    if bool((snapshot or {}).get("rank_inconsistent", False)):
        return False
    target = max(1, int((run_config or {}).get("target_rank") or 1))
    lower = int((snapshot or {}).get("rigorous_lower") or 0)
    witnesses = int((snapshot or {}).get("rigorous_witnesses") or 0)
    return lower >= target and witnesses >= target


def _record_breaker_annotate(result, run_config, snapshot):
    if not _record_breaker_goal_hit(run_config, snapshot):
        return result, False
    payload = dict(result or {})
    payload.update({
        "record_breaker_goal_reached": True,
        "target_rank": max(1, int((run_config or {}).get("target_rank") or 1)),
        "rigorous_lower": int((snapshot or {}).get("rigorous_lower") or 0),
        "proof_boundary": "rigorous_lower_and_witness_basis",
    })
    return payload, True


def _research_lower(db, curve_id):
    """Read the reducer-backed rigorous lower bound for decisions."""
    state = get_curve_research_state(db, int(curve_id))
    return int(state.get("rigorous_lower") or 0)


def _strategy_research_state(db, curve_id, E, *, witness_basis=False):
    """Reducer-backed rank state for deep strategy decisions."""
    state = get_curve_research_state(db, int(curve_id))
    payload = {
        "rigorous_lower": int(state.get("rigorous_lower") or 0),
        "rigorous_upper": state.get("rigorous_upper"),
        "exact_rank": state.get("exact_rank"),
        "rank_inconsistent": bool(state.get("rank_inconsistent", False)),
        "research_state_source": "curve_research_state",
        "witness_basis_complete": None,
        "witness_basis_required": None,
        "rigorous_witnesses": None,
    }
    if witness_basis:
        basis, required, complete = rigorous_witness_basis(
            db, int(curve_id), E
        )
        payload.update({
            "witness_basis_complete": bool(complete),
            "witness_basis_required": int(required),
            "rigorous_witnesses": len(basis),
        })
    return payload


def _snapshot(db, curve_id):
    """Return authoritative reduced rank state plus durable witness count."""
    try:
        state = get_curve_research_state(db, int(curve_id))
    except ValueError:
        return {
            "rigorous_lower": 0,
            "rigorous_upper": None,
            "exact_rank": None,
            "rank_inconsistent": False,
            "rigorous_witnesses": 0,
        }
    ledger = db.execute(
        """SELECT COUNT(*) AS n FROM points
           WHERE curve_id=?
             AND exact_verified=1
             AND rigorous_independent=1
             AND independence_status='rigorous_independent'""",
        (int(curve_id),),
    ).fetchone()
    ledger_count = 0 if ledger is None else int(ledger["n"] or 0)
    return {
        "rigorous_lower": int(state["rigorous_lower"] or 0),
        "rigorous_upper": state["rigorous_upper"],
        "exact_rank": state["exact_rank"],
        "rank_inconsistent": bool(state["rank_inconsistent"]),
        "rigorous_witnesses": int(ledger_count),
    }


def _run_mwrank_selmer_stage(db, *, curve_id, E, config, source):
    timeout = max(1, int(config.get("timeout") or 60))
    first_limit = max(1, int(config.get("first_limit") or 20))
    second_limit = max(1, int(config.get("second_limit") or 10))
    before = _research_lower(db, curve_id)
    try:
        result = run_classical_descent(
            E.a_invariants(),
            [],
            mode="mwrank_selmer",
            timeout=timeout,
            first_limit=first_limit,
            second_limit=second_limit,
        )
    except ClassicalDescentTimeout as exc:
        return {
            "status": "timeout",
            "engine": "mwrank_selmer",
            "error": str(exc),
            "rigorous_lower": before,
            "attempt_complete": True,
            "mathematical_outcome": "timeout",
            "attempt_timeout": timeout,
            "retry_policy": str(config.get("retry_policy") or "manual"),
            "retry_timeout": max(
                1, int(config.get("retry_timeout") or max(300, timeout))
            ),
        }
    except ClassicalDescentFailure as exc:
        return {
            "status": "inconclusive",
            "engine": "mwrank_selmer",
            "failure_class": getattr(exc, "failure_class", None),
            "error": str(exc),
            "rigorous_lower": before,
            "attempt_complete": True,
            "mathematical_outcome": "inconclusive",
            "attempt_timeout": timeout,
            "retry_policy": str(config.get("retry_policy") or "manual"),
            "retry_timeout": max(
                1, int(config.get("retry_timeout") or max(300, timeout))
            ),
        }

    upper = result.get("rigorous_upper")
    if upper is None:
        return {
            "status": "inconclusive",
            "engine": "mwrank_selmer",
            "bound_kind": "mwrank_rank_bound",
            "raw_selmer_rank": result.get("raw_selmer_rank"),
            "rigorous_lower": before,
            "rigorous_upper": None,
            "rank_upper": None,
            "upper_conflict": False,
            "reason": "mwrank_rank_bound_missing",
            "runtime_seconds": result.get("runtime_seconds"),
            "attempt_complete": True,
            "mathematical_outcome": "inconclusive",
            "attempt_timeout": timeout,
            "retry_policy": str(config.get("retry_policy") or "manual"),
            "retry_timeout": max(
                1, int(config.get("retry_timeout") or max(300, timeout))
            ),
        }

    promotion = promote_rigorous_rank_interval(
        db,
        curve_id=int(curve_id),
        model=E.a_invariants(),
        rigorous_lower=None,
        rigorous_upper=int(upper),
        certificate=result,
        engine="eclib_mwrank_selmer_two_descent",
        evidence_type="descent",
        source=str(source),
        options={
            "first_limit": first_limit,
            "second_limit": second_limit,
            "bound_kind": "mwrank_rank_bound",
        },
        metadata={
            "stage": "selmer_bound",
            "raw_selmer_rank": result.get("raw_selmer_rank"),
        },
        minimal_model_a_invariants=result.get("minimal_model_a_invariants"),
        elapsed_seconds=result.get("runtime_seconds"),
    )
    conflict = bool(promotion.get("rank_inconsistent"))
    return {
        "status": "completed" if not conflict else "inconclusive",
        "engine": "mwrank_selmer",
        "bound_kind": "mwrank_rank_bound",
        "raw_selmer_rank": result.get("raw_selmer_rank"),
        "rigorous_lower": int(promotion.get("rigorous_lower") or 0),
        "rigorous_upper": (
            None if conflict else promotion.get("rigorous_upper")
        ),
        "effective_rigorous_upper": promotion.get("rigorous_upper"),
        "rank_upper": int(upper),
        "upper_conflict": conflict,
        "interval_evidence_id": promotion.get("evidence_id"),
        "exact_rank": None if conflict else promotion.get("exact_rank"),
        "runtime_seconds": result.get("runtime_seconds"),
        "attempt_complete": True,
        "mathematical_outcome": (
            "inconclusive" if conflict else "completed"
        ),
        "attempt_timeout": timeout,
        "retry_policy": str(config.get("retry_policy") or "manual"),
        "retry_timeout": max(
            1, int(config.get("retry_timeout") or max(300, timeout))
        ),
    }


def _prior_stage_config(run, stage_index, wanted_stage_id):
    latest = None
    for index, rec in enumerate(normalize_pipeline(run.get("stages") or []), 1):
        if index >= int(stage_index):
            break
        if rec["id"] == wanted_stage_id:
            latest = dict(rec.get("config") or {})
    return latest



def _candidate_stage_history(
    db,
    *,
    run_id,
    before_stage_index,
    candidate_id=None,
    curve_id=None,
):
    if candidate_id is not None:
        row = db.execute(
            """SELECT stage_results_json
               FROM search_pipeline_candidates
               WHERE run_id=? AND id=?""",
            (int(run_id), int(candidate_id)),
        ).fetchone()
    elif curve_id is not None:
        # Compatibility path for non-Constructive historical consumers.
        # Constructive artifact chains must always pass candidate_id.
        row = db.execute(
            """SELECT stage_results_json
               FROM search_pipeline_candidates
               WHERE run_id=? AND curve_id=?
               ORDER BY id LIMIT 1""",
            (int(run_id), int(curve_id)),
        ).fetchone()
    else:
        raise ValueError("candidate stage history requires candidate_id or curve_id")
    if row is None:
        return []
    try:
        payload = json.loads(row["stage_results_json"] or "{}")
    except Exception:
        payload = {}
    out = []
    for key, entry in payload.items():
        try:
            index = int(key)
        except Exception:
            continue
        if index >= int(before_stage_index) or not isinstance(entry, dict):
            continue
        result = entry.get("result")
        if not isinstance(result, dict):
            continue
        out.append((index, str(entry.get("stage_id") or ""), result))
    out.sort(key=lambda rec: rec[0])
    return out


def _latest_prior_stage_result(
    db,
    *,
    run_id,
    before_stage_index,
    stage_id,
    candidate_id=None,
    curve_id=None,
):
    latest = None
    for _index, sid, result in _candidate_stage_history(
        db,
        run_id=run_id,
        candidate_id=candidate_id,
        curve_id=curve_id,
        before_stage_index=before_stage_index,
    ):
        if sid == stage_id:
            latest = result
    return latest


def _constructive_prior_stage_result(
    db,
    *,
    run_id,
    context,
    before_stage_index,
    stage_id,
):
    """Return prior Constructive state only from this exact Pipeline candidate.

    Scientific curve identity is intentionally insufficient here: multiple
    derivation paths may converge on one curve while carrying different
    symbolic construction artifacts.
    """
    candidate_id = context.get("pipeline_candidate_id")
    if candidate_id is None:
        return None
    return _latest_prior_stage_result(
        db,
        run_id=run_id,
        candidate_id=int(candidate_id),
        before_stage_index=before_stage_index,
        stage_id=stage_id,
    )


def _prior_denominator_yields(db, *, run_id, curve_id, before_stage_index):
    records = []
    for index, sid, result in _candidate_stage_history(
        db, run_id=run_id, curve_id=curve_id, before_stage_index=before_stage_index,
    ):
        if sid == "denominator_band":
            outcome = point_search_outcome(result)
            coverage = result.get("search_coverage")
            if not isinstance(coverage, dict):
                coverage = point_search_coverage(result)
            records.append({
                "stage_index": index,
                "band": [
                    int(result.get("denominator_low") or 1),
                    int(result.get("denominator_high") or 1),
                ],
                "exact_points": int(result.get("exact_points") or 0),
                "rank_growth": int(result.get("rank_growth") or 0),
                "search_outcome": outcome,
                "search_coverage": coverage,
                "comparable": outcome == "completed",
            })
        elif sid == "adaptive_ladder":
            for round_rec in result.get("rounds") or []:
                search = round_rec.get("search")
                if not isinstance(search, dict):
                    continue
                band = round_rec.get("band") or [1, 1]
                outcome = point_search_outcome(search)
                coverage = search.get("search_coverage")
                if not isinstance(coverage, dict):
                    coverage = point_search_coverage(search)
                records.append({
                    "stage_index": index,
                    "round": int(round_rec.get("round") or 0),
                    "band": [int(band[0]), int(band[1])],
                    "exact_points": int(search.get("exact_points") or 0),
                    "rank_growth": int(search.get("rank_growth") or 0),
                    "search_outcome": outcome,
                    "search_coverage": coverage,
                    "comparable": outcome == "completed",
                })
    return records



def _run_covering_selmer_branches(
    db,
    *,
    curve_id,
    E,
    run_id,
    stage_index,
    config,
    certificate_timeout,
    exact_candidates,
):
    engines = [str(x) for x in (config.get("engines") or [])]
    timeout = max(1, int(config.get("timeout") or 60))
    basis, required_basis, basis_complete = rigorous_witness_basis(db, curve_id, E)
    before = _research_lower(db, curve_id)
    branches = []
    total_exact_points = 0
    best_upper = None

    for engine in engines:
        if engine == "simon_known" and not basis_complete:
            branches.append({
                "engine": engine,
                "status": "skipped-incomplete-basis",
                "known_basis": len(basis),
                "required_basis": int(required_basis),
            })
            continue

        known = basis if basis_complete else []
        try:
            result = run_classical_descent(
                E.a_invariants(),
                known,
                mode=engine,
                timeout=timeout,
                first_limit=int(config.get("first_limit") or 20),
                second_limit=int(config.get("second_limit") or 10),
                n_aux=int(config.get("n_aux") or 33),
                lim1=int(config.get("lim1") or 5),
                lim3=int(config.get("lim3") or 80),
                limbigprime=(
                    int(config.get("simon_limbigprime"))
                    if config.get("simon_limbigprime") is not None
                    else 0
                ),
            )
        except ClassicalDescentTimeout as exc:
            branches.append({
                "engine": engine,
                "status": "timeout",
                "error": str(exc),
            })
            continue
        except ClassicalDescentFailure as exc:
            branches.append({
                "engine": engine,
                "status": "error",
                "error": str(exc),
            })
            continue

        upper = result.get("rigorous_upper")
        upper_conflict = False
        persisted_upper = None
        interval_evidence_id = None
        if upper is not None:
            engine_name = {
                "simon_known": "simon_two_descent_known_points",
                "mwrank_selmer": "eclib_mwrank_selmer_two_descent",
                "mwrank_coverings": "eclib_mwrank_full_two_descent",
            }.get(engine, engine)
            promotion = promote_rigorous_rank_interval(
                db,
                curve_id=int(curve_id),
                model=E.a_invariants(),
                rigorous_lower=None,
                rigorous_upper=int(upper),
                certificate=result,
                engine=engine_name,
                evidence_type="descent",
                source="builder_covering_selmer_branch",
                points_found=list(result.get("points") or []),
                options={
                    "pipeline_run_id": int(run_id),
                    "stage_index": int(stage_index),
                    "branch_engine": engine,
                    "known_basis_size": len(known),
                    "simon_limbigprime": (
                        int(config.get("simon_limbigprime"))
                        if engine == "simon_known"
                        and config.get("simon_limbigprime") is not None
                        else (0 if engine == "simon_known" else None)
                    ),
                    "upper_bound_rigorous": bool(
                        result.get("upper_bound_rigorous", upper is not None)
                    ),
                    "reported_upper": result.get("reported_upper"),
                },
                metadata={"stage": "covering_selmer_fanout"},
                minimal_model_a_invariants=result.get(
                    "minimal_model_a_invariants"
                ),
                elapsed_seconds=result.get("runtime_seconds"),
            )
            upper_conflict = bool(promotion.get("rank_inconsistent"))
            interval_evidence_id = promotion.get("evidence_id")
            persisted_upper = (
                None if upper_conflict else promotion.get("rigorous_upper")
            )
            if persisted_upper is not None:
                best_upper = (
                    int(persisted_upper)
                    if best_upper is None
                    else min(best_upper, int(persisted_upper))
                )

        branch_points = 0
        seen = set()
        for xy in result.get("points") or []:
            try:
                P = E(QQ(str(xy[0])), QQ(str(xy[1])))
            except Exception:
                continue
            if P.is_zero():
                continue
            Q = -P
            key = min(
                (str(P[0]), str(P[1])),
                (str(Q[0]), str(Q[1])),
            )
            if key in seen:
                continue
            seen.add(key)
            upsert_point(
                db,
                curve_id=curve_id,
                x=P[0],
                y=P[1],
                source="pipeline_covering_selmer_branch",
                role="candidate_extra",
                exact_verified=True,
                independence_status="unknown",
                rigorous_independent=False,
                search_ref=(
                    f"pipeline:{run_id}:covering-selmer:"
                    f"{stage_index}:{engine}"
                ),
                metadata={
                    "engine": engine,
                    "runtime_seconds": result.get("runtime_seconds"),
                    "rigorous_upper": persisted_upper,
                },
            )
            branch_points += 1

        total_exact_points += branch_points
        branches.append({
            "engine": engine,
            "status": "completed",
            "rigorous_upper": persisted_upper,
            "effective_rigorous_upper": (
                None if upper is None else promotion.get("rigorous_upper")
            ),
            "interval_evidence_id": interval_evidence_id,
            "reported_upper": result.get("reported_upper"),
            "upper_bound_rigorous": bool(
                result.get("upper_bound_rigorous", upper is not None)
            ),
            "upper_bound_scope": result.get("upper_bound_scope"),
            "deprecated_engine": bool(result.get("deprecated_engine", False)),
            "upper_conflict": bool(upper_conflict),
            "descent_lower_reported": result.get("descent_lower"),
            "exact_points": branch_points,
            "runtime_seconds": result.get("runtime_seconds"),
        })

    cert = certify_stored_independence(
        db,
        curve_id=curve_id,
        E=E,
        source="pipeline_covering_selmer_branch",
        search_ref=f"pipeline:{run_id}:covering-selmer:{stage_index}:exact",
        certificate_timeout=max(1, int(certificate_timeout)),
        max_candidates=max(1, int(exact_candidates)),
    )
    after = _research_lower(db, curve_id)

    statuses = [str(branch.get("status") or "inconclusive") for branch in branches]
    completed_branches = sum(status == "completed" for status in statuses)
    timeout_branches = sum(status == "timeout" for status in statuses)
    error_branches = sum(status == "error" for status in statuses)
    skipped_branches = sum(
        status.startswith("skipped") for status in statuses
    )
    if not branches:
        branch_status = "inconclusive"
    elif completed_branches == len(branches):
        branch_status = "completed"
    elif completed_branches:
        branch_status = "partial"
    elif timeout_branches == len(branches):
        branch_status = "timeout"
    elif error_branches == len(branches):
        branch_status = "error"
    else:
        branch_status = "inconclusive"

    certification_status = str(cert.get("status") or "inconclusive")
    if branch_status == "completed":
        outer_status = (
            "completed"
            if certification_status == "completed"
            else "partial"
        )
    elif branch_status == "partial":
        outer_status = "partial"
    elif branch_status in {"timeout", "error", "inconclusive"}:
        outer_status = branch_status
    else:
        outer_status = "inconclusive"

    return {
        "status": outer_status,
        "branches": branches,
        "engines": engines,
        "branch_status": branch_status,
        "branch_coverage": {
            "planned": len(engines),
            "attempted": len(branches),
            "completed": completed_branches,
            "timeouts": timeout_branches,
            "errors": error_branches,
            "skipped": skipped_branches,
        },
        "exact_points": total_exact_points,
        "rank_growth": max(0, after - before),
        "rigorous_lower": after,
        "best_rigorous_upper": best_upper,
        "certification_status": certification_status,
        "certificate_attempts": cert.get("exact_attempts", 0),
        "certificate_outcomes": cert.get("attempt_outcomes", []),
        "certificate_reason": cert.get("reason"),
        "basis_complete": cert.get("basis_complete", True),
        "timeouts_are_inconclusive": True,
    }



def _persist_rescue_upper(
    db,
    *,
    curve_id,
    E,
    upper,
    engine,
    source,
    options,
    certificate,
    elapsed_seconds=None,
):
    if upper is None:
        return None
    upper = int(upper)
    lower = int(proven_lower(get_curve(db, int(curve_id))))
    if upper < lower:
        return None
    promotion = promote_rigorous_rank_interval(
        db,
        curve_id=int(curve_id),
        model=E.a_invariants(),
        rigorous_lower=None,
        rigorous_upper=upper,
        certificate=certificate,
        engine=str(engine),
        evidence_type="rank_bounds",
        source=str(source),
        options=dict(options or {}),
        elapsed_seconds=elapsed_seconds,
    )
    if promotion.get("rank_inconsistent"):
        return None
    return promotion.get("rigorous_upper")


def _isogenous_pari_upper_rescue(db, *, context, config):
    """Try quick rigorous PARI bounds on exact Q-isogenous models.

    Prime-degree isogeny enumeration is performed outside Pipeline under a
    core-owned per-degree hard timeout. Rational isogeny preserves
    Mordell-Weil rank, so a rigorous upper obtained on an exact codomain is
    valid for the parent. No lower bound is inherited.
    """
    curve_id = int(context["curve_id"])
    E = context["E"]
    degrees = [
        int(x) for x in (config.get("isogeny_degrees") or [2, 3, 5, 7, 11, 13])
        if int(x) > 1
    ]
    max_models = max(1, int(config.get("isogeny_max_models") or 6))
    discovery_timeout = max(
        1, int(config.get("isogeny_discovery_timeout") or 15)
    )
    timeout = max(1, int(config.get("isogeny_pari_timeout") or 3))
    lower = int(proven_lower(get_curve(db, curve_id)))
    attempts = []
    degree_outcomes = []
    seen = set()
    tested = 0

    for degree_index, degree in enumerate(degrees):
        if tested >= max_models:
            degree_outcomes.extend({
                "degree": int(rest),
                "status": "skipped",
                "reason": "max_models_reached",
                "timeout_seconds": discovery_timeout,
                "discovered_edges": 0,
            } for rest in degrees[degree_index:])
            break

        discovery = run_isogeny_degree_discovery(
            E.a_invariants(),
            degree,
            [],
            timeout=discovery_timeout,
        )
        discovery_status = str(discovery.get("status") or "error")
        degree_outcome = {
            "degree": int(degree),
            "status": discovery_status,
            "reason": discovery.get("reason"),
            "error": discovery.get("error"),
            "timeout_seconds": discovery_timeout,
            "runtime_seconds": discovery.get("runtime_seconds"),
            "worker_exit_code": discovery.get("worker_exit_code"),
            "discovered_edges": len(discovery.get("children") or []),
            "models_tested": 0,
        }
        if discovery_status != "completed":
            degree_outcomes.append(degree_outcome)
            continue

        tested_this_degree = 0
        for branch in discovery.get("children") or []:
            if tested >= max_models:
                break
            edge_index = int(branch.get("edge_index") or (tested_this_degree + 1))
            try:
                raw_child = EllipticCurve(
                    QQ,
                    [QQ(str(x)) for x in branch["codomain_a_invariants"]],
                )
                try:
                    child = raw_child.global_minimal_model()
                except Exception:
                    child = raw_child.minimal_model()
                fingerprint = tuple(str(x) for x in child.a_invariants())
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
            except Exception as exc:
                attempts.append({
                    "degree": degree,
                    "edge_index": edge_index,
                    "status": "model_error",
                    "error": repr(exc),
                })
                continue
            tested += 1
            tested_this_degree += 1
            try:
                result = run_descent(
                    {
                        "mode": "quick_pari",
                        "a_invariants": [str(x) for x in child.a_invariants()],
                    },
                    timeout=timeout,
                )
            except DescentTimeout as exc:
                attempts.append({
                    "degree": degree,
                    "edge_index": edge_index,
                    "status": "timeout",
                    "error": str(exc),
                })
                continue
            except DescentFailure as exc:
                attempts.append({
                    "degree": degree,
                    "edge_index": edge_index,
                    "status": "error",
                    "failure_class": getattr(exc, "failure_class", None),
                    "error": str(exc),
                })
                continue

            upper = result.get("upper")
            accepted = None
            if upper is not None and int(upper) >= lower:
                accepted = _persist_rescue_upper(
                    db,
                    curve_id=curve_id,
                    E=E,
                    upper=int(upper),
                    engine="pari_isogenous_model",
                    source="builder_upper_bound_rescue_ladder",
                    options={
                        "isogeny_degree": degree,
                        "edge_index": edge_index,
                        "exact_rational_isogeny": True,
                        "rank_over_Q_is_invariant": True,
                        "isogenous_model_a_invariants": list(fingerprint),
                    },
                    certificate=result,
                    elapsed_seconds=result.get("_runtime_seconds"),
                )
            attempts.append({
                "degree": degree,
                "edge_index": edge_index,
                "status": "completed",
                "rigorous_upper": accepted,
                "reported_upper": upper,
            })
            if accepted is not None:
                degree_outcome["models_tested"] = tested_this_degree
                degree_outcomes.append(degree_outcome)
                return {
                    "status": "completed",
                    "rigorous_upper": int(accepted),
                    "models_tested": tested,
                    "attempts": attempts,
                    "degree_outcomes": degree_outcomes,
                    "isogeny_discovery_hard_isolated": True,
                }
        degree_outcome["models_tested"] = tested_this_degree
        degree_outcomes.append(degree_outcome)

    return {
        "status": "inconclusive",
        "rigorous_upper": None,
        "models_tested": tested,
        "attempts": attempts,
        "degree_outcomes": degree_outcomes,
        "isogeny_discovery_hard_isolated": True,
    }


def _run_upper_bound_rescue_ladder(
    db, *, run, run_config, target, context, config, stage_index
):
    """Bounded upper-bound fallbacks; every failure/timeout is inconclusive."""
    curve_id = int(context["curve_id"])
    initial = _snapshot(db, curve_id)
    initial_upper = initial.get("rigorous_upper")
    lower = int(initial.get("rigorous_lower") or 0)
    steps = []

    if initial_upper is not None:
        return {
            "status": "completed",
            "rigorous_lower": lower,
            "rigorous_upper": int(initial_upper),
            "already_had_upper": True,
            "steps": steps,
        }

    def current_upper():
        return _snapshot(db, curve_id).get("rigorous_upper")

    def run_step(stage_id, stage_config):
        started = time.monotonic()
        try:
            result, _terminal, _should_stop = _stage_result(
                stage_id,
                db=db,
                run=run,
                run_config=run_config,
                target=target,
                context=context,
                config=stage_config,
                stage_index=stage_index,
            )
        except Exception as exc:
            result = {"status": "error", "error": repr(exc)}
        steps.append({
            "stage_id": stage_id,
            "elapsed_seconds": time.monotonic() - started,
            "result": _safe(result),
            "rigorous_upper_after": current_upper(),
        })
        return result

    if bool(config.get("pari_enabled", True)):
        run_step("pari_upper_gate", {
            "timeout": int(config.get("pari_timeout") or 3),
            "eliminate_below_goal": False,
        })
        if current_upper() is not None:
            return {
                "status": "completed",
                "rigorous_lower": lower,
                "rigorous_upper": int(current_upper()),
                "winner": "pari_upper_gate",
                "steps": steps,
            }

    if bool(config.get("isogeny_pari_enabled", True)):
        started = time.monotonic()
        iso = _isogenous_pari_upper_rescue(db, context=context, config=config)
        steps.append({
            "stage_id": "isogenous_pari_upper",
            "elapsed_seconds": time.monotonic() - started,
            "result": _safe(iso),
            "rigorous_upper_after": current_upper(),
        })
        if current_upper() is not None:
            return {
                "status": "completed",
                "rigorous_lower": lower,
                "rigorous_upper": int(current_upper()),
                "winner": "isogenous_pari_upper",
                "steps": steps,
            }

    if bool(config.get("selmer_enabled", True)):
        run_step("selmer_bound", {
            "timeout": int(config.get("selmer_timeout") or 10),
            "first_limit": int(config.get("selmer_first_limit") or 12),
            "second_limit": int(config.get("selmer_second_limit") or 6),
        })
        if current_upper() is not None:
            return {
                "status": "completed",
                "rigorous_lower": lower,
                "rigorous_upper": int(current_upper()),
                "winner": "selmer_bound",
                "steps": steps,
            }

    basis, _required, basis_complete = rigorous_witness_basis(
        db, curve_id, context["E"]
    )
    if basis_complete and basis and bool(config.get("known_basis_coverings", True)):
        run_step("simon_covering", {
            "timeout": int(config.get("simon_timeout") or 12),
            "lim1": int(config.get("simon_lim1") or 5),
            "lim3": int(config.get("simon_lim3") or 40),
            "limbigprime": int(config.get("simon_limbigprime") or 0),
            "exact_candidates": int(config.get("exact_candidates") or 64),
        })
        if current_upper() is not None:
            return {
                "status": "completed",
                "rigorous_lower": int(proven_lower(get_curve(db, curve_id))),
                "rigorous_upper": int(current_upper()),
                "winner": "simon_covering",
                "steps": steps,
            }

        run_step("mwrank_covering", {
            "timeout": int(config.get("covering_timeout") or 12),
            "first_limit": int(config.get("covering_first_limit") or 12),
            "second_limit": int(config.get("covering_second_limit") or 6),
            "exact_candidates": int(config.get("exact_candidates") or 64),
        })
        if current_upper() is not None:
            return {
                "status": "completed",
                "rigorous_lower": int(proven_lower(get_curve(db, curve_id))),
                "rigorous_upper": int(current_upper()),
                "winner": "mwrank_covering",
                "steps": steps,
            }

    if bool(config.get("higher_descent_hooks", True)):
        levels = [
            int(x) for x in (config.get("higher_levels") or [4, 8, 12])
            if int(x) > 2
        ]
        if levels:
            run_step("higher_descent_ladder", {
                "levels": levels,
                "timeout": int(config.get("higher_timeout") or 20),
                "stop_on_growth": False,
                "certificate_timeout": int(
                    config.get("certificate_timeout")
                    or run_config.get("certificate_timeout")
                    or 120
                ),
                "exact_candidates": int(config.get("exact_candidates") or 64),
            })
            if current_upper() is not None:
                return {
                    "status": "completed",
                    "rigorous_lower": int(proven_lower(get_curve(db, curve_id))),
                    "rigorous_upper": int(current_upper()),
                    "winner": "higher_descent_ladder",
                    "steps": steps,
                }

    return {
        "status": "inconclusive",
        "rigorous_lower": int(proven_lower(get_curve(db, curve_id))),
        "rigorous_upper": None,
        "steps": steps,
        "timeouts_are_inconclusive": True,
        "candidate_may_continue": True,
    }



def _runtime_hunt_capabilities(db, context):
    """Discover optional family/research capabilities without family IDs.

    Core behavior is capability-driven.  Plugins may expose hooks, but they do
    not need to duplicate manifest flags for these optional Builder branches.
    """
    caps = {"exact_curve", "core_point_search", "core_prime_sieve"}
    family = context.get("family")
    if callable(getattr(family, "generic_section_points", None)):
        caps.add("family_sections")

    plugin = context.get("plugin")
    adapter = None
    if plugin is not None:
        try:
            adapter = load_adapter(plugin)
        except Exception:
            adapter = None
        caps.update(str(x) for x in getattr(plugin, "capabilities", ()) or ())

    hook_caps = {
        "derive_pipeline_coverings": "covering_hook",
        "run_pipeline_higher_descent": "higher_descent_hook",
        "run_pipeline_padic_covering_search": "padic_covering_hook",
        "derive_pipeline_transform": "pipeline_transform_hook",
    }
    for hook_name, cap in hook_caps.items():
        if adapter is not None and adapter.optional_hook(hook_name) is not None:
            caps.add(cap)

    curve_id = context.get("curve_id")
    if curve_id is not None:
        try:
            row = db.execute(
                "SELECT 1 FROM coverings WHERE curve_id=? LIMIT 1",
                (int(curve_id),),
            ).fetchone()
        except Exception:
            row = None
        if row is not None:
            caps.add("stored_coverings")

    return frozenset(caps)


def _append_skipped_strategy_step(steps, *, stage_id, reason, rigorous_lower):
    steps.append({
        "stage_id": str(stage_id),
        "status": "skipped",
        "elapsed_seconds": 0.0,
        "rigorous_lower": int(rigorous_lower),
        "result": {
            "status": "skipped",
            "reason": str(reason),
            "capability_driven": True,
        },
    })


def _aggregate_strategy_coverage(steps):
    """Reduce nested strategy execution without erasing incomplete coverage."""
    normalized = []
    for step in steps:
        raw = str(step.get("status") or "inconclusive")
        if raw == "completed":
            status = "completed"
        elif raw == "partial":
            status = "partial"
        elif raw == "timeout":
            status = "timeout"
        elif raw == "error":
            status = "error"
        elif raw in {"unsupported", "skipped"}:
            status = "unsupported"
        else:
            status = "inconclusive"
        normalized.append(status)

    counts = {
        name: normalized.count(name)
        for name in (
            "completed",
            "partial",
            "timeout",
            "error",
            "inconclusive",
            "unsupported",
        )
    }
    all_steps_completed = bool(normalized) and all(
        status == "completed" for status in normalized
    )
    if all_steps_completed:
        outer_status = "completed"
    elif counts["partial"] or (
        counts["completed"] and len(normalized) > counts["completed"]
    ):
        outer_status = "partial"
    elif counts["error"]:
        outer_status = "error"
    elif counts["timeout"]:
        outer_status = "timeout"
    elif counts["inconclusive"]:
        outer_status = "inconclusive"
    elif counts["unsupported"]:
        outer_status = "unsupported"
    else:
        outer_status = "inconclusive"

    return {
        "status": outer_status,
        "step_count": len(normalized),
        "status_counts": counts,
        "all_steps_completed": all_steps_completed,
        "incomplete_steps": len(normalized) - counts["completed"],
    }


def _strategy_occurrence_id(run, context, stage_index, strategy_id):
    candidate_id = context.get("pipeline_candidate_id")
    token = f"candidate:{int(candidate_id)}" if candidate_id is not None else f"parameter:{context.get('parameter')}"
    return f"pipeline:{int(run['id'])}:{token}:stage:{int(stage_index)}:{strategy_id}"


def _run_large_height_generator_hunt(
    db, *, run, run_config, target, context, config, stage_index
):
    """Adaptive exact/rigorous search for one or more hidden large-height generators.

    Search-producing routes run before subgroup cleanup/proof support.  In a
    record hunt, saturation and lattice reduction are late rescue tools rather
    than prerequisites for actually looking for another point.
    """
    curve_id = int(context["curve_id"])
    E = context["E"]
    initial_state = _strategy_research_state(
        db, curve_id, E, witness_basis=False
    )
    initial_lower = int(initial_state["rigorous_lower"])
    target_rank = int(run_config.get("target_rank") or 1)
    stop_on_growth = bool(config.get("stop_on_growth", True))
    wall_budget = max(1, int(config.get("wall_timeout") or 1800))
    retry_policy = str(config.get("retry_policy") or "automatic")
    strategy_id = str(config.get("_strategy_id") or "large_height_generator_hunt")
    occurrence_id = _strategy_occurrence_id(run, context, stage_index, strategy_id)
    candidate_id = context.get("pipeline_candidate_id")
    durable = candidate_id is not None
    prior_rows = (
        pipeline_strategy_steps(
            db, run["id"], candidate_id=int(candidate_id),
            stage_index=stage_index, occurrence_id=occurrence_id,
        ) if durable else []
    )
    prior_elapsed = sum(
        float(row["elapsed_seconds"] or 0.0)
        for row in prior_rows if str(row["step_kind"]) in {"substep", "band"}
    )
    strategy_started = time.monotonic()
    steps = []
    step_counter = 0
    capabilities = _runtime_hunt_capabilities(db, context)
    print(
        "[generator hunt] capabilities=" + ",".join(sorted(capabilities)),
        flush=True,
    )

    def current_research(*, witness_basis=False):
        return _strategy_research_state(
            db, curve_id, E, witness_basis=witness_basis
        )

    def current_lower():
        return int(current_research()["rigorous_lower"])

    def stop_state():
        record_mode = bool(run_config.get("record_breaker_mode", False))
        snap = current_research(witness_basis=record_mode)
        lower = int(snap.get("rigorous_lower") or 0)
        if record_mode:
            witness_count = int(snap.get("rigorous_witnesses") or 0)
            witness_requirement_met = bool(
                snap.get("witness_basis_complete")
                and witness_count >= target_rank
            )
            goal_reached = bool(
                not snap.get("rank_inconsistent")
                and lower >= target_rank
                and witness_requirement_met
            )
        else:
            witness_count = 0
            witness_requirement_met = True
            goal_reached = lower >= target_rank
        growth_stop_reached = bool(
            stop_on_growth and lower > initial_lower
        )
        if goal_reached:
            reason = "rank_goal"
        elif growth_stop_reached:
            reason = "certified_growth"
        else:
            reason = "none"
        return {
            "stop_reason": reason,
            "goal_reached": bool(goal_reached),
            "growth_stop_reached": growth_stop_reached,
            "witness_requirement_met": witness_requirement_met,
            "rigorous_witnesses": witness_count,
        }

    def done():
        state = stop_state()
        return bool(state["goal_reached"] or state["growth_stop_reached"])

    def wall_used():
        return prior_elapsed + (time.monotonic() - strategy_started)

    def persist_round_rows(step_index, step_key, stage_id, result):
        if not durable or not isinstance(result, dict):
            return
        for offset, rec in enumerate(result.get("rounds") or [], 1):
            if not isinstance(rec, dict):
                continue
            number = int(rec.get("round") or offset)
            status = str(rec.get("status") or (rec.get("search") or {}).get("status") or "completed")
            upsert_pipeline_strategy_step(
                db, run_id=run["id"], candidate_id=int(candidate_id), curve_id=curve_id,
                stage_index=stage_index, strategy_id=strategy_id, occurrence_id=occurrence_id,
                step_index=step_index * 1000 + number,
                step_key=f"{step_key}:round:{number}", step_kind="round",
                nested_stage_id=stage_id, status=status,
                wall_budget_seconds=wall_budget, retry_policy=retry_policy, result=rec,
            )

    def run_step(stage_id, stage_config, *, step_key=None, step_kind="substep"):
        nonlocal step_counter
        step_counter += 1
        step_key = str(step_key or stage_id)
        if durable:
            prior = get_pipeline_strategy_step(
                db, run_id=run["id"], candidate_id=int(candidate_id),
                stage_index=stage_index, occurrence_id=occurrence_id, step_key=step_key,
            )
            if prior is not None and str(prior["status"]) in {"completed", "skipped", "unsupported"}:
                try:
                    prior_result = json.loads(prior["result_json"] or "{}")
                except Exception:
                    prior_result = {}
                steps.append({
                    "stage_id": stage_id, "step_key": step_key, "status": str(prior["status"]),
                    "elapsed_seconds": float(prior["elapsed_seconds"] or 0.0),
                    "rigorous_lower": current_lower(), "result": prior_result,
                    "resumed_from_checkpoint": True,
                })
                return None, False
        remaining = wall_budget - wall_used()
        if remaining <= 0:
            result = {"status": "timeout", "reason": "strategy_wall_budget_exhausted_before_substep", "resume_token": step_key}
            if durable:
                upsert_pipeline_strategy_step(
                    db, run_id=run["id"], candidate_id=int(candidate_id), curve_id=curve_id,
                    stage_index=stage_index, strategy_id=strategy_id, occurrence_id=occurrence_id,
                    step_index=step_counter, step_key=step_key, step_kind=step_kind,
                    nested_stage_id=stage_id, status="timeout",
                    wall_budget_seconds=wall_budget, retry_policy=retry_policy, result=result,
                )
            steps.append({"stage_id": stage_id, "step_key": step_key, "status": "timeout", "elapsed_seconds": 0.0, "rigorous_lower": current_lower(), "result": result})
            return None, False
        effective = dict(stage_config or {})
        if effective.get("timeout") is not None:
            effective["timeout"] = max(1, min(int(effective["timeout"]), int(max(1, remaining))))
        if durable:
            upsert_pipeline_strategy_step(
                db, run_id=run["id"], candidate_id=int(candidate_id), curve_id=curve_id,
                stage_index=stage_index, strategy_id=strategy_id, occurrence_id=occurrence_id,
                step_index=step_counter, step_key=step_key, step_kind=step_kind,
                nested_stage_id=stage_id, status="running",
                wall_budget_seconds=wall_budget, retry_policy=retry_policy, result={},
            )
        started = time.monotonic()
        try:
            result, terminal, should_stop = _stage_result(
                stage_id, db=db, run=run, run_config=run_config, target=target,
                context=context, config=effective, stage_index=stage_index,
            )
        except Exception as exc:
            result = {"status": "error", "error": repr(exc)}
            terminal = None
            should_stop = False
        elapsed = time.monotonic() - started
        status = str(result.get("status") if isinstance(result, dict) else "inconclusive")
        if durable:
            upsert_pipeline_strategy_step(
                db, run_id=run["id"], candidate_id=int(candidate_id), curve_id=curve_id,
                stage_index=stage_index, strategy_id=strategy_id, occurrence_id=occurrence_id,
                step_index=step_counter, step_key=step_key, step_kind=step_kind,
                nested_stage_id=stage_id, status=status, elapsed_seconds=elapsed,
                wall_budget_seconds=wall_budget, retry_policy=retry_policy,
                result=_safe(result), error=(result.get("error") if isinstance(result, dict) else None),
            )
            persist_round_rows(step_counter, step_key, stage_id, result)
        steps.append({
            "stage_id": stage_id, "step_key": step_key, "status": status,
            "elapsed_seconds": elapsed, "rigorous_lower": current_lower(),
            "result": _safe(result), "resumed_from_checkpoint": False,
        })
        return terminal, should_stop

    def skip_step(stage_id, reason):
        nonlocal step_counter
        step_counter += 1
        payload = {"status": "skipped", "reason": str(reason), "capability_driven": True}
        if durable and get_pipeline_strategy_step(
            db, run_id=run["id"], candidate_id=int(candidate_id),
            stage_index=stage_index, occurrence_id=occurrence_id, step_key=stage_id,
        ) is None:
            upsert_pipeline_strategy_step(
                db, run_id=run["id"], candidate_id=int(candidate_id), curve_id=curve_id,
                stage_index=stage_index, strategy_id=strategy_id, occurrence_id=occurrence_id,
                step_index=step_counter, step_key=stage_id, step_kind="substep",
                nested_stage_id=stage_id, status="skipped",
                wall_budget_seconds=wall_budget, retry_policy=retry_policy, result=payload,
            )
        steps.append({"stage_id": stage_id, "step_key": stage_id, "status": "skipped", "elapsed_seconds": 0.0, "rigorous_lower": current_lower(), "result": payload})

    def strategy_runtime_metadata():
        resume_token = next((rec.get("step_key") for rec in steps if str(rec.get("status")) not in {"completed", "skipped", "unsupported"}), None)
        used = wall_used()
        research = current_research(
            witness_basis=bool(run_config.get("record_breaker_mode", False))
        )
        return {
            "strategy_occurrence_id": occurrence_id,
            "strategy_wall_budget_seconds": wall_budget,
            "strategy_wall_used_seconds": used,
            "strategy_wall_remaining_seconds": max(0.0, wall_budget - used),
            "strategy_wall_budget_exhausted": used >= wall_budget,
            "strategy_retry_policy": retry_policy,
            "strategy_resume_token": resume_token,
            "strategy_checkpointed": durable,
            "strategy_budget_estimate": deep_strategy_budget(config),
            "point_checkpoint_scope": "chart_height",
            "authoritative_rigorous_lower": int(research.get("rigorous_lower") or 0),
            "authoritative_rigorous_upper": research.get("rigorous_upper"),
            "authoritative_exact_rank": research.get("exact_rank"),
            "authoritative_rank_inconsistent": bool(research.get("rank_inconsistent", False)),
            "authoritative_witness_basis_complete": research.get("witness_basis_complete"),
            "research_state_source": research.get("research_state_source"),
        }

    shared_cert = int(config.get("certificate_timeout") or 180)
    shared_exact = int(config.get("exact_candidates") or 128)

    # Cheap/structured engines first. Level 2 is a core engine; levels above 2
    # require a real plugin hook. Skip unsupported optional branches entirely.
    descent_levels = list(config.get("descent_levels") or [2, 4, 8, 12])
    if 2 in descent_levels or "higher_descent_hook" in capabilities:
        run_step("higher_descent_ladder", {
            "levels": descent_levels,
            "timeout": int(config.get("descent_timeout") or 120),
            "first_limit": 20,
            "second_limit": 10,
            "stop_on_growth": False,
            "certificate_timeout": shared_cert,
            "exact_candidates": shared_exact,
        })
    else:
        skip_step("higher_descent_ladder", "no higher-descent capability for this family")
    if done():
        coverage = _aggregate_strategy_coverage(steps)
        return {
            "status": coverage["status"],
            "steps": steps,
            "rank_growth": current_lower() - initial_lower,
            "rigorous_lower": current_lower(),
            "stopped_after_growth": stop_state()["growth_stop_reached"],
            "stopped_at_rank_goal": stop_state()["goal_reached"],
            **stop_state(),
            "strategy_coverage": coverage,
            "budget_exhausted": False,
            "exhausted_strategy_budget": False,
            "timeouts_are_inconclusive": True,
            **strategy_runtime_metadata(),
        }

    if (
        "covering_hook" in capabilities
        or "stored_coverings" in capabilities
    ):
        run_step("selmer_element_fanout", {
            "max_coverings": int(config.get("max_coverings") or 24),
            "height": int(config.get("covering_height") or 1000000),
            "timeout": int(config.get("covering_timeout") or 60),
            "one_point": False,
            "certificate_timeout": shared_cert,
            "exact_candidates": shared_exact,
        })
    else:
        skip_step("selmer_element_fanout", "no covering hook or stored coverings")
    if done():
        coverage = _aggregate_strategy_coverage(steps)
        return {
            "status": coverage["status"],
            "steps": steps,
            "rank_growth": current_lower() - initial_lower,
            "rigorous_lower": current_lower(),
            "stopped_after_growth": stop_state()["growth_stop_reached"],
            "stopped_at_rank_goal": stop_state()["goal_reached"],
            **stop_state(),
            "strategy_coverage": coverage,
            "budget_exhausted": False,
            "exhausted_strategy_budget": False,
            "timeouts_are_inconclusive": True,
            **strategy_runtime_metadata(),
        }

    if bool(config.get("padic_enabled", True)) and "padic_covering_hook" in capabilities:
        run_step("padic_covering_search", {
            "prime": int(config.get("padic_prime") or 2),
            "precision": int(config.get("padic_precision") or 60),
            "timeout": int(config.get("padic_timeout") or 180),
            "certificate_timeout": shared_cert,
            "exact_candidates": shared_exact,
        })
        if done():
            coverage = _aggregate_strategy_coverage(steps)
            return {
                "status": coverage["status"],
                "steps": steps,
                "rank_growth": current_lower() - initial_lower,
                "rigorous_lower": current_lower(),
                "stopped_after_growth": stop_state()["growth_stop_reached"],
                "stopped_at_rank_goal": stop_state()["goal_reached"],
                **stop_state(),
                "strategy_coverage": coverage,
                "budget_exhausted": False,
                "exhausted_strategy_budget": False,
                "timeouts_are_inconclusive": True,
                **strategy_runtime_metadata(),
            }
    elif bool(config.get("padic_enabled", True)):
        skip_step("padic_covering_search", "no p-adic covering capability for this family")

    # Actual generator hunting comes before saturation/index cleanup.
    if current_lower() > 0:
        run_step("mw_growth_loop", {
            "heights": list(config.get("mw_growth_heights") or [10000, 100000, 1000000]),
            "anchors": int(config.get("mw_growth_anchors") or 32),
            "pool_size": int(config.get("mw_growth_pool") or 320),
            "max_rounds": int(config.get("mw_growth_rounds") or 6),
            "deep_keep": int(config.get("mw_growth_deep_keep") or 12),
            "timeout": int(config.get("mw_growth_timeout") or 8),
            "reduce_timeout": int(config.get("mw_growth_reduce_timeout") or 20),
            "certificate_timeout": shared_cert,
            "exact_candidates": shared_exact,
        })
        if done():
            coverage = _aggregate_strategy_coverage(steps)
            return {
                "status": coverage["status"],
                "steps": steps,
                "rank_growth": current_lower() - initial_lower,
                "rigorous_lower": current_lower(),
                "stopped_after_growth": stop_state()["growth_stop_reached"],
                "stopped_at_rank_goal": stop_state()["goal_reached"],
                **stop_state(),
                "strategy_coverage": coverage,
                "budget_exhausted": False,
                "exhausted_strategy_budget": False,
                "timeouts_are_inconclusive": True,
                **strategy_runtime_metadata(),
            }

    for low, high in config.get("denominator_bands") or [
        [2, 100], [101, 1000], [1001, 10000], [10001, 100000]
    ]:
        run_step("denominator_band", {
            "denominator_low": int(low),
            "denominator_high": int(high),
            "charts": int(config.get("denominator_charts") or 8),
            "heights": list(config.get("denominator_heights") or [100000, 1000000]),
            "timeout": int(config.get("denominator_timeout") or 12),
            "certificate_timeout": shared_cert,
            "exact_candidates": shared_exact,
        }, step_key=f"denominator_band:{int(low)}:{int(high)}", step_kind="band")
        if done():
            break

    if done():
        final_lower = current_lower()
        coverage = _aggregate_strategy_coverage(steps)
        return {
            "status": coverage["status"],
            "steps": steps,
            "rank_growth": max(0, final_lower - initial_lower),
            "rigorous_lower": final_lower,
            "stopped_after_growth": stop_state()["growth_stop_reached"],
            "stopped_at_rank_goal": stop_state()["goal_reached"],
            **stop_state(),
            "strategy_coverage": coverage,
            "budget_exhausted": False,
            "exhausted_strategy_budget": False,
            "timeouts_are_inconclusive": True,
            **strategy_runtime_metadata(),
        }

    # Late rescue: improve/recover the known subgroup only after point-producing
    # searches have exhausted their configured budget.
    if current_lower() > 0 and bool(config.get("late_saturation_enabled", True)):
        run_step("full_saturation_index_recovery", {
            "prime_bounds": list(config.get("saturation_bounds") or [7, 31]),
            "timeout": int(config.get("saturation_timeout") or 20),
            "stop_on_unit_index": bool(config.get("stop_on_unit_index", True)),
            "certificate_timeout": shared_cert,
            "exact_candidates": shared_exact,
        })
        if not done() and bool(config.get("late_lattice_enabled", True)):
            run_step("height_lattice_reduction", {
                "precision_bits": int(config.get("lattice_precision_bits") or 256),
                "certificate_timeout": shared_cert,
                "exact_candidates": shared_exact,
            })

    final_lower = current_lower()
    coverage = _aggregate_strategy_coverage(steps)
    budget_exhausted = bool(
        coverage["all_steps_completed"]
        and final_lower == initial_lower
        and final_lower < target_rank
    )
    return {
        "status": coverage["status"],
        "steps": steps,
        "rank_growth": max(0, final_lower - initial_lower),
        "rigorous_lower": final_lower,
        "stopped_after_growth": stop_state()["growth_stop_reached"],
        "stopped_at_rank_goal": stop_state()["goal_reached"],
        **stop_state(),
        "strategy_coverage": coverage,
        "budget_exhausted": budget_exhausted,
        "exhausted_strategy_budget": budget_exhausted,
        "timeouts_are_inconclusive": True,
        **strategy_runtime_metadata(),
    }


def _stage_result(stage_id, *, db, run, run_config, target, context, config, stage_index):
    curve_id = int(context["curve_id"])
    E = context["E"]
    parameter = context["parameter"]
    target_rank = int(run_config.get("target_rank") or 1)
    ratpoints = run_config.get("ratpoints") or None

    if stage_id == "prime_table_cache":
        # Legacy saved pipelines may carry include_all_bad=True from the old
        # default. Do not eagerly factor huge discriminants unless the user has
        # explicitly opted into the new eager_bad_primes flag.
        eager_bad = bool(config.get("eager_bad_primes", False))
        result = cache_curve_primes(
            db,
            curve_id=curve_id,
            E=E,
            prime_bound=int(config.get("prime_bound") or 2000),
            include_all_bad=bool(eager_bad and config.get("include_all_bad", True)),
            bad_prime_timeout=int(config.get("bad_prime_timeout") or 20),
        )
        result["bad_prime_mode"] = "eager" if eager_bad else "deferred"
        return _safe(result), None, False

    if stage_id == "multi_scale_frobenius":
        result = multi_scale_frobenius_score(
            db,
            curve_id=curve_id,
            E=E,
            bounds=[int(x) for x in config.get("bounds") or [523, 1979, 5000]],
        )
        return _safe(result), None, False

    if stage_id == "mestre_nagao_ensemble":
        result = mestre_nagao_ensemble_score(
            db, curve_id=curve_id, E=E,
            prime_bound=int(config.get("prime_bound") or 5000),
        )
        return _safe(result), None, False

    if stage_id == "frobenius_persistence":
        result = frobenius_persistence_score(
            db, curve_id=curve_id, E=E,
            bounds=[int(x) for x in config.get("bounds") or [523, 1979, 5000, 10000]],
        )
        return _safe(result), None, False

    if stage_id == "explicit_formula_indicator":
        result = explicit_formula_indicator_score(
            db, curve_id=curve_id, E=E,
            prime_bound=int(config.get("prime_bound") or 5000),
            max_prime_power=int(config.get("max_prime_power") or 4),
        )
        return _safe(result), None, False

    if stage_id == "mestre_bober_analytic_upper":
        options = {
            "method": "sage_analytic_rank_upper_bound",
            "max_delta": float(config.get("max_delta") or 1.5),
            "adaptive": bool(config.get("adaptive", True)),
            "root_number": "compute",
            "ncpus": int(config.get("ncpus") or 1),
            "timeout": int(config.get("timeout") or 60),
        }
        assumptions = [
            "Generalized Riemann Hypothesis for L(E,s)"
        ]
        model = [str(x) for x in E.a_invariants()]
        try:
            attempt = run_conditional_analytic_upper(
                E,
                timeout=options["timeout"],
                max_delta=options["max_delta"],
                adaptive=options["adaptive"],
                ncpus=options["ncpus"],
            )
        except AnalyticUpperTimeout as exc:
            evidence_id = record_rank_evidence(
                db,
                curve_id=curve_id,
                model=model,
                data={
                    "engine": "sage.mestre_bober_zero_sum",
                    "evidence_type": "conditional_analytic_upper_attempt",
                    "status": "timeout",
                    "rigorous": False,
                    "rigorous_lower": None,
                    "rigorous_upper": None,
                    "exact_rank": None,
                    "conditional_analytic_upper": None,
                    "numerical_rank_signal": None,
                    "assumptions": assumptions,
                    "points_found": [],
                    "options": options,
                    "elapsed_seconds": getattr(exc, "runtime", None),
                    "stderr_summary": str(exc),
                },
            )
            state = get_curve_research_state(db, curve_id)
            return _safe({
                "status": "timeout",
                "reason": "conditional_analytic_upper_timeout",
                "evidence_id": evidence_id,
                "attempt_conditional_analytic_upper": None,
                "conditional_analytic_upper": state.get(
                    "conditional_analytic_upper"
                ),
                "rigorous_upper": state.get("rigorous_upper"),
                "exact_rank": state.get("exact_rank"),
                "assumptions": assumptions,
                "options": options,
            }), None, False
        except AnalyticUpperFailure as exc:
            evidence_id = record_rank_evidence(
                db,
                curve_id=curve_id,
                model=model,
                data={
                    "engine": "sage.mestre_bober_zero_sum",
                    "evidence_type": "conditional_analytic_upper_attempt",
                    "status": "error",
                    "rigorous": False,
                    "rigorous_lower": None,
                    "rigorous_upper": None,
                    "exact_rank": None,
                    "conditional_analytic_upper": None,
                    "numerical_rank_signal": None,
                    "assumptions": assumptions,
                    "points_found": [],
                    "options": options,
                    "elapsed_seconds": getattr(exc, "runtime", None),
                    "stderr_summary": str(
                        getattr(exc, "tail", None) or exc
                    ),
                },
            )
            state = get_curve_research_state(db, curve_id)
            return _safe({
                "status": "error",
                "reason": "conditional_analytic_upper_error",
                "evidence_id": evidence_id,
                "attempt_conditional_analytic_upper": None,
                "conditional_analytic_upper": state.get(
                    "conditional_analytic_upper"
                ),
                "rigorous_upper": state.get("rigorous_upper"),
                "exact_rank": state.get("exact_rank"),
                "assumptions": assumptions,
                "options": options,
                "error": str(exc),
            }), None, False

        upper = int(attempt["conditional_analytic_upper"])
        evidence_id = record_rank_evidence(
            db,
            curve_id=curve_id,
            model=model,
            data={
                "engine": str(
                    attempt.get("engine")
                    or "sage.mestre_bober_zero_sum"
                ),
                "engine_version": attempt.get("engine_version"),
                "sage_version": attempt.get("sage_version"),
                "evidence_type": "conditional_analytic_upper",
                "status": "completed",
                "rigorous": False,
                "rigorous_lower": None,
                "rigorous_upper": None,
                "exact_rank": None,
                "conditional_analytic_upper": upper,
                "numerical_rank_signal": None,
                "assumptions": assumptions,
                "points_found": [],
                "minimal_model_a_invariants": attempt.get(
                    "minimal_model_a_invariants"
                ),
                "options": options,
                "elapsed_seconds": attempt.get("_runtime_seconds"),
                "started_at": attempt.get("started_at"),
                "finished_at": attempt.get("finished_at"),
                "stdout_summary": json.dumps(attempt, sort_keys=True),
            },
        )
        state = get_curve_research_state(db, curve_id)
        return _safe({
            "status": "completed",
            "evidence_id": evidence_id,
            "attempt_conditional_analytic_upper": upper,
            "conditional_analytic_upper": state.get(
                "conditional_analytic_upper"
            ),
            "rigorous_upper": state.get("rigorous_upper"),
            "exact_rank": state.get("exact_rank"),
            "assumptions": assumptions,
            "options": options,
            "engine": attempt.get("engine"),
            "engine_version": attempt.get("engine_version"),
            "sage_version": attempt.get("sage_version"),
            "minimal_model_a_invariants": attempt.get(
                "minimal_model_a_invariants"
            ),
            "elapsed_seconds": attempt.get("_runtime_seconds"),
            "proof_boundary": (
                "conditional analytic-rank upper only; never a rigorous "
                "Mordell-Weil rank upper"
            ),
        }), None, False

    if stage_id == "brumer_kramer_classgroup_bound":
        proof_mode = str(config.get("proof_mode") or "grh")
        timeout = int(config.get("timeout") or 300)
        options = {
            "proof_mode": proof_mode,
            "timeout": timeout,
            "theorem": "Brumer-Kramer Proposition 7.1",
        }
        model = [str(x) for x in E.a_invariants()]
        try:
            attempt = run_brumer_kramer_bound(
                E,
                timeout=timeout,
                proof_mode=proof_mode,
            )
        except BrumerKramerTimeout as exc:
            assumptions = (
                ["Generalized Riemann Hypothesis for class-group certification"]
                if proof_mode == "grh"
                else []
            )
            evidence_id = record_rank_evidence(
                db,
                curve_id=curve_id,
                model=model,
                data={
                    "engine": "rank42.brumer_kramer",
                    "engine_version": "1",
                    "evidence_type": "brumer_kramer_attempt",
                    "status": "timeout",
                    "rigorous": False,
                    "rigorous_lower": None,
                    "rigorous_upper": None,
                    "exact_rank": None,
                    "conditional_analytic_upper": None,
                    "conditional_mw_upper": None,
                    "numerical_rank_signal": None,
                    "assumptions": assumptions,
                    "points_found": [],
                    "options": options,
                    "elapsed_seconds": getattr(exc, "runtime", None),
                    "stderr_summary": str(exc),
                },
            )
            state = get_curve_research_state(db, curve_id)
            return _safe({
                "status": "timeout",
                "reason": "brumer_kramer_timeout",
                "evidence_id": evidence_id,
                "proof_mode": proof_mode,
                "conditional_mw_upper": state.get("conditional_mw_upper"),
                "rigorous_upper": state.get("rigorous_upper"),
                "exact_rank": state.get("exact_rank"),
                "options": options,
            }), None, False
        except BrumerKramerFailure as exc:
            assumptions = (
                ["Generalized Riemann Hypothesis for class-group certification"]
                if proof_mode == "grh"
                else []
            )
            evidence_id = record_rank_evidence(
                db,
                curve_id=curve_id,
                model=model,
                data={
                    "engine": "rank42.brumer_kramer",
                    "engine_version": "1",
                    "evidence_type": "brumer_kramer_attempt",
                    "status": "error",
                    "rigorous": False,
                    "rigorous_lower": None,
                    "rigorous_upper": None,
                    "exact_rank": None,
                    "conditional_analytic_upper": None,
                    "conditional_mw_upper": None,
                    "numerical_rank_signal": None,
                    "assumptions": assumptions,
                    "points_found": [],
                    "options": options,
                    "elapsed_seconds": getattr(exc, "runtime", None),
                    "stderr_summary": str(
                        getattr(exc, "tail", None) or exc
                    ),
                },
            )
            state = get_curve_research_state(db, curve_id)
            return _safe({
                "status": "error",
                "reason": "brumer_kramer_error",
                "error": str(exc),
                "evidence_id": evidence_id,
                "proof_mode": proof_mode,
                "conditional_mw_upper": state.get("conditional_mw_upper"),
                "rigorous_upper": state.get("rigorous_upper"),
                "exact_rank": state.get("exact_rank"),
                "options": options,
            }), None, False

        if str(attempt.get("status") or "") == "unsupported":
            evidence_id = record_rank_evidence(
                db,
                curve_id=curve_id,
                model=model,
                data={
                    "engine": "rank42.brumer_kramer",
                    "engine_version": "1",
                    "evidence_type": "brumer_kramer_attempt",
                    "status": "unsupported",
                    "rigorous": False,
                    "rigorous_lower": None,
                    "rigorous_upper": None,
                    "exact_rank": None,
                    "conditional_analytic_upper": None,
                    "conditional_mw_upper": None,
                    "numerical_rank_signal": None,
                    "assumptions": [],
                    "points_found": [],
                    "options": options,
                    "elapsed_seconds": attempt.get("_runtime_seconds"),
                    "stdout_summary": json.dumps(attempt, sort_keys=True),
                },
            )
            state = get_curve_research_state(db, curve_id)
            return _safe({
                **attempt,
                "evidence_id": evidence_id,
                "conditional_mw_upper": state.get("conditional_mw_upper"),
                "rigorous_upper": state.get("rigorous_upper"),
                "exact_rank": state.get("exact_rank"),
            }), None, False

        upper = int(attempt["mordell_weil_rank_upper"])
        assumptions = list(attempt.get("assumptions") or [])
        worker_proved = bool(attempt.get("class_group_proof"))
        rigorous = bool(
            proof_mode == "unconditional"
            and worker_proved
            and not assumptions
        )
        evidence_id = record_rank_evidence(
            db,
            curve_id=curve_id,
            model=model,
            data={
                "engine": str(
                    attempt.get("engine") or "rank42.brumer_kramer"
                ),
                "engine_version": attempt.get("engine_version"),
                "sage_version": attempt.get("sage_version"),
                "evidence_type": (
                    "brumer_kramer_2selmer"
                    if rigorous
                    else "conditional_mw_upper"
                ),
                "status": "completed",
                "rigorous": rigorous,
                "rigorous_lower": None,
                "rigorous_upper": upper if rigorous else None,
                "exact_rank": None,
                "conditional_analytic_upper": None,
                "conditional_mw_upper": None if rigorous else upper,
                "numerical_rank_signal": None,
                "assumptions": assumptions,
                "points_found": [],
                "minimal_model_a_invariants": attempt.get(
                    "minimal_model_a_invariants"
                ),
                "options": options,
                "elapsed_seconds": attempt.get("_runtime_seconds"),
                "started_at": attempt.get("started_at"),
                "finished_at": attempt.get("finished_at"),
                "stdout_summary": json.dumps(attempt, sort_keys=True),
            },
        )
        state = (
            apply_reduced_rank_state(db, curve_id)
            if rigorous
            else get_curve_research_state(db, curve_id)
        )
        return _safe({
            **attempt,
            "evidence_id": evidence_id,
            "proof_mode": proof_mode,
            "worker_class_group_proof": worker_proved,
            "proof_mode_mismatch": bool(
                proof_mode == "unconditional" and not rigorous
            ),
            "result_class": (
                "rigorous_mordell_weil_upper"
                if rigorous
                else "grh_conditional_mordell_weil_upper"
            ),
            "conditional_mw_upper": state.get("conditional_mw_upper"),
            "rigorous_upper": state.get("rigorous_upper"),
            "exact_rank": state.get("exact_rank"),
            "conditional_below_rigorous_lower": bool(
                not rigorous
                and state.get("rigorous_lower") is not None
                and upper < int(state["rigorous_lower"])
            ),
        }), None, False

    if stage_id == "cassels_tate_refinement":
        effort = int(config.get("effort") or 0)
        timeout = int(config.get("timeout") or 300)
        options = {"effort": effort, "timeout": timeout}
        model = [str(x) for x in E.a_invariants()]
        try:
            attempt = run_cassels_tate_refinement(
                E,
                timeout=timeout,
                effort=effort,
            )
            validate_cassels_tate_result(attempt)
        except (
            CasselsTateTimeout,
            CasselsTateFailure,
            ValueError,
            KeyError,
            TypeError,
        ) as exc:
            timed_out = isinstance(exc, CasselsTateTimeout)
            evidence_id = record_rank_evidence(
                db,
                curve_id=curve_id,
                model=model,
                data={
                    "engine": "pari.ellrank_cassels_pairing",
                    "engine_version": None,
                    "evidence_type": "cassels_tate_attempt",
                    "status": "timeout" if timed_out else "error",
                    "rigorous": False,
                    "rigorous_lower": None,
                    "rigorous_upper": None,
                    "exact_rank": None,
                    "conditional_analytic_upper": None,
                    "conditional_mw_upper": None,
                    "numerical_rank_signal": None,
                    "assumptions": [],
                    "points_found": [],
                    "options": options,
                    "elapsed_seconds": getattr(exc, "runtime", None),
                    "stderr_summary": str(getattr(exc, "tail", None) or exc),
                },
            )
            state = get_curve_research_state(db, curve_id)
            return _safe({
                "status": "timeout" if timed_out else "error",
                "reason": (
                    "cassels_tate_timeout"
                    if timed_out
                    else "cassels_tate_invalid_or_failed"
                ),
                "error": str(exc),
                "evidence_id": evidence_id,
                "rigorous_upper": state.get("rigorous_upper"),
                "exact_rank": state.get("exact_rank"),
                "options": options,
            }), None, False

        upper = int(attempt["mordell_weil_rank_upper"])
        evidence_id = record_rank_evidence(
            db,
            curve_id=curve_id,
            model=model,
            data={
                "engine": str(attempt["engine"]),
                "engine_version": attempt.get("engine_version"),
                "sage_version": attempt.get("sage_version"),
                "evidence_type": "cassels_tate_refinement",
                "status": "completed",
                "rigorous": True,
                "rigorous_lower": None,
                "rigorous_upper": upper,
                "exact_rank": None,
                "conditional_analytic_upper": None,
                "conditional_mw_upper": None,
                "numerical_rank_signal": None,
                "assumptions": [],
                "points_found": [],
                "minimal_model_a_invariants": attempt.get(
                    "minimal_model_a_invariants"
                ),
                "options": options,
                "elapsed_seconds": attempt.get("_runtime_seconds"),
                "started_at": attempt.get("started_at"),
                "finished_at": attempt.get("finished_at"),
                "stdout_summary": json.dumps(attempt, sort_keys=True),
            },
        )
        state = apply_reduced_rank_state(db, curve_id)
        return _safe({
            **attempt,
            "evidence_id": evidence_id,
            "result_class": "rigorous_mordell_weil_upper",
            "rigorous_upper": state.get("rigorous_upper"),
            "exact_rank": state.get("exact_rank"),
            "proof_boundary": (
                "PARI points and lower output are diagnostic only; the "
                "proof-attested refined upper alone enters rank evidence"
            ),
        }), None, False

    if stage_id == "isogeny_descent":
        degree = int(config.get("degree") or 2)
        first_limit = int(config.get("first_limit") or 20)
        second_limit = int(config.get("second_limit") or 8)
        second_descent = bool(config.get("second_descent", True))
        timeout = int(config.get("timeout") or 300)
        options = {
            "degree": degree,
            "first_limit": first_limit,
            "second_limit": second_limit,
            "second_descent": second_descent,
            "timeout": timeout,
        }
        model = [str(x) for x in E.a_invariants()]
        try:
            attempt = run_isogeny_descent(E, **options)
            validate_isogeny_descent_result(attempt, E=E)
        except (
            IsogenyDescentTimeout,
            IsogenyDescentFailure,
            ValueError,
            KeyError,
            TypeError,
        ) as exc:
            timed_out = isinstance(exc, IsogenyDescentTimeout)
            evidence_id = record_rank_evidence(
                db,
                curve_id=curve_id,
                model=model,
                data={
                    "engine": "eclib.mwrank_2_isogeny_descent",
                    "evidence_type": "isogeny_descent_attempt",
                    "status": "timeout" if timed_out else "error",
                    "rigorous": False,
                    "rigorous_lower": None,
                    "rigorous_upper": None,
                    "exact_rank": None,
                    "conditional_analytic_upper": None,
                    "conditional_mw_upper": None,
                    "numerical_rank_signal": None,
                    "assumptions": [],
                    "points_found": [],
                    "options": options,
                    "elapsed_seconds": getattr(exc, "runtime", None),
                    "stderr_summary": str(getattr(exc, "tail", None) or exc),
                },
            )
            state = get_curve_research_state(db, curve_id)
            return _safe({
                "status": "timeout" if timed_out else "error",
                "reason": (
                    "isogeny_descent_timeout"
                    if timed_out
                    else "isogeny_descent_invalid_or_failed"
                ),
                "error": str(exc),
                "evidence_id": evidence_id,
                "rigorous_upper": state.get("rigorous_upper"),
                "exact_rank": state.get("exact_rank"),
                "options": options,
            }), None, False

        if str(attempt.get("status") or "") == "unsupported":
            evidence_id = record_rank_evidence(
                db,
                curve_id=curve_id,
                model=model,
                data={
                    "engine": "eclib.mwrank_2_isogeny_descent",
                    "evidence_type": "isogeny_descent_attempt",
                    "status": "unsupported",
                    "rigorous": False,
                    "rigorous_lower": None,
                    "rigorous_upper": None,
                    "exact_rank": None,
                    "conditional_analytic_upper": None,
                    "conditional_mw_upper": None,
                    "numerical_rank_signal": None,
                    "assumptions": [],
                    "points_found": [],
                    "options": options,
                    "elapsed_seconds": attempt.get("_runtime_seconds"),
                    "stdout_summary": json.dumps(attempt, sort_keys=True),
                },
            )
            state = get_curve_research_state(db, curve_id)
            return _safe({
                **attempt,
                "evidence_id": evidence_id,
                "rigorous_upper": state.get("rigorous_upper"),
                "exact_rank": state.get("exact_rank"),
            }), None, False

        upper = int(attempt["mordell_weil_rank_upper"])
        evidence_id = record_rank_evidence(
            db,
            curve_id=curve_id,
            model=model,
            data={
                "engine": str(attempt["engine"]),
                "engine_version": attempt.get("engine_version"),
                "sage_version": attempt.get("sage_version"),
                "evidence_type": "isogeny_descent_2",
                "status": "completed",
                "rigorous": True,
                "rigorous_lower": None,
                "rigorous_upper": upper,
                "exact_rank": None,
                "conditional_analytic_upper": None,
                "conditional_mw_upper": None,
                "numerical_rank_signal": None,
                "assumptions": [],
                "points_found": [],
                "minimal_model_a_invariants": attempt.get(
                    "minimal_model_a_invariants"
                ),
                "options": options,
                "elapsed_seconds": attempt.get("_runtime_seconds"),
                "started_at": attempt.get("started_at"),
                "finished_at": attempt.get("finished_at"),
                "stdout_summary": json.dumps(attempt, sort_keys=True),
            },
        )
        state = apply_reduced_rank_state(db, curve_id)
        return _safe({
            **attempt,
            "evidence_id": evidence_id,
            "result_class": "rigorous_mordell_weil_upper",
            "rigorous_upper": state.get("rigorous_upper"),
            "exact_rank": state.get("exact_rank"),
            "proof_boundary": (
                "exact phi/dual-phi Selmer dimensions and the validated "
                "mwrank upper are rigorous; returned lower/search data are "
                "diagnostic only"
            ),
        }), None, False

    if stage_id == "local_root_numbers":
        result = local_root_number_profile(
            db,
            curve_id=curve_id,
            E=E,
            prime_bound=int(config.get("prime_bound") or 200),
            bad_prime_timeout=int(config.get("bad_prime_timeout") or 20),
            include_all_bad=bool(config.get("include_all_bad", True)),
        )
        root_number = result.get("global_root_number")
        if root_number is not None:
            publication = publish_curve_root_number(
                db,
                curve_id,
                int(root_number),
                source="pipeline:local_root_numbers",
                method="prime_local_finite_product",
            )
            authority_check = publication["authority_check"]
            result["arithmetic_authority_check"] = authority_check
            result["arithmetic_conflicts"] = list(
                authority_check.get("conflicts") or []
            )
            result["published_to_curve_arithmetic"] = bool(
                publication.get("published")
            )
            result["root_number_publication"] = publication
        else:
            result["published_to_curve_arithmetic"] = False
        wanted = str(config.get("global_sign") or "any")
        mismatch = (
            wanted in {"+1", "-1"}
            and bool(result.get("complete"))
            and not bool(result.get("arithmetic_conflicts"))
            and root_number is not None
            and int(root_number) != int(wanted)
        )
        result["requested_global_sign"] = wanted
        result["filter_mismatch"] = bool(mismatch)
        if mismatch and bool(config.get("prune_mismatch", False)):
            result["status"] = "constraint_filtered"
            result["reason"] = "exact_root_number_filter"
            return _safe(result), "constraint_filtered", False
        return _safe(result), None, False

    if stage_id == "bad_prime_fingerprint":
        result = bad_prime_fingerprint(
            db,
            curve_id=curve_id,
            E=E,
            prime_bound=int(config.get("prime_bound") or 200),
            bad_prime_timeout=int(config.get("bad_prime_timeout") or 20),
            include_all_bad=bool(config.get("include_all_bad", True)),
        )
        bad = {int(p) for p in result.get("bad_primes") or []}
        required = {int(p) for p in config.get("required_primes") or []}
        missing = sorted(required - bad)
        max_bad = int(config.get("max_bad_primes") or 0)
        too_many = bool(max_bad > 0 and len(bad) > max_bad)
        mismatch = bool(
            result.get("bad_primes_complete")
            and not bool(result.get("arithmetic_conflicts"))
            and (missing or too_many)
        )
        result["required_primes"] = sorted(required)
        result["missing_required_primes"] = missing
        result["max_bad_primes"] = max_bad
        result["filter_mismatch"] = mismatch
        if mismatch and bool(config.get("prune_mismatch", False)):
            result["status"] = "constraint_filtered"
            result["reason"] = (
                "missing_required_bad_primes" if missing
                else "too_many_bad_primes"
            )
            return _safe(result), "constraint_filtered", False
        return _safe(result), None, False

    if stage_id == "torsion_mod_p_sieve":
        group = str(config.get("torsion_group") or "target")
        if group == "target":
            if run["target_mode"] != "torsion":
                raise ValueError("torsion_group='target' requires Torsion Group mode")
            group = str(target["torsion_group"])
        result = torsion_mod_p_sieve(
            db,
            curve_id=curve_id,
            E=E,
            torsion_group=group,
            prime_bound=int(config.get("prime_bound") or 100),
            max_primes=int(config.get("max_primes") or 12),
        )
        if result.get("rigorous_rejection") and bool(
            config.get("prune_obstructed", True)
        ):
            result["status"] = "constraint_filtered"
            result["reason"] = "rigorous_torsion_mod_p_obstruction"
            return _safe(result), "constraint_filtered", False
        result["status"] = "completed"
        return _safe(result), None, False

    if stage_id == "local_solubility_sieve":
        max_prime = int(config.get("max_prime") or 97)
        # Ensure bad-prime rows exist even when the earlier cache used a very
        # small good-prime bound.
        cache_result = cache_curve_primes(
            db,
            curve_id=curve_id,
            E=E,
            prime_bound=max(3, max_prime + 1),
            include_all_bad=bool(config.get("include_bad", True)),
            bad_prime_timeout=int(config.get("bad_prime_timeout") or 20),
        )
        primes = local_sieve_prime_set(
            db,
            curve_id=curve_id,
            explicit_primes=[int(x) for x in config.get("explicit_primes") or []],
            include_bad=bool(config.get("include_bad", True)),
            max_prime=max_prime,
        )
        result = sieve_stored_quartics(
            db,
            curve_id=curve_id,
            primes=primes,
        )
        unknown_reduction = sorted({
            int(p)
            for p in (cache_result.get("unknown_reduction_primes") or [])
            if int(p) <= max_prime
        })
        include_bad = bool(config.get("include_bad", True))
        prime_set_complete = bool(
            not include_bad or not unknown_reduction
        )
        result.update(
            status=(
                "completed"
                if prime_set_complete
                else "completed_partial_prime_set"
            ),
            downstream_pointed_quartic_presieve=True,
            requested_max_prime=max_prime,
            include_bad_primes=include_bad,
            prime_cache_status=str(cache_result.get("status") or "inconclusive"),
            prime_set_complete_for_requested_bound=prime_set_complete,
            unknown_reduction_primes=unknown_reduction,
            bad_primes_complete=bool(cache_result.get("bad_primes_complete")),
            bad_prime_error=cache_result.get("bad_prime_error"),
        )
        return _safe(result), None, False

    if stage_id == "section_height_shell":
        result = section_height_shell(context=context, config=config)
        return _safe(result), None, False

    if stage_id == "trace_section_constructor":
        prior = _constructive_prior_stage_result(
            db,
            run_id=run["id"],
            context=context,
            before_stage_index=stage_index,
            stage_id="section_height_shell",
        )
        result = trace_section_constructor(
            context=context,
            config=config,
            section_shell=prior,
        )
        return _safe(result), None, False

    if stage_id == "forced_bisection_constructor":
        prior = _constructive_prior_stage_result(
            db,
            run_id=run["id"],
            context=context,
            before_stage_index=stage_index,
            stage_id="trace_section_constructor",
        )
        result = forced_bisection_constructor(
            context=context,
            config=config,
            trace_state=prior,
        )
        return _safe(result), None, False

    if stage_id == "exact_torsion":
        timeout = max(1, int(config.get("timeout") or 30))
        try:
            data = persist_curve_torsion(
                db,
                curve_id,
                E,
                algorithm="pipeline:sage.torsion_subgroup",
                timeout=timeout,
            )
        except TorsionTimeout as exc:
            return {
                "status": "timeout",
                "reason": "exact_torsion_timeout",
                "timeout": timeout,
                "error": str(exc),
                "torsion_known": False,
            }, None, False
        except TorsionFailure as exc:
            return {
                "status": "inconclusive",
                "reason": "exact_torsion_error",
                "timeout": timeout,
                "error": str(exc),
                "torsion_known": False,
            }, None, False

        # Torsion-target Family runs and single-curve Target runs use the same
        # hard proof boundary. A declared target group is authoritative only
        # after exact torsion completed; timeout/error never becomes mismatch.
        wanted_raw = target.get("torsion_group")
        if wanted_raw is not None:
            wanted = canonical_torsion_label(wanted_raw)
            if str(data["torsion_label"]) != wanted:
                return {
                    **data,
                    "status": "torsion_mismatch",
                    "reason": "torsion_mismatch",
                    "wanted_torsion": wanted,
                    "timeout": timeout,
                    "torsion_known": True,
                }, "torsion_mismatch", False
        return {
            **data,
            "status": "completed",
            "timeout": timeout,
            "torsion_known": True,
        }, None, False

    if stage_id == "specialization_seeds":
        family = context.get("family")
        if family is None:
            return {"status": "skipped", "reason": "no_family_context"}, None, False
        if not callable(getattr(family, "certified_specialization_points", None)):
            return {
                "status": "skipped",
                "reason": "family_has_no_specialization_seed_capability",
                "capability_driven": True,
            }, None, False
        source_E = context.get("source_E")
        if source_E is None:
            print(
                f"[pipeline family] reconstructing source model for specialization seeds "
                f"t={parameter}",
                flush=True,
            )
            source_E = context["family"].curve(QQ(str(parameter)))
            if source_E is None:
                return {
                    "status": "inconclusive",
                    "reason": "family_source_model_unavailable",
                }, None, False
            context["source_E"] = source_E
        result = _attach_family_specialization_seed(
            db,
            curve_id,
            context["family"],
            parameter,
            source_E,
            E,
            int(
                config.get("certificate_timeout")
                or run_config.get("certificate_timeout")
                or 180
            ),
        )
        point_status = str(result.get("family_point_status") or "")
        certificate_status = str(result.get("certificate_status") or "")
        seed_points = int(result.get("specialization_seed_points") or 0)
        verified = bool(result.get("specialization_seed_verified"))

        if point_status in {
            "plugin_exception", "invalid_plugin_point", "model_transport_error"
        }:
            return {
                "status": "error",
                "outcome": point_status,
                "reason": point_status,
                **_safe(result),
            }, None, False
        if seed_points <= 0:
            return {
                "status": "skipped",
                "outcome": "no_points_for_parameter",
                "reason": "no_matching_specialization_seed",
                **_safe(result),
            }, None, False
        if certificate_status == "timeout":
            return {
                "status": "timeout",
                "outcome": "certificate_timeout",
                "reason": "certificate_timeout",
                **_safe(result),
            }, None, False
        if certificate_status in {"error", "inconclusive"} and not verified:
            return {
                "status": "inconclusive",
                "outcome": "certificate_inconclusive",
                "reason": "certificate_inconclusive",
                **_safe(result),
            }, None, False

        return {
            "status": "completed",
            "outcome": (
                "completed_certified"
                if verified
                else "completed_no_growth"
            ),
            **_safe(result),
        }, None, False

    if stage_id == "family_baseline":
        family = context.get("family")
        if family is None:
            return {"status": "skipped", "reason": "no_family_context"}, None, False
        if not callable(getattr(family, "generic_section_points", None)):
            return {
                "status": "skipped",
                "reason": "family_has_no_generic_section_capability",
                "capability_driven": True,
            }, None, False
        source_E = context.get("source_E")
        if source_E is None:
            print(
                f"[pipeline family] reconstructing source model for sections "
                f"t={parameter}",
                flush=True,
            )
            source_E = context["family"].curve(QQ(str(parameter)))
            if source_E is None:
                return {
                    "status": "inconclusive",
                    "reason": "family_source_model_unavailable",
                }, None, False
            context["source_E"] = source_E
        result = _attach_family_baseline(
            db,
            curve_id,
            context["family"],
            parameter,
            source_E,
            E,
            int(config.get("certificate_timeout") or 120),
            int(config.get("exact_candidates") or 64),
        )
        point_status = str(result.get("family_point_status") or "")
        seed_status = str(result.get("specialization_seed_status") or "")
        bundle_status = str(result.get("bundle_certificate_status") or "")
        section_points = int(result.get("section_points") or 0)
        growth = int(result.get("growth") or 0)

        if point_status in {
            "plugin_exception", "invalid_plugin_point", "model_transport_error"
        }:
            return {
                "status": "error",
                "outcome": point_status,
                "reason": point_status,
                **_safe(result),
            }, None, False
        if seed_status in {
            "plugin_exception", "invalid_plugin_point", "model_transport_error"
        }:
            return {
                "status": "error",
                "outcome": f"specialization_{seed_status}",
                "reason": f"specialization_{seed_status}",
                **_safe(result),
            }, None, False
        if section_points <= 0:
            if growth > 0:
                return {
                    "status": "completed",
                    "outcome": "specialization_certified_no_sections",
                    **_safe(result),
                }, None, False
            return {
                "status": "skipped",
                "outcome": "no_sections",
                "reason": "no_sections_for_parameter",
                **_safe(result),
            }, None, False
        if bundle_status == "timeout" and growth <= 0:
            return {
                "status": "timeout",
                "outcome": "certificate_timeout",
                "reason": "certificate_timeout",
                **_safe(result),
            }, None, False
        if bundle_status in {"error", "inconclusive"} and growth <= 0:
            return {
                "status": "inconclusive",
                "outcome": "certificate_inconclusive",
                "reason": "certificate_inconclusive",
                **_safe(result),
            }, None, False

        return {
            "status": "completed",
            "outcome": (
                "completed_certified"
                if growth > 0 or bundle_status == "certified"
                else "completed_no_growth"
            ),
            **_safe(result),
        }, None, False

    if stage_id == "specialization_injectivity_certificate":
        family = context.get("family")
        if family is None:
            return {
                "status": "skipped",
                "reason": "no_family_context",
            }, None, False
        try:
            criterion = run_specialization_injectivity(
                family,
                parameter,
                timeout=int(config.get("timeout") or 60),
                max_divisors=int(config.get("max_divisors") or 4096),
            )
        except SpecializationInjectivityTimeout as exc:
            return {
                "status": "timeout",
                "reason": "injectivity_criterion_timeout",
                "elapsed_seconds": getattr(exc, "runtime", None),
            }, None, False
        except SpecializationInjectivityFailure as exc:
            return {
                "status": "error",
                "reason": "injectivity_criterion_worker_error",
                "error": str(exc),
                "elapsed_seconds": getattr(exc, "runtime", None),
            }, None, False

        criterion_status = str(criterion.get("status") or "inconclusive")
        if criterion_status == "unsupported":
            return {
                "status": "skipped",
                "reason": criterion.get("reason") or "criterion_unsupported",
                "criterion": _safe(criterion),
            }, None, False
        if criterion_status == "rejected":
            return {
                "status": "completed",
                "outcome": "criterion_not_satisfied",
                "injectivity_certified": False,
                "reason": criterion.get("reason"),
                "criterion": _safe(criterion),
                "criterion_failure_is_not_noninjectivity_proof": True,
            }, None, False
        if criterion_status != "completed":
            return {
                "status": "inconclusive",
                "reason": criterion.get("reason") or "criterion_inconclusive",
                "criterion": _safe(criterion),
            }, None, False
        if not (
            criterion.get("injective") is True
            and criterion.get("rigorous") is True
            and not list(criterion.get("assumptions") or [])
        ):
            return {
                "status": "error",
                "reason": "invalid_injectivity_proof_attestation",
                "criterion": _safe(criterion),
            }, None, False

        try:
            symbolic = family.validate_symbolically()
        except Exception as exc:
            return {
                "status": "error",
                "reason": "generic_section_symbolic_validation_failed",
                "error": repr(exc),
                "criterion": _safe(criterion),
            }, None, False

        source_E = family.curve(QQ(str(parameter)))
        if source_E is None:
            return {
                "status": "inconclusive",
                "reason": "specialized_family_curve_unavailable",
                "criterion": _safe(criterion),
            }, None, False

        try:
            section_points = list(
                family.generic_section_points(QQ(str(parameter))) or []
            )
        except Exception as exc:
            return {
                "status": "error",
                "reason": "generic_section_specialization_failed",
                "error": repr(exc),
                "criterion": _safe(criterion),
            }, None, False
        section_certificate = None
        section_certificate_status = "no_sections"
        generic_lower = None
        if section_points:
            section_certificate_status = "inconclusive"
            try:
                section_certificate = run_exact_certificate(
                    source_E.a_invariants(),
                    section_points,
                    timeout=max(1, int(config.get("certificate_timeout") or 120)),
                )
            except ExactCertificateTimeout:
                section_certificate_status = "timeout"
            except ExactCertificateFailure as exc:
                section_certificate_status = "error"
                section_certificate = {"error": str(exc)}
            else:
                if section_certificate.get("independent") is True:
                    section_certificate_status = "certified"
                    generic_lower = len(section_points)
                else:
                    section_certificate_status = str(
                        section_certificate.get("status") or "inconclusive"
                    )

        fiber_state = get_curve_research_state(db, int(curve_id))
        generic_upper = (
            None
            if fiber_state.get("rigorous_upper") is None
            else int(fiber_state["rigorous_upper"])
        )
        exact_generic_rank = (
            int(generic_lower)
            if generic_lower is not None
            and generic_upper is not None
            and int(generic_lower) == int(generic_upper)
            else None
        )

        curve_row = get_curve(db, int(curve_id))
        variant = context.get("variant")
        plugin = context.get("plugin")
        family_spec = (
            curve_row["family_spec"]
            if curve_row is not None and curve_row["family_spec"]
            else getattr(variant, "family_spec", None)
        )
        if not family_spec:
            return {
                "status": "error",
                "reason": "family_spec_identity_missing",
                "criterion": _safe(criterion),
            }, None, False
        family_sha256 = (
            curve_row["family_sha256"]
            if curve_row is not None
            else None
        )
        certificate_payload = {
            "criterion": _safe(criterion),
            "symbolic_family_validation": _safe(symbolic),
            "section_count": len(section_points),
            "section_certificate_status": section_certificate_status,
            "section_certificate": _safe(section_certificate),
            "specialized_curve_id": int(curve_id),
            "specialized_rigorous_lower": int(
                fiber_state.get("rigorous_lower") or 0
            ),
            "specialized_rigorous_upper": fiber_state.get("rigorous_upper"),
            "specialized_exact_rank": fiber_state.get("exact_rank"),
            "proof_boundary": (
                "injectivity supplies a generic upper from the specialized "
                "fiber upper; specialized section independence supplies the "
                "generic lower; fiber and generic rank evidence remain separate"
            ),
        }
        evidence_id = record_family_evidence(
            db,
            family_spec=str(family_spec),
            family_sha256=family_sha256,
            plugin_id=(None if plugin is None else getattr(plugin, "id", None)),
            plugin_version=(
                None if plugin is None else getattr(plugin, "version", None)
            ),
            criterion=str(criterion.get("criterion") or "Gusic-Tadic"),
            specialization_parameter=str(parameter),
            specialized_curve_id=int(curve_id),
            generic_lower=generic_lower,
            generic_upper=generic_upper,
            exact_generic_rank=exact_generic_rank,
            certificate=certificate_payload,
            status="completed",
        )
        family_key = family_evidence_key(family_spec, family_sha256)
        family_state = reduce_family_evidence(db, family_key)
        return {
            "status": "completed",
            "outcome": "injectivity_certified",
            "injectivity_certified": True,
            "family_evidence_id": evidence_id,
            "family_key": family_key,
            "generic_lower_from_sections": generic_lower,
            "generic_upper_from_injective_specialization": generic_upper,
            "exact_generic_rank": exact_generic_rank,
            "family_research_state": _safe(family_state),
            "section_certificate_status": section_certificate_status,
            "criterion": _safe(criterion),
            "generic_rank_is_separate_from_specialization_rank": True,
        }, None, False

    if stage_id == "root_number":
        result = local_root_number_profile(
            db,
            curve_id=curve_id,
            E=E,
            prime_bound=int(config.get("prime_bound") or 200),
            bad_prime_timeout=int(config.get("bad_prime_timeout") or 20),
            include_all_bad=True,
        )
        root_number = result.get("global_root_number")
        if root_number is None or not bool(result.get("complete")):
            return {
                **_safe(result),
                "status": "inconclusive",
                "reason": "root_number_incomplete",
                "root_number": None,
                "published_to_curve_arithmetic": False,
            }, None, False

        publication = publish_curve_root_number(
            db,
            curve_id,
            int(root_number),
            source="pipeline:root_number",
            method="prime_local_finite_product",
        )
        authority_check = publication["authority_check"]
        conflicts = list(authority_check.get("conflicts") or [])
        return {
            **_safe(result),
            "status": "inconclusive" if conflicts else "completed",
            **({"reason": "root_number_authority_conflict"} if conflicts else {}),
            "root_number": int(root_number),
            "arithmetic_authority_check": authority_check,
            "arithmetic_conflicts": conflicts,
            "root_number_publication": publication,
            "published_to_curve_arithmetic": bool(publication.get("published")),
        }, None, False

    if stage_id == "pari_upper_gate":
        result = _try_pari_upper(
            db,
            curve_id,
            E,
            int(config.get("timeout") or 2),
            phase=f"pipeline-{run['id']}-upper-gate",
        )
        attempt_status = str(result.get("attempt_status") or "inconclusive")
        upper = result.get("effective_rigorous_upper")
        if upper is None:
            upper = result.get("rigorous_upper")
        lower = _research_lower(db, curve_id)
        common = {
            **_safe(result),
            "budget_class": "screening",
        }
        if (
            bool(config.get("eliminate_below_goal", True))
            and upper is not None
            and lower <= int(upper) < target_rank
        ):
            return {
                **common,
                "status": "goal_filtered",
                "reason": "rigorous_upper_below_goal",
            }, "goal_filtered", False
        if attempt_status == "timeout":
            return {
                **common,
                "status": "timeout",
                "reason": "pari_upper_timeout",
            }, None, False
        if attempt_status != "completed":
            return {
                **common,
                "status": "inconclusive",
                "reason": "pari_upper_inconclusive",
            }, None, False
        return {
            **common,
            "status": "completed",
        }, None, False

    if stage_id == "selmer_bound":
        result = _run_mwrank_selmer_stage(
            db,
            curve_id=curve_id,
            E=E,
            config=config,
            source=f"pipeline:{run['id']}:selmer",
        )
        return _safe(result), None, False

    if stage_id == "selmer_headroom":
        prior = _latest_prior_stage_result(
            db, run_id=run["id"], curve_id=curve_id,
            before_stage_index=stage_index, stage_id="selmer_bound",
        )
        upper = None if prior is None else prior.get("rigorous_upper")
        lower = _research_lower(db, curve_id)
        if upper is None:
            return {
                "status": "inconclusive",
                "reason": "no_completed_mwrank_rank_bound",
                "rigorous_lower": lower,
                "rigorous_upper": None,
                "bound_kind": "mwrank_rank_bound",
                "pipeline_score": None,
                "heuristic_only": True,
            }, None, False
        upper = int(upper)
        if upper < lower:
            return {
                "status": "inconclusive",
                "reason": "upper_below_rigorous_lower",
                "rigorous_lower": lower,
                "rigorous_upper": upper,
                "pipeline_score": None,
                "heuristic_only": True,
            }, None, False
        return {
            "status": "completed",
            "rigorous_lower": lower,
            "rigorous_upper": upper,
            "bound_kind": "mwrank_rank_bound",
            "headroom_kind": "mwrank_rank_bound_minus_rigorous_lower",
            "mwrank_rank_headroom": upper - lower,
            "pipeline_score": float(upper - lower),
            "heuristic_only": True,
            "sha_may_contribute": True,
        }, None, False

    if stage_id == "covering_selmer_branch":
        result = _run_covering_selmer_branches(
            db,
            curve_id=curve_id,
            E=E,
            run_id=run["id"],
            stage_index=stage_index,
            config=config,
            certificate_timeout=int(
                config.get("certificate_timeout")
                or run_config.get("certificate_timeout")
                or 120
            ),
            exact_candidates=int(
                config.get("exact_candidates")
                or run_config.get("exact_candidates")
                or 64
            ),
        )
        return _safe(result), None, False

    if stage_id == "small_point_density":
        heights = sorted({int(x) for x in config.get("heights") or [100, 1000, 10000]})
        rounds = []
        weighted_yield = 0.0
        total_exact = 0
        total_growth = 0
        aggregate = {
            "planned_searches": 0,
            "completed_searches": 0,
            "resume_completed": 0,
            "resume_timeouts": 0,
            "resume_failures": 0,
            "timeouts": 0,
            "failures": 0,
            "map_back_failures": 0,
        }
        comparable_rounds = 0
        positive_rounds = 0
        retry_policy, retry_timeout = _point_retry_options(
            config, max(1, int(config.get("timeout") or 3))
        )
        prepared_model = prepare_point_search_model(
            db,
            curve_id=curve_id,
            minimal_model=True,
            model_prep_timeout=8,
        )
        for height in heights:
            result = run_auto_point_tier(
                db, curve_id=curve_id,
                tier=f"pipeline-density-{run['id']}-{stage_index}-H{height}",
                heights=(height,), chart_budget=1,
                timeout=max(1, int(config.get("timeout") or 3)),
                executable=ratpoints,
                certificate_timeout=int(
                    config.get("certificate_timeout")
                    or run_config.get("certificate_timeout") or 120
                ),
                exact_candidates=int(
                    config.get("exact_candidates")
                    or run_config.get("exact_candidates") or 32
                ),
                native_only=True, minimal_model=True,
                denominator_low=1, denominator_high=1, model_prep_timeout=8,
                pipeline_checkpoint=_pipeline_point_checkpoint(
                    run, context, stage_index, stage_id
                ),
                retry_policy=retry_policy,
                retry_timeout=retry_timeout,
                prepared_model=prepared_model,
            )
            exact_points = int(result.get("exact_points") or 0)
            rank_growth = int(result.get("rank_growth") or 0)
            outcome = point_search_outcome(result)
            coverage = result.get("search_coverage")
            if not isinstance(coverage, dict):
                coverage = point_search_coverage(result)
            total_exact += exact_points
            total_growth += rank_growth
            aggregate["planned_searches"] += int(
                coverage.get("planned_searches") or 0
            )
            for key in aggregate:
                if key == "planned_searches":
                    continue
                aggregate[key] += int(result.get(key) or 0)
            if outcome == "completed":
                comparable_rounds += 1
                if exact_points > 0:
                    positive_rounds += 1
                weighted_yield += float(exact_points) / max(
                    1.0, math.log10(float(height) + 10.0)
                )
            rounds.append({
                "height": height,
                "exact_points": exact_points,
                "rank_growth": rank_growth,
                "completed_searches": int(result.get("completed_searches") or 0),
                "timeouts": int(result.get("timeouts") or 0),
                "failures": int(result.get("failures") or 0),
                "rigorous_lower": result.get("rigorous_lower"),
                "search_model": result.get("search_model"),
                "search_outcome": outcome,
                "search_coverage": coverage,
                "comparable_for_score": outcome == "completed",
            })
        persistence_bonus = (
            float(positive_rounds) / float(comparable_rounds)
            if comparable_rounds > 0
            else 0.0
        )
        score = weighted_yield + persistence_bonus
        aggregate_contract = _point_search_stage_payload(aggregate)
        return {
            **aggregate_contract,
            "heights": heights,
            "rounds": rounds,
            "exact_points": total_exact,
            "rank_growth": total_growth,
            "positive_round_fraction": persistence_bonus,
            "weighted_point_yield": weighted_yield,
            "pipeline_score": score if comparable_rounds > 0 else None,
            "configured_rounds": len(rounds),
            "scored_rounds": comparable_rounds,
            "score_coverage_fraction": (
                float(comparable_rounds) / float(len(rounds))
                if rounds else 0.0
            ),
            "partial_score": comparable_rounds < len(rounds),
            "search_model": prepared_model["search_model"],
            "model_prep_error": prepared_model.get("model_prep_error"),
            "search_model_fixed_across_rounds": True,
            "heuristic_only": True,
            "exact_points_use_ordinary_evidence_path": True,
        }, None, False

    if stage_id == "integral_seed":
        model_mode = str(config.get("model_mode") or "stored").strip().lower()
        if model_mode not in {"stored", "minimal"}:
            raise ValueError("Native Model Seed model_mode must be stored or minimal")
        retry_policy, retry_timeout = _point_retry_options(
            config, int(config.get("timeout") or 4)
        )
        result = run_auto_point_tier(
            db,
            curve_id=curve_id,
            tier=f"pipeline-integral-{run['id']}-{stage_index}",
            heights=tuple(int(x) for x in config.get("heights") or [1000, 10000]),
            chart_budget=1,
            timeout=int(config.get("timeout") or 4),
            executable=ratpoints,
            certificate_timeout=int(run_config.get("certificate_timeout") or 120),
            exact_candidates=int(run_config.get("exact_candidates") or 64),
            native_only=True,
            minimal_model=(model_mode == "minimal"),
            denominator_low=1,
            denominator_high=1,
            model_prep_timeout=int(config.get("model_prep_timeout") or 8),
            certify_after_search=bool(config.get("certify_after_search", False)),
            pipeline_checkpoint=_pipeline_point_checkpoint(
                run, context, stage_index, stage_id
            ),
            retry_policy=retry_policy,
            retry_timeout=retry_timeout,
        )
        return _point_search_stage_payload(result), None, False

    if stage_id == "simon_covering":
        policy = {
            "classical_covering_enabled": True,
            "classical_covering_engine": "simon_known",
            "classical_covering_min_rank": 0,
            "classical_covering_top_fresh": 10**9,
            "classical_covering_timeout": int(config.get("timeout") or 90),
            "classical_covering_lim1": int(config.get("lim1") or 5),
            "classical_covering_lim3": int(config.get("lim3") or 80),
            "classical_covering_limbigprime": (
                int(config.get("limbigprime"))
                if config.get("limbigprime") is not None
                else 0
            ),
        }
        result = _run_classical_covering_escalation(
            db,
            curve_id=curve_id,
            E=E,
            policy=policy,
            certificate_timeout=int(run_config.get("certificate_timeout") or 120),
            exact_candidates=int(config.get("exact_candidates") or run_config.get("exact_candidates") or 64),
        )
        return _safe(result), None, False

    if stage_id == "mwrank_covering":
        timeout = int(config.get("timeout") or 90)
        retry_policy = str(config.get("retry_policy") or "manual")
        retry_timeout = max(
            1, int(config.get("retry_timeout") or max(300, timeout))
        )
        attempt_identity = _bounded_stage_attempt_identity(
            run,
            context,
            stage_index,
            stage_id,
            "mwrank_coverings",
        )
        policy = {
            "classical_covering_enabled": True,
            "classical_covering_engine": "mwrank_coverings",
            "classical_covering_min_rank": 0,
            "classical_covering_top_fresh": 10**9,
            "classical_covering_timeout": timeout,
            "classical_covering_first_limit": int(config.get("first_limit") or 20),
            "classical_covering_second_limit": int(config.get("second_limit") or 10),
        }
        result = _run_classical_covering_escalation(
            db,
            curve_id=curve_id,
            E=E,
            policy=policy,
            certificate_timeout=int(run_config.get("certificate_timeout") or 120),
            exact_candidates=int(
                config.get("exact_candidates")
                or run_config.get("exact_candidates")
                or 64
            ),
            attempt_identity=attempt_identity,
            attempt_number=max(1, int(config.get("_attempt_number") or 1)),
            retry_policy=retry_policy,
            retry_timeout=retry_timeout,
        )
        return _safe(result), None, False

    if stage_id == "plugin_family_search":
        plugin = context.get("plugin")
        variant = context.get("variant")
        if plugin is None or variant is None:
            return {"status": "skipped", "reason": "no_family_plugin"}, None, False
        args = SimpleNamespace(
            family_search_timeout=int(config.get("timeout") or 3600),
            family_search_retry_policy=str(config.get("retry_policy") or "manual"),
            family_search_retry_timeout=max(1, int(config.get("retry_timeout") or 7200)),
            family_search_attempt_number=max(1, int(config.get("_attempt_number") or 1)),
            family_search_options=dict(config.get("options") or {}),
            certificate_timeout=int(config.get("certificate_timeout") or run_config.get("certificate_timeout") or 120),
            exact_candidates=int(config.get("exact_candidates") or run_config.get("exact_candidates") or 64),
            ratpoints=ratpoints,
            project_root=run_config["project_root"],
            feature_plugin_ids=run_config.get("feature_plugin_ids"),
        )
        result = _run_plugin_family_search(
            args,
            db,
            curve_id=curve_id,
            plugin=plugin,
            variant=variant,
            source_pool_id=target.get("candidate_pool_id"),
            source_candidate_id=context.get("source_candidate_id"),
            E=E,
        )
        return _safe(result), None, False

    if stage_id == "plugin_geometry":
        plugin = context.get("plugin")
        variant = context.get("variant")
        if plugin is None or variant is None:
            return {"status": "skipped", "reason": "no_family_plugin"}, None, False
        args = SimpleNamespace(
            geometry_first=True,
            geometry_keep=10**9,
            target_rank=target_rank,
            ratpoints=ratpoints,
            certificate_timeout=int(run_config.get("certificate_timeout") or 120),
            exact_candidates=int(config.get("exact_candidates") or run_config.get("exact_candidates") or 64),
            geometry_timeout=int(config.get("timeout") or 180),
            geometry_retry_policy=str(config.get("retry_policy") or "manual"),
            geometry_retry_timeout=max(
                1,
                int(
                    config.get("retry_timeout")
                    or max(600, int(config.get("timeout") or 180))
                ),
            ),
            geometry_attempt_number=max(
                1, int(config.get("_attempt_number") or 1)
            ),
            geometry_options=dict(config.get("options") or {}),
            db=str(run_config["db_path"]),
            project_root=str(run_config["project_root"]),
            feature_plugin_ids=run_config.get("feature_plugin_ids"),
        )
        result = _run_plugin_geometry_target(
            args,
            db,
            curve_id=curve_id,
            plugin=plugin,
            variant=variant,
            rank_order=max(1, int(context.get("rank_order") or 1)),
            E=E,
        )
        return _safe(result or {"status": "skipped", "reason": "no_target_search_adapter"}), None, False

    if stage_id == "pointed_quartic":
        local_cfg = _prior_stage_config(
            run, stage_index, "local_solubility_sieve"
        )
        local_primes = ()
        if local_cfg is not None:
            max_prime = int(local_cfg.get("max_prime") or 97)
            cache_curve_primes(
                db,
                curve_id=curve_id,
                E=E,
                prime_bound=max(3, max_prime + 1),
                include_all_bad=bool(local_cfg.get("include_bad", True)),
                bad_prime_timeout=int(local_cfg.get("bad_prime_timeout") or 20),
            )
            local_primes = local_sieve_prime_set(
                db,
                curve_id=curve_id,
                explicit_primes=[
                    int(x) for x in local_cfg.get("explicit_primes") or []
                ],
                include_bad=bool(local_cfg.get("include_bad", True)),
                max_prime=max_prime,
            )
        result = run_pointed_quartic_escalation(
            db,
            curve_id=curve_id,
            E=E,
            heights=tuple(int(x) for x in config.get("heights") or [10000, 100000]),
            anchors=int(config.get("anchors") or 16),
            pool_size=int(config.get("pool_size") or 160),
            rounds=int(config.get("rounds") or 1),
            deep_keep=int(config.get("deep_keep") or 8),
            timeout=int(config.get("timeout") or 6),
            reduce_timeout=int(config.get("reduce_timeout") or 15),
            executable=ratpoints,
            certificate_timeout=int(run_config.get("certificate_timeout") or 120),
            exact_candidates=int(run_config.get("exact_candidates") or 64),
            parameter=parameter,
            pipeline_run_id=int(run["id"]),
            pipeline_candidate_id=context.get("pipeline_candidate_id"),
            pipeline_stage_index=int(stage_index),
            pipeline_stage_id=str(stage_id),
            target_rank=target_rank,
            local_sieve_primes=local_primes,
            dry_round_min_coverage=float(
                config.get("dry_round_min_coverage") or 1.0
            ),
        )
        return _safe(result), None, False

    if stage_id == "higher_descent_ladder":
        result = higher_descent_ladder(
            db,
            context=context,
            config=config,
            run_id=run["id"],
            stage_index=stage_index,
            certificate_timeout=int(
                config.get("certificate_timeout")
                or run_config.get("certificate_timeout")
                or 120
            ),
            exact_candidates=int(
                config.get("exact_candidates")
                or run_config.get("exact_candidates")
                or 96
            ),
        )
        return _safe(result), None, False

    if stage_id == "covering_minimize_reduce":
        result = minimize_reduce_coverings(
            db,
            curve_id=curve_id,
            max_coverings=int(config.get("max_coverings") or 16),
            timeout=int(config.get("timeout") or 20),
            require_improvement=bool(
                config.get("require_improvement", True)
            ),
        )
        return _safe(result), None, False

    if stage_id == "covering_local_height_planner":
        result = plan_covering_searches(
            db,
            curve_id=curve_id,
            max_coverings=int(config.get("max_coverings") or 16),
            timeout=int(config.get("timeout") or 30),
            base_height=int(config.get("base_height") or 10000),
            max_height=int(config.get("max_height") or 10000000),
            base_timeout=int(config.get("base_timeout") or 30),
            max_timeout=int(config.get("max_timeout") or 300),
            reject_locally_insoluble=bool(
                config.get("reject_locally_insoluble", True)
            ),
        )
        return _safe(result), None, False

    if stage_id == "selmer_element_fanout":
        result = selmer_element_fanout(
            db,
            context=context,
            config=config,
            ratpoints=ratpoints,
            run_id=run["id"],
            stage_index=stage_index,
            certificate_timeout=int(
                config.get("certificate_timeout")
                or run_config.get("certificate_timeout")
                or 120
            ),
            exact_candidates=int(
                config.get("exact_candidates")
                or run_config.get("exact_candidates")
                or 96
            ),
        )
        return _safe(result), None, False

    if stage_id == "padic_covering_search":
        result = padic_covering_point_search(
            db,
            context=context,
            config=config,
            run_id=run["id"],
            stage_index=stage_index,
            certificate_timeout=int(
                config.get("certificate_timeout")
                or run_config.get("certificate_timeout")
                or 120
            ),
            exact_candidates=int(
                config.get("exact_candidates")
                or run_config.get("exact_candidates")
                or 96
            ),
        )
        return _safe(result), None, False

    if stage_id == "mw_growth_loop":
        local_cfg = _prior_stage_config(
            run, stage_index, "local_solubility_sieve"
        )
        local_primes = ()
        if local_cfg is not None:
            max_prime = int(local_cfg.get("max_prime") or 97)
            cache_curve_primes(
                db,
                curve_id=curve_id,
                E=E,
                prime_bound=max(3, max_prime + 1),
                include_all_bad=bool(local_cfg.get("include_bad", True)),
                bad_prime_timeout=int(local_cfg.get("bad_prime_timeout") or 20),
            )
            local_primes = local_sieve_prime_set(
                db,
                curve_id=curve_id,
                explicit_primes=[
                    int(x) for x in local_cfg.get("explicit_primes") or []
                ],
                include_bad=bool(local_cfg.get("include_bad", True)),
                max_prime=max_prime,
            )
        result = run_pointed_quartic_escalation(
            db,
            curve_id=curve_id,
            E=E,
            heights=tuple(
                int(x)
                for x in config.get("heights")
                or [10000, 100000, 1000000]
            ),
            anchors=int(config.get("anchors") or 32),
            pool_size=int(config.get("pool_size") or 320),
            rounds=int(config.get("max_rounds") or 8),
            deep_keep=int(config.get("deep_keep") or 12),
            timeout=int(config.get("timeout") or 8),
            reduce_timeout=int(config.get("reduce_timeout") or 20),
            executable=ratpoints,
            certificate_timeout=int(
                config.get("certificate_timeout")
                or run_config.get("certificate_timeout")
                or 120
            ),
            exact_candidates=int(
                config.get("exact_candidates")
                or run_config.get("exact_candidates")
                or 96
            ),
            parameter=parameter,
            pipeline_run_id=int(run["id"]),
            pipeline_candidate_id=context.get("pipeline_candidate_id"),
            pipeline_stage_index=int(stage_index),
            pipeline_stage_id=str(stage_id),
            target_rank=target_rank,
            local_sieve_primes=local_primes,
            dry_round_min_coverage=float(
                config.get("dry_round_min_coverage") or 1.0
            ),
        )
        rounds = list(result.get("rounds") or [])
        stop_reason = str(result.get("stop_reason") or "unknown")
        return {
            **_safe(result),
            "feedback_loop": True,
            "max_rounds": int(config.get("max_rounds") or 8),
            "rounds_completed": len(rounds),
            "feedback_stop_reason": stop_reason,
            "stopped_on_dry_round": stop_reason == "dry_completed_round",
            "stopped_on_timeout_budget": (
                stop_reason == "timeout_budget_exhausted"
            ),
            "stopped_on_engine_errors": stop_reason == "engine_errors",
            "stopped_on_rank_goal": stop_reason == "rank_goal",
            "stopped_on_no_new_models": stop_reason == "no_new_models",
            "stopped_on_max_rounds": stop_reason == "max_rounds",
            "stopped_on_incomplete_round": stop_reason == "incomplete_round",
            "stopped_on_user_stop": stop_reason == "user_stop",
        }, None, False

    if stage_id == "denominator_band":
        low = int(config.get("denominator_low") or 1)
        high = int(config.get("denominator_high") or low)
        retry_policy, retry_timeout = _point_retry_options(
            config, max(1, int(config.get("timeout") or 8))
        )
        result = run_auto_point_tier(
            db,
            curve_id=curve_id,
            tier=f"pipeline-denom-{run['id']}-{stage_index}-{low}-{high}",
            heights=tuple(int(x) for x in config.get("heights") or [10000, 100000]),
            chart_budget=max(1, int(config.get("charts") or 5)),
            timeout=max(1, int(config.get("timeout") or 8)),
            executable=ratpoints,
            certificate_timeout=int(
                config.get("certificate_timeout")
                or run_config.get("certificate_timeout")
                or 120
            ),
            exact_candidates=int(
                config.get("exact_candidates")
                or run_config.get("exact_candidates")
                or 64
            ),
            native_only=False,
            minimal_model=False,
            denominator_low=low,
            denominator_high=high,
            pipeline_checkpoint=_pipeline_point_checkpoint(
                run, context, stage_index, stage_id
            ),
            retry_policy=retry_policy,
            retry_timeout=retry_timeout,
        )
        payload = _point_search_stage_payload(result)
        payload["denominator_low"] = low
        payload["denominator_high"] = high
        return payload, None, False

    if stage_id == "point_yield_persistence":
        all_records = _prior_denominator_yields(
            db, run_id=run["id"], curve_id=curve_id, before_stage_index=stage_index,
        )
        records = [
            rec for rec in all_records
            if str(rec.get("search_outcome") or "") == "completed"
        ]
        incomplete_records = [
            rec for rec in all_records
            if str(rec.get("search_outcome") or "") != "completed"
        ]
        minimum_bands = max(1, int(config.get("minimum_bands") or 2))
        if len(records) < minimum_bands:
            return {
                "status": "inconclusive",
                "reason": "insufficient_completed_denominator_bands",
                "bands_seen": len(all_records),
                "completed_bands": len(records),
                "incomplete_bands": len(incomplete_records),
                "minimum_bands": minimum_bands,
                "records": records,
                "incomplete_records": incomplete_records,
                "pipeline_score": None,
                "heuristic_only": True,
            }, None, False
        total = sum(int(rec["exact_points"]) for rec in records)
        positive = sum(1 for rec in records if int(rec["exact_points"]) > 0)
        active_fraction = float(positive) / float(len(records))
        weighted = 0.0
        for index, rec in enumerate(records, 1):
            weighted += (
                float(index) / float(len(records))
                * math.log1p(float(rec["exact_points"]))
            )
        late_bonus = math.log1p(float(records[-1]["exact_points"]))
        score = active_fraction + weighted + 0.5 * late_bonus
        return {
            "status": "partial" if incomplete_records else "completed",
            "records": records,
            "incomplete_records": incomplete_records,
            "bands_seen": len(all_records),
            "completed_bands": len(records),
            "incomplete_bands": len(incomplete_records),
            "positive_bands": positive,
            "active_band_fraction": active_fraction,
            "total_exact_points": total,
            "late_band_exact_points": int(records[-1]["exact_points"]),
            "weighted_yield": weighted,
            "pipeline_score": score,
            "score_coverage_fraction": (
                float(len(records)) / float(len(all_records))
                if all_records else 0.0
            ),
            "heuristic_only": True,
            "experimental_metric": "rank_hunter_point_yield_persistence",
        }, None, False

    if stage_id == "affine_search":
        model_mode = str(config.get("model_mode") or "stored").strip().lower()
        if model_mode not in {"stored", "minimal"}:
            raise ValueError("Affine Rational Search model_mode must be stored or minimal")
        retry_policy, retry_timeout = _point_retry_options(
            config, max(1, int(config.get("timeout") or 8))
        )
        result = run_auto_point_tier(
            db,
            curve_id=curve_id,
            tier=f"pipeline-affine-{run['id']}-{stage_index}",
            heights=tuple(int(x) for x in config.get("heights") or [10000, 100000, 1000000]),
            chart_budget=max(1, int(config.get("charts") or 24)),
            timeout=max(1, int(config.get("timeout") or 8)),
            executable=ratpoints,
            certificate_timeout=int(run_config.get("certificate_timeout") or 120),
            exact_candidates=int(run_config.get("exact_candidates") or 64),
            native_only=False,
            minimal_model=(model_mode == "minimal"),
            model_prep_timeout=int(config.get("model_prep_timeout") or 8),
            certify_after_search=bool(config.get("certify_after_search", True)),
            pipeline_checkpoint=_pipeline_point_checkpoint(
                run, context, stage_index, stage_id
            ),
            retry_policy=retry_policy,
            retry_timeout=retry_timeout,
        )
        return _point_search_stage_payload(result), None, False

    if stage_id == "independence":
        result = certify_stored_independence(
            db,
            curve_id=curve_id,
            E=E,
            source="pipeline_independence",
            search_ref=(
                f"pipeline:{run['id']}:independence:{stage_index}"
            ),
            certificate_timeout=int(
                config.get("certificate_timeout")
                or run_config.get("certificate_timeout")
                or 120
            ),
            max_candidates=int(
                config.get("max_candidates")
                or run_config.get("exact_candidates")
                or 64
            ),
            max_halvings=int(config.get("max_halvings") or 40),
            max_prime=int(config.get("max_prime") or 1000000),
            max_columns=int(config.get("max_columns") or 640),
        )
        return _safe(result), None, False

    if stage_id == "saturation":
        result = saturate_curve(
            db,
            curve_id,
            max_prime=int(config.get("max_prime") or 100),
            timeout=int(config.get("timeout") or 300),
            source=f"pipeline:{run['id']}",
        )
        return _safe(result), None, False

    if stage_id == "upper_bound_rescue_ladder":
        result = _run_upper_bound_rescue_ladder(
            db,
            run=run,
            run_config=run_config,
            target=target,
            context=context,
            config=config,
            stage_index=stage_index,
        )
        return _safe(result), None, False

    if stage_id == "large_height_generator_hunt":
        result = _run_large_height_generator_hunt(
            db,
            run=run,
            run_config=run_config,
            target=target,
            context=context,
            config=config,
            stage_index=stage_index,
        )
        return _safe(result), None, False

    if stage_id == "record_breaker_lane":
        threshold_mode = str(config.get("minimum_rank_mode") or "fixed")
        if threshold_mode == "goal_minus_one":
            goal = max(1, int(run_config.get("target_rank") or 1))
            minimum_rank = max(1, goal - 1)
        else:
            minimum_rank = max(1, int(config.get("minimum_rank") or 25))
        entry_state = _strategy_research_state(
            db, curve_id, context["E"], witness_basis=True
        )
        lower = int(entry_state["rigorous_lower"])
        if lower < minimum_rank:
            return {
                "status": "skipped",
                "reason": "rigorous_lower_below_record_threshold",
                "rigorous_lower": lower,
                "rigorous_upper": entry_state.get("rigorous_upper"),
                "exact_rank": entry_state.get("exact_rank"),
                "witness_basis_complete": entry_state.get("witness_basis_complete"),
                "minimum_rank": minimum_rank,
                "minimum_rank_mode": threshold_mode,
                "heuristics_do_not_promote": True,
                "research_state_source": "curve_research_state",
            }, None, False
        nested = dict(config)
        nested.pop("minimum_rank", None)
        nested.pop("minimum_rank_mode", None)
        nested["_strategy_id"] = "record_breaker_lane"
        result = _run_large_height_generator_hunt(
            db,
            run=run,
            run_config=run_config,
            target=target,
            context=context,
            config=nested,
            stage_index=stage_index,
        )
        result = {
            **_safe(result),
            "record_breaker_lane": True,
            "minimum_rank": minimum_rank,
            "minimum_rank_mode": threshold_mode,
            "entry_rigorous_lower": lower,
            "entry_rigorous_upper": entry_state.get("rigorous_upper"),
            "entry_exact_rank": entry_state.get("exact_rank"),
            "entry_witness_basis_complete": entry_state.get("witness_basis_complete"),
            "research_state_source": "curve_research_state",
        }
        return result, None, False

    if stage_id == "full_saturation_index_recovery":
        result = saturation_index_recovery(
            db,
            context=context,
            config=config,
            run_id=run["id"],
            stage_index=stage_index,
            certificate_timeout=int(
                config.get("certificate_timeout")
                or run_config.get("certificate_timeout")
                or 120
            ),
            exact_candidates=int(
                config.get("exact_candidates")
                or run_config.get("exact_candidates")
                or 96
            ),
        )
        return _safe(result), None, False

    if stage_id == "height_lattice_reduction":
        result = height_lattice_reduction(
            db,
            context=context,
            config=config,
            run_id=run["id"],
            stage_index=stage_index,
            certificate_timeout=int(
                config.get("certificate_timeout")
                or run_config.get("certificate_timeout")
                or 120
            ),
            exact_candidates=int(
                config.get("exact_candidates")
                or run_config.get("exact_candidates")
                or 96
            ),
        )
        return _safe(result), None, False

    if stage_id == "final_upper":
        result = _try_pari_upper(
            db,
            curve_id,
            E,
            int(config.get("timeout") or 30),
            phase=f"pipeline-{run['id']}-final-upper",
        )
        attempt_status = str(result.get("attempt_status") or "inconclusive")
        common = {
            **_safe(result),
            "attempt_outcome": attempt_status,
            "effective_upper": result.get("effective_rigorous_upper"),
            "effective_upper_source": result.get("upper_source"),
            "budget_class": "final_proof",
        }
        if attempt_status == "completed":
            return {
                **common,
                "status": "completed",
            }, None, False
        if attempt_status == "timeout":
            return {
                **common,
                "status": "timeout",
                "reason": "final_upper_timeout",
            }, None, False
        if attempt_status == "error":
            return {
                **common,
                "status": "error",
                "reason": "final_upper_error",
            }, None, False
        return {
            **common,
            "status": "inconclusive",
            "reason": "final_upper_inconclusive",
        }, None, False

    if stage_id == "stop_goal":
        snap = _snapshot(db, curve_id)
        lower = int(snap["rigorous_lower"])
        inconsistent = bool(snap.get("rank_inconsistent", False))
        if bool(run_config.get("record_breaker_mode", False)):
            hit = _record_breaker_goal_hit(run_config, snap)
        else:
            hit = not inconsistent and lower >= target_rank
        return {
            "status": "completed",
            "rigorous_lower": lower,
            "rigorous_upper": snap.get("rigorous_upper"),
            "rank_inconsistent": inconsistent,
            "rigorous_witnesses": int(snap.get("rigorous_witnesses") or 0),
            "target_rank": target_rank,
            "goal_reached": bool(hit),
        }, None, bool(hit)

    raise ValueError(f"unsupported runtime pipeline stage {stage_id!r}")



def _uses_population_scheduler(stages):
    return any(rec["id"] in POPULATION_STAGE_IDS for rec in stages)


def _item_key(item):
    return (str(item.get("provider_key") or ""), str(item["candidate"]["parameter"]))


def _row_key(row):
    return (str(row["provider_key"] or ""), str(row["parameter"]))


def _stage_results(row):
    try:
        value = json.loads(row["stage_results_json"] or "{}")
    except Exception:
        value = {}
    return value if isinstance(value, dict) else {}


def _stage_entry(row, stage_index, stage_id):
    entry = _stage_results(row).get(str(stage_index))
    if not isinstance(entry, dict) or entry.get("stage_id") != stage_id:
        return None
    result = entry.get("result")
    return result if isinstance(result, dict) else {}


def _resume_stage_policy(stage_id, prior_result, config):
    """Return (skip, effective_config, reason) for a prior bounded outcome."""
    cfg = dict(config or {})
    prior = dict(prior_result or {})
    status = str(prior.get("status") or "")
    bounded_retry_defaults = {
        "selmer_bound": 60,
        "mwrank_covering": 90,
        "higher_descent_ladder": 120,
        "selmer_element_fanout": 30,
        "plugin_geometry": 180,
        "full_saturation_index_recovery": 180,
        "height_lattice_reduction": 300,
        "large_height_generator_hunt": 1800,
        "record_breaker_lane": 7200,
    }
    if (
        stage_id not in bounded_retry_defaults
        or status not in {
            "timeout", "inconclusive", "error", "partial", "unsupported"
        }
    ):
        return False, cfg, None

    if (
        stage_id == "height_lattice_reduction"
        and status == "partial"
        and str(prior.get("lattice_status") or "") == "completed"
        and str(prior.get("certification_status") or "") != "completed"
    ):
        return True, cfg, "certificate_retry_requires_explicit_rerun"

    policy = str(
        prior.get("retry_policy")
        or cfg.get("retry_policy")
        or "manual"
    )
    covering_derivation_retry = (
        stage_id == "selmer_element_fanout"
        and str(prior.get("reason") or "") == "covering_derivation_timeout"
    )
    strategy_retry = stage_id in {"large_height_generator_hunt", "record_breaker_lane"}
    initial_timeout = max(
        1,
        int(
            (
                cfg.get("wall_timeout")
                if strategy_retry
                else (cfg.get("derive_timeout") if covering_derivation_retry else cfg.get("timeout"))
            )
            or bounded_retry_defaults[stage_id]
        ),
    )
    retry_timeout = max(
        1,
        int(cfg.get("retry_timeout") or max(300, initial_timeout)),
    )
    attempt_timeout = max(
        1,
        int(prior.get("attempt_timeout") or initial_timeout),
    )
    next_attempt = max(1, int(prior.get("attempt_number") or 1)) + 1

    if policy == "manual":
        return True, cfg, "manual"
    if policy == "automatic":
        cfg["_attempt_number"] = next_attempt
        return False, cfg, "automatic"
    if policy == "escalated":
        if attempt_timeout >= retry_timeout:
            return True, cfg, "escalated_budget_already_attempted"
        if strategy_retry:
            cfg["wall_timeout"] = retry_timeout
        elif covering_derivation_retry:
            cfg["derive_timeout"] = retry_timeout
        else:
            cfg["timeout"] = retry_timeout
        cfg["_attempt_number"] = next_attempt
        return False, cfg, "escalated"
    return True, cfg, "invalid_retry_policy"


def _write_stage_result(
    db,
    row,
    *,
    stage_index,
    stage_id,
    result,
    status=None,
    current=True,
    snapshot=None,
    curve_id=None,
    commit=True,
):
    results = _stage_results(row)
    results[str(stage_index)] = {
        "stage_id": str(stage_id),
        "result": _safe(result),
    }
    fields = {
        "stage_results_json": results,
        "current_stage_index": int(stage_index) if current else None,
        "current_stage_id": str(stage_id) if current else None,
    }
    if status is not None:
        fields["status"] = str(status)
    if curve_id is not None:
        fields["curve_id"] = int(curve_id)
    if snapshot is not None:
        fields.update(
            rigorous_lower=int(snapshot["rigorous_lower"] or 0),
            rigorous_upper=snapshot["rigorous_upper"],
            exact_rank=snapshot["exact_rank"],
        )
    update_pipeline_candidate(
        db,
        int(row["id"]),
        commit=bool(commit),
        **fields,
    )


def _write_population_stage_batch(db, writes):
    """Persist one population-stage decision set atomically.

    Selection rows are the durable resume record. A partial write must never be
    visible, because resume treats any recorded selection for the stage/round
    as a previously committed funnel.
    """
    writes = list(writes or [])
    if not writes:
        return
    savepoint = "rh_population_stage_batch"
    db.execute(f"SAVEPOINT {savepoint}")
    try:
        for write in writes:
            _write_stage_result(db, commit=False, **write)
    except Exception:
        db.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        db.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    db.execute(f"RELEASE SAVEPOINT {savepoint}")


def _pipeline_score(row):
    score = None if row["score"] is None else float(row["score"])
    results = _stage_results(row)
    for key in sorted(results, key=lambda value: int(value) if str(value).isdigit() else -1):
        entry = results.get(key)
        if not isinstance(entry, dict):
            continue
        result = entry.get("result")
        if not isinstance(result, dict) or result.get("pipeline_score") is None:
            continue
        try:
            score = float(result["pipeline_score"])
        except Exception:
            continue
    return -1e99 if score is None else score


def _exact_point_inventory(db, row):
    """Return total exact-verified point inventory for ranking, not run-local yield."""
    curve_id = row["curve_id"]
    if curve_id is None:
        return 0
    found = db.execute(
        "SELECT COUNT(*) AS n FROM points WHERE curve_id=? AND exact_verified=1",
        (int(curve_id),),
    ).fetchone()
    return int(found["n"] or 0) if found is not None else 0


def _rank_population_rows(db, rows, ranking):
    ranking = str(ranking or "rank_then_yield_then_score")
    enriched = []
    for row in rows:
        score = _pipeline_score(row)
        lower = int(row["rigorous_lower"] or 0)
        exact_point_inventory = _exact_point_inventory(db, row)
        if ranking == "score":
            key = (-score, -lower, -exact_point_inventory, int(row["id"]))
        elif ranking == "rank_then_score":
            key = (-lower, -score, -exact_point_inventory, int(row["id"]))
        else:
            key = (-lower, -exact_point_inventory, -score, int(row["id"]))
        enriched.append((key, row, exact_point_inventory))
    enriched.sort(key=lambda rec: rec[0])
    return [
        (row, exact_point_inventory)
        for _key, row, exact_point_inventory in enriched
    ]


def _queue_population_items(db, run_id, items):
    queued = 0
    existing = 0
    for item in items:
        candidate = item["candidate"]
        provider_key = str(item.get("provider_key") or "")
        if get_pipeline_candidate(
            db, run_id, provider_key, candidate["parameter"]
        ) is None:
            upsert_pipeline_candidate(
                db,
                run_id=run_id,
                provider_key=provider_key,
                parameter=candidate["parameter"],
                score=candidate.get("score"),
                score_provenance=candidate.get("score_provenance"),
                curve_id=candidate.get("curve_id"),
                status="queued",
            )
            queued += 1
        else:
            existing += 1
    print(
        f"[pipeline queue] total={len(items)} existing={existing} new={queued}",
        flush=True,
    )


def _prepare_family_population_items(
    db,
    *,
    run,
    run_config,
    stages,
    target,
    plugin,
    variant,
    provider_key="",
):
    print(
        f"[pipeline setup] loading family {plugin.id}/{variant.id}",
        flush=True,
    )
    family = load_family(variant.family_spec)
    print(
        f"[pipeline setup] family loaded · preparing candidate pool",
        flush=True,
    )
    fingerprints = plugin_fingerprints(plugin, variant)
    candidates = _family_candidates(
        db,
        run_config["project_root"],
        run["id"],
        plugin,
        variant,
        stages,
        run_config,
        target=target,
    )
    items = [
        {
            "provider_key": str(provider_key or ""),
            "candidate": candidate,
            "plugin": plugin,
            "variant": variant,
            "family": family,
            "fingerprints": fingerprints,
        }
        for candidate in candidates
    ]
    print(
        f"[pipeline resume] loaded {len(items)} family candidates "
        f"for {plugin.id}/{variant.id}",
        flush=True,
    )
    return items


def _prepare_general_population_items(db, *, run, stages, target):
    candidates = _general_candidates(target, stages)
    items = [
        {
            "provider_key": "general",
            "candidate": candidate,
            "plugin": None,
            "variant": None,
            "family": None,
            "fingerprints": {},
        }
        for candidate in candidates
    ]
    print(f"[pipeline resume] loaded {len(items)} general candidates", flush=True)
    return items


def _materialize_population_context(db, *, run, item, cache):
    key = _item_key(item)
    if key in cache:
        return cache[key]["context"]

    candidate = item["candidate"]
    parameter = str(candidate["parameter"])
    score = float(candidate.get("score") or 0.0)
    direct_curve_id = candidate.get("curve_id")
    family = item.get("family")
    existing_pipeline_row = get_pipeline_candidate(
        db, run["id"], item.get("provider_key") or "", parameter
    )
    if (
        direct_curve_id is None
        and family is not None
        and existing_pipeline_row is not None
        and existing_pipeline_row["curve_id"] is not None
    ):
        stored = get_curve(db, int(existing_pipeline_row["curve_id"]))
        if stored is not None and stored["a_invariants_json"]:
            direct_curve_id = int(existing_pipeline_row["curve_id"])
            candidate["curve_id"] = direct_curve_id
            candidate["_resume_family_curve"] = True

    if direct_curve_id is not None:
        stored = get_curve(db, int(direct_curve_id))
        if stored is None or not stored["a_invariants_json"]:
            raise ValueError(f"stored curve #{direct_curve_id} is unavailable")
        E = EllipticCurve(
            QQ,
            [QQ(str(x)) for x in json.loads(stored["a_invariants_json"])],
        )
        curve_id = int(direct_curve_id)
        source_E = None if candidate.get("_resume_family_curve") else E
        created = bool(candidate.get("created_by_pipeline", False))
    elif family is not None:
        curve_id, E, source_E, created = _stored_curve(
            db,
            plugin=item["plugin"],
            variant=item["variant"],
            family=family,
            parameter=parameter,
            score=score,
            fingerprints=item.get("fingerprints") or {},
        )
        if curve_id is None:
            row = get_pipeline_candidate(
                db, run["id"], item.get("provider_key") or "", parameter
            )
            if row is not None:
                update_pipeline_candidate(
                    db,
                    int(row["id"]),
                    status="filtered",
                    current_stage_index=None,
                    current_stage_id=None,
                    error="singular specialization",
                )
            return None
    else:
        curve_id, E, source_E, created = _materialize_general(db, candidate)

    context = {
        "pipeline_run_id": int(run["id"]),
        "curve_id": int(curve_id),
        "E": E,
        "source_E": source_E,
        "family": family,
        "plugin": item.get("plugin"),
        "variant": item.get("variant"),
        "parameter": parameter,
        "score": score,
        "rank_order": int(candidate.get("rank_order") or 0),
        "source_candidate_id": candidate.get("candidate_id"),
    }
    constructed_points = list(candidate.get("constructed_points") or [])
    invalid_constructed_point = None
    verified_constructed_points = []
    for point_index, raw_point in enumerate(constructed_points, 1):
        try:
            if not isinstance(raw_point, (list, tuple)) or len(raw_point) < 2:
                raise ValueError("constructed point must be [x,y]")
            P = E(QQ(str(raw_point[0])), QQ(str(raw_point[1])))
            if P.is_zero():
                raise ValueError("constructed point is infinity")
        except Exception as exc:
            invalid_constructed_point = (
                f"constructed point #{point_index} failed exact verification: {exc}"
            )
            break
        verified_constructed_points.append((point_index, P))
    row = get_pipeline_candidate(
        db, run["id"], item.get("provider_key") or "", parameter
    )
    if row is not None:
        context["pipeline_candidate_id"] = int(row["id"])
    if invalid_constructed_point is not None:
        if row is not None:
            update_pipeline_candidate(
                db,
                int(row["id"]),
                curve_id=int(curve_id),
                status="filtered",
                current_stage_index=None,
                current_stage_id=None,
                error=invalid_constructed_point,
            )
        return None
    for point_index, P in verified_constructed_points:
        upsert_point(
            db,
            curve_id=int(curve_id),
            x=P[0],
            y=P[1],
            source="pipeline_square_condition_specializer",
            role="candidate_extra",
            exact_verified=True,
            independence_status="unknown",
            rigorous_independent=False,
            search_ref=f"pipeline:{run['id']}:square-condition",
            metadata={
                "constructed_point_index": point_index,
                "exact_square_condition": True,
            },
        )
    snap = _snapshot(db, curve_id)
    if row is not None:
        update_pipeline_candidate(
            db,
            int(row["id"]),
            curve_id=int(curve_id),
            status="running",
            rigorous_lower=snap["rigorous_lower"],
            rigorous_upper=snap["rigorous_upper"],
            exact_rank=snap["exact_rank"],
            error=None,
        )
    cache[key] = {
        "context": context,
        "created": bool(created),
    }
    return context


def _apply_builder_retention(db, *, curve_id, created, run_config):
    """Apply Builder inventory retention to one run-created curve.

    Pipeline candidate/run history is durable even if the fresh curve is not
    retained in Curves. Preexisting curves are never removed.
    """
    if not created:
        return False
    floor = max(0, int(run_config.get("retention_floor") or 0))
    snap = _snapshot(db, int(curve_id))
    if floor > 0:
        if int(snap["rigorous_lower"] or 0) >= floor:
            return False
        _discard_unpromoted_auto_curve(db, int(curve_id))
        return True

    if int(snap["rigorous_lower"] or 0) > 0:
        return False
    return bool(discard_transient_zero_evidence_curve(db, int(curve_id)))


def _cleanup_population_contexts(db, *, run, run_config, cache):
    # Canonical transform identity allows multiple path-specific candidates to
    # share one scientific curve row. Retention therefore acts once per curve
    # and clears every cached candidate reference if that row is discarded.
    by_curve = {}
    for key, cached in list(cache.items()):
        if not cached:
            continue
        curve_id = int(cached["context"]["curve_id"])
        group = by_curve.setdefault(
            curve_id,
            {"created": False, "candidate_keys": []},
        )
        group["created"] = bool(group["created"] or cached.get("created"))
        group["candidate_keys"].append(key)

    for curve_id, group in by_curve.items():
        discarded = _apply_builder_retention(
            db,
            curve_id=curve_id,
            created=bool(group["created"]),
            run_config=run_config,
        )
        if not discarded:
            continue
        for provider_key, parameter in group["candidate_keys"]:
            row = get_pipeline_candidate(
                db,
                run["id"],
                provider_key,
                parameter,
            )
            if row is not None:
                update_pipeline_candidate(
                    db, int(row["id"]), curve_id=None
                )



def _derived_item_from_lineage(db, *, run_config, row):
    try:
        metadata = json.loads(row["metadata_json"] or "{}")
    except Exception:
        metadata = {}
    child_kind = str(metadata.get("child_kind") or "direct_curve")
    candidate = {
        "parameter": str(row["child_parameter"]),
        "score": metadata.get("child_score"),
        "rank_order": 0,
        "created_by_pipeline": bool(metadata.get("created_by_pipeline", False)),
        "constructed_points": list(metadata.get("constructed_points") or []),
    }
    if candidate["score"] is None:
        child_row = get_pipeline_candidate(
            db,
            int(row["run_id"]),
            str(row["child_provider_key"] or ""),
            str(row["child_parameter"]),
        )
        candidate["score"] = None if child_row is None else child_row["score"]

    if child_kind in {"family_parameter", "family_reference"}:
        plugin_id = metadata.get("plugin_id")
        variant_id = metadata.get("variant_id")
        if not plugin_id:
            return None
        plugin = get_plugin(run_config["project_root"], plugin_id)
        variant = get_variant(plugin, variant_id)
        family = load_family(variant.family_spec)
        return {
            "provider_key": str(row["child_provider_key"] or ""),
            "candidate": candidate,
            "plugin": plugin,
            "variant": variant,
            "family": family,
            "fingerprints": plugin_fingerprints(plugin, variant),
        }

    if row["child_curve_id"] is None:
        return None
    candidate["curve_id"] = int(row["child_curve_id"])
    return {
        "provider_key": str(row["child_provider_key"] or ""),
        "candidate": candidate,
        "plugin": None,
        "variant": None,
        "family": None,
        "fingerprints": {},
    }


def _restore_derived_population_items(db, *, run, run_config, items):
    out = list(items)
    seen = {_item_key(item) for item in out}
    for row in pipeline_derivations(db, run["id"], limit=1000000):
        item = _derived_item_from_lineage(db, run_config=run_config, row=row)
        if item is None:
            continue
        key = _item_key(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _child_item_from_transform(parent_item, stage_id, child, run_config):
    kind = str(child.get("kind") or "direct_curve")
    parent_provider = str(parent_item.get("provider_key") or "")
    score = child.get("score")
    if kind == "family_parameter":
        return {
            "provider_key": parent_provider,
            "candidate": {
                "parameter": str(child["parameter"]),
                "score": score,
                "rank_order": 0,
                "constructed_points": list(child.get("constructed_points") or []),
            },
            "plugin": parent_item.get("plugin"),
            "variant": parent_item.get("variant"),
            "family": parent_item.get("family"),
            "fingerprints": parent_item.get("fingerprints") or {},
        }
    if kind == "family_reference":
        plugin = get_plugin(run_config["project_root"], str(child["plugin_id"]))
        variant = get_variant(plugin, child.get("variant_id"))
        family = load_family(variant.family_spec)
        provider_key = f"{plugin.id}:{variant.id}|{stage_id}"
        return {
            "provider_key": provider_key,
            "candidate": {
                "parameter": str(child["parameter"]),
                "score": score,
                "rank_order": 0,
                "constructed_points": list(child.get("constructed_points") or []),
            },
            "plugin": plugin,
            "variant": variant,
            "family": family,
            "fingerprints": plugin_fingerprints(plugin, variant),
        }

    provider_key = (
        f"{parent_provider}|{stage_id}"
        if parent_provider
        else str(stage_id)
    )
    return {
        "provider_key": provider_key,
        "candidate": {
            "curve_id": int(child["curve_id"]),
            "parameter": str(child["parameter"]),
            "score": score,
            "rank_order": 0,
            "created_by_pipeline": bool(child.get("created_by_pipeline", False)),
            "constructed_points": list(child.get("constructed_points") or []),
        },
        "plugin": None,
        "variant": None,
        "family": None,
        "fingerprints": {},
    }


def _transform_population(
    db,
    *,
    run,
    run_config,
    active_items,
    stage_index,
    stage_id,
    config,
    cache,
):
    next_items = []
    seen = set()
    include_parent = bool(config.get("include_parent", False))
    max_children = max(1, int(config.get("max_children") or 1000000))

    for parent_item in active_items:
        parent_key = _item_key(parent_item)
        parent_row = get_pipeline_candidate(
            db,
            run["id"],
            parent_item.get("provider_key") or "",
            parent_item["candidate"]["parameter"],
        )
        if parent_row is None:
            continue

        prior = _stage_entry(parent_row, stage_index, stage_id)
        lineage_rows = pipeline_derivations(
            db,
            run["id"],
            parent_candidate_id=int(parent_row["id"]),
            stage_index=stage_index,
            limit=100000,
        )
        prior_status = (
            None if prior is None else str(prior.get("status") or "")
        )
        resumable_constructive = bool(
            stage_id == "constructive_rank_jump_loop"
            and prior is not None
            and prior.get("strategy_resume_token")
            and prior_status in {"partial", "inconclusive", "error"}
        )
        if (
            prior is not None
            and not resumable_constructive
            and prior_status in {
                "completed", "skipped", "error", "inconclusive", "partial"
            }
        ):
            prior_include_parent = bool(
                prior.get("include_parent", include_parent)
            )
            for lineage in lineage_rows:
                child_item = _derived_item_from_lineage(
                    db,
                    run_config=run_config,
                    row=lineage,
                )
                if child_item is None:
                    continue
                child_key = _item_key(child_item)
                child_row = get_pipeline_candidate(
                    db,
                    run["id"],
                    child_item.get("provider_key") or "",
                    child_item["candidate"]["parameter"],
                )
                if (
                    child_key not in seen
                    and child_row is not None
                    and str(child_row["status"] or "") not in TERMINAL_CANDIDATE_STATES
                ):
                    seen.add(child_key)
                    next_items.append(child_item)
            if prior_include_parent and parent_key not in seen:
                seen.add(parent_key)
                next_items.append(parent_item)
            continue

        context = _materialize_population_context(
            db,
            run=run,
            item=parent_item,
            cache=cache,
        )
        if context is None:
            continue

        if stage_id == "square_condition_specializer":
            context = dict(context)
            constructive_context = dict(context)
            constructive_context["pipeline_candidate_id"] = int(
                parent_row["id"]
            )
            context["constructive_state"] = {
                sid: _constructive_prior_stage_result(
                    db,
                    run_id=run["id"],
                    context=constructive_context,
                    before_stage_index=stage_index,
                    stage_id=sid,
                )
                for sid in (
                    "section_height_shell",
                    "trace_section_constructor",
                    "forced_bisection_constructor",
                )
            }

        context = dict(context)
        context["pipeline_stage_index"] = int(stage_index)
        context["pipeline_stage_id"] = str(stage_id)

        try:
            derived = derive_transform_children(
                stage_id,
                db,
                context=context,
                config=config,
            )
        except Exception as exc:
            derived = {
                "status": "error",
                "transform_kind": stage_id,
                "children": [],
                "error": repr(exc),
            }
            print(
                f"[pipeline transform] {stage_id} "
                f"parent={context['parameter']} error={exc!r}",
                flush=True,
            )

        raw_children = list(derived.get("children") or [])[:max_children]
        child_refs = []
        materialization_failures = []
        for child_index, child in enumerate(raw_children, 1):
            try:
                child_item = _child_item_from_transform(
                    parent_item, stage_id, child, run_config
                )
                if _item_key(child_item) == parent_key:
                    continue
                _queue_population_items(db, run["id"], [child_item])
                child_row = get_pipeline_candidate(
                    db,
                    run["id"],
                    child_item.get("provider_key") or "",
                    child_item["candidate"]["parameter"],
                )
                if child_row is None:
                    raise RuntimeError("derived child candidate was not queued")
            except Exception as exc:
                if len(materialization_failures) < 5:
                    materialization_failures.append({
                        "child_index": int(child_index),
                        "child_kind": str(
                            child.get("kind") or "direct_curve"
                        ) if isinstance(child, dict) else None,
                        "error_class": type(exc).__name__,
                        "error": str(exc)[:240],
                    })
                continue

            metadata = dict(child.get("metadata") or {})
            metadata.update({
                "child_kind": str(child.get("kind") or "direct_curve"),
                "child_score": child.get("score"),
                "parent_parameter": str(context["parameter"]),
                "parent_curve_id": int(context["curve_id"]),
                "created_by_pipeline": bool(child.get("created_by_pipeline", False)),
                "constructed_points": list(child.get("constructed_points") or []),
            })
            if child.get("kind") == "family_parameter":
                plugin = parent_item.get("plugin")
                variant = parent_item.get("variant")
                if plugin is not None:
                    metadata["plugin_id"] = plugin.id
                if variant is not None:
                    metadata["variant_id"] = variant.id
            elif child.get("kind") == "family_reference":
                metadata["plugin_id"] = str(child["plugin_id"])
                if child.get("variant_id") is not None:
                    metadata["variant_id"] = str(child["variant_id"])

            record_pipeline_derivation(
                db,
                run_id=run["id"],
                stage_index=stage_index,
                stage_id=stage_id,
                parent_candidate_id=int(parent_row["id"]),
                child_candidate_id=int(child_row["id"]),
                transform_kind=str(
                    derived.get("transform_kind") or stage_id
                ),
                metadata=metadata,
            )
            child_refs.append({
                "candidate_id": int(child_row["id"]),
                "provider_key": str(child_row["provider_key"] or ""),
                "parameter": str(child_row["parameter"]),
                "curve_id": (
                    None if child_row["curve_id"] is None
                    else int(child_row["curve_id"])
                ),
            })
            child_key = _item_key(child_item)
            if (
                child_key not in seen
                and str(child_row["status"] or "") not in TERMINAL_CANDIDATE_STATES
            ):
                seen.add(child_key)
                next_items.append(child_item)

        transform_status = str(derived.get("status") or "completed")
        if materialization_failures:
            if transform_status == "completed":
                transform_status = "partial" if child_refs else "error"
            elif transform_status not in {
                "partial", "error", "timeout", "inconclusive", "unsupported",
                "skipped"
            }:
                transform_status = "partial" if child_refs else "error"
        effective_include_parent = bool(
            include_parent
            or transform_status in {
                "skipped", "error", "inconclusive", "partial", "timeout",
                "unsupported"
            }
        )
        result = {
            key: value
            for key, value in derived.items()
            if key != "children"
        }
        result.update({
            "status": transform_status,
            "children_created": len(child_refs),
            "children": child_refs,
            "child_materialization_failures": len(materialization_failures),
            "child_materialization_failure_samples": materialization_failures,
            "include_parent": effective_include_parent,
            "lineage_persisted": True,
        })
        snapshot = _snapshot(db, int(context["curve_id"]))
        _write_stage_result(
            db,
            parent_row,
            stage_index=stage_index,
            stage_id=stage_id,
            result=result,
            status="running" if effective_include_parent else "transformed",
            current=False,
            snapshot=snapshot,
            curve_id=int(context["curve_id"]),
        )
        print(
            f"[pipeline transform] {stage_id} parent={context['parameter']} "
            f"children={len(child_refs)} include_parent={effective_include_parent}",
            flush=True,
        )
        if effective_include_parent and parent_key not in seen:
            seen.add(parent_key)
            next_items.append(parent_item)

    return next_items



def _select_survivors_population(
    db,
    *,
    run,
    active_items,
    stage_index,
    config,
):
    stage_id = "select_survivors"
    all_rows = pipeline_candidates(db, run["id"], limit=1000000)
    recorded = [
        row for row in all_rows
        if _stage_entry(row, stage_index, stage_id) is not None
    ]
    if recorded:
        selected_keys = {
            _row_key(row)
            for row in recorded
            if bool(_stage_entry(row, stage_index, stage_id).get("selected"))
        }
        print(
            f"[pipeline funnel] resume step={stage_index} selected={len(selected_keys)}",
            flush=True,
        )
        return [item for item in active_items if _item_key(item) in selected_keys]

    rows = []
    item_by_key = {_item_key(item): item for item in active_items}
    for item in active_items:
        row = get_pipeline_candidate(
            db,
            run["id"],
            item.get("provider_key") or "",
            item["candidate"]["parameter"],
        )
        if row is not None and str(row["status"] or "") not in TERMINAL_CANDIDATE_STATES:
            rows.append(row)

    keep = max(1, int(config.get("keep") or 10))
    ranking = str(config.get("ranking") or "rank_then_yield_then_score")
    ranked = _rank_population_rows(db, rows, ranking)
    selected_ids = {int(row["id"]) for row, _yield in ranked[:keep]}
    rank_by_id = {
        int(row["id"]): position
        for position, (row, _yield) in enumerate(ranked, 1)
    }
    inventory_by_id = {
        int(row["id"]): exact_point_inventory
        for row, exact_point_inventory in ranked
    }

    selection_writes = []
    for row in rows:
        selected = int(row["id"]) in selected_ids
        result = {
            "status": "completed",
            "selected": bool(selected),
            "selection_rank": rank_by_id[int(row["id"])],
            "keep": keep,
            "ranking": ranking,
            "rigorous_lower": int(row["rigorous_lower"] or 0),
            "exact_point_inventory": int(inventory_by_id[int(row["id"])]),
            "score": _pipeline_score(row),
            "candidate_score": None if row["score"] is None else float(row["score"]),
        }
        selection_writes.append({
            "row": row,
            "stage_index": stage_index,
            "stage_id": stage_id,
            "result": result,
            "status": "running" if selected else "funnel_pruned",
            "current": selected,
        })
    _write_population_stage_batch(db, selection_writes)

    selected_keys = {
        _row_key(row)
        for row in rows
        if int(row["id"]) in selected_ids
    }
    print(
        f"[pipeline funnel] keep={min(keep, len(rows))}/{len(rows)} ranking={ranking}",
        flush=True,
    )
    return [
        item for item in active_items
        if _item_key(item) in selected_keys
    ]


def _point_search_outcome(payload):
    """Compatibility wrapper around the shared point-search outcome reducer."""
    return point_search_outcome(payload)


def _point_search_stage_payload(payload):
    """Attach one honest Pipeline status/outcome/coverage contract."""
    out = dict(_safe(payload or {}))
    outcome = point_search_outcome(out)
    coverage = out.get("search_coverage")
    if not isinstance(coverage, dict):
        coverage = point_search_coverage(out)
    out["status"] = outcome
    out["search_outcome"] = outcome
    out["search_coverage"] = coverage
    out["search_complete"] = outcome == "completed"
    return out


def _pipeline_point_checkpoint(run, context, stage_index, stage_id):
    candidate_id = context.get("pipeline_candidate_id")
    if candidate_id is None:
        return None
    return {
        "run_id": int(run["id"]),
        "candidate_id": int(candidate_id),
        "stage_index": int(stage_index),
        "stage_id": str(stage_id),
    }


def _bounded_stage_attempt_identity(
    run, context, stage_index, stage_id, engine
):
    """Stable durable identity for one candidate/stage bounded engine."""
    candidate_id = context.get("pipeline_candidate_id")
    candidate_token = (
        f"candidate:{int(candidate_id)}"
        if candidate_id is not None
        else f"parameter:{context.get('parameter')}"
    )
    return (
        f"pipeline:{int(run['id'])}:{candidate_token}:"
        f"stage:{int(stage_index)}:{stage_id}:{engine}"
    )


def _point_retry_options(config, timeout):
    policy = str(config.get("retry_policy") or "manual")
    retry_timeout = max(
        1,
        int(config.get("retry_timeout") or max(1, int(timeout))),
    )
    return policy, retry_timeout


def _adaptive_search_reusable(search):
    """Only reuse a denominator-band search whose method outcome is complete."""
    if not isinstance(search, dict):
        return False
    outcome = search.get("search_outcome")
    if outcome is not None:
        return str(outcome) == "completed"
    if any(
        key in search
        for key in ("completed_searches", "resume_skipped", "timeouts", "failures")
    ):
        return _point_search_outcome(search) == "completed"
    # Backward compatibility for old clean records that predate search_outcome.
    return str(search.get("status") or "") == "completed"


def _adaptive_result(row, stage_index):
    entry = _stage_entry(row, stage_index, "adaptive_ladder")
    if entry is None:
        return {"status": "running", "rounds": []}
    out = dict(entry)
    rounds = out.get("rounds")
    out["rounds"] = list(rounds) if isinstance(rounds, list) else []
    return out


def _adaptive_round(result, round_number):
    for rec in result.get("rounds") or []:
        if int(rec.get("round") or 0) == int(round_number):
            return rec
    return None


def _set_adaptive_round(result, round_record):
    rounds = [
        dict(rec)
        for rec in (result.get("rounds") or [])
        if int(rec.get("round") or 0) != int(round_record["round"])
    ]
    rounds.append(dict(round_record))
    rounds.sort(key=lambda rec: int(rec.get("round") or 0))
    result["rounds"] = rounds
    return result


def _adaptive_ladder_population(
    db,
    *,
    run,
    run_config,
    target,
    active_items,
    stage_index,
    config,
    cache,
):
    if not active_items:
        return []

    # A fully completed ladder is immutable on resume; downstream work must not
    # retroactively change who survived an earlier funnel.
    active_rows = [
        get_pipeline_candidate(
            db, run["id"], item.get("provider_key") or "", item["candidate"]["parameter"]
        )
        for item in active_items
    ]
    if active_rows and all(
        row is not None
        and str((_stage_entry(row, stage_index, "adaptive_ladder") or {}).get("status") or "")
        == "completed"
        for row in active_rows
    ):
        print(
            f"[pipeline adaptive] resume completed step={stage_index} survivors={len(active_items)}",
            flush=True,
        )
        return active_items

    bands = [
        (int(pair[0]), int(pair[1]))
        for pair in (config.get("bands") or [])
    ]
    keeps = [int(x) for x in (config.get("keeps") or [])]
    ranking = str(config.get("ranking") or "rank_then_yield_then_score")
    current = list(active_items)

    for round_number, ((low, high), keep) in enumerate(zip(bands, keeps), 1):
        if not current:
            break

        current_rows = [
            get_pipeline_candidate(
                db, run["id"], item.get("provider_key") or "", item["candidate"]["parameter"]
            )
            for item in current
        ]
        current_rows = [
            row for row in current_rows
            if row is not None and str(row["status"] or "") not in TERMINAL_CANDIDATE_STATES
        ]

        # Preserve a previously committed round selection.
        all_rows = pipeline_candidates(db, run["id"], limit=1000000)
        recorded_round = []
        for row in all_rows:
            result = _adaptive_result(row, stage_index)
            rec = _adaptive_round(result, round_number)
            if rec is not None:
                recorded_round.append((row, rec))
        if recorded_round:
            selected_keys = {
                _row_key(row)
                for row, rec in recorded_round
                if bool(rec.get("selected"))
            }
            current = [item for item in current if _item_key(item) in selected_keys]
            print(
                f"[pipeline adaptive] resume round={round_number} "
                f"band={low}-{high} selected={len(current)}",
                flush=True,
            )
        else:
            ranked = _rank_population_rows(db, current_rows, ranking)
            selected_ids = {int(row["id"]) for row, _yield in ranked[: max(1, keep)]}
            rank_by_id = {
                int(row["id"]): position
                for position, (row, _yield) in enumerate(ranked, 1)
            }
            inventory_by_id = {
                int(row["id"]): exact_point_inventory
                for row, exact_point_inventory in ranked
            }
            selected_keys = set()
            selection_writes = []
            for row in current_rows:
                selected = int(row["id"]) in selected_ids
                result = _adaptive_result(row, stage_index)
                result["status"] = "running" if selected else "pruned"
                result["selected_final"] = False
                if not selected:
                    result["pruned_at_round"] = round_number
                result = _set_adaptive_round(
                    result,
                    {
                        "round": round_number,
                        "band": [low, high],
                        "keep": int(keep),
                        "selected": bool(selected),
                        "selection_rank": rank_by_id[int(row["id"])],
                        "ranking": ranking,
                        "rigorous_lower_before": int(row["rigorous_lower"] or 0),
                        "exact_point_inventory_before": int(
                            inventory_by_id[int(row["id"])]
                        ),
                        "score": None if row["score"] is None else float(row["score"]),
                    },
                )
                selection_writes.append({
                    "row": row,
                    "stage_index": stage_index,
                    "stage_id": "adaptive_ladder",
                    "result": result,
                    "status": "running" if selected else "funnel_pruned",
                    "current": selected,
                })
                if selected:
                    selected_keys.add(_row_key(row))
            _write_population_stage_batch(db, selection_writes)
            current = [item for item in current if _item_key(item) in selected_keys]
            print(
                f"[pipeline adaptive] round={round_number} band={low}-{high} "
                f"keep={len(current)}/{len(current_rows)}",
                flush=True,
            )

        survivors = []
        for item in current:
            row = get_pipeline_candidate(
                db, run["id"], item.get("provider_key") or "", item["candidate"]["parameter"]
            )
            if row is None or str(row["status"] or "") in TERMINAL_CANDIDATE_STATES:
                continue
            result = _adaptive_result(row, stage_index)
            round_rec = _adaptive_round(result, round_number) or {
                "round": round_number,
                "band": [low, high],
                "keep": int(keep),
                "selected": True,
                "ranking": ranking,
            }
            search = round_rec.get("search")
            if _adaptive_search_reusable(search):
                survivors.append(item)
                continue

            context = _materialize_population_context(
                db, run=run, item=item, cache=cache
            )
            if context is None:
                continue
            before = _snapshot(db, int(context["curve_id"]))
            search_timeout = max(1, int(config.get("timeout") or 8))
            retry_policy, retry_timeout = _point_retry_options(
                config, search_timeout
            )
            search_budget = {
                "timeout_seconds": int(search_timeout),
                "timeout_scope": "per_chart_height_ratpoints_call",
                "coverage": "bounded_screening",
                "exhaustive": False,
                "retry_policy": retry_policy,
                "retry_timeout_seconds": retry_timeout,
            }
            try:
                search_result = run_auto_point_tier(
                    db,
                    curve_id=int(context["curve_id"]),
                    tier=(
                        f"pipeline-adaptive-{run['id']}-{stage_index}-"
                        f"r{round_number}-{low}-{high}"
                    ),
                    heights=tuple(
                        int(x) for x in config.get("heights") or [10000, 100000]
                    ),
                    chart_budget=max(1, int(config.get("charts") or 5)),
                    timeout=search_timeout,
                    executable=run_config.get("ratpoints") or None,
                    certificate_timeout=int(
                        config.get("certificate_timeout")
                        or run_config.get("certificate_timeout")
                        or 120
                    ),
                    exact_candidates=int(
                        config.get("exact_candidates")
                        or run_config.get("exact_candidates")
                        or 64
                    ),
                    native_only=False,
                    minimal_model=False,
                    denominator_low=low,
                    denominator_high=high,
                    pipeline_checkpoint=_pipeline_point_checkpoint(
                        run, context, stage_index, "adaptive_ladder"
                    ),
                    retry_policy=retry_policy,
                    retry_timeout=retry_timeout,
                )
                search_payload = _point_search_stage_payload(search_result)
                search_payload["search_budget"] = dict(search_budget)
            except Exception as exc:
                search_payload = {
                    "status": "error",
                    "search_outcome": "error",
                    "search_budget": dict(search_budget),
                    "error": repr(exc),
                }
                print(
                    f"[pipeline adaptive] band={low}-{high} "
                    f"candidate={item['candidate']['parameter']} error={exc!r}",
                    flush=True,
                )
            after = _snapshot(db, int(context["curve_id"]))
            round_rec["search"] = {
                "rigorous_lower_before": before["rigorous_lower"],
                "rigorous_lower_after": after["rigorous_lower"],
                **search_payload,
            }
            result = _set_adaptive_round(result, round_rec)
            result, record_breaker_stop = _record_breaker_annotate(
                result, run_config, after
            )
            _write_stage_result(
                db,
                row,
                stage_index=stage_index,
                stage_id="adaptive_ladder",
                result=result,
                status="completed" if record_breaker_stop else "running",
                current=not record_breaker_stop,
                snapshot=after,
                curve_id=int(context["curve_id"]),
            )
            survivors.append(item)
            if record_breaker_stop:
                progress = run_progress(db, run["id"])
                update_pipeline_run(
                    db,
                    run["id"],
                    status="target_hit",
                    candidates_total=progress["total"],
                    candidates_done=progress["done"],
                    best_lower=progress["best_lower"],
                    best_curve_id=progress["best_curve_id"],
                )
                print(
                    f"[record breaker] target reached during adaptive ladder "
                    f"curve={int(context['curve_id'])} rank>={after['rigorous_lower']}",
                    flush=True,
                )
                return survivors
        current = survivors

    for item in current:
        row = get_pipeline_candidate(
            db, run["id"], item.get("provider_key") or "", item["candidate"]["parameter"]
        )
        if row is None:
            continue
        result = _adaptive_result(row, stage_index)
        result["status"] = "completed"
        result["selected_final"] = True
        context = cache.get(_item_key(item), {}).get("context")
        snap = (
            _snapshot(db, int(context["curve_id"]))
            if context is not None
            else {
                "rigorous_lower": int(row["rigorous_lower"] or 0),
                "rigorous_upper": row["rigorous_upper"],
                "exact_rank": row["exact_rank"],
            }
        )
        _write_stage_result(
            db,
            row,
            stage_index=stage_index,
            stage_id="adaptive_ladder",
            result=result,
            status="running",
            current=False,
            snapshot=snap,
            curve_id=None if context is None else int(context["curve_id"]),
        )
    return current


def _run_population_pipeline(
    db,
    *,
    run,
    run_config,
    target,
    stages,
    items,
):
    print(
        f"[pipeline population] preparing {len(items)} source candidates",
        flush=True,
    )
    _queue_population_items(db, run["id"], items)
    items = _restore_derived_population_items(
        db,
        run=run,
        run_config=run_config,
        items=items,
    )
    progress = run_progress(db, run["id"])
    update_pipeline_run(
        db,
        run["id"],
        candidates_total=progress["total"],
        candidates_done=progress["done"],
    )
    active = []
    for item in items:
        row = get_pipeline_candidate(
            db, run["id"], item.get("provider_key") or "", item["candidate"]["parameter"]
        )
        if row is not None and str(row["status"] or "") not in TERMINAL_CANDIDATE_STATES:
            active.append(item)

    print(
        f"[pipeline population] active={len(active)} restored_total={len(items)}",
        flush=True,
    )
    cache = {}
    stopped_on_goal = False
    per_curve_records = _per_curve_stage_records(stages)
    for stage_position, (stage_index, rec) in enumerate(per_curve_records, 1):
        if not active:
            break
        stage_id = rec["id"]
        stage_label = stage_spec(stage_id).label
        stage_started = time.monotonic()
        print(
            f"[pipeline stage] {stage_position}/{len(per_curve_records)} "
            f"{stage_label} · active={len(active)}",
            flush=True,
        )
        update_pipeline_run(
            db,
            run["id"],
            current_stage_index=stage_index,
            current_stage_id=stage_id,
        )

        if stage_id in FANOUT_STAGE_IDS:
            active = _transform_population(
                db,
                run=run,
                run_config=run_config,
                active_items=active,
                stage_index=stage_index,
                stage_id=stage_id,
                config=rec["config"],
                cache=cache,
            )
            progress = run_progress(db, run["id"])
            update_pipeline_run(
                db,
                run["id"],
                candidates_total=progress["total"],
                candidates_done=progress["done"],
                best_lower=progress["best_lower"],
                best_curve_id=progress["best_curve_id"],
            )
            print(
                f"[pipeline stage done] {stage_label} · active={len(active)} "
                f"elapsed={time.monotonic() - stage_started:.1f}s",
                flush=True,
            )
            continue

        if stage_id == "select_survivors":
            active = _select_survivors_population(
                db,
                run=run,
                active_items=active,
                stage_index=stage_index,
                config=rec["config"],
            )
            progress = run_progress(db, run["id"])
            update_pipeline_run(
                db,
                run["id"],
                candidates_total=progress["total"],
                candidates_done=progress["done"],
                best_lower=progress["best_lower"],
                best_curve_id=progress["best_curve_id"],
            )
            continue

        if stage_id == "adaptive_ladder":
            active = _adaptive_ladder_population(
                db,
                run=run,
                run_config=run_config,
                target=target,
                active_items=active,
                stage_index=stage_index,
                config=rec["config"],
                cache=cache,
            )
            progress = run_progress(db, run["id"])
            latest_run = get_pipeline_run(db, run["id"])
            adaptive_target_hit = bool(
                latest_run is not None
                and str(latest_run["status"] or "") == "target_hit"
                and bool(run_config.get("record_breaker_mode", False))
            )
            update_pipeline_run(
                db,
                run["id"],
                candidates_total=progress["total"],
                candidates_done=progress["done"],
                best_lower=progress["best_lower"],
                best_curve_id=progress["best_curve_id"],
            )
            if adaptive_target_hit:
                stopped_on_goal = True
                break
            continue

        next_active = []
        active_total = len(active)
        for active_index, item in enumerate(active, 1):
            if active_index == 1 or active_index % 10 == 0 or active_index == active_total:
                print(
                    f"[pipeline stage] {stage_label} "
                    f"{active_index}/{active_total} "
                    f"t={item['candidate']['parameter']} "
                    f"elapsed={time.monotonic() - stage_started:.1f}s",
                    flush=True,
                )
            row = get_pipeline_candidate(
                db,
                run["id"],
                item.get("provider_key") or "",
                item["candidate"]["parameter"],
            )
            if row is None or str(row["status"] or "") in TERMINAL_CANDIDATE_STATES:
                continue

            prior = _stage_entry(row, stage_index, stage_id)
            prior_status = str((prior or {}).get("status") or "")
            if prior_status in {
                "completed", "skipped", "filtered", "constraint_filtered",
                "torsion_mismatch", "goal_filtered",
            }:
                if prior_status not in {
                    "filtered", "constraint_filtered", "torsion_mismatch",
                    "goal_filtered"
                }:
                    next_active.append(item)
                if (
                    stage_id == "stop_goal"
                    and bool((prior or {}).get("goal_reached"))
                ):
                    stopped_on_goal = True
                    break
                continue

            skip_retry, stage_config, retry_reason = _resume_stage_policy(
                stage_id, prior, rec["config"]
            )
            if skip_retry:
                print(
                    f"[pipeline resume] preserve prior {stage_id} "
                    f"outcome={prior_status} retry_policy={retry_reason}",
                    flush=True,
                )
                next_active.append(item)
                continue

            context = _materialize_population_context(
                db, run=run, item=item, cache=cache
            )
            if context is None:
                continue

            terminal_status = None
            should_stop = False
            try:
                result, terminal_status, should_stop = _stage_result(
                    stage_id,
                    db=db,
                    run=run,
                    run_config=run_config,
                    target=target,
                    context=context,
                    config=stage_config,
                    stage_index=stage_index,
                )
            except Exception as exc:
                result = {"status": "error", "error": repr(exc)}
                print(
                    f"[pipeline] stage {stage_id} inconclusive/error: {exc!r}",
                    flush=True,
                )
                if stage_id == "exact_torsion" and target.get("torsion_group") is not None:
                    terminal_status = "inconclusive"

            snap = _snapshot(db, int(context["curve_id"]))
            result, record_breaker_stop = _record_breaker_annotate(
                result, run_config, snap
            )
            if record_breaker_stop:
                should_stop = True
            candidate_status = (
                "completed"
                if record_breaker_stop and terminal_status is None
                else (terminal_status or "running")
            )
            _write_stage_result(
                db,
                row,
                stage_index=stage_index,
                stage_id=stage_id,
                result=result,
                status=candidate_status,
                current=terminal_status is None and not should_stop,
                snapshot=snap,
                curve_id=int(context["curve_id"]),
            )
            if terminal_status is None:
                next_active.append(item)
            if should_stop:
                stopped_on_goal = True
                break

        active = next_active
        print(
            f"[pipeline stage done] {stage_label} · survivors={len(active)} "
            f"elapsed={time.monotonic() - stage_started:.1f}s",
            flush=True,
        )
        progress = run_progress(db, run["id"])
        update_pipeline_run(
            db,
            run["id"],
            candidates_total=progress["total"],
            candidates_done=progress["done"],
            best_lower=progress["best_lower"],
            best_curve_id=progress["best_curve_id"],
        )
        if stopped_on_goal:
            break

    if not stopped_on_goal:
        for item in active:
            row = get_pipeline_candidate(
                db,
                run["id"],
                item.get("provider_key") or "",
                item["candidate"]["parameter"],
            )
            if row is not None and str(row["status"] or "") not in TERMINAL_CANDIDATE_STATES:
                update_pipeline_candidate(
                    db,
                    int(row["id"]),
                    status="completed",
                    current_stage_index=None,
                    current_stage_id=None,
                )

    _cleanup_population_contexts(
        db, run=run, run_config=run_config, cache=cache
    )
    for item in items:
        source_candidate_id = item["candidate"].get("candidate_id")
        if source_candidate_id is None:
            continue
        row = get_pipeline_candidate(
            db,
            run["id"],
            item.get("provider_key") or "",
            item["candidate"]["parameter"],
        )
        if row is None or str(row["status"] or "") not in TERMINAL_CANDIDATE_STATES:
            continue
        _mark_source_candidate_done(
            db,
            source_candidate_id,
            curve_id=row["curve_id"],
        )

    progress = run_progress(db, run["id"])
    update_pipeline_run(
        db,
        run["id"],
        candidates_total=progress["total"],
        candidates_done=progress["done"],
        best_lower=progress["best_lower"],
        best_curve_id=progress["best_curve_id"],
    )
    return stopped_on_goal



def _mark_source_candidate_done(db, candidate_id, curve_id=None):
    if candidate_id is None:
        return
    db.execute(
        """UPDATE candidates
           SET status='searched',curve_id=?,updated_at=?
           WHERE id=?""",
        (
            None if curve_id is None else int(curve_id),
            now(),
            int(candidate_id),
        ),
    )
    db.commit()


def _process_candidate(
    db,
    *,
    run,
    run_config,
    target,
    stages,
    provider_key,
    candidate,
    plugin=None,
    variant=None,
    family=None,
    fingerprints=None,
):
    parameter = str(candidate["parameter"])
    score = float(candidate.get("score") or 0.0)
    score_provenance = dict(candidate.get("score_provenance") or {})
    rank_order = int(candidate.get("rank_order") or 0)
    source_candidate_id = candidate.get("candidate_id")

    if family is not None:
        curve_id, E, source_E, created = _stored_curve(
            db,
            plugin=plugin,
            variant=variant,
            family=family,
            parameter=parameter,
            score=score,
            fingerprints=fingerprints or {},
        )
        if curve_id is None:
            upsert_pipeline_candidate(
                db,
                run_id=run["id"],
                provider_key=provider_key,
                parameter=parameter,
                score=score,
                score_provenance=score_provenance,
                status="filtered",
                error="singular specialization",
            )
            _mark_source_candidate_done(db, source_candidate_id, curve_id=None)
            return False
    else:
        curve_id, E, source_E, created = _materialize_general(db, candidate)

    context = {
        "pipeline_run_id": int(run["id"]),
        "curve_id": int(curve_id),
        "E": E,
        "source_E": source_E,
        "family": family,
        "plugin": plugin,
        "variant": variant,
        "parameter": parameter,
        "rank_order": rank_order,
        "source_candidate_id": source_candidate_id,
    }
    existing = get_pipeline_candidate(
        db, run["id"], provider_key, parameter
    )
    if existing is not None and str(existing["status"]) in {
        "completed", "filtered", "constraint_filtered", "torsion_mismatch",
        "goal_filtered", "funnel_pruned"
    }:
        print(
            f"[pipeline resume] skip terminal candidate {parameter} "
            f"status={existing['status']}",
            flush=True,
        )
        _mark_source_candidate_done(
            db,
            source_candidate_id,
            curve_id=existing["curve_id"],
        )
        return False
    results = (
        json.loads(existing["stage_results_json"] or "{}")
        if existing is not None
        else {}
    )
    stop_all = False
    status = "completed"
    error = None

    pipeline_row = upsert_pipeline_candidate(
        db,
        run_id=run["id"],
        provider_key=provider_key,
        parameter=parameter,
        score=score,
        score_provenance=score_provenance,
        curve_id=curve_id,
        status="running",
        rigorous_lower=int(proven_lower(get_curve(db, curve_id))),
        stage_results=results,
    )
    if pipeline_row is not None:
        context["pipeline_candidate_id"] = int(pipeline_row["id"])

    for stage_index, rec in _per_curve_stage_records(stages):
        stage_id = rec["id"]
        prior = results.get(str(stage_index))
        prior_status = (
            str((prior or {}).get("result", {}).get("status") or "")
            if isinstance(prior, dict)
            else ""
        )
        if prior_status in {
            "completed", "skipped", "filtered", "constraint_filtered",
            "torsion_mismatch", "goal_filtered", "funnel_pruned"
        }:
            print(
                f"[pipeline resume] skip completed stage={stage_id} "
                f"candidate={parameter}",
                flush=True,
            )
            if prior_status in {
                "filtered", "constraint_filtered", "torsion_mismatch",
                "goal_filtered", "funnel_pruned"
            }:
                status = prior_status
                break
            if stage_id == "stop_goal" and bool(
                (prior or {}).get("result", {}).get("goal_reached")
            ):
                stop_all = True
                break
            continue
        prior_result = (
            (prior or {}).get("result", {})
            if isinstance(prior, dict)
            else {}
        )
        skip_retry, stage_config, retry_reason = _resume_stage_policy(
            stage_id, prior_result, rec["config"]
        )
        if skip_retry:
            print(
                f"[pipeline resume] preserve prior {stage_id} "
                f"outcome={prior_status} retry_policy={retry_reason}",
                flush=True,
            )
            continue

        print(
            f"[pipeline] candidate={parameter} stage={stage_index} {stage_id}",
            flush=True,
        )
        try:
            result, terminal_status, should_stop = _stage_result(
                stage_id,
                db=db,
                run=run,
                run_config=run_config,
                target=target,
                context=context,
                config=stage_config,
                stage_index=stage_index,
            )
            results[str(stage_index)] = {
                "stage_id": stage_id,
                "result": _safe(result),
            }
            if terminal_status:
                status = terminal_status
            if should_stop:
                stop_all = True
        except Exception as exc:
            # One bounded scientific engine failure is normally inconclusive
            # and later independent stages may continue. Exact torsion is a
            # hard gate for torsion-target runs: without verification this
            # fiber cannot contribute rank evidence to that target campaign.
            results[str(stage_index)] = {
                "stage_id": stage_id,
                "result": {"status": "error", "error": repr(exc)},
            }
            print(
                f"[pipeline] stage {stage_id} inconclusive/error: {exc!r}",
                flush=True,
            )
            if stage_id == "exact_torsion" and target.get("torsion_group") is not None:
                status = "inconclusive"

        snap = _snapshot(db, curve_id)
        latest_entry = results.get(str(stage_index), {})
        latest_result = (
            latest_entry.get("result")
            if isinstance(latest_entry, dict)
            else None
        )
        latest_result, record_breaker_stop = _record_breaker_annotate(
            latest_result, run_config, snap
        )
        if isinstance(latest_entry, dict):
            latest_entry["result"] = _safe(latest_result)
            results[str(stage_index)] = latest_entry
        if record_breaker_stop:
            stop_all = True
        upsert_pipeline_candidate(
            db,
            run_id=run["id"],
            provider_key=provider_key,
            parameter=parameter,
            score=score,
            score_provenance=score_provenance,
            curve_id=curve_id,
            status="running" if status == "completed" and not stop_all else status,
            current_stage_index=stage_index,
            current_stage_id=stage_id,
            rigorous_lower=snap["rigorous_lower"],
            rigorous_upper=snap["rigorous_upper"],
            exact_rank=snap["exact_rank"],
            stage_results=results,
            error=error,
        )
        update_pipeline_run(
            db,
            run["id"],
            current_stage_index=stage_index,
            current_stage_id=stage_id,
        )
        if status in {
            "filtered", "constraint_filtered", "torsion_mismatch",
            "goal_filtered", "funnel_pruned", "inconclusive"
        } or stop_all:
            break

    snap = _snapshot(db, curve_id)
    final_status = status if status != "completed" else "completed"
    upsert_pipeline_candidate(
        db,
        run_id=run["id"],
        provider_key=provider_key,
        parameter=parameter,
        score=score,
        score_provenance=score_provenance,
        curve_id=curve_id,
        status=final_status,
        current_stage_index=None,
        current_stage_id=None,
        rigorous_lower=snap["rigorous_lower"],
        rigorous_upper=snap["rigorous_upper"],
        exact_rank=snap["exact_rank"],
        stage_results=results,
        error=error,
    )

    # Builder retention is an inventory policy, never a rank claim.
    # A nonzero floor removes only curves first materialized by this run whose
    # final rigorous lower bound is below the requested threshold. With floor=0
    # preserve the historical behavior of deleting only truly empty rank-zero
    # transient curves.
    if _apply_builder_retention(
        db,
        curve_id=int(curve_id),
        created=bool(created),
        run_config=run_config,
    ):
        db.execute(
            """UPDATE search_pipeline_candidates
               SET curve_id=NULL,updated_at=?
               WHERE run_id=? AND provider_key=? AND parameter=?""",
            (now(), int(run["id"]), str(provider_key), parameter),
        )
        db.commit()

    source_curve_id = (
        int(curve_id) if get_curve(db, int(curve_id)) is not None else None
    )
    _mark_source_candidate_done(
        db,
        source_candidate_id,
        curve_id=source_curve_id,
    )

    progress = run_progress(db, run["id"])
    update_pipeline_run(
        db,
        run["id"],
        candidates_total=progress["total"],
        candidates_done=progress["done"],
        best_lower=progress["best_lower"],
        best_curve_id=progress["best_curve_id"],
    )
    print(
        f"[pipeline candidate done] {parameter} status={final_status} "
        f"rank>={snap['rigorous_lower']} upper={snap['rigorous_upper']}",
        flush=True,
    )
    return stop_all


def _run_family_lane(db, *, run, run_config, stages, target, plugin, variant, provider_key=""):
    family = load_family(variant.family_spec)
    fingerprints = plugin_fingerprints(plugin, variant)
    candidates = _family_candidates(
        db,
        run_config["project_root"],
        run["id"],
        plugin,
        variant,
        stages,
        run_config,
        target=target,
    )
    for candidate in candidates:
        if get_pipeline_candidate(
            db, run["id"], provider_key, candidate["parameter"]
        ) is None:
            upsert_pipeline_candidate(
                db,
                run_id=run["id"],
                provider_key=provider_key,
                parameter=candidate["parameter"],
                score=candidate["score"],
                score_provenance=candidate.get("score_provenance"),
                status="queued",
            )
    current = run_progress(db, run["id"])
    update_pipeline_run(
        db,
        run["id"],
        candidates_total=current["total"],
        candidates_done=current["done"],
    )
    for index, candidate in enumerate(candidates, 1):
        print(
            f"[pipeline candidate] {index}/{len(candidates)} "
            f"{plugin.id}/{variant.id} t={candidate['parameter']} "
            f"score={candidate['score']:.6f}",
            flush=True,
        )
        stop = _process_candidate(
            db,
            run=run,
            run_config=run_config,
            target=target,
            stages=stages,
            provider_key=provider_key,
            candidate=candidate,
            plugin=plugin,
            variant=variant,
            family=family,
            fingerprints=fingerprints,
        )
        if stop:
            return True
    return False


def _run_general_lane(db, *, run, run_config, stages, target):
    candidates = _general_candidates(target, stages)
    for candidate in candidates:
        if get_pipeline_candidate(
            db, run["id"], "general", candidate["parameter"]
        ) is None:
            upsert_pipeline_candidate(
                db,
                run_id=run["id"],
                provider_key="general",
                parameter=candidate["parameter"],
                score=candidate.get("score"),
                score_provenance=candidate.get("score_provenance"),
                status="queued",
            )
    current = run_progress(db, run["id"])
    update_pipeline_run(
        db,
        run["id"],
        candidates_total=current["total"],
        candidates_done=current["done"],
    )
    for index, candidate in enumerate(candidates, 1):
        print(
            f"[pipeline candidate] {index}/{len(candidates)} "
            f"{candidate['parameter']} score={candidate.get('score', 0.0):.6f}",
            flush=True,
        )
        stop = _process_candidate(
            db,
            run=run,
            run_config=run_config,
            target=target,
            stages=stages,
            provider_key="general",
            candidate=candidate,
        )
        if stop:
            return True
    return False



def _replan_stage_result_compatible(*, previous, wanted, entry):
    """Return whether an old completed stage result satisfies the new stage.

    Exact config equality is the default.  A small set of cache-like stages can
    safely reuse stronger/superset work under a changed budget.
    """
    if previous is None or wanted is None or not isinstance(entry, dict):
        return False
    stage_id = str(entry.get("stage_id") or "")
    if stage_id != str(previous.get("id") or "") or stage_id != str(wanted.get("id") or ""):
        return False

    previous_config = dict(previous.get("config") or {})
    wanted_config = dict(wanted.get("config") or {})
    result = entry.get("result")
    if not isinstance(result, dict):
        return False

    if stage_id == "prime_table_cache":
        # Prime-cache reuse is valid only for genuinely complete Frobenius
        # coverage.  The historical requested prime_bound is not sufficient:
        # older cache results could report completed even when E.ap(p) failed.
        if str(result.get("status") or "") != "completed":
            return False
        try:
            completed_bound = int(result.get("frobenius_complete_through") or 0)
            wanted_bound = int(wanted_config.get("prime_bound") or 0)
        except Exception:
            return False
        if completed_bound < wanted_bound:
            return False
        wants_all_bad = bool(
            wanted_config.get("eager_bad_primes", False)
            and wanted_config.get("include_all_bad", True)
        )
        if wants_all_bad and not bool(result.get("bad_primes_complete", False)):
            return False
        return True

    if previous_config == wanted_config:
        return True

    return False


def _replan_run_from_preset(db, *, run_id, preset_id):
    row = get_pipeline_run(db, int(run_id))
    if row is None:
        raise ValueError(f"pipeline run #{run_id} not found")
    run = pipeline_run_payload(row)
    stages = template_stages(str(preset_id), run["target_mode"])
    errors = validate_pipeline(run["target_mode"], stages)
    if errors:
        raise ValueError(
            f"preset {preset_id!r} is invalid for {run['target_mode']}: "
            + "; ".join(errors)
        )

    normalized = normalize_pipeline(stages)
    old_normalized = normalize_pipeline(run["stages"])
    old_by_index = {
        str(index): rec
        for index, rec in enumerate(old_normalized, 1)
    }
    new_by_index = {
        str(index): rec
        for index, rec in enumerate(normalized, 1)
    }
    rows = pipeline_candidates(db, int(run_id), limit=1000000)
    kept_entries = 0
    dropped_entries = 0
    for candidate in rows:
        try:
            old_results = json.loads(candidate["stage_results_json"] or "{}")
        except Exception:
            old_results = {}
        new_results = {}
        for key, entry in old_results.items():
            if not isinstance(entry, dict):
                dropped_entries += 1
                continue
            wanted = new_by_index.get(str(key))
            previous = old_by_index.get(str(key))
            same_contract = _replan_stage_result_compatible(
                previous=previous,
                wanted=wanted,
                entry=entry,
            )
            if same_contract:
                new_results[str(key)] = entry
                kept_entries += 1
            else:
                dropped_entries += 1

        status = str(candidate["status"] or "queued")
        if status in {
            "funnel_pruned", "transformed", "inconclusive", "error",
            "completed",
        }:
            status = "running"
        update_pipeline_candidate(
            db,
            int(candidate["id"]),
            status=status,
            current_stage_index=None,
            current_stage_id=None,
            stage_results_json=new_results,
            error=None,
        )

    db.execute(
        """UPDATE search_pipeline_runs
           SET stages_json=?,status='queued',current_stage_index=NULL,
               current_stage_id=NULL,error=NULL,result_json=NULL,
               finished_at=NULL,updated_at=?
           WHERE id=?""",
        (
            json.dumps(normalized, sort_keys=True),
            now(),
            int(run_id),
        ),
    )
    db.commit()
    print(
        f"[pipeline replan] preset={preset_id} "
        f"stages={len(normalized)} candidates={len(rows)} "
        f"kept_stage_results={kept_entries} dropped_stage_results={dropped_entries}",
        flush=True,
    )
    return normalized


def main():
    args = parse_args()
    db_path = Path(args.db).resolve()
    project_root = Path(args.project_root).resolve()
    db = connect(db_path)
    ensure_pipeline_schema(db)

    row = get_pipeline_run(db, args.run_id)
    if row is None:
        raise SystemExit(f"pipeline run #{args.run_id} not found")
    if args.replan_preset:
        _replan_run_from_preset(
            db,
            run_id=int(args.run_id),
            preset_id=str(args.replan_preset),
        )
        row = get_pipeline_run(db, args.run_id)
    run = pipeline_run_payload(row)
    stages = normalize_pipeline(run["stages"])
    errors = validate_pipeline(run["target_mode"], stages)
    if errors:
        update_pipeline_run(
            db,
            run["id"],
            status="failed",
            error="; ".join(errors),
            finished_at=now(),
        )
        raise SystemExit("invalid pipeline: " + "; ".join(errors))

    target = dict(run["target"])
    run_config = dict(run["run_config"])
    run_config["project_root"] = str(project_root)
    run_config["db_path"] = str(db_path)
    update_pipeline_run(
        db,
        run["id"],
        status="running",
        started_at=row["started_at"] or now(),
        error=None,
        finished_at=None,
    )

    print("RANK HUNTER BUILDER", flush=True)
    print("=" * 72, flush=True)
    print(f"run = #{run['id']} · {run['pipeline_name']}", flush=True)
    print(f"target mode = {run['target_mode']}", flush=True)
    print(
        "pipeline = "
        + " -> ".join(stage_spec(rec["id"]).label for rec in stages),
        flush=True,
    )
    print(
        "proof boundary = heuristic stages schedule work only; rigorous rank "
        "promotion requires Rank Hunter exact evidence",
        flush=True,
    )

    stopped_on_goal = False
    candidate_generation_result = None
    try:
        population_mode = _uses_population_scheduler(stages)
        print(
            f"[pipeline setup] scheduler={'population' if population_mode else 'candidate'}",
            flush=True,
        )
        if run["target_mode"] == "family":
            plugin = get_plugin(project_root, target["plugin_id"])
            variant = get_variant(plugin, target.get("variant_id"))
            if bool(run_config.get("candidate_only")):
                candidate_generation_result = _run_candidate_generation_lane(
                    db,
                    run=run,
                    run_config=run_config,
                    stages=stages,
                    target=target,
                    plugin=plugin,
                    variant=variant,
                )
            elif population_mode:
                items = _prepare_family_population_items(
                    db,
                    run=run,
                    run_config=run_config,
                    stages=stages,
                    target=target,
                    plugin=plugin,
                    variant=variant,
                )
                stopped_on_goal = _run_population_pipeline(
                    db,
                    run=run,
                    run_config=run_config,
                    target=target,
                    stages=stages,
                    items=items,
                )
            else:
                stopped_on_goal = _run_family_lane(
                    db,
                    run=run,
                    run_config=run_config,
                    stages=stages,
                    target=target,
                    plugin=plugin,
                    variant=variant,
                )
        elif run["target_mode"] == "torsion":
            torsion = canonical_torsion_label(target["torsion_group"])
            target["torsion_group"] = torsion
            providers = discover_providers(
                project_root,
                db,
                torsion,
                include_secondary=bool(target.get("include_secondary_providers", False)),
            )
            # A stored candidate pool already belongs to one exact family/variant.
            # Family Search can therefore run the torsion-aware Pipeline on that
            # pool without cycling unrelated providers for the same torsion group.
            providers = _restrict_torsion_providers(providers, target)
            wanted_plugin = str(target.get("plugin_id") or "").strip()
            wanted_variant = str(target.get("variant_id") or "").strip()
            if not providers:
                detail = (
                    f" for {wanted_plugin}/{wanted_variant}"
                    if wanted_plugin
                    else ""
                )
                raise RuntimeError(
                    f"no enabled torsion provider matched {torsion}{detail}"
                )
            print(f"[pipeline torsion] providers={len(providers)} exact={torsion}", flush=True)
            if population_mode:
                items = []
                for pindex, provider in enumerate(providers, 1):
                    print(
                        f"[pipeline torsion] provider {pindex}/{len(providers)} "
                        f"{provider['plugin_name']} / {provider['variant_name']}",
                        flush=True,
                    )
                    plugin = get_plugin(project_root, provider["plugin_id"])
                    variant = get_variant(plugin, provider["variant_id"])
                    items.extend(
                        _prepare_family_population_items(
                            db,
                            run=run,
                            run_config=run_config,
                            stages=stages,
                            target=target,
                            plugin=plugin,
                            variant=variant,
                            provider_key=str(provider["provider_key"]),
                        )
                    )
                stopped_on_goal = _run_population_pipeline(
                    db,
                    run=run,
                    run_config=run_config,
                    target=target,
                    stages=stages,
                    items=items,
                )
            else:
                for pindex, provider in enumerate(providers, 1):
                    print(
                        f"[pipeline torsion] provider {pindex}/{len(providers)} "
                        f"{provider['plugin_name']} / {provider['variant_name']}",
                        flush=True,
                    )
                    plugin = get_plugin(project_root, provider["plugin_id"])
                    variant = get_variant(plugin, provider["variant_id"])
                    stopped_on_goal = _run_family_lane(
                        db,
                        run=run,
                        run_config=run_config,
                        stages=stages,
                        target=target,
                        plugin=plugin,
                        variant=variant,
                        provider_key=str(provider["provider_key"]),
                    )
                    if stopped_on_goal:
                        break
        elif run["target_mode"] == "general":
            if population_mode:
                items = _prepare_general_population_items(
                    db,
                    run=run,
                    stages=stages,
                    target=target,
                )
                stopped_on_goal = _run_population_pipeline(
                    db,
                    run=run,
                    run_config=run_config,
                    target=target,
                    stages=stages,
                    items=items,
                )
            else:
                stopped_on_goal = _run_general_lane(
                    db,
                    run=run,
                    run_config=run_config,
                    stages=stages,
                    target=target,
                )
        elif run["target_mode"] == "curve":
            curve_id = int(target["curve_id"])
            stored = get_curve(db, curve_id)
            if stored is None:
                raise RuntimeError(f"stored curve #{curve_id} not found")
            plugin = None
            variant = None
            fingerprints = {}
            plugin_id = str(target.get("plugin_id") or "").strip()
            if plugin_id:
                plugin = get_plugin(project_root, plugin_id)
                variant = get_variant(plugin, target.get("variant_id"))
                fingerprints = plugin_fingerprints(plugin, variant)
            items = [{
                "provider_key": "curve",
                "candidate": {
                    "curve_id": curve_id,
                    "parameter": f"curve:{curve_id}",
                    "score": stored["score"],
                    "rank_order": 1,
                },
                "plugin": plugin,
                "variant": variant,
                "family": None,
                "fingerprints": fingerprints,
            }]
            stopped_on_goal = _run_population_pipeline(
                db,
                run=run,
                run_config=run_config,
                target=target,
                stages=stages,
                items=items,
            )
        else:
            raise RuntimeError(f"unsupported target mode {run['target_mode']!r}")

        progress = run_progress(db, run["id"])
        result = {
            "run_id": int(run["id"]),
            "pipeline": run["pipeline_name"],
            "target_mode": run["target_mode"],
            "candidates_total": progress["total"],
            "candidates_done": progress["done"],
            "best_lower": progress["best_lower"],
            "best_curve_id": progress["best_curve_id"],
            "stopped_on_goal": bool(stopped_on_goal),
        }
        if candidate_generation_result is not None:
            result["candidate_generation"] = dict(candidate_generation_result)
        update_pipeline_run(
            db,
            run["id"],
            status="target_hit" if stopped_on_goal else "completed",
            current_stage_index=None,
            current_stage_id=None,
            candidates_total=progress["total"],
            candidates_done=progress["done"],
            best_lower=progress["best_lower"],
            best_curve_id=progress["best_curve_id"],
            result_json=json.dumps(result, sort_keys=True),
            finished_at=now(),
        )
        print(MARKER + json.dumps(result, sort_keys=True), flush=True)
    except BaseException as exc:
        latest = get_pipeline_run(db, run["id"])
        if latest is not None and str(latest["status"]) not in {"paused", "killed"}:
            update_pipeline_run(
                db,
                run["id"],
                status="failed",
                error=str(exc),
                finished_at=now(),
            )
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
