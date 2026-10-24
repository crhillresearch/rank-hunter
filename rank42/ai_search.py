"""AI-directed campaigns built from Rank Hunter's validated Pipelines."""

from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path

from rank42.db import now
from rank42.ai_provider import AVAILABLE_MODELS, generate_json
from rank42.gemini_api import DEFAULT_MODEL
from rank42.pipeline_catalog import (
    normalize_stage,
    normalize_pipeline,
    pipeline_stage_specs,
    pipeline_preset_options,
    stage_spec,
    template_stages,
    validate_pipeline,
)
from rank42.pipeline_state import list_pipelines, pipeline_payload
from rank42.schema_sql import execute_static_schema_script
from rank42.torsion import canonical_torsion_label


AI_SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_search_campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    research_campaign_id INTEGER NOT NULL REFERENCES research_campaigns(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    objective TEXT NOT NULL,
    target_mode TEXT NOT NULL,
    target_json TEXT NOT NULL,
    config_json TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    best_lower INTEGER NOT NULL DEFAULT 0,
    elapsed_seconds REAL NOT NULL DEFAULT 0,
    best_curve_id INTEGER,
    job_id INTEGER,
    error TEXT,
    next_decision_json TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_search_campaigns_updated
    ON ai_search_campaigns(updated_at DESC,id DESC);
CREATE TABLE IF NOT EXISTS ai_search_rounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ai_campaign_id INTEGER NOT NULL REFERENCES ai_search_campaigns(id) ON DELETE CASCADE,
    round_number INTEGER NOT NULL,
    decision_json TEXT NOT NULL,
    pipeline_run_id INTEGER REFERENCES search_pipeline_runs(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'planned',
    best_lower INTEGER NOT NULL DEFAULT 0,
    exit_code INTEGER,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(ai_campaign_id,round_number)
);
CREATE INDEX IF NOT EXISTS idx_ai_search_rounds_campaign
    ON ai_search_rounds(ai_campaign_id,round_number DESC);
"""


def ensure_ai_schema(db):
    present = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_search_rounds'"
    ).fetchone()
    if present is None:
        execute_static_schema_script(db, AI_SCHEMA)
        db.commit()
    else:
        columns = {str(row["name"]) for row in
                   db.execute("PRAGMA table_info(ai_search_campaigns)").fetchall()}
        if "elapsed_seconds" not in columns:
            db.execute("ALTER TABLE ai_search_campaigns ADD COLUMN "
                       "elapsed_seconds REAL NOT NULL DEFAULT 0")
        if "next_decision_json" not in columns:
            db.execute("ALTER TABLE ai_search_campaigns ADD COLUMN next_decision_json TEXT")
            db.commit()


def create_ai_campaign(db, *, research_campaign_id, name, objective,
                       target_mode, target, config, model=DEFAULT_MODEL):
    ensure_ai_schema(db)
    stamp = now()
    cursor = db.execute(
        """INSERT INTO ai_search_campaigns(
            research_campaign_id,name,objective,target_mode,target_json,config_json,
            model,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?)""",
        (int(research_campaign_id), str(name), str(objective), str(target_mode),
         json.dumps(dict(target), sort_keys=True),
         json.dumps(dict(config), sort_keys=True), str(model), stamp, stamp),
    )
    db.commit()
    return int(cursor.lastrowid)


def get_ai_campaign(db, campaign_id):
    ensure_ai_schema(db)
    return db.execute(
        "SELECT * FROM ai_search_campaigns WHERE id=?", (int(campaign_id),)
    ).fetchone()


def list_ai_campaigns(db, *, limit=20):
    ensure_ai_schema(db)
    return db.execute(
        "SELECT * FROM ai_search_campaigns ORDER BY id DESC LIMIT ?", (int(limit),)
    ).fetchall()


def list_ai_rounds(db, campaign_id):
    ensure_ai_schema(db)
    return db.execute(
        "SELECT * FROM ai_search_rounds WHERE ai_campaign_id=? ORDER BY round_number",
        (int(campaign_id),),
    ).fetchall()


def update_ai_campaign(db, campaign_id, **values):
    allowed = {"status", "best_lower", "best_curve_id", "job_id", "error",
               "next_decision_json", "config_json",
               "elapsed_seconds",
               "started_at", "finished_at"}
    fields = {key: value for key, value in values.items() if key in allowed}
    if not fields:
        return
    fields["updated_at"] = now()
    db.execute(
        "UPDATE ai_search_campaigns SET " + ",".join(f"{key}=?" for key in fields)
        + " WHERE id=?", [*fields.values(), int(campaign_id)],
    )
    db.commit()


def continue_ai_campaign(db, campaign_id, *, minutes=240, rounds=5):
    """Grant another bounded block of work to an exhausted campaign."""
    row = get_ai_campaign(db, campaign_id)
    if row is None or row["status"] != "budget_exhausted":
        raise ValueError("Only a finished, budget-exhausted hunt can be continued")
    config = json.loads(row["config_json"])
    extra_minutes = max(10, int(minutes))
    extra_rounds = max(1, int(rounds))
    config["max_total_minutes"] = int(config["max_total_minutes"]) + extra_minutes
    config["max_rounds"] = int(config["max_rounds"]) + extra_rounds
    config["hard_stop_minutes"] = max(
        int(config.get("hard_stop_minutes") or 0), int(config["max_total_minutes"])
    )
    config["hard_stop_rounds"] = max(
        int(config.get("hard_stop_rounds") or 0), int(config["max_rounds"])
    )
    config["auto_continue"] = False
    update_ai_campaign(db, campaign_id, config_json=json.dumps(config, sort_keys=True),
                       status="queued", finished_at=None, error=None)
    return config


def reconcile_ai_job(db, job_id, *, stopped_as=None):
    """Retire AI state when Jobs stops a worker before its cleanup can run."""
    present = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_search_campaigns'"
    ).fetchone()
    if present is None:
        return False
    row = db.execute(
        "SELECT * FROM ai_search_campaigns WHERE job_id=?", (int(job_id),)
    ).fetchone()
    if row is None or row["status"] not in {"queued", "running"}:
        return False
    job = db.execute("SELECT * FROM ui_jobs WHERE id=?", (int(job_id),)).fetchone()
    if job is None or job["kind"] != "ai_search":
        return False
    job_status = str(stopped_as or job["status"])
    if job_status not in {"killed", "stopped", "paused", "failed", "interrupted"}:
        return False
    status = "failed" if job_status in {"failed", "interrupted"} else job_status
    if status == "stopped":
        try:
            meta = json.loads(job["metadata_json"] or "{}")
        except (TypeError, ValueError):
            meta = {}
        if meta.get("pause_requested"):
            status = "paused"
    latest = db.execute(
        "SELECT * FROM ai_search_rounds WHERE ai_campaign_id=? "
        "ORDER BY round_number DESC LIMIT 1", (int(row["id"]),)
    ).fetchone()
    if latest is not None and latest["status"] in {"planned", "running"}:
        update_ai_round(db, int(latest["id"]), status="paused",
                        error="Search stopped from Jobs")
        if latest["pipeline_run_id"]:
            from rank42.pipeline_state import get_pipeline_run, update_pipeline_run

            run_id = int(latest["pipeline_run_id"])
            run = get_pipeline_run(db, run_id)
            if run is not None and run["status"] in {"queued", "running"}:
                update_pipeline_run(db, run_id, status="paused", finished_at=now(),
                                    error="AI Hunt job stopped")
    elapsed = float(row["elapsed_seconds"] or 0)
    if job["started_at"]:
        try:
            start = datetime.fromisoformat(str(job["started_at"]))
            end = datetime.fromisoformat(str(job["finished_at"] or now()))
            elapsed += max(0, (end - start).total_seconds())
        except (TypeError, ValueError):
            pass
    update_ai_campaign(db, int(row["id"]), status=status,
                       elapsed_seconds=elapsed, finished_at=now(),
                       error=None if status in {"paused", "stopped", "killed"}
                       else "AI Hunt job ended before its campaign completed")
    from rank42.manage_store import update_campaign

    update_campaign(db, int(row["research_campaign_id"]), status="paused")
    return True


def reconcile_ai_campaigns(db, *, limit=20):
    """Repair jobs stopped outside the AI worker, including older force stops."""
    present = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_search_campaigns'"
    ).fetchone()
    if present is None:
        return 0
    rows = db.execute(
        "SELECT a.job_id FROM ai_search_campaigns a JOIN ui_jobs j ON j.id=a.job_id "
        "WHERE a.status IN ('queued','running') AND "
        "j.status IN ('killed','stopped','failed','interrupted') "
        "ORDER BY a.id DESC LIMIT ?", (int(limit),)
    ).fetchall()
    return sum(bool(reconcile_ai_job(db, int(item["job_id"]))) for item in rows)


def create_ai_round(db, campaign_id, number, decision):
    stamp = now()
    cursor = db.execute(
        """INSERT INTO ai_search_rounds(
            ai_campaign_id,round_number,decision_json,created_at,updated_at
        ) VALUES(?,?,?,?,?)""",
        (int(campaign_id), int(number), json.dumps(decision, sort_keys=True),
         stamp, stamp),
    )
    db.commit()
    return int(cursor.lastrowid)


def update_ai_round(db, round_id, **values):
    allowed = {"pipeline_run_id", "status", "best_lower", "exit_code", "error"}
    fields = {key: value for key, value in values.items() if key in allowed}
    if not fields:
        return
    fields["updated_at"] = now()
    db.execute(
        "UPDATE ai_search_rounds SET " + ",".join(f"{key}=?" for key in fields)
        + " WHERE id=?", [*fields.values(), int(round_id)],
    )
    db.commit()


def strategy_options(db, *, target_mode, target):
    """Only recipes accepted by Rank Hunter's own Pipeline validator."""
    mode = str(target_mode)
    plugin_id = str(target.get("plugin_id") or "")
    options = []
    for preset in pipeline_preset_options(mode, plugin_id=plugin_id):
        stages = template_stages(preset["id"], mode)
        if validate_pipeline(mode, stages):
            continue
        options.append({"id": f"preset:{preset['id']}",
                        "name": preset["label"],
                        "description": preset["description"],
                        "stages": stages, "pipeline_id": None})
    for row in list_pipelines(db, limit=100):
        saved = pipeline_payload(row)
        if saved["target_mode"] != mode:
            continue
        default_target = dict(saved.get("launch_defaults", {}).get("target") or {})
        if (default_target.get("plugin_id")
                and str(default_target["plugin_id"]) != plugin_id):
            continue
        try:
            stages = normalize_pipeline(saved["stages"])
            if validate_pipeline(mode, stages):
                continue
        except (ValueError, TypeError):
            continue
        options.append({"id": f"saved:{saved['id']}",
                        "name": saved["name"],
                        "description": "Saved Rank Hunter Pipeline",
                        "stages": stages, "pipeline_id": int(saved["id"])})
    return options


_WINDOWS = {"default", "central", "positive", "negative", "wide"}

_BUILDABLE_STAGE_IDS = frozenset({
    # Candidate construction and exact local arithmetic.
    "nagao_screen", "nagao_rescore", "euler_parameter_scan",
    "euler_parameter_rescore", "corpus_filter", "select_survivors",
    "mestre_nagao_ensemble", "multi_scale_frobenius",
    "frobenius_persistence", "torsion_mod_p_sieve",
    "local_solubility_sieve", "root_number", "exact_torsion",
    # Families, exact transforms, and generic-section construction.
    "specialization_seeds", "family_baseline",
    "specialization_injectivity_certificate", "quadratic_twist_sweep",
    "targeted_twist_search", "isogeny_walk", "rank_jump_base_change",
    "autonomous_rank_jump", "parameter_pullback",
    "bounded_section_search", "section_height_shell", "trace_section_constructor",
    "forced_bisection_constructor", "square_condition_specializer",
    "constructive_rank_jump_loop", "surface_fibration_switch",
    # Independent point and covering attacks.
    "small_point_density", "integral_seed", "denominator_band",
    "point_yield_persistence", "affine_search", "adaptive_ladder",
    "simon_covering", "mwrank_covering", "higher_descent_ladder",
    "covering_selmer_branch", "covering_minimize_reduce",
    "covering_local_height_planner", "selmer_element_fanout",
    "padic_covering_search", "pointed_quartic", "mw_growth_loop",
    "large_height_generator_hunt", "height_lattice_reduction",
    "half_lattice_hole_enumeration", "lattice_chart_construction",
    "lattice_chart_search", "plugin_geometry", "plugin_family_search",
    # Evidence and stopping. Upper bounds are useful for triage, not rank growth.
    "independence", "saturation", "full_saturation_index_recovery",
    "pari_upper_gate", "selmer_bound", "selmer_headroom",
    "isogeny_descent", "stop_goal",
})
_DISCOVERY_STAGE_IDS = frozenset({
    "integral_seed", "affine_search", "small_point_density", "denominator_band",
    "adaptive_ladder", "pointed_quartic", "mwrank_covering",
    "simon_covering", "higher_descent_ladder", "covering_selmer_branch",
    "selmer_element_fanout", "padic_covering_search", "mw_growth_loop",
    "lattice_chart_search", "quadratic_twist_sweep", "targeted_twist_search",
    "isogeny_walk", "rank_jump_base_change", "autonomous_rank_jump",
    "bounded_section_search", "section_height_shell", "constructive_rank_jump_loop",
    "square_condition_specializer", "surface_fibration_switch",
    "large_height_generator_hunt", "plugin_geometry", "plugin_family_search",
})


def buildable_modules(target_mode):
    """Expose executable, budgeted modules for one elliptic target mode."""
    return [spec for spec in pipeline_stage_specs(target_mode)
            if spec.id in _BUILDABLE_STAGE_IDS]


def option_for_decision(decision, options):
    """Resolve a saved/preset recipe or the exact validated AI-built sequence."""
    if decision["recipe_id"] == "built":
        return {
            "id": "built", "name": "AI-built Pipeline",
            "description": "Validated module sequence assembled for this round",
            "stages": [normalize_stage(stage_id)
                       for stage_id in decision["stage_ids"]],
            "pipeline_id": None,
        }
    return next((option for option in options
                 if option["id"] == decision["recipe_id"]), None)


def validate_decision(raw, *, options, config, previous=(), target_mode=None,
                      target_summary=None):
    if not isinstance(raw, dict):
        raise ValueError("AI strategy must be an object")
    recipe_id = str(raw.get("recipe_id") or "")
    if recipe_id not in {option["id"] for option in options} | {"built"}:
        raise ValueError("AI selected a recipe unavailable for this target")
    stage_ids = None
    if recipe_id == "built":
        if config.get("allow_pipeline_build", True) is False:
            raise ValueError("AI-built Pipelines are disabled for this hunt")
        allowed = {spec.id for spec in buildable_modules(target_mode)}
        stage_ids = raw.get("stage_ids")
        if (not isinstance(stage_ids, list) or not 3 <= len(stage_ids) <= 16
                or any(not isinstance(item, str) or item not in allowed
                       for item in stage_ids)):
            raise ValueError("AI-built Pipeline needs 3–16 available module IDs")
        if not any(stage_id in _DISCOVERY_STAGE_IDS for stage_id in stage_ids):
            raise ValueError("AI-built Pipeline needs a discovery stage")
        if "independence" not in stage_ids:
            raise ValueError("AI-built Pipeline needs Exact Independence")
        if ("bounded_section_search" in stage_ids
                and (target_summary or {}).get("formula_family") is False):
            raise ValueError("Bounded Q(t) Section Search requires a JSON formula family")
        stages = normalize_pipeline(stage_ids)
        errors = validate_pipeline(target_mode, stages)
        if errors:
            raise ValueError("Invalid AI-built Pipeline: " + "; ".join(errors[:3]))
        if sum(stage_spec(stage_id).cost == "expensive" for stage_id in stage_ids) > 6:
            raise ValueError("AI-built Pipeline has too many expensive modules")
    window = str(raw.get("window") or "default")
    if window not in _WINDOWS:
        raise ValueError("AI selected an unknown search window")
    if target_mode in {"curve", "general"} and window != "default":
        raise ValueError("This target uses the default search window")
    try:
        depth = int(raw.get("depth") or 1)
        candidate_limit = int(raw.get("candidate_limit") or config["max_candidates"])
        search_seed = int(raw.get("search_seed") or 1729)
    except (TypeError, ValueError):
        raise ValueError("AI strategy has an invalid numeric setting") from None
    if not 1 <= depth <= 3:
        raise ValueError("Search depth must be 1, 2, or 3")
    if not 1 <= candidate_limit <= int(config["max_candidates"]):
        raise ValueError("Candidate limit exceeds this campaign's budget")
    if not 0 <= search_seed <= 2**31 - 1:
        raise ValueError("Search seed is outside the supported range")
    decision = {"recipe_id": recipe_id, "window": window, "depth": depth,
                "candidate_limit": candidate_limit, "search_seed": search_seed,
                "reason": str(raw.get("reason") or "").strip()[:700]}
    if stage_ids is not None:
        decision["stage_ids"] = stage_ids
    if raw.get("_model_used") in AVAILABLE_MODELS:
        decision["model_used"] = raw["_model_used"]
    def signature(item):
        if item.get("recipe_id") == "built":
            return ("built", tuple(item.get("stage_ids") or ()), item.get("window"),
                    item.get("depth"), item.get("candidate_limit"))
        if target_mode == "curve":
            return item.get("recipe_id"), item.get("depth")
        return (item.get("recipe_id"), item.get("window"), item.get("depth"),
                item.get("candidate_limit"), item.get("search_seed"))
    if any(signature(item) == signature(decision) for item in previous):
        raise ValueError("AI repeated an identical search strategy")
    return decision


def _prompt(*, objective, target_mode, target_summary, goal_rank, config,
            options, observations, previous, error=None):
    choices = [
        {"id": option["id"], "name": option["name"],
         "description": option["description"],
         "stages": [stage_spec(stage["id"]).label for stage in option["stages"]]}
        for option in options
    ]
    payload = {
        "research_goal": objective[:1600], "target_mode": target_mode,
        "target": target_summary, "rigorous_rank_goal": int(goal_rank),
        "limits": {key: config[key] for key in
                   ("max_candidates", "max_scan_parameters", "max_point_height",
                    "numerator_limit", "denominator_limit", "max_run_minutes")},
        "available_recipes": choices, "recent_results": list(observations)[-6:],
        "previous_decisions": list(previous)[-8:],
        "validation_error": error,
    }
    if config.get("allow_pipeline_build", True):
        payload["buildable_modules"] = [
            {"id": spec.id, "label": spec.label, "category": spec.category,
             "requires": sorted(spec.requires), "provides": sorted(spec.provides),
             "cost": spec.cost,
             **({"condition": spec.conditional[:180]} if spec.conditional else {})}
            for spec in buildable_modules(target_mode)
        ]
        payload["valid_build_example"] = (
            ["nagao_screen", "exact_torsion", "integral_seed",
             "independence", "stop_goal"]
            if target_mode == "torsion" else
            ["integral_seed", "independence", "stop_goal"]
            if target_mode == "curve" else
            ["nagao_screen", "integral_seed", "independence", "stop_goal"]
        )
        payload["build_rules"] = (
            "Use recipe_id='built' and stage_ids with 3-16 module IDs, including "
            "a discovery stage and independence. Use nagao_screen first for "
            "family/torsion/general targets; put exact_torsion before all "
            "advanced torsion stages and immediately after any torsion transform. "
            "Order prerequisite capabilities before consumers. Conditional "
            "modules need the family/model/adapter named in their condition; "
            "do not choose one when that support is absent or unknown. "
            "Bounded Q(t) Section Search requires formula_family=true. "
            "Stage settings are fixed to Rank Hunter defaults and bounded by "
            "campaign limits."
        )
    opening = (
        "You direct a Rank Hunter elliptic-curve search campaign. Choose ONE "
        "available recipe or build a bounded Pipeline from listed modules. "
        if config.get("allow_pipeline_build", True) else
        "You direct a Rank Hunter elliptic-curve search campaign. Choose ONE "
        "available recipe. "
    )
    return (
        opening +
        "The app validates and executes the recipe; "
        "you cannot issue shell commands, alter proofs, or change the stated limits. "
        "Heuristic scores are not rank certificates. Prefer a different strategy "
        "or window when prior runs made no rigorous rank progress. When rounds "
        "time out before searching many candidates, choose a shorter point "
        "discovery and independence sequence. Prioritize new independent points "
        "over upper-bound work when the goal is a higher rigorous lower bound. "
        "A certified "
        "strong result in another torsion group is a separate lead: mention it "
        "in your reason, but keep this campaign on its selected target. For curve "
        "and general targets, window is 'default'. For family/torsion targets, window may "
        "be default, central, positive, negative, or wide. Depth is 1, 2, or 3. "
        "Return only a JSON object with recipe_id, window, depth, "
        "candidate_limit, search_seed, and a brief reason. "
        + ("For a built Pipeline, also return stage_ids in execution order. "
           if config.get("allow_pipeline_build", True) else "")
        + "\n\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )


def plan_strategy(project_root: str | Path, *, model, objective, target_mode,
                  target_summary, goal_rank, config, options, observations=(),
                  previous=()):
    if not options:
        raise ValueError("No compatible Pipeline recipes are available")
    error = None
    for _ in range(2):
        raw = generate_json(
            project_root,
            _prompt(objective=objective, target_mode=target_mode,
                    target_summary=target_summary, goal_rank=goal_rank,
                    config=config, options=options, observations=observations,
                    previous=previous, error=error),
            model=model,
        )
        try:
            return validate_decision(raw, options=options, config=config,
                                     previous=previous, target_mode=target_mode,
                                     target_summary=target_summary)
        except ValueError as exc:
            error = str(exc)
    raise ValueError(f"AI service could not produce a valid strategy: {error}")


def execution_for_decision(*, option, decision, target_mode, target, config):
    """Apply bounded scheduling choices to an existing validated recipe."""
    stages = copy.deepcopy(option["stages"])
    limit = int(decision["candidate_limit"])
    depth = int(decision["depth"])
    for stage in stages:
        settings = stage["config"]
        if "keep" in settings:
            settings["keep"] = min(int(settings["keep"]), limit)
        for key in ("max_candidates", "exact_candidates", "pool_size", "max_children",
                    "max_coverings", "max_sections", "max_conditions", "scan_limit"):
            if key in settings and type(settings[key]) is int and settings[key] > 0:
                settings[key] = min(int(settings[key]), limit)
        for key in ("max_parameters", "max_trials", "max_tests"):
            if key in settings and type(settings[key]) is int and settings[key] > 0:
                settings[key] = min(int(settings[key]),
                                    int(config["max_scan_parameters"]))
        if "max_results" in settings:
            settings["max_results"] = min(int(settings["max_results"]), limit)
        if (option["id"] == "built" and "timeout" in settings
                and type(settings["timeout"]) is int):
            settings["timeout"] = min(
                int(settings["timeout"]),
                max(30, int(config["max_run_minutes"]) * 20),
            )
        for key in ("height", "covering_height", "max_height", "point_bound"):
            if key in settings and type(settings[key]) is int and settings[key] > 0:
                settings[key] = min(int(config["max_point_height"]),
                                    int(settings[key]) * (2 ** (depth - 1)))
        for key in ("heights", "denominator_heights"):
            values = settings.get(key)
            if isinstance(values, list) and values and all(
                type(value) is int and value > 0 for value in values
            ):
                settings[key] = sorted({
                    min(int(config["max_point_height"]),
                        value * (2 ** (depth - 1)))
                    for value in values
                })
    errors = validate_pipeline(target_mode, stages)
    if errors:
        raise ValueError("AI recipe failed Pipeline validation: " + "; ".join(errors))
    execution_target = dict(target)
    if target_mode in {"family", "torsion"}:
        window = decision["window"]
        generation = {
            "engine": "sampled",
            "sample_count": int(config["max_scan_parameters"]),
            "sample_seed": int(decision["search_seed"]),
        }
        numerator = int(config["numerator_limit"])
        denominator = int(config["denominator_limit"])
        if window == "central":
            generation.update(a_min=-max(1, numerator // 2),
                              a_max=max(1, numerator // 2),
                              b_min=1, b_max=max(1, denominator // 2))
        elif window == "positive":
            generation.update(a_min=1, a_max=numerator,
                              b_min=1, b_max=denominator)
        elif window == "negative":
            generation.update(a_min=-numerator, a_max=-1,
                              b_min=1, b_max=denominator)
        elif window == "wide":
            generation.update(a_min=-numerator, a_max=numerator,
                              b_min=1, b_max=denominator)
        execution_target["candidate_generation"] = generation
        if target_mode == "torsion":
            # The runner divides this cap across the discovered providers.
            execution_target["ai_candidate_limit"] = limit
            execution_target["ai_scan_limit"] = int(config["max_scan_parameters"])
        else:
            generation["top"] = limit
    elif target_mode == "general":
        execution_target["random_seed"] = int(decision["search_seed"])
        execution_target["pool_size"] = min(int(execution_target["pool_size"]), limit)
    return stages, execution_target


def verified_campaign_best(db, *, research_campaign_id, target_mode, target):
    """Only local completed rigorous evidence can satisfy an AI rank goal."""
    ids = set()
    if target_mode == "curve" and target.get("curve_id"):
        ids.add(int(target["curve_id"]))
    runs = db.execute(
        "SELECT id,best_curve_id FROM search_pipeline_runs WHERE campaign_id=?",
        (int(research_campaign_id),),
    ).fetchall()
    for run in runs:
        if run["best_curve_id"]:
            ids.add(int(run["best_curve_id"]))
        ids.update(
            int(row["curve_id"])
            for row in db.execute(
                "SELECT curve_id FROM search_pipeline_candidates "
                "WHERE run_id=? AND curve_id IS NOT NULL", (int(run["id"]),)
            ).fetchall()
        )
    if not ids:
        return 0, None
    rows = db.execute(
        """SELECT e.curve_id,
                  MAX(CASE WHEN COALESCE(e.rigorous_lower,0) >= COALESCE(e.exact_rank,0)
                           THEN COALESCE(e.rigorous_lower,0)
                           ELSE COALESCE(e.exact_rank,0) END) AS lower,
                  c.torsion_label
           FROM rank_evidence e JOIN curves c ON c.id=e.curve_id
           WHERE e.rigorous=1 AND e.status IN ('completed','certified_independent')
             AND e.curve_id IN (""" + ",".join("?" for _ in ids) + ") "
        "GROUP BY e.curve_id ORDER BY lower DESC,e.curve_id",
        sorted(ids),
    ).fetchall()
    if target_mode == "torsion":
        wanted = canonical_torsion_label(str(target.get("torsion_group") or ""))
        rows = [row for row in rows if row["torsion_label"] and
                canonical_torsion_label(str(row["torsion_label"])) == wanted]
    if not rows:
        return 0, None
    return int(rows[0]["lower"] or 0), int(rows[0]["curve_id"])
