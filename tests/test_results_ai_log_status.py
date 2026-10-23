"""The Jobs terminal follows the active Pipeline inside an AI campaign."""

import json
import sqlite3

from rank42.ui_pages import results_page


def test_ai_raw_log_shows_current_round_stage_and_campaign_best(monkeypatch):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE ai_search_campaigns(id INTEGER PRIMARY KEY,best_lower INTEGER,
                                         config_json TEXT);
        CREATE TABLE ai_search_rounds(id INTEGER PRIMARY KEY,ai_campaign_id INTEGER,
                                      round_number INTEGER,pipeline_run_id INTEGER,
                                      status TEXT);
        CREATE TABLE search_pipeline_candidates(id INTEGER PRIMARY KEY,run_id INTEGER,curve_id INTEGER,
            parameter TEXT,rigorous_lower INTEGER,exact_rank INTEGER,updated_at TEXT);
        CREATE TABLE curves(id INTEGER PRIMARY KEY,descent_lower INTEGER,
                            exact_rank INTEGER,updated_at TEXT);
    """)
    db.execute("INSERT INTO ai_search_campaigns VALUES(1,3,?)",
               (json.dumps({"max_rounds": 5}),))
    db.executemany("INSERT INTO ai_search_rounds VALUES(?,?,?,?,?)", [
        (1, 1, 1, 10, "completed"), (2, 1, 2, 11, "running"),
    ])
    db.execute("INSERT INTO curves VALUES(4,2,NULL,'2026-10-23T12:00:00')")
    db.execute("INSERT INTO search_pipeline_candidates VALUES(1,11,4,'7/3',2,NULL,?)",
               ("2026-10-23T12:00:00",))
    runs = {
        10: {"id": 10, "stages_json": '[{"id":"affine_search"}]',
             "current_stage_index": 1, "current_stage_id": "affine_search",
             "best_lower": 3, "best_curve_id": None},
        11: {"id": 11, "stages_json": '[{"id":"integral_seed"},{"id":"independence"}]',
             "current_stage_index": 2, "current_stage_id": "independence",
             "best_lower": 2, "best_curve_id": 4},
    }
    monkeypatch.setattr(results_page, "get_pipeline_run", lambda _db, run_id: runs[run_id])
    row = {"kind": "ai_search", "status": "running", "started_at": None,
           "created_at": None, "finished_at": None,
           "metadata_json": '{"ai_search_campaign_id":1}'}
    log = ("ROUND 1/5 · Pipeline #10\n"
           "[pipeline] candidate=1 stage=1 affine_search\n"
           "ROUND 2/5 · Pipeline #11\n")

    assert results_page._raw_log_progress(db, row, log=log)[1] == (
        "Round 2/5 · 2/2 · Exact Independence"
    )
    assert results_page._best_rank_found(db, row, log) == "≥ 3"
    assert results_page._newest_pipeline_discovery(db, row) == "t=7/3 · ≥2"
    db.close()
