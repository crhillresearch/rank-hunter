import inspect
import json

from rank42 import manage_store
from rank42.analysis_case_store import save_analysis_case
from rank42.curve_size_metrics import ensure_curve_size_metrics_schema
from rank42.db import connect, log_event, upsert_curve, update_curve
from rank42.rank_evidence import record_rank_evidence
from rank42.torsion import ensure_torsion_schema
from rank42.ui_pages import curves as curves_page
from rank42.ui_pages.curves import _curve_inventory_rows, _evidence_state
from rank42.ui_store import ensure_ui_schema


MODEL = ["0", "0", "0", "-1", "0"]


def _db(path):
    db = connect(path)
    ensure_curve_size_metrics_schema(db)
    ensure_torsion_schema(db)
    return db


def _curve(db, parameter, *, lower=None, score=0.0, status="new"):
    cid = upsert_curve(db, family="curves-test", parameter=str(parameter), score=score)
    update_curve(
        db,
        cid,
        a_invariants_json=json.dumps(MODEL),
        descent_lower=lower,
        status=status,
    )
    return cid


def _evidence(db, cid, *, lower=None, upper=None):
    return record_rank_evidence(
        db,
        curve_id=cid,
        model=MODEL,
        data={
            "engine": "curves-test",
            "evidence_type": "rank_bounds",
            "status": "completed",
            "rigorous": True,
            "rigorous_lower": lower,
            "rigorous_upper": upper,
            "exact_rank": None,
            "conditional_analytic_upper": None,
            "numerical_rank_signal": None,
            "assumptions": [],
            "points_found": [],
            "options": {},
        },
    )


def test_curves_inventory_orders_by_authoritative_evidence_state(tmp_path):
    db = _db(tmp_path / "rank42.db")
    try:
        legacy = _curve(db, "legacy", lower=4, score=100.0)
        evidence_only = _curve(db, "evidence", score=1.0)
        _evidence(db, evidence_only, lower=6)

        rows = _curve_inventory_rows(db)

        assert [int(row["id"]) for row in rows[:2]] == [evidence_only, legacy]
        assert rows[0]["rigorous_lower"] == 6
        assert rows[0]["exact_rank"] is None
    finally:
        db.close()


def test_curves_inventory_rank_range_uses_evidence_only_lower(tmp_path):
    db = _db(tmp_path / "rank42.db")
    try:
        low = _curve(db, "low")
        middle = _curve(db, "middle")
        high = _curve(db, "high")
        _evidence(db, low, lower=4)
        _evidence(db, middle, lower=5)
        _evidence(db, high, lower=6)

        rows = _curve_inventory_rows(db, minrank=5, maxrank=5)

        assert [int(row["id"]) for row in rows] == [middle]
        assert rows[0]["rigorous_lower"] == 5
    finally:
        db.close()


def test_curves_inventory_does_not_project_dormant_research_cases(tmp_path):
    db = _db(tmp_path / "rank42.db")
    try:
        ensure_ui_schema(db)
        opened = _curve(db, "open-case", lower=5)
        deferred = _curve(db, "deferred-case", lower=5)
        untracked = _curve(db, "untracked-case", lower=5)
        save_analysis_case(
            db,
            curve_id=opened,
            priority=200,
            workflow_status="open",
            research_goal="Find another independent point",
        )
        save_analysis_case(
            db,
            curve_id=deferred,
            priority=50,
            workflow_status="deferred",
        )

        rows = _curve_inventory_rows(db)

        assert {int(row["id"]) for row in rows} == {opened, deferred, untracked}
        assert all("workflow_status" not in row for row in rows)
        assert all("workflow_priority" not in row for row in rows)
        assert all("workflow_goal" not in row for row in rows)
    finally:
        db.close()


def test_curves_rank_slider_ceiling_uses_authoritative_rigorous_lower(tmp_path):
    db = _db(tmp_path / "rank42.db")
    try:
        _curve(db, "legacy", lower=4)
        evidence_only = _curve(db, "evidence")
        _evidence(db, evidence_only, lower=7)

        assert curves_page._max_rigorous_lower(db) == 7
    finally:
        db.close()


