import json
import sqlite3

from rank42.pipeline_state import (
    PIPELINE_SCHEMA,
    SCHEMA_VERSION,
    create_pipeline_run,
    ensure_pipeline_schema,
    get_pipeline_candidate,
    get_pipeline_run,
    get_pipeline,
    pipeline_derivations,
    pipeline_point_attempts,
    pipeline_payload,
    pipeline_run_payload,
    pipeline_run_runtime_drift,
    list_pipeline_runs_readonly,
    pipeline_strategy_steps,
    pipeline_runtime_identity,
    record_pipeline_derivation,
    run_progress,
    save_pipeline,
    upsert_pipeline_candidate,
    upsert_pipeline_point_attempt,
    upsert_pipeline_strategy_step,
)


def _db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("CREATE TABLE curves(id INTEGER PRIMARY KEY)")
    ensure_pipeline_schema(db)
    return db


def test_save_pipeline_and_create_snapshot_run():
    db = _db()
    stages = [
        {"id": "nagao_screen", "config": {"prime_bound": 523, "keep": 5000}},
        {"id": "integral_seed", "config": {"heights": [1000], "timeout": 4}},
    ]
    pid = save_pipeline(
        db,
        name="My Hunt",
        target_mode="family",
        stages=stages,
        config={
            "note": "test",
            "target": {"plugin_id": "demo", "variant_id": "default"},
            "target_rank": 32,
            "retention_floor": 18,
            "certificate_timeout": 240,
            "exact_candidates": 160,
            "ratpoints_backend": "GPU",
        },
    )
    payload = pipeline_payload(get_pipeline(db, pid))
    assert payload["name"] == "My Hunt"
    assert payload["stages"] == stages
    assert payload["config"]["target"] == {
        "plugin_id": "demo",
        "variant_id": "default",
    }
    assert payload["recipe_config"]["target_rank"] == 32
    assert payload["recipe_config"]["retention_floor"] == 18
    assert payload["launch_defaults"] == {
        "target": {"plugin_id": "demo", "variant_id": "default"},
    }
    assert payload["config"]["target_rank"] == 32
    assert payload["config"]["retention_floor"] == 18
    assert payload["config"]["certificate_timeout"] == 240
    assert payload["config"]["exact_candidates"] == 160
    assert payload["config"]["ratpoints_backend"] == "GPU"
    assert payload["revision"] == 1
    assert len(payload["content_hash"]) == 64

    rid = create_pipeline_run(
        db,
        pipeline_id=pid,
        pipeline_name="My Hunt",
        target_mode="family",
        target={"plugin_id": "demo", "variant_id": "default"},
        stages=stages,
        run_config={"target_rank": 10},
    )
    row = get_pipeline_run(db, rid)
    assert row["status"] == "queued"
    assert row["pipeline_id"] == pid
    frozen = json.loads(row["run_config_json"])
    assert frozen["source_pipeline_id"] == pid
    assert frozen["source_pipeline_revision"] == 1
    assert frozen["source_pipeline_hash"] == payload["content_hash"]

    runtime = pipeline_runtime_identity()
    run_payload = pipeline_run_payload(row)
    manifest = run_payload["manifest"]
    assert run_payload["pipeline_schema_version"] == runtime["pipeline_schema_version"]
    assert run_payload["pipeline_catalog_version"] == runtime["pipeline_catalog_version"]
    assert run_payload["pipeline_catalog_hash"] == runtime["pipeline_catalog_hash"]
    assert manifest["manifest_version"] == 3
    assert manifest["core_version"] == runtime["core_version"]
    assert manifest["pipeline_schema_version"] == runtime["pipeline_schema_version"]
    assert manifest["pipeline_catalog_hash"] == runtime["pipeline_catalog_hash"]
    assert manifest["source_pipeline_id"] == pid
    assert manifest["source_pipeline_revision"] == 1
    assert manifest["source_pipeline_hash"] == payload["content_hash"]
    assert len(manifest["run_snapshot_hash"]) == 64
    assert pipeline_run_runtime_drift(row)["warnings"] == []


