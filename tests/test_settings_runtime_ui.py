from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "rank42" / "ui_pages" / "settings_page.py"


def source():
    return SETTINGS.read_text(encoding="utf-8")


def block(text, start, end):
    left = text.index(start)
    right = text.index(end, left)
    return text[left:right]


def test_runtime_status_overview_is_compact_and_role_explicit():
    text = source()
    summary = block(
        text,
        "def _render_effective_runtime_summary",
        "def _runtime_settings",
    )

    assert 'st.markdown("#### Runtime status")' in summary
    assert 'metric("Default point engine"' in summary
    assert '"Required runtimes"' in summary
    assert '"Optional GPU"' in summary
    assert '"Requirement": "Required" if probe.required else "Optional"' in summary
    assert '"Status": "Ready" if probe.resolved else "Missing"' in summary
    assert '"Use": usage' in summary
    assert '"Active point engine"' in summary
    assert '"Configured"' not in summary
    assert '"Resolved executable"' not in summary


def test_runtime_page_groups_executables_testing_budgets_and_save():
    text = source()
    runtime = block(text, "def _runtime_settings", "def _icarm_settings")

    status_pos = runtime.index("_render_effective_runtime_summary")
    executable_pos = runtime.index('st.markdown("#### Runtime executables")')
    test_pos = runtime.index('st.markdown("##### Test runtimes")')
    budget_pos = runtime.index('st.markdown("#### Default time budgets")')
    save_pos = runtime.index('"Save runtime defaults"')

    assert status_pos < executable_pos < test_pos < budget_pos < save_pos
    assert '"Science Python / Sage · Required"' in runtime
    assert '"CPU ratpoints · Required"' in runtime
    assert '"GPU ratpoints · Optional"' in runtime
    assert '"Default point engine"' in runtime


def test_runtime_render_does_not_add_expensive_health_probes():
    text = source()
    resolution = block(text, "def _runtime_resolution", "def _render_effective_runtime_summary")
    summary = block(text, "def _render_effective_runtime_summary", "def _runtime_settings")

    assert "run_probe=False" in resolution
    assert "probe_runtime_bundle(" not in resolution
    assert "probe_runtime_bundle(" not in summary


def test_runtime_test_and_save_keep_existing_probe_and_required_gate():
    text = source()
    runtime = block(text, "def _runtime_settings", "def _icarm_settings")

    assert runtime.count("probe_runtime_bundle(") == 2
    assert "required_runtime_failures(probes)" in runtime

    save_start = runtime.index('"Save runtime defaults"')
    save = runtime[save_start:]
    probe_pos = save.index("probe_runtime_bundle(")
    failure_pos = save.index("required_runtime_failures(probes)")
    first_save_pos = save.index("save_setting_value(")

    assert probe_pos < failure_pos < first_save_pos
    assert '"science_python"' in save
    assert '"ratpoints"' in save
    assert '"ratpoints_gpu"' in save
    assert '"ratpoints_backend"' in save


def test_runtime_timeout_settings_and_defaults_are_preserved():
    text = source()
    runtime = block(text, "def _runtime_settings", "def _icarm_settings")

    assert '"pari_rank_timeout", 300' in runtime
    assert '"mwrank_rank_timeout", 300' in runtime
    assert '"deep_cert_timeout", 900' in runtime
    assert 'save_setting_value(db, "pari_rank_timeout", pari_timeout)' in runtime
    assert 'save_setting_value(db, "mwrank_rank_timeout", mwrank_timeout)' in runtime
    assert 'save_setting_value(db, "deep_cert_timeout", deep_timeout)' in runtime


def test_optional_gpu_ui_remains_labeled_and_notice_only():
    text = source()
    runtime = block(text, "def _runtime_settings", "def _icarm_settings")

    assert '"GPU ratpoints · Optional"' in runtime
    assert 'gpu_probe = probes["ratpoints_gpu"]' in runtime
    assert "if not gpu_probe.healthy:" in runtime
    assert 'st.session_state["settings-runtime-notice"]' in runtime

    save_start = runtime.index('"Save runtime defaults"')
    gpu_branch = runtime[runtime.index('gpu_probe = probes["ratpoints_gpu"]', save_start):]
    assert "required_runtime_failures(probes)" in runtime[save_start:]
    assert "return" not in gpu_branch
