import json
import sqlite3
from types import SimpleNamespace

import pytest

import rank42.pipeline_runner as runner
from rank42.db import connect as connect_core, upsert_curve, update_curve
from rank42.points import upsert_point
from rank42.rank_evidence import record_rank_evidence
from rank42.pipeline_state import (
    create_pipeline_run,
    ensure_pipeline_schema,
    get_pipeline_candidate,
    pipeline_derivations,
    pipeline_run_payload,
    pipeline_strategy_steps,
    upsert_pipeline_candidate,
)


def _db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("CREATE TABLE curves(id INTEGER PRIMARY KEY)")
    db.execute(
        """CREATE TABLE points(
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               curve_id INTEGER,
               exact_verified INTEGER NOT NULL DEFAULT 0
           )"""
    )
    ensure_pipeline_schema(db)
    return db


def _rank_evidence(db, curve_id, *, lower=None, upper=None, key=None):
    return record_rank_evidence(
        db,
        curve_id=int(curve_id),
        model=["0", "0", "0", "-1", "0"],
        data={
            "engine": "pipeline-stop-test",
            "evidence_type": "rank_bounds",
            "status": "completed",
            "rigorous": True,
            "rigorous_lower": lower,
            "rigorous_upper": upper,
            "exact_rank": None,
            "conditional_analytic_upper": None,
            "numerical_rank_signal": None,
            "assumptions": [],
            "points_found": [],
            "options": {},
        },
        key=key,
    )


def _run(db, name="Population"):
    rid = create_pipeline_run(
        db,
        pipeline_name=name,
        target_mode="general",
        target={"pool_mode": "open"},
        stages=[{"id": "nagao_screen", "config": {}}],
        run_config={"target_rank": 8},
    )
    return pipeline_run_payload(
        db.execute("SELECT * FROM search_pipeline_runs WHERE id=?", (rid,)).fetchone()
    )


def test_population_queue_persists_candidate_score_provenance():
    db = _db()
    run = _run(db, "Score provenance queue")
    provenance = {
        "algorithm": "short-nagao-v1",
        "prime_bound": 523,
        "primes_used": [5, 7, 11],
        "terms_used": 3,
        "scorer": "short_weierstrass_exact_character_sum",
    }
    runner._queue_population_items(
        db,
        run["id"],
        [{
            "provider_key": "general",
            "candidate": {
                "parameter": "A=1,B=2",
                "score": 4.5,
                "score_provenance": provenance,
            },
        }],
    )
    row = get_pipeline_candidate(db, run["id"], "general", "A=1,B=2")
    assert json.loads(row["score_provenance_json"]) == provenance


def test_select_survivors_prunes_population_by_rank_then_score():
    db = _db()
    run = _run(db)
    items = []
    for curve_id, parameter, score in (
        (1, "p1", 10.0),
        (2, "p2", 30.0),
        (3, "p3", 20.0),
    ):
        db.execute("INSERT INTO curves(id) VALUES(?)", (curve_id,))
        upsert_pipeline_candidate(
            db,
            run_id=run["id"],
            provider_key="general",
            parameter=parameter,
            curve_id=curve_id,
            score=score,
            status="running",
            rigorous_lower=4,
        )
        items.append(
            {
                "provider_key": "general",
                "candidate": {"parameter": parameter, "score": score},
            }
        )

    survivors = runner._select_survivors_population(
        db,
        run=run,
        active_items=items,
        stage_index=2,
        config={"keep": 2, "ranking": "rank_then_score"},
    )

    assert [item["candidate"]["parameter"] for item in survivors] == ["p2", "p3"]
    assert get_pipeline_candidate(db, run["id"], "general", "p1")["status"] == "funnel_pruned"
    assert get_pipeline_candidate(db, run["id"], "general", "p2")["status"] == "running"
    assert get_pipeline_candidate(db, run["id"], "general", "p3")["status"] == "running"


def test_select_survivors_uses_and_names_exact_point_inventory():
    db = _db()
    run = _run(db, "Inventory semantics")
    items = []
    for curve_id, parameter, score in (
        (1, "inventory-rich", 5.0),
        (2, "score-rich", 50.0),
    ):
        db.execute("INSERT INTO curves(id) VALUES(?)", (curve_id,))
        upsert_pipeline_candidate(
            db,
            run_id=run["id"],
            provider_key="general",
            parameter=parameter,
            curve_id=curve_id,
            score=score,
            status="running",
            rigorous_lower=4,
        )
        items.append({
            "provider_key": "general",
            "candidate": {"parameter": parameter, "score": score},
        })

    # These exact points predate this selection stage. They are inventory, not
    # fresh yield from the current Pipeline run.
    db.executemany(
        "INSERT INTO points(curve_id,exact_verified) VALUES(?,1)",
        [(1,), (1,), (1,)],
    )
    db.execute(
        "INSERT INTO points(curve_id,exact_verified) VALUES(?,1)",
        (2,),
    )
    db.commit()

    survivors = runner._select_survivors_population(
        db,
        run=run,
        active_items=items,
        stage_index=2,
        config={"keep": 1, "ranking": "rank_then_yield_then_score"},
    )

    assert [item["candidate"]["parameter"] for item in survivors] == ["inventory-rich"]
    rich = get_pipeline_candidate(db, run["id"], "general", "inventory-rich")
    poor = get_pipeline_candidate(db, run["id"], "general", "score-rich")
    rich_result = runner._stage_entry(rich, 2, "select_survivors")
    poor_result = runner._stage_entry(poor, 2, "select_survivors")
    assert rich_result["exact_point_inventory"] == 3
    assert poor_result["exact_point_inventory"] == 1
    assert "point_yield" not in rich_result
    assert "point_yield" not in poor_result
    assert rich_result["ranking"] == "rank_then_yield_then_score"


def test_select_survivors_rolls_back_partial_selection_batch(monkeypatch):
    db = _db()
    run = _run(db, "Atomic survivors")
    items = []
    for curve_id, parameter, score in (
        (1, "p1", 30.0),
        (2, "p2", 20.0),
        (3, "p3", 10.0),
    ):
        db.execute("INSERT INTO curves(id) VALUES(?)", (curve_id,))
        upsert_pipeline_candidate(
            db,
            run_id=run["id"],
            provider_key="general",
            parameter=parameter,
            curve_id=curve_id,
            score=score,
            status="running",
            rigorous_lower=4,
        )
        items.append({
            "provider_key": "general",
            "candidate": {"parameter": parameter, "score": score},
        })

    original = runner._write_stage_result
    calls = {"n": 0}

    def fail_after_first_write(*args, **kwargs):
        original(*args, **kwargs)
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("injected partial selection failure")

    monkeypatch.setattr(runner, "_write_stage_result", fail_after_first_write)

    with pytest.raises(RuntimeError, match="injected partial selection failure"):
        runner._select_survivors_population(
            db,
            run=run,
            active_items=items,
            stage_index=2,
            config={"keep": 2, "ranking": "rank_then_score"},
        )

    for parameter in ("p1", "p2", "p3"):
        row = get_pipeline_candidate(db, run["id"], "general", parameter)
        assert row["status"] == "running"
        assert runner._stage_entry(row, 2, "select_survivors") is None


def test_adaptive_ladder_rolls_back_partial_round_selection_batch(monkeypatch):
    db = _db()
    run = _run(db, "Atomic adaptive")
    items = []
    for curve_id, parameter, score in (
        (1, "p1", 30.0),
        (2, "p2", 20.0),
        (3, "p3", 10.0),
    ):
        db.execute("INSERT INTO curves(id) VALUES(?)", (curve_id,))
        upsert_pipeline_candidate(
            db,
            run_id=run["id"],
            provider_key="general",
            parameter=parameter,
            curve_id=curve_id,
            score=score,
            status="running",
            rigorous_lower=4,
        )
        items.append({
            "provider_key": "general",
            "candidate": {"parameter": parameter, "score": score},
        })

    original = runner._write_stage_result
    calls = {"n": 0}

    def fail_after_first_write(*args, **kwargs):
        original(*args, **kwargs)
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("injected adaptive round failure")

    monkeypatch.setattr(runner, "_write_stage_result", fail_after_first_write)

    with pytest.raises(RuntimeError, match="injected adaptive round failure"):
        runner._adaptive_ladder_population(
            db,
            run=run,
            run_config={
                "ratpoints": None,
                "certificate_timeout": 120,
                "exact_candidates": 64,
            },
            target={},
            active_items=items,
            stage_index=2,
            config={
                "bands": [[2, 50]],
                "keeps": [2],
                "ranking": "rank_then_yield_then_score",
                "charts": 5,
                "heights": [10000],
                "timeout": 1,
            },
            cache={},
        )

    for parameter in ("p1", "p2", "p3"):
        row = get_pipeline_candidate(db, run["id"], "general", parameter)
        assert row["status"] == "running"
        assert runner._adaptive_round(
            runner._adaptive_result(row, 2),
            1,
        ) is None


def test_adaptive_point_search_outcome_vocabulary():
    assert runner._point_search_outcome({
        "completed_searches": 0,
        "resume_skipped": 0,
        "timeouts": 3,
        "failures": 0,
    }) == "timeout"
    assert runner._point_search_outcome({
        "completed_searches": 2,
        "resume_skipped": 0,
        "timeouts": 1,
        "failures": 0,
    }) == "partial"
    assert runner._point_search_outcome({
        "completed_searches": 2,
        "resume_skipped": 0,
        "timeouts": 0,
        "failures": 0,
    }) == "completed"
    assert runner._point_search_outcome({
        "completed_searches": 0,
        "resume_skipped": 0,
        "timeouts": 0,
        "failures": 1,
    }) == "error"


def test_adaptive_resume_only_reuses_completed_search_outcome():
    assert runner._adaptive_search_reusable({
        "status": "completed",
        "search_outcome": "completed",
    }) is True
    assert runner._adaptive_search_reusable({
        "status": "completed",
        "search_outcome": "partial",
        "completed_searches": 1,
        "timeouts": 1,
    }) is False
    assert runner._adaptive_search_reusable({
        "status": "completed",
        "search_outcome": "timeout",
        "completed_searches": 0,
        "timeouts": 2,
    }) is False
    assert runner._adaptive_search_reusable({
        "status": "completed",
        "completed_searches": 0,
        "timeouts": 2,
    }) is False
    assert runner._adaptive_search_reusable({
        "status": "completed",
    }) is True


def test_adaptive_ladder_persists_timeout_outcome_without_marking_method_complete(monkeypatch):
    db = _db()
    run = _run(db, "Adaptive timeout outcome")
    db.execute("INSERT INTO curves(id) VALUES(1)")
    upsert_pipeline_candidate(
        db,
        run_id=run["id"],
        provider_key="general",
        parameter="p1",
        curve_id=1,
        score=10.0,
        status="running",
        rigorous_lower=4,
    )
    item = {
        "provider_key": "general",
        "candidate": {"parameter": "p1", "score": 10.0},
    }

    monkeypatch.setattr(
        runner,
        "_materialize_population_context",
        lambda db, *, run, item, cache: {
            "curve_id": 1,
            "E": None,
            "source_E": None,
            "family": None,
            "plugin": None,
            "variant": None,
            "parameter": "p1",
            "rank_order": 1,
        },
    )
    monkeypatch.setattr(
        runner,
        "_snapshot",
        lambda db, curve_id: {
            "rigorous_lower": 4,
            "rigorous_upper": None,
            "exact_rank": None,
            "rank_inconsistent": False,
            "rigorous_witnesses": 0,
        },
    )
    captured = {}

    def fake_point_tier(*args, **kwargs):
        captured.update(kwargs)
        return {
            "completed_searches": 0,
            "resume_skipped": 0,
            "timeouts": 2,
            "failures": 0,
            "exact_points": 0,
            "rank_growth": 0,
            "rigorous_lower": 4,
        }

    monkeypatch.setattr(runner, "run_auto_point_tier", fake_point_tier)

    survivors = runner._adaptive_ladder_population(
        db,
        run=run,
        run_config={
            "ratpoints": None,
            "certificate_timeout": 120,
            "exact_candidates": 64,
        },
        target={},
        active_items=[item],
        stage_index=2,
        config={
            "bands": [[2, 50]],
            "keeps": [1],
            "ranking": "rank_then_yield_then_score",
            "charts": 2,
            "heights": [10000],
            "timeout": 1,
        },
        cache={},
    )

    assert [rec["candidate"]["parameter"] for rec in survivors] == ["p1"]
    row = get_pipeline_candidate(db, run["id"], "general", "p1")
    round_rec = runner._adaptive_round(runner._adaptive_result(row, 2), 1)
    assert captured["timeout"] == 1
    assert round_rec["search"]["status"] == "timeout"
    assert round_rec["search"]["search_outcome"] == "timeout"
    assert round_rec["search"]["search_budget"] == {
        "timeout_seconds": 1,
        "timeout_scope": "per_chart_height_ratpoints_call",
        "coverage": "bounded_screening",
        "exhaustive": False,
        "retry_policy": "manual",
        "retry_timeout_seconds": 1,
    }
    assert runner._adaptive_search_reusable(round_rec["search"]) is False


def test_adaptive_ladder_reranks_after_each_denominator_band(monkeypatch):
    db = _db()
    run = _run(db, "Adaptive")
    items = []
    curve_by_parameter = {}
    rank_by_curve = {}
    for curve_id, parameter, score in (
        (1, "p1", 30.0),
        (2, "p2", 20.0),
        (3, "p3", 10.0),
    ):
        db.execute("INSERT INTO curves(id) VALUES(?)", (curve_id,))
        upsert_pipeline_candidate(
            db,
            run_id=run["id"],
            provider_key="general",
            parameter=parameter,
            curve_id=curve_id,
            score=score,
            status="running",
            rigorous_lower=4,
        )
        curve_by_parameter[parameter] = curve_id
        rank_by_curve[curve_id] = 4
        items.append(
            {
                "provider_key": "general",
                "candidate": {"parameter": parameter, "score": score},
            }
        )

    def fake_materialize(db, *, run, item, cache):
        curve_id = curve_by_parameter[item["candidate"]["parameter"]]
        context = {
            "curve_id": curve_id,
            "E": None,
            "source_E": None,
            "family": None,
            "plugin": None,
            "variant": None,
            "parameter": item["candidate"]["parameter"],
            "rank_order": 0,
        }
        cache[runner._item_key(item)] = {"context": context, "created": False}
        return context

    def fake_snapshot(db, curve_id):
        return {
            "rigorous_lower": rank_by_curve[int(curve_id)],
            "rigorous_upper": None,
            "exact_rank": None,
        }

    calls = []

    def fake_point_tier(db, *, curve_id, denominator_low=None, denominator_high=None, **kwargs):
        calls.append((int(curve_id), int(denominator_low), int(denominator_high)))
        before = rank_by_curve[int(curve_id)]
        if int(denominator_low) == 2 and int(curve_id) == 3:
            rank_by_curve[int(curve_id)] = 5
        return {
            "rank_growth": rank_by_curve[int(curve_id)] - before,
            "rigorous_lower": rank_by_curve[int(curve_id)],
            "exact_points": 1 if rank_by_curve[int(curve_id)] > before else 0,
        }

    monkeypatch.setattr(runner, "_materialize_population_context", fake_materialize)
    monkeypatch.setattr(runner, "_snapshot", fake_snapshot)
    monkeypatch.setattr(runner, "run_auto_point_tier", fake_point_tier)

    survivors = runner._adaptive_ladder_population(
        db,
        run=run,
        run_config={
            "ratpoints": None,
            "certificate_timeout": 120,
            "exact_candidates": 64,
        },
        target={},
        active_items=items,
        stage_index=2,
        config={
            "bands": [[2, 50], [51, 500]],
            "keeps": [3, 1],
            "ranking": "rank_then_yield_then_score",
            "charts": 5,
            "heights": [10000],
            "timeout": 1,
            "certificate_timeout": 120,
            "exact_candidates": 64,
        },
        cache={},
    )

    assert [item["candidate"]["parameter"] for item in survivors] == ["p3"]
    assert calls == [
        (1, 2, 50),
        (2, 2, 50),
        (3, 2, 50),
        (3, 51, 500),
    ]
    assert get_pipeline_candidate(db, run["id"], "general", "p1")["status"] == "funnel_pruned"
    assert get_pipeline_candidate(db, run["id"], "general", "p2")["status"] == "funnel_pruned"
    assert get_pipeline_candidate(db, run["id"], "general", "p3")["rigorous_lower"] == 5


def test_denominator_band_stage_passes_exact_band_to_point_search(monkeypatch):
    captured = {}

    def fake_point_tier(db, **kwargs):
        captured.update(kwargs)
        return {
            "rank_growth": 0,
            "rigorous_lower": 4,
            "exact_points": 0,
        }

    monkeypatch.setattr(runner, "run_auto_point_tier", fake_point_tier)
    result, terminal, stop = runner._stage_result(
        "denominator_band",
        db=object(),
        run={"id": 12, "target_mode": "general"},
        run_config={
            "ratpoints": "/tmp/ratpoints",
            "certificate_timeout": 120,
            "exact_candidates": 64,
        },
        target={},
        context={"curve_id": 7, "E": None, "parameter": "p"},
        config={
            "denominator_low": 101,
            "denominator_high": 1000,
            "charts": 5,
            "heights": [10000, 100000],
            "timeout": 8,
        },
        stage_index=4,
    )

    assert captured["denominator_low"] == 101
    assert captured["denominator_high"] == 1000
    assert captured["chart_budget"] == 5
    assert result["status"] == "completed"
    assert terminal is None
    assert stop is False



@pytest.mark.parametrize(
    ("stage_id", "config"),
    [
        (
            "integral_seed",
            {
                "heights": [1000, 10000],
                "timeout": 4,
                "model_mode": "stored",
                "model_prep_timeout": 8,
                "certify_after_search": False,
            },
        ),
        (
            "denominator_band",
            {
                "denominator_low": 2,
                "denominator_high": 50,
                "charts": 2,
                "heights": [1000],
                "timeout": 4,
            },
        ),
        (
            "affine_search",
            {
                "charts": 2,
                "heights": [1000],
                "timeout": 4,
            },
        ),
    ],
)
def test_point_search_stages_do_not_report_all_timeout_as_completed(
    monkeypatch, stage_id, config
):
    monkeypatch.setattr(
        runner,
        "run_auto_point_tier",
        lambda *args, **kwargs: {
            "planned_searches": 2,
            "completed_searches": 0,
            "resume_skipped": 0,
            "timeouts": 2,
            "failures": 0,
            "exact_points": 0,
            "rank_growth": 0,
            "rigorous_lower": 4,
        },
    )
    result, terminal, stop = runner._stage_result(
        stage_id,
        db=object(),
        run={"id": 12, "target_mode": "general"},
        run_config={
            "ratpoints": None,
            "certificate_timeout": 120,
            "exact_candidates": 64,
        },
        target={},
        context={"curve_id": 7, "E": object(), "parameter": "p"},
        config=config,
        stage_index=4,
    )
    assert result["status"] == "timeout"
    assert result["search_outcome"] == "timeout"
    assert result["search_complete"] is False
    assert result["search_coverage"]["planned_searches"] == 2
    assert result["search_coverage"]["completed_regions"] == 0
    assert result["search_coverage"]["coverage_fraction"] == 0.0
    assert terminal is None
    assert stop is False


def test_survivor_score_uses_latest_prime_pipeline_score():
    db = _db()
    run = _run(db, "Prime score")
    items = []
    for curve_id, parameter, original_score, prime_score in (
        (1, "p1", 100.0, 1.0),
        (2, "p2", 10.0, 9.0),
    ):
        db.execute("INSERT INTO curves(id) VALUES(?)", (curve_id,))
        upsert_pipeline_candidate(
            db,
            run_id=run["id"],
            provider_key="general",
            parameter=parameter,
            curve_id=curve_id,
            score=original_score,
            status="running",
            rigorous_lower=4,
            stage_results={
                "2": {
                    "stage_id": "multi_scale_frobenius",
                    "result": {
                        "status": "completed",
                        "pipeline_score": prime_score,
                    },
                }
            },
        )
        items.append(
            {
                "provider_key": "general",
                "candidate": {"parameter": parameter, "score": original_score},
            }
        )

    survivors = runner._select_survivors_population(
        db,
        run=run,
        active_items=items,
        stage_index=3,
        config={"keep": 1, "ranking": "score"},
    )
    assert [item["candidate"]["parameter"] for item in survivors] == ["p2"]