def test_curves_inventory_exact_filter_uses_reduced_exact_rank(tmp_path):
    db = _db(tmp_path / "rank42.db")
    try:
        exact = _curve(db, "exact")
        interval = _curve(db, "interval")
        _evidence(db, exact, lower=4, upper=4)
        _evidence(db, interval, lower=4, upper=6)

        rows = _curve_inventory_rows(db, evidence="Exact")

        assert [int(row["id"]) for row in rows] == [exact]
        assert rows[0]["exact_rank"] == 4
        assert rows[0]["rigorous_upper"] == 4
    finally:
        db.close()


def test_curves_inventory_interval_filter_uses_evidence_upper(tmp_path):
    db = _db(tmp_path / "rank42.db")
    try:
        interval = _curve(db, "interval")
        lower_only = _curve(db, "lower")
        _evidence(db, interval, lower=3, upper=5)
        _evidence(db, lower_only, lower=4)

        rows = _curve_inventory_rows(db, evidence="Rigorous interval")

        assert [int(row["id"]) for row in rows] == [interval]
        assert rows[0]["rigorous_lower"] == 3
        assert rows[0]["rigorous_upper"] == 5
        assert rows[0]["exact_rank"] is None
    finally:
        db.close()


def test_curves_inventory_preserves_selected_or_manual_unresolved_rows(tmp_path):
    db = _db(tmp_path / "rank42.db")
    try:
        selected = _curve(db, "selected")
        imported = _curve(db, "imported")
        hidden = _curve(db, "hidden")
        log_event(db, imported, "info", "Manual external curve import from test")

        rows = _curve_inventory_rows(db, wanted=selected)

        ids = {int(row["id"]) for row in rows}
        assert selected in ids
        assert imported in ids
        assert hidden not in ids
    finally:
        db.close()

def test_curves_inventory_campaign_filter_uses_authoritative_campaign_traceability(tmp_path):
    db = _db(tmp_path / "rank42.db")
    try:
        manage_store.ensure_manage_schema(db)
        in_campaign = _curve(db, "campaign", lower=5, score=1.0)
        outside = _curve(db, "outside", lower=7, score=2.0)
        campaign_id = manage_store.create_campaign(
            db,
            name="Curves filter campaign",
        )
        manage_store.pin_campaign_curve(db, campaign_id, in_campaign)

        rows = _curve_inventory_rows(db, campaign_id=campaign_id)

        assert [int(row["id"]) for row in rows] == [in_campaign]
        assert outside not in {int(row["id"]) for row in rows}
    finally:
        db.close()


def test_curves_page_places_search_and_rank_slider_on_top_row():
    source = inspect.getsource(curves_page._render_browse)

    assert "campaigns = list(list_campaigns(db))" in source
    assert 'rank_ceiling = _max_rigorous_lower(db)' in source
    assert 'search_col, rank_slider_col = st.columns(' in source
    assert '[4.2, 1.55]' in source
    assert 'vertical_alignment="center"' in source
    assert 'q = search_col.text_input(' in source
    assert 'label_visibility="collapsed"' in source
    assert 'icon=":material/search:"' not in source
    assert 'rank_label_col.markdown("Rank")' not in source
    assert 'for value in rank_slider_col.slider(' in source
    assert '"Rigorous rank"' in source
    assert 'key="curves-rank-range"' in source
    assert 'max_value=rank_ceiling' in source
    assert 'help="Rigorous lower-bound rank range"' in source
    assert 'c1, c2, c3, c4, c5 = st.columns(' in source
    assert '[2.2, 1.2, 1.2, 1.05, 1.05]' in source
    assert 'fam = c1.selectbox("Family"' in source
    assert 'campaign_choice = c2.selectbox(' in source
    assert '"Campaign"' in source
    assert 'key="curves-campaign"' in source
    assert 'evidence = c3.selectbox(' in source
    assert 'torsion = c4.selectbox(' in source
    assert '"Torsion"' in source
    assert '"Not stored"' in source
    assert 'key="curves-torsion"' in source
    assert 'status = c5.selectbox(' in source
    assert '"Research case"' not in source
    assert 'key="curves-workflow"' not in source
    assert "campaign_id=(" in source
    assert "minrank=minrank" in source
    assert "maxrank=maxrank" in source
    assert "torsion=torsion" in source
    assert "int(campaign_choice)" in source