def test_pipeline_payload_separates_new_launch_defaults_from_recipe_config():
    db = _db()
    pid = save_pipeline(
        db,
        name="Reusable curve recipe",
        target_mode="curve",
        stages=[{"id": "integral_seed", "config": {}}],
        config={
            "target_rank": 12,
            "retention_floor": 7,
            "launch_defaults": {
                "target": {"curve_id": 44},
            },
        },
    )
    payload = pipeline_payload(get_pipeline(db, pid))

    assert payload["recipe_config"] == {
        "target_rank": 12,
        "retention_floor": 7,
    }
    assert payload["launch_defaults"] == {
        "target": {"curve_id": 44},
    }
    assert "target" not in payload["recipe_config"]


def test_pipeline_candidate_upsert_and_progress():
    db = _db()
    rid = create_pipeline_run(
        db,
        pipeline_name="General",
        target_mode="general",
        target={"pool_mode": "open"},
        stages=[{"id": "nagao_screen", "config": {}}],
        run_config={"target_rank": 8},
    )
    upsert_pipeline_candidate(
        db,
        run_id=rid,
        provider_key="general",
        parameter="A=1,B=2",
        score=4.5,
        status="completed",
        rigorous_lower=3,
    )
    upsert_pipeline_candidate(
        db,
        run_id=rid,
        provider_key="general",
        parameter="A=2,B=3",
        score=5.5,
        status="filtered",
        rigorous_lower=0,
    )

    one = get_pipeline_candidate(db, rid, "general", "A=1,B=2")
    assert one["status"] == "completed"
    progress = run_progress(db, rid)
    assert progress["total"] == 2
    assert progress["done"] == 2
    assert progress["best_lower"] == 3


def test_pipeline_point_attempt_checkpoint_tracks_retries():
    db = _db()
    rid = create_pipeline_run(
        db,
        pipeline_name="Point checkpoint",
        target_mode="curve",
        target={"curve_id": 1},
        stages=[{"id": "affine_search", "config": {}}],
    )
    candidate = upsert_pipeline_candidate(
        db,
        run_id=rid,
        provider_key="curve",
        parameter="curve:1",
        status="running",
    )

    common = {
        "run_id": rid,
        "candidate_id": int(candidate["id"]),
        "curve_id": None,
        "stage_index": 1,
        "stage_id": "affine_search",
        "tier": "pipeline-affine-test",
        "center": "0",
        "scale": "1",
        "height": 1000,
        "denominator_low": 101,
        "denominator_high": 10000,
        "effective_denominator_low": 101,
        "effective_denominator_high": 1000,
        "timeout_seconds": 8,
        "retry_policy": "automatic",
    }
    first_engine = {
        "backend": "GPU",
        "executable": "/opt/ratpoints_gpu",
        "version": "2.2.2",
        "vendored_revision": "a" * 40,
        "argv": ["/opt/ratpoints_gpu", "1 0 0 1", "1000"],
        "timeout_seconds": 8.0,
        "runtime_seconds": 8.01,
        "status": "timeout",
    }
    second_engine = {
        **first_engine,
        "version": "3.0.0",
        "runtime_seconds": 0.25,
        "status": "completed",
    }
    row = upsert_pipeline_point_attempt(db, status="running", **common)
    assert row["attempt_count"] == 1
    row = upsert_pipeline_point_attempt(
        db,
        status="timeout",
        error="bounded timeout",
        engine=first_engine,
        **common,
    )
    assert row["attempt_count"] == 1
    assert row["status"] == "timeout"

    row = upsert_pipeline_point_attempt(db, status="running", **common)
    assert row["attempt_count"] == 2
    assert json.loads(row["engine_json"])["version"] == "2.2.2"
    row = upsert_pipeline_point_attempt(
        db,
        status="completed",
        exact_points=2,
        runtime=0.25,
        engine=second_engine,
        **common,
    )
    assert row["attempt_count"] == 2
    assert row["exact_points"] == 2

    rows = pipeline_point_attempts(
        db, rid, candidate_id=int(candidate["id"]), stage_index=1
    )
    assert len(rows) == 1
    assert rows[0]["status"] == "completed"
    assert rows[0]["attempt_count"] == 2
    assert rows[0]["denominator_low"] == 101
    assert rows[0]["denominator_high"] == 10000
    assert rows[0]["effective_denominator_low"] == 101
    assert rows[0]["effective_denominator_high"] == 1000
    engine = json.loads(rows[0]["engine_json"])
    assert engine["backend"] == "GPU"
    assert engine["version"] == "3.0.0"
    assert engine["argv"][0] == "/opt/ratpoints_gpu"



