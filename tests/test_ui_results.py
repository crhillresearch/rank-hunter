import json
import sqlite3

from rank42.ui_results import (
    chart_history_sums,
    pipeline_retention_quartic_summary,
    summarize_job,
)


def row(**kw):
    defaults = {
        "id": 7,
        "kind": "kihara_chart_batch",
        "label": "batch",
        "status": "succeeded",
        "exit_code": 0,
        "metadata_json": json.dumps({"count": 2}),
    }
    defaults.update(kw)
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    vals = list(defaults.values())
    select_list = ",".join(f"? AS {name}" for name in defaults)
    return con.execute(f"SELECT {select_list}", vals).fetchone()


def test_pipeline_retention_quartic_summary_explains_persisted_hits():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE search_pipeline_runs(
            id INTEGER PRIMARY KEY,
            run_config_json TEXT
        );
        CREATE TABLE search_pipeline_candidates(
            id INTEGER PRIMARY KEY,
            run_id INTEGER,
            parameter TEXT,
            curve_id INTEGER,
            rigorous_lower INTEGER
        );
        CREATE TABLE quartic_searches(
            id INTEGER PRIMARY KEY,
            curve_id INTEGER,
            parameter TEXT
        );
        CREATE TABLE quartic_points(
            id INTEGER PRIMARY KEY,
            search_id INTEGER,
            created_at TEXT
        );
        """
    )
    db.execute(
        "INSERT INTO search_pipeline_runs(id,run_config_json) VALUES(?,?)",
        (3, json.dumps({"retention_floor": 5})),
    )
    db.execute(
        """INSERT INTO search_pipeline_candidates(
               id,run_id,parameter,curve_id,rigorous_lower
           ) VALUES(?,?,?,?,?)""",
        (10, 3, "2337/77", None, 4),
    )
    db.execute(
        """INSERT INTO search_pipeline_candidates(
               id,run_id,parameter,curve_id,rigorous_lower
           ) VALUES(?,?,?,?,?)""",
        (11, 3, "-825/32", 30, 4),
    )
    db.execute(
        "INSERT INTO quartic_searches(id,curve_id,parameter) VALUES(?,?,?)",
        (541, None, "2337/77"),
    )
    db.executemany(
        "INSERT INTO quartic_points(id,search_id,created_at) VALUES(?,?,?)",
        [
            (1, 541, "2026-10-03T11:22:31+00:00"),
            (2, 541, "2026-10-03T11:22:32+00:00"),
        ],
    )
    db.commit()

    job = row(
        kind="pipeline_search",
        metadata_json=json.dumps({"pipeline_run_id": 3}),
        created_at="2026-10-03T11:00:00+00:00",
        started_at="2026-10-03T11:00:00+00:00",
        finished_at="2026-10-03T12:00:00+00:00",
    )
    summary = pipeline_retention_quartic_summary(db, job)

    assert summary["run_id"] == 3
    assert summary["retention_floor"] == 5
    assert summary["quartic_hits"] == 2
    assert summary["candidates"] == [
        {
            "parameter": "2337/77",
            "rigorous_lower": 4,
            "quartic_hits": 2,
            "quartic_searches": 1,
        }
    ]


def test_pipeline_retention_quartic_summary_is_quiet_at_zero_floor():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE search_pipeline_runs(
            id INTEGER PRIMARY KEY,
            run_config_json TEXT
        );
        CREATE TABLE search_pipeline_candidates(
            id INTEGER PRIMARY KEY,
            run_id INTEGER,
            parameter TEXT,
            curve_id INTEGER,
            rigorous_lower INTEGER
        );
        CREATE TABLE quartic_searches(
            id INTEGER PRIMARY KEY,
            curve_id INTEGER,
            parameter TEXT
        );
        CREATE TABLE quartic_points(
            id INTEGER PRIMARY KEY,
            search_id INTEGER,
            created_at TEXT
        );
        """
    )
    db.execute(
        "INSERT INTO search_pipeline_runs(id,run_config_json) VALUES(?,?)",
        (4, json.dumps({"retention_floor": 0})),
    )
    db.commit()

    job = row(
        kind="pipeline_search",
        metadata_json=json.dumps({"pipeline_run_id": 4}),
    )
    summary = pipeline_retention_quartic_summary(db, job)

    assert summary == {
        "run_id": 4,
        "retention_floor": 0,
        "quartic_hits": 0,
        "candidates": [],
    }


