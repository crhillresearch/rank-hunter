import json
import sqlite3
import types

from rank42 import launch_context
from rank42.ui_pages import common


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeLaunchDB:
    def __init__(self, *, campaign=None, error=None):
        self.campaign = campaign
        self.error = error

    def execute(self, sql, params=()):
        if self.error is not None:
            raise self.error
        if "FROM ui_application_state" in sql:
            if self.campaign is None:
                return _FakeResult(None)
            return _FakeResult({
                "value_json": json.dumps(int(self.campaign["id"])),
            })
        if "FROM research_campaigns" in sql:
            if self.campaign is None or str(self.campaign.get("status", "active")) != "active":
                return _FakeResult(None)
            return _FakeResult(self.campaign)
        raise AssertionError(f"unexpected Launch Context SQL: {sql}")


def _campaign():
    return {
        "id": 17,
        "name": "Rank 31 Neighborhood",
        "created_at": "2026-09-25T12:00:00",
        "status": "active",
    }


def test_resolver_inherits_campaign_and_normalizes_launch_provenance():
    resolved = launch_context.resolve_launch_context(
        _FakeLaunchDB(campaign=_campaign()),
        metadata={
            "plugin_id": "family.demo",
            "variant_id": "v2",
            "pool_id": 44,
        },
        launch_surface="family_search",
        target_mode="family",
        pipeline_run_id=9,
        pipeline_id=3,
        pipeline_name="Deep Search",
        feature_plugin_ids=["feat.a", "feat.b"],
    )

    assert resolved.failure is None
    assert resolved.metadata["campaign_id"] == 17
    assert resolved.metadata["campaign_name"] == "Rank 31 Neighborhood"
    assert resolved.metadata["campaign_created_at"] == "2026-09-25T12:00:00"
    assert resolved.metadata["launch_surface"] == "family_search"
    assert resolved.metadata["plugin_id"] == "family.demo"
    assert resolved.metadata["plugin_variant"] == "v2"
    assert resolved.metadata["target_mode"] == "family"
    assert resolved.metadata["pipeline_run_id"] == 9
    assert resolved.metadata["pipeline_id"] == 3
    assert resolved.metadata["pipeline_name"] == "Deep Search"
    assert resolved.metadata["feature_plugin_ids"] == ["feat.a", "feat.b"]
    assert resolved.metadata["source_pool_id"] == 44


def test_resolver_does_not_auto_attach_paused_current_campaign():
    paused = dict(_campaign(), status="paused")

    resolved = launch_context.resolve_launch_context(
        _FakeLaunchDB(campaign=paused),
        metadata={},
        launch_surface="family_search",
    )

    assert resolved.failure is None
    assert resolved.campaign is None
    assert "campaign_id" not in resolved.metadata
    assert "campaign_name" not in resolved.metadata


def test_resolver_preserves_explicit_campaign_without_active_lookup():
    db = _FakeLaunchDB(
        error=AssertionError("active campaign lookup should be skipped")
    )

    resolved = launch_context.resolve_launch_context(
        db,
        metadata={
            "campaign_id": 88,
            "campaign_name": "Explicit",
        },
        launch_surface="target_search",
    )

    assert resolved.failure is None
    assert resolved.metadata["campaign_id"] == 88
    assert resolved.metadata["campaign_name"] == "Explicit"
    assert resolved.metadata["launch_surface"] == "target_search"


def test_resolver_preserves_transient_campaign_failure_as_metadata():
    resolved = launch_context.resolve_launch_context(
        _FakeLaunchDB(error=sqlite3.OperationalError("database is locked")),
        metadata={"pool_id": 5},
        launch_surface="family_search",
    )

    assert resolved.campaign is None
    assert resolved.failure == {
        "level": "warning",
        "message": "Active campaign temporarily unavailable: database is locked",
    }
    assert resolved.metadata["campaign_context_error"] == resolved.failure
    assert resolved.metadata["source_pool_id"] == 5