def test_small_point_density_scores_fresh_yield(monkeypatch):
    calls = []
    prepare_calls = []
    yields = {100: 3, 1000: 1, 10000: 0}
    prepared = {
        "search_model": "global_minimal",
        "model_prep_error": None,
    }

    def fake_prepare(db, **kwargs):
        prepare_calls.append(dict(kwargs))
        return prepared

    def fake_point_tier(db, **kwargs):
        height = int(kwargs["heights"][0])
        assert kwargs["prepared_model"] is prepared
        calls.append(height)
        return {
            "exact_points": yields[height],
            "rank_growth": 0,
            "completed_searches": 1,
            "timeouts": 0,
            "failures": 0,
            "rigorous_lower": 4,
            "search_model": "global_minimal",
        }

    monkeypatch.setattr(runner, "prepare_point_search_model", fake_prepare)
    monkeypatch.setattr(runner, "run_auto_point_tier", fake_point_tier)
    result, terminal, stop = runner._stage_result(
        "small_point_density",
        db=object(),
        run={"id": 9, "target_mode": "general"},
        run_config={"ratpoints": None, "certificate_timeout": 120, "exact_candidates": 32},
        target={},
        context={"curve_id": 7, "E": None, "parameter": "p"},
        config={
            "heights": [100, 1000, 10000],
            "timeout": 1,
            "certificate_timeout": 120,
            "exact_candidates": 32,
        },
        stage_index=3,
    )
    assert calls == [100, 1000, 10000]
    assert len(prepare_calls) == 1
    assert prepare_calls[0]["curve_id"] == 7
    assert prepare_calls[0]["minimal_model"] is True
    assert result["search_model"] == "global_minimal"
    assert result["search_model_fixed_across_rounds"] is True
    assert result["exact_points"] == 4
    assert result["positive_round_fraction"] == 2 / 3
    assert result["pipeline_score"] > 0
    assert terminal is None
    assert stop is False


def test_small_point_density_scores_only_completed_rounds(monkeypatch):
    prepared = {
        "search_model": "stored_exact_fallback",
        "model_prep_error": "minimal prep timeout",
    }
    monkeypatch.setattr(
        runner,
        "prepare_point_search_model",
        lambda db, **kwargs: prepared,
    )

    def fake_point_tier(db, **kwargs):
        assert kwargs["prepared_model"] is prepared
        height = int(kwargs["heights"][0])
        if height == 100:
            return {
                "planned_searches": 1,
                "completed_searches": 1,
                "timeouts": 0,
                "failures": 0,
                "exact_points": 2,
                "rank_growth": 0,
                "rigorous_lower": 4,
                "search_model": prepared["search_model"],
            }
        return {
            "planned_searches": 1,
            "completed_searches": 0,
            "timeouts": 1,
            "failures": 0,
            "exact_points": 0,
            "rank_growth": 0,
            "rigorous_lower": 4,
            "search_model": prepared["search_model"],
        }

    monkeypatch.setattr(runner, "run_auto_point_tier", fake_point_tier)
    result, terminal, stop = runner._stage_result(
        "small_point_density",
        db=object(),
        run={"id": 9, "target_mode": "general"},
        run_config={
            "ratpoints": None,
            "certificate_timeout": 120,
            "exact_candidates": 32,
        },
        target={},
        context={"curve_id": 7, "E": None, "parameter": "p"},
        config={
            "heights": [100, 1000],
            "timeout": 1,
            "certificate_timeout": 120,
            "exact_candidates": 32,
        },
        stage_index=3,
    )
    assert result["status"] == "partial"
    assert result["search_outcome"] == "partial"
    assert result["scored_rounds"] == 1
    assert result["configured_rounds"] == 2
    assert result["score_coverage_fraction"] == 0.5
    assert result["partial_score"] is True
    assert result["search_model"] == "stored_exact_fallback"
    assert result["model_prep_error"] == "minimal prep timeout"
    assert result["search_model_fixed_across_rounds"] is True
    assert {rec["search_model"] for rec in result["rounds"]} == {"stored_exact_fallback"}
    assert result["positive_round_fraction"] == 1.0
    assert result["pipeline_score"] is not None
    assert result["rounds"][0]["comparable_for_score"] is True
    assert result["rounds"][1]["comparable_for_score"] is False
    assert terminal is None
    assert stop is False


def test_point_yield_persistence_excludes_incomplete_bands():
    db = _db()
    run = _run(db, "Yield persistence incomplete")
    db.execute("INSERT INTO curves(id) VALUES(1)")
    upsert_pipeline_candidate(
        db,
        run_id=run["id"],
        provider_key="general",
        parameter="p1",
        curve_id=1,
        score=1.0,
        status="running",
        rigorous_lower=4,
        stage_results={
            "2": {
                "stage_id": "denominator_band",
                "result": {
                    "status": "completed",
                    "search_outcome": "completed",
                    "denominator_low": 2,
                    "denominator_high": 50,
                    "exact_points": 3,
                    "rank_growth": 0,
                },
            },
            "3": {
                "stage_id": "denominator_band",
                "result": {
                    "status": "timeout",
                    "search_outcome": "timeout",
                    "denominator_low": 51,
                    "denominator_high": 500,
                    "exact_points": 0,
                    "rank_growth": 0,
                    "planned_searches": 2,
                    "completed_searches": 0,
                    "timeouts": 2,
                    "failures": 0,
                },
            },
        },
    )
    result, terminal, stop = runner._stage_result(
        "point_yield_persistence",
        db=db,
        run={"id": run["id"], "target_mode": "general"},
        run_config={},
        target={},
        context={"curve_id": 1, "E": None, "parameter": "p1"},
        config={"minimum_bands": 2},
        stage_index=4,
    )
    assert result["status"] == "inconclusive"
    assert result["reason"] == "insufficient_completed_denominator_bands"
    assert result["bands_seen"] == 2
    assert result["completed_bands"] == 1
    assert result["incomplete_bands"] == 1
    assert len(result["records"]) == 1
    assert len(result["incomplete_records"]) == 1
    assert result["pipeline_score"] is None
    assert terminal is None
    assert stop is False


def test_point_yield_persistence_reads_prior_denominator_bands():
    db = _db()
    run = _run(db, "Yield persistence")
    db.execute("INSERT INTO curves(id) VALUES(1)")
    upsert_pipeline_candidate(
        db, run_id=run["id"], provider_key="general", parameter="p1",
        curve_id=1, score=1.0, status="running", rigorous_lower=4,
        stage_results={
            "2": {
                "stage_id": "denominator_band",
                "result": {
                    "status": "completed", "denominator_low": 2,
                    "denominator_high": 50, "exact_points": 3, "rank_growth": 0,
                },
            },
            "3": {
                "stage_id": "denominator_band",
                "result": {
                    "status": "completed", "denominator_low": 51,
                    "denominator_high": 500, "exact_points": 1, "rank_growth": 0,
                },
            },
        },
    )
    result, terminal, stop = runner._stage_result(
        "point_yield_persistence",
        db=db,
        run={"id": run["id"], "target_mode": "general"},
        run_config={}, target={},
        context={"curve_id": 1, "E": None, "parameter": "p1"},
        config={"minimum_bands": 2}, stage_index=4,
    )
    assert result["status"] == "completed"
    assert result["bands_seen"] == 2
    assert result["positive_bands"] == 2
    assert result["late_band_exact_points"] == 1
    assert result["pipeline_score"] > 0
    assert terminal is None
    assert stop is False


def test_pari_upper_helper_preserves_timeout_separately_from_prior_upper(monkeypatch):
    import rank42.search_science as search_science
    from rank42.descent import DescentTimeout

    monkeypatch.setattr(
        search_science,
        "get_curve_research_state",
        lambda db, curve_id: {
            "rigorous_lower": 4,
            "specialization_rigorous_lower": 4,
            "rigorous_upper": 7,
            "exact_rank": None,
            "rank_inconsistent": False,
        },
    )
    monkeypatch.setattr(
        search_science,
        "run_descent",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            DescentTimeout("injected timeout")
        ),
    )
    monkeypatch.setattr(search_science, "log_event", lambda *args, **kwargs: None)

    class E:
        def a_invariants(self):
            return [0, 0, 0, -1, 0]

    result = search_science.try_pari_upper(
        object(), 17, E(), 2, phase="test-screen"
    )
    assert result["attempt_status"] == "timeout"
    assert result["new_upper"] is None
    assert result["effective_rigorous_upper"] == 7
    assert result["rigorous_upper"] == 7
    assert result["upper_source"] == "prior_evidence"
    assert result["attempt_timeout"] == 2
    assert result["failure_class"] == "timeout"


def test_pari_upper_helper_marks_fresh_effective_upper_current_attempt(monkeypatch):
    import rank42.search_science as search_science

    monkeypatch.setattr(
        search_science,
        "get_curve_research_state",
        lambda db, curve_id: {
            "rigorous_lower": 4,
            "specialization_rigorous_lower": 4,
            "rigorous_upper": 9,
            "exact_rank": None,
            "rank_inconsistent": False,
        },
    )
    descent_result = {
        "upper": 7,
        "engine_version": "test",
        "sage_version": "test",
        "_runtime_seconds": 0.1,
    }
    monkeypatch.setattr(
        search_science,
        "run_descent",
        lambda *args, **kwargs: dict(descent_result),
    )
    captured = {}

    def fake_promote(db, **kwargs):
        captured.update(kwargs)
        return {
            "rigorous_lower": 4,
            "rigorous_upper": 7,
            "exact_rank": None,
            "rank_inconsistent": False,
            "evidence_id": 12,
        }

    monkeypatch.setattr(
        search_science,
        "promote_rigorous_rank_interval",
        fake_promote,
    )

    class E:
        def a_invariants(self):
            return [0, 0, 0, -1, 0]

    result = search_science.try_pari_upper(
        object(), 17, E(), 2, phase="test-screen"
    )
    assert result["attempt_status"] == "completed"
    assert result["new_upper"] == 7
    assert result["effective_rigorous_upper"] == 7
    assert result["upper_source"] == "current_attempt"
    assert result["failure_class"] is None
    assert captured["rigorous_lower"] is None
    assert captured["rigorous_upper"] == 7
    assert captured["certificate"] == descent_result
    assert captured["engine"] == "pari"
    assert captured["source"] == "auto_search"
    assert captured["options"]["phase"] == "test-screen"


def test_pari_upper_gate_timeout_is_not_reported_completed(monkeypatch):
    monkeypatch.setattr(
        runner,
        "_try_pari_upper",
        lambda *args, **kwargs: {
            "rigorous_lower": 4,
            "rigorous_upper": None,
            "effective_rigorous_upper": None,
            "attempt_status": "timeout",
            "new_upper": None,
            "upper_source": None,
        },
    )
    monkeypatch.setattr(
        runner,
        "get_curve_research_state",
        lambda db, curve_id: {
            "rigorous_lower": 4,
            "rank_inconsistent": False,
        },
    )
    monkeypatch.setattr(
        runner,
        "proven_lower",
        lambda row: (_ for _ in ()).throw(
            AssertionError("Arithmetic stage must not read compatibility lower")
        ),
    )

    result, terminal, stop = runner._stage_result(
        "pari_upper_gate",
        db=object(),
        run={"id": 12, "target_mode": "general"},
        run_config={"target_rank": 10},
        target={},
        context={"curve_id": 7, "E": object(), "parameter": "p"},
        config={"timeout": 2, "eliminate_below_goal": True},
        stage_index=4,
    )
    assert result["status"] == "timeout"
    assert result["reason"] == "pari_upper_timeout"
    assert result["attempt_status"] == "timeout"
    assert result["budget_class"] == "screening"
    assert terminal is None
    assert stop is False


def test_final_upper_timeout_keeps_prior_upper_provenance(monkeypatch):
    monkeypatch.setattr(
        runner,
        "_try_pari_upper",
        lambda *args, **kwargs: {
            "rigorous_lower": 4,
            "rigorous_upper": 7,
            "effective_rigorous_upper": 7,
            "attempt_status": "timeout",
            "new_upper": None,
            "upper_source": "prior_evidence",
            "failure_class": "timeout",
        },
    )

    result, terminal, stop = runner._stage_result(
        "final_upper",
        db=object(),
        run={"id": 91, "target_mode": "curve"},
        run_config={"target_rank": 10},
        target={},
        context={"curve_id": 7, "E": object(), "parameter": "p"},
        config={"timeout": 30},
        stage_index=8,
    )

    assert result["status"] == "timeout"
    assert result["reason"] == "final_upper_timeout"
    assert result["attempt_status"] == "timeout"
    assert result["attempt_outcome"] == "timeout"
    assert result["new_upper"] is None
    assert result["effective_upper"] == 7
    assert result["effective_upper_source"] == "prior_evidence"
    assert result["budget_class"] == "final_proof"
    assert terminal is None
    assert stop is False


def test_final_upper_completed_uses_current_attempt_provenance(monkeypatch):
    monkeypatch.setattr(
        runner,
        "_try_pari_upper",
        lambda *args, **kwargs: {
            "rigorous_lower": 5,
            "rigorous_upper": 5,
            "exact_rank": 5,
            "effective_rigorous_upper": 5,
            "attempt_status": "completed",
            "new_upper": 5,
            "upper_source": "current_attempt",
            "failure_class": None,
        },
    )

    result, terminal, stop = runner._stage_result(
        "final_upper",
        db=object(),
        run={"id": 92, "target_mode": "curve"},
        run_config={"target_rank": 10},
        target={},
        context={"curve_id": 8, "E": object(), "parameter": "q"},
        config={"timeout": 45},
        stage_index=9,
    )

    assert result["status"] == "completed"
    assert result["attempt_outcome"] == "completed"
    assert result["new_upper"] == 5
    assert result["effective_upper"] == 5
    assert result["effective_upper_source"] == "current_attempt"
    assert result["exact_rank"] == 5
    assert terminal is None
    assert stop is False


def test_pari_upper_completed_equal_lower_closes_exact_through_shared_promotion(
    tmp_path, monkeypatch
):
    import rank42.search_science as search_science

    db = connect_core(tmp_path / "final-upper.db")
    curve_id = upsert_curve(
        db,
        family="final-upper",
        parameter="1",
        a_invariants_json='["0","0","1","-1","0"]',
    )
    record_rank_evidence(
        db,
        curve_id=curve_id,
        model=["0", "0", "1", "-1", "0"],
        data={
            "engine": "fixture-lower",
            "evidence_type": "certified_subgroup",
            "status": "completed",
            "rigorous": True,
            "rigorous_lower": 1,
            "rigorous_upper": None,
            "exact_rank": None,
            "assumptions": [],
            "points_found": [["0", "0"]],
            "options": {},
        },
    )

    descent_result = {
        "upper": 1,
        "engine_version": "test",
        "sage_version": "test",
        "_runtime_seconds": 0.05,
    }
    monkeypatch.setattr(
        search_science,
        "run_descent",
        lambda *args, **kwargs: dict(descent_result),
    )

    class E:
        def a_invariants(self):
            return [0, 0, 1, -1, 0]

    result = search_science.try_pari_upper(
        db,
        curve_id,
        E(),
        30,
        phase="pipeline-test-final-upper",
    )

    assert result["attempt_status"] == "completed"
    assert result["new_upper"] == 1
    assert result["upper_source"] == "current_attempt"
    assert result["rigorous_lower"] == 1
    assert result["rigorous_upper"] == 1
    assert result["exact_rank"] == 1

    evidence = db.execute(
        """SELECT * FROM rank_evidence
           WHERE curve_id=? AND engine='pari'
           ORDER BY id DESC LIMIT 1""",
        (curve_id,),
    ).fetchone()
    assert evidence is not None
    assert evidence["status"] == "completed"
    assert evidence["rigorous_upper"] == 1
    options = json.loads(evidence["options_json"])
    assert options["certificate_fingerprint"]
    assert options["source"] == "auto_search"
    assert options["phase"] == "pipeline-test-final-upper"


def test_mwrank_selmer_stage_identifies_rank_bound_quantity(monkeypatch):
    monkeypatch.setattr(
        runner,
        "get_curve_research_state",
        lambda db, curve_id: {
            "rigorous_lower": 4,
            "rank_inconsistent": False,
        },
    )
    monkeypatch.setattr(
        runner,
        "proven_lower",
        lambda row: (_ for _ in ()).throw(
            AssertionError("Arithmetic stage must not read compatibility lower")
        ),
    )
    descent_result = {
        "rigorous_upper": 7,
        "raw_selmer_rank": None,
        "minimal_model_a_invariants": ["0", "0", "0", "-1", "0"],
        "runtime_seconds": 0.1,
    }
    monkeypatch.setattr(
        runner,
        "run_classical_descent",
        lambda *args, **kwargs: dict(descent_result),
    )
    captured = {}

    def fake_promote(db, **kwargs):
        captured.update(kwargs)
        return {
            "rigorous_lower": 4,
            "rigorous_upper": 7,
            "exact_rank": None,
            "rank_inconsistent": False,
            "evidence_id": 23,
        }

    monkeypatch.setattr(
        runner,
        "promote_rigorous_rank_interval",
        fake_promote,
    )

    class E:
        def a_invariants(self):
            return [0, 0, 0, -1, 0]

    result = runner._run_mwrank_selmer_stage(
        object(),
        curve_id=1,
        E=E(),
        config={"timeout": 60, "first_limit": 20, "second_limit": 10},
        source="test",
    )
    assert result["status"] == "completed"
    assert result["bound_kind"] == "mwrank_rank_bound"
    assert result["rank_upper"] == 7
    assert result["rigorous_upper"] == 7
    assert result["raw_selmer_rank"] is None
    assert result["interval_evidence_id"] == 23
    assert captured["rigorous_lower"] is None
    assert captured["rigorous_upper"] == 7
    assert captured["certificate"] == descent_result
    assert captured["engine"] == "eclib_mwrank_selmer_two_descent"
    assert captured["evidence_type"] == "descent"
    assert captured["source"] == "test"
    assert captured["options"]["bound_kind"] == "mwrank_rank_bound"


def test_mwrank_upper_shared_promotion_closes_exact_rank(tmp_path, monkeypatch):
    db = connect_core(tmp_path / "mwrank-upper.db")
    try:
        curve_id = upsert_curve(
            db,
            family="mwrank-upper",
            parameter="1",
            a_invariants_json=json.dumps(["0", "0", "0", "-1", "0"]),
        )
        record_rank_evidence(
            db,
            curve_id=curve_id,
            model=[0, 0, 0, -1, 0],
            data={
                "engine": "known-lower",
                "evidence_type": "certified_subgroup",
                "status": "completed",
                "rigorous": True,
                "rigorous_lower": 4,
                "rigorous_upper": None,
                "exact_rank": None,
                "conditional_analytic_upper": None,
                "numerical_rank_signal": None,
                "assumptions": [],
                "points_found": [],
                "options": {},
            },
        )
        monkeypatch.setattr(
            runner,
            "run_classical_descent",
            lambda *args, **kwargs: {
                "rigorous_upper": 4,
                "raw_selmer_rank": None,
                "minimal_model_a_invariants": ["0", "0", "0", "-1", "0"],
                "runtime_seconds": 0.1,
            },
        )

        class E:
            def a_invariants(self):
                return [0, 0, 0, -1, 0]

        result = runner._run_mwrank_selmer_stage(
            db,
            curve_id=curve_id,
            E=E(),
            config={"timeout": 60, "first_limit": 20, "second_limit": 10},
            source="test-exact-closure",
        )

        assert result["status"] == "completed"
        assert result["rank_upper"] == 4
        assert result["rigorous_lower"] == 4
        assert result["rigorous_upper"] == 4
        assert result["exact_rank"] == 4
        row = db.execute(
            "SELECT descent_lower,descent_upper,exact_rank,certain,status "
            "FROM curves WHERE id=?",
            (curve_id,),
        ).fetchone()
        assert int(row["descent_lower"]) == 4
        assert int(row["descent_upper"]) == 4
        assert int(row["exact_rank"]) == 4
        assert int(row["certain"]) == 1
        assert row["status"] == "exact"
    finally:
        db.close()


def test_mwrank_retry_policy_manual_escalated_and_automatic():
    prior = {
        "status": "timeout",
        "attempt_complete": True,
        "attempt_timeout": 60,
        "retry_policy": "manual",
    }
    skip, cfg, reason = runner._resume_stage_policy(
        "selmer_bound",
        prior,
        {"timeout": 60, "retry_policy": "manual", "retry_timeout": 300},
    )
    assert skip is True
    assert cfg["timeout"] == 60
    assert reason == "manual"

    prior["retry_policy"] = "escalated"
    skip, cfg, reason = runner._resume_stage_policy(
        "selmer_bound",
        prior,
        {"timeout": 60, "retry_policy": "escalated", "retry_timeout": 300},
    )
    assert skip is False
    assert cfg["timeout"] == 300
    assert reason == "escalated"

    prior["attempt_timeout"] = 300
    skip, cfg, reason = runner._resume_stage_policy(
        "selmer_bound",
        prior,
        {"timeout": 60, "retry_policy": "escalated", "retry_timeout": 300},
    )
    assert skip is True
    assert reason == "escalated_budget_already_attempted"

    prior["retry_policy"] = "automatic"
    prior["attempt_timeout"] = 60
    skip, cfg, reason = runner._resume_stage_policy(
        "selmer_bound",
        prior,
        {"timeout": 60, "retry_policy": "automatic", "retry_timeout": 300},
    )
    assert skip is False
    assert cfg["timeout"] == 60
    assert reason == "automatic"


