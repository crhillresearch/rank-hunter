import ast
import json
from pathlib import Path
import sqlite3

import pytest

from rank42.settings_registry import (
    RUNTIME_DEFAULT,
    SETTING_SCOPES,
    SETTING_SPECS,
    USER_SETTING,
    resolve_setting,
    save_setting_value,
    science_python_bootstrap_value,
    seed_setting_if_missing,
    setting_inventory,
    setting_spec,
    setting_value,
)


EXPECTED_KEYS = {
    "deep_cert_timeout",
    "mwrank_rank_timeout",
    "pari_rank_timeout",
    "queue_max_workers",
    "queue_resource_limits",
    "ratpoints",
    "ratpoints_backend",
    "ratpoints_gpu",
    "science_python",
    "ui_theme",
    "ui_appearance",
}


def _literal_setting_calls():
    root = Path(__file__).resolve().parents[1] / "rank42"
    names = {
        "get_setting",
        "set_setting",
        "setting",
        "save_setting",
        "setting_value",
        "save_setting_value",
        "seed_setting_if_missing",
    }
    found = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None
            )
            if name not in names or len(node.args) < 2:
                continue
            key = node.args[1]
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                found.append(
                    (
                        str(path.relative_to(root.parent)),
                        int(node.lineno),
                        str(name),
                        str(key.value),
                    )
                )
    return found


def test_registry_inventories_every_live_literal_setting_key():
    calls = _literal_setting_calls()
    live = {key for _, _, _, key in calls}
    assert live == EXPECTED_KEYS
    assert set(SETTING_SPECS) == EXPECTED_KEYS


def test_setting_scopes_cover_user_and_runtime_configuration_only():
    assert SETTING_SCOPES == {
        USER_SETTING,
        RUNTIME_DEFAULT,
    }
    assert setting_spec("ui_theme").scope == USER_SETTING
    assert setting_spec("ui_appearance").scope == USER_SETTING

    for key in {
        "science_python",
        "ratpoints",
        "ratpoints_gpu",
        "ratpoints_backend",
        "pari_rank_timeout",
        "mwrank_rank_timeout",
        "deep_cert_timeout",
        "queue_max_workers",
        "queue_resource_limits",
    }:
        assert setting_spec(key).scope == RUNTIME_DEFAULT

def test_registry_records_current_defaults_and_runtime_probe_policy():
    assert setting_spec("ui_appearance").default_value == "light"
    assert setting_spec("ui_appearance").validator == "enum:light,dark"
    assert setting_spec("ratpoints_backend").default_value == "CPU"
    assert setting_spec("pari_rank_timeout").default_value == 300
    assert setting_spec("mwrank_rank_timeout").default_value == 300
    assert setting_spec("deep_cert_timeout").default_value == 900
    assert setting_spec("queue_max_workers").default_value == 2
    assert setting_spec("queue_resource_limits").default_value == {
        "gpu_ratpoints": 1,
        "sage_heavy": 2,
        "database_maintenance": 1,
    }

    assert setting_spec("science_python").validator == "runtime_probe_required"
    assert setting_spec("ratpoints").validator == "runtime_probe_required"
    assert setting_spec("ratpoints_gpu").validator == "runtime_probe_optional"


def test_application_state_keys_are_not_settings_anymore():
    for key in {
        "active_campaign_id",
        "candidate_file",
        "batch_offset",
        "dispatcher_heartbeat",
    }:
        assert key not in SETTING_SPECS
        with pytest.raises(KeyError, match="unknown Rank Hunter setting"):
            setting_spec(key)


def test_icarm_secret_is_intentionally_outside_ui_settings_registry():
    assert all(spec.secret is False for spec in SETTING_SPECS.values())
    assert "icarm_token" not in SETTING_SPECS
    assert "token" not in SETTING_SPECS


def test_application_state_callers_do_not_use_settings_storage():
    root = Path(__file__).resolve().parents[1]
    launch_source = (root / "rank42" / "launch_context.py").read_text(
        encoding="utf-8"
    )
    store_source = (root / "rank42" / "ui_store.py").read_text(
        encoding="utf-8"
    )

    assert "get_application_state(" in launch_source
    assert '"current_campaign_id",' in launch_source
    assert '"active_campaign_id",' not in launch_source
    assert "FROM ui_settings" not in launch_source

    assert 'get_application_state(db, "candidate_file", source)' in store_source
    assert 'get_application_state(db, "batch_offset", start)' in store_source
    assert 'set_application_state(db, "batch_offset", next_offset)' in store_source
    assert 'get_setting(db, "candidate_file"' not in store_source
    assert 'get_setting(db, "batch_offset"' not in store_source
    assert 'set_setting(db, "batch_offset"' not in store_source


