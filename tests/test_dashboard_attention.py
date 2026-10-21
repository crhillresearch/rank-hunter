import inspect
import sqlite3

import rank42.dashboard_snapshot as dashboard_snapshot
from rank42.ui_pages import dashboard
from rank42.ui_store import create_job, ensure_ui_schema, list_jobs, update_job


def _campaign():
    return {
        "id": 7,
        "name": "Rank target",
        "target_rank": 12,
        "target_reached": True,
        "analysis_issues": [
            {
                "curve_id": 41,
                "rigorous_lower": 10,
                "inconsistent": False,
                "actionable_points": 2,
                "witness_gap": 1,
            }
        ],
    }


def test_attention_snapshot_aggregates_authoritative_cross_system_issues(monkeypatch):
    monkeypatch.setattr(
        dashboard_snapshot,
        "list_curve_research_states",
        lambda db: [
            {
                "curve_id": 40,
                "rank_inconsistent": True,
                "rigorous_lower": 9,
                "rigorous_upper": 8,
            }
        ],
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "list_jobs",
        lambda db, limit=100: [
            {"id": 5, "status": "failed", "label": "Deep search", "kind": "search"}
        ],
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "list_pipeline_runs_readonly",
        lambda db, limit=100: [
            {"id": 6, "status": "failed", "pipeline_name": "High-rank hunt"}
        ],
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "queue_counts",
        lambda db: {"queued": 3},
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "dispatcher_status",
        lambda db: {"healthy": False, "status": "stale", "worker_id": "worker-1"},
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "_plugin_health_snapshot",
        lambda db, root: {
            "issues": [
                {
                    "kind": "needs_revalidation",
                    "plugin_id": "family.demo",
                    "plugin_type": "family",
                    "name": "Demo Family",
                    "reason": "Installed content changed.",
                }
            ]
        },
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "list_schedule_errors",
        lambda db, limit=100: [
            {"id": 8, "last_error": "schedule boom"}
        ],
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "list_schedule_occurrences",
        lambda db, limit=100: [],
    )

    items = dashboard_snapshot.dashboard_attention_snapshot(
        object(),
        "/project",
        campaign=_campaign(),
        diagnostics_result={
            "generated_at": "2026-09-27T12:00:00+00:00",
            "overall": "ATTENTION REQUIRED",
            "verification_mode": "fast",
            "deep_checks_complete": False,
            "counts": {"pass": 4, "warn": 1, "fail": 2},
        },
        limit=50,
    )

    kinds = {item["kind"] for item in items}
    assert {
        "evidence_conflict",
        "campaign_target_review",
        "witness_gap",
        "unresolved_exact_points",
        "job_failure",
        "pipeline_failure",
        "dispatcher_unhealthy",
        "needs_revalidation",
        "schedule_failure",
        "diagnostics_failure",
    } <= kinds

    required = {
        "severity",
        "title",
        "reason",
        "page",
        "object_type",
        "object_id",
        "kind",
    }
    assert all(required <= set(item) for item in items)


def test_attention_snapshot_is_empty_when_authorities_are_healthy(monkeypatch):
    monkeypatch.setattr(dashboard_snapshot, "list_curve_research_states", lambda db: [])
    monkeypatch.setattr(dashboard_snapshot, "list_jobs", lambda db, limit=100: [])
    monkeypatch.setattr(
        dashboard_snapshot,
        "list_pipeline_runs_readonly",
        lambda db, limit=100: [],
    )
    monkeypatch.setattr(dashboard_snapshot, "queue_counts", lambda db: {})
    monkeypatch.setattr(
        dashboard_snapshot,
        "dispatcher_status",
        lambda db: {"healthy": True, "status": "running"},
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "_plugin_health_snapshot",
        lambda db, root: {"issues": []},
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "list_schedule_errors",
        lambda db, limit=100: [],
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "list_schedule_occurrences",
        lambda db, limit=100: [],
    )

    items = dashboard_snapshot.dashboard_attention_snapshot(
        object(),
        "/project",
        campaign=None,
        diagnostics_result=None,
    )

    assert items == []


