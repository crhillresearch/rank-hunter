from pathlib import Path
import re


DASHBOARD_SOURCE = Path("rank42/ui_pages/dashboard.py").read_text(encoding="utf-8")
DASHBOARD_PLUGINS_SOURCE = Path("rank42/ui_pages/dashboard_plugins.py").read_text(encoding="utf-8")
DASHBOARD_CHARTS_SOURCE = Path("rank42/ui_pages/dashboard_charts.py").read_text(encoding="utf-8")
DASHBOARD_SECTIONS_SOURCE = Path("rank42/ui_pages/dashboard_sections.py").read_text(encoding="utf-8")


def test_dashboard_explicit_widget_keys_are_unique():
    keys = re.findall(r'key="(rh-dashboard-[^"]+)"', DASHBOARD_SOURCE)
    assert len(keys) == len(set(keys))


def test_dashboard_does_not_embed_search_renderers():
    assert "from . import family_search, general_hunt" not in DASHBOARD_SOURCE
    assert "family_search.render(" not in DASHBOARD_SOURCE
    assert "general_hunt.render(" not in DASHBOARD_SOURCE
    assert "_search_panel(" not in DASHBOARD_SOURCE


def test_dashboard_quick_actions_only_route_to_owner_pages():
    assert 'def _quick_actions_panel():' in DASHBOARD_SOURCE
    for route in ("Candidate Generate", "Search", "Target", "Auto"):
        assert f'args=("{route}",)' in DASHBOARD_SOURCE
    assert "Dashboard does not launch research work itself." not in DASHBOARD_SOURCE
    assert "Open the researcher Case Board or a specialist proof/geometry workspace." not in DASHBOARD_SOURCE



def test_dashboard_is_one_continuous_page_without_view_tabs():
    assert 'dashboard-main-tabs' not in DASHBOARD_SOURCE
    assert 'dashboard_view' not in DASHBOARD_SOURCE
    assert '["Overview", "Research", "Operations"]' not in DASHBOARD_SOURCE
    assert "def _render_overview" not in DASHBOARD_SOURCE
    assert "def _render_research" not in DASHBOARD_SOURCE
    assert "def _render_operations" not in DASHBOARD_SOURCE
    assert "def _render_dashboard(db, ctx):" in DASHBOARD_SOURCE


def test_dashboard_restores_all_existing_widgets_on_one_page():
    # Unique union of the former Overview / Research / Operations widgets.
    required = [
        "_summary_metrics(research)",
        "_local_frontier_panel(",
        "_attention_panel(ctx.db_path, attention)",
        "_quick_actions_panel()",
        '_evidence_summary_row(db, research["certification"])',
        "_operations_state_panel(operations)",
        '_latest_activity(operations["recent_jobs"])',
        "render_research_charts(db)",
        "_icarm_leaderboard(db)",
        'render_plugins_panel(db, ctx, health=operations["plugins"])',
        "_research_frontier_actions()",
    ]
    for call in required:
        assert call in DASHBOARD_SOURCE


def test_dashboard_leaderboard_uses_self_closing_torsion_menu_in_heading():
    source = DASHBOARD_SOURCE[
        DASHBOARD_SOURCE.index("def _icarm_leaderboard"):
        DASHBOARD_SOURCE.index("def _go_to_jobs_section")
    ]
    assert "ICARM LEADERBOARD" not in source
    assert "FILTER BY TORSION" not in source
    assert "source-provided ICARM torsion</small>" not in source
    assert source.count(">LEADERBOARD<") == 1
    assert 'labels = {"all": "All"}' in source
    assert '"Trivial"' in source
    assert "All torsion groups" not in source
    assert 'filter_key = "rh-dashboard-icarm-torsion-menu"' in source
    assert 'vertical_alignment="top"' in source
    assert "chosen = st.menu_button(" in source
    assert "with st.popover(" not in source
    assert "st.radio(" not in source
    assert "box-shadow:none!important" not in source
    assert "var(--rh-text-secondary)" not in source
    assert "st.selectbox(" not in source
    assert 'f"{labels[selected]} ▾"' not in source
    assert "labels[selected]," in source
    assert 'type="tertiary"' in source
    assert 'width="content"' in source
    assert 'st.session_state.get("rh-dashboard-icarm-torsion")' in source
    assert 'st.session_state["rh-dashboard-icarm-torsion"] = selected' in source
    assert 'triggered = st.session_state.get(filter_key)' in source
    assert 'if triggered is not None and str(triggered) in options:' in source
    chosen_block = source[
        source.index("if chosen is not None:"):
        source.index('torsion = None if selected == "all"')
    ]
    assert "st.rerun()" not in chosen_block
    assert source.index("triggered = st.session_state.get(filter_key)") < source.index("chosen = st.menu_button(")
    assert source.index(">LEADERBOARD<") < source.index("chosen = st.menu_button(")
    assert source.index("chosen = st.menu_button(") < source.index("torsion = None if selected")
    assert source.index("torsion = None if selected") < source.index("rh-dashboard-icarm-rank")



