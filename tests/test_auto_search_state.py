import sqlite3

from rank42.auto_search_state import (
    campaign_progress,
    child_campaign,
    campaign_trials,
    completed_step_count,
    create_campaign,
    ensure_auto_search_schema,
    get_campaign,
    get_step,
    list_campaigns,
    reconcile_child_campaign_lifecycle,
    terminal_trial_parameters,
    update_campaign,
    upsert_step,
    upsert_trial,
)


def _db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE candidate_pools(id INTEGER PRIMARY KEY)")
    db.execute("CREATE TABLE candidates(id INTEGER PRIMARY KEY)")
    db.execute("CREATE TABLE curves(id INTEGER PRIMARY KEY, family TEXT)")
    ensure_auto_search_schema(db)
    return db


def test_campaign_and_trial_lifecycle():
    db = _db()
    cid = create_campaign(
        db,
        plugin_id="demo",
        plugin_version="1.0",
        variant_id="default",
        family_spec="demo_family",
        family_name="Demo Family",
        target_rank=20,
        config={"pool_size": 50},
    )
    row = get_campaign(db, cid)
    assert row["status"] == "queued"
    assert row["target_rank"] == 20

    update_campaign(
        db,
        cid,
        status="running",
        candidates_total=50,
        best_lower=3,
    )
    row = get_campaign(db, cid)
    assert row["status"] == "running"
    assert row["candidates_total"] == 50
    assert row["best_lower"] == 3

    upsert_trial(
        db,
        campaign_id=cid,
        parameter="2/3",
        score=12.5,
        status="searching",
        tier="structural",
        rigorous_lower=3,
        exact_points=5,
        rank_growth=1,
        metadata={"tiers": ["scout", "structural"]},
    )
    upsert_trial(
        db,
        campaign_id=cid,
        parameter="2/3",
        score=12.5,
        status="exhausted",
        tier="deep",
        rigorous_lower=4,
        rigorous_upper=7,
        exact_points=9,
        rank_growth=2,
        metadata={"exhaustion_scope": "configured Auto Search tiers only"},
    )
    rows = campaign_trials(db, cid)
    assert len(rows) == 1
    assert rows[0]["status"] == "exhausted"
    assert rows[0]["rigorous_lower"] == 4
    assert rows[0]["rigorous_upper"] == 7
    assert rows[0]["rank_growth"] == 2

    campaigns = list_campaigns(db)
    assert [row["id"] for row in campaigns] == [cid]


def test_step_cursor_is_durable_and_running_step_is_retryable():
    db = _db()
    cid = create_campaign(
        db,
        plugin_id="demo",
        plugin_version="1.0",
        variant_id="default",
        family_spec="demo_family",
        target_rank=20,
    )
    upsert_step(
        db,
        campaign_id=cid,
        parameter="2/3",
        tier="structural",
        center="5/7",
        scale="1/3",
        height=10000,
        status="running",
    )
    row = get_step(
        db,
        campaign_id=cid,
        parameter="2/3",
        tier="structural",
        center="5/7",
        scale="1/3",
        height=10000,
    )
    assert row["status"] == "running"
    assert completed_step_count(db, cid) == 0

    upsert_step(
        db,
        campaign_id=cid,
        parameter="2/3",
        tier="structural",
        center="5/7",
        scale="1/3",
        height=10000,
        status="completed",
        exact_points=2,
        runtime=0.25,
    )
    row = get_step(
        db,
        campaign_id=cid,
        parameter="2/3",
        tier="structural",
        center="5/7",
        scale="1/3",
        height=10000,
    )
    assert row["status"] == "completed"
    assert row["exact_points"] == 2
    assert completed_step_count(db, cid, "2/3") == 1


def test_campaign_progress_and_terminal_parameters_derive_from_trials():
    db = _db()
    cid = create_campaign(
        db,
        plugin_id="demo",
        plugin_version="1.0",
        variant_id="default",
        family_spec="demo_family",
        target_rank=20,
    )
    upsert_trial(
        db,
        campaign_id=cid,
        parameter="1/2",
        status="exact",
        rigorous_lower=4,
        rigorous_upper=4,
        exact_rank=4,
    )
    upsert_trial(
        db,
        campaign_id=cid,
        parameter="2/3",
        status="exhausted",
        rigorous_lower=3,
    )
    upsert_trial(
        db,
        campaign_id=cid,
        parameter="3/4",
        status="searching",
        rigorous_lower=5,
    )
    progress = campaign_progress(db, cid)
    assert progress["done"] == 2
    assert progress["exact_count"] == 1
    assert progress["target_hit_count"] == 0
    assert progress["exhausted_count"] == 1
    assert progress["best_lower"] == 5
    assert terminal_trial_parameters(db, cid) == {"1/2", "2/3"}


