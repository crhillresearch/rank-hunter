import json
from pathlib import Path

import pytest

from rank42.theme_loader import ThemeError, discover_themes, load_theme, read_theme, resolve_release_theme, resolve_theme, theme_records


def project_root():
    return Path(__file__).resolve().parents[1]


def _write_theme(tmp_path, *, theme_id='paper', streamlit=None, dark=False):
    t = tmp_path / 'themes' / theme_id
    t.mkdir(parents=True)
    (t / 'theme.css').write_text('body { background: var(--rh-bg); }')
    manifest = {
        'schema_version': 1,
        'id': theme_id,
        'name': theme_id.title(),
        'version': '1.2.0',
        'stylesheets': ['theme.css'],
        'tokens': {'rh-bg': '#ffffff', 'rh-text': '#000000'},
    }
    if streamlit is not None:
        manifest['streamlit_theme'] = 'streamlit-theme.toml'
        (t / 'streamlit-theme.toml').write_text(streamlit)
    if dark:
        (t / 'appearance-dark.css').write_text(
            'body { background: var(--rh-bg); color: var(--rh-text); }'
        )
        (t / 'streamlit-theme-dark.toml').write_text(
            '[theme]\nbase="dark"\nbackgroundColor="#101522"\n'
        )
        manifest['appearances'] = {
            'dark': {
                'tokens': {
                    'rh-bg': '#101522',
                    'rh-text': '#f3f6fb',
                    'brand': '#6ea2ff',
                    'rh-primary': 'var(--brand)',
                },
                'stylesheets': ['appearance-dark.css'],
                'streamlit_theme': 'streamlit-theme-dark.toml',
            }
        }
    (t / 'theme.json').write_text(json.dumps(manifest))
    return t


def test_empty_project_root_has_no_theme_package(tmp_path):
    assert discover_themes(tmp_path) == []
    assert not any((tmp_path / "themes").glob("*/theme.json"))
    with pytest.raises(ThemeError, match="not installed"):
        load_theme(tmp_path)


def test_theme_loader_discovers_external_filesystem_theme(tmp_path):
    _write_theme(tmp_path)
    rows = discover_themes(tmp_path)
    assert [theme.id for theme in rows] == ['paper']
    theme = load_theme(tmp_path, 'paper')
    assert theme.streamlit_theme_path is None
    assert '--rh-bg: #ffffff;' in theme.css_text()
    assert 'body { background: var(--rh-bg); }' in theme.css_text()


def test_theme_loader_accepts_presentation_only_streamlit_theme(tmp_path):
    t = _write_theme(tmp_path, streamlit='''
[theme]
base = "dark"
primaryColor = "#277A8D"

[theme.sidebar]
backgroundColor = "#11101E"
''')
    theme = read_theme(t)
    assert theme.streamlit_theme_path == (t / 'streamlit-theme.toml').resolve()


def test_theme_loader_rejects_non_theme_streamlit_config(tmp_path):
    t = _write_theme(tmp_path, streamlit='''
[theme]
base = "dark"

[server]
headless = true
''')
    with pytest.raises(ThemeError, match=r'only contain a \[theme\] table'):
        read_theme(t)


def test_theme_loader_rejects_streamlit_theme_path_escape(tmp_path):
    t = _write_theme(tmp_path)
    (tmp_path / 'outside.toml').write_text('[theme]\nbase="dark"\n')
    manifest = json.loads((t / 'theme.json').read_text())
    manifest['streamlit_theme'] = '../../outside.toml'
    (t / 'theme.json').write_text(json.dumps(manifest))
    with pytest.raises(ThemeError, match='escapes'):
        read_theme(t)


def test_theme_loader_rejects_malformed_streamlit_theme(tmp_path):
    t = _write_theme(tmp_path, streamlit='[theme\nbase="dark"')
    with pytest.raises(ThemeError, match='cannot parse Streamlit theme'):
        read_theme(t)


def test_theme_assets_cannot_escape_theme_directory(tmp_path):
    t = tmp_path / 'themes' / 'bad'
    t.mkdir(parents=True)
    (tmp_path / 'outside.css').write_text('body{}')
    (t / 'theme.json').write_text(json.dumps({
        'schema_version': 1, 'id': 'bad', 'name': 'Bad', 'version': '1.0.0',
        'stylesheets': ['../../outside.css'], 'tokens': {}
    }))
    with pytest.raises(ThemeError, match='escapes'):
        read_theme(t)