def test_chart_no_hits_summary():
    r = row()
    log = """candidates          = 2
charts/candidate    = 2
[1/2] curve #1 t=1 score=1.0
    [chart 1/2]
      H=1000      4 point(s) in 0.1s
    [chart 2/2]
      H=1000      TIMEOUT -> skip higher stages for this chart
    no new native quartic x-fibers found -> no elliptic model needed
[2/2] curve #2 t=2 score=0.9
    [chart 1/2]
      H=1000      reuse search #3 (4 point(s))
    [chart 2/2]
      H=1000      4 point(s) in 0.1s
    no new native quartic x-fibers found -> no elliptic model needed
[done] Kihara Mobius-chart scout complete
"""
    s = summarize_job(r, log)
    assert s["result_status"] == "NO HITS"
    assert s["candidates_tested"] == 2
    assert s["candidates_completed"] == 2
    assert s["charts_searched"] == 4
    assert s["quartic_stages_checked"] == 4
    assert s["timeouts"] == 1
    assert s["new_native_fibers"] == 0
    assert s["best_screened_rank"] == 14


def test_chart_record_candidate_summary():
    r = row()
    log = """candidates          = 1
charts/candidate    = 8
[1/1] curve #442 t=1 score=9.8
    [chart 1/8]
      H=1000      6 point(s) in 0.1s
    NEW NATIVE QUARTIC HIT(S): 3; materializing 14-section model...
    mapped unique points outside known ±sections = 2
    [height] candidate against basis size 14...
        PASS -> screened basis size 15
    [height] candidate against basis size 15...
        PASS -> screened basis size 16
    EXTRA-POINT HIT: basis 14 -> 16 (screened)
"""
    s = summarize_job(r, log)
    assert s["result_status"] == "RECORD CANDIDATE"
    assert s["new_native_fibers"] == 3
    assert s["mapped_extra_points"] == 2
    assert s["rank_growth_curves"] == 1
    assert s["best_screened_rank"] == 16
    assert s["interesting"] is True


def test_nagao_summary():
    r = row(kind="nagao_search", label="nagao")
    log = """[stage 1/2] retained 3,000 from 1,234,567
[stage 2/2] rescored 3,000; retained 300
[done] wrote 300 candidates to /tmp/scout.jsonl
[best] (10.3821, 1, 2, [])
[best] (9.9, 2, 3, [])
"""
    s = summarize_job(r, log)
    assert s["result_status"] == "CANDIDATES READY"
    assert s["parameters_screened"] == 1234567
    assert s["stage_survivors"] == [3000, 300]
    assert s["candidates_written"] == 300
    assert s["best_nagao_score"] == 10.3821


def test_history_sums():
    sums = chart_history_sums([
        {"kind": "kihara_chart_batch", "candidates_tested": 20, "new_native_fibers": 1, "mapped_extra_points": 1, "rank_growth_curves": 0, "best_screened_rank": 14},
        {"kind": "kihara_chart_target", "candidates_tested": 1, "new_native_fibers": 2, "mapped_extra_points": 2, "rank_growth_curves": 1, "best_screened_rank": 16},
        {"kind": "nagao_search", "candidates_tested": 999},
    ])
    assert sums == {
        "candidates_tested": 21,
        "new_native_fibers": 3,
        "mapped_extra_points_reported": 3,
        "rank_growth_runs": 1,
        "best_screened_rank": 16,
    }


def test_running_chart_summary_distinguishes_started_from_completed():
    r = row(status="running", exit_code=None)
    log = """candidates          = 20
charts/candidate    = 8
[1/20] curve #1 t=1 score=1.0
[candidate done] 1/20
[2/20] curve #2 t=2 score=0.9
    [chart 1/8]
      H=1000      4 point(s) in 0.1s
"""
    s = summarize_job(r, log)
    assert s["result_status"] == "RUNNING"
    assert s["candidates_tested"] == 2
    assert s["candidates_completed"] == 1
    assert s["current_candidate"] == 2
    assert s["current_curve_id"] == 2
    assert s["current_parameter"] == "2"


def test_general_hunt_live_and_hit_summary():
    r = row(kind="playground", label="workbench", status="succeeded", metadata_json=json.dumps({"target": 2}))
    log = """RANK HUNTER GENERAL HUNT / PLAYGROUND
PLAYGROUND planned  = 20
[playground 1/20] mode=seeded source=u=1,v=1 A=-1 B=1
[playground done] 1/20 candidates=1 screened=0 rigorous=0 status=insufficient_points
[playground 2/20] mode=seeded source=u=1,v=2 A=2 B=1
[playground hit] curve #9 rigorous_lower=2 A=2 B=1 points=2
[playground done] 2/20 candidates=3 screened=2 rigorous=2 status=certified
RANK42_PLAYGROUND_RESULT={"status":"PLAYGROUND HIT","mode":"seeded","target_lower":2,"trials_planned":20,"trials_tested":2,"rigorous_hits":1,"best_rigorous_lower":2}
"""
    s = summarize_job(r, log)
    assert s["result_status"] == "PLAYGROUND HIT"
    assert s["rigorous_hits"] == 1
    assert s["best_rigorous_lower"] == 2
    assert s["trials_tested"] == 2
    assert s["current_A"] == 2
    assert s["interesting"] is True


