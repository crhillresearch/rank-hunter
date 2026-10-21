from types import SimpleNamespace

import rank42.dashboard_snapshot as dashboard_snapshot


class _Cursor:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return {"n": self.value}


class _DB:
    def __init__(self, *, points=0, searched=0, unsearched=0, pools=3):
        self.points = points
        self.searched = searched
        self.unsearched = unsearched
        self.pools = pools

    def execute(self, sql, params=()):
        text = " ".join(str(sql).split())
        if "FROM points" in text:
            return _Cursor(self.points)
        if "status='unsearched'" in text:
            return _Cursor(self.unsearched)
        if "status!='unsearched'" in text:
            return _Cursor(self.searched)
        if "FROM candidate_pools" in text:
            return _Cursor(self.pools)
        raise AssertionError(f"unexpected SQL: {sql}")


def test_dashboard_research_snapshot_uses_shared_rank_authorities(monkeypatch):
    db = _DB(points=11, searched=23)
    monkeypatch.setattr(dashboard_snapshot, "list_curve_research_states", lambda db_: [])
    rank = {"best_rigorous": 9}
    certification = {"total": 2, "counts": {"exact": 1}}
    frontier = [{"id": 7}]

    monkeypatch.setattr(dashboard_snapshot, "dashboard_rank_summary", lambda db_, **kwargs: rank)
    monkeypatch.setattr(
        dashboard_snapshot,
        "dashboard_rank_certification",
        lambda db_, **kwargs: certification,
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "dashboard_frontier_rows",
        lambda db_, **kwargs: frontier,
    )

    result = dashboard_snapshot.dashboard_research_snapshot(db)

    assert result["rank"] is rank
    assert result["certification"] is certification
    assert result["frontier"] is frontier
    assert result["point_count"] == 11
    assert result["searched_candidates"] == 23


def test_dashboard_overview_snapshot_uses_campaign_and_planner_brief(monkeypatch):
    db = _DB(points=3, searched=5)
    campaign = {
        "id": 4,
        "name": "Rank jump",
        "objective": "Find a stronger fiber",
        "family": "demo",
        "plugin_id": "demo-family",
        "variant_id": "main",
        "target_rank": 12,
    }
    progress = {"label": "Rigorous ≥10 / target ≥12"}
    next_action = {
        "title": "Certify unresolved points",
        "why": "Two exact points remain unresolved.",
        "page": "Analyze",
    }
    brief = {
        "next_action": next_action,
        "frontier_milestones": [{"label": "Reached rank 10"}],
        "target_reached": False,
        "target_blocked_by_conflict": False,
    }

    monkeypatch.setattr(dashboard_snapshot, "current_campaign", lambda db_: campaign)
    monkeypatch.setattr(
        dashboard_snapshot,
        "campaign_progress_summary",
        lambda db_, campaign_id: progress,
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "campaign_research_brief",
        lambda db_, campaign_id: brief,
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "dashboard_research_snapshot",
        lambda db_: {"rank": {"best_rigorous": 10}},
    )

    result = dashboard_snapshot.dashboard_overview_snapshot(db)

    assert result["campaign"]["id"] == 4
    assert result["campaign"]["progress"] is progress
    assert result["campaign"]["next_action"] is next_action
    assert result["campaign"]["latest_milestone"] == {"label": "Reached rank 10"}
    assert result["research"]["rank"]["best_rigorous"] == 10


def test_dashboard_overview_snapshot_handles_no_current_campaign(monkeypatch):
    monkeypatch.setattr(dashboard_snapshot, "current_campaign", lambda db_: None)
    monkeypatch.setattr(
        dashboard_snapshot,
        "dashboard_research_snapshot",
        lambda db_: {"rank": {}},
    )

    result = dashboard_snapshot.dashboard_overview_snapshot(object())

    assert result["campaign"] is None
    assert result["research"] == {"rank": {}}