def test_mwrank_timeout_records_operational_completion_and_retry_policy(monkeypatch):
    monkeypatch.setattr(
        runner,
        "get_curve_research_state",
        lambda db, curve_id: {
            "rigorous_lower": 4,
            "rank_inconsistent": False,
        },
    )
    monkeypatch.setattr(
        runner,
        "proven_lower",
        lambda row: (_ for _ in ()).throw(
            AssertionError("Arithmetic stage must not read compatibility lower")
        ),
    )
    monkeypatch.setattr(
        runner,
        "run_classical_descent",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            runner.ClassicalDescentTimeout("injected timeout")
        ),
    )

    class E:
        def a_invariants(self):
            return [0, 0, 0, -1, 0]

    result = runner._run_mwrank_selmer_stage(
        object(),
        curve_id=1,
        E=E(),
        config={
            "timeout": 60,
            "first_limit": 20,
            "second_limit": 10,
            "retry_policy": "escalated",
            "retry_timeout": 300,
        },
        source="test",
    )
    assert result["status"] == "timeout"
    assert result["attempt_complete"] is True
    assert result["mathematical_outcome"] == "timeout"
    assert result["attempt_timeout"] == 60
    assert result["retry_policy"] == "escalated"
    assert result["retry_timeout"] == 300


def test_pari_gate_uses_authoritative_lower_when_columns_would_filter(monkeypatch):
    monkeypatch.setattr(
        runner,
        "_try_pari_upper",
        lambda *args, **kwargs: {
            "rigorous_lower": 5,
            "rigorous_upper": 4,
            "effective_rigorous_upper": 4,
            "attempt_status": "completed",
            "new_upper": 4,
            "upper_source": "current_attempt",
        },
    )
    monkeypatch.setattr(
        runner,
        "get_curve_research_state",
        lambda db, curve_id: {
            "rigorous_lower": 5,
            "rank_inconsistent": True,
        },
    )
    monkeypatch.setattr(
        runner,
        "proven_lower",
        lambda row: (_ for _ in ()).throw(
            AssertionError("PARI gate must not consult compatibility lower")
        ),
    )

    result, terminal, stop = runner._stage_result(
        "pari_upper_gate",
        db=object(),
        run={"id": 12, "target_mode": "general"},
        run_config={"target_rank": 10},
        target={},
        context={"curve_id": 7, "E": object(), "parameter": "p"},
        config={"timeout": 2, "eliminate_below_goal": True},
        stage_index=4,
    )

    assert result["status"] == "completed"
    assert terminal is None
    assert stop is False


def test_mwrank_headroom_uses_prior_rank_bound(monkeypatch):
    db = _db()
    run = _run(db, "mwrank headroom")
    db.execute("INSERT INTO curves(id) VALUES(1)")
    upsert_pipeline_candidate(
        db, run_id=run["id"], provider_key="general", parameter="p1",
        curve_id=1, score=1.0, status="running", rigorous_lower=4,
        stage_results={
            "2": {
                "stage_id": "selmer_bound",
                "result": {"status": "completed", "rigorous_upper": 7},
            },
        },
    )
    monkeypatch.setattr(
        runner,
        "get_curve_research_state",
        lambda db, curve_id: {
            "rigorous_lower": 6,
            "rank_inconsistent": False,
        },
    )
    monkeypatch.setattr(
        runner,
        "proven_lower",
        lambda row: (_ for _ in ()).throw(
            AssertionError("Arithmetic stage must not read compatibility lower")
        ),
    )
    result, terminal, stop = runner._stage_result(
        "selmer_headroom",
        db=db,
        run={"id": run["id"], "target_mode": "general"},
        run_config={}, target={},
        context={"curve_id": 1, "E": None, "parameter": "p1"},
        config={}, stage_index=3,
    )
    assert result["status"] == "completed"
    assert result["bound_kind"] == "mwrank_rank_bound"
    assert result["headroom_kind"] == "mwrank_rank_bound_minus_rigorous_lower"
    assert result["mwrank_rank_headroom"] == 1
    assert "selmer_headroom" not in result
    assert result["pipeline_score"] == 1.0
    assert result["sha_may_contribute"] is True
    assert terminal is None
    assert stop is False



def test_family_pipeline_can_use_existing_candidate_pool_slice(monkeypatch):
    rows = [
        {
            "id": 11, "parameter": "1/2", "score": 9.0, "rank_order": 1,
            "metadata_json": json.dumps({"score_provenance": {"algorithm": "family-v1", "terms_used": 4}}),
        },
        {
            "id": 12, "parameter": "2/3", "score": 8.0, "rank_order": 2,
            "metadata_json": json.dumps({"score_provenance": {"algorithm": "family-v1", "terms_used": 5}}),
        },
        {
            "id": 13, "parameter": "3/4", "score": 7.0, "rank_order": 3,
            "metadata_json": json.dumps({"score_provenance": {"algorithm": "family-v1", "terms_used": 6}}),
        },
    ]
    captured = {}

    def fake_candidate_rows(db, pool_id, **kwargs):
        captured["pool_id"] = pool_id
        captured.update(kwargs)
        return rows

    monkeypatch.setattr(runner, "candidate_rows", fake_candidate_rows)
    result = runner._family_candidates(
        object(),
        ".",
        99,
        None,
        None,
        [],
        {},
        target={
            "candidate_pool_id": 7,
            "candidate_ids": [12, 13],
            "only_unsearched": True,
        },
    )
    assert captured["pool_id"] == 7
    assert captured["only_unsearched"] is True
    assert [rec["candidate_id"] for rec in result] == [12, 13]
    assert [rec["parameter"] for rec in result] == ["2/3", "3/4"]
    assert [rec["score_provenance"]["terms_used"] for rec in result] == [5, 6]



def test_family_existing_pool_applies_declared_corpus_filter(monkeypatch):
    rows = [
        {"id": 21, "parameter": "known", "score": 9.0, "rank_order": 1},
        {"id": 22, "parameter": "new", "score": 8.0, "rank_order": 2},
    ]
    captured = {}

    monkeypatch.setattr(runner, "candidate_rows", lambda *args, **kwargs: rows)

    def fake_policy(project_root, plugin, variant, records, policy):
        captured["policy"] = policy
        captured["records"] = [rec["parameter"] for rec in records]
        kept = [dict(records[1])]
        kept[0]["corpus_summary"] = {"known": False}
        kept[0]["corpus_matches"] = []
        return kept, {
            "policy": policy,
            "declared": 1,
            "ready": 1,
            "known": 1,
            "retained": 1,
            "classified": True,
        }

    monkeypatch.setattr(runner, "apply_candidate_corpus_policy", fake_policy)
    result = runner._family_candidates(
        object(),
        ".",
        101,
        object(),
        object(),
        [{"id": "corpus_filter", "config": {"policy": "exclude-known"}}],
        {},
        target={"candidate_pool_id": 7},
    )

    assert captured["policy"] == "exclude-known"
    assert captured["records"] == ["known", "new"]
    assert [rec["candidate_id"] for rec in result] == [22]
    assert result[0]["corpus_summary"] == {"known": False}


def test_covering_selmer_branches_all_success_reports_completed(monkeypatch):
    lowers = iter([4, 4])
    monkeypatch.setattr(
        runner, "_research_lower", lambda db, curve_id: next(lowers)
    )
    monkeypatch.setattr(
        runner,
        "rigorous_witness_basis",
        lambda db, curve_id, E: ([], 0, True),
    )
    monkeypatch.setattr(
        runner,
        "run_classical_descent",
        lambda *args, mode, **kwargs: {
            "rigorous_upper": None,
            "reported_upper": None,
            "points": [],
            "runtime_seconds": 0.01,
        },
    )
    monkeypatch.setattr(
        runner,
        "certify_stored_independence",
        lambda *args, **kwargs: {
            "status": "completed",
            "exact_attempts": 0,
            "attempt_outcomes": [],
            "basis_complete": True,
            "reason": None,
        },
    )

    result = runner._run_covering_selmer_branches(
        object(),
        curve_id=7,
        E=SimpleNamespace(a_invariants=lambda: [0, 0, 1, -1, 0]),
        run_id=41,
        stage_index=6,
        config={
            "engines": ["mwrank_selmer", "mwrank_coverings"],
            "timeout": 5,
        },
        certificate_timeout=10,
        exact_candidates=8,
    )

    assert result["status"] == "completed"
    assert result["branch_status"] == "completed"
    assert result["branch_coverage"] == {
        "planned": 2,
        "attempted": 2,
        "completed": 2,
        "timeouts": 0,
        "errors": 0,
        "skipped": 0,
    }
    assert result["certification_status"] == "completed"
    assert result["rigorous_lower"] == 4
    assert "runtime_capabilities" not in result


def test_covering_selmer_branches_all_timeout_reports_timeout(monkeypatch):
    lowers = iter([3, 3])
    monkeypatch.setattr(
        runner, "_research_lower", lambda db, curve_id: next(lowers)
    )
    monkeypatch.setattr(
        runner,
        "rigorous_witness_basis",
        lambda db, curve_id, E: ([], 0, True),
    )

    def timeout_descent(*args, **kwargs):
        raise runner.ClassicalDescentTimeout("fixture timeout")

    monkeypatch.setattr(runner, "run_classical_descent", timeout_descent)
    monkeypatch.setattr(
        runner,
        "certify_stored_independence",
        lambda *args, **kwargs: {
            "status": "completed",
            "exact_attempts": 0,
            "attempt_outcomes": [],
            "basis_complete": True,
            "reason": "no_exact_candidates",
        },
    )

    result = runner._run_covering_selmer_branches(
        object(),
        curve_id=8,
        E=SimpleNamespace(a_invariants=lambda: [0, 0, 1, -1, 0]),
        run_id=42,
        stage_index=7,
        config={
            "engines": ["mwrank_selmer", "mwrank_coverings"],
            "timeout": 2,
        },
        certificate_timeout=10,
        exact_candidates=8,
    )

    assert result["status"] == "timeout"
    assert result["branch_status"] == "timeout"
    assert result["branch_coverage"]["timeouts"] == 2
    assert all(branch["status"] == "timeout" for branch in result["branches"])


def test_covering_selmer_branches_mixed_outcomes_report_partial(monkeypatch):
    lowers = iter([2, 2])
    monkeypatch.setattr(
        runner, "_research_lower", lambda db, curve_id: next(lowers)
    )
    monkeypatch.setattr(
        runner,
        "rigorous_witness_basis",
        lambda db, curve_id, E: ([], 0, True),
    )

    def mixed_descent(*args, mode, **kwargs):
        if mode == "mwrank_coverings":
            raise runner.ClassicalDescentTimeout("fixture timeout")
        return {
            "rigorous_upper": None,
            "reported_upper": None,
            "points": [],
            "runtime_seconds": 0.01,
        }

    monkeypatch.setattr(runner, "run_classical_descent", mixed_descent)
    monkeypatch.setattr(
        runner,
        "certify_stored_independence",
        lambda *args, **kwargs: {
            "status": "completed",
            "exact_attempts": 0,
            "attempt_outcomes": [],
            "basis_complete": True,
            "reason": None,
        },
    )

    result = runner._run_covering_selmer_branches(
        object(),
        curve_id=9,
        E=SimpleNamespace(a_invariants=lambda: [0, 0, 1, -1, 0]),
        run_id=43,
        stage_index=8,
        config={
            "engines": ["mwrank_selmer", "mwrank_coverings"],
            "timeout": 2,
        },
        certificate_timeout=10,
        exact_candidates=8,
    )

    assert result["status"] == "partial"
    assert result["branch_status"] == "partial"
    assert result["branch_coverage"]["completed"] == 1
    assert result["branch_coverage"]["timeouts"] == 1


def test_covering_selmer_completed_branches_with_cert_timeout_is_partial(
    monkeypatch,
):
    lowers = iter([5, 5])
    monkeypatch.setattr(
        runner, "_research_lower", lambda db, curve_id: next(lowers)
    )
    monkeypatch.setattr(
        runner,
        "rigorous_witness_basis",
        lambda db, curve_id, E: ([], 0, True),
    )
    monkeypatch.setattr(
        runner,
        "run_classical_descent",
        lambda *args, **kwargs: {
            "rigorous_upper": None,
            "reported_upper": None,
            "points": [],
            "runtime_seconds": 0.01,
        },
    )
    monkeypatch.setattr(
        runner,
        "certify_stored_independence",
        lambda *args, **kwargs: {
            "status": "timeout",
            "exact_attempts": 2,
            "attempt_outcomes": [
                {"point_id": 1, "status": "timeout", "evidence_id": 17}
            ],
            "basis_complete": True,
            "reason": "all_certificate_attempts_timed_out",
        },
    )

    result = runner._run_covering_selmer_branches(
        object(),
        curve_id=10,
        E=SimpleNamespace(a_invariants=lambda: [0, 0, 1, -1, 0]),
        run_id=44,
        stage_index=9,
        config={"engines": ["mwrank_selmer"], "timeout": 2},
        certificate_timeout=3,
        exact_candidates=8,
    )

    assert result["status"] == "partial"
    assert result["branch_status"] == "completed"
    assert result["certification_status"] == "timeout"
    assert result["certificate_attempts"] == 2
    assert result["certificate_reason"] == "all_certificate_attempts_timed_out"
    assert result["certificate_outcomes"][0]["status"] == "timeout"


def test_transform_population_fans_out_and_records_lineage(monkeypatch):
    db = _db()
    run = _run(db, "Transform fanout")
    db.execute("INSERT INTO curves(id) VALUES(1)")
    db.execute("INSERT INTO curves(id) VALUES(2)")
    upsert_pipeline_candidate(
        db,
        run_id=run["id"],
        provider_key="general",
        parameter="parent",
        curve_id=1,
        score=10.0,
        status="running",
        rigorous_lower=4,
    )
    item = {
        "provider_key": "general",
        "candidate": {"parameter": "parent", "score": 10.0, "curve_id": 1},
        "plugin": None,
        "variant": None,
        "family": None,
        "fingerprints": {},
    }

    monkeypatch.setattr(
        runner,
        "_materialize_population_context",
        lambda *args, **kwargs: {
            "curve_id": 1,
            "E": object(),
            "source_E": object(),
            "family": None,
            "plugin": None,
            "variant": None,
            "parameter": "parent",
            "score": 10.0,
            "rank_order": 1,
        },
    )
    monkeypatch.setattr(
        runner,
        "_snapshot",
        lambda db, curve_id: {
            "rigorous_lower": 4 if int(curve_id) == 1 else 0,
            "rigorous_upper": None,
            "exact_rank": None,
        },
    )
    monkeypatch.setattr(
        runner,
        "derive_transform_children",
        lambda stage_id, db, context, config: {
            "status": "completed",
            "transform_kind": "quadratic_twist",
            "children": [{
                "kind": "direct_curve",
                "curve_id": 2,
                "parameter": "twist-child",
                "score": 12.0,
                "metadata": {"twist_d": -1},
            }],
        },
    )

    out = runner._transform_population(
        db,
        run=run,
        run_config={"project_root": "."},
        active_items=[item],
        stage_index=2,
        stage_id="quadratic_twist_sweep",
        config={"max_children": 10, "include_parent": False},
        cache={},
    )
    assert [rec["candidate"]["parameter"] for rec in out] == ["twist-child"]
    parent = get_pipeline_candidate(db, run["id"], "general", "parent")
    assert parent["status"] == "transformed"
    child = get_pipeline_candidate(
        db,
        run["id"],
        "general|quadratic_twist_sweep",
        "twist-child",
    )
    assert child is not None
    edges = pipeline_derivations(db, run["id"])
    assert len(edges) == 1
    assert edges[0]["parent_candidate_id"] == parent["id"]
    assert edges[0]["child_candidate_id"] == child["id"]



def test_transform_partial_keeps_parent_and_unscored_child(monkeypatch):
    db = _db()
    run = _run(db, "Transform partial")
    db.execute("INSERT INTO curves(id) VALUES(1)")
    db.execute("INSERT INTO curves(id) VALUES(2)")
    upsert_pipeline_candidate(
        db,
        run_id=run["id"],
        provider_key="general",
        parameter="parent",
        curve_id=1,
        score=25.0,
        status="running",
    )
    item = {
        "provider_key": "general",
        "candidate": {"parameter": "parent", "score": 25.0, "curve_id": 1},
        "plugin": None,
        "variant": None,
        "family": None,
        "fingerprints": {},
    }
    monkeypatch.setattr(
        runner,
        "_materialize_population_context",
        lambda *args, **kwargs: {
            "curve_id": 1,
            "E": object(),
            "source_E": object(),
            "family": None,
            "plugin": None,
            "variant": None,
            "parameter": "parent",
            "score": 25.0,
            "rank_order": 1,
        },
    )
    monkeypatch.setattr(
        runner,
        "_snapshot",
        lambda db, curve_id: {
            "rigorous_lower": 0,
            "rigorous_upper": None,
            "exact_rank": None,
        },
    )
    monkeypatch.setattr(
        runner,
        "derive_transform_children",
        lambda stage_id, db, context, config: {
            "status": "partial",
            "transform_kind": "quadratic_twist",
            "attempted": 2,
            "succeeded": 1,
            "failed": 1,
            "children": [{
                "kind": "direct_curve",
                "curve_id": 2,
                "parameter": "twist-child",
                "score": None,
                "metadata": {"twist_d": -1, "score_inherited": False},
            }],
        },
    )

    out = runner._transform_population(
        db,
        run=run,
        run_config={"project_root": "."},
        active_items=[item],
        stage_index=2,
        stage_id="quadratic_twist_sweep",
        config={"max_children": 10, "include_parent": False},
        cache={},
    )

    assert {rec["candidate"]["parameter"] for rec in out} == {
        "parent", "twist-child"
    }
    parent = get_pipeline_candidate(db, run["id"], "general", "parent")
    assert parent["status"] == "running"
    child = get_pipeline_candidate(
        db,
        run["id"],
        "general|quadratic_twist_sweep",
        "twist-child",
    )
    assert child is not None
    assert child["score"] is None
    stage = json.loads(parent["stage_results_json"])["2"]
    assert stage["stage_id"] == "quadratic_twist_sweep"
    assert stage["result"]["status"] == "partial"
    assert stage["result"]["include_parent"] is True
    assert stage["result"]["failed"] == 1




def test_incomplete_constructive_loop_stage_reenters_transform_on_resume(
    monkeypatch,
):
    db = _db()
    run = _run(db, "Constructive loop stage resume")
    db.execute("INSERT INTO curves(id) VALUES(1)")
    upsert_pipeline_candidate(
        db,
        run_id=run["id"],
        provider_key="general",
        parameter="constructive-parent",
        curve_id=1,
        status="running",
    )
    item = {
        "provider_key": "general",
        "candidate": {
            "parameter": "constructive-parent",
            "curve_id": 1,
        },
        "plugin": None,
        "variant": None,
        "family": None,
        "fingerprints": {},
    }
    monkeypatch.setattr(
        runner,
        "_materialize_population_context",
        lambda *args, **kwargs: {
            "pipeline_run_id": run["id"],
            "pipeline_candidate_id": get_pipeline_candidate(
                db, run["id"], "general", "constructive-parent"
            )["id"],
            "curve_id": 1,
            "E": object(),
            "source_E": object(),
            "family": None,
            "plugin": None,
            "variant": None,
            "parameter": "constructive-parent",
            "score": None,
            "rank_order": 1,
        },
    )
    monkeypatch.setattr(
        runner,
        "_snapshot",
        lambda db, curve_id: {
            "rigorous_lower": 0,
            "rigorous_upper": None,
            "exact_rank": None,
        },
    )
    calls = []

    def derive(stage_id, db, context, config):
        calls.append({
            "stage_id": stage_id,
            "stage_index": context.get("pipeline_stage_index"),
            "pipeline_stage_id": context.get("pipeline_stage_id"),
        })
        if len(calls) == 1:
            return {
                "status": "partial",
                "transform_kind": "constructive_rank_jump_loop",
                "children": [],
                "strategy_resume_token": "height:2:8",
            }
        return {
            "status": "completed",
            "transform_kind": "constructive_rank_jump_loop",
            "children": [],
            "strategy_resume_token": None,
        }

    monkeypatch.setattr(runner, "derive_transform_children", derive)
    config = {"max_children": 8, "include_parent": True}

    first = runner._transform_population(
        db,
        run=run,
        run_config={"project_root": "."},
        active_items=[item],
        stage_index=5,
        stage_id="constructive_rank_jump_loop",
        config=config,
        cache={},
    )
    assert [rec["candidate"]["parameter"] for rec in first] == [
        "constructive-parent"
    ]
    first_stage = json.loads(
        get_pipeline_candidate(
            db, run["id"], "general", "constructive-parent"
        )["stage_results_json"]
    )["5"]["result"]
    assert first_stage["status"] == "partial"
    assert first_stage["strategy_resume_token"] == "height:2:8"

    second = runner._transform_population(
        db,
        run=run,
        run_config={"project_root": "."},
        active_items=[item],
        stage_index=5,
        stage_id="constructive_rank_jump_loop",
        config=config,
        cache={},
    )
    assert [rec["candidate"]["parameter"] for rec in second] == [
        "constructive-parent"
    ]
    assert len(calls) == 2
    assert calls[0] == {
        "stage_id": "constructive_rank_jump_loop",
        "stage_index": 5,
        "pipeline_stage_id": "constructive_rank_jump_loop",
    }
    assert calls[1] == calls[0]
    final_stage = json.loads(
        get_pipeline_candidate(
            db, run["id"], "general", "constructive-parent"
        )["stage_results_json"]
    )["5"]["result"]
    assert final_stage["status"] == "completed"
    assert final_stage["strategy_resume_token"] is None


