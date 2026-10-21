from pathlib import Path


COMMON = Path("rank42/ui_pages/common.py").read_text(encoding="utf-8")
COMPONENTS = Path("rank42/ui_components.py").read_text(encoding="utf-8")
CHARTS = Path("rank42/ui_pages/dashboard_charts.py").read_text(encoding="utf-8")
COMPLEXITY = Path("rank42/ui_pages/dashboard_complexity.py").read_text(encoding="utf-8")
CURVES = Path("rank42/ui_pages/curves.py").read_text(encoding="utf-8")
PIPELINES = Path("rank42/ui_pages/build_your_own.py").read_text(encoding="utf-8")
MODULE_ROW = Path("rank42/ui_assets/pipeline_module_row/index.html").read_text(encoding="utf-8")
STAGE_LIST = Path("rank42/ui_assets/pipeline_stage_list/index.html").read_text(encoding="utf-8")
EDITOR_ACTIONS = Path("rank42/ui_assets/pipeline_editor_actions/index.html").read_text(encoding="utf-8")


def test_runtime_theme_palette_is_published_for_iframe_consumers():
    assert 'st.session_state["_rh_appearance"]' in COMMON
    assert 'st.session_state["_rh_theme_palette"]' in COMMON
    assert "theme.resolved_tokens(appearance)" in COMMON
    for token in (
        "rh-card",
        "rh-card-2",
        "rh-surface-hover",
        "rh-surface-active",
        "rh-border",
        "rh-text",
        "rh-muted",
        "rh-primary",
        "rh-success",
        "rh-warning",
    ):
        assert f'"{token}"' in COMMON


def test_aggrid_uses_resolved_rank_hunter_palette_inside_component_iframe():
    assert "def _aggrid_custom_css" in COMPONENTS
    assert 'st.session_state.get("_rh_theme_palette")' in COMPONENTS
    assert '"--ag-background-color"' in COMPONENTS
    assert '"--ag-header-background-color"' in COMPONENTS
    assert '"--ag-selected-row-background-color"' in COMPONENTS
    assert '"html, body, #root, .stAgGrid, .ag-theme-streamlit' in COMPONENTS
    assert '".ag-root-wrapper, .ag-root-wrapper-body, .ag-root"' in COMPONENTS
    assert '".ag-row, .ag-row-even, .ag-row-odd, .ag-cell, .ag-cell-value"' in COMPONENTS
    assert '".ag-header, .ag-header-viewport, .ag-header-container, .ag-header-row, .ag-header-cell"' in COMPONENTS
    assert "custom_css=_aggrid_custom_css()" in COMPONENTS


def test_dashboard_altair_surface_uses_resolved_appearance():
    assert 'background="white"' not in CHARTS
    assert 'fill="white"' not in CHARTS
    assert 'st.session_state.get("_rh_theme_palette")' in CHARTS
    assert 'palette.get("rh-card")' in CHARTS
    assert "configure_axis(" in CHARTS
    assert "configure_legend(" in CHARTS
    assert "theme=None" in CHARTS


def test_rank_evidence_chart_has_no_inline_light_surface():
    assert "background:#fff" not in COMPLEXITY.lower()
    assert "background-color:#fff" not in COMPLEXITY.lower()
    assert 'class="rh-rank-evidence-chart-shell"' in COMPLEXITY


def test_curves_vega_charts_use_resolved_appearance():
    assert "def _appearance_vega_spec" in CURVES
    assert 'st.session_state.get("_rh_theme_palette")' in CURVES
    assert 'palette.get("rh-card")' in CURVES
    assert "theme=None" in CURVES
    assert CURVES.count("_themed_vega_lite_chart(") >= 5


def test_curves_summary_and_filter_surfaces_use_semantic_tokens():
    assert "background:#FFFFFF;" not in CURVES
    assert "background: #FFFFFF !important;" not in CURVES
    assert "background:var(--rh-card,#FFFFFF);" in CURVES
    assert "background: var(--rh-card,#FFFFFF) !important;" in CURVES


def test_pipeline_components_receive_resolved_palette():
    assert "def _component_theme_palette" in PIPELINES
    assert PIPELINES.count("palette=_component_theme_palette()") == 3
    assert "var(--rh-card" in PIPELINES
    assert "var(--rh-border" in PIPELINES

    for source in (MODULE_ROW, STAGE_LIST, EDITOR_ACTIONS):
        assert "args.palette" in source
        assert 'palette["rh-text"]' in source
        assert 'palette["rh-card"]' in source
        assert 'palette["rh-border"]' in source


def test_dark_native_controls_cover_current_streamlit_widget_structure():
    assert '[data-testid="stExpandSidebarButton"]' in COMMON
    assert '[data-testid="stSidebarCollapseButton"] [data-testid="stIconMaterial"]' in COMMON
    assert '[data-testid="stExpandSidebarButton"] [data-testid="stIconMaterial"]' in COMMON
    assert '[data-testid="stSidebarCollapseButton"] svg' in COMMON
    assert 'background: transparent !important;' in COMMON
    assert 'border: 0 !important;' in COMMON
    assert 'outline: none !important;' in COMMON
    assert '[data-testid="stTextInputRootElement"]' in COMMON
    assert '[data-testid="stDateInputField"]' in COMMON
    assert '[data-testid="stTimeInputTimeDisplay"]' in COMMON
    assert '[data-testid="stDateInputCalendar"]' in COMMON
    assert '[role="spinbutton"]' in COMMON
    assert '.gdg-search-bar' in COMMON
    assert '[data-testid="stDataFrame"] canvas' not in COMMON
    assert '.stDataFrameGlideDataEditor' not in COMMON
    assert '.dvn-scroller' not in COMMON
    assert 'background: var(--rh-native-control-bg) !important;' in COMMON
    assert 'border-color: var(--rh-native-control-border) !important;' in COMMON
    assert 'background-color: var(--rh-native-table-bg) !important;' in COMMON


def test_native_dark_overrides_are_dark_only():
    start = COMMON.index('_DARK_NATIVE_SURFACE_CSS = """')
    end = COMMON.index('def _native_appearance_css', start)
    dark_css = COMMON[start:end]

    assert 'color-scheme: dark' in dark_css
    assert 'stTextInputRootElement' in dark_css
    assert 'stDateInputField' in dark_css
    assert 'stTimeInputTimeDisplay' in dark_css
    assert '[data-testid="stDataFrame"] canvas' not in dark_css
    assert 'return _DARK_NATIVE_SURFACE_CSS if str(appearance).lower() == "dark" else ""' in COMMON
