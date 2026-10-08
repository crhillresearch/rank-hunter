import json
import sqlite3

from rank42.theme_runtime import resolve_active_theme


def _theme(root, theme_id, *, native=False, dark=False):
    t = root / 'themes' / theme_id
    t.mkdir(parents=True)
    (t / 'theme.css').write_text('body{}')
    manifest = {
        'schema_version': 1,
        'id': theme_id,
        'name': theme_id.title(),
        'version': '1.0.0',
        'stylesheets': ['theme.css'],
        'tokens': {},
    }
    if native:
        (t / 'streamlit-theme.toml').write_text('[theme]\nbase="light"\nprimaryColor="#277A8D"\n')
        manifest['streamlit_theme'] = 'streamlit-theme.toml'
    if dark:
        (t / 'dark.css').write_text('body{}')
        (t / 'streamlit-theme-dark.toml').write_text(
            '[theme]\nbase="dark"\nprimaryColor="#6EA2FF"\n'
        )
        manifest['appearances'] = {
            'dark': {
                'tokens': {'rh-bg': '#101522'},
                'stylesheets': ['dark.css'],
                'streamlit_theme': 'streamlit-theme-dark.toml',
            }
        }
    (t / 'theme.json').write_text(json.dumps(manifest))
    return t


def _db(path, value=None, appearance=None):
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE ui_settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT NOT NULL)')
    if value is not None:
        db.execute('INSERT INTO ui_settings VALUES (?,?,?)', ('ui_theme', json.dumps(value), 'now'))
    if appearance is not None:
        db.execute(
            'INSERT INTO ui_settings VALUES (?,?,?)',
            ('ui_appearance', json.dumps(appearance), 'now'),
        )
    db.commit()
    db.close()


def test_release_runtime_ignores_persisted_theme_and_uses_axiom(tmp_path):
    _theme(tmp_path, 'axiom', native=True)
    _theme(tmp_path, 'paper')
    dbp = tmp_path / 'rank42.db'
    _db(dbp, 'paper')
    active = resolve_active_theme(tmp_path, dbp)
    assert active.id == 'axiom'
    assert active.streamlit_theme_path.name == 'streamlit-theme.toml'


def test_missing_persisted_theme_still_uses_release_axiom(tmp_path):
    _theme(tmp_path, 'axiom', native=True)
    dbp = tmp_path / 'rank42.db'
    _db(dbp, 'missing')
    active = resolve_active_theme(tmp_path, dbp)
    assert active.id == 'axiom'
    assert active.streamlit_theme_path.name == 'streamlit-theme.toml'


def test_invalid_persisted_theme_shape_does_not_change_release_axiom(tmp_path):
    _theme(tmp_path, 'axiom', native=True)
    _theme(tmp_path, 'paper')
    dbp = tmp_path / 'rank42.db'
    _db(dbp, 123)

    active = resolve_active_theme(tmp_path, dbp)

    assert active.id == 'axiom'
    assert active.streamlit_theme_path.name == 'streamlit-theme.toml'


def test_missing_database_uses_release_axiom(tmp_path):
    _theme(tmp_path, 'axiom', native=True)
    active = resolve_active_theme(tmp_path, tmp_path / 'missing.db')
    assert active.id == 'axiom'


def test_no_installed_themes_returns_core_baseline(tmp_path):
    active = resolve_active_theme(tmp_path, tmp_path / 'missing.db')
    assert active.theme is None
    assert active.id == ''
    assert active.streamlit_theme_path is None



def test_persisted_theme_reader_is_preserved_for_future_reactivation():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "rank42" / "theme_runtime.py").read_text(
        encoding="utf-8"
    )

    assert "from rank42.settings_registry import setting_value" in source
    assert 'setting_value(db, "ui_theme", None)' in source
    assert "def _persisted_theme_id" in source
    assert "FROM ui_settings" not in source