def test_curves_arithmetic_and_catalog_column_order():
    source = inspect.getsource(curves_page._inventory_table)

    arithmetic = source[source.index('if view == "Arithmetic":'):source.index('elif view == "Catalog":')]
    arithmetic_expected = [
        '"id": r["id"]',
        '"torsion": _torsion_display(r)',
        '"family / t": family_t',
        '"log N": r["arithmetic_log_conductor"]',
        '"N source": _arithmetic_table_source_label(r, "conductor")',
        '"naive height": _real_value(r["arithmetic_naive_height"])',
        '"naive source": _arithmetic_table_source_label(r, "naive_height")',
        '"Faltings height": _real_value(r["arithmetic_faltings_height"])',
        '"Faltings source": _arithmetic_table_source_label(r, "faltings_height")',
        '"log |Δ|": _log_abs_integer(r["arithmetic_discriminant"])',
        '"Δ source": _arithmetic_table_source_label(r, "discriminant")',
        '"evidence": _evidence_state(r)',
    ]
    arithmetic_positions = [arithmetic.index(item) for item in arithmetic_expected]
    assert arithmetic_positions == sorted(arithmetic_positions)

    catalog = source[source.index('elif view == "Catalog":'):source.index('else:', source.index('elif view == "Catalog":'))]
    catalog_expected = [
        '"id": r["id"]',
        '"family / t": family_t',
        '"ICARM": _catalog_summary(r, "icarm")',
        '"LMFDB": _catalog_summary(r, "lmfdb")',
        '"checked":',
        '"status": r["status"]',
        '"evidence": _evidence_state(r)',
    ]
    catalog_positions = [catalog.index(item) for item in catalog_expected]
    assert catalog_positions == sorted(catalog_positions)


def test_curves_inventory_uses_view_specific_grid_instances():
    source = inspect.getsource(curves_page._render_browse)

    assert 'semantic=f"curves-browse-table-{inventory_view.lower()}"' in source
    assert 'semantic="curves-browse-table"' not in source


def test_curves_research_table_default_column_order_matches_inventory_priority():
    source = inspect.getsource(curves_page._inventory_table)
    research = source[source.index("else:"):]

    expected = [
        '"id": r["id"]',
        '"family / t": family_t',
        '"rank ≥": r["rigorous_lower"]',
        '"exact": r["exact_rank"]',
        '"points": int(r["point_count"] or 0)',
        '"rigorous pts": int(r["rigorous_point_count"] or 0)',
        '"Nagao":',
        '"rigorous upper": _rigorous_upper(r)',
        '"evidence": _evidence_state(r)',
        '"status": r["status"]',
    ]
    positions = [research.index(item) for item in expected]
    assert positions == sorted(positions)


def test_curves_research_landscape_projects_filtered_rows_only():
    rows = [
        {
            "id": 1,
            "family": "family-a",
            "parameter": "a1",
            "rigorous_lower": 7,
            "exact_rank": None,
            "score": 13.25,
        },
        {
            "id": 2,
            "family": "family-a",
            "parameter": "a2",
            "rigorous_lower": 7,
            "exact_rank": 7,
            "score": 12.5,
        },
        {
            "id": 3,
            "family": "family-b",
            "parameter": "b1",
            "rigorous_lower": 6,
            "exact_rank": None,
            "score": None,
        },
        {
            "id": 4,
            "family": "family-c",
            "parameter": "c1",
            "rigorous_lower": 5,
            "exact_rank": 5,
            "score": 9.0,
        },
    ]

    landscape = curves_page._research_landscape_data(rows, family_limit=2)

    assert landscape["rank_distribution"] == [
        {"rigorous_lower": 5, "evidence": "Exact", "count": 1},
        {"rigorous_lower": 6, "evidence": "Lower bound only", "count": 1},
        {"rigorous_lower": 7, "evidence": "Lower bound only", "count": 1},
        {"rigorous_lower": 7, "evidence": "Exact", "count": 1},
    ]
    assert [row["curve_id"] for row in landscape["scatter"]] == [1, 2, 4]
    assert landscape["scatter"][0]["evidence_class"] == "Lower bound only"
    assert landscape["scatter"][0]["evidence"] == "Proven ≥ 7"
    assert landscape["scatter"][1]["evidence_class"] == "Exact"
    assert landscape["scatter"][1]["evidence"] == "Exact"
    assert landscape["family_order"] == ["family-a", "family-b"]
    assert landscape["family_heatmap"] == [
        {
            "family": "family-a",
            "rigorous_lower": 7,
            "count": 2,
            "exact_count": 1,
        },
        {
            "family": "family-b",
            "rigorous_lower": 6,
            "count": 1,
            "exact_count": 0,
        },
    ]