def test_pipeline_candidate_persists_score_provenance_across_upsert():
    db = _db()
    rid = create_pipeline_run(
        db,
        pipeline_name="Score provenance",
        target_mode="general",
        target={"pool_mode": "open"},
        stages=[{"id": "nagao_screen", "config": {}}],
    )
    provenance = {
        "algorithm": "short-nagao-v1",
        "prime_bound": 523,
        "primes_used": [5, 7, 11],
        "terms_used": 3,
        "scorer": "short_weierstrass_exact_character_sum",
    }
    upsert_pipeline_candidate(
        db,
        run_id=rid,
        provider_key="general",
        parameter="A=1,B=2",
        score=4.5,
        score_provenance=provenance,
        status="queued",
    )
    row = get_pipeline_candidate(db, rid, "general", "A=1,B=2")
    assert json.loads(row["score_provenance_json"]) == provenance

    # Later orchestration upserts that do not restate provenance must preserve it.
    upsert_pipeline_candidate(
        db,
        run_id=rid,
        provider_key="general",
        parameter="A=1,B=2",
        score=4.5,
        status="running",
    )
    row = get_pipeline_candidate(db, rid, "general", "A=1,B=2")
    assert json.loads(row["score_provenance_json"]) == provenance


def test_pipeline_schema_migrates_v3_candidate_score_provenance_column():
    assert SCHEMA_VERSION >= 4
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("CREATE TABLE curves(id INTEGER PRIMARY KEY)")
    legacy_schema = PIPELINE_SCHEMA.replace(
        "    score_provenance_json TEXT NOT NULL DEFAULT '{}',\n",
        "",
    )
    db.executescript(legacy_schema)
    db.commit()

    before = {
        row["name"]
        for row in db.execute(
            "PRAGMA table_info(search_pipeline_candidates)"
        ).fetchall()
    }
    assert "score_provenance_json" not in before

    assert ensure_pipeline_schema(db) is True
    after = {
        row["name"]
        for row in db.execute(
            "PRAGMA table_info(search_pipeline_candidates)"
        ).fetchall()
    }
    assert "score_provenance_json" in after
    assert ensure_pipeline_schema(db) is False


def test_pipeline_payload_accepts_preversioned_row_mapping():
    row = {
        "id": 9,
        "name": "Legacy mapping",
        "target_mode": "general",
        "stages_json": '[{"config":{},"id":"nagao_screen"}]',
        "config_json": '{"target_rank":8}',
    }
    payload = pipeline_payload(row)
    assert payload["revision"] == 1
    assert len(payload["content_hash"]) == 64
    assert payload["stages"] == [{"config": {}, "id": "nagao_screen"}]
    assert payload["config"] == {"target_rank": 8}


def test_pipeline_run_payload_treats_missing_manifest_as_legacy():
    row = {
        "id": 3,
        "pipeline_id": None,
        "pipeline_name": "Legacy Run",
        "target_mode": "general",
        "target_json": "{}",
        "stages_json": "[]",
        "run_config_json": "{}",
        "status": "failed",
    }
    payload = pipeline_run_payload(row)
    assert payload["campaign_id"] is None
    assert payload["manifest"] == {}
    drift = pipeline_run_runtime_drift(row)
    assert drift["legacy"] is True
    assert drift["blocking"] == []
    assert drift["warnings"] == ["legacy run has no frozen runtime manifest"]


def test_pipeline_run_runtime_drift_detects_catalog_and_core_changes():
    db = _db()
    rid = create_pipeline_run(
        db,
        pipeline_name="Drift",
        target_mode="general",
        target={"pool_mode": "open"},
        stages=[{"id": "nagao_screen", "config": {}}],
        run_config={"target_rank": 8},
    )
    row = get_pipeline_run(db, rid)
    manifest = json.loads(row["manifest_json"])
    manifest["pipeline_catalog_hash"] = "older-catalog"
    manifest["core_version"] = "0.0.0"
    db.execute(
        "UPDATE search_pipeline_runs SET manifest_json=? WHERE id=?",
        (json.dumps(manifest, sort_keys=True), rid),
    )
    db.commit()

    drift = pipeline_run_runtime_drift(get_pipeline_run(db, rid))
    assert drift["legacy"] is False
    assert drift["blocking"] == []
    assert "Pipeline module catalog fingerprint changed" in drift["warnings"]
    assert any("Rank Hunter core changed" in item for item in drift["warnings"])


