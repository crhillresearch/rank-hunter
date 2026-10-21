import inspect
from pathlib import Path

from rank42 import ui_components, ui_store
from rank42.ui_pages import (
    analyze_page,
    campaigns_page,
    common,
    descent_page,
    independence_page,
    saturation_page,
    lattices_page,
    database_page,
    diagnostics_page,
    external_catalog,
    results_page,
    quartics_page,
    target_curve,
    themes_page,
    corpus_page,
    candidate_pools_page,
    curves as curves_page,
)


ROOT = Path(__file__).resolve().parents[1]


def test_global_page_end_runway_is_normal_flow_not_scroll_css():
    source = (ROOT / "rank42" / "ui.py").read_text(encoding="utf-8")
    common_source = (
        ROOT / "rank42" / "ui_pages" / "common.py"
    ).read_text(encoding="utf-8")

    assert "def _page_end_runway():" in source
    assert "rh-page-end-runway" in source
    assert "height:3.5rem" in source
    assert "pointer-events:none" in source
    assert "_render_route(db)\n        _page_end_runway()" in source
    assert "padding-bottom: 5rem" not in common_source
    assert "scroll-padding-bottom" not in common_source


def test_analysis_navigation_follows_proof_workflow_and_hides_research_shells():
    source = (ROOT / "rank42" / "ui.py").read_text(encoding="utf-8")
    groups_start = source.index("groups = {")
    groups_end = source.index(
        "active_page = st.session_state.get(\"rh_page\", \"Dashboard\")",
        groups_start,
    )
    groups = source[groups_start:groups_end]
    analysis_start = groups.index('"Analysis": [')
    data_start = groups.index('"Data": [', analysis_start)
    analysis = groups[analysis_start:data_start]

    descent = analysis.index('("Descent", "Descent")')
    quartics = analysis.index('("Quartics", "Quartics")')
    points = analysis.index('("Points", "Points")')
    independence = analysis.index('("Independence", "Independence")')
    saturation = analysis.index('("Saturation", "Saturation")')
    lattices = analysis.index('("Lattices", "Lattices")')
    assert descent < quartics < points < independence < saturation < lattices

    assert '("Work Center", "Analyze")' not in analysis
    assert '("Landscape", "Landscape"' not in analysis
    assert '("Rank Proof", "Rank Proof")' not in analysis


def test_selectable_dataframe_can_require_explicit_row_click_without_checkboxes():
    source = inspect.getsource(ui_components.selectable_dataframe)
    options = inspect.getsource(ui_components._aggrid_options)

    assert "allow_empty=False" in source
    assert "if allow_empty:" in source
    assert "preselected=[] if default_index is None else [default_index]" in source
    assert "use_checkbox=False" in options
    assert "suppressRowClickSelection=False" in options
    assert "data_return_mode=DataReturnMode.AS_INPUT" in source


def test_dark_runtime_native_surface_overrides_are_appearance_gated():
    source = (ROOT / "rank42" / "ui_pages" / "common.py").read_text(encoding="utf-8")

    assert '_DARK_NATIVE_SURFACE_CSS = """' in source
    assert 'color-scheme: dark' in source
    assert '[data-testid="stSidebarCollapseButton"]' in source
    assert '[data-testid="stSidebarCollapsedControl"]' in source
    assert '[data-testid="stTextInput"] [data-baseweb="input"]' in source
    assert '[data-testid="stTextInput"] [data-baseweb="base-input"]' in source
    assert 'background-color: var(--rh-native-control-bg) !important' in source
    assert 'caret-color: var(--rh-text,#f3f6fb) !important' in source
    assert '[data-testid="stSelectbox"] [data-baseweb="select"] > div' in source
    assert 'label[data-baseweb="radio"]:has(input:not(:checked))' in source
    assert 'div[class*="-page-nav"] [data-testid="stBaseButton-primary"]' in source
    assert '[data-testid="stTable"]' in source
    assert '[data-testid="stDataFrame"] canvas' not in source
    assert '[data-testid="stDataFrame"] .gdg-search-bar' in source
    assert '--rh-active-campaign-pin-bg' in source
    assert 'var(--rh-success,#1fa65d) 34%' in source
    assert 'def _native_appearance_css(appearance: str)' in source
    assert common._native_appearance_css("light") == ""
    assert "color-scheme: dark" in common._native_appearance_css("dark")


def test_dark_page_stragglers_use_semantic_palette_handoffs():
    pipelines = (
        ROOT / "rank42" / "ui_pages" / "build_your_own.py"
    ).read_text(encoding="utf-8")
    quartics = (
        ROOT / "rank42" / "ui_pages" / "quartics_page.py"
    ).read_text(encoding="utf-8")
    lattices = (
        ROOT / "rank42" / "ui_pages" / "lattices_page.py"
    ).read_text(encoding="utf-8")

    assert 'st-key-byo-functional-features' in pipelines
    assert '[data-baseweb="select"] svg' in pipelines
    assert 'fill: currentColor !important' in pipelines

    assert "def _appearance_vega_spec(spec):" in quartics
    assert 'palette.get("rh-card")' in quartics
    assert 'themed["background"] = card' in quartics
    assert 'theme=None' in quartics
    assert "_themed_vega_lite_chart(" in quartics

    assert "def _themed_altair_chart(chart, **kwargs):" in lattices
    assert 'palette.get("rh-card")' in lattices
    assert '.configure(background=card)' in lattices
    assert '.configure_view(fill=card, stroke=None)' in lattices
    assert 'return st.altair_chart(themed, theme=None, **kwargs)' in lattices


def test_shared_layout_has_narrow_screen_column_and_overflow_safeguards():
    source = (ROOT / "rank42" / "ui_pages" / "common.py").read_text(encoding="utf-8")

    assert '_RESPONSIVE_LAYOUT_CSS = """' in source
    assert '@media (max-width: 900px)' in source
    assert '@media (max-width: 640px)' in source
    assert 'flex-wrap: wrap !important' in source
    assert 'flex: 1 1 16rem !important' in source
    assert 'min-width: min(16rem, 100%) !important' in source
    assert 'overflow-x: auto' in source
    assert 'overscroll-behavior-x: contain' in source
    assert '[data-testid="stIFrame"]' in source
    assert 'st.html(_RESPONSIVE_LAYOUT_CSS)' in source


def test_curve_detail_renders_next_action_once_across_detail_views():
    overview = inspect.getsource(curves_page._render_overview)
    detail = inspect.getsource(curves_page._render_curve_detail)

    assert "_render_next_action(db, row)" not in overview
    assert detail.count("_render_next_action(db, row)") == 1
    assert detail.index("_render_next_action(db, row)") > detail.index(
        "_render_overview(db, row, point_stats)"
    )


def test_campaign_header_and_curve_summary_have_mobile_fallbacks():
    campaigns = (
        ROOT / "rank42" / "ui_pages" / "campaigns_landing_panel.py"
    ).read_text(encoding="utf-8")
    curves = (ROOT / "rank42" / "ui_pages" / "curves.py").read_text(encoding="utf-8")

    assert '@media (max-width: 900px)' in campaigns
    assert 'st-key-rh-campaigns-header-title' in campaigns
    assert 'display: none !important' in campaigns
    assert '@media (max-width: 640px)' in campaigns
    assert 'justify-content: flex-start !important' in campaigns

    assert '@media (max-width: 560px)' in curves
    assert '.rh-curve-summary-grid{grid-template-columns:minmax(0,1fr);}' in curves