def test_invalid_family_reference_child_does_not_abort_transform_scheduler(
    monkeypatch,
):
    db = _db()
    run = _run(db, "Bad transform child")
    db.execute("INSERT INTO curves(id) VALUES(1)")
    upsert_pipeline_candidate(
        db,
        run_id=run["id"],
        provider_key="general",
        parameter="parent",
        curve_id=1,
        status="running",
    )
    item = {
        "provider_key": "general",
        "candidate": {"parameter": "parent", "curve_id": 1},
        "plugin": None,
        "variant": None,
        "family": None,
        "fingerprints": {},
    }
    monkeypatch.setattr(
        runner,
        "_materialize_population_context",
        lambda *args, **kwargs: {
            "curve_id": 1,
            "E": object(),
            "source_E": object(),
            "family": None,
            "plugin": None,
            "variant": None,
            "parameter": "parent",
            "score": None,
            "rank_order": 1,
        },
    )
    monkeypatch.setattr(
        runner,
        "_snapshot",
        lambda db, curve_id: {
            "rigorous_lower": 0,
            "rigorous_upper": None,
            "exact_rank": None,
        },
    )
    monkeypatch.setattr(
        runner,
        "derive_transform_children",
        lambda stage_id, db, context, config: {
            "status": "completed",
            "transform_kind": "rank_jump_base_change",
            "children": [{
                "kind": "family_reference",
                "plugin_id": "missing-plugin",
                "variant_id": "v1",
                "parameter": "3/5",
                "score": None,
                "metadata": {},
            }],
        },
    )
    monkeypatch.setattr(
        runner,
        "get_plugin",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ValueError("missing plugin fixture")
        ),
    )

    out = runner._transform_population(
        db,
        run=run,
        run_config={"project_root": "."},
        active_items=[item],
        stage_index=3,
        stage_id="rank_jump_base_change",
        config={"include_parent": False, "max_children": 8},
        cache={},
    )

    assert [rec["candidate"]["parameter"] for rec in out] == ["parent"]
    parent = get_pipeline_candidate(db, run["id"], "general", "parent")
    assert parent["status"] == "running"
    stage = json.loads(parent["stage_results_json"])["3"]["result"]
    assert stage["status"] == "error"
    assert stage["include_parent"] is True
    assert stage["children_created"] == 0
    assert stage["child_materialization_failures"] == 1
    assert (
        stage["child_materialization_failure_samples"][0]["error_class"]
        == "ValueError"
    )


def test_pipeline_independence_preserves_certificate_attempt_status(monkeypatch):
    captured = {}

    def fake_independence(db, **kwargs):
        captured.update(kwargs)
        return {
            "status": "timeout",
            "reason": "all_certificate_attempts_timed_out",
            "exact_attempts": 2,
            "timeouts": 2,
            "errors": 0,
            "independent": 0,
            "dependent": 0,
            "inconclusive": 0,
            "not_attempted": 0,
            "rigorous_lower": 4,
        }

    monkeypatch.setattr(runner, "certify_stored_independence", fake_independence)
    result, terminal, stop = runner._stage_result(
        "independence",
        db=object(),
        run={"id": 55, "target_mode": "curve"},
        run_config={
            "certificate_timeout": 120,
            "exact_candidates": 64,
        },
        target={},
        context={
            "curve_id": 9,
            "E": object(),
            "parameter": "curve:9",
        },
        config={
            "certificate_timeout": 17,
            "max_candidates": 11,
            "max_halvings": 80,
            "max_prime": 2000000,
            "max_columns": 900,
        },
        stage_index=5,
    )

    assert result["status"] == "timeout"
    assert result["exact_attempts"] == 2
    assert captured["certificate_timeout"] == 17
    assert captured["max_candidates"] == 11
    assert captured["max_halvings"] == 80
    assert captured["max_prime"] == 2000000
    assert captured["max_columns"] == 900
    assert captured["search_ref"] == "pipeline:55:independence:5"
    assert terminal is None
    assert stop is False


def test_mw_growth_loop_runs_feedback_rounds_until_engine_stops(monkeypatch):
    captured = {}

    def fake_loop(db, **kwargs):
        captured.update(kwargs)
        return {
            "status": "completed",
            "stop_reason": "dry_completed_round",
            "exact_points": 3,
            "rank_growth": 2,
            "rigorous_lower": 9,
            "rounds": [
                {"round": 1, "new_exact_points": 2},
                {"round": 2, "new_exact_points": 1},
                {"round": 3, "new_exact_points": 0},
            ],
        }

    monkeypatch.setattr(runner, "run_pointed_quartic_escalation", fake_loop)
    result, terminal, stop = runner._stage_result(
        "mw_growth_loop",
        db=object(),
        run={"id": 77, "target_mode": "general", "stages": []},
        run_config={
            "target_rank": 12,
            "ratpoints": "/usr/bin/ratpoints",
            "certificate_timeout": 120,
            "exact_candidates": 64,
        },
        target={},
        context={
            "curve_id": 5,
            "E": object(),
            "parameter": "p",
        },
        config={
            "heights": [1000, 10000],
            "anchors": 24,
            "pool_size": 240,
            "max_rounds": 7,
            "deep_keep": 9,
            "timeout": 4,
            "reduce_timeout": 11,
            "certificate_timeout": 90,
            "exact_candidates": 80,
        },
        stage_index=4,
    )
    assert captured["rounds"] == 7
    assert captured["anchors"] == 24
    assert captured["pool_size"] == 240
    assert captured["deep_keep"] == 9
    assert captured["certificate_timeout"] == 90
    assert captured["exact_candidates"] == 80
    assert captured["target_rank"] == 12
    assert result["feedback_loop"] is True
    assert result["rounds_completed"] == 3
    assert result["stopped_on_dry_round"] is True
    assert result["stopped_on_rank_goal"] is False
    assert terminal is None
    assert stop is False



def test_builder_retention_floor_only_discards_fresh_below_floor(monkeypatch):
    discarded = []
    monkeypatch.setattr(
        runner,
        "_snapshot",
        lambda db, curve_id: {
            "rigorous_lower": 4,
            "rigorous_upper": None,
            "exact_rank": None,
        },
    )
    monkeypatch.setattr(
        runner,
        "_discard_unpromoted_auto_curve",
        lambda db, curve_id: discarded.append(int(curve_id)),
    )

    assert runner._apply_builder_retention(
        object(),
        curve_id=17,
        created=True,
        run_config={"retention_floor": 5},
    ) is True
    assert discarded == [17]

    discarded.clear()
    assert runner._apply_builder_retention(
        object(),
        curve_id=18,
        created=False,
        run_config={"retention_floor": 5},
    ) is False
    assert discarded == []


def test_builder_retention_floor_keeps_fresh_curve_at_threshold(monkeypatch):
    discarded = []
    monkeypatch.setattr(
        runner,
        "_snapshot",
        lambda db, curve_id: {
            "rigorous_lower": 8,
            "rigorous_upper": None,
            "exact_rank": None,
        },
    )
    monkeypatch.setattr(
        runner,
        "_discard_unpromoted_auto_curve",
        lambda db, curve_id: discarded.append(int(curve_id)),
    )
    assert runner._apply_builder_retention(
        object(),
        curve_id=19,
        created=True,
        run_config={"retention_floor": 8},
    ) is False
    assert discarded == []


def test_builder_zero_retention_preserves_existing_transient_policy(monkeypatch):
    monkeypatch.setattr(
        runner,
        "_snapshot",
        lambda db, curve_id: {
            "rigorous_lower": 0,
            "rigorous_upper": None,
            "exact_rank": None,
        },
    )
    seen = []
    monkeypatch.setattr(
        runner,
        "discard_transient_zero_evidence_curve",
        lambda db, curve_id: seen.append(int(curve_id)) or True,
    )
    assert runner._apply_builder_retention(
        object(),
        curve_id=20,
        created=True,
        run_config={"retention_floor": 0},
    ) is True
    assert seen == [20]



def test_large_height_generator_hunt_stops_after_certified_growth(monkeypatch):
    state = {"lower": 5}
    seen = []

    monkeypatch.setattr(
        runner,
        "_strategy_research_state",
        lambda db, curve_id, E, witness_basis=False: {
            "rigorous_lower": state["lower"],
            "rigorous_upper": None,
            "exact_rank": None,
            "rank_inconsistent": False,
            "witness_basis_complete": None,
            "witness_basis_required": None,
            "rigorous_witnesses": None,
            "research_state_source": "curve_research_state",
        },
    )
    monkeypatch.setattr(
        runner,
        "_runtime_hunt_capabilities",
        lambda db, context: frozenset({
            "exact_curve",
            "core_point_search",
            "core_prime_sieve",
            "covering_hook",
        }),
    )

    def fake_stage_result(stage_id, **kwargs):
        seen.append(stage_id)
        if stage_id == "selmer_element_fanout":
            state["lower"] = 6
        return {"status": "completed"}, None, False

    monkeypatch.setattr(runner, "_stage_result", fake_stage_result)
    result = runner._run_large_height_generator_hunt(
        object(),
        run={"id": 77},
        run_config={"target_rank": 20},
        target={},
        context={"curve_id": 9, "E": object()},
        config={
            "descent_levels": [2, 4],
            "saturation_bounds": [7, 31],
            "denominator_bands": [[2, 100]],
            "certificate_timeout": 30,
            "exact_candidates": 16,
            "max_coverings": 4,
            "mw_growth_rounds": 2,
            "stop_on_growth": True,
        },
        stage_index=5,
    )
    assert seen == ["higher_descent_ladder", "selmer_element_fanout"]
    assert result["rank_growth"] == 1
    assert result["rigorous_lower"] == 6
    assert result["stopped_after_growth"] is True
    assert result["stopped_at_rank_goal"] is False



def test_population_resume_reuses_stored_curve_without_respecializing(tmp_path, monkeypatch):
    db = runner.connect(tmp_path / "rank42.db")
    ensure_pipeline_schema(db)

    E = runner.EllipticCurve(runner.QQ, [0, 0, 0, -1, 1])
    curve_id = runner.upsert_curve(
        db,
        family="Resume Family",
        parameter="5/7",
        a_invariants_json='["0","0","0","-1","1"]',
        status="pipeline_searching",
    )
    run_id = create_pipeline_run(
        db,
        pipeline_name="resume-test",
        target_mode="family",
        target={"plugin_id": "demo", "variant_id": "v1"},
        stages=[{"id": "nagao_screen"}, {"id": "prime_table_cache"}],
        run_config={"project_root": str(tmp_path)},
    )
    upsert_pipeline_candidate(
        db,
        run_id=run_id,
        provider_key="",
        parameter="5/7",
        score=9.0,
        curve_id=curve_id,
        status="running",
    )

    def fail_respecialize(*args, **kwargs):
        raise AssertionError("resume must not re-specialize family curve")

    monkeypatch.setattr(runner, "_stored_curve", fail_respecialize)

    item = {
        "provider_key": "",
        "candidate": {"parameter": "5/7", "score": 9.0},
        "plugin": object(),
        "variant": object(),
        "family": object(),
        "fingerprints": {},
    }
    context = runner._materialize_population_context(
        db,
        run={"id": run_id},
        item=item,
        cache={},
    )
    assert context["curve_id"] == curve_id
    assert context["source_E"] is None
    assert list(context["E"].a_invariants()) == list(E.a_invariants())



def test_replan_aggressive_preserves_compatible_stage_results(tmp_path):
    db = runner.connect(tmp_path / "rank42.db")
    ensure_pipeline_schema(db)
    run_id = create_pipeline_run(
        db,
        pipeline_name="legacy aggressive",
        target_mode="family",
        target={"plugin_id": "demo", "variant_id": "v1"},
        stages=[
            {"id": "nagao_screen"},
            {"id": "nagao_rescore"},
            {"id": "prime_table_cache"},
            {"id": "mestre_nagao_ensemble"},
            {"id": "frobenius_persistence"},
            {"id": "local_root_numbers"},
            {"id": "family_baseline"},
        ],
        run_config={"target_rank": 20},
    )
    upsert_pipeline_candidate(
        db,
        run_id=run_id,
        provider_key="",
        parameter="5/7",
        score=9.0,
        status="running",
        stage_results={
            "3": {
                "stage_id": "prime_table_cache",
                "result": {
                    "status": "completed",
                    "prime_bound": 10000,
                    "frobenius_complete_through": 10000,
                },
            },
            "6": {
                "stage_id": "local_root_numbers",
                "result": {"status": "completed", "global_root_number": -1},
            },
        },
    )
    stages = runner._replan_run_from_preset(
        db,
        run_id=run_id,
        preset_id="aggressive_rank_hunter",
    )
    ids = [rec["id"] for rec in stages]
    assert ids[2] == "prime_table_cache"
    assert ids[5] == "select_survivors"

    row = get_pipeline_candidate(db, run_id, "", "5/7")
    results = runner._stage_results(row)
    assert results["3"]["stage_id"] == "prime_table_cache"
    assert "6" not in results



def test_stop_goal_uses_evidence_only_authoritative_lower(tmp_path):
    db = connect_core(tmp_path / "stop-goal-evidence.db")
    try:
        curve_id = upsert_curve(db, family="pipeline-stop", parameter="evidence")
        _rank_evidence(db, curve_id, lower=5, key="stop-goal-evidence-lower")

        row = db.execute(
            "SELECT descent_lower,generic_lower,exact_rank FROM curves WHERE id=?",
            (curve_id,),
        ).fetchone()
        assert int(row["descent_lower"] or 0) == 0
        assert int(row["generic_lower"] or 0) == 0
        assert row["exact_rank"] is None

        result, terminal, stop = runner._stage_result(
            "stop_goal",
            db=db,
            run={"id": 1, "target_mode": "general"},
            run_config={"target_rank": 5},
            target={},
            context={"curve_id": curve_id, "E": None, "parameter": "evidence"},
            config={},
            stage_index=2,
        )
    finally:
        db.close()

    assert result["rigorous_lower"] == 5
    assert result["rank_inconsistent"] is False
    assert result["goal_reached"] is True
    assert terminal is None
    assert stop is True


def test_stop_goal_refuses_inconsistent_reduced_rank_state(tmp_path):
    db = connect_core(tmp_path / "stop-goal-conflict.db")
    try:
        curve_id = upsert_curve(db, family="pipeline-stop", parameter="conflict")
        _rank_evidence(db, curve_id, lower=7, key="stop-goal-conflict-lower")
        _rank_evidence(db, curve_id, upper=6, key="stop-goal-conflict-upper")

        result, terminal, stop = runner._stage_result(
            "stop_goal",
            db=db,
            run={"id": 2, "target_mode": "general"},
            run_config={"target_rank": 7},
            target={},
            context={"curve_id": curve_id, "E": None, "parameter": "conflict"},
            config={},
            stage_index=2,
        )
    finally:
        db.close()

    assert result["rigorous_lower"] == 7
    assert result["rigorous_upper"] == 6
    assert result["rank_inconsistent"] is True
    assert result["goal_reached"] is False
    assert terminal is None
    assert stop is False


def test_record_breaker_stop_requires_durable_rigorous_witnesses(tmp_path):
    db = connect_core(tmp_path / "stop-goal-witnesses.db")
    try:
        curve_id = upsert_curve(db, family="pipeline-stop", parameter="record")
        _rank_evidence(db, curve_id, lower=2, key="stop-goal-record-lower")
        update_curve(
            db,
            curve_id,
            generators_json='[["legacy","generator"],["legacy","generator2"]]',
        )

        config = {"target_rank": 2, "record_breaker_mode": True}
        context = {"curve_id": curve_id, "E": None, "parameter": "record"}

        before, _, before_stop = runner._stage_result(
            "stop_goal",
            db=db,
            run={"id": 3, "target_mode": "general"},
            run_config=config,
            target={},
            context=context,
            config={},
            stage_index=2,
        )
        assert before["rigorous_lower"] == 2
        assert before["rigorous_witnesses"] == 0
        assert before["goal_reached"] is False
        assert before_stop is False

        for index in range(2):
            upsert_point(
                db,
                curve_id=curve_id,
                x=str(index),
                y=str(index + 1),
                source="pipeline-stop-test",
                role="rigorous_witness",
                exact_verified=True,
                independence_status="rigorous_independent",
                rigorous_independent=True,
                search_ref="pipeline-stop-test",
            )

        after, terminal, after_stop = runner._stage_result(
            "stop_goal",
            db=db,
            run={"id": 3, "target_mode": "general"},
            run_config=config,
            target={},
            context=context,
            config={},
            stage_index=2,
        )
    finally:
        db.close()

    assert after["rigorous_witnesses"] == 2
    assert after["goal_reached"] is True
    assert terminal is None
    assert after_stop is True


def test_upper_bound_rescue_continues_after_selmer_timeout(monkeypatch):
    state = {"upper": None}
    seen = []

    monkeypatch.setattr(
        runner,
        "_snapshot",
        lambda db, curve_id: {
            "rigorous_lower": 18,
            "rigorous_upper": state["upper"],
            "exact_rank": None,
        },
    )
    monkeypatch.setattr(runner, "get_curve", lambda db, curve_id: object())
    monkeypatch.setattr(runner, "proven_lower", lambda row: 18)
    monkeypatch.setattr(
        runner,
        "rigorous_witness_basis",
        lambda db, curve_id, E: ([object()], 1, True),
    )

    def fake_stage_result(stage_id, **kwargs):
        seen.append(stage_id)
        if stage_id == "selmer_bound":
            return {"status": "timeout", "rigorous_upper": None}, None, False
        if stage_id == "simon_covering":
            assert kwargs["config"]["limbigprime"] == 0
            state["upper"] = 18
            return {"status": "completed", "rigorous_upper": 18}, None, False
        raise AssertionError(stage_id)

    monkeypatch.setattr(runner, "_stage_result", fake_stage_result)

    result = runner._run_upper_bound_rescue_ladder(
        object(),
        run={"id": 9},
        run_config={"target_rank": 19},
        target={},
        context={"curve_id": 5, "E": object()},
        config={
            "pari_enabled": False,
            "isogeny_pari_enabled": False,
            "selmer_enabled": True,
            "selmer_timeout": 3,
            "known_basis_coverings": True,
            "simon_timeout": 3,
            "covering_timeout": 3,
            "higher_descent_hooks": False,
        },
        stage_index=12,
    )
    assert seen == ["selmer_bound", "simon_covering"]
    assert result["status"] == "completed"
    assert result["winner"] == "simon_covering"
    assert result["rigorous_upper"] == 18



def test_arithmetic_root_number_stage_uses_shared_local_authority(monkeypatch):
    captured = {}

    def fake_profile(db, **kwargs):
        captured["profile"] = kwargs
        return {
            "status": "completed",
            "global_root_number": -1,
            "complete": True,
            "arithmetic_conflicts": [],
        }

    def fake_publish(db, curve_id, value, **kwargs):
        captured["publish"] = {
            "curve_id": curve_id,
            "value": value,
            **kwargs,
        }
        return {
            "status": "promoted",
            "published": True,
            "conflict": False,
            "authority": -1,
            "provenance": {
                "kind": "local_computed",
                "source": kwargs["source"],
                "method": kwargs["method"],
            },
            "authority_check": {
                "conflicts": [],
                "matches": ["root_number"],
                "missing_authority": [],
                "consistent": True,
            },
        }

    monkeypatch.setattr(runner, "local_root_number_profile", fake_profile)
    monkeypatch.setattr(runner, "publish_curve_root_number", fake_publish)

    class E:
        def root_number(self):
            raise AssertionError("direct E.root_number() path must not be used")

    result, terminal, stop = runner._stage_result(
        "root_number",
        db=object(),
        run={"id": 7, "target_mode": "general"},
        run_config={"target_rank": 10},
        target={},
        context={"curve_id": 11, "E": E(), "parameter": "p"},
        config={"prime_bound": 97, "bad_prime_timeout": 3},
        stage_index=4,
    )

    assert result["status"] == "completed"
    assert result["root_number"] == -1
    assert result["published_to_curve_arithmetic"] is True
    assert captured["profile"]["prime_bound"] == 97
    assert captured["profile"]["bad_prime_timeout"] == 3
    assert captured["profile"]["include_all_bad"] is True
    assert captured["publish"]["source"] == "pipeline:root_number"
    assert captured["publish"]["method"] == "prime_local_finite_product"
    assert terminal is None
    assert stop is False


def test_arithmetic_root_number_stage_preserves_authority_conflict(monkeypatch):
    monkeypatch.setattr(
        runner,
        "local_root_number_profile",
        lambda *args, **kwargs: {
            "status": "completed",
            "global_root_number": -1,
            "complete": True,
            "arithmetic_conflicts": [],
        },
    )
    monkeypatch.setattr(
        runner,
        "publish_curve_root_number",
        lambda *args, **kwargs: {
            "status": "conflict",
            "published": False,
            "conflict": True,
            "authority": 1,
            "provenance": {
                "kind": "local_computed",
                "source": "pipeline:root_number",
                "method": "prime_local_finite_product",
            },
            "authority_check": {
                "conflicts": ["root_number"],
                "matches": [],
                "missing_authority": [],
                "consistent": False,
            },
        },
    )

    result, terminal, stop = runner._stage_result(
        "root_number",
        db=object(),
        run={"id": 7, "target_mode": "general"},
        run_config={"target_rank": 10},
        target={},
        context={"curve_id": 11, "E": object(), "parameter": "p"},
        config={},
        stage_index=4,
    )

    assert result["status"] == "inconclusive"
    assert result["reason"] == "root_number_authority_conflict"
    assert result["published_to_curve_arithmetic"] is False
    assert result["arithmetic_conflicts"] == ["root_number"]
    assert terminal is None
    assert stop is False


