from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_ui_launcher_uses_isolated_ui_runtime_and_recorded_scientific_python():
    launcher = (ROOT / "scripts" / "run-ui.sh").read_text(encoding="utf-8")

    assert ".rank42-ui/science-python" in launcher
    assert 'UI_RUNTIME_DEFAULT="$PROJECT_ROOT/.venv-ui/bin/python"' in launcher
    assert "import sage.all, streamlit" in launcher
    assert 'export RANK_HUNTER_SCIENCE_PYTHON="$SCIENCE_PYTHON"' in launcher
    assert 'exec "$UI_RUNTIME" -m rank42.streamlit_launcher run' in launcher
    assert 'exec "$SCIENCE_PYTHON" -m rank42.streamlit_launcher run' not in launcher


def test_install_creates_sage_capable_ui_venv_and_records_launch_command():
    installer = (ROOT / "scripts" / "install-ui.sh").read_text(encoding="utf-8")

    assert 'RANK_HUNTER_SCIENCE_PYTHON' in installer
    assert '-m venv --system-site-packages "$UI_VENV"' in installer
    assert '"$UI_VENV/bin/python" -c \'import sage.all, streamlit\'' in installer
    assert '$UI_VENV/bin/python -m rank42.streamlit_launcher run' in installer


def test_streamlit_launcher_initializes_sage_before_streamlit():
    launcher = (ROOT / "rank42" / "streamlit_launcher.py").read_text(
        encoding="utf-8"
    )

    sage_import = launcher.index("import sage.all")
    streamlit_import = launcher.index("from streamlit.web import cli")
    streamlit_main = launcher.index("streamlit_cli.main")
    assert sage_import < streamlit_import < streamlit_main


def test_ui_launcher_uses_pre_streamlit_resolved_native_appearance():
    launcher = (ROOT / "scripts" / "run-ui.sh").read_text(encoding="utf-8")
    runtime = (ROOT / "rank42" / "theme_runtime.py").read_text(encoding="utf-8")

    assert "-m rank42.theme_runtime" in launcher
    assert 'STREAMLIT_THEME_ARGS+=(--theme.base "$NATIVE_THEME")' in launcher
    assert '_persisted_appearance' in runtime
    assert 'streamlit_theme_for(self.appearance)' in runtime
