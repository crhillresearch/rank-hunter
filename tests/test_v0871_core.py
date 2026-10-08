from pathlib import Path

from rank42 import __version__


def root():
    return Path(__file__).resolve().parents[1]


def test_release_is_current_core_v089():
    r = root()
    assert __version__ == "0.8.9"
    assert not (r / "rank42" / "families").exists()
    assert not (r / "rank42" / "ui_pages" / "playground.py").exists()


def test_navigation_contract_v089():
    ui = (root() / "rank42" / "ui.py").read_text()
    for item in [
        '("Auto", "Auto")',
        '("Search", "Search")',
        '("Target", "Target")',
        '("Analyze", "Analyze")',
        '("Descent", "Descent")',
        '("Lattices", "Lattices & Heights")',
        '("Independence", "Independence")',
        '("Curves", "Curves")',
        '("Points", "Points")',
        '("Candidates", "Candidates")',
        '("Corpora", "Corpora")',
        '("Results", "Results")',
        '("External Catalog", "External Catalog")',
        '("Campaigns", "Campaigns")',
        '("Pipelines", "Pipelines")',
        '("Jobs", "Jobs")',
        '("Families", "Plugin Families")',
        '("Extensions", "Plugin Extensions")',
        '("Themes", "Themes", ":material/palette:")',
        '("Diagnostics", "Diagnostics")',
        '("Database", "Database")',
        '("Settings", "Settings")',
    ]:
        assert item in ui
    assert '"Curves": ":material/gesture:"' in ui
    assert '"Results": ":material/analytics:"' in ui


def test_page_header_is_page_owned_not_parent_section_owned():
    common = (root() / "rank42" / "ui_pages" / "common.py").read_text()
    extensions = (root() / "rank42" / "ui_extensions.py").read_text()
    assert 'st.title(f":material/{icon}: {name}", anchor=False)' in common
    assert 'title(page.label, plugin.description or None, icon=plugin.icon)' in extensions


def test_features_and_workspaces_share_extensions_surface():
    plugins_page = (root() / "rank42" / "ui_pages" / "plugins_page.py").read_text()
    registry = (root() / "rank42" / "plugins.py").read_text()
    assert 'PLUGIN_TYPES = {"family", "feature", "extension"}' in registry
    assert 'with st.expander(f"Features · {len(features)}"' in plugins_page
    assert 'with st.expander(f"Workspaces · {len(extensions)}"' in plugins_page
    assert 'tabs(["Families", "Extensions"]' in plugins_page


def test_segmented_control_cannot_be_deselected():
    components = (root() / "rank42" / "ui_components.py").read_text()
    assert "required=True" in components


def test_external_catalog_exposes_icarm_and_lmfdb_tabs():
    page = (root() / "rank42" / "ui_pages" / "external_catalog.py").read_text()
    assert 'tabs(["ICARM", "LMFDB"]' in page
    assert "fetch_lmfdb_cached" in page
    assert "catalog_query_cache" in page
    assert "LMFDB_COMPLETE_CONDUCTOR" in page
    assert "Sync ICARM catalog" in page


def test_vendored_ratpoints_runtime_contract_v089():
    r = root()
    ui = (r / "rank42" / "ui.py").read_text()
    settings = (r / "rank42" / "ui_pages" / "settings_page.py").read_text()
    installer = (r / "scripts" / "install-ratpoints.sh").read_text()
    assert 'vendored_executable("CPU", ctx.project_root)' in ui
    assert 'vendored_executable("GPU", ctx.project_root)' in ui
    assert 'cpu_default = vendored_executable("CPU", ctx.project_root)' in settings
    assert 'gpu_default = vendored_executable("GPU", ctx.project_root)' in settings
    assert "vendor/ratpoints" in installer
    assert "git clone" not in installer
    assert 'git -C "$WORK" pull' not in installer