def test_torsion_parent_child_campaign_state_and_filter_counts():
    db = _db()
    parent = create_campaign(
        db,
        plugin_id="__torsion__",
        plugin_version=None,
        variant_id="C2 × C6",
        family_spec="torsion:C2 × C6",
        family_name="Torsion C2 × C6",
        target_rank=16,
        search_mode="torsion",
        torsion_group="C2 × C6",
        retention_floor=12,
        stop_on_target=True,
    )
    child = create_campaign(
        db,
        plugin_id="provider",
        plugin_version="1",
        variant_id="default",
        family_spec="provider_family",
        family_name="Provider",
        target_rank=16,
        search_mode="torsion_provider",
        torsion_group="C2 × C6",
        retention_floor=12,
        stop_on_target=True,
        parent_campaign_id=parent,
        provider_key="provider:default",
    )
    assert [row["id"] for row in list_campaigns(db, top_level_only=True)] == [parent]
    assert child_campaign(db, parent, "provider:default")["id"] == child

    upsert_trial(
        db,
        campaign_id=child,
        parameter="1/3",
        status="torsion_mismatch",
        rigorous_lower=18,
        metadata={"target_torsion": "C2 × C6", "exact_torsion": "Trivial"},
    )
    progress = campaign_progress(db, child)
    assert progress["done"] == 1
    assert progress["torsion_mismatch_count"] == 1
    assert progress["target_hit_count"] == 0
    assert progress["best_lower"] == 0
    assert progress["best_curve_id"] is None
    assert terminal_trial_parameters(db, child) == {"1/3"}


def test_auto_search_schema_second_check_is_read_only():
    db = _db()
    traced = []
    db.set_trace_callback(traced.append)
    assert ensure_auto_search_schema(db) is False
    writes = [
        stmt for stmt in traced
        if stmt.lstrip().upper().startswith(("CREATE ", "ALTER ", "INSERT ", "UPDATE ", "DELETE "))
    ]
    assert writes == []


def test_exact_trial_at_goal_counts_as_target_hit():
    db = _db()
    cid = create_campaign(
        db,
        plugin_id="demo",
        plugin_version="1.0",
        variant_id="default",
        family_spec="demo_family",
        target_rank=4,
    )
    upsert_trial(
        db,
        campaign_id=cid,
        parameter="5/7",
        status="exact",
        rigorous_lower=4,
        rigorous_upper=4,
        exact_rank=4,
    )
    progress = campaign_progress(db, cid)
    assert progress["exact_count"] == 1
    assert progress["target_hit_count"] == 1



def test_screened_out_geometry_trial_is_terminal_but_not_rank_eligible():
    db = _db()
    cid = create_campaign(
        db,
        plugin_id="demo",
        plugin_version="1.0",
        variant_id="default",
        family_spec="demo_family",
        target_rank=4,
        search_mode="geometry",
    )
    upsert_trial(
        db,
        campaign_id=cid,
        parameter="11/13",
        status="screened_out",
        rigorous_lower=99,
        metadata={"reason": "below geometry shortlist"},
    )
    progress = campaign_progress(db, cid)
    assert progress["done"] == 1
    assert progress["target_hit_count"] == 0
    assert progress["best_lower"] == 0
    assert progress["best_curve_id"] is None
    assert terminal_trial_parameters(db, cid) == {"11/13"}



def test_parent_pause_repairs_running_provider_child():
    db = _db()
    parent = create_campaign(
        db,
        plugin_id="__torsion_geometry__",
        plugin_version=None,
        variant_id="C2 × C8",
        family_spec="torsion:C2 × C8",
        family_name="Torsion C2 × C8",
        target_rank=4,
        search_mode="geometry_torsion",
        torsion_group="C2 × C8",
    )
    child = create_campaign(
        db,
        plugin_id="mazur",
        plugin_version="1",
        variant_id="c2xc8",
        family_spec="mazur_family",
        family_name="Mazur C2 × C8",
        target_rank=4,
        search_mode="geometry_torsion_provider",
        torsion_group="C2 × C8",
        parent_campaign_id=parent,
        provider_key="mazur:c2xc8",
    )
    update_campaign(db, parent, status="paused")
    update_campaign(db, child, status="running")

    changed = reconcile_child_campaign_lifecycle(db, parent)

    assert changed == 1
    assert get_campaign(db, child)["status"] == "paused"


def test_parent_kill_does_not_downgrade_completed_provider():
    db = _db()
    parent = create_campaign(
        db,
        plugin_id="__torsion__",
        plugin_version=None,
        variant_id="C8",
        family_spec="torsion:C8",
        family_name="Torsion C8",
        target_rank=7,
        search_mode="torsion",
        torsion_group="C8",
    )
    child = create_campaign(
        db,
        plugin_id="mazur",
        plugin_version="1",
        variant_id="c8",
        family_spec="mazur_family",
        family_name="Mazur C8",
        target_rank=7,
        search_mode="torsion_provider",
        torsion_group="C8",
        parent_campaign_id=parent,
        provider_key="mazur:c8",
    )
    update_campaign(db, parent, status="killed")
    update_campaign(db, child, status="completed")

    changed = reconcile_child_campaign_lifecycle(db, parent)

    assert changed == 0
    assert get_campaign(db, child)["status"] == "completed"
