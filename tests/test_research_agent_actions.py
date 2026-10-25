from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from rank42.db import connect, upsert_curve
from rank42 import research_agent_actions as actions


def test_agent_acknowledges_only_reviewed_attention_alerts(monkeypatch, tmp_path):
    db = connect(":memory:")
    alerts = [
        {"attention_key": "job_failure:job:12:old", "title": "Job #12 failed",
         "object_type": "job", "object_id": "12",
         "acknowledgeable": True},
        {"attention_key": "dispatcher:1", "title": "Dispatcher unavailable",
         "acknowledgeable": False},
    ]
    acknowledged = []
    monkeypatch.setattr(actions, "dashboard_attention_snapshot", lambda *_a, **_k: alerts)
    monkeypatch.setattr(actions, "acknowledge_dashboard_attention",
                        lambda _db, item: acknowledged.append(item["attention_key"]))
    monkeypatch.setattr(actions, "generate_json", lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("Explicit Attention request should not require model classification")))
    plan = actions.plan_action(db, tmp_path, message="Clear my Attention notifications")
    assert plan["kind"] == "acknowledge_attention"
    assert plan["attention_keys"] == ["job_failure:job:12:old"]
    assert "Job #12 failed" in plan["preview"]
    assert acknowledged == []
    alerts.append({"attention_key": "job_failure:job:13:new", "title": "Job #13 failed",
                   "object_type": "job", "object_id": "13",
                   "acknowledgeable": True})
    ctx = SimpleNamespace(project_root=tmp_path)
    assert actions.execute_action(db, ctx, plan) == "Acknowledged 1 Dashboard Attention alert(s)."
    assert acknowledged == ["job_failure:job:12:old"]


def test_agent_does_not_clear_live_attention_issues(monkeypatch, tmp_path):
    db = connect(":memory:")
    monkeypatch.setattr(actions, "dashboard_attention_snapshot", lambda *_a, **_k: [
        {"attention_key": "dispatcher:1", "title": "Dispatcher unavailable",
         "acknowledgeable": False},
    ])
    with pytest.raises(ValueError, match="no matching Attention alerts"):
        actions.plan_action(db, tmp_path, message="Acknowledge all attention alerts")


def test_agent_can_target_one_failed_job_attention_alert(monkeypatch, tmp_path):
    db = connect(":memory:")
    alerts = [
        {"attention_key": f"job_failure:job:{job_id}:old",
         "title": f"Job #{job_id} failed", "object_type": "job",
         "object_id": str(job_id), "acknowledgeable": True}
        for job_id in (12, 13)
    ]
    monkeypatch.setattr(actions, "dashboard_attention_snapshot", lambda *_a, **_k: alerts)
    plan = actions.plan_action(db, tmp_path,
                               message="Clear the Attention alert for job #12")
    assert plan["attention_keys"] == ["job_failure:job:12:old"]


def test_campaign_is_previewed_before_creation(monkeypatch, tmp_path):
    db = connect(":memory:")
    monkeypatch.setattr(actions, "generate_json", lambda *_a, **_k: {
        "action": "create_campaign", "name": "C8 record attempt",
        "objective": "Find an independent point", "goal_rank": 4,
    })
    plan = actions.plan_action(db, tmp_path, message="Create a campaign for a C8 record attempt")
    assert plan["kind"] == "create_campaign"
    assert db.execute("SELECT name FROM sqlite_master WHERE name='research_campaigns'").fetchone() is None
    ctx = SimpleNamespace(project_root=tmp_path, db_path=tmp_path / "test.db")
    answer = actions.execute_action(db, ctx, plan)
    assert "campaign #1" in answer
    assert db.execute("SELECT name FROM research_campaigns").fetchone()[0] == "C8 record attempt"


def test_hunt_uses_fixed_budget_and_only_queues_after_confirmation(monkeypatch, tmp_path):
    db = connect(":memory:")
    curve_id = upsert_curve(db, family="test", parameter="1", a_invariants_json="[0,0,0,1,1]")
    monkeypatch.setattr(actions, "generate_json", lambda *_a, **_k: {
        "action": "start_ai_hunt", "target_mode": "curve", "curve_id": curve_id,
        "goal_rank": 5, "objective": "Find one more independent point",
    })
    monkeypatch.setattr(actions, "strategy_options", lambda *_a, **_k: [{"id": "test"}])
    queued = []
    monkeypatch.setattr(actions, "enqueue_job", lambda *_a, **kwargs: queued.append(kwargs) or 77)
    plan = actions.plan_action(db, tmp_path, message=f"Start an AI hunt for curve #{curve_id} to rank 5")
    assert plan["config"]["max_total_minutes"] == 60
    assert plan["config"]["max_rounds"] == 2
    assert queued == []
    ctx = SimpleNamespace(project_root=tmp_path, db_path=tmp_path / "test.db")
    answer = actions.execute_action(db, ctx, plan)
    assert "job #77" in answer
    assert queued[0]["kind"] == "ai_search"
    assert db.execute("SELECT COUNT(*) FROM ai_search_campaigns").fetchone()[0] == 1