def test_pipeline_schema_second_check_is_read_only():
    db = _db()
    traced = []
    db.set_trace_callback(traced.append)
    assert ensure_pipeline_schema(db) is False
    writes = [
        stmt for stmt in traced
        if stmt.lstrip().upper().startswith(
            ("CREATE ", "ALTER ", "INSERT ", "UPDATE ", "DELETE ")
        )
    ]
    assert writes == []


def test_deleting_saved_recipe_preserves_run_snapshot():
    db = _db()
    pid = save_pipeline(
        db,
        name="Disposable recipe",
        target_mode="general",
        stages=[{"id": "nagao_screen", "config": {}}],
    )
    rid = create_pipeline_run(
        db,
        pipeline_id=pid,
        pipeline_name="Disposable recipe",
        target_mode="general",
        target={"pool_mode": "open"},
        stages=[{"id": "nagao_screen", "config": {}}],
    )
    from rank42.pipeline_state import delete_pipeline
    delete_pipeline(db, pid)
    row = get_pipeline_run(db, rid)
    assert row is not None
    assert row["pipeline_id"] is None
    assert row["pipeline_name"] == "Disposable recipe"



def test_torsion_progress_excludes_unverified_and_mismatching_fibers():
    db = _db()
    rid = create_pipeline_run(
        db,
        pipeline_name="C2xC8",
        target_mode="torsion",
        target={"torsion_group": "C2 × C8"},
        stages=[
            {"id": "nagao_screen", "config": {}},
            {"id": "exact_torsion", "config": {}},
        ],
        run_config={"target_rank": 4},
    )
    upsert_pipeline_candidate(
        db,
        run_id=rid,
        provider_key="bad",
        parameter="1",
        status="torsion_mismatch",
        rigorous_lower=18,
        stage_results={
            "2": {
                "stage_id": "exact_torsion",
                "result": {"status": "torsion_mismatch", "torsion_label": "Trivial"},
            }
        },
    )
    upsert_pipeline_candidate(
        db,
        run_id=rid,
        provider_key="waiting",
        parameter="2",
        status="running",
        rigorous_lower=12,
        stage_results={},
    )
    progress = run_progress(db, rid)
    assert progress["best_lower"] == 0
    assert progress["best_curve_id"] is None


def test_torsion_goal_filtered_fiber_remains_rank_eligible_after_exact_match():
    db = _db()
    db.execute("INSERT INTO curves(id) VALUES(7)")
    rid = create_pipeline_run(
        db,
        pipeline_name="C8",
        target_mode="torsion",
        target={"torsion_group": "C8"},
        stages=[
            {"id": "nagao_screen", "config": {}},
            {"id": "exact_torsion", "config": {}},
            {"id": "pari_upper_gate", "config": {}},
        ],
        run_config={"target_rank": 7},
    )
    upsert_pipeline_candidate(
        db,
        run_id=rid,
        provider_key="mazur:c8",
        parameter="3/2",
        curve_id=7,
        status="goal_filtered",
        rigorous_lower=5,
        rigorous_upper=6,
        stage_results={
            "2": {
                "stage_id": "exact_torsion",
                "result": {"status": "completed", "torsion_label": "C8"},
            },
            "3": {
                "stage_id": "pari_upper_gate",
                "result": {"status": "goal_filtered", "rigorous_upper": 6},
            },
        },
    )
    progress = run_progress(db, rid)
    assert progress["done"] == 1
    assert progress["best_lower"] == 5
    assert progress["best_curve_id"] == 7