def test_ui_pages_do_not_use_eager_native_streamlit_tabs():
    offenders = []
    paths = list((ROOT / "rank42" / "ui_pages").rglob("*.py"))
    paths.append(ROOT / "rank42" / "ui.py")
    for path in sorted(paths):
        source = path.read_text(encoding="utf-8")
        if "st.tabs(" in source:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_stale_job_finalization_never_reconciles_whole_pool():
    source = inspect.getsource(ui_store.finalize_pool_search)

    assert "reconcile_candidates" in source
    assert "reconcile_pool(" not in source
    assert "candidate_ids" in source


def test_raw_log_is_always_visible_and_live_refresh_uses_thread_local_db():
    detail = inspect.getsource(results_page.render_job_scientific_result)
    live = inspect.getsource(results_page._render_live_raw_log_fragment)
    wrapper = inspect.getsource(results_page._render_job_raw_log)
    db_path = inspect.getsource(results_page._database_path)

    assert "'Load raw log'" not in detail
    assert "_render_job_raw_log(db, row)" in detail
    assert '@st.fragment(run_every="1s")' in live
    assert "fresh_db = connect_existing(db_path)" in live
    assert "fresh = get_job(fresh_db, int(job_id))" in live
    assert 'render_raw_log(read_log(fresh["log_path"]))' in live
    assert "fresh_db.close()" in live
    assert 'terminal = str(fresh["status"]) not in ACTIVE' in live
    assert "st.rerun()" in live
    assert "PRAGMA database_list" in db_path

    assert 'st.markdown("#### Raw log")' in wrapper
    assert 'if str(row["status"]) in ACTIVE:' in wrapper
    assert "db_path = _database_path(db)" in wrapper
    assert '_render_live_raw_log_fragment(db_path, int(row["id"]))' in wrapper
    assert 'render_raw_log(read_log(row["log_path"]))' in wrapper
    assert "refreshing every second" in wrapper


def test_results_remove_redundant_parsed_scientific_result_presentation():
    source = (
        ROOT / "rank42" / "ui_pages" / "results_page.py"
    ).read_text(encoding="utf-8")
    detail = inspect.getsource(results_page.render_job_scientific_result)

    assert "Parsed scientific result" not in source
    assert "results-scientific-" not in source
    assert "st.json(parsed)" not in source
    assert "_render_job_raw_log(db, row)" in detail
    assert "_hunt_recap(db, row, summary, science_python)" in detail
    assert "_dynamic_stats(row, summary)" in detail
    assert "summarize_job" in source


def test_database_page_has_no_render_time_integrity_scan_or_backup_read():
    page = inspect.getsource(database_page.page)
    overview = inspect.getsource(database_page._database_overview)
    transfer = inspect.getsource(database_page._database_import_export)

    assert "st.tabs(" not in page
    assert '["Overview", "Import / Export", "Advanced SQL"]' in page

    integrity_button = overview.index('"Run integrity check"')
    integrity_query = overview.index('"PRAGMA integrity_check"', integrity_button)
    assert integrity_button < integrity_query

    counts_button = overview.index('"Load table counts"')
    counts_query = overview.index("SELECT COUNT(*)", counts_button)
    assert counts_button < counts_query

    assert "database_backup_download" not in transfer
    assert "p.read_bytes()" not in transfer
    assert "data=_deferred_backup_bytes(p)" in transfer
    assert "Browser download disabled for this large backup" in transfer


def test_campaign_detail_executes_only_selected_section():
    source = inspect.getsource(campaigns_page._render_campaign_detail)

    assert "st.tabs(" not in source
    assert '["Overview", "Progress", "Schedules", "Notebook", "Handoff"]' in source
    assert 'if current == "Notes":' in source
    assert 'current = "Notebook"' in source
    assert 'if choice == "Overview":' in source
    assert 'elif choice == "Progress":' in source
    assert 'elif choice == "Schedules":' in source
    assert 'elif choice == "Notebook":' in source
    assert "campaign_research_brief(db, campaign_id)" in source
    assert "campaign_snapshot(db, campaign_id)" in source



def test_quartics_executes_only_selected_workspace():
    source = inspect.getsource(quartics_page.page)
    covering_fragment = inspect.getsource(
        quartics_page._render_covering_selector_fragment
    )

    assert "st.tabs(" not in source
    assert '["Overview", "Coverings", "Search", "Research", "Audit"]' in source
    assert 'variant="page"' in source
    assert 'variant="line"' in source
    assert 'label="Quartics workspace"' not in source
    assert "_render_curve_browser(db, rows, wanted)" in source
    assert source.index("_render_curve_browser(db, rows, wanted)") < source.index("workspace = tabs(")
    assert "height:.45rem" in source
    assert 'if workspace == "Overview":' in source
    assert 'elif workspace == "Coverings":' in source
    assert "_render_covering_selector_fragment(" in source
    assert "selectable_dataframe(" not in source
    assert "selectable_dataframe(" in covering_fragment
    assert "selectable_dataframe_rows(" not in covering_fragment
    assert "checkboxes=True" not in covering_fragment
    assert 'elif workspace == "Search":' in source
    assert 'elif workspace == "Research":' in source
    assert "_render_intelligence_board(row, summary)" in source
    assert "_render_overview_visuals(" not in source
    assert '["Coverage Map", "Yield & Search", "Coverage Table"]' in source
    assert 'if search_view == "Coverage Map":' in source
    assert "quartic_coverage_density(" in source
    assert "_render_search_coverage_map(" in source
    assert 'elif search_view == "Coverage Table":' in source
    assert "_render_search_coverage(coverage)" in source
    assert "_render_model_yield_charts(yield_rows)" in source
    assert '["Family signatures", "Exact-model comparison"]' in source
    assert 'if research_view == "Family signatures":' in source
    assert "_render_family_signature_heatmap(signature)" in source
    assert "_render_research_comparison(comparison)" in source




def test_shared_curve_scope_latest_campaign_and_all_views():
    rows = [
        {"id": 1, "updated_at": "2026-09-01T00:00:00+00:00"},
        {"id": 2, "updated_at": "2026-09-03T00:00:00+00:00"},
        {"id": 3, "updated_at": "2026-09-02T00:00:00+00:00"},
        {"id": 4, "updated_at": "2026-08-31T00:00:00+00:00"},
    ]

    latest = common.curve_selector_scope_rows(
        rows,
        scope="Latest",
        wanted=4,
        latest_limit=2,
    )
    assert [row["id"] for row in latest] == [2, 3, 4]

    campaign = common.curve_selector_scope_rows(
        rows,
        scope="Campaign",
        campaign_curve_ids=[1, 3],
    )
    assert [row["id"] for row in campaign] == [3, 1]

    all_rows = common.curve_selector_scope_rows(rows, scope="All")
    assert [row["id"] for row in all_rows] == [1, 2, 3, 4]


def test_shared_active_curve_selector_owns_native_filter_experience():
    source = inspect.getsource(common.active_curve_selector)
    close_source = inspect.getsource(common._close_active_curve_filter)

    assert 'with st.popover(' in source
    assert 'fa_markdown_icon("filter", alt="Filter curves")' in source
    assert 'type="tertiary"' in source
    assert 'on_change="rerun"' in source
    assert '["All", "Latest", "Campaign"]' in source
    assert 'on_change=_close_active_curve_filter' in source
    assert 'st.session_state[str(filter_key)] = False' in close_source
    assert 'border:0!important' in source
    assert 'width:22px;height:22px;display:block;margin:0' in source
    assert 'label_visibility="collapsed"' in source
    assert 'campaign_curve_ids_readonly(' in source


