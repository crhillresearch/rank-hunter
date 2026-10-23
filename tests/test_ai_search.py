import sqlite3
import json
import io
import urllib.error
from pathlib import Path

import pytest

from rank42.ai_search import (
    execution_for_decision,
    strategy_options,
    validate_decision,
    verified_campaign_best,
)
from rank42.gemini_api import (
    GeminiAPIError,
    delete_local_key,
    generate_json,
    key_path,
    key_source,
    read_key,
    write_key,
)
from rank42.pipeline_catalog import validate_pipeline


LIMITS = {
    "max_candidates": 250,
    "max_scan_parameters": 50000,
    "max_point_height": 4000000,
    "numerator_limit": 3000,
    "denominator_limit": 300,
    "max_run_minutes": 45,
}


def test_gemini_key_stays_in_private_local_file(tmp_path):
    assert read_key(tmp_path, environ={}) == ""
    target = write_key(tmp_path, "example-key")
    assert target == key_path(tmp_path)
    assert target.stat().st_mode & 0o777 == 0o600
    assert read_key(tmp_path, environ={}) == "example-key"
    assert key_source(tmp_path, environ={}) == "local file"
    assert read_key(tmp_path, environ={"GEMINI_API_KEY": "environment-key"}) == "environment-key"
    assert delete_local_key(tmp_path)
    assert read_key(tmp_path, environ={}) == ""