def test_ordinary_launch_uses_resolver_and_adds_launch_surface(monkeypatch):
    from rank42 import plugins
    monkeypatch.setattr(
        plugins,
        "apply_search_command_features",
        lambda root, db, command, **kwargs: (list(command), []),
    )

    captured = {}
    monkeypatch.setattr(
        common,
        "enqueue_job",
        lambda db_path, **kwargs: captured.update(kwargs) or 41,
    )
    fake_st = types.SimpleNamespace(
        session_state={},
        warning=lambda _message: None,
        error=lambda _message: None,
        caption=lambda _message: None,
    )
    monkeypatch.setattr(common, "st", fake_st)
    ctx = types.SimpleNamespace(db_path="rank42.db", project_root=".")

    jid = common.launch(
        ctx,
        _FakeLaunchDB(campaign=_campaign()),
        kind="candidate_generate",
        label="Generate",
        command=["python", "-V"],
        metadata={"pool_id": 22},
    )

    assert jid == 41
    assert captured["metadata"]["launch_surface"] == "candidate_generate"
    assert captured["metadata"]["source_pool_id"] == 22
    assert captured["metadata"]["campaign_id"] == 17
    assert captured["metadata"]["campaign_name"] == "Rank 31 Neighborhood"


def test_pipeline_launch_freezes_one_campaign_snapshot_for_run_and_job(monkeypatch):
    from rank42 import pipeline_catalog, pipeline_state

    monkeypatch.setattr(pipeline_catalog, "normalize_pipeline", lambda stages: list(stages))
    monkeypatch.setattr(pipeline_catalog, "validate_pipeline", lambda mode, stages: [])
    captured = {}

    def create_run(db, **kwargs):
        captured["run"] = kwargs
        return 73

    def enqueue(ctx, db, **kwargs):
        captured["job"] = kwargs
        return 91

    monkeypatch.setattr(pipeline_state, "create_pipeline_run", create_run)
    monkeypatch.setattr(common, "setting", lambda *_args, **_kwargs: "python")
    monkeypatch.setattr(common, "_enqueue_resolved_launch", enqueue)
    ctx = types.SimpleNamespace(
        db_path="rank42.db",
        project_root=".",
        detected_science_python=lambda: "python",
    )

    jid = common.launch_with_pipeline(
        ctx,
        _FakeLaunchDB(campaign=_campaign()),
        pipeline_choice={
            "pipeline": {
                "id": 4,
                "name": "Deep Rank",
                "stages": [],
                "config": {"feature_plugin_ids": ["feat.one"]},
            },
            "target_rank": 12,
        },
        target_mode="family",
        target={
            "plugin_id": "family.demo",
            "variant_id": "v1",
            "candidate_pool_id": 33,
        },
        run_config={"ratpoints_backend": "CPU"},
        native_kind="family_search",
        native_label="Family search",
        native_command=["python", "-V"],
        native_metadata={
            "plugin_id": "family.demo",
            "plugin_variant": "v1",
            "pool_id": 33,
        },
    )

    assert jid == 91
    run_config = captured["run"]["run_config"]
    job_metadata = captured["job"]["metadata"]

    for key in (
        "campaign_id",
        "campaign_name",
        "campaign_created_at",
        "launch_surface",
        "plugin_id",
        "plugin_variant",
        "target_mode",
        "feature_plugin_ids",
        "source_pool_id",
    ):
        assert job_metadata[key] == run_config[key]

    assert run_config["campaign_id"] == 17
    assert run_config["launch_surface"] == "family_search"
    assert run_config["source_pool_id"] == 33
    assert run_config["feature_plugin_ids"] == ["feat.one"]

    assert job_metadata["pipeline_run_id"] == 73
    assert job_metadata["pipeline_id"] == 4
    assert job_metadata["pipeline_name"] == "Deep Rank"
    assert job_metadata["builder_override"] is True


def test_pipeline_launch_preserves_campaign_lookup_failure_in_run_and_job(monkeypatch):
    from rank42 import pipeline_catalog, pipeline_state

    monkeypatch.setattr(pipeline_catalog, "normalize_pipeline", lambda stages: list(stages))
    monkeypatch.setattr(pipeline_catalog, "validate_pipeline", lambda mode, stages: [])
    captured = {}

    def create_run(db, **kwargs):
        captured["run"] = kwargs
        return 74

    def enqueue(ctx, db, **kwargs):
        captured["job"] = kwargs
        return 92

    monkeypatch.setattr(pipeline_state, "create_pipeline_run", create_run)
    monkeypatch.setattr(common, "setting", lambda *_args, **_kwargs: "python")
    monkeypatch.setattr(common, "_enqueue_resolved_launch", enqueue)
    warnings = []
    monkeypatch.setattr(
        common,
        "st",
        types.SimpleNamespace(
            warning=warnings.append,
            error=lambda _message: None,
        ),
    )
    ctx = types.SimpleNamespace(
        db_path="rank42.db",
        project_root=".",
        detected_science_python=lambda: "python",
    )

    jid = common.launch_with_pipeline(
        ctx,
        _FakeLaunchDB(error=sqlite3.OperationalError("database is locked")),
        pipeline_choice={
            "pipeline": {"id": 5, "name": "Pipeline", "stages": [], "config": {}},
            "target_rank": 8,
        },
        target_mode="curve",
        target={"curve_id": 10},
        run_config={},
        native_kind="target_search",
        native_label="Target",
        native_command=["python", "-V"],
        native_metadata={},
    )

    assert jid == 92
    expected = {
        "level": "warning",
        "message": "Active campaign temporarily unavailable: database is locked",
    }
    assert captured["run"]["run_config"]["campaign_context_error"] == expected
    assert captured["job"]["metadata"]["campaign_context_error"] == expected
    assert warnings == [
        "Active campaign temporarily unavailable: database is locked "
        "Launching without automatic campaign attachment."
    ]