def test_single_active_curve_pages_use_shared_curve_browser():
    page_sources = {
        "Quartics": inspect.getsource(quartics_page._render_curve_browser),
        "Independence": inspect.getsource(independence_page.page),
        "Descent": inspect.getsource(descent_page.page),
        "Saturation": inspect.getsource(saturation_page.page),
        "Lattices": inspect.getsource(lattices_page.page),
        "Target": inspect.getsource(target_curve.page),
    }

    for name, source in page_sources.items():
        assert "active_curve_selector(" in source, name

    assert 'state_prefix="quartics"' in page_sources["Quartics"]
    assert 'state_prefix="independence"' in page_sources["Independence"]
    assert "state_prefix='descent'" in page_sources["Descent"]
    assert 'state_prefix="saturation"' in page_sources["Saturation"]
    assert 'state_prefix="lattice"' in page_sources["Lattices"]
    assert "state_prefix='target'" in page_sources["Target"]


def test_case_board_and_analysis_shell_are_retired_into_curves():
    source = (ROOT / "rank42" / "ui.py").read_text(encoding="utf-8")
    groups = source[
        source.index("groups = {"):
        source.index("active_page = st.session_state.get(\"rh_page\", \"Dashboard\")")
    ]
    routes = source[source.index("routes = {"):]

    assert '("Case Board", "Case Board", ":material/view_kanban:")' not in groups
    assert '"Case Board": analyze_page.case_board_page' not in routes
    assert '"Analyze": analyze_page.page' not in routes
    assert '"Landscape": landscape_page.page' in routes
    assert '("Work Center", "Analyze")' not in groups
    assert '("Landscape", "Landscape"' not in groups
    assert '"Work Center": "Curves"' in source
    assert 'requested in {"Analyze", "Case Board", "Work Center"}' in source
    assert '"Rank Proof": "Descent"' in source


def test_lattices_is_the_canonical_visible_route():
    shell = (ROOT / "rank42" / "ui.py").read_text(encoding="utf-8")
    page = inspect.getsource(lattices_page.page)

    assert '("Lattices", "Lattices"),' in shell
    assert '"Lattices": lattices_page.page' in shell
    assert '"MW Geometry": "Lattices"' in shell
    assert '"Lattices & Heights": "Lattices"' in shell
    assert '"Lattices": PAGE_ICONS["Lattices"]' in shell
    assert 'title(\n        "Lattices",' in page


def test_quartics_curve_browser_remains_native_streamlit_only():
    source = inspect.getsource(quartics_page._render_curve_browser)
    page_source = (
        ROOT / "rank42" / "ui_pages" / "quartics_page.py"
    ).read_text(encoding="utf-8")
    asset = ROOT / "rank42" / "ui_assets" / "quartics_curve_browser" / "index.html"

    assert "active_curve_selector(" in source
    assert 'state_prefix="quartics"' in source
    assert 'widget_prefix="quartics-curve"' in source
    assert 'streamlit.components.v1' not in page_source
    assert 'declare_component(' not in page_source
    assert not asset.exists()


def test_shared_page_title_supports_material_icons_and_global_breadcrumbs():
    source = (ROOT / "rank42" / "ui_pages" / "common.py").read_text(encoding="utf-8")
    shell = (ROOT / "rank42" / "ui.py").read_text(encoding="utf-8")
    title_source = inspect.getsource(common.title)

    assert 'QUARTICS_ICON = ":material/control_camera:"' in source
    assert '"Quartics": QUARTICS_ICON' in source
    assert '":material/space_dashboard:"' in source
    assert '":material/line_curve:"' in source
    assert '":material/salinity:"' in source
    assert 'material_icon = icon_text.removeprefix(":material/").removesuffix(":")' in title_source
    assert 'st.title(f":material/{material_icon}: {name}", anchor=False)' in title_source
    assert 'NAV_ICONS.get(page, ":material/chevron_right:")' in shell

    assert "def page_breadcrumb_path" in source
    assert "def _render_page_breadcrumb" in source
    assert '"Dashboard", "Dashboard"' in source
    assert '"Corpora": "Libraries"' in source
    assert '"External Catalog": "Catalogs"' in source
    assert 'st.session_state["rh_page"] = str(route)' in source
    assert 'type="tertiary"' in source
    assert "aria-current='page'" in source
    assert "_render_page_breadcrumb(name)" in title_source
    assert title_source.index("_render_page_breadcrumb(name)") > title_source.index("st.title(")
    assert "st.caption(str(subtitle))" not in title_source
    assert "if len(path) == 1:" in source
    assert "margin-top:-.5rem" in source
    assert "min-height:1.05rem" in source
    assert "width:max-content" in source
    assert "[data-testid='stButton']::after" in source
    assert "left:calc(100% + .18rem)" in source
    assert "left:4.45rem" not in source
    assert 'trailing = json.dumps(f"› {leaf}", ensure_ascii=False)' in source
    assert "pointer-events:none" in source
    breadcrumb_source = inspect.getsource(common._render_page_breadcrumb)
    assert "st.columns(" not in breadcrumb_source


def test_page_breadcrumb_path_uses_canonical_shell_route_labels():
    assert common.page_breadcrumb_path(
        "Curves",
        {"rh_page": "Curves"},
    ) == (("Dashboard", "Dashboard"), ("Curves", None))
    assert common.page_breadcrumb_path(
        "Libraries",
        {"rh_page": "Corpora"},
    ) == (("Dashboard", "Dashboard"), ("Libraries", None))
    assert common.page_breadcrumb_path(
        "Catalogs",
        {"rh_page": "External Catalog"},
    ) == (("Dashboard", "Dashboard"), ("Catalogs", None))
    assert common.page_breadcrumb_path(
        "Dashboard",
        {"rh_page": "Dashboard"},
    ) == (("Dashboard", None),)
    assert common.page_breadcrumb_path(
        "Curve Explorer",
        {"rh_page": "extension:curve_explorer:curve_explorer"},
    ) == (("Dashboard", "Dashboard"), ("Curve Explorer", None))


def test_shared_navigation_prefers_themeable_native_tabs_and_keeps_shadcn_fallback():
    source = (ROOT / "rank42" / "ui_components.py").read_text(encoding="utf-8")
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    axiom = (ROOT / "themes" / "axiom" / "theme.css").read_text(encoding="utf-8")

    segmented = source.index('if hasattr(st, "segmented_control")')
    shadcn_tabs = source.index('if shadcn is not None and hasattr(shadcn, "tabs")')
    assert segmented >= 0
    assert shadcn_tabs >= 0
    assert segmented < shadcn_tabs
    assert "st.segmented_control(" in source
    assert "shadcn.tabs(" in source
    assert 'variant="default"' in source
    assert 'variant=str(variant or "default")' in source
    assert 'str(variant or "").lower() == "page"' in source
    assert "background:var(--rh-card,#fff)!important" in source
    assert "background:var(--rh-surface-hover,#f6f8fb)!important" in source
    assert "color:var(--rh-text-secondary,#31333f)!important" in source
    assert "[data-testid='stBaseButton-secondary']" in source
    assert 'button[data-variant="segmented_control"]' in axiom
    assert 'button[data-variant="segmented_control"][data-selected="true"]' in axiom
    assert 'hasattr(shadcn, "combobox")' in source
    assert "shadcn.combobox(" in source
    assert "streamlit-shadcn-ui==1.3.0" in requirements