def test_general_hunt_summary_tracks_scoring_and_search():
    r = row(kind="general_hunt", label="General Hunt", status="succeeded", metadata_json=json.dumps({"target": 5, "nagao_bound": 100}))
    log = """RANK HUNTER GENERAL HUNT
[general-hunt score] 100/5000 best=2.10000000
[general-hunt score] 5000/5000 best=4.25000000
[general-hunt shortlist] kept=100 best=4.25 cutoff=2.1
GENERAL-HUNT planned = 100
[general-hunt 1/100] mode=seeded source=u=2,v=7 A=44 B=4 nagao=4.25000000
[general-hunt stage] 1/100 H=100 rational_points=5 runtime=0.100
[general-hunt done] 1/100 rational_points=5 screened=4 rigorous=0 status=screened_below_target
[general-hunt 2/100] mode=seeded source=u=3,v=9 A=71 B=9 nagao=4.10000000
[general-hunt stage] 2/100 H=100 rational_points=7 runtime=0.100
[general-hunt hit] curve #44 rigorous_lower=5 A=71 B=9 points=5 nagao=4.10000000
[general-hunt done] 2/100 rational_points=7 screened=6 rigorous=5 status=certified
RANK42_GENERAL_HUNT_RESULT={"status":"GENERAL HUNT HIT","mode":"general_hunt_seeded","target_lower":5,"pool_scored":5000,"nagao_bound":100,"best_nagao_score":4.25,"shortlist_planned":100,"shortlist_searched":2,"ratpoints_timeouts":0,"best_rational_point_count":7,"best_screened_rank":6,"rigorous_hits":1,"best_rigorous_lower":5}
"""
    s = summarize_job(r, log)
    assert s["result_status"] == "GENERAL HUNT HIT"
    assert s["pool_scored"] == 5000
    assert s["shortlist_searched"] == 2
    assert s["best_rational_point_count"] == 7
    assert s["best_screened_rank"] == 6
    assert s["best_rigorous_lower"] == 5
    assert s["current_A"] == 71
    assert s["current_nagao"] == 4.1


def test_playground_focus_summary_distinguishes_exact_target_search():
    from rank42.ui_results import summarize_job

    r = row(
        kind="playground_focus",
        label="Focus Playground curve #153 · seek ≥7",
        status="succeeded",
        metadata_json=json.dumps({"curve_id": 153, "target": 7}),
    )
    log = "\n".join([
        "[focus stage] H=10000 rational_points=9 runtime=0.250",
        "[focus hit] curve #153 rigorous_lower=7 points=7",
        'RANK42_PLAYGROUND_FOCUS_RESULT={"status":"FOCUS RANK GROWTH","curve_id":153,"target_lower":7,"rational_points":9,"screened_rank":7,"best_rigorous_lower":7,"ratpoints_height_reached":10000,"ratpoints_timed_out":false}',
    ])
    out = summarize_job(r, log)
    assert out["result_status"] == "FOCUS RANK GROWTH"
    assert out["curve_id"] == 153
    assert out["target_lower"] == 7
    assert out["best_rigorous_lower"] == 7
    assert out["interesting"] is True


def test_playground_focus_summary_surfaces_subgroup_diagnostics():
    r = row(
        kind="playground_focus",
        label="Focus Playground curve #181 · seek ≥7",
        status="succeeded",
        metadata_json=json.dumps({"curve_id": 181, "target": 7}),
    )
    log = 'RANK42_PLAYGROUND_FOCUS_RESULT={"status":"FOCUS COMPLETE","curve_id":181,"target_lower":7,"focus_strategy":"subgroup_aware","rational_points":240,"unique_exact_points_seen":240,"subgroup_candidates_screened":128,"numerically_novel_candidates":3,"best_novelty_relative_residual":0.0025,"exact_candidate_attempts":4,"exact_dependent":3,"exact_inconclusive":1,"height_chunk_failures":0,"screened_rank":7,"best_rigorous_lower":6,"ratpoints_height_reached":100000,"ratpoints_timed_out":false}'
    out = summarize_job(r, log)
    assert out["focus_strategy"] == "subgroup_aware"
    assert out["unique_exact_points_seen"] == 240
    assert out["subgroup_candidates_screened"] == 128
    assert out["numerically_novel_candidates"] == 3
    assert out["exact_candidate_attempts"] == 4
    assert out["best_rigorous_lower"] == 6


