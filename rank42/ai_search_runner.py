"""Run a bounded Gemini-directed sequence of ordinary Rank Hunter Pipelines."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from rank42.ai_search import (
    create_ai_round,
    execution_for_decision,
    get_ai_campaign,
    list_ai_rounds,
    option_for_decision,
    plan_strategy,
    strategy_options,
    update_ai_campaign,
    update_ai_round,
    validate_decision,
    verified_campaign_best,
)
from rank42.db import connect_existing, now
from rank42.curve_research_state import get_curve_research_state
from rank42.gemini_api import GeminiAPIError
from rank42.manage_store import update_campaign as update_research_campaign
from rank42.pipeline_state import (
    create_pipeline_run,
    ensure_pipeline_schema,
    get_pipeline_run,
    update_pipeline_run,
)
from rank42.pipeline_retention import cleanup_pipeline_run
from rank42.ratpoints import vendored_executable
from rank42.settings_registry import science_python_bootstrap_value, setting_value


class CampaignStopped(Exception):
    pass


_CHILD = None


def _terminate_child():
    child = _CHILD
    if child is None or child.poll() is not None:
        return
    try:
        os.killpg(int(child.pid), signal.SIGTERM)
    except ProcessLookupError:
        pass


def _handle_stop(_signum, _frame):
    _terminate_child()
    raise CampaignStopped()


def _run_pipeline(command, *, cwd, seconds):
    global _CHILD
    _CHILD = subprocess.Popen(command, cwd=str(cwd), start_new_session=True)
    try:
        return int(_CHILD.wait(timeout=max(1, int(seconds)))), False
    except subprocess.TimeoutExpired:
        _terminate_child()
        try:
            _CHILD.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(int(_CHILD.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            _CHILD.wait()
        return 124, True
    finally:
        _CHILD = None


def _recent_observations(db, campaign_id, *, share_parameters=False):
    observations = []
    decisions = []
    curve_columns = {str(column["name"]) for column in db.execute(
        "PRAGMA table_info(curves)"
    ).fetchall()}
    torsion_select = ("curve.torsion_label" if "torsion_label" in curve_columns
                      else "NULL AS torsion_label")
    for item in list_ai_rounds(db, campaign_id):
        decision = json.loads(item["decision_json"])
        decisions.append(decision)
        run_id = item["pipeline_run_id"]
        run = get_pipeline_run(db, int(run_id)) if run_id else None
        record = {
            "round": int(item["round_number"]),
            "recipe": decision.get("recipe_id"),
            "status": str(item["status"]),
            "rigorous_best": int(item["best_lower"] or 0),
            "run_best": int(run["best_lower"] or 0) if run else 0,
            "candidates_done": int(run["candidates_done"] or 0) if run else 0,
            "candidates_total": int(run["candidates_total"] or 0) if run else 0,
            "error": str(item["error"] or "")[:240],
        }
        if run is not None:
            rows = db.execute(
                f"""SELECT candidate.parameter,candidate.score,
                          candidate.rigorous_lower,candidate.status,
                          candidate.curve_id,{torsion_select}
                   FROM search_pipeline_candidates AS candidate
                   LEFT JOIN curves AS curve ON curve.id=candidate.curve_id
                   WHERE candidate.run_id=?
                   ORDER BY MAX(COALESCE(candidate.rigorous_lower,0),
                                COALESCE(curve.descent_lower,0),
                                COALESCE(curve.exact_rank,0)) DESC,
                            candidate.score DESC LIMIT 5""",
                (int(run_id),),
            ).fetchall()
            record["candidate_signals"] = [
                {
                    **({"parameter": str(row["parameter"])[:80]}
                       if share_parameters else {}),
                    "score": row["score"],
                    "rigorous_lower": max(
                        int(row["rigorous_lower"] or 0),
                        int(get_curve_research_state(db, int(row["curve_id"]))
                            .get("rigorous_lower") or 0) if row["curve_id"] else 0,
                    ),
                    "torsion_group": str(row["torsion_label"] or "unknown"),
                    "status": str(row["status"]),
                }
                for row in rows
            ]
        observations.append(record)
    return observations, decisions


def _runtime_settings(db, root):
    science = str(setting_value(
        db, "science_python",
        science_python_bootstrap_value(root, fallback=sys.executable),
    ))
    backend = str(setting_value(db, "ratpoints_backend", "CPU") or "CPU").upper()
    if backend not in {"CPU", "GPU"}:
        backend = "CPU"
    executable_key = "ratpoints_gpu" if backend == "GPU" else "ratpoints"
    executable = str(setting_value(
        db, executable_key, vendored_executable(backend, root)
    ))
    return science, backend, executable


def run_campaign(db, root, campaign_id):
    ensure_pipeline_schema(db)
    row = get_ai_campaign(db, campaign_id)
    if row is None:
        raise ValueError(f"AI campaign #{campaign_id} does not exist")
    if row["status"] not in {"queued", "running", "paused", "failed",
                             "budget_exhausted", "goal_met", "stopped",
                             "killed"}:
        raise ValueError(f"AI campaign #{campaign_id} is {row['status']}")
    config = json.loads(row["config_json"])
    target = json.loads(row["target_json"])
    mode = str(row["target_mode"])
    research_id = int(row["research_campaign_id"])
    research = db.execute(
        "SELECT id,name,created_at FROM research_campaigns WHERE id=?", (research_id,)
    ).fetchone()
    if research is None:
        raise ValueError("Research campaign was removed")
    science, backend, ratpoints = _runtime_settings(db, root)
    if not Path(science).is_file() or not Path(ratpoints).is_file():
        raise ValueError("Configure Science Python and ratpoints in Settings → Runtimes")

    for job in db.execute(
        "SELECT id,metadata_json FROM ui_jobs WHERE kind='ai_search' "
        "ORDER BY id DESC LIMIT 50"
    ).fetchall():
        try:
            owner = int(json.loads(job["metadata_json"] or "{}").get(
                "ai_search_campaign_id") or 0)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if owner == int(campaign_id):
            update_ai_campaign(db, campaign_id, job_id=int(job["id"]))
            break
    update_research_campaign(db, research_id, status="active")
    update_ai_campaign(db, campaign_id, status="running",
                       started_at=row["started_at"] or now(),
                       finished_at=None, error=None)
    started = time.monotonic()
    prior_elapsed = float(row["elapsed_seconds"] or 0)
    elapsed = lambda: prior_elapsed + (time.monotonic() - started)
    goal_rank = int(config["goal_rank"])
    print(f"AI HUNT · #{campaign_id} · {row['name']}", flush=True)
    print(f"target={mode} · rigorous goal ≥{goal_rank} · model={row['model']}", flush=True)
    print(f"budget={config['max_total_minutes']} min · rounds={config['max_rounds']} "
          f"· run≤{config['max_run_minutes']} min", flush=True)

    prior_rounds = list_ai_rounds(db, campaign_id)
    pending_round = (
        prior_rounds[-1]
        if prior_rounds and prior_rounds[-1]["status"] in {"running", "paused"}
        and prior_rounds[-1]["pipeline_run_id"] else None
    )
    first_round = (int(pending_round["round_number"]) if pending_round
                   else len(prior_rounds) + 1)
    round_id = None
    run_id = None
    try:
        round_number = first_round
        while True:
            round_id = None
            run_id = None
            current = get_ai_campaign(db, campaign_id)
            config = json.loads(current["config_json"])
            remaining = float(config["max_total_minutes"]) * 60 - elapsed()
            hard_remaining = float(config.get("hard_stop_minutes") or
                                   config["max_total_minutes"]) * 60 - elapsed()
            best, curve_id = verified_campaign_best(
                db, research_campaign_id=research_id, target_mode=mode, target=target)
            update_ai_campaign(db, campaign_id, best_lower=best, best_curve_id=curve_id)
            if best >= goal_rank:
                update_ai_campaign(db, campaign_id, status="goal_met",
                                   elapsed_seconds=elapsed(), finished_at=now())
                update_research_campaign(db, research_id, status="completed")
                print(f"RIGOROUS_GOAL_MET curve=#{curve_id} rank≥{best}", flush=True)
                return
            if remaining < 45 or round_number > int(config["max_rounds"]):
                if (not config.get("auto_continue") or hard_remaining < 45 or
                        round_number > int(config.get("hard_stop_rounds") or
                                           config["max_rounds"])):
                    break
                next_total = min(
                    int(config["hard_stop_minutes"]),
                    int(config["max_total_minutes"]) +
                    int(config.get("checkpoint_minutes") or 240),
                )
                next_rounds = min(
                    int(config["hard_stop_rounds"]),
                    int(config["max_rounds"]) + int(config.get("checkpoint_rounds") or 5),
                )
                if ((next_total == int(config["max_total_minutes"]) and remaining < 45)
                        or (next_rounds == int(config["max_rounds"])
                            and round_number > int(config["max_rounds"]))):
                    break
                config["max_total_minutes"] = next_total
                config["max_rounds"] = next_rounds
                update_ai_campaign(db, campaign_id,
                                   config_json=json.dumps(config, sort_keys=True))
                print(f"CHECKPOINT · continuing through {next_total} min / "
                      f"{next_rounds} Pipelines", flush=True)
                continue
            options = strategy_options(db, target_mode=mode, target=target)
            observations, previous = _recent_observations(
                db, campaign_id,
                share_parameters=bool(config.get("share_candidate_parameters")),
            )
            if not options:
                raise ValueError("No compatible Pipeline recipes are available")
            if pending_round is not None and round_number == first_round:
                decision = json.loads(pending_round["decision_json"])
                option = option_for_decision(decision, options)
                if option is None:
                    raise ValueError("Interrupted Pipeline recipe is no longer available")
                round_id = int(pending_round["id"])
                run_id = int(pending_round["pipeline_run_id"])
                print(f"RESUMING round {round_number} · Pipeline #{run_id}", flush=True)
            else:
                initial = (
                    json.loads(current["next_decision_json"])
                    if round_number == first_round and current["next_decision_json"]
                    else config.get("initial_decision") if round_number == 1 else None
                )
                decision = (
                    validate_decision(initial, options=options, config=config,
                                      previous=previous, target_mode=mode)
                    if initial else plan_strategy(
                        root, model=row["model"], objective=row["objective"],
                        target_mode=mode,
                        target_summary=config["target_summary"],
                        goal_rank=goal_rank, config=config, options=options,
                        observations=observations, previous=previous,
                    )
                )
                option = option_for_decision(decision, options)
                if option is None:
                    raise ValueError("AI Pipeline recipe is no longer available")
                stages, execution_target = execution_for_decision(
                    option=option, decision=decision, target_mode=mode,
                    target=target, config=config,
                )
                round_id = create_ai_round(db, campaign_id, round_number, decision)
                if current["next_decision_json"]:
                    update_ai_campaign(db, campaign_id, next_decision_json=None)
                run_config = {
                    "target_rank": goal_rank,
                    "retention_floor": int(config["retention_floor"]),
                    "preserve_near_frontier": True,
                    "frontier_margin": 10,
                    "certificate_timeout": 300,
                    "exact_candidates": int(decision["candidate_limit"]),
                    "ratpoints_backend": backend,
                    "ratpoints": ratpoints,
                    "campaign_id": research_id,
                    "campaign_name": str(research["name"]),
                    "campaign_created_at": str(research["created_at"]),
                    "ai_search_campaign_id": int(campaign_id),
                    "ai_search_round": int(round_number),
                }
                run_id = create_pipeline_run(
                    db, pipeline_id=option["pipeline_id"],
                    pipeline_name=f"AI #{campaign_id} · {option['name']}",
                    target_mode=mode, target=execution_target, stages=stages,
                    run_config=run_config, project_root=root,
                )
            update_ai_round(db, round_id, pipeline_run_id=run_id, status="running")
            print(f"\nROUND {round_number}/{config['max_rounds']} · Pipeline #{run_id}", flush=True)
            print(f"strategy={option['name']} · depth={decision['depth']} "
                  f"· window={decision['window']} · keep≤{decision['candidate_limit']}", flush=True)
            print(f"Gemini reason: {decision['reason']}", flush=True)
            remaining = float(config["max_total_minutes"]) * 60 - elapsed()
            seconds = min(float(config["max_run_minutes"]) * 60, remaining)
            command = [science, "-u", "-m", "rank42.pipeline_runner",
                       "--project-root", str(root), "--db", str(config["db_path"]),
                       "--run-id", str(run_id)]
            exit_code, timed_out = _run_pipeline(command, cwd=root, seconds=seconds)
            run = get_pipeline_run(db, run_id)
            if run is not None and run["status"] == "running":
                update_pipeline_run(
                    db, run_id, status="failed",
                    error=("AI Hunt per-run time limit reached" if timed_out
                           else f"Pipeline process exited with code {exit_code}"),
                    finished_at=now(),
                )
                run = get_pipeline_run(db, run_id)
            retention = cleanup_pipeline_run(db, run_id)
            if retention["removed"]:
                print(
                    f"RETENTION Pipeline #{run_id} · removed {retention['removed']} "
                    f"curves below rigorous rank ≥{retention['floor']}",
                    flush=True,
                )
            if retention["frontier_retained"]:
                print(
                    f"NEAR-FRONTIER LEADS Pipeline #{run_id} · kept "
                    f"{retention['frontier_retained']} certified curves below the "
                    f"hunt floor; local threshold ≥{retention['frontier_threshold']}",
                    flush=True,
                )
            best, curve_id = verified_campaign_best(
                db, research_campaign_id=research_id, target_mode=mode, target=target)
            update_ai_round(
                db, round_id, best_lower=best, exit_code=exit_code,
                status="timeout" if timed_out else ("completed" if exit_code == 0 else "failed"),
                error="Per-run time limit reached" if timed_out else (
                    str(run["error"] or "")[:500] if run is not None and exit_code else None
                ),
            )
            update_ai_campaign(db, campaign_id, best_lower=best, best_curve_id=curve_id)
            print(f"ROUND_RESULT #{round_number} exit={exit_code} "
                  f"rigorous_best≥{best} curve=#{curve_id or '—'}", flush=True)
            if best >= goal_rank:
                update_ai_campaign(db, campaign_id, status="goal_met",
                                   elapsed_seconds=elapsed(), finished_at=now())
                update_research_campaign(db, research_id, status="completed")
                print(f"RIGOROUS_GOAL_MET curve=#{curve_id} rank≥{best}", flush=True)
                return
            round_number += 1
        next_decision = None
        try:
            options = strategy_options(db, target_mode=mode, target=target)
            observations, previous = _recent_observations(
                db, campaign_id,
                share_parameters=bool(config.get("share_candidate_parameters")),
            )
            next_decision = plan_strategy(
                root, model=row["model"], objective=row["objective"],
                target_mode=mode, target_summary=config["target_summary"],
                goal_rank=goal_rank, config=config, options=options,
                observations=observations, previous=previous,
            )
        except (GeminiAPIError, ValueError) as exc:
            print(f"NEXT_MOVE_UNAVAILABLE {exc}", flush=True)
        if next_decision:
            print(f"NEXT_MOVE {next_decision['recipe_id']} · "
                  f"{next_decision['reason']}", flush=True)
        update_ai_campaign(db, campaign_id, status="budget_exhausted",
                           next_decision_json=(json.dumps(next_decision, sort_keys=True)
                                               if next_decision else None),
                           elapsed_seconds=elapsed(), finished_at=now())
        update_research_campaign(db, research_id, status="paused")
        print("AI_SEARCH_BUDGET_EXHAUSTED", flush=True)
    except CampaignStopped:
        if round_id is not None:
            update_ai_round(db, round_id, status="paused", error="Job paused or stopped")
        if run_id is not None:
            active = get_pipeline_run(db, run_id)
            if active is not None and active["status"] == "running":
                update_pipeline_run(db, run_id, status="paused", finished_at=now())
        update_ai_campaign(db, campaign_id, status="paused",
                           elapsed_seconds=elapsed(), finished_at=now())
        update_research_campaign(db, research_id, status="paused")
        print("AI_SEARCH_STOPPED", flush=True)
    except Exception as exc:
        update_ai_campaign(db, campaign_id, status="failed", error=str(exc)[:700],
                           elapsed_seconds=elapsed(), finished_at=now())
        update_research_campaign(db, research_id, status="paused")
        print(f"AI_SEARCH_FAILED {type(exc).__name__}: {exc}", flush=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--campaign-id", type=int, required=True)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    db = connect_existing(args.db)
    try:
        run_campaign(db, Path(args.project_root).resolve(), args.campaign_id)
    finally:
        db.close()


if __name__ == "__main__":
    main()