def test_settings_page_uses_line_subnavigation_and_current_section_hierarchy():
    source = (
        ROOT / "rank42" / "ui_pages" / "settings_page.py"
    ).read_text(encoding="utf-8")

    assert '["Runtimes", "Services", "About"]' in source
    assert 'variant="line"' in source
    assert "with region(" not in source
    assert 'st.markdown("#### Runtime status")' in source
    assert 'st.markdown("#### Runtime executables")' in source
    assert 'st.markdown("#### Default time budgets")' in source
    assert 'st.subheader("ICARM")' in source
    assert 'st.subheader("System health")' not in source
    assert '"Open Diagnostics"' not in source


def test_diagnostics_tabs_reuse_cached_snapshot():
    page = inspect.getsource(diagnostics_page.page)
    cached = inspect.getsource(diagnostics_page._diagnostic_result)

    assert "st.tabs(" not in page
    assert '["Overview", "Runtime", "Plugins & Libraries", "State", "Legacy Auto", "Report"]' in page
    assert "diagnostics_result_cache" in cached
    assert "collect_diagnostics(" in cached
    assert "collect_diagnostics(" not in page
    assert "force=bool(deep or refresh)" in page
    assert 'st.session_state.pop("diagnostics_result_cache", None)' not in page
    assert "st.session_state[key] = fresh" in cached


def test_results_page_bounds_and_caches_log_parsing():
    pairs = inspect.getsource(results_page._pairs)
    summary = inspect.getsource(results_page._summary_for_job)
    workspace = inspect.getsource(results_page.render_jobs_results)
    detail = inspect.getsource(results_page._detail)
    scientific = inspect.getsource(results_page.render_job_scientific_result)
    recap = inspect.getsource(results_page._hunt_recap)
    page = inspect.getsource(results_page.page)

    assert "limit=100" in pairs
    assert "list_jobs(db, limit=limit)" in pairs
    assert "_summary_for_job(row)" in pairs
    assert "_results_summary_cache" in summary
    assert "_job_log_signature(row)" in summary

    assert "[50,100,250,500]" in workspace
    assert "index=1" in workspace
    assert "limit=history_limit" in workspace
    assert "Open latest hunt recap" not in workspace
    assert "results-latest-hunt" not in workspace
    assert "st.divider()" not in workspace
    assert "with f1:" in workspace
    assert "with f2:" in workspace
    assert "with f3:" in workspace

    assert "render_job_scientific_result(" in detail
    assert "'Load raw log'" not in scientific
    assert "_render_pipeline_retention_notice(db, row)" in scientific
    assert "_render_job_raw_log(db, row)" in scientific
    assert scientific.index("_render_pipeline_retention_notice(db, row)") < scientific.index(
        "_render_job_raw_log(db, row)"
    )
    assert scientific.index("_render_job_raw_log(db, row)") < scientific.index(
        "_hunt_recap(db, row, summary, science_python)"
    )

    retention_notice = inspect.getsource(results_page._render_pipeline_retention_notice)
    assert "pipeline_retention_quartic_summary(db, row)" in retention_notice
    assert "persisted quartic" in retention_notice
    assert "retention floor" in retention_notice
    assert "curve-scoped" in retention_notice
    assert "Parsed scientific result" not in scientific
    assert "with expander('Command'" not in scientific
    assert "command_json" not in scientific

    assert "With native hits or rank growth only" not in recap
    assert "Show all new native x-fibers" not in recap
    assert "Show native x-fibers" not in recap
    assert "selectable_dataframe(" not in recap
    assert "st.dataframe(" in recap
    assert "'Reopen candidate'" in recap
    assert "'Open selected curve'" in recap
    assert "st.session_state['curves_selected_id']" in recap
    assert "st.session_state['rh_page'] = 'Curves'" in recap
    assert "st.rerun()" in recap
    assert "show_native = any(" in recap
    assert "show_mapped = any(" in recap
    assert "show_completed = any(" in recap

    raw_wrapper = inspect.getsource(results_page._render_job_raw_log)
    assert '_render_live_raw_log_fragment(db_path, int(row["id"]))' in raw_wrapper
    assert 'render_raw_log(read_log(row["log_path"]))' in raw_wrapper

    assert "render_jobs_results(db, ctx)" in page


def test_theme_preview_bytes_are_cached_by_file_mtime():
    markup = inspect.getsource(themes_page._preview_markup)
    cached = inspect.getsource(themes_page._preview_image_data)

    assert "image.stat().st_mtime_ns" in markup
    assert "_preview_image_data(str(image), mtime_ns)" in markup
    assert "@st.cache_data" in inspect.getsource(themes_page)
    assert "path.read_bytes()" in cached


def test_candidate_pool_ranking_labels_distinguish_library_rank_from_nagao():
    known_rank = {
        "metadata_json": '{"ranking_kind":"known_rank","ranking_value":9}',
        "score": 9.0,
    }
    nagao = {
        "metadata_json": '{"ranking_kind":"nagao","ranking_value":7.25}',
        "score": 7.25,
    }

    assert candidate_pools_page._candidate_ranking_view(known_rank) == (
        "Known rank",
        9,
    )
    assert candidate_pools_page._candidate_ranking_view(nagao) == (
        "Nagao",
        7.25,
    )


def test_corpus_browser_bounds_default_preview():
    source = inspect.getsource(corpus_page._render_plugin_libraries)

    assert '"Rows to display"' in source
    assert "[100, 250, 500, 1000, 2000]" in source
    assert "index=1" in source
    assert "limit=preview_limit" in source
    assert "limit=2000" not in source


def test_all_pages_use_strip_only_live_polling():
    source = (ROOT / "rank42" / "ui.py").read_text(encoding="utf-8")

    assert "def _live_job_strip" in source
    assert "notifications=False" in source
    assert "active_campaign_pin(db)" in source
    assert "_render_route(db)" in source
    assert "def _live_main_content" not in source
    assert "def _live_independence_job_strip" not in source
    assert "_page_supports_live_refresh" not in source



def test_shell_startup_never_purges_scientific_curves():
    source = (ROOT / "rank42" / "ui.py").read_text(encoding="utf-8")

    assert "purge_zero_evidence_curves" not in source
    assert "_rh_zero_cleanup_done" not in source



def test_live_polling_defers_completion_notifications_to_full_rerun():
    source = (ROOT / "rank42" / "ui.py").read_text(encoding="utf-8")

    live_start = source.index("def _live_job_strip")
    live_end = source.index("\n            _live_job_strip()", live_start)
    live_source = source[live_start:live_end]
    assert "notifications=False" in live_source
    assert "if not active:" in live_source
    assert "st.rerun()" in live_source

    normal_start = source.index("else:", live_end)
    normal_source = source[normal_start:source.index("_render_route(db)", normal_start)]
    assert "job_strip(" in normal_source
    assert "ctx.db_path" in normal_source
    assert "theme=ctx.theme" in normal_source
    assert "appearance=ctx.appearance" in normal_source



def test_legacy_shell_routes_normalize_to_canonical_session_state():
    from rank42 import ui

    cases = [
        ("Family Search", "Search", {"search_tab": "Family"}),
        ("General Hunt", "Search", {"search_tab": "General"}),
        ("Auto Search", "Auto", {}),
        ("Auto Hunter", "Auto", {}),
        ("Build Your Own", "Pipelines", {}),
        ("Builder", "Pipelines", {}),
        ("Target Curve", "Target", {}),
        (
            "Candidate Generate",
            "Candidates",
            {
                "candidates_tab": "Generate",
                "_rh-candidates-main-tabs_value": "Generate",
            },
        ),
        (
            "Candidate Pools",
            "Candidates",
            {
                "candidates_tab": "Pools",
                "_rh-candidates-main-tabs_value": "Pools",
            },
        ),
        ("Work Center", "Curves", {}),
        ("Rank Proof", "Descent", {}),
        ("Results", "Jobs", {"jobs_section_pending": "Results"}),
        (
            "Plugin Families",
            "Plugins",
            {
                "plugins_tab": "Families",
                "_rh-plugins-main-tabs_value": "Families",
            },
        ),
        (
            "Plugin Extensions",
            "Plugins",
            {
                "plugins_tab": "Extensions",
                "_rh-plugins-main-tabs_value": "Extensions",
            },
        ),
    ]
    for requested, expected, extra in cases:
        state = {"rh_page": requested}
        assert ui._normalize_route(requested, state) == expected
        assert state["rh_page"] == expected
        for key, value in extra.items():
            assert state[key] == value