def test_dashboard_route_buttons_reuse_primary_navigation_icons():
    assert "from .common import PAGE_ICONS, title" in DASHBOARD_SOURCE
    assert 'def _route_icon(page, fallback=":material/arrow_forward:"):' in DASHBOARD_SOURCE
    for route in (
        "Results",
        "Candidate Generate",
        "Target",
        "Search",
        "Auto",
        "Curves",
        "Points",
        "Descent",
        "Lattices & Heights",
        "Pipelines",
        "Candidate Pools",
    ):
        assert f'_route_icon("{route}")' in DASHBOARD_SOURCE
    assert 'icon=_route_icon(item["page"])' in DASHBOARD_SOURCE
    assert 'icon=PAGE_ICONS["Plugins"]' in DASHBOARD_PLUGINS_SOURCE


def test_dashboard_plugin_health_copy_is_capitalized():
    assert 'health_text = f"{attention} Need Attention" if attention else "Healthy"' in DASHBOARD_PLUGINS_SOURCE
    assert '"all healthy"' not in DASHBOARD_PLUGINS_SOURCE
    assert '"need attention"' not in DASHBOARD_PLUGINS_SOURCE


def test_dashboard_major_sections_emit_theme_owned_title_hooks_without_core_css():
    assert "def _dashboard_section_title_style():" not in DASHBOARD_SOURCE
    assert ".rh-dashboard-section-heading {" not in DASHBOARD_SOURCE
    assert ".rh-dashboard-section-title {" not in DASHBOARD_SOURCE
    assert "#006DFC" not in DASHBOARD_SOURCE
    assert "var(--rh-text-secondary,#5c6370)" not in DASHBOARD_SOURCE
    assert "var(--rh-text,#31333f)" not in DASHBOARD_SOURCE

    for title in (
        "MOST RECENT ACTIVITY",
        "QUICK ACTIONS",
        "LEADERBOARD",
        "OPERATIONS",
        "ATTENTION",
    ):
        assert f'<span class="rh-dashboard-section-title">{title}</span>' in DASHBOARD_SOURCE

    # Research Frontier keeps its existing native treatment as the visual reference.
    assert '<span>RESEARCH FRONTIER</span><span>LIVE DATABASE</span>' in DASHBOARD_SOURCE

    # Status/health badges remain their own small badge elements, not section titles.
    assert 'class="rh-dashboard-plugin-health {"ok" if dispatcher_healthy else "warning"}"' in DASHBOARD_SOURCE
    assert 'class="rh-dashboard-plugin-health {"warning" if items else "ok"}"' in DASHBOARD_SOURCE
    assert '<div class="rh-dashboard-section-heading rh-dashboard-plugin-heading">' in DASHBOARD_PLUGINS_SOURCE
    assert '<span class="rh-dashboard-section-title">PLUGINS</span>' in DASHBOARD_PLUGINS_SOURCE
    assert 'class="rh-dashboard-plugin-health {health_class}"' in DASHBOARD_PLUGINS_SOURCE
    assert '<span class="rh-dashboard-section-title">RESEARCH LANDSCAPE</span>' in DASHBOARD_CHARTS_SOURCE
    assert 'class="rh-dashboard-section-title">{html.escape(title)}</span>' in DASHBOARD_SECTIONS_SOURCE
    assert '"%d Open" % len(items) if items else "Clear"' in DASHBOARD_SOURCE