def test_inventory_is_stable_serializable_metadata():
    rows = setting_inventory()
    assert [row["key"] for row in rows] == sorted(EXPECTED_KEYS)
    assert {row["scope"] for row in rows} == SETTING_SCOPES
    assert all(row["description"] for row in rows)



def _settings_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        """CREATE TABLE ui_settings (
               key TEXT PRIMARY KEY,
               value_json TEXT NOT NULL,
               updated_at TEXT NOT NULL
           )"""
    )
    return db


def test_typed_resolver_uses_registry_default_and_explicit_dynamic_default():
    db = _settings_db()
    try:
        assert setting_value(db, "pari_rank_timeout") == 300
        assert setting_value(db, "science_python", "/marker/python") == (
            "/marker/python"
        )

        resolved = resolve_setting(db, "science_python", "/marker/python")
        assert resolved.present is False
        assert resolved.valid is True
        assert resolved.source == "default"
    finally:
        db.close()


def test_typed_resolver_falls_back_without_mutating_invalid_stored_value():
    db = _settings_db()
    try:
        db.execute(
            "INSERT INTO ui_settings(key,value_json,updated_at) VALUES(?,?,?)",
            ("pari_rank_timeout", json.dumps("not-an-int"), "now"),
        )
        db.commit()

        resolved = resolve_setting(db, "pari_rank_timeout")
        assert resolved.present is True
        assert resolved.valid is False
        assert resolved.value == 300
        assert resolved.source == "default"

        raw = db.execute(
            "SELECT value_json FROM ui_settings WHERE key='pari_rank_timeout'"
        ).fetchone()
        assert json.loads(raw["value_json"]) == "not-an-int"
    finally:
        db.close()


def test_typed_writer_normalizes_enum_and_rejects_invalid_shape():
    db = _settings_db()
    try:
        assert save_setting_value(db, "ratpoints_backend", "gpu") == "GPU"
        assert setting_value(db, "ratpoints_backend") == "GPU"

        with pytest.raises(ValueError, match="between 1 and 32"):
            save_setting_value(db, "queue_max_workers", 0)

        assert resolve_setting(db, "queue_max_workers").present is False
    finally:
        db.close()


def test_bootstrap_seeds_only_absent_rows_and_never_overwrites_existing_state():
    db = _settings_db()
    try:
        assert seed_setting_if_missing(
            db, "science_python", "/bootstrap/python"
        ) is True
        assert setting_value(db, "science_python") == "/bootstrap/python"

        db.execute(
            "UPDATE ui_settings SET value_json=? WHERE key='science_python'",
            (json.dumps(123),),
        )
        db.commit()

        assert seed_setting_if_missing(
            db, "science_python", "/new-marker/python"
        ) is False

        raw = db.execute(
            "SELECT value_json FROM ui_settings WHERE key='science_python'"
        ).fetchone()
        assert json.loads(raw["value_json"]) == 123

        resolved = resolve_setting(
            db, "science_python", "/runtime-fallback/python"
        )
        assert resolved.present is True
        assert resolved.valid is False
        assert resolved.value == "/runtime-fallback/python"
    finally:
        db.close()


def test_settings_page_and_shell_use_typed_facade_for_migrated_callers():
    root = Path(__file__).resolve().parents[1]
    settings_source = (
        root / "rank42" / "ui_pages" / "settings_page.py"
    ).read_text(encoding="utf-8")
    shell_source = (root / "rank42" / "ui.py").read_text(encoding="utf-8")

    assert "setting_value(db," in settings_source
    assert "save_setting_value(db," in settings_source
    assert "from .common import title, setting, save_setting" not in settings_source

    assert 'setting_value(db, "ui_theme"' not in shell_source
    assert 'setting_value(db, "ui_appearance", "light")' in shell_source
    assert 'seed_setting_if_missing(' in shell_source
    assert '"science_python",' in shell_source
    assert '"ratpoints",' in shell_source
    assert '"ratpoints_gpu",' in shell_source
    assert "get_setting(db," not in shell_source
    assert "set_setting(db," not in shell_source



def test_settings_runtime_controls_enforce_shared_probe_policy():
    root = Path(__file__).resolve().parents[1]
    source = (
        root / "rank42" / "ui_pages" / "settings_page.py"
    ).read_text(encoding="utf-8")

    assert '"Test runtimes"' in source
    assert "probe_runtime_bundle(" in source
    assert "required_runtime_failures(probes)" in source
    assert "Runtime settings were not saved" in source
    assert "Optional GPU ratpoints is " in source
    assert "unavailable, so GPU searches cannot launch" in source
    assert "save_setting_value(" in source