def test_dashboard_plugin_health_suppresses_archived_plugins(monkeypatch):
    class FakePlugin:
        def __init__(self, plugin_id, name, plugin_type="family"):
            self.id = plugin_id
            self.name = name
            self.plugin_type = plugin_type

    archived = FakePlugin("family.archived", "Archived Family")
    active = FakePlugin("family.active", "Active Family")

    monkeypatch.setattr(dashboard_snapshot, "Plugin", FakePlugin)
    monkeypatch.setattr(
        dashboard_snapshot,
        "discover_plugins",
        lambda root: [archived, active],
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "is_archived",
        lambda db, plugin: plugin.id == archived.id,
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "is_enabled",
        lambda db, plugin: plugin.id == active.id,
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "plugin_state",
        lambda db, plugin_id: {"status": "disabled"},
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "validation_freshness",
        lambda db, plugin: {
            "state": (
                "needs_revalidation"
                if plugin.id == archived.id
                else "ready"
            )
        },
    )

    class FakeDB:
        def execute(self, sql):
            assert "plugin_states" in sql
            return self

        def fetchall(self):
            return [{"plugin_id": archived.id}]

    health = dashboard_snapshot._plugin_health_snapshot(FakeDB(), "/project")

    assert health["installed"] == 1
    assert health["enabled"] == 1
    assert health["attention"] == 0
    assert health["needs_revalidation"] == 0
    assert health["counts"]["family"] == {"installed": 1, "enabled": 1}
    assert health["active_preview"] == [
        {"id": active.id, "name": active.name, "type": active.plugin_type}
    ]


def test_dashboard_plugin_health_suppresses_identifiable_archived_manifest(monkeypatch):
    class FakeDB:
        def execute(self, sql):
            assert "plugin_states" in sql
            return self

        def fetchall(self):
            return [{"plugin_id": "family.archived"}]

    monkeypatch.setattr(dashboard_snapshot, "discover_plugins", lambda root: [
        {
            "id": "family.archived",
            "error": "broken manifest",
        }
    ])

    health = dashboard_snapshot._plugin_health_snapshot(FakeDB(), "/project")

    assert health["installed"] == 0
    assert health["invalid_manifests"] == 0
    assert health["attention"] == 0
    assert health["issues"] == []


def test_attention_snapshot_deduplicates_schedule_failures(monkeypatch):
    monkeypatch.setattr(dashboard_snapshot, "list_curve_research_states", lambda db: [])
    monkeypatch.setattr(dashboard_snapshot, "list_jobs", lambda db, limit=100: [])
    monkeypatch.setattr(
        dashboard_snapshot,
        "list_pipeline_runs_readonly",
        lambda db, limit=100: [],
    )
    monkeypatch.setattr(dashboard_snapshot, "queue_counts", lambda db: {})
    monkeypatch.setattr(
        dashboard_snapshot,
        "dispatcher_status",
        lambda db: {"healthy": True},
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "_plugin_health_snapshot",
        lambda db, root: {"issues": []},
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "list_schedule_errors",
        lambda db, limit=100: [{"id": 9, "last_error": "top-level error"}],
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "list_schedule_occurrences",
        lambda db, limit=100: [
            {
                "schedule_id": 9,
                "state": "failed",
                "last_error": "occurrence error",
            }
        ],
    )

    items = dashboard_snapshot.dashboard_attention_snapshot(
        object(),
        "/project",
        campaign=None,
        limit=20,
    )

    schedule_items = [
        item for item in items if item["object_type"] == "schedule"
    ]
    assert len(schedule_items) == 1
    assert schedule_items[0]["object_id"] == "9"


def test_attention_ignores_intentional_operator_stop_states():
    operations = {
        "jobs": [
            {"id": 1, "status": "stopped", "label": "Stopped", "kind": "search"},
            {"id": 2, "status": "killed", "label": "Killed", "kind": "search"},
            {
                "id": 3,
                "status": "failed",
                "label": "Stop race",
                "kind": "search",
                "metadata_json": '{"stop_requested_at":"2026-09-30T00:00:00+00:00"}',
            },
        ],
        "pipeline_runs": [
            {"id": 4, "status": "paused", "pipeline_name": "Paused"},
            {"id": 5, "status": "killed", "pipeline_name": "Killed"},
        ],
        "queue_counts": {},
        "dispatcher": {"healthy": True},
        "plugins": {"issues": []},
        "schedules": [],
        "schedule_occurrences": [],
    }

    items = dashboard_snapshot.dashboard_attention_snapshot(
        object(),
        "/project",
        campaign=None,
        operations=operations,
        rank_conflicts=[],
    )

    assert items == []