def test_plugin_disable_is_explicit_and_reviewed(monkeypatch, tmp_path):
    db = connect(":memory:")
    plugin = SimpleNamespace(id="test_plugin", name="Test Plugin", plugin_type="family")
    monkeypatch.setattr(actions, "_plugins", lambda _root: [plugin])
    monkeypatch.setattr(actions, "is_enabled", lambda _db, _plugin: True)
    monkeypatch.setattr(actions, "generate_json", lambda *_a, **_k: {
        "action": "disable_plugin", "plugin": "Test Plugin",
    })
    calls = []
    monkeypatch.setattr(actions, "set_plugin_state", lambda *_a, **kwargs: calls.append(kwargs))
    with pytest.raises(ValueError, match="Name the installed plugin"):
        actions.plan_action(db, tmp_path, message="Disable the plugin")
    plan = actions.plan_action(db, tmp_path, message="Disable Test Plugin")
    assert calls == []
    ctx = SimpleNamespace(project_root=tmp_path, db_path=tmp_path / "test.db")
    assert actions.execute_action(db, ctx, plan) == "Disabled Test Plugin."
    assert calls == [{"enabled": False, "status": "disabled"}]


def test_plugin_enable_queues_validation_instead_of_flipping_state(monkeypatch, tmp_path):
    db = connect(":memory:")
    plugin = SimpleNamespace(id="test_plugin", name="Test Plugin", plugin_type="extension")
    monkeypatch.setattr(actions, "_plugins", lambda _root: [plugin])
    monkeypatch.setattr(actions, "is_enabled", lambda _db, _plugin: False)
    monkeypatch.setattr(actions, "is_archived", lambda _db, _plugin: False)
    monkeypatch.setattr(actions, "generate_json", lambda *_a, **_k: {
        "action": "enable_plugin", "plugin": "Test Plugin",
    })
    state_changes = []
    jobs = []
    monkeypatch.setattr(actions, "set_plugin_state", lambda *_a, **_k: state_changes.append(True))
    monkeypatch.setattr(actions, "enqueue_job", lambda *_a, **kwargs: jobs.append(kwargs) or 12)
    plan = actions.plan_action(db, tmp_path, message="Enable Test Plugin")
    assert jobs == []
    ctx = SimpleNamespace(project_root=tmp_path, db_path=tmp_path / "test.db")
    assert "job #12" in actions.execute_action(db, ctx, plan)
    assert jobs[0]["kind"] == "plugin_validate"
    assert jobs[0]["command"][-1] == "test_plugin"
    assert state_changes == []


def test_model_cannot_redirect_hunt_to_unmentioned_curve(monkeypatch, tmp_path):
    db = connect(":memory:")
    first = upsert_curve(db, family="test", parameter="1", a_invariants_json="[0,0,0,1,1]")
    second = upsert_curve(db, family="test", parameter="2", a_invariants_json="[0,0,0,1,2]")
    monkeypatch.setattr(actions, "generate_json", lambda *_a, **_k: {
        "action": "start_ai_hunt", "target_mode": "curve", "curve_id": second,
    })
    monkeypatch.setattr(actions, "strategy_options", lambda *_a, **_k: [{"id": "test"}])
    plan = actions.plan_action(db, tmp_path, message=f"Start an AI hunt for curve #{first}")
    assert plan["target"] == {"curve_id": first}
    with pytest.raises(ValueError, match="Name the stored curve"):
        actions._plan_hunt(
            db, tmp_path,
            {"target_mode": "curve", "curve_id": second}, None,
            f"Start an AI hunt for curve #{first}",
        )