def test_curves_research_landscape_is_row_only_and_short_circuits_on_selection():
    renderer = inspect.getsource(curves_page._render_research_landscape)
    browse = inspect.getsource(curves_page._render_browse)

    assert "db.execute" not in renderer
    assert "curve_rank_summary_map" not in renderer
    assert '_themed_vega_lite_chart(' in renderer
    wrapper = inspect.getsource(curves_page._themed_vega_lite_chart)
    assert 'st.vega_lite_chart(' in wrapper
    assert 'theme=None' in wrapper
    assert "Research landscape" not in renderer
    assert "Charts summarize the currently filtered Inventory rows only." not in renderer
    assert "Rigorous rank distribution" in renderer
    assert "Nagao vs rigorous rank" in renderer
    assert "Family × rigorous-rank landscape" in renderer
    assert 'Nagao is a heuristic signal; the vertical axis is rigorous evidence.' in renderer
    assert '"field": "evidence_class"' in renderer
    assert '"field": "evidence"' in renderer
    assert '"labelLimit": 210' in renderer
    assert '"labelPadding": 6' in renderer
    assert '{"field": "family", "type": "nominal", "title": "Family"}' in renderer

    selected_pos = browse.index('if selected is not None:')
    landscape_pos = browse.index('_render_research_landscape(rows)')
    assert selected_pos < landscape_pos
    assert 'if inventory_view == "Evidence":' in browse


def test_curves_inventory_uses_semantic_filter_card_tabs_outside_and_integer_rank_format():
    page = inspect.getsource(curves_page.page)
    inventory = inspect.getsource(curves_page._render_browse)

    assert 'title(\n        "Curves",\n        None,' in page
    assert "Permanent specializations, rigorous rank evidence" not in page
    assert 'background: var(--rh-card,#FFFFFF) !important;' in inventory
    assert 'background: #FFFFFF !important;' not in inventory
    assert 'with st.container(key="curves-browse-filters", border=False):' in inventory
    assert 'placeholder="Search family, parameter, curve ID, ICARM/LMFDB ID…"' in inventory
    assert 'label_visibility="collapsed"' in inventory
    assert 'icon=":material/search:"' not in inventory

    card_pos = inventory.index('with st.container(key="curves-browse-filters"')
    filter_pos = inventory.index('q = search_col.text_input(')
    slider_pos = inventory.index('rank_slider_col.slider(')
    family_pos = inventory.index('fam = c1.selectbox("Family"')
    campaign_pos = inventory.index('campaign_choice = c2.selectbox(')
    evidence_pos = inventory.index('evidence = c3.selectbox(')
    torsion_pos = inventory.index('torsion = c4.selectbox(')
    status_pos = inventory.index('status = c5.selectbox(')
    gap_pos = inventory.index('st.html("<div style=\'height:.85rem\'></div>")')
    tabs_pos = inventory.index('inventory_view = tabs(')
    rows_pos = inventory.index('rows = _curve_inventory_rows(')

    assert card_pos < filter_pos < slider_pos < family_pos < campaign_pos < evidence_pos < torsion_pos < status_pos < gap_pos < tabs_pos < rows_pos
    assert '["Evidence", "Arithmetic", "Catalog"]' in inventory
    assert 'workflow = c6.selectbox(' not in inventory
    assert '"rank ≥": st.column_config.NumberColumn(format="%d")' in inventory
    assert '"exact": st.column_config.NumberColumn(format="%d")' in inventory
    assert '"rigorous upper": st.column_config.NumberColumn(format="%d")' in inventory
    assert 'selectable_dataframe(' in inventory
    assert 'allow_empty=True' in inventory
    assert 'if not rows:' in inventory
    assert 'st.info("No curves match the current filters.")' in inventory
    assert '"Reset filters"' in inventory
    assert 'best_rigorous = max(' in inventory
    assert 'exact_count = sum(' in inventory
    assert 'best rigorous ≥{best_rigorous}' in inventory
    assert '_render_research_landscape(rows)' in inventory
    assert '_selected_table_record(' not in inventory