def test_settings_page_uses_owned_subviews_without_diagnostics_handoff():
    root = Path(__file__).resolve().parents[1]
    source = (
        root / "rank42" / "ui_pages" / "settings_page.py"
    ).read_text(encoding="utf-8")

    assert '["Runtimes", "Services", "About"]' in source
    assert 'key="settings-view-tabs"' in source
    assert 'variant="line"' in source
    assert 'st.session_state["settings_view"] = choice' in source
    assert '"Open Diagnostics"' not in source
    assert 'st.session_state["rh_page"] = "Diagnostics"' not in source

    assert '"queue_max_workers"' not in source
    assert '"queue_resource_limits"' not in source
    assert '"Start dispatcher"' not in source
    assert '"Restart dispatcher service"' not in source
    assert '"Scheduled"' not in source


def test_settings_page_shows_runtime_status_without_eager_health_probe():
    root = Path(__file__).resolve().parents[1]
    source = (
        root / "rank42" / "ui_pages" / "settings_page.py"
    ).read_text(encoding="utf-8")

    assert "def _runtime_resolution(values):" in source
    assert "probe_runtime_setting(" in source
    assert "run_probe=False" in source
    assert 'st.markdown("#### Runtime status")' in source
    assert '"Default point engine"' in source
    assert '"Required runtimes"' in source
    assert '"Optional GPU"' in source
    assert '"Active point engine"' in source
    assert '"Configured"' not in source[source.index("def _render_effective_runtime_summary"):source.index("def _runtime_settings")]
    assert '"Resolved executable"' not in source[source.index("def _render_effective_runtime_summary"):source.index("def _runtime_settings")]
    assert '"Test runtimes"' in source
    assert "probe_runtime_bundle(" in source


def test_settings_page_keeps_icarm_secret_outside_ui_settings():
    root = Path(__file__).resolve().parents[1]
    source = (
        root / "rank42" / "ui_pages" / "settings_page.py"
    ).read_text(encoding="utf-8")

    assert "read_token(ctx.project_root)" in source
    assert "write_token(ctx.project_root, token)" in source
    assert "delete_local_token(ctx.project_root)" in source
    assert '"ICARM token"' in source
    assert 'save_setting_value(db, "icarm' not in source


def test_queue_and_diagnostics_callers_use_typed_settings_facade():
    root = Path(__file__).resolve().parents[1]
    ui_store_source = (root / "rank42" / "ui_store.py").read_text(
        encoding="utf-8"
    )
    dispatcher_source = (root / "rank42" / "dispatcher.py").read_text(
        encoding="utf-8"
    )
    queue_source = (
        root / "rank42" / "ui_pages" / "jobs_queue_panel.py"
    ).read_text(encoding="utf-8")
    diagnostics_source = (root / "rank42" / "diagnostics.py").read_text(
        encoding="utf-8"
    )

    assert 'setting_value(db, "queue_resource_limits", {})' in ui_store_source
    assert 'setting_value(db, "queue_max_workers", 2)' in ui_store_source
    assert 'get_setting(db, "queue_resource_limits"' not in ui_store_source

    assert 'setting_value(db, "queue_max_workers", 2)' in dispatcher_source
    assert 'get_setting(db, "queue_max_workers"' not in dispatcher_source

    assert 'setting_value(db, "queue_max_workers", 2)' in queue_source
    assert 'save_setting_value(db, "queue_max_workers", workers)' in queue_source
    assert '"queue_resource_limits",' in queue_source
    assert 'get_setting(db, "queue_max_workers"' not in queue_source
    assert 'set_setting(db, "queue_max_workers"' not in queue_source

    assert "from rank42.settings_registry import setting_value" in diagnostics_source
    assert "from rank42.ui_store import get_setting" not in diagnostics_source
    assert "return setting_value(db, key, default)" in diagnostics_source



def test_low_level_settings_storage_is_private_to_registry_and_schema_migration():
    root = Path(__file__).resolve().parents[1] / "rank42"
    low_level_calls = []
    raw_sql_files = set()
    sql_needles = (
        "FROM ui_settings",
        "INTO ui_settings",
        "UPDATE ui_settings",
        "DELETE FROM ui_settings",
    )

    for path in sorted(root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        relative = str(path.relative_to(root.parent))
        if any(needle in source for needle in sql_needles):
            raw_sql_files.add(relative)

        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None
            )
            if name in {"get_setting", "set_setting"}:
                low_level_calls.append((relative, name))

    # Registered core settings must go through settings_registry. plugin_api
    # intentionally keeps a compatibility fallback for unregistered
    # plugin-owned keys, but checks SETTING_SPECS first so core settings still
    # use the typed resolver.
    assert set(low_level_calls) == {
        ("rank42/plugin_api.py", "get_setting"),
        ("rank42/settings_registry.py", "set_setting"),
    }
    assert raw_sql_files == {
        "rank42/settings_registry.py",
        "rank42/ui_store.py",
    }