def test_dashboard_operations_snapshot_uses_jobs_pipeline_queue_and_health_authorities(
    monkeypatch,
):
    db = _DB(searched=8, unsearched=2)
    jobs = [
        {"status": "running"},
        {"status": "succeeded"},
        {"status": "failed"},
    ]
    runs = [
        {"status": "running"},
        {"status": "paused"},
        {"status": "interrupted"},
    ]
    plugins = {"attention": 1, "enabled": 2}

    monkeypatch.setattr(
        dashboard_snapshot,
        "list_jobs",
        lambda db_, limit=100: jobs,
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "list_pipeline_runs_readonly",
        lambda db_, limit=100: runs,
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "job_status_counts",
        lambda db_: {"running": 1, "succeeded": 1, "failed": 1},
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "pipeline_run_status_counts_readonly",
        lambda db_: {"running": 1, "paused": 1, "interrupted": 1},
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "queue_counts",
        lambda db_: {"queued": 2, "running": 1},
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "dispatcher_status",
        lambda db_: {"healthy": True, "status": "running"},
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "list_schedule_errors",
        lambda db_, limit=100: [
            {"id": 11, "enabled": 0, "last_error": "schedule error"},
        ],
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "list_schedule_occurrences",
        lambda db_, limit=100: [
            {
                "schedule_id": 11,
                "state": "failed",
                "last_error": "occurrence error",
            }
        ],
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "schedule_status_summary",
        lambda db_: {"total": 2, "enabled": 1, "disabled": 1, "errors": 1},
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "_plugin_health_snapshot",
        lambda db_, root: plugins,
    )

    result = dashboard_snapshot.dashboard_operations_snapshot(
        db,
        "/project",
        diagnostics_result={
            "generated_at": "2026-09-27T12:00:00+00:00",
            "overall": "FAST CHECKS READY",
            "verification_mode": "fast",
            "deep_checks_complete": False,
            "counts": {"pass": 7, "warn": 1, "fail": 0},
        },
    )

    assert result["jobs"] is jobs
    assert result["job_counts"] == {"active": 1, "completed": 1, "failed": 1}
    assert result["pipeline_run_counts"] == {
        "active": 1,
        "paused": 1,
        "completed": 0,
        "failed": 1,
    }
    assert result["queue_counts"] == {"queued": 2, "running": 1}
    assert result["queue_waiting"] == 2
    assert result["queue_claimed"] == 0
    assert result["queue_running"] == 1
    assert result["dispatcher"]["healthy"] is True
    assert result["dispatcher"]["state"] == "running"
    assert result["dispatcher"]["label"].startswith("Running")
    assert result["scheduler"] == {
        "total": 2,
        "enabled": 1,
        "disabled": 1,
        "errors": 1,
    }
    assert result["candidate_counts"] == {
        "total": 10,
        "backlog": 2,
        "searched": 8,
    }
    assert result["pool_count"] == 3
    assert result["plugins"] is plugins
    assert result["diagnostics"]["overall"] == "FAST CHECKS READY"


def test_dashboard_plugin_health_uses_plugin_lifecycle_and_validation(monkeypatch):
    class FakePlugin:
        def __init__(self, plugin_id, name, plugin_type):
            self.id = plugin_id
            self.name = name
            self.plugin_type = plugin_type

    family = FakePlugin("family", "Family", "family")
    extension = FakePlugin("extension", "Extension", "extension")
    invalid_manifest = {"error": "bad manifest"}

    monkeypatch.setattr(dashboard_snapshot, "Plugin", FakePlugin)
    monkeypatch.setattr(
        dashboard_snapshot,
        "discover_plugins",
        lambda root: [family, extension, invalid_manifest],
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "is_enabled",
        lambda db, plugin: plugin.id == "family",
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "plugin_state",
        lambda db, plugin_id: (
            {"status": "invalid"} if plugin_id == "extension" else None
        ),
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "validation_freshness",
        lambda db, plugin: {"state": "ready"},
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "is_archived",
        lambda db, plugin: False,
    )

    class FakeResult:
        def fetchall(self):
            return []

    class FakeDB:
        def execute(self, sql, params=()):
            assert "status='archived'" in sql
            assert params == ()
            return FakeResult()

    result = dashboard_snapshot._plugin_health_snapshot(FakeDB(), "/project")

    assert result["installed"] == 2
    assert result["enabled"] == 1
    assert result["invalid_manifests"] == 1
    assert result["invalid_states"] == 1
    assert result["attention"] == 2
    assert result["active_preview"] == [
        {"id": "family", "name": "Family", "type": "family"}
    ]


def test_cached_diagnostics_snapshot_is_observational_only():
    assert dashboard_snapshot._cached_diagnostics_snapshot(None) is None
    result = dashboard_snapshot._cached_diagnostics_snapshot(
        {
            "generated_at": "2026-09-27T12:00:00+00:00",
            "overall": "DEEP CHECKS READY",
            "verification_mode": "deep",
            "deep_checks_complete": True,
            "counts": {"pass": 10, "warn": 0, "fail": 0},
            "checks": [{"id": "ignored"}],
        }
    )
    assert result == {
        "generated_at": "2026-09-27T12:00:00+00:00",
        "overall": "DEEP CHECKS READY",
        "verification_mode": "deep",
        "deep_checks_complete": True,
        "counts": {"pass": 10, "warn": 0, "fail": 0},
    }