def test_local_root_stage_does_not_overwrite_conflicting_arithmetic_authority(
    monkeypatch, tmp_path
):
    db = connect_core(tmp_path / "root-conflict.db")
    try:
        curve_id = upsert_curve(db, family="root-authority", parameter="conflict")
        update_curve(db, curve_id, root_number=1)

        monkeypatch.setattr(
            runner,
            "local_root_number_profile",
            lambda *args, **kwargs: {
                "status": "inconclusive",
                "global_root_number": -1,
                "complete": True,
                "arithmetic_conflicts": ["root_number"],
                "arithmetic_authority_check": {
                    "conflicts": ["root_number"],
                    "matches": [],
                    "missing_authority": [],
                    "consistent": False,
                },
            },
        )

        result, terminal, stop = runner._stage_result(
            "local_root_numbers",
            db=db,
            run={"id": 1, "target_mode": "general"},
            run_config={"target_rank": 1},
            target={},
            context={"curve_id": curve_id, "E": object(), "parameter": "p"},
            config={
                "global_sign": "+1",
                "prune_mismatch": True,
                "prime_bound": 11,
                "include_all_bad": True,
                "bad_prime_timeout": 1,
            },
            stage_index=4,
        )

        row = db.execute(
            "SELECT root_number FROM curves WHERE id=?", (curve_id,)
        ).fetchone()
        assert int(row["root_number"]) == 1
        assert result["status"] == "inconclusive"
        assert result["arithmetic_conflicts"] == ["root_number"]
        assert result["published_to_curve_arithmetic"] is False
        assert result["filter_mismatch"] is False
        assert terminal is None
        assert stop is False
    finally:
        db.close()


def test_local_root_stage_publishes_missing_root_through_arithmetic_projection(
    monkeypatch, tmp_path
):
    db = connect_core(tmp_path / "root-publish.db")
    try:
        curve_id = upsert_curve(db, family="root-authority", parameter="publish")

        monkeypatch.setattr(
            runner,
            "local_root_number_profile",
            lambda *args, **kwargs: {
                "status": "completed",
                "global_root_number": -1,
                "complete": True,
                "arithmetic_conflicts": [],
                "arithmetic_authority_check": {
                    "conflicts": [],
                    "matches": [],
                    "missing_authority": ["root_number"],
                    "consistent": True,
                },
            },
        )

        result, terminal, stop = runner._stage_result(
            "local_root_numbers",
            db=db,
            run={"id": 1, "target_mode": "general"},
            run_config={"target_rank": 1},
            target={},
            context={"curve_id": curve_id, "E": object(), "parameter": "p"},
            config={
                "global_sign": "any",
                "prune_mismatch": False,
                "prime_bound": 11,
                "include_all_bad": True,
                "bad_prime_timeout": 1,
            },
            stage_index=4,
        )

        row = db.execute(
            "SELECT root_number FROM curves WHERE id=?", (curve_id,)
        ).fetchone()
        assert int(row["root_number"]) == -1
        assert result["published_to_curve_arithmetic"] is True
        assert result["arithmetic_conflicts"] == []
        assert result["arithmetic_authority_check"]["matches"] == ["root_number"]
        assert terminal is None
        assert stop is False
    finally:
        db.close()


def test_bad_prime_filter_does_not_prune_on_arithmetic_authority_conflict(
    monkeypatch
):
    monkeypatch.setattr(
        runner,
        "bad_prime_fingerprint",
        lambda *args, **kwargs: {
            "status": "inconclusive",
            "bad_primes": [2],
            "bad_primes_complete": True,
            "arithmetic_conflicts": ["bad_primes"],
            "arithmetic_authority_check": {
                "conflicts": ["bad_primes"],
                "matches": [],
                "missing_authority": [],
                "consistent": False,
            },
        },
    )

    result, terminal, stop = runner._stage_result(
        "bad_prime_fingerprint",
        db=object(),
        run={"id": 1, "target_mode": "general"},
        run_config={"target_rank": 1},
        target={},
        context={"curve_id": 7, "E": object(), "parameter": "p"},
        config={
            "required_primes": [3],
            "max_bad_primes": 0,
            "prune_mismatch": True,
            "prime_bound": 11,
            "include_all_bad": True,
            "bad_prime_timeout": 1,
        },
        stage_index=4,
    )

    assert result["missing_required_primes"] == [3]
    assert result["arithmetic_conflicts"] == ["bad_primes"]
    assert result["filter_mismatch"] is False
    assert terminal is None
    assert stop is False


def test_local_solubility_stage_marks_unknown_reduction_prime_set_partial(monkeypatch):
    monkeypatch.setattr(
        runner,
        "cache_curve_primes",
        lambda *args, **kwargs: {
            "status": "inconclusive",
            "unknown_reduction_primes": [5],
            "bad_primes_complete": False,
            "bad_prime_error": "injected reduction failure",
        },
    )
    monkeypatch.setattr(
        runner,
        "local_sieve_prime_set",
        lambda *args, **kwargs: (2, 3),
    )
    monkeypatch.setattr(
        runner,
        "sieve_stored_quartics",
        lambda *args, **kwargs: {
            "status": "completed",
            "quartics_tested": 1,
            "obstructed": [{"search_id": 11, "p": 3, "hole_label": "q"}],
            "obstructed_count": 1,
            "primes": [2, 3],
            "passing_is_proof_of_local_solubility": False,
        },
    )

    result, terminal, stop = runner._stage_result(
        "local_solubility_sieve",
        db=object(),
        run={"id": 1, "target_mode": "general"},
        run_config={"target_rank": 1},
        target={},
        context={"curve_id": 7, "E": object(), "parameter": "p"},
        config={
            "explicit_primes": [2, 3],
            "include_bad": True,
            "max_prime": 7,
            "bad_prime_timeout": 1,
        },
        stage_index=4,
    )

    assert result["status"] == "completed_partial_prime_set"
    assert result["prime_set_complete_for_requested_bound"] is False
    assert result["unknown_reduction_primes"] == [5]
    assert result["prime_cache_status"] == "inconclusive"
    assert result["requested_max_prime"] == 7
    assert result["include_bad_primes"] is True
    # The rigorous obstruction that actually ran is preserved unchanged.
    assert result["obstructed_count"] == 1
    assert result["obstructed"][0]["p"] == 3
    assert terminal is None
    assert stop is False


def test_local_solubility_stage_distinguishes_global_bad_prime_timeout_from_bounded_gap(monkeypatch):
    monkeypatch.setattr(
        runner,
        "cache_curve_primes",
        lambda *args, **kwargs: {
            "status": "inconclusive",
            "unknown_reduction_primes": [],
            "bad_primes_complete": False,
            "bad_prime_error": "global bad-prime factorization timeout",
        },
    )
    monkeypatch.setattr(
        runner,
        "local_sieve_prime_set",
        lambda *args, **kwargs: (2, 3, 5, 7),
    )
    monkeypatch.setattr(
        runner,
        "sieve_stored_quartics",
        lambda *args, **kwargs: {
            "status": "completed",
            "quartics_tested": 0,
            "obstructed": [],
            "obstructed_count": 0,
            "primes": [2, 3, 5, 7],
            "passing_is_proof_of_local_solubility": False,
        },
    )

    result, terminal, stop = runner._stage_result(
        "local_solubility_sieve",
        db=object(),
        run={"id": 1, "target_mode": "general"},
        run_config={"target_rank": 1},
        target={},
        context={"curve_id": 7, "E": object(), "parameter": "p"},
        config={
            "explicit_primes": [2, 3],
            "include_bad": True,
            "max_prime": 7,
            "bad_prime_timeout": 1,
        },
        stage_index=4,
    )

    assert result["status"] == "completed"
    assert result["prime_set_complete_for_requested_bound"] is True
    assert result["unknown_reduction_primes"] == []
    assert result["bad_primes_complete"] is False
    assert result["bad_prime_error"] == "global bad-prime factorization timeout"
    assert terminal is None
    assert stop is False


def test_local_solubility_stage_explicit_only_does_not_require_reduction_classification(monkeypatch):
    monkeypatch.setattr(
        runner,
        "cache_curve_primes",
        lambda *args, **kwargs: {
            "status": "inconclusive",
            "unknown_reduction_primes": [5],
            "bad_primes_complete": False,
            "bad_prime_error": "injected reduction failure",
        },
    )
    monkeypatch.setattr(
        runner,
        "local_sieve_prime_set",
        lambda *args, **kwargs: (2, 3),
    )
    monkeypatch.setattr(
        runner,
        "sieve_stored_quartics",
        lambda *args, **kwargs: {
            "status": "completed",
            "quartics_tested": 0,
            "obstructed": [],
            "obstructed_count": 0,
            "primes": [2, 3],
            "passing_is_proof_of_local_solubility": False,
        },
    )

    result, terminal, stop = runner._stage_result(
        "local_solubility_sieve",
        db=object(),
        run={"id": 1, "target_mode": "general"},
        run_config={"target_rank": 1},
        target={},
        context={"curve_id": 7, "E": object(), "parameter": "p"},
        config={
            "explicit_primes": [2, 3],
            "include_bad": False,
            "max_prime": 7,
            "bad_prime_timeout": 1,
        },
        stage_index=4,
    )

    assert result["status"] == "completed"
    assert result["prime_set_complete_for_requested_bound"] is True
    assert result["unknown_reduction_primes"] == [5]
    assert result["include_bad_primes"] is False
    assert terminal is None
    assert stop is False


def test_native_model_seed_bypasses_minimal_prep_and_defers_certificate(monkeypatch):
    captured = {}

    def fake_point_tier(db, **kwargs):
        captured.update(kwargs)
        return {
            "exact_points": 0,
            "rank_growth": 0,
            "completed_searches": 1,
            "rigorous_lower": 18,
        }

    monkeypatch.setattr(runner, "run_auto_point_tier", fake_point_tier)
    result, terminal, stop = runner._stage_result(
        "integral_seed",
        db=object(),
        run={"id": 4, "target_mode": "family"},
        run_config={
            "ratpoints": "/tmp/ratpoints_gpu",
            "certificate_timeout": 120,
            "exact_candidates": 64,
        },
        target={},
        context={"curve_id": 7, "E": object(), "parameter": "p"},
        config={
            "heights": [1000, 10000],
            "timeout": 4,
            "model_mode": "stored",
            "model_prep_timeout": 8,
            "certify_after_search": False,
        },
        stage_index=9,
    )
    assert captured["minimal_model"] is False
    assert captured["native_only"] is True
    assert captured["denominator_low"] == 1
    assert captured["denominator_high"] == 1
    assert captured["certify_after_search"] is False
    assert result["status"] == "completed"
    assert terminal is None
    assert stop is False


def test_replan_drops_result_when_stage_config_changes(tmp_path):
    db = runner.connect(tmp_path / "rank42.db")
    ensure_pipeline_schema(db)
    old_stages = [
        {"id": "nagao_screen", "config": {"prime_bound": 523, "keep": 20000}},
        {"id": "nagao_rescore", "config": {"prime_bound": 5000, "keep": 1000}},
        {
            "id": "prime_table_cache",
            "config": {
                "prime_bound": 5000,
                "include_all_bad": False,
                "eager_bad_primes": False,
            },
        },
        {"id": "mestre_nagao_ensemble", "config": {"prime_bound": 5000}},
        {"id": "frobenius_persistence", "config": {"bounds": [523, 1979, 5000]}},
        {"id": "select_survivors", "config": {"keep": 64, "ranking": "score"}},
        {"id": "family_baseline"},
        {"id": "select_survivors", "config": {"keep": 16, "ranking": "rank_then_score"}},
        {
            "id": "integral_seed",
            "config": {
                "heights": [1000, 10000, 100000],
                "timeout": 6,
                "model_mode": "minimal",
                "model_prep_timeout": 8,
                "certify_after_search": True,
            },
        },
    ]
    run_id = create_pipeline_run(
        db,
        pipeline_name="old aggressive",
        target_mode="family",
        target={"plugin_id": "demo", "variant_id": "v1"},
        stages=old_stages,
        run_config={"target_rank": 20},
    )
    upsert_pipeline_candidate(
        db,
        run_id=run_id,
        provider_key="",
        parameter="5/7",
        score=9.0,
        status="running",
        stage_results={
            "3": {
                "stage_id": "prime_table_cache",
                "result": {
                    "status": "completed",
                    "frobenius_complete_through": 5000,
                },
            },
            "9": {
                "stage_id": "integral_seed",
                "result": {"status": "completed", "model_prep_error": "timeout"},
            },
        },
    )
    runner._replan_run_from_preset(
        db,
        run_id=run_id,
        preset_id="aggressive_rank_hunter",
    )
    row = get_pipeline_candidate(db, run_id, "", "5/7")
    results = runner._stage_results(row)
    assert results["3"]["stage_id"] == "prime_table_cache"
    assert "9" not in results



def test_replan_stage_compatibility_preserves_superset_cache_but_not_changed_search():
    assert runner._replan_stage_result_compatible(
        previous={
            "id": "prime_table_cache",
            "config": {"prime_bound": 2000, "include_all_bad": True},
        },
        wanted={
            "id": "prime_table_cache",
            "config": {
                "prime_bound": 5000,
                "include_all_bad": False,
                "eager_bad_primes": False,
            },
        },
        entry={
            "stage_id": "prime_table_cache",
            "result": {
                "status": "completed",
                "prime_bound": 10000,
                "frobenius_complete_through": 10000,
                "bad_primes_complete": False,
            },
        },
    ) is True

    assert runner._replan_stage_result_compatible(
        previous={
            "id": "prime_table_cache",
            "config": {"prime_bound": 10000},
        },
        wanted={
            "id": "prime_table_cache",
            "config": {"prime_bound": 5000},
        },
        entry={
            "stage_id": "prime_table_cache",
            "result": {
                "status": "partial",
                "prime_bound": 10000,
                "frobenius_complete_through": 1979,
                "frobenius_failed_primes": [1979],
            },
        },
    ) is False

    assert runner._replan_stage_result_compatible(
        previous={
            "id": "integral_seed",
            "config": {
                "model_mode": "minimal",
                "certify_after_search": True,
            },
        },
        wanted={
            "id": "integral_seed",
            "config": {
                "model_mode": "stored",
                "certify_after_search": False,
            },
        },
        entry={
            "stage_id": "integral_seed",
            "result": {"status": "completed"},
        },
    ) is False



def test_large_height_generator_hunt_searches_before_late_saturation(monkeypatch):
    seen = []

    monkeypatch.setattr(
        runner,
        "_strategy_research_state",
        lambda db, curve_id, E, witness_basis=False: {
            "rigorous_lower": 5,
            "rigorous_upper": None,
            "exact_rank": None,
            "rank_inconsistent": False,
            "witness_basis_complete": True if witness_basis else None,
            "witness_basis_required": 5 if witness_basis else None,
            "rigorous_witnesses": 5 if witness_basis else None,
            "research_state_source": "curve_research_state",
            
        },
    )
    monkeypatch.setattr(
        runner,
        "_runtime_hunt_capabilities",
        lambda db, context: frozenset({
            "exact_curve",
            "higher_descent_hook",
            "covering_hook",
            "core_point_search",
        }),
    )

    def fake_stage_result(stage_id, **kwargs):
        seen.append(stage_id)
        return {"status": "completed"}, None, False

    monkeypatch.setattr(runner, "_stage_result", fake_stage_result)

    result = runner._run_large_height_generator_hunt(
        object(),
        run={"id": 77},
        run_config={"target_rank": 20},
        target={},
        context={"curve_id": 9, "E": object(), "parameter": "1/2"},
        config={
            "descent_levels": [4, 8],
            "padic_enabled": False,
            "denominator_bands": [[2, 100]],
            "late_saturation_enabled": True,
            "late_lattice_enabled": True,
            "saturation_bounds": [7, 31],
            "saturation_timeout": 5,
            "certificate_timeout": 30,
            "exact_candidates": 16,
            "stop_on_growth": True,
        },
        stage_index=5,
    )

    assert seen == [
        "higher_descent_ladder",
        "selmer_element_fanout",
        "mw_growth_loop",
        "denominator_band",
        "full_saturation_index_recovery",
        "height_lattice_reduction",
    ]
    assert seen.index("mw_growth_loop") < seen.index("full_saturation_index_recovery")
    assert seen.index("denominator_band") < seen.index("full_saturation_index_recovery")
    assert result["rigorous_lower"] == 5
    assert result["exhausted_strategy_budget"] is True



def test_capability_driven_hunt_skips_unavailable_plugin_branches(monkeypatch):
    seen = []

    monkeypatch.setattr(
        runner,
        "_strategy_research_state",
        lambda db, curve_id, E, witness_basis=False: {
            "rigorous_lower": 3,
            "rigorous_upper": None,
            "exact_rank": None,
            "rank_inconsistent": False,
            "witness_basis_complete": True if witness_basis else None,
            "witness_basis_required": 3 if witness_basis else None,
            "rigorous_witnesses": 3 if witness_basis else None,
            "research_state_source": "curve_research_state",
            
        },
    )
    monkeypatch.setattr(
        runner,
        "_runtime_hunt_capabilities",
        lambda db, context: frozenset({
            "exact_curve",
            "core_point_search",
            "core_prime_sieve",
        }),
    )

    def fake_stage_result(stage_id, **kwargs):
        seen.append(stage_id)
        return {"status": "completed"}, None, False

    monkeypatch.setattr(runner, "_stage_result", fake_stage_result)

    result = runner._run_large_height_generator_hunt(
        object(),
        run={"id": 88},
        run_config={"target_rank": 10},
        target={},
        context={"curve_id": 4, "E": object(), "parameter": "7/9"},
        config={
            "descent_levels": [4, 8, 12],
            "padic_enabled": True,
            "denominator_bands": [[2, 20]],
            "late_saturation_enabled": False,
            "certificate_timeout": 30,
            "exact_candidates": 16,
            "stop_on_growth": True,
        },
        stage_index=5,
    )

    assert "higher_descent_ladder" not in seen
    assert "selmer_element_fanout" not in seen
    assert "padic_covering_search" not in seen
    assert seen == ["mw_growth_loop", "denominator_band"]
    skipped = {
        rec["stage_id"]: rec
        for rec in result["steps"]
        if rec["status"] == "skipped"
    }
    assert skipped["higher_descent_ladder"]["result"]["capability_driven"] is True
    assert skipped["selmer_element_fanout"]["result"]["capability_driven"] is True
    assert skipped["padic_covering_search"]["result"]["capability_driven"] is True


def test_runtime_hunt_capabilities_detect_optional_family_hooks(monkeypatch):
    from rank42.plugin_api import FamilyAdapterV1

    class Family:
        def generic_section_points(self, parameter):
            return []

    class Adapter(FamilyAdapterV1):
        def derive_pipeline_coverings(self, payload):
            return None

        def derive_pipeline_transform(self, payload):
            return None

        def run_pipeline_higher_descent(self, payload):
            return None

        def run_pipeline_padic_covering_search(self, payload):
            return None

    class Plugin:
        capabilities = frozenset({"target_search"})

    class EmptyDB:
        def execute(self, *args, **kwargs):
            class Row:
                def fetchone(self):
                    return None
            return Row()

    monkeypatch.setattr(runner, "load_adapter", lambda plugin: Adapter())

    caps = runner._runtime_hunt_capabilities(
        EmptyDB(),
        {
            "curve_id": 12,
            "family": Family(),
            "plugin": Plugin(),
        },
    )
    assert "family_sections" in caps
    assert "covering_hook" in caps
    assert "higher_descent_hook" in caps
    assert "padic_covering_hook" in caps
    assert "pipeline_transform_hook" in caps
    assert "target_search" in caps


def test_exact_torsion_timeout_is_inconclusive_not_mismatch(monkeypatch):
    from rank42.torsion import TorsionTimeout

    monkeypatch.setattr(
        runner,
        "persist_curve_torsion",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            TorsionTimeout("injected exact torsion timeout")
        ),
    )

    result, terminal, stop = runner._stage_result(
        "exact_torsion",
        db=object(),
        run={"id": 3, "target_mode": "torsion"},
        run_config={},
        target={"torsion_group": "C5"},
        context={
            "curve_id": 5,
            "E": object(),
            "source_E": None,
            "family": None,
            "parameter": "1/3",
        },
        config={"timeout": 7},
        stage_index=4,
    )
    assert result["status"] == "timeout"
    assert result["reason"] == "exact_torsion_timeout"
    assert result["timeout"] == 7
    assert result["torsion_known"] is False
    assert terminal is None
    assert stop is False