def test_gemini_retries_temporary_http_failures(monkeypatch):
    from rank42 import gemini_api

    attempts = []
    delays = []

    def urlopen(request, timeout):
        attempts.append(request.full_url)
        if len(attempts) < 3:
            raise urllib.error.HTTPError(request.full_url, 503, "Unavailable", {}, None)
        return io.BytesIO(b'{"name":"models/gemini-3.8-flash"}')

    monkeypatch.setattr(gemini_api.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(gemini_api.time, "sleep", delays.append)
    result = gemini_api._request("https://example.test/model", "test-key")
    assert result["name"] == "models/gemini-3.8-flash"
    assert len(attempts) == 3
    assert delays == [1, 2]


def test_gemini_generation_falls_back_when_flash_is_unavailable(tmp_path, monkeypatch):
    from rank42 import gemini_api

    write_key(tmp_path, "test-key")
    models = []

    def request(url, key, **kwargs):
        models.append(url)
        if "gemini-3.8-flash:" in url:
            raise GeminiAPIError("Gemini API returned HTTP 503", status_code=503)
        return {"candidates": [{"content": {"parts": [{
            "text": '{"recipe_id":"preset:auto"}',
        }]}}]}

    monkeypatch.setattr(gemini_api, "_request", request)
    result = generate_json(tmp_path, "Choose one recipe", model="gemini-3.8-flash")
    assert result["recipe_id"] == "preset:auto"
    assert result["_model_used"] == "gemini-3.5-flash-lite"
    assert len(models) == 2


def test_gemini_can_choose_only_validated_bounded_recipes(monkeypatch):
    monkeypatch.setattr("rank42.ai_search.list_pipelines", lambda db, limit: [])
    options = strategy_options(None, target_mode="torsion",
                               target={"torsion_group": "C2 × C8"})
    chosen = next(item for item in options if item["id"] == "preset:professor_hunt")
    decision = validate_decision(
        {"recipe_id": chosen["id"], "window": "wide", "depth": 2,
         "candidate_limit": 80, "search_seed": 123, "reason": "Widen the scan."},
        options=options, config=LIMITS, target_mode="torsion",
    )
    stages, target = execution_for_decision(
        option=chosen, decision=decision, target_mode="torsion",
        target={"torsion_group": "C2 × C8"}, config=LIMITS,
    )
    assert validate_pipeline("torsion", stages) == []
    assert target["candidate_generation"] == {
        "engine": "sampled", "sample_count": 50000, "sample_seed": 123,
        "a_min": -3000, "a_max": 3000, "b_min": 1, "b_max": 300,
    }
    assert all(int(stage["config"]["keep"]) <= 80 for stage in stages
               if "keep" in stage["config"])
    with pytest.raises(ValueError, match="unavailable"):
        validate_decision({"recipe_id": "preset:made_up"},
                          options=options, config=LIMITS)
    with pytest.raises(ValueError, match="exceeds"):
        validate_decision({"recipe_id": chosen["id"],
                           "candidate_limit": 251}, options=options,
                          config=LIMITS)


def test_curve_replay_cannot_repeat_the_same_recipe_at_the_same_depth(monkeypatch):
    monkeypatch.setattr("rank42.ai_search.list_pipelines", lambda db, limit: [])
    options = strategy_options(None, target_mode="curve", target={"curve_id": 1})
    old = {"recipe_id": "preset:auto", "window": "default", "depth": 1,
           "candidate_limit": 100, "search_seed": 1}
    with pytest.raises(ValueError, match="identical"):
        validate_decision(
            {**old, "search_seed": 2, "candidate_limit": 200},
            options=options, config=LIMITS, previous=[old], target_mode="curve",
        )


def test_goal_uses_only_linked_local_completed_rigorous_evidence():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE curves(id INTEGER PRIMARY KEY,torsion_label TEXT);
        CREATE TABLE rank_evidence(curve_id INTEGER,rigorous INTEGER,status TEXT,
                                   rigorous_lower INTEGER,exact_rank INTEGER);
        CREATE TABLE search_pipeline_runs(id INTEGER PRIMARY KEY,campaign_id INTEGER,
                                          best_curve_id INTEGER);
        CREATE TABLE search_pipeline_candidates(run_id INTEGER,curve_id INTEGER);
    """)
    db.executemany("INSERT INTO curves VALUES(?,?)", [
        (1, "C2 × C8"), (2, "Trivial"), (3, "C2 × C8")])
    db.executemany("INSERT INTO rank_evidence VALUES(?,?,?,?,?)", [
        (1, 1, "completed", 27, None),
        (2, 1, "completed", 29, None),
        (3, 0, "completed", 32, None),
        (1, 1, "inconclusive", 32, None),
    ])
    db.execute("INSERT INTO search_pipeline_runs VALUES(10,7,1)")
    db.execute("INSERT INTO search_pipeline_candidates VALUES(10,2)")
    assert verified_campaign_best(db, research_campaign_id=7,
                                  target_mode="torsion",
                                  target={"torsion_group": "C2 × C8"}) == (27, 1)
    assert verified_campaign_best(db, research_campaign_id=7,
                                  target_mode="general", target={}) == (29, 2)
    assert verified_campaign_best(db, research_campaign_id=8,
                                  target_mode="general", target={}) == (0, None)
    db.close()


def test_force_stopping_ai_job_retires_campaign_round_and_pipeline(tmp_path, monkeypatch):
    from rank42.ai_search import (
        create_ai_campaign, create_ai_round, get_ai_campaign, list_ai_rounds,
        reconcile_ai_campaigns, update_ai_campaign, update_ai_round,
    )
    from rank42.db import connect, now
    from rank42.manage_store import create_campaign, ensure_manage_schema, resume_job
    from rank42.pipeline_catalog import template_stages
    from rank42.pipeline_state import (
        create_pipeline_run, ensure_pipeline_schema, get_pipeline_run,
        update_pipeline_run,
    )
    from rank42.ui_store import create_job, ensure_ui_schema, get_job, stop_job, update_job

    db = connect(tmp_path / "rank42.db")
    ensure_manage_schema(db)
    ensure_ui_schema(db)
    ensure_pipeline_schema(db)
    research_id = create_campaign(db, name="Stop smoke", objective="Find rank 28",
                                  target_rank=28)
    ai_id = create_ai_campaign(
        db, research_campaign_id=research_id, name="AI stop smoke",
        objective="Find rank 28", target_mode="general", target={},
        config={"goal_rank": 28},
    )
    round_id = create_ai_round(db, ai_id, 1, {"recipe_id": "preset:auto"})
    run_id = create_pipeline_run(
        db, pipeline_name="AI stop smoke", target_mode="general", target={},
        stages=template_stages("auto", "general"),
        run_config={"campaign_id": research_id}, project_root=tmp_path,
    )
    update_ai_round(db, round_id, pipeline_run_id=run_id, status="running")
    update_pipeline_run(db, run_id, status="running")
    job_id = create_job(
        db, kind="ai_search", label="AI stop smoke", command=["/bin/true"],
        cwd=tmp_path, log_path=tmp_path / "job.log",
        metadata={"ai_search_campaign_id": ai_id, "campaign_id": research_id},
    )
    update_job(db, job_id, status="running", started_at=now())
    update_ai_campaign(db, ai_id, status="running", job_id=job_id)
    assert reconcile_ai_campaigns(db) == 0
    stop_job(db, job_id, force=True)
    assert get_job(db, job_id)["status"] == "killed"
    assert get_ai_campaign(db, ai_id)["status"] == "killed"
    assert list_ai_rounds(db, ai_id)[0]["status"] == "paused"
    assert get_pipeline_run(db, run_id)["status"] == "paused"
    assert db.execute("SELECT status FROM research_campaigns WHERE id=?",
                      (research_id,)).fetchone()["status"] == "paused"
    assert reconcile_ai_campaigns(db) == 0
    update_ai_campaign(db, ai_id, status="running")
    update_ai_round(db, round_id, status="running")
    update_pipeline_run(db, run_id, status="running")
    assert reconcile_ai_campaigns(db) == 1
    assert get_ai_campaign(db, ai_id)["status"] == "killed"
    assert get_pipeline_run(db, run_id)["status"] == "paused"
    monkeypatch.setattr("rank42.manage_store.enqueue_job", lambda *args, **kwargs: 123)
    assert resume_job(db, tmp_path / "rank42.db", job_id) == 123
    assert get_ai_campaign(db, ai_id)["status"] == "queued"
    assert get_ai_campaign(db, ai_id)["job_id"] == 123
    db.close()


@pytest.mark.parametrize("auto_continue,expected_rounds", [(False, 2), (True, 3)])
def test_ai_campaign_continues_from_saved_history_without_a_live_api(
    tmp_path, monkeypatch, auto_continue, expected_rounds,
):
    from rank42.ai_search import (
        continue_ai_campaign, create_ai_campaign, get_ai_campaign, list_ai_rounds,
    )
    from rank42.ai_search_runner import run_campaign
    from rank42.db import connect
    from rank42.manage_store import create_campaign, ensure_manage_schema
    from rank42.ui_store import ensure_ui_schema

    db_path = tmp_path / "rank42.db"
    db = connect(db_path)
    ensure_manage_schema(db)
    ensure_ui_schema(db)
    fake_science = tmp_path / "fake-science"
    fake_science.write_text(
        "#!/usr/bin/env python3\n"
        "import sqlite3,sys\n"
        "args=sys.argv\n"
        "db=sqlite3.connect(args[args.index('--db')+1])\n"
        "db.execute(\"UPDATE search_pipeline_runs SET status='completed',"
        "candidates_total=1,candidates_done=1 WHERE id=?\","
        "(int(args[args.index('--run-id')+1]),))\n"
        "db.commit()\n"
    )
    fake_science.chmod(0o700)
    for key, value in (
        ("science_python", str(fake_science)),
        ("ratpoints", "/bin/true"),
        ("ratpoints_backend", "CPU"),
    ):
        db.execute(
            "INSERT INTO ui_settings(key,value_json,updated_at) VALUES(?,?,?)",
            (key, json.dumps(value), "now"),
        )
    db.commit()
    research_id = create_campaign(db, name="AI smoke", objective="Seek rank 28",
                                  target_rank=28)
    config = {
        **LIMITS, "goal_rank": 28, "max_total_minutes": 10,
        "max_run_minutes": 1, "max_rounds": 2, "retention_floor": 3,
        "auto_continue": auto_continue, "checkpoint_minutes": 10,
        "checkpoint_rounds": 2, "hard_stop_minutes": 10,
        "hard_stop_rounds": 3,
        "target_summary": {"pool_mode": "seeded"}, "db_path": str(db_path),
        "initial_decision": {
            "recipe_id": "preset:auto", "window": "default", "depth": 1,
            "candidate_limit": 10, "search_seed": 1, "reason": "Start broad.",
        },
    }
    ai_id = create_ai_campaign(
        db, research_campaign_id=research_id, name="AI smoke",
        objective="Seek rank 28", target_mode="general",
        target={"pool_mode": "seeded", "pool_size": 10, "random_seed": 42,
                "u_min": 1, "u_max": 10, "v_min": 1, "v_max": 10},
        config=config,
    )
    choices = iter(["preset:prime_sieve_funnel", "preset:generator_breaker",
                    "preset:selmer_gate", "preset:covering_forest"])
    monkeypatch.setattr("rank42.ai_search_runner.plan_strategy", lambda *args, **kwargs: {
        "recipe_id": next(choices), "window": "default", "depth": 1,
        "candidate_limit": 10, "search_seed": 2,
        "reason": "Try another point-search method.",
    })
    cleaned_runs = []
    def record_cleanup(connection, run_id):
        from rank42.pipeline_retention import cleanup_pipeline_run
        cleaned_runs.append(run_id)
        return cleanup_pipeline_run(connection, run_id)

    monkeypatch.setattr("rank42.ai_search_runner.cleanup_pipeline_run", record_cleanup)
    run_campaign(db, Path(__file__).resolve().parents[1], ai_id)
    assert get_ai_campaign(db, ai_id)["status"] == "budget_exhausted"
    rounds = list_ai_rounds(db, ai_id)
    assert len(rounds) == expected_rounds
    assert all(row["status"] == "completed" for row in rounds)
    assert len({row["pipeline_run_id"] for row in rounds}) == expected_rounds
    assert cleaned_runs == [row["pipeline_run_id"] for row in rounds]
    assert all(
        json.loads(db.execute(
            "SELECT run_config_json FROM search_pipeline_runs WHERE id=?",
            (run_id,),
        ).fetchone()["run_config_json"])["retention_floor"] == 3
        for run_id in cleaned_runs
    )
    assert db.execute("SELECT status FROM research_campaigns WHERE id=?",
                      (research_id,)).fetchone()["status"] == "paused"
    if not auto_continue:
        assert get_ai_campaign(db, ai_id)["next_decision_json"]
        continue_ai_campaign(db, ai_id, minutes=10, rounds=1)
        run_campaign(db, Path(__file__).resolve().parents[1], ai_id)
        assert len(list_ai_rounds(db, ai_id)) == 3
        assert get_ai_campaign(db, ai_id)["status"] == "budget_exhausted"
    db.close()


def test_ai_retention_keeps_certified_near_frontier_and_paused_work(monkeypatch):
    from rank42.pipeline_retention import cleanup_pipeline_run

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE search_pipeline_runs (
            id INTEGER, started_at TEXT, run_config_json TEXT, status TEXT
        );
        CREATE TABLE curves (
            id INTEGER, created_at TEXT, exact_rank INTEGER,
            descent_lower INTEGER, generic_lower INTEGER
        );
        CREATE TABLE search_pipeline_candidates (
            run_id INTEGER, curve_id INTEGER, score_provenance_json TEXT
        );
    """)
    db.execute("INSERT INTO search_pipeline_runs VALUES (10,'2026-01-01',?, 'failed')",
               (json.dumps({"retention_floor": 26, "preserve_near_frontier": True,
                            "frontier_margin": 10}),))
    db.execute("INSERT INTO search_pipeline_runs VALUES (11,'2026-01-01','{}','paused')")
    db.executemany("INSERT INTO curves VALUES (?,'2026-01-02',NULL,?,NULL)",
                   [(1, 27), (2, 23), (3, 5), (4, 0)])
    for curve_id in (2, 3, 4):
        db.execute("INSERT INTO search_pipeline_candidates VALUES (10,?,?)",
                   (curve_id, json.dumps({"created_by_pipeline_run": 10})))
    db.execute("INSERT INTO search_pipeline_candidates VALUES (11,4,'{}')")
    monkeypatch.setattr(
        "rank42.pipeline_retention.get_curve_research_state",
        lambda connection, curve_id: {"rigorous_lower": connection.execute(
            "SELECT descent_lower FROM curves WHERE id=?", (curve_id,),
        ).fetchone()[0]},
    )
    result = cleanup_pipeline_run(db, 10, dry_run=True)
    assert result["frontier_threshold"] == 17
    assert result["frontier_retained"] == 1
    assert result["active_elsewhere"] == 1
    assert result["curve_ids_removed"] == [3]
