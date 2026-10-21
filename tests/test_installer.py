from __future__ import annotations

import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.sh"


def _run(*args):
    return subprocess.run(
        ["/bin/bash", str(INSTALLER), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env={**os.environ, "RANK_HUNTER_DB": "rank42-installer-test.db"},
    )


def test_installer_help():
    cp = _run("--help")
    assert cp.returncode == 0
    assert "bash install.sh [--lite]" in cp.stdout
    assert "--science-python PATH" in cp.stdout


def test_unknown_argument_is_rejected():
    cp = _run("--definitely-not-an-option")
    assert cp.returncode == 2
    assert "unknown installer argument" in cp.stderr


def test_lite_dry_run_skips_native_builds():
    cp = _run("--lite", "--dry-run", "--science-python", "/opt/rank-hunter/sage-python")
    assert cp.returncode == 0, cp.stderr
    assert "installer mode: lite" in cp.stdout
    assert "lite mode: skipping native vendor submodule/build work" in cp.stdout
    assert "CPU ratpoints:       configured external path preserved (not inspected in dry-run)" in cp.stdout
    assert "GPU ratpoints:       configured external path preserved (not inspected in dry-run)" in cp.stdout
    assert "scripts/install-ratpoints.sh" not in cp.stdout
    assert "git submodule update --init --recursive" not in cp.stdout
    assert "scripts/install-official-plugins.sh" in cp.stdout


def test_full_dry_run_selects_submodules_cpu_and_conditional_gpu():
    cp = _run("--dry-run", "--science-python", "/opt/rank-hunter/sage-python")
    assert cp.returncode == 0, cp.stderr
    assert "installer mode: full" in cp.stdout
    assert "git submodule update --init --recursive" in cp.stdout
    assert "scripts/install-ratpoints.sh" in cp.stdout
    assert "conditional build/smoke" in cp.stdout


def test_installer_uses_authoritative_schema_and_public_plugin_checkout():
    src = INSTALLER.read_text(encoding="utf-8")
    modules = (ROOT / ".gitmodules").read_text(encoding="utf-8")
    helper = (ROOT / "scripts" / "install-official-plugins.sh").read_text(encoding="utf-8")
    assert "open_database_with_migrations" in src
    assert 'submodule "plugins"' not in modules
    assert "scripts/install-official-plugins.sh" in src
    assert "https://github.com/crhillresearch/rh-plugins.git" in helper
    assert 'DEST="${RANK_HUNTER_OFFICIAL_PLUGINS_DIR:-$ROOT/plugins}"' in helper
    assert 'RANK_HUNTER_OFFICIAL_PLUGINS_REF' in helper
    assert 'v0.9.2' in helper
    assert "git clone --no-checkout" in helper
    assert "git -C \"$DEST\" fetch --tags origin" in helper
    assert 'git -C "$DEST" checkout --detach "$TARGET"' in helper
    assert "status --porcelain --untracked-files=all" in helper
    assert "git push" not in helper
    assert "scripts/sync-dev-plugins.sh" not in src
    assert 'seed_setting_if_missing(db, "science_python"' in src
    assert 'seed_setting_if_missing(db, "ratpoints"' in src

def test_ui_install_isolated_but_sage_capable():
    src = (ROOT / "scripts" / "install-ui.sh").read_text(encoding="utf-8")
    assert "-m venv --system-site-packages" in src
    assert "$UI_VENV/bin/python" in src
    assert "import sage.all, streamlit" in src
    assert "pip install -r requirements.txt" in src


def test_ui_launcher_uses_ui_venv_and_preserves_science_bootstrap():
    src = (ROOT / "scripts" / "run-ui.sh").read_text(encoding="utf-8")
    assert 'UI_RUNTIME_DEFAULT="$PROJECT_ROOT/.venv-ui/bin/python"' in src
    assert 'export RANK_HUNTER_SCIENCE_PYTHON="$SCIENCE_PYTHON"' in src
    assert '"$UI_RUNTIME" -m rank42.streamlit_launcher' in src


def test_gpu_installer_requires_real_cuda_smoke():
    src = (ROOT / "scripts" / "install-ratpoints-gpu.sh").read_text(encoding="utf-8")
    assert "command -v nvcc" in src
    assert "make -C" in src
    assert "ratpoints_gpu" in src
    assert "'1 0 0 0 1' 10 -q -i" in src


def test_installer_and_helpers_have_valid_bash_syntax():
    for rel in (
        "install.sh",
        "scripts/install-ui.sh",
        "scripts/install-ratpoints.sh",
        "scripts/install-ratpoints-gpu.sh",
        "scripts/install-catalog-sql.sh",
        "scripts/install-official-plugins.sh",
        "scripts/run-ui.sh",
    ):
        cp = subprocess.run(
            ["/bin/bash", "-n", str(ROOT / rel)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert cp.returncode == 0, f"{rel}: {cp.stderr}"


def test_missing_science_python_has_clear_failure():
    cp = _run("--science-python", "/definitely/missing/rank-hunter-python")
    assert cp.returncode == 2
    assert "scientific Python is not executable" in cp.stderr


def test_dry_run_is_repeatable_and_does_not_create_database(tmp_path):
    db = tmp_path / "fresh.db"
    args = ("--lite", "--dry-run", "--science-python", "/opt/rank-hunter/sage-python", "--db", str(db))
    first = _run(*args)
    second = _run(*args)
    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
    assert not db.exists()


def test_catalog_dependency_is_required_but_live_probe_is_optional():
    helper = (ROOT / "scripts" / "install-catalog-sql.sh").read_text(encoding="utf-8")
    installer = INSTALLER.read_text(encoding="utf-8")
    assert '"$SCIENCE_PYTHON" -m pip install -r requirements-science-catalog.txt' in helper
    assert 'RANK_HUNTER_CATALOG_PROBE_OPTIONAL' in helper
    assert 'RANK_HUNTER_CATALOG_PROBE_OPTIONAL=1' in installer


def test_only_smoke_validated_vendor_builds_seed_runtime_defaults():
    src = INSTALLER.read_text(encoding="utf-8")
    assert "CPU_READY=0" in src and "GPU_READY=0" in src
    assert "CPU_READY=1" in src
    assert "GPU_READY=1" in src
    assert 'RANK_HUNTER_INSTALL_CPU="$([[ "$CPU_READY" == "1" ]]' in src
    assert 'RANK_HUNTER_INSTALL_GPU="$([[ "$GPU_READY" == "1" ]]' in src


def test_lite_mode_reports_configured_or_missing_external_engines():
    src = INSTALLER.read_text(encoding="utf-8")
    assert "read_stored_runtime ratpoints" in src
    assert "read_stored_runtime ratpoints_gpu" in src
    assert "not configured; set CPU ratpoints in System -> Settings" in src
    assert "not configured; set GPU ratpoints in System -> Settings" in src


def test_preflight_checks_system_and_scientific_venv_support():
    src = INSTALLER.read_text(encoding="utf-8")
    assert 'python3 -m venv --help' in src
    assert '"$SCIENCE_PYTHON" -m venv --help' in src
    assert "python3-venv" in src


def test_plugins_path_is_not_a_gitlink():
    cp = subprocess.run(
        ["git", "ls-files", "--stage", "plugins"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert cp.returncode == 0, cp.stderr
    assert cp.stdout.strip() == ""


def test_installer_reports_public_plugin_checkout_revision_and_count():
    src = INSTALLER.read_text(encoding="utf-8")
    assert 'OFFICIAL_PLUGINS="$ROOT/plugins"' in src
    assert 'git -C "$OFFICIAL_PLUGINS" rev-parse --short HEAD' in src
    assert "discover_plugins(root)" in src
    assert 'PLUGIN_STATUS="ready ($PLUGIN_REV; $PLUGIN_COUNT discovered)"' in src


def test_installer_operates_only_inside_existing_checkout():
    src = INSTALLER.read_text(encoding="utf-8")
    assert 'install.sh must be run from a Rank Hunter checkout' in src
    assert 'this directory is not a Git checkout' in src
    assert 'git clone https://github.com/crhillresearch/rank-hunter' not in src
    assert '$HOME/rh' not in src
    assert 'mkdir -p "$HOME' not in src


def test_installer_can_autodiscover_sage_from_command_or_conda():
    src = INSTALLER.read_text(encoding="utf-8")
    assert "command -v sage" in src
    assert "sage -python -c" in src
    assert "command -v conda" in src
    assert "conda env list --json" in src


def test_ui_component_version_is_pinned_for_reproducible_fresh_installs():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "streamlit-shadcn-ui==1.3.0" in requirements
    assert "streamlit-shadcn-ui>=" not in requirements