def test_explicit_torsion_hunt_continues_after_checkpoints(monkeypatch, tmp_path):
    db = connect(":memory:")
    monkeypatch.setattr(actions, "generate_json", lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("Explicit AI Hunt should not require model classification")))
    monkeypatch.setattr(actions, "strategy_options", lambda *_a, **_k: [{"id": "test"}])
    queued = []
    monkeypatch.setattr(actions, "enqueue_job", lambda *_a, **kw: queued.append(kw) or 91)
    plan = actions.plan_action(
        db, tmp_path,
        message="Setup an AI Hunt for the C9 torsion group. Keep running after checkpoints.",
    )
    assert plan["kind"] == "start_ai_hunt"
    assert plan["target_mode"] == "torsion"
    assert plan["target"]["torsion_group"] == "C9"
    assert plan["config"]["auto_continue"] is True
    assert plan["config"]["hard_stop_minutes"] == 1440
    assert plan["config"]["hard_stop_rounds"] == 32
    assert "continue automatically" in plan["preview"]
    assert queued == []
    ctx = SimpleNamespace(project_root=tmp_path, db_path=tmp_path / "test.db")
    assert "job #91" in actions.execute_action(db, ctx, plan)
    saved = db.execute("SELECT target_json,config_json FROM ai_search_campaigns").fetchone()
    assert json.loads(saved["target_json"])["torsion_group"] == "C9"
    assert json.loads(saved["config_json"])["auto_continue"] is True


@pytest.mark.parametrize("label,expected", [
    ("Z/9Z", "C9"), ("C2xC8", "C2 × C8"), ("Z/2Z x Z/8Z", "C2 × C8"),
])
def test_explicit_torsion_aliases_work_without_model(monkeypatch, tmp_path, label, expected):
    db = connect(":memory:")
    monkeypatch.setattr(actions, "strategy_options", lambda *_a, **_k: [{"id": "test"}])
    plan = actions.plan_action(db, tmp_path, message=f"Start an AI Hunt for {label}")
    assert plan["target"]["torsion_group"] == expected


def test_explicit_general_hunt_uses_review_card(monkeypatch, tmp_path):
    db = connect(":memory:")
    monkeypatch.setattr(actions, "strategy_options", lambda *_a, **_k: [{"id": "test"}])
    plan = actions.plan_action(db, tmp_path, message="Launch an AI Hunt for general curves")
    assert plan["target_mode"] == "general"
    assert plan["config"]["auto_continue"] is False


def test_explicit_family_and_current_curve_hunts_use_named_targets(monkeypatch, tmp_path):
    db = connect(":memory:")
    curve_id = upsert_curve(
        db, family="test", parameter="1", a_invariants_json="[0,0,0,1,1]",
    )
    family = SimpleNamespace(
        id="family_test", name="Test Family", plugin_type="family",
        capabilities={"candidate_generation"}, default_variant_id="main",
        variants=(SimpleNamespace(id="main", name="Main", family_spec="json:family.json"),),
    )
    monkeypatch.setattr(actions, "_plugins", lambda _root: [family])
    monkeypatch.setattr(actions, "is_enabled", lambda _db, _plugin: True)
    monkeypatch.setattr(actions, "strategy_options", lambda *_a, **_k: [{"id": "test"}])
    family_plan = actions.plan_action(
        db, tmp_path, message="Start an AI Hunt for Test Family",
    )
    assert family_plan["target"] == {"plugin_id": "family_test", "variant_id": "main"}
    curve_plan = actions.plan_action(
        db, tmp_path, message="Start an AI Hunt for this curve",
        active_curve_id=curve_id,
    )
    assert curve_plan["target"] == {"curve_id": curve_id}


def test_hunt_continuation_respects_explicit_caps_and_secondary_providers(monkeypatch, tmp_path):
    db = connect(":memory:")
    monkeypatch.setattr(actions, "strategy_options", lambda *_a, **_k: [{"id": "test"}])
    plan = actions.plan_action(
        db, tmp_path,
        message="Start AI Hunt for Z/9Z. Include secondary providers. Keep running after checkpoints for 8 hours, up to 20 Pipelines. Rank goal 4.",
    )
    assert plan["goal_rank"] == 4
    assert plan["target"]["include_secondary_providers"] is True
    assert plan["config"]["hard_stop_minutes"] == 480
    assert plan["config"]["hard_stop_rounds"] == 20