def test_exact_torsion_completed_mismatch_remains_exact_filter(monkeypatch):
    monkeypatch.setattr(
        runner,
        "persist_curve_torsion",
        lambda *args, **kwargs: {
            "torsion_order": 2,
            "torsion_invariants_json": "[2]",
            "torsion_label": "C2",
        },
    )
    result, terminal, stop = runner._stage_result(
        "exact_torsion",
        db=object(),
        run={"id": 3, "target_mode": "torsion"},
        run_config={},
        target={"torsion_group": "C5"},
        context={
            "curve_id": 5,
            "E": object(),
            "source_E": None,
            "family": None,
            "parameter": "1/3",
        },
        config={"timeout": 7},
        stage_index=4,
    )
    assert result["status"] == "torsion_mismatch"
    assert result["wanted_torsion"] == "C5"
    assert result["torsion_known"] is True
    assert terminal == "torsion_mismatch"
    assert stop is False


def test_family_baseline_skips_family_without_generic_sections():
    class Family:
        pass

    result, terminal, stop = runner._stage_result(
        "family_baseline",
        db=object(),
        run={"id": 3, "target_mode": "family"},
        run_config={},
        target={},
        context={
            "curve_id": 5,
            "E": object(),
            "source_E": None,
            "family": Family(),
            "parameter": "1/3",
        },
        config={},
        stage_index=4,
    )
    assert result["status"] == "skipped"
    assert result["reason"] == "family_has_no_generic_section_capability"
    assert result["capability_driven"] is True
    assert terminal is None
    assert stop is False


def test_record_breaker_lane_skips_below_rigorous_threshold(monkeypatch):
    monkeypatch.setattr(
        runner,
        "_strategy_research_state",
        lambda db, curve_id, E, witness_basis=False: {
            "rigorous_lower": 24,
            "rigorous_upper": None,
            "exact_rank": None,
            "rank_inconsistent": False,
            "witness_basis_complete": True if witness_basis else None,
            "witness_basis_required": 24 if witness_basis else None,
            "rigorous_witnesses": 24 if witness_basis else None,
            "research_state_source": "curve_research_state",
            
        },
    )

    called = []
    monkeypatch.setattr(
        runner,
        "_run_large_height_generator_hunt",
        lambda *args, **kwargs: called.append(True) or {"status": "completed"},
    )

    result, terminal, stop = runner._stage_result(
        "record_breaker_lane",
        db=object(),
        run={"id": 7, "target_mode": "family"},
        run_config={"target_rank": 31},
        target={},
        context={"curve_id": 11, "E": object(), "parameter": "p"},
        config={"minimum_rank": 25},
        stage_index=9,
    )
    assert result["status"] == "skipped"
    assert result["rigorous_lower"] == 24
    assert result["minimum_rank"] == 25
    assert result["heuristics_do_not_promote"] is True
    assert called == []
    assert terminal is None
    assert stop is False


def test_record_breaker_lane_runs_deep_hunt_at_rigorous_threshold(monkeypatch):
    monkeypatch.setattr(
        runner,
        "_strategy_research_state",
        lambda db, curve_id, E, witness_basis=False: {
            "rigorous_lower": 25,
            "rigorous_upper": None,
            "exact_rank": None,
            "rank_inconsistent": False,
            "witness_basis_complete": True if witness_basis else None,
            "witness_basis_required": 25 if witness_basis else None,
            "rigorous_witnesses": 25 if witness_basis else None,
            "research_state_source": "curve_research_state",
            
        },
    )
    captured = {}

    def fake_hunt(db, **kwargs):
        captured.update(kwargs)
        return {
            "status": "completed",
            "rigorous_lower": 26,
            "rank_growth": 1,
        }

    monkeypatch.setattr(runner, "_run_large_height_generator_hunt", fake_hunt)
    config = {
        "minimum_rank": 25,
        "denominator_charts": 128,
        "denominator_bands": [[1000001, 10000000]],
        "stop_on_growth": False,
    }
    result, terminal, stop = runner._stage_result(
        "record_breaker_lane",
        db=object(),
        run={"id": 7, "target_mode": "family"},
        run_config={"target_rank": 31},
        target={},
        context={"curve_id": 11, "E": object(), "parameter": "p"},
        config=config,
        stage_index=9,
    )
    assert result["record_breaker_lane"] is True
    assert result["entry_rigorous_lower"] == 25
    assert result["rigorous_lower"] == 26
    assert captured["config"]["denominator_charts"] == 128
    assert "minimum_rank" not in captured["config"]
    assert terminal is None
    assert stop is False


def test_family_named_specialization_points_map_to_stored_model():
    from sage.all import EllipticCurve, QQ
    from rank42.auto_search import _family_named_points_on_stored_model

    source = EllipticCurve(QQ, [0, 0, 0, -1, 0])
    point = source(0, 0)

    class Family:
        def certified_specialization_points(self, _t):
            return [point]

    got = _family_named_points_on_stored_model(
        Family(),
        "certified_specialization_points",
        "1",
        source,
        source,
    )
    assert len(got) == 1
    assert got[0] == point


def test_family_named_points_exposes_plugin_and_transport_failures():
    from sage.all import EllipticCurve, QQ
    from rank42.auto_search import (
        FamilyPointLoadError,
        _family_named_points_on_stored_model,
    )

    source = EllipticCurve(QQ, [0, 0, 0, -1, 0])
    other = EllipticCurve(QQ, [0, 0, 0, -1, 1])
    point = source(0, 0)

    class BrokenHook:
        def generic_section_points(self, _t):
            raise RuntimeError("broken family hook")

    with pytest.raises(FamilyPointLoadError) as excinfo:
        _family_named_points_on_stored_model(
            BrokenHook(), "generic_section_points", "1", source, source
        )
    assert excinfo.value.reason == "plugin_exception"

    class InvalidPoint:
        def generic_section_points(self, _t):
            return [object()]

    with pytest.raises(FamilyPointLoadError) as excinfo:
        _family_named_points_on_stored_model(
            InvalidPoint(), "generic_section_points", "1", source, source
        )
    assert excinfo.value.reason == "invalid_plugin_point"

    class NeedsTransport:
        def generic_section_points(self, _t):
            return [point]

    with pytest.raises(FamilyPointLoadError) as excinfo:
        _family_named_points_on_stored_model(
            NeedsTransport(), "generic_section_points", "1", source, other
        )
    assert excinfo.value.reason == "model_transport_error"


def test_specialization_seed_stage_surfaces_family_hook_error(monkeypatch):
    class Family:
        def certified_specialization_points(self, _t):
            return []

    monkeypatch.setattr(
        runner,
        "_attach_family_specialization_seed",
        lambda *_args, **_kwargs: {
            "specialization_seed_points": 0,
            "specialization_seed_verified": False,
            "family_point_status": "plugin_exception",
            "family_point_error": {"detail": "boom"},
            "certificate_status": "not_attempted",
            "growth": 0,
            "rigorous_lower": 0,
        },
    )

    result, terminal, stop = runner._stage_result(
        "specialization_seeds",
        db=object(),
        run={"id": 8, "target_mode": "family"},
        run_config={},
        target={},
        context={
            "curve_id": 17,
            "E": object(),
            "source_E": object(),
            "family": Family(),
            "parameter": "7/5",
        },
        config={},
        stage_index=2,
    )
    assert result["status"] == "error"
    assert result["outcome"] == "plugin_exception"
    assert result["reason"] == "plugin_exception"
    assert terminal is None
    assert stop is False


def test_family_baseline_stage_preserves_certificate_timeout(monkeypatch):
    class Family:
        def generic_section_points(self, _t):
            return []

    monkeypatch.setattr(
        runner,
        "_attach_family_baseline",
        lambda *_args, **_kwargs: {
            "section_points": 2,
            "family_point_status": "completed",
            "specialization_seed_status": "no_points_for_parameter",
            "bundle_certificate_status": "timeout",
            "growth": 0,
            "rigorous_lower": 0,
        },
    )

    result, terminal, stop = runner._stage_result(
        "family_baseline",
        db=object(),
        run={"id": 9, "target_mode": "family"},
        run_config={},
        target={},
        context={
            "curve_id": 18,
            "E": object(),
            "source_E": object(),
            "family": Family(),
            "parameter": "11/7",
        },
        config={},
        stage_index=3,
    )
    assert result["status"] == "timeout"
    assert result["outcome"] == "certificate_timeout"
    assert result["reason"] == "certificate_timeout"
    assert terminal is None
    assert stop is False


def test_specialization_seed_stage_skips_family_without_seed_hook():
    class Family:
        pass

    result, terminal, stop = runner._stage_result(
        "specialization_seeds",
        db=object(),
        run={"id": 3, "target_mode": "family"},
        run_config={},
        target={},
        context={
            "curve_id": 5,
            "E": object(),
            "source_E": None,
            "family": Family(),
            "parameter": "1/3",
        },
        config={},
        stage_index=4,
    )
    assert result["status"] == "skipped"
    assert result["reason"] == "family_has_no_specialization_seed_capability"
    assert result["capability_driven"] is True
    assert terminal is None
    assert stop is False


def test_specialization_seed_stage_uses_plugin_bundle_before_family_baseline(monkeypatch):
    class Family:
        def certified_specialization_points(self, parameter):
            return [object()]

        def curve(self, parameter):
            return object()

    captured = {}
    monkeypatch.setattr(
        runner,
        "_attach_family_specialization_seed",
        lambda db, curve_id, family, parameter, source_E, stored_E, certificate_timeout: (
            captured.update({
                "curve_id": curve_id,
                "parameter": parameter,
                "certificate_timeout": certificate_timeout,
            })
            or {
                "specialization_seed_points": 9,
                "specialization_seed_verified": True,
                "growth": 5,
                "rigorous_lower": 9,
            }
        ),
    )

    result, terminal, stop = runner._stage_result(
        "specialization_seeds",
        db=object(),
        run={"id": 8, "target_mode": "family"},
        run_config={"certificate_timeout": 120},
        target={},
        context={
            "curve_id": 17,
            "E": object(),
            "source_E": object(),
            "family": Family(),
            "parameter": "-61/16",
        },
        config={"certificate_timeout": 240},
        stage_index=2,
    )
    assert result["status"] == "completed"
    assert result["specialization_seed_verified"] is True
    assert result["rigorous_lower"] == 9
    assert captured == {
        "curve_id": 17,
        "parameter": "-61/16",
        "certificate_timeout": 240,
    }
    assert terminal is None
    assert stop is False



def test_record_breaker_lane_goal_minus_one_uses_run_target(monkeypatch):
    monkeypatch.setattr(
        runner,
        "_strategy_research_state",
        lambda db, curve_id, E, witness_basis=False: {
            "rigorous_lower": 10,
            "rigorous_upper": None,
            "exact_rank": None,
            "rank_inconsistent": False,
            "witness_basis_complete": True if witness_basis else None,
            "witness_basis_required": 10 if witness_basis else None,
            "rigorous_witnesses": 10 if witness_basis else None,
            "research_state_source": "curve_research_state",
            
        },
    )
    called = []

    monkeypatch.setattr(
        runner,
        "_run_large_height_generator_hunt",
        lambda *args, **kwargs: called.append(kwargs["config"]) or {
            "status": "completed",
            "rigorous_lower": 10,
        },
    )

    result, terminal, stop = runner._stage_result(
        "record_breaker_lane",
        db=object(),
        run={"id": 9, "target_mode": "family"},
        run_config={"target_rank": 11},
        target={},
        context={"curve_id": 1, "E": object(), "parameter": "-463/191"},
        config={
            **runner.stage_spec("record_breaker_lane").defaults,
            "minimum_rank_mode": "goal_minus_one",
        },
        stage_index=12,
    )
    assert result["status"] == "completed"
    assert result["minimum_rank"] == 10
    assert result["minimum_rank_mode"] == "goal_minus_one"
    assert called
    assert "minimum_rank" not in called[0]
    assert "minimum_rank_mode" not in called[0]
    assert terminal is None
    assert stop is False


def test_affine_stage_wires_pipeline_checkpoint_and_retry_policy(monkeypatch):
    captured = {}

    def fake_point_tier(db, **kwargs):
        captured.update(kwargs)
        return {
            "planned_searches": 1,
            "completed_searches": 1,
            "resume_completed": 0,
            "resume_timeouts": 0,
            "resume_failures": 0,
            "timeouts": 0,
            "failures": 0,
            "exact_points": 0,
            "rank_growth": 0,
        }

    monkeypatch.setattr(runner, "run_auto_point_tier", fake_point_tier)

    result, terminal, stop = runner._stage_result(
        "affine_search",
        db=object(),
        run={"id": 17, "target_mode": "curve"},
        run_config={},
        target={},
        context={
            "curve_id": 9,
            "E": object(),
            "parameter": "curve:9",
            "pipeline_candidate_id": 44,
        },
        config={
            "heights": [1000],
            "charts": 2,
            "timeout": 4,
            "retry_policy": "automatic",
            "retry_timeout": 16,
        },
        stage_index=6,
    )

    assert result["status"] == "completed"
    assert captured["pipeline_checkpoint"] == {
        "run_id": 17,
        "candidate_id": 44,
        "stage_index": 6,
        "stage_id": "affine_search",
    }
    assert captured["retry_policy"] == "automatic"
    assert captured["retry_timeout"] == 16
    assert terminal is None
    assert stop is False



def test_small_point_density_map_back_failure_marks_stage_partial(monkeypatch):
    prepared = {
        "search_model": "global_minimal",
        "model_prep_error": None,
    }
    monkeypatch.setattr(
        runner,
        "prepare_point_search_model",
        lambda db, **kwargs: prepared,
    )
    monkeypatch.setattr(
        runner,
        "run_auto_point_tier",
        lambda db, **kwargs: {
            "planned_searches": 1,
            "completed_searches": 1,
            "resume_completed": 0,
            "resume_timeouts": 0,
            "resume_failures": 0,
            "timeouts": 0,
            "failures": 0,
            "map_back_failures": 1,
            "exact_points": 0,
            "rank_growth": 0,
            "rigorous_lower": 4,
            "search_model": "global_minimal",
        },
    )

    result, terminal, stop = runner._stage_result(
        "small_point_density",
        db=object(),
        run={"id": 9, "target_mode": "general"},
        run_config={
            "ratpoints": None,
            "certificate_timeout": 120,
            "exact_candidates": 32,
        },
        target={},
        context={"curve_id": 7, "E": None, "parameter": "p"},
        config={
            "heights": [100],
            "timeout": 1,
            "certificate_timeout": 120,
            "exact_candidates": 32,
        },
        stage_index=3,
    )

    assert result["map_back_failures"] == 1
    assert result["status"] == "partial"
    assert result["search_outcome"] == "partial"
    assert result["scored_rounds"] == 0
    assert result["pipeline_score"] is None
    assert result["rounds"][0]["comparable_for_score"] is False
    assert terminal is None
    assert stop is False



def test_simon_pipeline_stage_defaults_to_deterministic_limbigprime(monkeypatch):
    captured = []

    def fake_covering(db, **kwargs):
        captured.append(dict(kwargs))
        return {
            "status": "completed",
            "rigorous_upper": None,
            "exact_points": 0,
            "rank_growth": 0,
        }

    monkeypatch.setattr(runner, "_run_classical_covering_escalation", fake_covering)

    common = {
        "db": object(),
        "run": {"id": 7, "target_mode": "curve"},
        "run_config": {
            "target_rank": 10,
            "certificate_timeout": 120,
            "exact_candidates": 64,
        },
        "target": {},
        "context": {"curve_id": 11, "E": object(), "parameter": "curve:11"},
        "stage_index": 3,
    }
    result, terminal, stop = runner._stage_result(
        "simon_covering",
        config={"timeout": 5, "lim1": 2, "lim3": 10},
        **common,
    )
    assert captured[-1]["policy"]["classical_covering_limbigprime"] == 0
    assert result["status"] == "completed"
    assert terminal is None
    assert stop is False

    runner._stage_result(
        "simon_covering",
        config={
            "timeout": 5,
            "lim1": 2,
            "lim3": 10,
            "limbigprime": 30,
        },
        **common,
    )
    assert captured[-1]["policy"]["classical_covering_limbigprime"] == 30



def test_mwrank_covering_resume_policy_is_explicit():
    prior = {
        "status": "timeout",
        "attempt_number": 1,
        "attempt_timeout": 90,
        "retry_policy": "manual",
    }
    skip, cfg, reason = runner._resume_stage_policy(
        "mwrank_covering",
        prior,
        {
            "timeout": 90,
            "retry_policy": "manual",
            "retry_timeout": 300,
        },
    )
    assert skip is True
    assert reason == "manual"

    prior["retry_policy"] = "automatic"
    skip, cfg, reason = runner._resume_stage_policy(
        "mwrank_covering",
        prior,
        {
            "timeout": 90,
            "retry_policy": "automatic",
            "retry_timeout": 300,
        },
    )
    assert skip is False
    assert reason == "automatic"
    assert cfg["timeout"] == 90
    assert cfg["_attempt_number"] == 2

    prior["retry_policy"] = "escalated"
    skip, cfg, reason = runner._resume_stage_policy(
        "mwrank_covering",
        prior,
        {
            "timeout": 90,
            "retry_policy": "escalated",
            "retry_timeout": 300,
        },
    )
    assert skip is False
    assert reason == "escalated"
    assert cfg["timeout"] == 300
    assert cfg["_attempt_number"] == 2

    exhausted = dict(prior, attempt_timeout=300)
    skip, cfg, reason = runner._resume_stage_policy(
        "mwrank_covering",
        exhausted,
        {
            "timeout": 90,
            "retry_policy": "escalated",
            "retry_timeout": 300,
        },
    )
    assert skip is True
    assert reason == "escalated_budget_already_attempted"


def test_mwrank_covering_stage_wires_durable_attempt_identity(monkeypatch):
    captured = {}

    def fake_covering(db, **kwargs):
        captured.update(kwargs)
        return {
            "status": "timeout",
            "engine": "mwrank_coverings",
            "attempt_identity": kwargs["attempt_identity"],
            "attempt_number": kwargs["attempt_number"],
            "attempt_timeout": 90,
            "retry_policy": kwargs["retry_policy"],
            "retry_timeout": kwargs["retry_timeout"],
            "exact_points": 0,
            "rank_growth": 0,
        }

    monkeypatch.setattr(runner, "_run_classical_covering_escalation", fake_covering)

    result, terminal, stop = runner._stage_result(
        "mwrank_covering",
        db=object(),
        run={"id": 17, "target_mode": "curve"},
        run_config={
            "certificate_timeout": 120,
            "exact_candidates": 64,
        },
        target={},
        context={
            "curve_id": 9,
            "E": object(),
            "parameter": "curve:9",
            "pipeline_candidate_id": 44,
        },
        config={
            "timeout": 90,
            "retry_policy": "automatic",
            "retry_timeout": 300,
            "_attempt_number": 2,
        },
        stage_index=6,
    )

    assert captured["attempt_identity"] == (
        "pipeline:17:candidate:44:stage:6:"
        "mwrank_covering:mwrank_coverings"
    )
    assert captured["attempt_number"] == 2
    assert captured["retry_policy"] == "automatic"
    assert captured["retry_timeout"] == 300
    assert result["status"] == "timeout"
    assert result["attempt_identity"] == captured["attempt_identity"]
    assert terminal is None
    assert stop is False



def test_higher_descent_resume_policy_is_explicit_for_partial_and_unsupported():
    partial = {
        "status": "partial",
        "attempt_number": 1,
        "attempt_timeout": 120,
        "retry_policy": "manual",
    }
    skip, cfg, reason = runner._resume_stage_policy(
        "higher_descent_ladder",
        partial,
        {
            "timeout": 120,
            "retry_policy": "manual",
            "retry_timeout": 300,
        },
    )
    assert skip is True
    assert cfg["timeout"] == 120
    assert reason == "manual"

    partial["retry_policy"] = "automatic"
    skip, cfg, reason = runner._resume_stage_policy(
        "higher_descent_ladder",
        partial,
        {
            "timeout": 120,
            "retry_policy": "automatic",
            "retry_timeout": 300,
        },
    )
    assert skip is False
    assert cfg["timeout"] == 120
    assert cfg["_attempt_number"] == 2
    assert reason == "automatic"

    unsupported = {
        "status": "unsupported",
        "attempt_number": 1,
        "attempt_timeout": 120,
        "retry_policy": "manual",
    }
    skip, cfg, reason = runner._resume_stage_policy(
        "higher_descent_ladder",
        unsupported,
        {
            "timeout": 120,
            "retry_policy": "manual",
            "retry_timeout": 300,
        },
    )
    assert skip is True
    assert reason == "manual"