def test_curves_inventory_filters_by_persisted_torsion_without_arithmetic(tmp_path, monkeypatch):
    db = _db(tmp_path / "rank42.db")
    try:
        c2 = _curve(db, "torsion-c2", lower=4)
        c3 = _curve(db, "torsion-c3", lower=4)
        unknown = _curve(db, "torsion-unknown", lower=4)
        db.execute(
            "UPDATE curves SET torsion_label='C2', torsion_order=2, torsion_computed_at='2026-09-29T00:00:00Z' WHERE id=?",
            (c2,),
        )
        db.execute(
            "UPDATE curves SET torsion_label='C3', torsion_order=3, torsion_computed_at='2026-09-29T00:00:00Z' WHERE id=?",
            (c3,),
        )
        db.commit()
        monkeypatch.setattr(
            curves_page,
            "curve_arithmetic_state_map",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("torsion filtering should use persisted curve metadata only")
            ),
        )

        c2_rows = curves_page._curve_inventory_rows(
            db,
            torsion="C2",
            include_arithmetic=False,
            include_point_counts=False,
            lightweight_metadata=True,
        )
        unknown_rows = curves_page._curve_inventory_rows(
            db,
            torsion="Not stored",
            include_arithmetic=False,
            include_point_counts=False,
            lightweight_metadata=True,
        )

        assert [int(row["id"]) for row in c2_rows] == [c2]
        assert unknown in {int(row["id"]) for row in unknown_rows}
        assert c2 not in {int(row["id"]) for row in unknown_rows}
        assert c3 not in {int(row["id"]) for row in unknown_rows}
    finally:
        db.close()


def test_curves_inventory_lightweight_mode_skips_arithmetic_projection(tmp_path, monkeypatch):
    db = _db(tmp_path / "rank42.db")
    try:
        cid = _curve(db, "lightweight", lower=4)
        monkeypatch.setattr(
            curves_page,
            "curve_arithmetic_state_map",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("arithmetic projection should be deferred")
            ),
        )

        rows = curves_page._curve_inventory_rows(
            db,
            include_arithmetic=False,
        )

        assert [int(row["id"]) for row in rows] == [cid]
        assert "arithmetic_conductor" not in rows[0]
    finally:
        db.close()


def test_curves_research_metadata_path_skips_catalog_joins_and_full_curve_rows(tmp_path):
    db = _db(tmp_path / "rank42.db")
    try:
        cid = _curve(db, "research-light", lower=6)
        statements = []
        db.set_trace_callback(statements.append)
        rows = curves_page._curve_inventory_rows(
            db,
            include_arithmetic=False,
            include_point_counts=False,
            lightweight_metadata=True,
        )
        db.set_trace_callback(None)

        assert [int(row["id"]) for row in rows] == [cid]
        metadata = next(
            statement
            for statement in statements
            if "SELECT curves.id,curves.family,curves.parameter,curves.score,curves.status,curves.torsion_label,curves.torsion_computed_at FROM curves" in statement
        )
        assert "LEFT JOIN curve_catalog_checks" not in metadata
        assert "curves.*" not in metadata
    finally:
        db.set_trace_callback(None)
        db.close()