def test_campaign_update_requires_explicit_id_and_confirmation(monkeypatch, tmp_path):
    db = connect(":memory:")
    actions.ensure_manage_schema(db)
    campaign_id = actions.create_campaign(db, name="Original")
    monkeypatch.setattr(actions, "generate_json", lambda *_a, **_k: {
        "action": "update_campaign", "campaign_id": campaign_id,
        "name": "Revised", "campaign_status": "paused",
    })
    with pytest.raises(ValueError, match="Name the campaign ID"):
        actions.plan_action(db, tmp_path, message="Pause my campaign")
    plan = actions.plan_action(db, tmp_path, message=f"Rename and pause campaign #{campaign_id}")
    assert actions.get_campaign(db, campaign_id)["name"] == "Original"
    ctx = SimpleNamespace(project_root=tmp_path, db_path=tmp_path / "test.db")
    actions.execute_action(db, ctx, plan)
    row = actions.get_campaign(db, campaign_id)
    assert (row["name"], row["status"]) == ("Revised", "paused")


def test_job_control_never_targets_an_unmentioned_job(monkeypatch, tmp_path):
    db = connect(":memory:")
    monkeypatch.setattr(actions, "generate_json", lambda *_a, **_k: {
        "action": "pause_job", "job_id": 9,
    })
    monkeypatch.setattr(actions, "get_job", lambda _db, job_id: {
        "id": job_id, "label": "Research search", "status": "running",
    })
    with pytest.raises(ValueError, match="Name the job ID"):
        actions.plan_action(db, tmp_path, message="Pause job #8")
    plan = actions.plan_action(db, tmp_path, message="Pause job #9")
    calls = []
    monkeypatch.setattr(actions, "pause_job", lambda _db, job_id: calls.append(job_id))
    assert calls == []
    ctx = SimpleNamespace(project_root=tmp_path, db_path=tmp_path / "test.db")
    assert actions.execute_action(db, ctx, plan) == "Paused job #9."
    assert calls == [9]


def test_continue_hunt_is_bounded_and_reviewed(monkeypatch, tmp_path):
    db = connect(":memory:")
    actions.ensure_manage_schema(db)
    research_id = actions.create_campaign(db, name="Rank hunt")
    ai_id = actions.create_ai_campaign(
        db, research_campaign_id=research_id, name="Rank hunt", objective="Grow rank",
        target_mode="general", target={"pool_mode": "seeded"},
        config={"max_total_minutes": 60, "max_rounds": 2,
                "hard_stop_minutes": 600, "hard_stop_rounds": 10},
    )
    actions.update_ai_campaign(db, ai_id, status="budget_exhausted")
    monkeypatch.setattr(actions, "generate_json", lambda *_a, **_k: {
        "action": "continue_ai_hunt", "ai_hunt_id": ai_id,
    })
    queued = []
    monkeypatch.setattr(actions, "enqueue_job", lambda *_a, **kwargs: queued.append(kwargs) or 88)
    plan = actions.plan_action(db, tmp_path, message=f"Continue AI Hunt #{ai_id}")
    assert "60 more minutes" in plan["preview"]
    assert queued == []
    ctx = SimpleNamespace(project_root=tmp_path, db_path=tmp_path / "test.db")
    answer = actions.execute_action(db, ctx, plan)
    assert "job #88" in answer
    assert queued[0]["kind"] == "ai_search"


def test_sync_icarm_uses_existing_catalog_job_after_confirmation(monkeypatch, tmp_path):
    db = connect(":memory:")
    monkeypatch.setattr(actions, "generate_json", lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("Sync ICARM should not need Gemini classification")))
    queued = []
    monkeypatch.setattr(actions, "enqueue_job", lambda *_a, **kwargs: queued.append(kwargs) or 55)
    monkeypatch.setattr(actions, "setting_value", lambda _db, _key, fallback: fallback)
    plan = actions.plan_action(db, tmp_path, message="sync icarm")
    assert plan["kind"] == "sync_icarm"
    assert queued == []
    ctx = SimpleNamespace(project_root=tmp_path, db_path=tmp_path / "test.db",
                          detected_science_python=lambda: "/science/python")
    assert actions.execute_action(db, ctx, plan) == "ICARM catalog sync queued as job #55."
    assert queued[0]["kind"] == "catalog_sync"
    assert queued[0]["command"] == ["/science/python", "-m", "rank42.catalog", "--db",
                                    str(ctx.db_path), "sync", "--source", "icarm"]


def test_icarm_sync_duplicate_is_refused(monkeypatch, tmp_path):
    db = connect(":memory:")
    monkeypatch.setattr(actions, "_active_icarm_sync", lambda _db: {"id": 31})
    with pytest.raises(ValueError, match="already active as job #31"):
        actions.plan_action(db, tmp_path, message="Refresh ICARM catalog")