def test_funnel_pruned_candidate_is_done_but_rank_eligible():
    db = _db()
    db.execute("INSERT INTO curves(id) VALUES(9)")
    rid = create_pipeline_run(
        db,
        pipeline_name="Funnel",
        target_mode="general",
        target={"pool_mode": "open"},
        stages=[{"id": "nagao_screen", "config": {}}],
        run_config={"target_rank": 8},
    )
    upsert_pipeline_candidate(
        db,
        run_id=rid,
        provider_key="general",
        parameter="A=9,B=1",
        curve_id=9,
        status="funnel_pruned",
        rigorous_lower=4,
    )
    progress = run_progress(db, rid)
    assert progress["total"] == 1
    assert progress["done"] == 1
    assert progress["best_lower"] == 4
    assert progress["best_curve_id"] == 9



def test_constraint_filtered_candidate_is_done_and_rank_ineligible():
    db = _db()
    db.execute("INSERT INTO curves(id) VALUES(13)")
    rid = create_pipeline_run(
        db,
        pipeline_name="Prime constraint",
        target_mode="general",
        target={"pool_mode": "open"},
        stages=[{"id": "nagao_screen", "config": {}}],
        run_config={"target_rank": 8},
    )
    upsert_pipeline_candidate(
        db,
        run_id=rid,
        provider_key="general",
        parameter="A=13,B=1",
        curve_id=13,
        status="constraint_filtered",
        rigorous_lower=9,
    )
    progress = run_progress(db, rid)
    assert progress["total"] == 1
    assert progress["done"] == 1
    assert progress["best_lower"] == 0
    assert progress["best_curve_id"] is None



def test_save_pipeline_updates_existing_definition_in_place():
    db = _db()
    pid = save_pipeline(
        db,
        name="Original",
        target_mode="family",
        stages=[{"id": "nagao_screen", "config": {"prime_bound": 523}}],
        config={"feature_plugin_ids": ["feature-a"]},
    )
    original = pipeline_payload(get_pipeline(db, pid))
    assert original["revision"] == 1

    updated_id = save_pipeline(
        db,
        pipeline_id=pid,
        name="Edited",
        target_mode="general",
        stages=[
            {"id": "nagao_screen", "config": {"prime_bound": 997}},
            {"id": "integral_seed", "config": {"heights": [1000]}},
        ],
        config={"feature_plugin_ids": ["feature-b"]},
    )
    assert updated_id == pid
    payload = pipeline_payload(get_pipeline(db, pid))
    assert payload["name"] == "Edited"
    assert payload["target_mode"] == "general"
    assert payload["stages"][0]["config"]["prime_bound"] == 997
    assert payload["stages"][1]["id"] == "integral_seed"
    assert payload["config"]["feature_plugin_ids"] == ["feature-b"]
    assert payload["revision"] == 2
    assert payload["content_hash"] != original["content_hash"]

    same_id = save_pipeline(
        db,
        pipeline_id=pid,
        name="Edited",
        target_mode="general",
        stages=payload["stages"],
        config=payload["config"],
    )
    assert same_id == pid
    unchanged = pipeline_payload(get_pipeline(db, pid))
    assert unchanged["revision"] == 2
    assert unchanged["content_hash"] == payload["content_hash"]