def test_dashboard_parameter_scope_captions_follow_parameter_charts():
    source = Path("rank42/ui_pages/dashboard_charts.py").read_text(encoding="utf-8")

    yield_start = source.index("Parameter-Space Yield Heatmap")
    neighborhood_start = source.index("High-Rank Neighborhood Map")
    exceptional_start = source.index("Exceptional Curve Radar")

    yield_block = source[yield_start:neighborhood_start]
    neighborhood_block = source[neighborhood_start:exceptional_start]

    assert yield_block.index("st.altair_chart(") < yield_block.index(
        "st.caption(scope_caption)"
    )
    assert neighborhood_block.index("st.altair_chart(") < neighborhood_block.index(
        "st.caption(scope_caption)"
    )


def test_research_frontier_actions_are_buttons_without_extra_heading():
    source = DASHBOARD_SOURCE[
        DASHBOARD_SOURCE.index("def _research_frontier_actions"):
        DASHBOARD_SOURCE.index("def _local_frontier_panel")
    ]
    assert "EXPLORE RESEARCH" not in source
    assert "rh-dashboard-explore-heading" not in source
    for label in ("Curves", "Points", "Descent", "Lattices"):
        assert f'"{label}"' in source


def test_dashboard_research_links_and_icarm_filter_keep_owner_boundaries():
    assert "def _research_owner_links" not in DASHBOARD_SOURCE
    assert "def _research_frontier_actions" in DASHBOARD_SOURCE
    assert '"Lattices"' in DASHBOARD_SOURCE
    assert 'args=("Lattices & Heights",)' in DASHBOARD_SOURCE
    assert "icarm_torsion_groups(db)" in DASHBOARD_SOURCE
    assert "torsion=torsion" in DASHBOARD_SOURCE
    assert "ensure_icarm_synced" not in DASHBOARD_SOURCE
    assert "sync_icarm(" not in DASHBOARD_SOURCE
    assert "fetch_icarm(" not in DASHBOARD_SOURCE


def test_dashboard_reuses_one_research_and_operations_snapshot():
    assert DASHBOARD_SOURCE.count("dashboard_research_snapshot(db)") == 1
    assert DASHBOARD_SOURCE.count("dashboard_operations_snapshot(") == 1
    assert "dashboard_overview_snapshot" not in DASHBOARD_SOURCE

    # Attention consumes already-loaded rank-conflict and operations state.
    # Campaign analysis is deliberately disabled on Dashboard.
    assert "campaign=None" in DASHBOARD_SOURCE
    assert 'rank_conflicts=research["rank_conflicts"]' in DASHBOARD_SOURCE
    assert "operations=operations" in DASHBOARD_SOURCE


def test_dashboard_has_no_current_campaign_section_or_campaign_projection():
    assert "def _campaign_panel" not in DASHBOARD_SOURCE
    assert "CURRENT CAMPAIGN" not in DASHBOARD_SOURCE
    assert "rh-dashboard-open-campaigns" not in DASHBOARD_SOURCE
    assert "dashboard_overview_snapshot" not in DASHBOARD_SOURCE


def test_dashboard_follows_research_first_reading_order():
    render_start = DASHBOARD_SOURCE.index("def _render_dashboard(db, ctx):")
    render = DASHBOARD_SOURCE[render_start:]

    hero = render.index("_summary_metrics(research)")
    certification = render.index('_evidence_summary_row(db, research["certification"])')
    frontier = render.index("_local_frontier_panel(")
    operations = render.index("_operations_state_panel(operations)")
    activity = render.index('_latest_activity(operations["recent_jobs"])')
    attention = render.index("_attention_panel(ctx.db_path, attention)")
    quick_actions = render.index("_quick_actions_panel()")
    charts = render.index("render_research_charts(db)")
    leaderboard = render.index("_icarm_leaderboard(db)")
    plugins = render.index('render_plugins_panel(db, ctx, health=operations["plugins"])')

    assert hero < certification < frontier < operations < charts
    assert charts < quick_actions < leaderboard < attention < plugins < activity


def test_dashboard_evidence_depth_shows_cumulative_stage_totals_in_compact_legend():
    source = DASHBOARD_SOURCE[
        DASHBOARD_SOURCE.index("def _evidence_depth_html"):
        DASHBOARD_SOURCE.index("def _evidence_summary_row")
    ]
    assert "reached_stage_counts(deepest_counts)" in source
    assert "deepest_count = deepest_counts[depth]" in source
    assert "count = reached_counts[depth]" in source
    assert 'title = f"Reached {label}: {count:,} · {pct:.1f}%"' in source
    assert "cumulative totals · deepest-stage bar" in source
    assert "Stops at Selmer" not in source