def test_runtime_and_shell_delegate_to_release_axiom_resolver():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    runtime_source = (root / "rank42" / "theme_runtime.py").read_text(encoding="utf-8")
    ui_source = (root / "rank42" / "ui.py").read_text(encoding="utf-8")

    assert "resolve_release_theme(root, RELEASE_THEME_ID)" in runtime_source
    assert "resolve_release_theme(root, RELEASE_THEME_ID)" in ui_source
    assert "setting_value(db, \"ui_theme\"" not in ui_source
    assert "discover_themes(root)" not in ui_source
    assert "load_theme(root" not in ui_source


def test_themes_page_distinguishes_active_from_selected_lifecycle():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "rank42" / "ui_pages" / "themes_page.py").read_text(encoding="utf-8")

    assert "selected_id" in source
    assert "loaded_id" in source
    assert "rh-theme-active-badge" in source
    assert "rh-theme-selected-badge" in source
    assert '"Select"' in source
    assert "selected for the next Rank Hunter UI start" in source
    assert "remains active until restart" in source
    assert '"Enable"' not in source
    assert "archive_plugin" not in source
    assert "validation_freshness" not in source


def test_public_distribution_links_remain_centralized():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    themes = (root / "rank42" / "ui_pages" / "themes_page.py").read_text(encoding="utf-8")
    plugins = (root / "rank42" / "ui_pages" / "plugins_page.py").read_text(encoding="utf-8")
    links = (root / "rank42" / "release_links.py").read_text(encoding="utf-8")

    assert "Install Themes" not in themes
    assert "THEME_INSTALL_URL" not in themes
    assert "rank-hunter-themes" not in links
    assert "https://github.com/crhillresearch/rh-plugins" not in plugins
    assert 'PLUGIN_INSTALL_URL = "https://github.com/crhillresearch/rh-plugins"' in links


def test_themes_route_code_is_preserved_but_sidebar_item_is_hidden():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    ui_source = (root / "rank42" / "ui.py").read_text(encoding="utf-8")
    themes_source = (root / "rank42" / "ui_pages" / "themes_page.py").read_text(
        encoding="utf-8"
    )

    assert '"Themes": themes_page.page' in ui_source
    manage_group = ui_source.split('"Manage": [', 1)[1].split('],', 1)[0]
    assert '"Themes"' not in manage_group
    assert 'def page(db, ctx):' in themes_source
    assert '"Select"' in themes_source


def test_release_runtime_uses_persisted_dark_appearance_native_palette(tmp_path):
    _theme(tmp_path, "axiom", native=True, dark=True)
    dbp = tmp_path / "rank42.db"
    _db(dbp, "paper", appearance="dark")

    active = resolve_active_theme(tmp_path, dbp)

    assert active.id == "axiom"
    assert active.appearance == "dark"
    assert active.streamlit_theme_path.name == "streamlit-theme-dark.toml"


def test_release_runtime_defaults_appearance_to_light(tmp_path):
    _theme(tmp_path, "axiom", native=True, dark=True)
    dbp = tmp_path / "rank42.db"
    _db(dbp)

    active = resolve_active_theme(tmp_path, dbp)

    assert active.appearance == "light"
    assert active.streamlit_theme_path.name == "streamlit-theme.toml"


def test_shell_appearance_is_separate_from_release_theme_identity():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    ui_source = (root / "rank42" / "ui.py").read_text(encoding="utf-8")
    common_source = (root / "rank42" / "ui_pages" / "common.py").read_text(
        encoding="utf-8"
    )
    component_source = (
        root / "rank42" / "ui_assets" / "active_work" / "index.html"
    ).read_text(encoding="utf-8")

    assert 'setting_value(db, "ui_appearance", "light")' in ui_source
    assert 'save_setting_value(db, "ui_appearance", "light")' in ui_source
    assert 'save_setting_value(db, "ui_appearance", "dark")' in ui_source
    assert ':material/light_mode:' in ui_source
    assert ':material/dark_mode:' in ui_source
    assert "theme.css_text(appearance)" in common_source
    assert "theme.resolved_tokens(appearance)" in common_source
    assert "palette=active_work_palette" in common_source
    assert "applyPalette" in component_source