def test_pipeline_schema_migrates_legacy_definition_revision_and_hash():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("CREATE TABLE curves(id INTEGER PRIMARY KEY)")
    db.execute(
        """CREATE TABLE search_pipeline_definitions(
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               name TEXT NOT NULL,
               target_mode TEXT NOT NULL,
               stages_json TEXT NOT NULL,
               config_json TEXT NOT NULL DEFAULT '{}',
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL
           )"""
    )
    db.execute(
        """INSERT INTO search_pipeline_definitions(
               name,target_mode,stages_json,config_json,created_at,updated_at
           ) VALUES(?,?,?,?,?,?)""",
        (
            "Legacy",
            "general",
            '[{"config":{},"id":"nagao_screen"}]',
            '{"target_rank":8}',
            "2026-09-01T00:00:00",
            "2026-09-01T00:00:00",
        ),
    )
    db.commit()

    assert ensure_pipeline_schema(db) is True
    row = get_pipeline(db, 1)
    payload = pipeline_payload(row)
    assert payload["revision"] == 1
    assert len(payload["content_hash"]) == 64
    assert payload["pipeline_schema_version"] == 0
    assert payload["pipeline_catalog_version"] == 0
    assert payload["pipeline_catalog_hash"] == ""
    run_columns = {
        row["name"] for row in db.execute(
            "PRAGMA table_info(search_pipeline_runs)"
        ).fetchall()
    }
    assert {
        "campaign_id",
        "pipeline_schema_version",
        "pipeline_catalog_version",
        "pipeline_catalog_hash",
        "manifest_json",
    }.issubset(run_columns)
    run_indexes = {
        row["name"]
        for row in db.execute("PRAGMA index_list(search_pipeline_runs)").fetchall()
    }
    assert "idx_pipeline_runs_campaign" in run_indexes
    point_attempt_columns = {
        row["name"]
        for row in db.execute(
            "PRAGMA table_info(search_pipeline_point_attempts)"
        ).fetchall()
    }
    assert {
        "denominator_low",
        "denominator_high",
        "effective_denominator_low",
        "effective_denominator_high",
    }.issubset(point_attempt_columns)
    tables = {
        row["name"]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    run_fks = db.execute(
        "PRAGMA foreign_key_list(search_pipeline_runs)"
    ).fetchall()
    assert "research_campaigns" in tables
    assert any(
        row["table"] == "research_campaigns"
        and row["from"] == "campaign_id"
        and row["on_delete"].upper() == "SET NULL"
        for row in run_fks
    )
    assert ensure_pipeline_schema(db) is False


def test_pipeline_derivation_graph_records_parent_child_lineage():
    db = _db()
    rid = create_pipeline_run(
        db,
        pipeline_name="Transforms",
        target_mode="general",
        target={"pool_mode": "open"},
        stages=[{"id": "nagao_screen", "config": {}}],
    )
    parent = upsert_pipeline_candidate(
        db,
        run_id=rid,
        provider_key="general",
        parameter="parent",
        status="running",
    )
    child = upsert_pipeline_candidate(
        db,
        run_id=rid,
        provider_key="general|quadratic_twist_sweep",
        parameter="twist:d=-1",
        status="queued",
    )
    record_pipeline_derivation(
        db,
        run_id=rid,
        stage_index=2,
        stage_id="quadratic_twist_sweep",
        parent_candidate_id=parent["id"],
        child_candidate_id=child["id"],
        transform_kind="quadratic_twist",
        metadata={"twist_d": -1, "child_kind": "direct_curve"},
    )
    rows = pipeline_derivations(db, rid)
    assert len(rows) == 1
    assert rows[0]["stage_id"] == "quadratic_twist_sweep"
    assert rows[0]["transform_kind"] == "quadratic_twist"
    assert rows[0]["parent_parameter"] == "parent"
    assert rows[0]["child_parameter"] == "twist:d=-1"


def test_transformed_parent_is_terminal_but_rank_eligible():
    db = _db()
    db.execute("INSERT INTO curves(id) VALUES(1)")
    rid = create_pipeline_run(
        db,
        pipeline_name="Transform",
        target_mode="general",
        target={"pool_mode": "open"},
        stages=[{"id": "nagao_screen", "config": {}}],
    )
    upsert_pipeline_candidate(
        db,
        run_id=rid,
        provider_key="general",
        parameter="parent",
        curve_id=1,
        status="transformed",
        rigorous_lower=6,
    )
    progress = run_progress(db, rid)
    assert progress["done"] == 1
    assert progress["best_lower"] == 6
    assert progress["best_curve_id"] == 1



def test_pipeline_schema_migrates_v5_point_attempt_regions():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("CREATE TABLE curves(id INTEGER PRIMARY KEY)")
    legacy_schema = PIPELINE_SCHEMA.replace(
        "    effective_denominator_low INTEGER,\n"
        "    effective_denominator_high INTEGER,\n",
        "",
    )
    db.executescript(legacy_schema)

    before = {
        row["name"]
        for row in db.execute(
            "PRAGMA table_info(search_pipeline_point_attempts)"
        ).fetchall()
    }
    assert "effective_denominator_low" not in before
    assert "effective_denominator_high" not in before

    assert ensure_pipeline_schema(db) is True
    after = {
        row["name"]
        for row in db.execute(
            "PRAGMA table_info(search_pipeline_point_attempts)"
        ).fetchall()
    }
    assert "effective_denominator_low" in after
    assert "effective_denominator_high" in after
    assert ensure_pipeline_schema(db) is False



def test_pipeline_run_manifest_freezes_ratpoints_engine_identity(monkeypatch):
    import rank42.pipeline_state as pipeline_state

    expected = {
        "backend": "GPU",
        "executable": "/vendor/ratpoints_gpu",
        "version": "3.0.0",
        "source": "vendored",
        "vendored_revision": "b" * 40,
        "version_probe_error": None,
    }
    captured = {}

    def fake_identity(executable=None, backend_hint=None):
        captured["executable"] = executable
        captured["backend_hint"] = backend_hint
        return dict(expected)

    monkeypatch.setattr(pipeline_state, "ratpoints_engine_identity", fake_identity)
    db = _db()
    rid = create_pipeline_run(
        db,
        pipeline_name="Engine manifest",
        target_mode="curve",
        target={"curve_id": 1},
        stages=[{"id": "affine_search", "config": {}}],
        run_config={
            "ratpoints_backend": "GPU",
            "ratpoints": "/vendor/ratpoints_gpu",
        },
    )
    manifest = pipeline_run_payload(get_pipeline_run(db, rid))["manifest"]
    assert manifest["manifest_version"] == 3
    assert manifest["ratpoints_backend"] == "GPU"
    assert manifest["ratpoints_engine"] == expected
    assert captured == {
        "executable": "/vendor/ratpoints_gpu",
        "backend_hint": "GPU",
    }


def test_pipeline_schema_migrates_v6_point_attempt_engine_provenance():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("CREATE TABLE curves(id INTEGER PRIMARY KEY)")
    legacy_schema = PIPELINE_SCHEMA.replace(
        "    engine_json TEXT NOT NULL DEFAULT '{}',\n",
        "",
    )
    db.executescript(legacy_schema)

    before = {
        row["name"]
        for row in db.execute(
            "PRAGMA table_info(search_pipeline_point_attempts)"
        ).fetchall()
    }
    assert "engine_json" not in before

    assert ensure_pipeline_schema(db) is True
    after = {
        row["name"]
        for row in db.execute(
            "PRAGMA table_info(search_pipeline_point_attempts)"
        ).fetchall()
    }
    assert "engine_json" in after
    assert ensure_pipeline_schema(db) is False



def test_strategy_step_checkpoint_persists_attempt_and_result():
    db = _db()
    run_id = create_pipeline_run(
        db, pipeline_name="strategy-checkpoint", target_mode="general",
        target={"pool_mode": "open"},
        stages=[{"id": "large_height_generator_hunt", "config": {}}],
        run_config={"target_rank": 20},
    )
    candidate = upsert_pipeline_candidate(
        db, run_id=run_id, parameter="curve:1",
        provider_key="general", status="searching",
    )
    occurrence = f"pipeline:{run_id}:candidate:{candidate['id']}:stage:1:large_height_generator_hunt"
    common = dict(
        run_id=run_id, candidate_id=candidate["id"], curve_id=None,
        stage_index=1, strategy_id="large_height_generator_hunt",
        occurrence_id=occurrence, step_index=1,
        step_key="higher_descent_ladder", step_kind="substep",
        nested_stage_id="higher_descent_ladder",
        wall_budget_seconds=1800, retry_policy="automatic",
    )
    upsert_pipeline_strategy_step(db, status="running", **common)
    upsert_pipeline_strategy_step(
        db, status="completed", elapsed_seconds=12.5,
        result={"status": "completed"}, **common
    )
    rows = pipeline_strategy_steps(
        db, run_id, candidate_id=candidate["id"],
        stage_index=1, occurrence_id=occurrence,
    )
    assert len(rows) == 1
    assert rows[0]["status"] == "completed"
    assert rows[0]["attempt_count"] == 1
    assert rows[0]["elapsed_seconds"] == 12.5


def test_list_pipeline_runs_readonly_does_not_create_schema():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    try:
        assert list_pipeline_runs_readonly(db, limit=10) == []
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='search_pipeline_runs'"
        ).fetchone()
        assert exists is None
    finally:
        db.close()