def test_covering_fanout_resume_policy_is_explicit():
    prior = {
        "status": "partial",
        "attempt_number": 1,
        "attempt_timeout": 30,
        "retry_policy": "manual",
    }
    skip, cfg, reason = runner._resume_stage_policy(
        "selmer_element_fanout",
        prior,
        {
            "timeout": 30,
            "retry_policy": "manual",
            "retry_timeout": 120,
        },
    )
    assert skip is True
    assert cfg["timeout"] == 30
    assert reason == "manual"

    prior["retry_policy"] = "automatic"
    skip, cfg, reason = runner._resume_stage_policy(
        "selmer_element_fanout",
        prior,
        {
            "timeout": 30,
            "retry_policy": "automatic",
            "retry_timeout": 120,
        },
    )
    assert skip is False
    assert cfg["timeout"] == 30
    assert cfg["_attempt_number"] == 2
    assert reason == "automatic"

    prior["retry_policy"] = "escalated"
    skip, cfg, reason = runner._resume_stage_policy(
        "selmer_element_fanout",
        prior,
        {
            "timeout": 30,
            "retry_policy": "escalated",
            "retry_timeout": 120,
        },
    )
    assert skip is False
    assert cfg["timeout"] == 120
    assert cfg["_attempt_number"] == 2
    assert reason == "escalated"

    exhausted = dict(prior, attempt_timeout=120)
    skip, cfg, reason = runner._resume_stage_policy(
        "selmer_element_fanout",
        exhausted,
        {
            "timeout": 30,
            "retry_policy": "escalated",
            "retry_timeout": 120,
        },
    )
    assert skip is True
    assert reason == "escalated_budget_already_attempted"


    derivation_timeout = {
        "status": "timeout",
        "reason": "covering_derivation_timeout",
        "attempt_number": 1,
        "attempt_timeout": 45,
        "retry_policy": "escalated",
    }
    skip, cfg, reason = runner._resume_stage_policy(
        "selmer_element_fanout",
        derivation_timeout,
        {
            "timeout": 30,
            "derive_timeout": 45,
            "retry_policy": "escalated",
            "retry_timeout": 180,
        },
    )
    assert skip is False
    assert cfg["timeout"] == 30
    assert cfg["derive_timeout"] == 180
    assert cfg["_attempt_number"] == 2
    assert reason == "escalated"



def test_saturation_ladder_resume_policy_is_explicit():
    prior = {
        "status": "timeout",
        "attempt_number": 1,
        "attempt_timeout": 180,
        "retry_policy": "manual",
    }
    config = {
        "timeout": 180,
        "retry_policy": "manual",
        "retry_timeout": 600,
    }
    skip, cfg, reason = runner._resume_stage_policy(
        "full_saturation_index_recovery",
        prior,
        config,
    )
    assert skip is True
    assert reason == "manual"

    prior["retry_policy"] = "automatic"
    skip, cfg, reason = runner._resume_stage_policy(
        "full_saturation_index_recovery",
        prior,
        {**config, "retry_policy": "automatic"},
    )
    assert skip is False
    assert cfg["_attempt_number"] == 2
    assert cfg["timeout"] == 180
    assert reason == "automatic"

    prior["retry_policy"] = "escalated"
    skip, cfg, reason = runner._resume_stage_policy(
        "full_saturation_index_recovery",
        prior,
        {**config, "retry_policy": "escalated"},
    )
    assert skip is False
    assert cfg["_attempt_number"] == 2
    assert cfg["timeout"] == 600
    assert reason == "escalated"


def test_height_lattice_resume_policy_is_explicit():
    prior = {
        "status": "timeout",
        "attempt_number": 1,
        "attempt_timeout": 300,
        "retry_policy": "manual",
    }
    config = {
        "timeout": 300,
        "retry_policy": "manual",
        "retry_timeout": 900,
    }
    skip, cfg, reason = runner._resume_stage_policy(
        "height_lattice_reduction",
        prior,
        config,
    )
    assert skip is True
    assert reason == "manual"

    prior["retry_policy"] = "automatic"
    skip, cfg, reason = runner._resume_stage_policy(
        "height_lattice_reduction",
        prior,
        {**config, "retry_policy": "automatic"},
    )
    assert skip is False
    assert cfg["timeout"] == 300
    assert cfg["_attempt_number"] == 2
    assert reason == "automatic"

    prior["retry_policy"] = "escalated"
    skip, cfg, reason = runner._resume_stage_policy(
        "height_lattice_reduction",
        prior,
        {**config, "retry_policy": "escalated"},
    )
    assert skip is False
    assert cfg["timeout"] == 900
    assert cfg["_attempt_number"] == 2
    assert reason == "escalated"


def test_height_lattice_certificate_partial_preserves_completed_lattice():
    prior = {
        "status": "partial",
        "lattice_status": "completed",
        "certification_status": "timeout",
        "retry_policy": "escalated",
        "attempt_timeout": 300,
    }
    skip, cfg, reason = runner._resume_stage_policy(
        "height_lattice_reduction",
        prior,
        {
            "timeout": 300,
            "retry_policy": "escalated",
            "retry_timeout": 900,
        },
    )
    assert skip is True
    assert cfg["timeout"] == 300
    assert reason == "certificate_retry_requires_explicit_rerun"


def test_plugin_geometry_resume_policy_is_explicit():
    prior = {
        "status": "timeout",
        "attempt_number": 1,
        "attempt_timeout": 180,
        "retry_policy": "manual",
    }
    skip, cfg, reason = runner._resume_stage_policy(
        "plugin_geometry",
        prior,
        {
            "timeout": 180,
            "retry_policy": "manual",
            "retry_timeout": 600,
        },
    )
    assert skip is True
    assert cfg["timeout"] == 180
    assert reason == "manual"

    prior["retry_policy"] = "automatic"
    skip, cfg, reason = runner._resume_stage_policy(
        "plugin_geometry",
        prior,
        {
            "timeout": 180,
            "retry_policy": "automatic",
            "retry_timeout": 600,
        },
    )
    assert skip is False
    assert cfg["timeout"] == 180
    assert cfg["_attempt_number"] == 2
    assert reason == "automatic"

    prior["retry_policy"] = "escalated"
    skip, cfg, reason = runner._resume_stage_policy(
        "plugin_geometry",
        prior,
        {
            "timeout": 180,
            "retry_policy": "escalated",
            "retry_timeout": 600,
        },
    )
    assert skip is False
    assert cfg["timeout"] == 600
    assert cfg["_attempt_number"] == 2
    assert reason == "escalated"

    exhausted = dict(prior, attempt_timeout=600)
    skip, cfg, reason = runner._resume_stage_policy(
        "plugin_geometry",
        exhausted,
        {
            "timeout": 180,
            "retry_policy": "escalated",
            "retry_timeout": 600,
        },
    )
    assert skip is True
    assert reason == "escalated_budget_already_attempted"


def test_plugin_geometry_stage_wires_retry_attempt(monkeypatch):
    captured = {}

    def fake_geometry(args, db, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        return {
            "status": "partial",
            "attempt_number": args.geometry_attempt_number,
            "attempt_timeout": args.geometry_timeout,
            "retry_policy": args.geometry_retry_policy,
            "retry_timeout": args.geometry_retry_timeout,
            "rank_growth": 0,
        }

    monkeypatch.setattr(runner, "_run_plugin_geometry_target", fake_geometry)
    result, terminal, stop = runner._stage_result(
        "plugin_geometry",
        db=object(),
        run={"id": 41, "target_mode": "curve"},
        run_config={
            "db_path": "/tmp/rank42.db",
            "project_root": "/tmp/project",
            "certificate_timeout": 120,
            "exact_candidates": 64,
        },
        target={},
        context={
            "curve_id": 7,
            "E": object(),
            "parameter": "curve:7",
            "plugin": SimpleNamespace(id="demo"),
            "variant": SimpleNamespace(id="v1"),
            "rank_order": 1,
        },
        config={
            "timeout": 600,
            "retry_policy": "escalated",
            "retry_timeout": 600,
            "_attempt_number": 2,
        },
        stage_index=9,
    )
    assert captured["args"].geometry_timeout == 600
    assert captured["args"].geometry_attempt_number == 2
    assert captured["args"].geometry_retry_policy == "escalated"
    assert captured["args"].geometry_retry_timeout == 600
    assert result["status"] == "partial"
    assert terminal is None
    assert stop is False



def test_pointed_quartic_stage_preserves_partial_coverage_status(monkeypatch):
    captured = {}

    def fake_pointed(db, **kwargs):
        captured.update(kwargs)
        return {
            "status": "partial",
            "stop_reason": "incomplete_round",
            "planned_searches": 4,
            "completed_searches": 2,
            "timeouts": 2,
            "coverage_fraction": 0.5,
            "dry_completed": False,
            "rank_growth": 0,
            "rigorous_lower": 3,
            "rounds": [{
                "round": 1,
                "status": "partial",
                "planned_searches": 4,
                "completed_searches": 2,
                "timeouts": 2,
                "coverage_fraction": 0.5,
                "dry_completed": False,
                "new_exact_points": 0,
            }],
        }

    monkeypatch.setattr(runner, "run_pointed_quartic_escalation", fake_pointed)
    result, terminal, stop = runner._stage_result(
        "pointed_quartic",
        db=object(),
        run={"id": 81, "target_mode": "curve", "stages": []},
        run_config={
            "target_rank": 10,
            "ratpoints": "/tmp/ratpoints",
            "certificate_timeout": 120,
            "exact_candidates": 64,
        },
        target={},
        context={
            "curve_id": 7,
            "E": object(),
            "parameter": "curve:7",
        },
        config={
            "heights": [100, 1000],
            "anchors": 4,
            "pool_size": 8,
            "rounds": 2,
            "deep_keep": 2,
            "timeout": 3,
            "reduce_timeout": 4,
            "dry_round_min_coverage": 0.9,
        },
        stage_index=6,
    )
    assert captured["dry_round_min_coverage"] == 0.9
    assert result["status"] == "partial"
    assert result["dry_completed"] is False
    assert result["coverage_fraction"] == 0.5
    assert terminal is None
    assert stop is False



def test_pointed_quartic_incomplete_result_is_rerun_on_resume():
    for status in ("partial", "timeout", "error", "inconclusive"):
        skip, cfg, reason = runner._resume_stage_policy(
            "pointed_quartic",
            {"status": status},
            {
                "timeout": 6,
                "dry_round_min_coverage": 1.0,
            },
        )
        assert skip is False
        assert cfg["timeout"] == 6
        assert reason is None



def test_mw_growth_loop_forwards_underlying_stop_reason(monkeypatch):
    cases = [
        ("dry_completed_round", "stopped_on_dry_round"),
        ("timeout_budget_exhausted", "stopped_on_timeout_budget"),
        ("engine_errors", "stopped_on_engine_errors"),
        ("rank_goal", "stopped_on_rank_goal"),
        ("no_new_models", "stopped_on_no_new_models"),
        ("max_rounds", "stopped_on_max_rounds"),
        ("incomplete_round", "stopped_on_incomplete_round"),
        ("user_stop", "stopped_on_user_stop"),
    ]
    for stop_reason, flag in cases:
        def fake_loop(db, **kwargs):
            return {
                "status": (
                    "completed"
                    if stop_reason in {
                        "dry_completed_round", "rank_goal",
                        "no_new_models", "max_rounds", "user_stop"
                    }
                    else "partial"
                ),
                "stop_reason": stop_reason,
                "exact_points": 0,
                "rank_growth": 0,
                "rigorous_lower": 12 if stop_reason == "rank_goal" else 3,
                "rounds": [{
                    "round": 1,
                    "new_exact_points": 0,
                    "dry_completed": stop_reason == "dry_completed_round",
                }],
            }

        monkeypatch.setattr(
            runner, "run_pointed_quartic_escalation", fake_loop
        )
        result, terminal, stop = runner._stage_result(
            "mw_growth_loop",
            db=object(),
            run={"id": 91, "target_mode": "curve", "stages": []},
            run_config={
                "target_rank": 12,
                "ratpoints": "/tmp/ratpoints",
                "certificate_timeout": 120,
                "exact_candidates": 64,
            },
            target={},
            context={
                "curve_id": 7,
                "E": object(),
                "parameter": "curve:7",
            },
            config={
                "heights": [100],
                "anchors": 4,
                "pool_size": 8,
                "max_rounds": 2,
                "deep_keep": 2,
                "timeout": 3,
                "reduce_timeout": 4,
                "dry_round_min_coverage": 1.0,
                "certificate_timeout": 120,
                "exact_candidates": 64,
            },
            stage_index=7,
        )
        assert result["feedback_stop_reason"] == stop_reason
        assert result[flag] is True
        for other in (
            "stopped_on_dry_round",
            "stopped_on_timeout_budget",
            "stopped_on_engine_errors",
            "stopped_on_rank_goal",
            "stopped_on_no_new_models",
            "stopped_on_max_rounds",
            "stopped_on_incomplete_round",
            "stopped_on_user_stop",
        ):
            assert result[other] is (other == flag)
        assert terminal is None
        assert stop is False


def test_transform_timeout_keeps_parent_when_include_parent_false(monkeypatch):
    db = _db()
    run = _run(db, "Transform timeout")
    db.execute("INSERT INTO curves(id) VALUES(1)")
    upsert_pipeline_candidate(
        db,
        run_id=run["id"],
        provider_key="general",
        parameter="parent-timeout",
        curve_id=1,
        status="running",
    )
    item = {
        "provider_key": "general",
        "candidate": {"parameter": "parent-timeout", "curve_id": 1},
        "plugin": None,
        "variant": None,
        "family": None,
        "fingerprints": {},
    }
    monkeypatch.setattr(
        runner,
        "_materialize_population_context",
        lambda *args, **kwargs: {
            "curve_id": 1,
            "E": object(),
            "source_E": object(),
            "family": None,
            "plugin": None,
            "variant": None,
            "parameter": "parent-timeout",
            "score": None,
            "rank_order": 1,
        },
    )
    monkeypatch.setattr(
        runner,
        "_snapshot",
        lambda db, curve_id: {
            "rigorous_lower": 0,
            "rigorous_upper": None,
            "exact_rank": None,
        },
    )
    monkeypatch.setattr(
        runner,
        "derive_transform_children",
        lambda stage_id, db, context, config: {
            "status": "timeout",
            "reason": "fixture hard timeout",
            "transform_kind": "rank_jump_base_change",
            "children": [],
            "hard_isolated": True,
            "timeout_seconds": 1,
        },
    )

    out = runner._transform_population(
        db,
        run=run,
        run_config={"project_root": "."},
        active_items=[item],
        stage_index=4,
        stage_id="rank_jump_base_change",
        config={"max_children": 8, "include_parent": False},
        cache={},
    )

    assert [rec["candidate"]["parameter"] for rec in out] == [
        "parent-timeout"
    ]
    parent = get_pipeline_candidate(
        db, run["id"], "general", "parent-timeout"
    )
    assert parent["status"] == "running"
    stage = json.loads(parent["stage_results_json"])["4"]["result"]
    assert stage["status"] == "timeout"
    assert stage["include_parent"] is True
    assert stage["hard_isolated"] is True



def test_upper_bound_rescue_ignores_unpromoted_higher_descent_upper(monkeypatch):
    state = {"upper": None}
    seen = []

    monkeypatch.setattr(
        runner,
        "_snapshot",
        lambda db, curve_id: {
            "rigorous_lower": 18,
            "rigorous_upper": state["upper"],
            "exact_rank": None,
        },
    )
    monkeypatch.setattr(runner, "get_curve", lambda db, curve_id: object())
    monkeypatch.setattr(runner, "proven_lower", lambda row: 18)
    monkeypatch.setattr(
        runner,
        "rigorous_witness_basis",
        lambda db, curve_id, E: ([], 0, True),
    )

    def fake_stage_result(stage_id, **kwargs):
        seen.append(stage_id)
        assert stage_id == "higher_descent_ladder"
        assert kwargs["config"]["certificate_timeout"] == 45
        return {
            "status": "completed",
            "rigorous_upper": 18,
            "plugin_reported_upper": 18,
        }, None, False

    monkeypatch.setattr(runner, "_stage_result", fake_stage_result)

    result = runner._run_upper_bound_rescue_ladder(
        object(),
        run={"id": 10},
        run_config={"target_rank": 19},
        target={},
        context={"curve_id": 5, "E": object()},
        config={
            "pari_enabled": False,
            "isogeny_pari_enabled": False,
            "selmer_enabled": False,
            "known_basis_coverings": False,
            "higher_descent_hooks": True,
            "higher_levels": [4],
            "higher_timeout": 7,
            "certificate_timeout": 45,
            "exact_candidates": 16,
        },
        stage_index=13,
    )

    assert seen == ["higher_descent_ladder"]
    assert result["status"] == "inconclusive"
    assert result["rigorous_upper"] is None
    assert result.get("winner") is None



def test_isogenous_pari_rescue_uses_isolated_per_degree_discovery(monkeypatch):
    E = SimpleNamespace(a_invariants=lambda: [0, 0, 0, -1, 0])
    discovery_calls = []

    monkeypatch.setattr(runner, "get_curve", lambda db, curve_id: object())
    monkeypatch.setattr(runner, "proven_lower", lambda row: 0)

    def discover(ainvs, degree, points, timeout):
        discovery_calls.append((degree, list(points), timeout))
        return {
            "status": "timeout",
            "reason": "fixture timeout",
            "children": [],
            "runtime_seconds": 2.0,
            "worker_exit_code": -15,
        }

    monkeypatch.setattr(
        runner, "run_isogeny_degree_discovery", discover
    )
    result = runner._isogenous_pari_upper_rescue(
        object(),
        context={"curve_id": 7, "E": E},
        config={
            "isogeny_degrees": [2, 3],
            "isogeny_discovery_timeout": 9,
            "isogeny_max_models": 4,
            "isogeny_pari_timeout": 2,
        },
    )

    assert discovery_calls == [(2, [], 9), (3, [], 9)]
    assert result["status"] == "inconclusive"
    assert result["models_tested"] == 0
    assert result["isogeny_discovery_hard_isolated"] is True
    assert [x["status"] for x in result["degree_outcomes"]] == [
        "timeout",
        "timeout",
    ]


def test_rescue_upper_uses_shared_interval_promotion(monkeypatch):
    E = SimpleNamespace(a_invariants=lambda: [0, 0, 0, -1, 0])
    captured = {}

    monkeypatch.setattr(runner, "get_curve", lambda db, curve_id: object())
    monkeypatch.setattr(runner, "proven_lower", lambda row: 5)

    def promote(db, **kwargs):
        captured.update(kwargs)
        return {
            "rigorous_upper": 7,
            "rank_inconsistent": False,
        }

    monkeypatch.setattr(runner, "promote_rigorous_rank_interval", promote)
    accepted = runner._persist_rescue_upper(
        object(),
        curve_id=7,
        E=E,
        upper=7,
        engine="pari_isogenous_model",
        source="builder_upper_bound_rescue_ladder",
        options={"isogeny_degree": 3},
        certificate={"upper": 7, "status": "completed"},
        elapsed_seconds=0.2,
    )
    assert accepted == 7
    assert captured["rigorous_upper"] == 7
    assert captured["certificate"]["upper"] == 7
    assert captured["options"]["isogeny_degree"] == 3



def test_large_height_generator_hunt_incomplete_coverage_is_not_exhausted(
    monkeypatch,
):
    monkeypatch.setattr(
        runner,
        "_strategy_research_state",
        lambda db, curve_id, E, witness_basis=False: {
            "rigorous_lower": 5,
            "rigorous_upper": None,
            "exact_rank": None,
            "rank_inconsistent": False,
            "witness_basis_complete": True if witness_basis else None,
            "witness_basis_required": 5 if witness_basis else None,
            "rigorous_witnesses": 5 if witness_basis else None,
            "research_state_source": "curve_research_state",
            
        },
    )
    monkeypatch.setattr(
        runner,
        "_runtime_hunt_capabilities",
        lambda db, context: frozenset({
            "exact_curve",
            "higher_descent_hook",
            "covering_hook",
            "core_point_search",
        }),
    )

    def fake_stage_result(stage_id, **kwargs):
        if stage_id == "higher_descent_ladder":
            return {"status": "timeout"}, None, False
        return {"status": "completed"}, None, False

    monkeypatch.setattr(runner, "_stage_result", fake_stage_result)
    result = runner._run_large_height_generator_hunt(
        object(),
        run={"id": 101},
        run_config={"target_rank": 20},
        target={},
        context={"curve_id": 9, "E": object(), "parameter": "1/2"},
        config={
            "descent_levels": [4, 8],
            "padic_enabled": False,
            "denominator_bands": [[2, 100]],
            "late_saturation_enabled": False,
            "certificate_timeout": 30,
            "exact_candidates": 16,
            "stop_on_growth": True,
        },
        stage_index=5,
    )

    assert result["status"] == "partial"
    assert result["budget_exhausted"] is False
    assert result["exhausted_strategy_budget"] is False
    coverage = result["strategy_coverage"]
    assert coverage["all_steps_completed"] is False
    assert coverage["status_counts"]["timeout"] == 1
    assert coverage["status_counts"]["completed"] >= 1


def test_record_breaker_lane_preserves_nested_incomplete_status(monkeypatch):
    monkeypatch.setattr(
        runner,
        "_strategy_research_state",
        lambda db, curve_id, E, witness_basis=False: {
            "rigorous_lower": 25,
            "rigorous_upper": None,
            "exact_rank": None,
            "rank_inconsistent": False,
            "witness_basis_complete": True if witness_basis else None,
            "witness_basis_required": 25 if witness_basis else None,
            "rigorous_witnesses": 25 if witness_basis else None,
            "research_state_source": "curve_research_state",
            
        },
    )
    monkeypatch.setattr(
        runner,
        "_run_large_height_generator_hunt",
        lambda *args, **kwargs: {
            "status": "partial",
            "rigorous_lower": 25,
            "budget_exhausted": False,
            "strategy_coverage": {
                "status": "partial",
                "all_steps_completed": False,
            },
        },
    )

    result, terminal, stop = runner._stage_result(
        "record_breaker_lane",
        db=object(),
        run={"id": 7, "target_mode": "family"},
        run_config={"target_rank": 31},
        target={},
        context={"curve_id": 11, "E": object(), "parameter": "p"},
        config={"minimum_rank": 25},
        stage_index=9,
    )

    assert result["status"] == "partial"
    assert result["record_breaker_lane"] is True
    assert result["entry_rigorous_lower"] == 25
    assert result["budget_exhausted"] is False
    assert terminal is None
    assert stop is False



def test_large_hunt_resumes_from_first_unfinished_checkpoint(tmp_path, monkeypatch):
    db = runner.connect(tmp_path / "strategy-resume.db")
    runner.ensure_pipeline_schema(db)
    E = runner.EllipticCurve(runner.QQ, [0, 0, 0, -1, 1])
    curve_id = runner.upsert_curve(
        db, family="strategy-resume", parameter="1",
        a_invariants_json='["0","0","0","-1","1"]', status="test",
    )
    run_id = create_pipeline_run(
        db, pipeline_name="strategy resume", target_mode="general",
        target={"pool_mode": "open"},
        stages=[{"id": "large_height_generator_hunt", "config": {}}],
        run_config={"target_rank": 20},
    )
    candidate = upsert_pipeline_candidate(
        db, run_id=run_id, parameter="1", provider_key="general",
        curve_id=curve_id, status="searching",
    )
    run = pipeline_run_payload(db.execute(
        "SELECT * FROM search_pipeline_runs WHERE id=?", (run_id,)
    ).fetchone())
    context = {
        "curve_id": curve_id, "E": E, "parameter": "1",
        "pipeline_candidate_id": int(candidate["id"]),
    }
    monkeypatch.setattr(
        runner,
        "_strategy_research_state",
        lambda db, curve_id, E, witness_basis=False: {
            "rigorous_lower": 5,
            "rigorous_upper": None,
            "exact_rank": None,
            "rank_inconsistent": False,
            "witness_basis_complete": True if witness_basis else None,
            "witness_basis_required": 5 if witness_basis else None,
            "rigorous_witnesses": 5 if witness_basis else None,
            "research_state_source": "curve_research_state",
            
        },
    )
    monkeypatch.setattr(
        runner, "_runtime_hunt_capabilities",
        lambda db, context: frozenset({
            "exact_curve", "higher_descent_hook", "core_point_search"
        }),
    )
    calls = []
    def first(stage_id, **kwargs):
        calls.append(stage_id)
        if stage_id == "mw_growth_loop":
            return {"status": "timeout"}, None, False
        return {"status": "completed"}, None, False
    monkeypatch.setattr(runner, "_stage_result", first)
    cfg = {
        "descent_levels": [4], "padic_enabled": False,
        "denominator_bands": [[2, 10]], "late_saturation_enabled": False,
        "wall_timeout": 120, "retry_policy": "automatic",
        "retry_timeout": 240, "stop_on_growth": False,
    }
    first_result = runner._run_large_height_generator_hunt(
        db, run=run, run_config={"target_rank": 20}, target={},
        context=context, config=cfg, stage_index=1,
    )
    assert first_result["strategy_resume_token"] == "mw_growth_loop"
    calls.clear()
    def second_stage(stage_id, **kwargs):
        calls.append(stage_id)
        return {"status": "completed"}, None, False
    monkeypatch.setattr(runner, "_stage_result", second_stage)
    second = runner._run_large_height_generator_hunt(
        db, run=run, run_config={"target_rank": 20}, target={},
        context=context, config=cfg, stage_index=1,
    )
    assert "higher_descent_ladder" not in calls
    assert calls[0] == "mw_growth_loop"
    assert second["strategy_resume_token"] is None
    rows = pipeline_strategy_steps(
        db, run_id, candidate_id=candidate["id"], stage_index=1,
        occurrence_id=second["strategy_occurrence_id"],
    )
    assert any(row["step_key"] == "higher_descent_ladder" and row["status"] == "completed" for row in rows)
    assert any(row["step_key"] == "mw_growth_loop" and row["status"] == "completed" for row in rows)



def test_large_hunt_record_goal_metadata_requires_witness_basis(monkeypatch):
    monkeypatch.setattr(
        runner,
        "_strategy_research_state",
        lambda db, curve_id, E, witness_basis=False: {
            "rigorous_lower": 31,
            "rigorous_upper": 35,
            "exact_rank": None,
            "rank_inconsistent": False,
            "witness_basis_complete": False if witness_basis else None,
            "witness_basis_required": 31 if witness_basis else None,
            "rigorous_witnesses": 30 if witness_basis else None,
            "research_state_source": "curve_research_state",
        },
    )
    monkeypatch.setattr(
        runner,
        "_runtime_hunt_capabilities",
        lambda db, context: frozenset({"exact_curve"}),
    )
    result = runner._run_large_height_generator_hunt(
        object(),
        run={"id": 1},
        run_config={"target_rank": 31, "record_breaker_mode": True},
        target={},
        context={"curve_id": 1, "E": object(), "parameter": "x"},
        config={
            "descent_levels": [4],
            "padic_enabled": False,
            "denominator_bands": [],
            "late_saturation_enabled": False,
            "stop_on_growth": False,
            "wall_timeout": 60,
        },
        stage_index=1,
    )
    assert result["goal_reached"] is False
    assert result["stopped_at_rank_goal"] is False
    assert result["witness_requirement_met"] is False
    assert result["stop_reason"] == "none"


def test_large_hunt_growth_metadata_respects_stop_on_growth(monkeypatch):
    values = iter([5, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6])
    monkeypatch.setattr(
        runner,
        "_strategy_research_state",
        lambda db, curve_id, E, witness_basis=False: {
            "rigorous_lower": next(values, 6),
            "rigorous_upper": None,
            "exact_rank": None,
            "rank_inconsistent": False,
            "witness_basis_complete": None,
            "witness_basis_required": None,
            "rigorous_witnesses": None,
            "research_state_source": "curve_research_state",
        },
    )
    monkeypatch.setattr(
        runner,
        "_runtime_hunt_capabilities",
        lambda db, context: frozenset({"exact_curve"}),
    )
    result = runner._run_large_height_generator_hunt(
        object(),
        run={"id": 2},
        run_config={"target_rank": 20},
        target={},
        context={"curve_id": 2, "E": object(), "parameter": "y"},
        config={
            "descent_levels": [4],
            "padic_enabled": False,
            "denominator_bands": [],
            "late_saturation_enabled": False,
            "stop_on_growth": False,
            "wall_timeout": 60,
        },
        stage_index=1,
    )
    assert result["growth_stop_reached"] is False
    assert result["stopped_after_growth"] is False
    assert result["stop_reason"] == "none"



def test_large_hunt_result_persists_shared_budget_estimate(monkeypatch):
    monkeypatch.setattr(
        runner,
        "_strategy_research_state",
        lambda db, curve_id, E, witness_basis=False: {
            "rigorous_lower": 5,
            "rigorous_upper": None,
            "exact_rank": None,
            "rank_inconsistent": False,
            "witness_basis_complete": True if witness_basis else None,
            "witness_basis_required": 5 if witness_basis else None,
            "rigorous_witnesses": 5 if witness_basis else None,
            "research_state_source": "curve_research_state",
            
        },
    )
    monkeypatch.setattr(
        runner,
        "_runtime_hunt_capabilities",
        lambda db, context: frozenset({"exact_curve"}),
    )
    monkeypatch.setattr(
        runner,
        "_stage_result",
        lambda stage_id, **kwargs: (
            {"status": "completed"}, None, False
        ),
    )
    result = runner._run_large_height_generator_hunt(
        object(),
        run={"id": 7},
        run_config={"target_rank": 20},
        target={},
        context={"curve_id": 7, "E": object(), "parameter": "budget"},
        config={
            "descent_levels": [4],
            "padic_enabled": False,
            "mw_growth_rounds": 1,
            "mw_growth_anchors": 2,
            "mw_growth_deep_keep": 1,
            "mw_growth_heights": [100, 1000],
            "mw_growth_timeout": 3,
            "mw_growth_reduce_timeout": 4,
            "denominator_bands": [[101, 1000]],
            "denominator_charts": 5,
            "denominator_heights": [100, 1000],
            "denominator_timeout": 7,
            "late_saturation_enabled": False,
            "wall_timeout": 60,
            "retry_timeout": 120,
            "stop_on_growth": False,
        },
        stage_index=3,
    )
    estimate = result["strategy_budget_estimate"]
    assert estimate["denominator_configured_slots"] == 10
    assert estimate["denominator_searches"] == 5
    assert estimate["denominator_configured_seconds"] == 70
    assert estimate["denominator_seconds"] == 35
    assert estimate["wall_timeout_seconds"] == 60
    assert result["point_checkpoint_scope"] == "chart_height"



def test_record_breaker_entry_uses_reduced_state_when_compat_lower_lags(monkeypatch):
    monkeypatch.setattr(runner, "get_curve", lambda db, curve_id: object())
    monkeypatch.setattr(runner, "proven_lower", lambda row: 1)
    monkeypatch.setattr(
        runner,
        "_strategy_research_state",
        lambda db, curve_id, E, witness_basis=False: {
            "rigorous_lower": 25,
            "rigorous_upper": 32,
            "exact_rank": None,
            "rank_inconsistent": False,
            "witness_basis_complete": True if witness_basis else None,
            "witness_basis_required": 25 if witness_basis else None,
            "rigorous_witnesses": 25 if witness_basis else None,
            "research_state_source": "curve_research_state",
        },
    )
    monkeypatch.setattr(
        runner,
        "_run_large_height_generator_hunt",
        lambda *args, **kwargs: {
            "status": "completed",
            "rigorous_lower": 25,
        },
    )
    result, terminal, stop = runner._stage_result(
        "record_breaker_lane",
        db=object(),
        run={"id": 4, "target_mode": "family"},
        run_config={"target_rank": 31},
        target={},
        context={"curve_id": 8, "E": object(), "parameter": "r"},
        config={"minimum_rank": 25},
        stage_index=4,
    )
    assert result["status"] == "completed"
    assert result["entry_rigorous_lower"] == 25
    assert result["entry_rigorous_upper"] == 32
    assert result["entry_witness_basis_complete"] is True
    assert result["research_state_source"] == "curve_research_state"
    assert terminal is None
    assert stop is False


def test_strategy_research_state_reads_reducer_and_exact_witness_basis(monkeypatch):
    monkeypatch.setattr(
        runner,
        "get_curve_research_state",
        lambda db, curve_id: {
            "rigorous_lower": 7,
            "rigorous_upper": 9,
            "exact_rank": None,
            "rank_inconsistent": False,
        },
    )
    monkeypatch.setattr(
        runner,
        "rigorous_witness_basis",
        lambda db, curve_id, E: ([1, 2, 3, 4, 5, 6, 7], 7, True),
    )
    state = runner._strategy_research_state(
        object(), 9, object(), witness_basis=True
    )
    assert state["rigorous_lower"] == 7
    assert state["rigorous_upper"] == 9
    assert state["exact_rank"] is None
    assert state["witness_basis_complete"] is True
    assert state["witness_basis_required"] == 7
    assert state["rigorous_witnesses"] == 7
    assert state["research_state_source"] == "curve_research_state"


def test_pipeline_pari_upper_uses_shared_search_science_not_legacy_auto():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "rank42" / "pipeline_runner.py").read_text(encoding="utf-8")
    auto_source = (root / "rank42" / "auto_search.py").read_text(encoding="utf-8")

    assert "from rank42.search_science import try_pari_upper as _try_pari_upper" in source
    assert "from rank42.auto_search import _try_pari_upper" not in source
    assert "_try_pari_upper," not in source
    assert "try_pari_upper as _try_pari_upper" in auto_source