def test_curves_lightweight_metadata_preserves_manual_unresolved_import(tmp_path):
    db = _db(tmp_path / "rank42.db")
    try:
        imported = _curve(db, "manual-light")
        hidden = _curve(db, "hidden-light")
        log_event(db, imported, "info", "Manual external curve import from test")

        rows = curves_page._curve_inventory_rows(
            db,
            include_arithmetic=False,
            include_point_counts=False,
            lightweight_metadata=True,
        )

        ids = {int(row["id"]) for row in rows}
        assert imported in ids
        assert hidden not in ids
    finally:
        db.close()


def test_curves_point_counts_are_deferred_until_after_retention(tmp_path, monkeypatch):
    db = _db(tmp_path / "rank42.db")
    try:
        retained = _curve(db, "retained-counts", lower=5)
        _curve(db, "hidden-counts", lower=None)
        captured = {}

        def fake_counts(_db, curve_ids):
            captured["ids"] = list(curve_ids)
            return {
                retained: {
                    "point_count": 7,
                    "rigorous_point_count": 3,
                }
            }

        monkeypatch.setattr(
            curves_page,
            "_point_counts_for_curve_ids",
            fake_counts,
        )

        rows = curves_page._curve_inventory_rows(
            db,
            include_arithmetic=False,
            include_point_counts=True,
        )

        assert captured["ids"] == [retained]
        assert [int(row["id"]) for row in rows] == [retained]
        assert rows[0]["point_count"] == 7
        assert rows[0]["rigorous_point_count"] == 3

        source = inspect.getsource(curves_page._curve_inventory_rows)
        assert "GROUP BY curve_id" not in source
        assert "_point_counts_for_curve_ids(" in source
    finally:
        db.close()


def test_curves_inventory_uses_sql_rank_summary_not_full_evidence_projection():
    source = inspect.getsource(curves_page._curve_inventory_rows)

    assert "curve_rank_summary_map(" in source
    assert "curve_research_state_map(" not in source
    assert "compact=True" not in source


def test_curves_browse_defers_arithmetic_until_arithmetic_view():
    browse = inspect.getsource(curves_page._render_browse)
    inventory = inspect.getsource(curves_page._curve_inventory_rows)

    assert 'include_arithmetic=inventory_view == "Arithmetic"' in browse
    assert "curve_arithmetic_state_map(db, ids)" in inventory
    assert "if include_arithmetic" in inventory
    assert 'height=700' in browse
    assert '"Curve inventory"' not in browse


def test_curves_navigation_drills_into_dedicated_detail_with_back_button():
    page = inspect.getsource(curves_page.page)
    detail = inspect.getsource(curves_page._render_curve_detail)
    overview = inspect.getsource(curves_page._render_overview)
    portrait = inspect.getsource(curves_page._render_curve_portrait)
    actions = inspect.getsource(curves_page._render_curve_actions)

    assert '["Inventory", "Hall of Fame", "Import"]' in page
    assert 'value="Inventory"' in page
    assert 'variant="line"' in page
    assert 'width="content"' in page
    assert 'variant="page"' not in page
    assert "height:.85rem" in page
    assert 'selected_id = st.session_state.get("curves_detail_id")' in page
    assert "landing = st.empty()" in page
    assert "with landing.container():" in page
    assert "landing.empty()" in page
    assert "_render_curve_detail(db, ctx, row)" in page
    assert "st.rerun()" not in inspect.getsource(curves_page._render_browse)
    assert '"Back"' in detail
    assert 'st.session_state.pop("curves_detail_id", None)' in detail
    assert "_render_curve_portrait(db, row)" not in detail
    assert "_render_curve_portrait(db, row)" in overview
    assert overview.index("_render_curve_portrait(db, row)") < overview.index('section_title("Stored model"')
    assert '"height": 230' in portrait
    assert detail.index("_render_curve_actions(db, ctx, row)") > detail.index("_render_overview")
    assert '"Target Search"' in actions
    assert '"Points"' in actions
    assert '"Curve Explorer"' in actions
    assert '"Lattices"' in actions
    assert 'st.session_state["rh_page"] = "Lattices"' in actions
    assert '["Overview", "Rank Evidence", "Arithmetic", "History"]' in detail
    assert 'detail_view == "Research"' not in detail
    assert "_render_curve_summary_cards(row, point_stats)" in detail
    assert "_render_rank_evidence(db, row, point_stats)" in detail
    assert "render_curve_research_workflow" not in detail
    assert "_render_next_action(db, row)" in overview