def test_pipeline_run_manifest_freezes_plugin_fingerprints_and_reports_drift(
    tmp_path,
    monkeypatch,
):
    import rank42.pipeline_state as pipeline_state
    import rank42.plugins as plugins

    class FakeVariant:
        def __init__(self, variant_id):
            self.id = variant_id

    class FakePlugin:
        def __init__(self, plugin_id, version, plugin_type, variants=()):
            self.id = plugin_id
            self.version = version
            self.plugin_type = plugin_type
            self.variants = tuple(variants)

    family = FakePlugin(
        "family-a",
        "1.0",
        "family",
        (FakeVariant("v1"),),
    )
    feature = FakePlugin("feature-a", "2.0", "feature")
    current = {
        "family-a": {
            "plugin_manifest_sha256": "m1",
            "family_sha256": "f1",
            "adapter_sha256": "a1",
        },
        "feature-a": {
            "plugin_manifest_sha256": "fm1",
            "family_sha256": None,
            "adapter_sha256": "fc1",
        },
    }

    monkeypatch.setattr(plugins, "Plugin", FakePlugin)
    monkeypatch.setattr(
        plugins,
        "discover_plugins",
        lambda *_args, **_kwargs: [family, feature],
    )
    monkeypatch.setattr(
        plugins,
        "get_variant",
        lambda plugin: plugin.variants[0] if plugin.variants else None,
    )
    monkeypatch.setattr(
        plugins,
        "plugin_fingerprints",
        lambda plugin, variant=None: dict(current[plugin.id]),
    )

    db = _db()
    rid = create_pipeline_run(
        db,
        pipeline_name="Plugin provenance",
        target_mode="family",
        target={"plugin_id": "family-a", "variant_id": "v1"},
        stages=[{"id": "nagao_screen", "config": {}}],
        run_config={
            "plugin_id": "family-a",
            "plugin_variant": "v1",
            "feature_plugin_ids": ["feature-a"],
        },
        project_root=tmp_path,
    )
    row = get_pipeline_run(db, rid)
    manifest = pipeline_run_payload(row)["manifest"]
    assert manifest["manifest_version"] == 3
    assert manifest["plugin_runtime"]["primary"] == {
        "plugin_id": "family-a",
        "plugin_version": "1.0",
        "plugin_type": "family",
        "variant_id": "v1",
        "fingerprints": current["family-a"],
    }
    assert manifest["plugin_runtime"]["features"] == [
        {
            "plugin_id": "feature-a",
            "plugin_version": "2.0",
            "plugin_type": "feature",
            "variant_id": None,
            "fingerprints": current["feature-a"],
        }
    ]
    assert pipeline_run_runtime_drift(
        row,
        project_root=tmp_path,
    )["warnings"] == []

    current["family-a"]["family_sha256"] = "f2"
    current["feature-a"]["adapter_sha256"] = "fc2"
    drift = pipeline_run_runtime_drift(
        get_pipeline_run(db, rid),
        project_root=tmp_path,
    )
    assert "Plugin runtime fingerprint changed: family-a" in drift["warnings"]
    assert (
        "Feature plugin runtime fingerprint changed: feature-a"
        in drift["warnings"]
    )


def test_pipeline_run_runtime_drift_keeps_pre_v3_manifest_readable(tmp_path):
    db = _db()
    rid = create_pipeline_run(
        db,
        pipeline_name="Legacy manifest",
        target_mode="general",
        target={},
        stages=[{"id": "nagao_screen", "config": {}}],
        run_config={},
        project_root=tmp_path,
    )
    row = get_pipeline_run(db, rid)
    manifest = json.loads(row["manifest_json"])
    manifest["manifest_version"] = 2
    manifest.pop("plugin_runtime", None)
    db.execute(
        "UPDATE search_pipeline_runs SET manifest_json=? WHERE id=?",
        (json.dumps(manifest, sort_keys=True), rid),
    )
    db.commit()

    drift = pipeline_run_runtime_drift(
        get_pipeline_run(db, rid),
        project_root=tmp_path,
    )
    assert drift["legacy"] is False
    assert drift["blocking"] == []
    assert "legacy run has no frozen plugin runtime fingerprints" in drift["warnings"]