def test_legacy_case_board_route_opens_the_selected_curve_detail():
    from rank42 import ui

    state = {"rh_page": "Case Board", "case_board_curve_id": 42}

    assert ui._normalize_route("Case Board", state) == "Curves"
    assert state["rh_page"] == "Curves"
    assert state["curves_detail_id"] == 42


def test_canonical_candidates_route_preserves_clicked_page_navigation():
    from rank42 import ui

    for clicked, stale in (("Pools", "Generate"), ("Generate", "Pools")):
        state = {
            "rh_page": "Candidates",
            "candidates_tab": stale,
            "_rh-candidates-main-tabs_value": clicked,
        }

        assert ui._normalize_route("Candidates", state) == "Candidates"
        assert state["candidates_tab"] == clicked
        assert state["_rh-candidates-main-tabs_value"] == clicked


def test_legacy_geometry_route_clears_stale_auto_tab_and_normalizes():
    from rank42 import ui

    for requested in ("Geometry Search", "Geometry"):
        state = {
            "rh_page": requested,
            "auto-main-tab": "Geometry Search",
        }
        assert ui._normalize_route(requested, state) == "Auto"
        assert state["rh_page"] == "Auto"
        assert "auto-main-tab" not in state


def test_shell_has_no_dead_geometry_import_or_duplicate_builder_route():
    source = (ROOT / "rank42" / "ui.py").read_text(encoding="utf-8")

    assert "geometry_search_page" not in source
    assert '"Builder": build_your_own.page' not in source
    assert '"Builder": ":material/tune:"' not in source
    assert '"Builder": "Pipelines"' in source



def test_route_normalization_happens_before_sidebar_navigation():
    source = (ROOT / "rank42" / "ui.py").read_text(encoding="utf-8")

    normalize_pos = source.index("page = _normalize_route(")
    navigation_pos = source.index("navigation(db, ctx, extensions)", normalize_pos)
    assert normalize_pos < navigation_pos


def test_sidebar_appearance_control_precedes_menu_groups():
    from rank42 import ui

    source = inspect.getsource(ui.navigation)
    dashboard_pos = source.index('_nav_button("Dashboard", "Dashboard")')
    groups_pos = source.index("for group, items in groups.items():")
    appearance_pos = source.index("_appearance_control(db, ctx)")
    telemetry_pos = source.index("_system_metrics_panel()")

    assert dashboard_pos < appearance_pos < groups_pos < telemetry_pos


def test_hidden_icarm_submit_route_is_intentional_and_reachable_from_curves():
    ui_source = (ROOT / "rank42" / "ui.py").read_text(encoding="utf-8")
    curves_source = (
        ROOT / "rank42" / "ui_pages" / "curves.py"
    ).read_text(encoding="utf-8")

    assert '"ICARM Submit": icarm_submit.page' in ui_source
    assert '"ICARM Submit"' not in ui_source[ui_source.index("groups = {"):ui_source.index("active_page = st.session_state.get(\"rh_page\", \"Dashboard\")")]
    assert 'st.session_state["rh_page"] = "ICARM Submit"' in curves_source



def test_point_ledger_is_visible_and_direct_route_remains():
    source = (ROOT / "rank42" / "ui.py").read_text(encoding="utf-8")
    groups_start = source.index("groups = {")
    groups_end = source.index(
        "active_page = st.session_state.get(\"rh_page\", \"Dashboard\")",
        groups_start,
    )
    navigation = source[groups_start:groups_end]
    routes = source[source.index("routes = {"):]

    quartics = navigation.index('("Quartics", "Quartics")')
    points = navigation.index('("Points", "Points")')
    assert quartics < points
    assert '"Points": points_page.page' in routes


def test_curves_and_analysis_preserve_point_handoffs():
    curves_source = (ROOT / "rank42" / "ui_pages" / "curves.py").read_text(
        encoding="utf-8"
    )
    analyze_source = (
        ROOT / "rank42" / "ui_pages" / "analyze_page.py"
    ).read_text(encoding="utf-8")

    assert "set_active_curve_id(" in curves_source
    assert '"points_curve_id"' in curves_source
    assert 'st.session_state["rh_page"] = "Points"' in curves_source

    assert 'point_ids=[int(rec["id"]) for rec in snapshot["actionable_points"]]' in analyze_source
    assert 'st.session_state["independence_point_id"] = ids[0]' in analyze_source
    assert 'st.session_state["independence_selected_point_ids"] = ids' in analyze_source


def test_results_navigation_is_consolidated_into_jobs():
    source = (ROOT / "rank42" / "ui.py").read_text(encoding="utf-8")
    groups_start = source.index("groups = {")
    groups_end = source.index(
        "active_page = st.session_state.get(\"rh_page\", \"Dashboard\")",
        groups_start,
    )
    groups = source[groups_start:groups_end]

    assert '("Results", "Results")' not in groups
    assert '"Results": results_page.page' not in source
    assert "results_page," not in source

    from rank42 import ui

    state = {"rh_page": "Results"}
    assert ui._normalize_route("Results", state) == "Jobs"
    assert state["rh_page"] == "Jobs"
    assert state["jobs_section_pending"] == "Results"


def test_plugins_are_canonical_manage_navigation_with_legacy_deep_links():
    source = (ROOT / "rank42" / "ui.py").read_text(encoding="utf-8")
    groups_start = source.index("groups = {")
    groups_end = source.index(
        "active_page = st.session_state.get(\"rh_page\", \"Dashboard\")",
        groups_start,
    )
    groups = source[groups_start:groups_end]
    routes = source[source.index("routes = {"):]

    assert '"Plugins": [' not in groups
    manage_start = groups.index('"Manage": [')
    system_start = groups.index('"System": [', manage_start)
    manage = groups[manage_start:system_start]

    jobs = manage.index('("Jobs", "Jobs")')
    campaigns = manage.index('("Campaigns", "Campaigns")')
    pipelines = manage.index('("Pipelines", "Pipelines")')
    plugins = manage.index('("Plugins", "Plugins")')
    assert jobs < campaigns < pipelines < plugins
    assert '("Case Board", "Case Board"' not in manage

    assert '"Plugins": plugins_page.page' in routes
    assert '"Plugin Families": lambda' not in routes
    assert '"Plugin Extensions": lambda' not in routes

    # Themes remains a preserved hidden route, not a visible navigation item.
    assert '"Themes": themes_page.page' in routes
    assert '("Themes", "Themes")' not in groups


def test_plugins_page_uses_one_canonical_shell_with_family_extension_subpages():
    source = (ROOT / "rank42" / "ui_pages" / "plugins_page.py").read_text(
        encoding="utf-8"
    )

    assert 'title(\n        "Plugins",' in source
    assert 'icon="extension"' in source
    assert '["Families", "Extensions"]' in source
    assert 'key="plugins-main-tabs"' in source
    assert 'variant="line"' in source
    assert 'width="content"' in source
    assert 'st.session_state["plugins_tab"] = choice' in source
    assert 'st.subheader("Families", anchor=False)' not in source
    assert 'st.subheader("Extensions", anchor=False)' not in source


