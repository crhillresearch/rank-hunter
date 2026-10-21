from pathlib import Path

from rank42.settings_registry import GLOBAL_TIME_BUDGET_KEYS, setting_spec


ROOT = Path(__file__).resolve().parents[1]
RANK42 = ROOT / "rank42"


def _files_containing(setting_key):
    needle = f'"{setting_key}"'
    single = f"'{setting_key}'"
    return {
        str(path.relative_to(ROOT))
        for path in RANK42.rglob("*.py")
        if needle in path.read_text(encoding="utf-8")
        or single in path.read_text(encoding="utf-8")
    }


def test_global_time_budget_registry_contract_is_explicit():
    assert GLOBAL_TIME_BUDGET_KEYS == {
        "pari_rank_timeout",
        "mwrank_rank_timeout",
        "deep_cert_timeout",
    }
    for key in GLOBAL_TIME_BUDGET_KEYS:
        spec = setting_spec(key)
        assert spec.scope == "runtime_default"
        assert spec.description.startswith("Global default ")


def test_global_timeout_setting_consumers_are_bounded_and_known():
    assert _files_containing("pari_rank_timeout") == {
        "rank42/settings_registry.py",
        "rank42/ui_pages/settings_page.py",
        "rank42/ui_pages/descent_page.py",
        "rank42/ui_pages/family_search.py",
        "rank42/ui_pages/legacy/descent_page.py",
    }
    assert _files_containing("mwrank_rank_timeout") == {
        "rank42/settings_registry.py",
        "rank42/ui_pages/settings_page.py",
        "rank42/ui_pages/descent_page.py",
        "rank42/ui_pages/legacy/descent_page.py",
    }
    assert _files_containing("deep_cert_timeout") == {
        "rank42/settings_registry.py",
        "rank42/ui_pages/settings_page.py",
        "rank42/ui_pages/descent_page.py",
        "rank42/ui_pages/family_search.py",
        "rank42/ui_pages/legacy/descent_page.py",
    }


def test_settings_labels_rank_timeouts_as_global_defaults():
    source = (
        ROOT / "rank42" / "ui_pages" / "settings_page.py"
    ).read_text(encoding="utf-8")

    assert "Global default time budgets." in source
    assert "Family Search, Pipeline " in source
    assert "stages, and Analysis actions may override these per workflow." in source


def test_legacy_descent_timeout_defaults_preserve_same_override_contract():
    source = (
        ROOT / "rank42" / "ui_pages" / "legacy" / "descent_page.py"
    ).read_text(encoding="utf-8")

    assert "value=int(setting(db,'pari_rank_timeout',300))" in source
    assert "value=int(setting(db,'mwrank_rank_timeout',300))" in source
    assert "value=int(setting(db,'deep_cert_timeout',900))" in source
    assert "'--timeout',pari_timeout" in source
    assert "'--mwrank-timeout',mwrank_timeout" in source
    assert "'--timeout',timeout" in source
    assert "'--timeout',sat_timeout" in source


def test_analysis_descent_uses_settings_only_as_editable_starting_budgets():
    source = (
        ROOT / "rank42" / "ui_pages" / "descent_page.py"
    ).read_text(encoding="utf-8")

    assert "per-action controls own the timeout passed to the launched command" in source
    assert "value=int(setting(db,'pari_rank_timeout',300))" in source
    assert "value=int(setting(db,'mwrank_rank_timeout',300))" in source
    assert "value=int(setting(db,'deep_cert_timeout',900))" in source
    assert "'--timeout',pari_timeout" in source
    assert "'--mwrank-timeout',mwrank_timeout" in source
    assert "'--timeout',timeout" in source
    assert "'--timeout',sat_timeout" in source


def test_family_free_mode_uses_global_defaults_as_editable_overrides_only():
    source = (
        ROOT / "rank42" / "ui_pages" / "family_search.py"
    ).read_text(encoding="utf-8")

    assert "settings seed editable FREE-mode budgets" in source
    assert 'setting(db, "pari_rank_timeout", 300)' in source
    assert 'setting(db, "deep_cert_timeout", 900)' in source
    assert 'value=quick_timeout' in source
    assert 'value=strong_timeout' in source

    plugin_start = source.index('if launch_mode == "Plugin":')
    torsion_start = source.index('elif launch_mode == "Torsion":', plugin_start)
    plugin_branch = source[plugin_start:torsion_start]
    assert "quick_timeout" not in plugin_branch
    assert "strong_timeout" not in plugin_branch

    free_choice_start = source.index("def _free_family_pipeline_choice(")
    free_choice_end = source.index("def _torsion_pipeline_options(", free_choice_start)
    free_choice = source[free_choice_start:free_choice_end]
    assert '"timeout": quick_timeout' in free_choice
    assert '"timeout": strong_timeout' in free_choice

    native_start = source.index("else:", torsion_start)
    native_end = source.index("adapter_file =", native_start)
    native_branch = source[native_start:native_end]
    assert "_free_family_pipeline_choice(" in native_branch
    assert "quick_timeout=quick_timeout" in native_branch
    assert "strong_timeout=strong_timeout" in native_branch


def test_pipeline_stage_budgets_are_owned_by_pipeline_config_not_global_settings():
    source = (
        ROOT / "rank42" / "ui_pages" / "build_your_own.py"
    ).read_text(encoding="utf-8")

    for key in GLOBAL_TIME_BUDGET_KEYS:
        assert key not in source
    assert 'cfg["timeout"] = int(' in source
    assert 'cfg["certificate_timeout"] = int(' in source
    assert '"Seconds / branch"' in source
    assert '"Certificate seconds"' in source