def test_mestre_search_summary_tracks_exact_rank_growth():
    r = row(
        kind="mestre_quartic_batch",
        label="Mestre batch",
        status="succeeded",
        metadata_json=json.dumps({"count": 2, "search_mode": "integer"}),
    )
    log = """RANK HUNTER MESTRE NATIVE-QUARTIC SEARCH
candidates          = 2
[1/2] curve #90 t=1069/4 score=8.0
    [mestre stage] mode=integer H=1000 points=12 runtime=0.10
    [mestre stage] mode=integer H=10000 points=14 runtime=0.20
    NEW NATIVE QUARTIC HIT(S): 1 new x-fibre(s)
    mapped unique points outside known subgroup = 2
    [mestre hit] curve #90 rigorous_lower=12 points=12
[candidate done] 1/2
[2/2] curve #91 t=842/9 score=7.5
    [mestre stage] mode=integer H=1000 TIMEOUT
    no new native quartic x-fibres
    mapped unique points outside known subgroup = 0
[candidate done] 2/2
RANK42_MESTRE_SEARCH_RESULT={"status":"MESTRE COMPLETE","candidates_planned":2,"candidates_completed":2,"new_native_fibers":1,"mapped_extra_points":2,"rank_growth_curves":1,"best_rigorous_lower":12,"best_screened_rank":12,"ratpoints_timeouts":1,"subgroup_candidates_screened":2,"numerically_novel_candidates":1,"exact_candidate_attempts":2,"exact_dependent":1,"exact_inconclusive":0,"search_mode":"integer"}
"""
    s = summarize_job(r, log)
    assert s["result_status"] == "RANK GROWTH"
    assert s["candidates_tested"] == 2
    assert s["candidates_completed"] == 2
    assert s["new_native_fibers"] == 1
    assert s["mapped_extra_points"] == 2
    assert s["best_rigorous_lower"] == 12
    assert s["exact_candidate_attempts"] == 2
    assert s["timeouts"] == 1
    assert s["search_mode"] == "integer"


def test_history_sums_include_mestre_searches():
    sums = chart_history_sums([
        {"kind": "kihara_chart_batch", "candidates_tested": 2, "new_native_fibers": 1, "mapped_extra_points": 1, "rank_growth_curves": 0, "best_screened_rank": 14},
        {"kind": "mestre_quartic_batch", "candidates_tested": 3, "new_native_fibers": 2, "mapped_extra_points": 2, "rank_growth_curves": 1, "best_screened_rank": 12},
    ])
    assert sums["candidates_tested"] == 5
    assert sums["new_native_fibers"] == 3
    assert sums["mapped_extra_points_reported"] == 3
    assert sums["rank_growth_runs"] == 1
    assert sums["best_screened_rank"] == 14


def test_mestre_pgl2_summary_carries_adaptive_chart_strategy_counts():
    import sqlite3
    from rank42.ui_results import summarize_mestre_job

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE jobs(id INTEGER, kind TEXT, label TEXT, status TEXT, exit_code INTEGER, metadata_json TEXT)")
    conn.execute(
        "INSERT INTO jobs VALUES(1,'mestre_quartic_target','adaptive','succeeded',0,?)",
        ('{"search_geometry":"pgl2","chart_strategy":"hybrid"}',),
    )
    row = conn.execute("SELECT * FROM jobs").fetchone()
    log = '''RANK42_MESTRE_SEARCH_RESULT={"status":"MESTRE PGL2 COMPLETE","candidates_planned":1,"candidates_completed":1,"best_rigorous_lower":16,"best_screened_rank":16,"search_geometry":"pgl2","search_mode":"pgl2-both","chart_strategy":"hybrid","charts_searched":24,"charts_base":6,"charts_subgroup":12,"charts_free":6,"discovered_anchor_fibres":87}\n'''
    summary = summarize_mestre_job(row, log)
    assert summary["chart_strategy"] == "hybrid"
    assert summary["charts_searched"] == 24
    assert summary["charts_base"] == 6
    assert summary["charts_subgroup"] == 12
    assert summary["charts_free"] == 6
    assert summary["discovered_anchor_fibres"] == 87



def test_structured_target_does_not_call_existing_anchor_pool_a_new_fiber():
    r = row(
        kind="target_plugin",
        label="Target #3010 · Mestre/Fermigier Sextuple",
        status="succeeded",
        metadata_json=json.dumps({"curve_id": 3010}),
    )
    log = (
        'RANK42_MESTRE_SEARCH_RESULT={"status":"MESTRE PGL2 COMPLETE",'
        '"candidates_planned":1,"candidates_completed":1,'
        '"new_native_fibers":0,"mapped_extra_points":0,'
        '"numerically_novel_candidates":0,"best_rigorous_lower":18,'
        '"best_screened_rank":18,"rank_growth_curves":0,'
        '"discovered_anchor_fibres":7}\n'
    )
    s = summarize_job(r, log)
    assert s["result_status"] == "NO HITS"
    assert s["interesting"] is False
    assert s["new_native_fibers"] == 0
    assert s["discovered_anchor_fibres"] == 7