def test_curves_detail_uses_semantic_summary_cards_and_folds_points_into_rank_evidence():
    cards = inspect.getsource(curves_page._render_curve_summary_cards)
    evidence = inspect.getsource(curves_page._render_rank_evidence)
    point_summary = inspect.getsource(curves_page._render_point_ledger_summary)

    assert "background:var(--rh-card,#FFFFFF)" in cards
    assert "background:#FFFFFF" not in cards
    assert '"RANK"' in cards
    assert '"POINTS"' in cards
    assert '"NAGAO"' in cards
    assert '"ICARM"' in cards
    assert "_evidence_state(row)" in cards
    assert "_render_point_ledger_summary(db, row, point_stats)" in evidence
    assert evidence.index("_render_point_ledger_summary") < evidence.index("External catalog evidence")
    assert '"Open full Point Ledger"' in point_summary
    assert 'st.session_state["rh_page"] = "Points"' in point_summary


def test_curves_history_surfaces_durable_curve_search_chronology():
    history_rows = inspect.getsource(curves_page._curve_history_rows)
    history = inspect.getsource(curves_page._render_history)

    assert '"Stored in Rank Hunter"' in history_rows
    assert '"candidates"' in history_rows
    assert '"candidate_pools"' in history_rows
    assert '"search_pipeline_point_attempts"' in history_rows
    assert '"point_discoveries"' in history_rows
    assert '"general_hunt_trials"' in history_rows
    assert '"quartic_searches"' in history_rows
    assert '"rank_evidence"' in history_rows
    assert "_curve_history_rows(db, row)" in history
    assert '"Curve timeline"' in history
    assert "Legacy searches without a durable curve link are intentionally not inferred." in history


def test_curves_detail_keeps_arithmetic_display_without_calculation_action():
    overview = inspect.getsource(curves_page._render_overview)
    arithmetic = inspect.getsource(curves_page._render_arithmetic)
    detail = inspect.getsource(curves_page._render_curve_detail)

    assert '"Naive height"' in overview
    assert '"Faltings height"' in overview
    assert '"Root number"' in arithmetic
    assert "Calculate missing arithmetic" not in overview
    assert "Calculate missing arithmetic" not in arithmetic
    assert "curve_enrich" not in overview
    assert "curve_enrich" not in arithmetic
    assert "_render_overview(db, row, point_stats)" in detail
    assert "_render_arithmetic(row)" in detail


def test_curves_import_is_a_subpage_not_browse_expander():
    page = inspect.getsource(curves_page.page)
    importer = inspect.getsource(curves_page._render_import_curve)

    assert 'elif section == "Import":' in page
    assert "_render_import_curve(db)" in page
    assert 'st.expander("Import curve"' not in importer
    assert '"#### JSON import"' in importer
    assert '"#### Manual curve"' in importer


def test_curves_hall_of_fame_uses_rigorous_rank_and_stored_torsion():
    source = inspect.getsource(curves_page._render_hall_of_fame)

    assert "hall_of_fame_groups(rows, top_per_group=5)" in source
    assert 'int(row.get("rigorous_lower") or 0) > 0' in source
    assert '"rank ≥": int(row["rigorous_lower"] or 0)' in source
    assert '"exact": row["exact_rank"]' in source
    assert '"Nagao"' in source
    assert 'return int(selected["id"])' in source
    assert "st.rerun()" not in source


def test_curves_inventory_does_not_present_conflict_as_interval(tmp_path):
    db = _db(tmp_path / "rank42.db")
    try:
        cid = _curve(db, "conflict", lower=5)
        _evidence(db, cid, upper=4)

        rows = _curve_inventory_rows(db)
        row = next(item for item in rows if int(item["id"]) == cid)
        assert row["rank_inconsistent"] is True
        assert _evidence_state(row) == "EVIDENCE CONFLICT"
        assert _curve_inventory_rows(db, evidence="Rigorous interval") == []
    finally:
        db.close()