def test_family_about_uses_campaign_like_selected_id_subpage_route():
    source = (
        ROOT / "rank42" / "ui_pages" / "plugins_page.py"
    ).read_text(encoding="utf-8")

    assert "def _render_family_detail_page" in source
    assert '"All families"' in source
    assert '"About · {plugin.name}"' in source
    assert 'st.session_state["plugin-family-manager-selected"] = plugin.id' in source
    assert '"plugin-family-manager-detail-open"' not in source

    page_start = source.index("def page(db, ctx, initial_tab=None):")
    tabs_pos = source.index('choice = tabs(', page_start)
    selected_pos = source.index(
        'selected_family_id = st.session_state.get("plugin-family-manager-selected")',
        page_start,
    )
    render_pos = source.index("_render_family_detail_page(", selected_pos)
    return_pos = source.index("return", render_pos)
    assert selected_pos < render_pos < return_pos < tabs_pos


def test_extensions_use_campaign_like_about_subpages_and_split_landing():
    source = (
        ROOT / "rank42" / "ui_pages" / "plugins_page.py"
    ).read_text(encoding="utf-8")

    assert "def _render_extension_detail_page" in source
    assert '"All extensions"' in source
    assert 'st.session_state["plugin-extension-manager-selected"] = plugin.id' in source
    assert 'st.markdown("### Features")' in source
    assert 'st.markdown("### Workspaces")' in source
    assert '"Installed Extensions"' not in source
    row_start = source.index("def _extension_manager_row")
    row_end = source.index("def _extension_detail", row_start)
    assert "_extension_description(plugin)" not in source[row_start:row_end]
    assert 'expanded=False' in source
    assert '"plugin-extension-manager-type"' not in source

    page_start = source.index("def page(db, ctx, initial_tab=None):")
    tabs_pos = source.index('choice = tabs(', page_start)
    selected_pos = source.index(
        'selected_extension_id = st.session_state.get("plugin-extension-manager-selected")',
        page_start,
    )
    render_pos = source.index("_render_extension_detail_page(", selected_pos)
    return_pos = source.index("return", render_pos)
    assert selected_pos < render_pos < return_pos < tabs_pos


def test_plugins_manager_uses_one_toolbar_and_scannable_detail_hierarchy():
    source = (
        ROOT / "rank42" / "ui_pages" / "plugins_page.py"
    ).read_text(encoding="utf-8")

    assert '"plugin-family-manager-search"' in source
    assert '"plugin-family-manager-status"' in source
    assert '"plugin-family-manager-capability"' in source
    assert '"plugin-family-manager-sort"' in source
    assert '"plugin-extension-manager-search"' in source
    assert '"plugin-extension-manager-status"' in source
    assert '"plugin-extension-manager-type"' not in source
    assert '"plugin-extension-manager-sort"' in source
    assert '"Install Plugins"' not in source
    assert "rh-family-toolbar-summary" not in source
    assert "def _family_manager_row" in source
    assert '"Needs rank verification"' in source
    assert 'show=len(values), collapse_after=len(values)' in source
    assert '"Active only"' not in source
    assert "plugin-families-active-only" not in source
    assert "plugin-extensions-active-only" not in source

    family_start = source.index("def _family_detail")
    family_end = source.index("def _extension_type_label", family_start)
    family = source[family_start:family_end]
    assert 'st.markdown("#### Variants")' in family
    assert 'st.markdown("#### Research assets")' in family
    assert 'st.markdown("#### Provenance & trust")' in family
    assert 'st.markdown("#### Validation record")' in family
    assert 'st.expander("Provenance & trust"' not in family

    ext_start = source.index("def _extension_detail")
    ext_end = source.index("def _render_unavailable_plugins", ext_start)
    extension = source[ext_start:ext_end]
    assert 'st.markdown(f"#### {interface_label}")' in extension
    assert 'st.markdown("#### Provenance & trust")' in extension
    assert 'st.markdown("#### Validation record")' in extension
    assert 'st.expander("Provenance & trust"' not in extension


def test_candidates_are_one_search_destination_with_scatter_plot_icon():
    source = (ROOT / "rank42" / "ui.py").read_text(encoding="utf-8")
    groups_start = source.index("groups = {")
    groups_end = source.index(
        "active_page = st.session_state.get(\"rh_page\", \"Dashboard\")",
        groups_start,
    )
    groups = source[groups_start:groups_end]

    search_start = groups.index('"Search": [')
    analysis_start = groups.index('"Analysis": [', search_start)
    search_group = groups[search_start:analysis_start]

    auto = search_group.index('("Auto", "Auto")')
    search = search_group.index('("Search", "Search")')
    target = search_group.index('("Target", "Target")')
    candidates = search_group.index('("Candidates", "Candidates")')
    assert auto < search < target < candidates

    assert '"Candidates": [' not in groups
    assert '("Generate", "Candidate Generate"' not in groups
    assert '("Pools", "Candidate Pools"' not in groups
    assert '"Candidates": PAGE_ICONS["Candidates"]' in source

def test_candidate_generation_uses_material_group_add_icon():
    source = (
        ROOT / "rank42" / "ui_pages" / "candidate_generate_page.py"
    ).read_text(encoding="utf-8")

    assert 'icon="group_add"' in source
    assert "st.form_submit_button('Generate Nagao pool'" in source
    assert "icon=':material/group_add:'" in source


def test_icarm_catalog_uses_source_torsion_and_metric_frontier_controls():
    source = inspect.getsource(external_catalog._icarm_tab)

    assert "icarm_torsion_groups(db)" in source
    assert '"Torsion"' in source
    assert '"Only best"' in source
    assert '"Best metric"' in source
    assert "external_torsion_label(row)" in source
    assert 'torsion=None if torsion_choice == "Any"' in source
    assert "only_best=bool(only_best)" in source
    assert "best_metric=_ICARM_BEST_METRIC_OPTIONS[best_metric_label]" in source
    assert 'f"{len(rows):,} curves shown"' in source
    assert "display limit reached" in source
    assert "if len(rows) >= 500 else" in source
    assert "curve model" not in source.lower()


def test_catalogs_page_matches_curves_page_navigation_shell():
    source = inspect.getsource(external_catalog.page)

    assert 'title(\n        "Catalogs",' in source
    assert 'icon="public"' in source
    assert 'tabs(\n        ["ICARM", "LMFDB"],' in source
    assert 'variant="line"' in source
    assert 'width="content"' in source
    assert 'variant="page"' not in source
    assert 'st.html("<div style=\'height:.85rem\'></div>")' in source
    assert "st.divider()" not in source


def test_curves_presentation_uses_dashboard_semantics_and_next_action_order():
    source = (
        ROOT / "rank42" / "ui_pages" / "curves.py"
    ).read_text(encoding="utf-8")

    assert "def _curve_chart_palette():" in source
    assert '"evidence_domain": ["Exact", "Lower bound only"]' in source
    assert '"evidence_range": [primary, muted_2]' in source
    assert '"heatmap_range": [card_2, surface_active, primary_deep]' in source
    assert '"portrait_domain": ["Rigorous witness", "Exact point"]' in source
    assert '"portrait_range": [primary, muted_2]' in source
    assert '"color": chart_colors["portrait_curve"]' in source
    assert '"range": chart_colors["evidence_range"]' in source
    assert '"range": chart_colors["heatmap_range"]' in source

    detail_start = source.index("def _render_curve_detail")
    detail_end = source.index("def _render_browse", detail_start)
    detail = source[detail_start:detail_end]
    assert "_render_next_action(db, row)" in detail
    assert detail.index("_render_next_action(db, row)") < detail.index(
        "_render_curve_actions(db, ctx, row)"
    )