def test_user_and_runtime_defaults_do_not_bypass_typed_resolver():
    bypass = []
    for path, line, name, key in _literal_setting_calls():
        if name not in {"get_setting", "set_setting"}:
            continue
        if setting_spec(key).scope in {USER_SETTING, RUNTIME_DEFAULT}:
            bypass.append((path, line, name, key))
    assert bypass == []


def test_common_compatibility_facade_and_jobs_runtime_read_are_typed():
    root = Path(__file__).resolve().parents[1]
    common_source = (
        root / "rank42" / "ui_pages" / "common.py"
    ).read_text(encoding="utf-8")
    jobs_source = (
        root / "rank42" / "ui_pages" / "jobs_page.py"
    ).read_text(encoding="utf-8")

    assert "return setting_value(db, key, default)" in common_source
    assert "return save_setting_value(db, key, value)" in common_source
    assert "from rank42.ui_store import latest_candidate_done" in common_source
    assert "get_setting" not in common_source
    assert "set_setting" not in common_source

    assert 'setting_value(db, "science_python"' in jobs_source
    assert "from rank42.settings_registry import setting_value" in jobs_source
    assert 'get_setting(db, "science_python"' not in jobs_source



def test_plugin_contexts_route_registered_core_settings_through_typed_resolver():
    root = Path(__file__).resolve().parents[1]
    api_source = (root / "rank42" / "plugin_api.py").read_text(
        encoding="utf-8"
    )
    feature_source = (root / "rank42" / "feature_hooks.py").read_text(
        encoding="utf-8"
    )
    extension_source = (root / "rank42" / "ui_extensions.py").read_text(
        encoding="utf-8"
    )

    # Context ownership moved into the public SDK facade. The hosts only
    # construct the versioned context objects.
    assert "class FeatureContextV1:" in api_source
    assert "class ExtensionContextV1:" in api_source
    assert api_source.count(
        'return setting_value(self.db, "science_python", "")'
    ) == 2
    assert api_source.count("if name in SETTING_SPECS:") == 2
    assert api_source.count(
        "return setting_value(self.db, name, default)"
    ) == 2
    assert 'get_setting(self.db, "science_python"' not in api_source

    assert "FeatureContextV1(" in feature_source
    assert "ExtensionContextV1(" in extension_source



def test_science_python_bootstrap_marker_is_one_way_initialization_input(tmp_path):
    marker = tmp_path / ".rank42-ui" / "science-python"
    marker.parent.mkdir(parents=True)
    marker.write_text("/marker/python\n", encoding="utf-8")

    assert science_python_bootstrap_value(tmp_path) == "/marker/python"

    db = _settings_db()
    try:
        assert seed_setting_if_missing(
            db,
            "science_python",
            science_python_bootstrap_value(tmp_path),
        ) is True
        assert setting_value(db, "science_python") == "/marker/python"

        marker.write_text("/replacement/python\n", encoding="utf-8")
        assert seed_setting_if_missing(
            db,
            "science_python",
            science_python_bootstrap_value(tmp_path),
        ) is False
        assert setting_value(db, "science_python") == "/marker/python"
    finally:
        db.close()


def test_science_python_bootstrap_falls_back_when_marker_missing_or_empty(tmp_path):
    assert science_python_bootstrap_value(
        tmp_path,
        fallback="/fallback/python",
    ) == "/fallback/python"

    marker = tmp_path / ".rank42-ui" / "science-python"
    marker.parent.mkdir(parents=True)
    marker.write_text("\n", encoding="utf-8")

    assert science_python_bootstrap_value(
        tmp_path,
        fallback="/fallback/python",
    ) == "/fallback/python"


def test_installation_surfaces_document_one_way_science_runtime_bootstrap():
    root = Path(__file__).resolve().parents[1]
    installer = (root / "scripts" / "install-ui.sh").read_text(
        encoding="utf-8"
    )
    installation = (
        root / "docs" / "getting-started" / "installation.md"
    ).read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")

    assert ".rank42-ui/science-python" in installer
    assert "One-way bootstrap marker only" in installer
    assert "ui_settings.science_python" in installer

    assert ".rank42-ui/science-python" in installation
    assert "one-way bootstrap input only" in installation
    assert "persisted Settings value is runtime truth" in installation

    assert ".rank42-ui/science-python" in readme
    assert "first-run bootstrap marker" in readme
    assert "persisted Settings value is authoritative" in readme


def test_ui_appearance_setting_normalizes_and_rejects_unknown_modes():
    db = _settings_db()
    try:
        assert setting_value(db, "ui_appearance") == "light"
        assert save_setting_value(db, "ui_appearance", "DARK") == "dark"
        assert setting_value(db, "ui_appearance") == "dark"

        with pytest.raises(ValueError, match="light or dark"):
            save_setting_value(db, "ui_appearance", "system")
    finally:
        db.close()