def test_shared_family_materialization_preserves_identity_and_plugin_provenance(tmp_path):
    from rank42.search_materialization import materialize_family_curve

    db = connect_core(tmp_path / "materialize-family.db")

    class Family:
        @staticmethod
        def name():
            return "Materialize Family"

        @staticmethod
        def curve(t):
            return runner.EllipticCurve(runner.QQ, [0, 0, 0, -1, t])

    plugin = SimpleNamespace(id="family-demo", version="1.2.3")
    variant = SimpleNamespace(family_spec="demo.family:Family")
    fingerprints = {
        "plugin_manifest_sha256": "manifest-v1",
        "family_sha256": "family-v1",
        "adapter_sha256": "adapter-v1",
    }

    curve_id, E, source_E, created = materialize_family_curve(
        db,
        plugin=plugin,
        variant=variant,
        family=Family(),
        parameter="1",
        score=8.5,
        fingerprints=fingerprints,
    )

    assert created is True
    assert curve_id > 0
    assert E.a_invariants() == source_E.a_invariants()

    stored = db.execute("SELECT * FROM curves WHERE id=?", (curve_id,)).fetchone()
    assert stored["plugin_id"] == "family-demo"
    assert stored["plugin_version"] == "1.2.3"
    assert stored["family_spec"] == "demo.family:Family"
    assert stored["status"] == "auto_searching"

    plugin2 = SimpleNamespace(id="family-demo", version="1.2.4")
    second_id, second_E, second_source, second_created = materialize_family_curve(
        db,
        plugin=plugin2,
        variant=variant,
        family=Family(),
        parameter="1",
        score=9.25,
        fingerprints={
            "plugin_manifest_sha256": "manifest-v2",
            "family_sha256": "family-v1",
            "adapter_sha256": "adapter-v2",
        },
    )

    assert second_id == curve_id
    assert second_created is False
    assert second_E.a_invariants() == E.a_invariants()
    assert second_source.a_invariants() == source_E.a_invariants()
    refreshed = db.execute("SELECT * FROM curves WHERE id=?", (curve_id,)).fetchone()
    assert refreshed["plugin_version"] == "1.2.4"
    assert float(refreshed["score"]) == pytest.approx(9.25)


def test_shared_transient_discard_preserves_historical_rows():
    from rank42.search_materialization import discard_unpromoted_search_curve

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE curves(id INTEGER PRIMARY KEY)")
    db.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, curve_id INTEGER)")
    db.execute("CREATE TABLE quartic_searches(id INTEGER PRIMARY KEY, curve_id INTEGER)")
    db.execute(
        "CREATE TABLE reverse_engineering_reports(id INTEGER PRIMARY KEY, curve_id INTEGER)"
    )
    db.execute("INSERT INTO curves(id) VALUES(17)")
    db.execute("INSERT INTO events(id,curve_id) VALUES(1,17)")
    db.execute("INSERT INTO quartic_searches(id,curve_id) VALUES(2,17)")
    db.execute("INSERT INTO reverse_engineering_reports(id,curve_id) VALUES(3,17)")
    db.commit()

    discard_unpromoted_search_curve(db, 17)

    assert db.execute("SELECT 1 FROM curves WHERE id=17").fetchone() is None
    assert db.execute("SELECT curve_id FROM events WHERE id=1").fetchone()[0] is None
    assert db.execute("SELECT curve_id FROM quartic_searches WHERE id=2").fetchone()[0] is None
    assert (
        db.execute(
            "SELECT curve_id FROM reverse_engineering_reports WHERE id=3"
        ).fetchone()[0]
        is None
    )


def test_pipeline_shared_search_science_dependencies_bypass_legacy_auto():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    pipeline = (root / "rank42" / "pipeline_runner.py").read_text(encoding="utf-8")
    auto_source = (root / "rank42" / "auto_search.py").read_text(encoding="utf-8")

    assert "from rank42.search_materialization import (" in pipeline
    assert "from rank42.search_family_baseline import (" in pipeline
    assert "from rank42.search_classical_covering import (" in pipeline
    assert "from rank42.search_plugin_geometry import (" in pipeline
    assert "from rank42.auto_search import" not in pipeline

    assert "materialize_family_curve as _stored_curve" in auto_source
    assert "discard_unpromoted_search_curve as _discard_unpromoted_auto_curve" in auto_source
    assert "attach_family_baseline as _attach_family_baseline" in auto_source
    assert (
        "attach_family_specialization_seed as _attach_family_specialization_seed"
        in auto_source
    )
    assert (
        "run_classical_covering_escalation as _run_classical_covering_escalation"
        in auto_source
    )
    assert "run_plugin_geometry_target as _run_plugin_geometry_target" in auto_source


def test_shared_classical_covering_timeout_preserves_attempt_contract(monkeypatch):
    import rank42.search_classical_covering as covering

    monkeypatch.setattr(
        covering,
        "classical_covering_policy",
        lambda policy: {
            "engine": "mwrank_coverings",
            "timeout": 12,
            "first_limit": 20,
            "second_limit": 10,
            "n_aux": 0,
            "lim1": 2,
            "lim3": 10,
            "limbigprime": 0,
        },
    )
    monkeypatch.setattr(
        covering,
        "rigorous_witness_basis",
        lambda db, curve_id, E: (["P1"], 1, True),
    )
    monkeypatch.setattr(
        covering,
        "research_rank_state",
        lambda db, curve_id: {
            "rigorous_lower": 4,
            "specialization_rigorous_lower": 4,
            "rigorous_upper": None,
            "exact_rank": None,
            "rank_inconsistent": False,
        },
    )
    monkeypatch.setattr(
        covering,
        "run_classical_descent",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            covering.ClassicalDescentTimeout("fixture timeout")
        ),
    )

    result = covering.run_classical_covering_escalation(
        object(),
        curve_id=17,
        E=SimpleNamespace(a_invariants=lambda: [0, 0, 0, -1, 0]),
        policy={},
        certificate_timeout=60,
        exact_candidates=16,
        attempt_identity="pipeline:1:candidate:2:stage:3:mwrank_covering",
        attempt_number=2,
        retry_policy="automatic",
        retry_timeout=300,
    )

    assert result["status"] == "timeout"
    assert result["attempt_identity"] == "pipeline:1:candidate:2:stage:3:mwrank_covering"
    assert result["attempt_number"] == 2
    assert result["attempt_timeout"] == 12
    assert result["retry_policy"] == "automatic"
    assert result["retry_timeout"] == 300
    assert result["mathematical_outcome"] == "timeout"


def test_shared_classical_covering_keeps_lower_bound_out_of_descent_claim(monkeypatch):
    import rank42.search_classical_covering as covering

    monkeypatch.setattr(
        covering,
        "classical_covering_policy",
        lambda policy: {
            "engine": "simon_known",
            "timeout": 5,
            "first_limit": 20,
            "second_limit": 10,
            "n_aux": 0,
            "lim1": 2,
            "lim3": 10,
            "limbigprime": 0,
        },
    )
    monkeypatch.setattr(
        covering,
        "rigorous_witness_basis",
        lambda db, curve_id, E: ([], 0, True),
    )
    states = iter([
        {
            "rigorous_lower": 4,
            "specialization_rigorous_lower": 4,
            "rigorous_upper": None,
            "exact_rank": None,
            "rank_inconsistent": False,
        },
        {
            "rigorous_lower": 4,
            "specialization_rigorous_lower": 4,
            "rigorous_upper": 7,
            "exact_rank": None,
            "rank_inconsistent": False,
        },
    ])
    monkeypatch.setattr(covering, "research_rank_state", lambda db, curve_id: next(states))
    descent_result = {
        "rigorous_upper": 7,
        "reported_upper": 7,
        "descent_lower": 99,
        "upper_bound_rigorous": True,
        "upper_bound_scope": "fixture",
        "points": [],
        "runtime_seconds": 0.01,
    }
    monkeypatch.setattr(
        covering,
        "run_classical_descent",
        lambda *args, **kwargs: dict(descent_result),
    )
    promoted = {}

    def fake_promote(db, **kwargs):
        promoted.update(kwargs)
        return {
            "rigorous_lower": 4,
            "rigorous_upper": 7,
            "exact_rank": None,
            "rank_inconsistent": False,
            "evidence_id": 88,
        }

    monkeypatch.setattr(covering, "promote_rigorous_rank_interval", fake_promote)
    monkeypatch.setattr(
        covering,
        "certify_ledger_growth",
        lambda *args, **kwargs: {"attempts": 0, "basis_complete": True},
    )

    result = covering.run_classical_covering_escalation(
        object(),
        curve_id=18,
        E=SimpleNamespace(a_invariants=lambda: [0, 0, 1, -1, 0]),
        policy={},
        certificate_timeout=60,
        exact_candidates=16,
    )

    assert promoted["rigorous_lower"] is None
    assert promoted["rigorous_upper"] == 7
    assert promoted["source"] == "auto_search_classical_covering"
    assert result["descent_lower_reported"] == 99
    assert result["rank_growth"] == 0
    assert result["rigorous_upper"] == 7
    assert result["interval_evidence_id"] == 88


def test_plugin_geometry_shared_host_keeps_sandbox_contract_source_boundary():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "rank42" / "search_plugin_geometry.py").read_text(encoding="utf-8")

    assert "plugin_geometry_sandbox" in source
    assert "force_command_database" in source
    assert "load_plugin_geometry_result" in source
    assert "legacy_sandbox_geometry_result" in source
    assert "apply_plugin_geometry_result" in source
    assert '"sandbox_core_validated_artifacts_only"' in source
    assert '"temporary_sandbox_only"' in source