def test_curves_hall_of_fame_uses_research_hero_groups():
    source = (
        ROOT / "rank42" / "ui_pages" / "curves.py"
    ).read_text(encoding="utf-8")

    assert "hall_of_fame_summary(all_rows)" in source
    assert 'st.markdown("**Rank**")' in source
    assert '"Best Rig"' in source
    assert '"Best Exact"' in source
    assert 'st.markdown("**Ranked Curves**")' in source
    assert 'left.metric("Rig"' in source
    assert 'right.metric("Exact"' in source
    assert 'st.markdown("**Torsion Groups**")' in source
    assert 'left.metric("Stored"' in source
    assert '"Most common"' in source
    assert "locally stored torsion only" in source

    # Old generic three-stat hero is gone; grouped Hall-of-Fame tables remain.
    assert 'a.metric("Torsion groups"' not in source
    assert 'b.metric("Ranked curves"' not in source
    assert 'c.metric(' not in source[source.index("def _render_hall_of_fame"):source.index("def page(", source.index("def _render_hall_of_fame"))]
    assert "hall_of_fame_groups(rows, top_per_group=5)" in source
    assert 'semantic=f"curves-hof-{index}"' in source


def test_curves_import_uses_page_nav_context_without_redundant_heading():
    source = (
        ROOT / "rank42" / "ui_pages" / "curves.py"
    ).read_text(encoding="utf-8")
    start = source.index("def _render_import_curve")
    end = source.index("def _render_curve_portrait", start)
    import_source = source[start:end]

    assert 'section_title(\n        "Import curve",' not in import_source
    assert "Bring an elliptic curve over Q into the permanent Curves collection." not in import_source
    assert 'st.markdown("#### JSON import")' in import_source
    assert 'st.markdown("#### Manual curve")' in import_source
    assert 'with st.form("curves-import-form")' in import_source
    assert '"Import JSON"' in import_source


def test_curves_arithmetic_view_exposes_bounded_background_backfill():
    source = (
        ROOT / "rank42" / "ui_pages" / "curves.py"
    ).read_text(encoding="utf-8")
    backfill_source = (
        ROOT / "rank42" / "curve_arithmetic_backfill.py"
    ).read_text(encoding="utf-8")

    assert "def _render_arithmetic_backfill(db, ctx):" in source
    assert 'section_title(\n            "Arithmetic backfill",' in source
    assert '"Backfill missing arithmetic"' in source
    assert '"rank42.curve_arithmetic_backfill"' in source
    assert 'kind="curve_arithmetic_backfill"' in source
    assert '"--retry-unresolved"' in source
    assert "Existing local values are preserved." in source
    assert "_render_arithmetic_backfill(db, ctx)" in source
    assert "value=25" in source
    assert 'ap.add_argument("--limit", type=int, default=25)' in backfill_source
    assert "limit: int | None = 25" in backfill_source


def test_ui_launcher_suppresses_only_known_mpmath_private_module_warnings():
    source = (ROOT / "scripts" / "run-ui.sh").read_text(encoding="utf-8")

    assert "ignore::DeprecationWarning:mpmath.rational" in source
    assert "ignore::DeprecationWarning:mpmath.math2" in source
    assert "ignore::DeprecationWarning" not in source.replace(
        "ignore::DeprecationWarning:mpmath.rational", ""
    ).replace(
        "ignore::DeprecationWarning:mpmath.math2", ""
    )


def test_system_telemetry_keeps_three_second_fragment_and_bar_contract():
    source = (ROOT / "rank42" / "ui.py").read_text(encoding="utf-8")

    assert "_SYSTEM_METRICS_REFRESH_SECONDS = 3.0" in source
    assert '@st.fragment(run_every="3s")' in source
    assert "rows = _cached_system_metrics()" in source
    assert 'if row.get("show_bar"):' in source
    assert 'class="rh-system-track"' in source


def test_data_reference_surfaces_use_researcher_labels_with_stable_routes():
    source = (ROOT / "rank42" / "ui.py").read_text(encoding="utf-8")
    corpus_source = (
        ROOT / "rank42" / "ui_pages" / "corpus_page.py"
    ).read_text(encoding="utf-8")
    catalog_source = (
        ROOT / "rank42" / "ui_pages" / "external_catalog.py"
    ).read_text(encoding="utf-8")

    assert '("Libraries", "Corpora")' in source
    assert '("Catalogs", "External Catalog")' in source
    assert '"Corpora": corpus_page.page' in source
    assert '"External Catalog": external_catalog.page' in source
    assert '"Libraries"' in corpus_source
    assert '"Plugin Libraries"' in corpus_source
    assert '"Build Library"' in corpus_source
    assert '"Catalogs"' in catalog_source


def test_build_library_ui_preserves_external_vault_and_explicit_handoffs():
    page = inspect.getsource(corpus_page.page)
    build = inspect.getsource(corpus_page._render_build_library)

    assert 'title(\n        "Libraries",' in page
    assert 'icon="dataset"' in page
    assert '["Plugin Libraries", "Browse Library", "Build Library"]' in page
    assert 'variant="line"' in page
    assert 'width="content"' in page
    assert 'variant="page"' not in page
    assert "create_research_library(" in build
    assert "add_research_artifact(" in build
    assert "list_research_libraries(" in build
    assert "preview_research_artifact(" in build
    assert '"Preview selected artifact"' in build
    assert "Bounded read-only inspection" in build
    assert "Nothing is" in build
    assert "promoted automatically" in build
    assert "Explicit handoffs are limited to unsearched Candidate" in build
    assert "Pools, exact point validation, and exact external Curve/model import." in build
    assert "rank claims never become proof evidence directly." in build
    assert "No rank claim, point" in build
    assert "curve, or certificate is promoted by this page." in build
    assert "_render_library_delete(ctx, manifest)" in build
    assert "upsert_curve(" not in build
    assert "insert_point(" not in build


def test_library_wide_browser_searches_current_external_projections_only():
    page = inspect.getsource(corpus_page.page)
    browser = inspect.getsource(corpus_page._render_library_browser)

    assert '"Browse Library"' in page
    assert '"libraries-section-pending"' in page
    assert "research_projection_inventory(" in browser
    assert 'row["projection_state"] == "current"' in browser
    assert 'row["projection_state"] == "stale"' in browser
    assert "Library-wide search includes current projections only." in browser
    assert '"Artifact inventory"' in browser
    assert '"Search Library"' in browser
    assert "artifact_ids=eligible_ids" in browser
    assert "roles=roles or None" in browser
    assert "Rank values shown here remain claims" in browser
    assert '"Selected projected record"' in browser
    assert '"Original source row"' in browser
    assert '"Open source artifact in Build Library"' in browser
    assert "_queue_library_artifact_for_build(" in browser
    assert "create_candidate_pool_from_research_projection(" not in browser
    assert "upsert_point(" not in browser
    assert "upsert_curve(" not in browser
    assert "record_point_discovery(" not in browser