def test_launch_context_lookup_is_read_only():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        """CREATE TABLE ui_application_state(
               state_key TEXT PRIMARY KEY,
               value_json TEXT NOT NULL,
               updated_at TEXT NOT NULL
           )"""
    )
    db.execute(
        """CREATE TABLE research_campaigns(
               id INTEGER PRIMARY KEY,
               name TEXT NOT NULL,
               status TEXT NOT NULL,
               created_at TEXT NOT NULL
           )"""
    )
    db.execute(
        """INSERT INTO ui_application_state(state_key,value_json,updated_at)
           VALUES(?,?,?)""",
        ("current_campaign_id", json.dumps(17), "now"),
    )
    db.execute(
        "INSERT INTO research_campaigns(id,name,status,created_at) VALUES(?,?,?,?)",
        (17, "Read only", "active", "2026-09-25T12:00:00"),
    )
    db.commit()

    statements = []
    db.set_trace_callback(statements.append)
    resolved = launch_context.resolve_launch_context(
        db,
        metadata={"pool_id": 7},
        launch_surface="family_search",
    )
    db.set_trace_callback(None)

    assert resolved.metadata["campaign_id"] == 17
    assert resolved.metadata["source_pool_id"] == 7
    writes = [
        statement for statement in statements
        if statement.lstrip().upper().startswith(
            ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "REPLACE")
        )
    ]
    assert writes == []
    db.close()


def test_pipeline_launch_honors_explicit_native_campaign_without_lookup(monkeypatch):
    from rank42 import pipeline_catalog, pipeline_state

    monkeypatch.setattr(pipeline_catalog, "normalize_pipeline", lambda stages: list(stages))
    monkeypatch.setattr(pipeline_catalog, "validate_pipeline", lambda mode, stages: [])
    captured = {}

    def create_run(db, **kwargs):
        captured["run"] = kwargs
        return 81

    def enqueue(ctx, db, **kwargs):
        captured["job"] = kwargs
        return 82

    monkeypatch.setattr(pipeline_state, "create_pipeline_run", create_run)
    monkeypatch.setattr(common, "setting", lambda *_args, **_kwargs: "python")
    monkeypatch.setattr(common, "_enqueue_resolved_launch", enqueue)

    ctx = types.SimpleNamespace(
        db_path="rank42.db",
        project_root=".",
        detected_science_python=lambda: "python",
    )
    db = _FakeLaunchDB(
        error=AssertionError("active campaign lookup should be skipped")
    )

    jid = common.launch_with_pipeline(
        ctx,
        db,
        pipeline_choice={
            "pipeline": {"id": 6, "name": "Explicit Campaign", "stages": [], "config": {}},
            "target_rank": 9,
        },
        target_mode="curve",
        target={"curve_id": 12},
        run_config={},
        native_kind="target_search",
        native_label="Target",
        native_command=["python", "-V"],
        native_metadata={
            "campaign_id": 91,
            "campaign_name": "Caller Campaign",
            "campaign_created_at": "2026-09-01T00:00:00",
        },
    )

    assert jid == 82
    for payload in (
        captured["run"]["run_config"],
        captured["job"]["metadata"],
    ):
        assert payload["campaign_id"] == 91
        assert payload["campaign_name"] == "Caller Campaign"
        assert payload["campaign_created_at"] == "2026-09-01T00:00:00"
    assert captured["job"]["metadata"]["pipeline_run_id"] == 81
