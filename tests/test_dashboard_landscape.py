import inspect
from pathlib import Path

from rank42.candidates import create_pool, replace_pool_rows
from rank42.db import connect
from rank42.ui_pages import dashboard, dashboard_charts, dashboard_sections, landscape_page


UI_SOURCE = Path("rank42/ui.py").read_text(encoding="utf-8")


def test_landscape_is_preserved_but_hidden_from_primary_analysis_navigation():
    assert "landscape_page," in UI_SOURCE
    assert '"Landscape": landscape_page.page' in UI_SOURCE

    groups = UI_SOURCE[
        UI_SOURCE.index("groups = {"):
        UI_SOURCE.index("for route, plugin, page in extension_rows")
    ]
    assert '("Landscape", "Landscape", ":material/landscape:")' not in groups


def test_dashboard_restores_landscape_charts_and_keeps_specialist_links_in_frontier():
    source = inspect.getsource(dashboard._render_dashboard)

    assert "render_landscape_charts(" not in source
    assert "render_research_charts(db)" in source
    assert "render_landscape_comparisons(" not in source
    assert "render_chart_followups(" not in source
    assert "_research_owner_links()" not in source

    links = inspect.getsource(dashboard._research_frontier_actions)
    assert 'args=("Curves",)' in links
    assert 'args=("Points",)' in links
    assert 'args=("Descent",)' in links
    assert 'args=("Lattices & Heights",)' in links
    assert 'args=("Case Board",)' not in links
    assert 'args=("Landscape",)' not in links
    assert 'args=("Analyze",)' not in links

    frontier = inspect.getsource(dashboard._local_frontier_panel)
    assert "_research_frontier_actions()" in frontier
    assert "Open curves" not in frontier


def test_dashboard_chart_selector_defaults_to_global_all_and_restores_torsion():
    selector = inspect.getsource(dashboard_charts._dashboard_parameter_scope)
    renderer = inspect.getsource(dashboard_charts.render_research_charts)

    assert "_parameter_pool_rows(db)" in selector
    assert 'options = ["all", *ids]' in selector
    assert 'st.session_state.get("dashboard_chart_parameter_scope", "all")' in selector
    assert '"All candidate pools' in selector
    assert 'key="dashboard-chart-parameter-pool-select"' in selector
    assert 'label_visibility="collapsed"' in selector
    assert "help=" not in selector
    assert "cohort" not in selector.lower()
    assert "_dashboard_parameter_scope(db)" in renderer
    assert 'parameter_pool_id=parameter_scope["pool_id"]' in renderer
    assert "render_torsion_landscape(db)" in renderer



def test_dashboard_research_landscape_is_one_card_with_borderless_equal_chart_regions():
    renderer = inspect.getsource(dashboard_charts.render_research_charts)
    charts = inspect.getsource(dashboard_charts.render_landscape_charts)

    assert 'with region("dashboard-research-landscape", border=True):' in renderer
    assert renderer.index("RESEARCH LANDSCAPE") < renderer.index("_dashboard_parameter_scope(db)")
    for semantic in (
        "dashboard-chart-rank-distribution",
        "dashboard-chart-parameter-yield",
        "dashboard-chart-search-progress",
        "dashboard-chart-root-rank",
    ):
        assert f'with region("{semantic}", border=False, height="stretch"):' in charts
    assert 'border=True' not in "\n".join(
        line for line in charts.splitlines()
        if "dashboard-chart-" in line and "with region" in line
    )


def test_dashboard_landscape_uses_restrained_axiom_chart_palette():
    palette = inspect.getsource(dashboard_charts._dashboard_chart_palette)
    charts = inspect.getsource(dashboard_charts.render_landscape_charts)

    assert 'palette.get("rh-primary")' in palette
    assert 'palette.get("rh-primary-deep")' in palette
    assert 'palette.get("rh-card-2")' in palette
    assert 'palette.get("rh-surface-active")' in palette
    assert 'st.session_state.get("_rh_appearance")' in palette
    assert 'yield_domain = [0, 0.5, 1]' in palette
    assert 'yield_range = [card, surface_active, primary_deep]' in palette
    assert 'yield_domain = [0, 1]' in palette
    assert 'yield_range = [card_2, primary_deep]' in palette
    assert 'chart_colors = _dashboard_chart_palette()' in charts
    assert 'color=chart_colors["primary"]' in charts
    assert 'domain=chart_colors["yield_domain"]' in charts
    assert 'range=chart_colors["yield_range"]' in charts
    assert 'domain=["Exact rank", "Rigorous lower bound"]' in charts
    assert 'range=[chart_colors["primary"], chart_colors["muted_2"]]' in charts
    assert 'color=chart_colors["primary_deep"]' in charts