def test_attention_does_not_raise_dispatcher_failure_while_starting():
    operations = {
        "jobs": [],
        "pipeline_runs": [],
        "queue_counts": {"queued": 3},
        "dispatcher": {
            "healthy": False,
            "state": "starting",
            "label": "Starting",
        },
        "plugins": {"issues": []},
        "schedules": [],
        "schedule_occurrences": [],
    }

    items = dashboard_snapshot.dashboard_attention_snapshot(
        object(),
        "/project",
        campaign=None,
        operations=operations,
        rank_conflicts=[],
    )

    assert not any(item["kind"] == "dispatcher_unhealthy" for item in items)


def test_terminal_job_attention_can_be_acknowledged_without_rewriting_job(tmp_path):
    db = sqlite3.connect(tmp_path / "attention.db")
    db.row_factory = sqlite3.Row
    ensure_ui_schema(db)
    job_id = create_job(
        db,
        kind="search",
        label="Failed search",
        command=["python", "-c", "raise SystemExit(1)"],
        cwd=tmp_path,
        log_path=tmp_path / "attention.log",
    )
    update_job(db, job_id, status="failed", exit_code=1)

    def snapshot():
        return dashboard_snapshot.dashboard_attention_snapshot(
            db,
            tmp_path,
            campaign=None,
            operations={
                "jobs": list_jobs(db, limit=100),
                "pipeline_runs": [],
                "queue_counts": {},
                "dispatcher": {"healthy": True},
                "plugins": {"issues": []},
                "schedules": [],
                "schedule_occurrences": [],
            },
            rank_conflicts=[],
        )

    items = snapshot()
    assert len(items) == 1
    assert items[0]["acknowledgeable"] is True

    dashboard_snapshot.acknowledge_dashboard_attention(db, items[0])

    assert snapshot() == []
    assert list_jobs(db, limit=1)[0]["status"] == "failed"
    db.close()


def test_dashboard_attention_callback_does_not_capture_render_connection():
    callback = inspect.getsource(dashboard._acknowledge_attention_item)
    renderer = inspect.getsource(dashboard._render_attention_item)
    panel = inspect.getsource(dashboard._attention_panel)
    dashboard_render = inspect.getsource(dashboard._render_dashboard)

    assert "def _acknowledge_attention_item(db_path, item):" in callback
    assert "connect_existing(db_path)" in callback
    assert "finally:" in callback
    assert "live.close()" in callback
    assert "def _render_attention_item(db_path, item, index):" in renderer
    assert "args=(db_path, item)" in renderer
    assert "def _attention_panel(db_path, items):" in panel
    assert "_render_attention_item(db_path, item, index)" in panel
    assert "_attention_panel(ctx.db_path, attention)" in dashboard_render


def test_attention_panel_is_dynamic_through_three_items_then_scrolls():
    panel = inspect.getsource(dashboard._attention_panel)
    item = inspect.getsource(dashboard._render_attention_item)
    opener = inspect.getsource(dashboard._open_attention_item)

    assert "Reserved for alerts and review items" not in panel
    assert "if len(items) <= 3:" in panel
    assert "st.container(height=520" in panel
    assert panel.count("for index, item in enumerate(items):") == 2
    assert "_ATTENTION_PREVIEW_LIMIT" not in panel
    assert "dashboard_attention_expanded" not in panel
    assert "Show fewer" not in panel
    assert "Show " not in panel
    assert 'item["severity"]' in item
    assert 'item["title"]' in item
    assert 'item["reason"]' in item
    assert 'item["page"]' in item
    assert 'item["object_id"]' in item
    assert '"Acknowledge"' in item
    assert "on_click=_acknowledge_attention_item" in item
    assert "args=(db_path, item)" in item

    acknowledge = inspect.getsource(dashboard._acknowledge_attention_item)
    assert "connect_existing(db_path)" in acknowledge
    assert "acknowledge_dashboard_attention(live, item)" in acknowledge
    assert "live.close()" in acknowledge
    assert "acknowledge_dashboard_attention(db, item)" not in acknowledge
    assert not hasattr(dashboard, "_toggle_dashboard_attention")

    assert 'st.session_state["analyze_curve_id"]' in opener
    assert 'st.session_state["manage_campaign_id"]' in opener
    assert 'st.session_state["manage_job_id"]' in opener
    assert 'st.session_state["byo_focus_run_id"]' in opener
    assert 'st.session_state["manage_schedule_id"]' in opener
    assert 'st.session_state["jobs_section_pending"] = "Queue"' in opener