def test_invalid_themes_are_auditable_without_breaking_discovery(tmp_path):
    good = tmp_path / 'themes' / 'good'
    good.mkdir(parents=True)
    (good / 'theme.css').write_text('body{}')
    (good / 'theme.json').write_text(json.dumps({
        'schema_version': 1, 'id': 'good', 'name': 'Good', 'version': '1.0.0',
        'stylesheets': ['theme.css'], 'tokens': {}
    }))
    bad = tmp_path / 'themes' / 'bad'
    bad.mkdir()
    (bad / 'theme.json').write_text('{broken')
    assert [theme.id for theme in discover_themes(tmp_path)] == ['good']
    records = theme_records(tmp_path)
    assert len(records) == 2
    assert any(isinstance(row, dict) and 'cannot parse' in row['error'] for row in records)



def test_shared_theme_resolver_uses_requested_theme_without_warning(tmp_path):
    _write_theme(tmp_path, theme_id="paper")
    theme, warning = resolve_theme(tmp_path, "paper")
    assert theme is not None
    assert theme.id == "paper"
    assert warning is None


def test_shared_theme_resolver_falls_back_to_default_with_warning(tmp_path):
    _write_theme(tmp_path, theme_id="axiom")
    theme, warning = resolve_theme(tmp_path, "missing")
    assert theme is not None
    assert theme.id == "axiom"
    assert isinstance(warning, ThemeError)


def test_shared_theme_resolver_returns_core_baseline_without_themes(tmp_path):
    theme, warning = resolve_theme(tmp_path, "missing")
    assert theme is None
    assert warning is None


def test_release_theme_resolver_uses_axiom_even_with_other_installed_themes(tmp_path):
    _write_theme(tmp_path, theme_id="paper")
    _write_theme(tmp_path, theme_id="axiom")
    theme, warning = resolve_release_theme(tmp_path)
    assert theme is not None
    assert theme.id == "axiom"
    assert warning is None


def test_release_theme_resolver_refuses_silent_non_axiom_fallback(tmp_path):
    _write_theme(tmp_path, theme_id="paper")
    theme, warning = resolve_release_theme(tmp_path)
    assert theme is None
    assert isinstance(warning, ThemeError)
    assert "release theme 'axiom' is not installed" in str(warning)


def test_theme_loader_layers_dark_appearance_without_changing_theme_identity(tmp_path):
    _write_theme(
        tmp_path,
        theme_id="axiom",
        streamlit='[theme]\nbase="light"\nbackgroundColor="#ffffff"\n',
        dark=True,
    )
    theme = read_theme(tmp_path / "themes" / "axiom")

    assert theme.id == "axiom"
    assert theme.tokens_for("light")["rh-bg"] == "#ffffff"
    assert theme.tokens_for("dark")["rh-bg"] == "#101522"
    assert theme.resolved_tokens("dark")["rh-primary"] == "#6ea2ff"
    assert "appearance-dark.css" in str(theme.appearances["dark"].stylesheets[0])
    assert "#101522" in theme.css_text("dark")
    assert "appearance-dark.css" not in theme.css_text("light")
    assert theme.streamlit_theme_for("light").name == "streamlit-theme.toml"
    assert theme.streamlit_theme_for("dark").name == "streamlit-theme-dark.toml"


def test_theme_loader_rejects_appearance_asset_escape(tmp_path):
    t = _write_theme(tmp_path)
    (tmp_path / "outside.css").write_text("body{}")
    manifest = json.loads((t / "theme.json").read_text())
    manifest["appearances"] = {
        "dark": {
            "tokens": {"rh-bg": "#111111"},
            "stylesheets": ["../../outside.css"],
        }
    }
    (t / "theme.json").write_text(json.dumps(manifest))

    with pytest.raises(ThemeError, match="escapes"):
        read_theme(t)


def test_theme_loader_rejects_invalid_appearance_streamlit_config(tmp_path):
    t = _write_theme(tmp_path)
    (t / "streamlit-theme-dark.toml").write_text(
        '[theme]\nbase="dark"\n\n[server]\nheadless=true\n'
    )
    manifest = json.loads((t / "theme.json").read_text())
    manifest["appearances"] = {
        "dark": {
            "streamlit_theme": "streamlit-theme-dark.toml",
        }
    }
    (t / "theme.json").write_text(json.dumps(manifest))

    with pytest.raises(ThemeError, match=r"only contain a \[theme\] table"):
        read_theme(t)


def test_core_bundles_current_axiom_release_theme():
    root = project_root()
    theme = read_theme(root / "themes" / "axiom")

    assert theme.id == "axiom"
    assert theme.name == "Axiom"
    assert theme.version == "1.1.14"
    assert theme.streamlit_theme_for("light").name == "streamlit-theme.toml"
    assert theme.streamlit_theme_for("dark").name == "streamlit-theme-dark.toml"
    assert theme.logo_path is not None
    assert theme.logo_path.name == "rank-hunter-mark.png"
    assert theme.logo_path.is_file()
    assert "dark" in theme.appearances


def test_core_distribution_contains_only_bundled_axiom_theme():
    themes = discover_themes(project_root())
    assert [(theme.id, theme.version) for theme in themes] == [("axiom", "1.1.14")]