def test_landscape_parameter_rows_default_to_all_pools():
    source = inspect.getsource(dashboard_charts._parameter_search_rows)

    assert "if pool_id is not None:" in source
    assert "AND c.pool_id=?" in source
    assert "status!='unsearched'" in source
    assert "ROW_NUMBER() OVER" not in source
    assert "pool_row" not in source


def test_landscape_parameter_rows_all_mode_combines_unrelated_candidate_pools(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        first = create_pool(
            db,
            name="landscape-all-one",
            plugin_id="family-one",
            family_spec="family/variant-one",
        )
        second = create_pool(
            db,
            name="landscape-all-two",
            plugin_id="family-two",
            family_spec="family/variant-two",
        )
        replace_pool_rows(
            db,
            int(first["id"]),
            [{"parameter": "1", "a": 1, "b": 2, "score": 1.0}],
        )
        replace_pool_rows(
            db,
            int(second["id"]),
            [{"parameter": "2", "a": 100, "b": 200, "score": 1.0}],
        )
        db.execute(
            "UPDATE candidates SET status='searched' WHERE pool_id IN (?,?)",
            (int(first["id"]), int(second["id"])),
        )
        db.commit()

        all_rows = dashboard_charts._parameter_search_rows(db)
        first_rows = dashboard_charts._parameter_search_rows(
            db,
            pool_id=int(first["id"]),
        )

        assert {
            (row["Parameter a"], row["Parameter b"])
            for row in all_rows
        } == {(1.0, 2.0), (100.0, 200.0)}
        assert {
            row["Pool"]
            for row in all_rows
        } == {
            f'#{int(first["id"])} · landscape-all-one',
            f'#{int(second["id"])} · landscape-all-two',
        }
        assert [(row["Parameter a"], row["Parameter b"]) for row in first_rows] == [
            (1.0, 2.0)
        ]
    finally:
        db.close()


def test_dashboard_no_longer_exposes_parameter_compatibility_cohort_helpers():
    source = inspect.getsource(dashboard_charts)

    assert "def _parameter_pool_frame" not in source
    assert "def _parameter_pool_cohorts" not in source
    assert "dashboard-chart-parameter-cohort-select" not in source
    assert "All compatible pools" not in source



def test_parameter_scope_only_filters_coordinate_dependent_charts():
    source = inspect.getsource(dashboard_charts.render_landscape_charts)

    parameter_rows = source.index("_parameter_search_rows")
    global_rank = source.index("dashboard_exact_rank_distribution")
    exceptional = source.index("_exceptional_curve_rows")

    assert parameter_rows < global_rank
    assert "parameter_pool_id" in source
    assert "dashboard_exact_rank_distribution(db)" in source
    assert "_exceptional_curve_rows(db)" in source
    assert "pool_id" not in inspect.getsource(dashboard_charts._exceptional_curve_rows)



def test_legacy_landscape_helpers_remain_compatible_but_page_uses_r4_r5_contracts():
    comparison = inspect.getsource(dashboard_sections.render_landscape_comparisons)
    page = inspect.getsource(landscape_page.page)

    assert "_render_lattices(" not in comparison
    assert "_render_unresolved(" not in comparison
    assert "render_record_complexity(db)" in comparison
    assert "render_torsion_landscape(db)" in comparison
    torsion = inspect.getsource(dashboard_sections.render_torsion_landscape)
    assert "_torsion_research_data(db)" in torsion
    assert "_render_torsion_overview(data)" in torsion
    assert "_render_torsion_distribution(data)" in torsion
    assert "_render_torsion_signals(data)" in torsion
    assert "_render_torsion_records(" in torsion
    assert "_render_torsion_rank(" in torsion
    assert torsion.index("_render_torsion_distribution(data)") < torsion.index("_render_torsion_signals(data)")
    assert torsion.index("_render_torsion_signals(data)") < torsion.index("_render_torsion_records(")
    assert torsion.index("_render_torsion_records(") < torsion.index("_render_torsion_rank(")
    assert "_render_exceptional_arithmetic(" in comparison

    assert "render_landscape_charts(" not in page
    assert "render_landscape_comparisons(" not in page
    assert "_explore(db)" in page
    assert "_saved_analyses(db)" in page