def test_build_library_mapping_drafts_remain_descriptive_only():
    build = inspect.getsource(corpus_page._render_build_library)
    mapping = inspect.getsource(corpus_page._render_mapping_draft)

    assert "_render_mapping_draft(" in build
    assert '"Map fields (draft only)"' in mapping
    assert '"Save mapping draft"' in mapping
    assert "save_research_library_mapping_draft(" in mapping
    assert "research_library_mapping_draft(" in mapping
    assert "mapped_preview_rows(" in mapping
    assert "Mapped rank fields remain external claims only." in mapping
    assert "rigorous rank evidence exists" in mapping
    assert "upsert_curve(" not in mapping
    assert "insert_point(" not in mapping
    assert "create_pool(" not in mapping


def test_build_library_external_projection_stays_outside_core_state():
    mapping = inspect.getsource(corpus_page._render_mapping_draft)

    assert '"External projection"' in mapping
    assert '"Build / Rebuild external projection"' in mapping
    assert "build_research_projection(" in mapping
    assert "research_projection_status(" in mapping
    assert "count_research_projection_records(" in mapping
    assert "search_research_projection(" in mapping
    assert "never writes rank42.db" in mapping
    assert "Rank fields are claims" in mapping
    assert "upsert_curve(" not in mapping
    assert "insert_point(" not in mapping
    assert "create_pool(" not in mapping
    assert "replace_pool_rows(" not in mapping


def test_research_library_delete_ui_requires_exact_name_confirmation():
    delete_ui = inspect.getsource(corpus_page._render_library_delete)

    assert '"Danger zone · Delete Library"' in delete_ui
    assert '"Delete Library permanently"' in delete_ui
    assert "disabled=confirmation != library_name" in delete_ui
    assert "delete_research_library(" in delete_ui
    assert "immutable " in delete_ui
    assert "originals, mapping drafts, and external projection" in delete_ui
    assert "does not delete" in delete_ui
    assert "Candidate Pools or any other Rank Hunter core state" in delete_ui


def test_research_library_curve_model_handoff_reuses_exact_external_importer():
    mapping = inspect.getsource(corpus_page._render_mapping_draft)
    curve_ui = inspect.getsource(corpus_page._render_projection_curve_import)

    assert "_render_projection_curve_import(" in mapping
    assert '"Import mapped curve models"' in curve_ui
    assert "existing " in curve_ui
    assert "exact external-curve importer" in curve_ui
    assert '{"a1", "a2", "a3", "a4", "a6"}' in curve_ui
    assert '"rank42.research_library_curve_import"' in curve_ui
    assert 'kind="library_curve_import"' in curve_ui
    assert '"validation": "existing_exact_external_curve_importer"' in curve_ui
    assert '"rank_claim_policy": "provenance_only"' in curve_ui
    assert '"point_policy": "ignored_use_library_point_validation"' in curve_ui
    assert "Singular models or key/model conflicts" in curve_ui
    assert "Mapped x/y coordinates are ignored" in curve_ui
    assert "Mapped rank fields are copied only to import provenance." in curve_ui
    assert "import_curve(" not in curve_ui
    assert "import_curve_records(" not in curve_ui
    assert "upsert_curve(" not in curve_ui
    assert "upsert_point(" not in curve_ui


def test_research_library_point_handoff_uses_exact_worker_and_existing_curve():
    mapping = inspect.getsource(corpus_page._render_mapping_draft)
    point_ui = inspect.getsource(corpus_page._render_projection_point_validation)

    assert "_render_projection_point_validation(" in mapping
    assert '"Validate mapped points"' in point_ui
    assert "Exact Sage validation" in point_ui
    assert "existing stored curve" in point_ui
    assert '"Existing curve for exact validation"' in point_ui
    assert '"rank42.research_library_point_import"' in point_ui
    assert 'kind="library_point_import"' in point_ui
    assert '"validation": "exact_on_stored_curve_over_QQ"' in point_ui
    assert "Valid points enter as exact candidates" in point_ui
    assert "invalid coordinates are recorded only as rejections" in point_ui
    assert "upsert_point(" not in point_ui
    assert "record_point_discovery(" not in point_ui
    assert "upsert_curve(" not in point_ui


def test_research_library_candidate_pool_handoff_is_explicit_and_claims_only():
    promotion = inspect.getsource(corpus_page._render_projection_candidate_promotion)

    assert '"Send to Candidate Pool"' in promotion
    assert "first explicit core-state handoff" in promotion
    assert "unsearched" in promotion
    assert "provenance claims" in promotion
    assert '"Create Candidate Pool from ' in promotion
    assert "create_candidate_pool_from_research_projection(" in promotion
    assert '"Open created pool in Pipelines"' in promotion
    assert 'generation.get("variant_id")' in promotion
    assert "upsert_curve(" not in promotion
    assert "insert_point(" not in promotion


def test_candidate_and_pipeline_ui_use_library_terminology():
    candidate_source = (
        ROOT / "rank42" / "ui_pages" / "candidate_generate_page.py"
    ).read_text(encoding="utf-8")
    pipeline_source = (
        ROOT / "rank42" / "ui_pages" / "build_your_own.py"
    ).read_text(encoding="utf-8")
    catalog_source = (
        ROOT / "rank42" / "pipeline_catalog.py"
    ).read_text(encoding="utf-8")

    assert "'Library use'" in candidate_source
    assert "Libraries:" in candidate_source
    assert '"Library policy"' in pipeline_source
    assert '"Existing pool / library"' in pipeline_source
    assert '"Library Filter"' in catalog_source


def test_candidate_navigation_uses_one_canonical_route_with_legacy_handoffs():
    from rank42 import ui

    state = {"rh_page": "Candidates"}
    assert ui._normalize_route("Candidates", state) == "Candidates"
    assert state["rh_page"] == "Candidates"
    assert state["candidates_tab"] == "Generate"

    pool_state = {"rh_page": "Candidates", "candidates_tab": "Pools"}
    assert ui._normalize_route("Candidates", pool_state) == "Candidates"
    assert pool_state["rh_page"] == "Candidates"
    assert pool_state["candidates_tab"] == "Pools"

    transfer_state = {
        "rh_page": "Candidates",
        "candidates_tab": "Import / Export",
    }
    assert ui._normalize_route("Candidates", transfer_state) == "Candidates"
    assert transfer_state["candidates_tab"] == "Pools"

    generate_state = {"rh_page": "Candidate Generate"}
    assert ui._normalize_route("Candidate Generate", generate_state) == "Candidates"
    assert generate_state["candidates_tab"] == "Generate"

    pools_state = {"rh_page": "Candidate Pools"}
    assert ui._normalize_route("Candidate Pools", pools_state) == "Candidates"
    assert pools_state["candidates_tab"] == "Pools"

    source = (ROOT / "rank42" / "ui.py").read_text(encoding="utf-8")
    routes = source[source.index("routes = {"):]
    assert '"Candidates": candidates_page.page' in routes
    assert '"Candidate Generate": candidate_generate_page.page' not in routes
    assert '"Candidate Pools": candidate_pools_page.page' not in routes

def test_quartics_covering_matrix_keeps_numeric_fields_and_localized_display():
    source = inspect.getsource(quartics_page._render_covering_selector_fragment)
    page_source = inspect.getsource(quartics_page.page)
    file_source = (ROOT / "rank42" / "ui_pages" / "quartics_page.py").read_text(
        encoding="utf-8"
    )
    assert "localized_integer_columns=" in source
    assert '"Search max H"' in source
    assert "@st.fragment" in file_source
    assert "def _render_covering_selector_fragment" in file_source
    assert "connect_existing(ctx.db_path)" in source
    assert "_render_covering_detail(" in source
    assert "live_db," in source
    assert "finally:" in source
    assert "live_db.close()" in source
    assert "def _render_covering_selector_fragment(db," not in file_source
    assert "_render_covering_selector_fragment(\n                db," not in page_source
