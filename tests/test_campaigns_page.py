import inspect

from rank42 import manage_store
from rank42.ui_pages import (
    campaign_handoff_panel,
    campaigns_landing_panel,
    campaigns_page,
)
from rank42.ui_pages import common


def test_campaign_landing_is_extracted_without_changing_compatibility_names():
    page_source = inspect.getsource(campaigns_page)

    for definition in (
        "def _campaign_status_label(",
        "def _campaign_status_value(",
        "def _campaign_family_choices(",
        "def _campaign_family_selector(",
        "def _create_campaign_dialog(",
        "def _campaign_row_meta(",
        "def _campaign_component_event(",
        "def _campaign_add_button(",
        "def _campaign_row(",
        "def _campaign_page_style(",
        "def _render_campaign_header(",
        "def _campaign_search_blob(",
        "def _filtered_campaigns(",
        "def _campaign_page_slice(",
        "def _render_campaign_landing(",
    ):
        assert definition not in page_source

    assert (
        campaigns_page.CAMPAIGN_MANAGER_COMPONENT_DIR
        == campaigns_landing_panel.CAMPAIGN_MANAGER_COMPONENT_DIR
    )
    assert (
        campaigns_page._campaign_status_label
        is campaigns_landing_panel.campaign_status_label
    )
    assert (
        campaigns_page._campaign_status_value
        is campaigns_landing_panel.campaign_status_value
    )
    assert (
        campaigns_page._campaign_family_choices
        is campaigns_landing_panel.campaign_family_choices
    )
    assert (
        campaigns_page._campaign_family_selector
        is campaigns_landing_panel.campaign_family_selector
    )
    assert (
        campaigns_page._create_campaign_dialog
        is campaigns_landing_panel.create_campaign_dialog
    )
    assert campaigns_page._campaign_row is campaigns_landing_panel.campaign_row
    assert (
        campaigns_page._render_campaign_header
        is campaigns_landing_panel.render_campaign_header
    )
    assert (
        campaigns_page._filtered_campaigns
        is campaigns_landing_panel.filtered_campaigns
    )
    assert (
        campaigns_page._campaign_page_slice
        is campaigns_landing_panel.campaign_page_slice
    )
    assert (
        campaigns_page._render_campaign_landing
        is campaigns_landing_panel.render_campaign_landing
    )

    landing_source = inspect.getsource(
        campaigns_landing_panel.render_campaign_landing
    )
    for key in (
        "campaign-list-search",
        "campaign-list-status",
        "campaign-list-sort",
        "campaign-list-page-size",
        "campaign-list-prev",
        "campaign-list-next",
        "campaign-list-page",
        "manage_campaign_id",
    ):
        assert key in (
            landing_source
            + inspect.getsource(campaigns_landing_panel.campaign_row)
        )


def test_campaign_manager_component_is_packaged_with_fontawesome_controls():
    asset = campaigns_page.CAMPAIGN_MANAGER_COMPONENT_DIR / "index.html"
    assert asset.is_file()
    html = asset.read_text()

    for icon in ("play", "pause", "check", "box-archive"):
        assert f'"{icon}":' in html
    assert 'emit("open")' in html
    assert 'action("activate","play"' in html
    assert 'action("pause","pause"' in html
    assert 'args.status==="active"' in html
    assert 'args.active ? "Resume campaign" : "Resume & set current campaign"' in html
    assert 'args.active ? "Reopen campaign" : "Reopen & set current campaign"' in html
    assert "if(!args.active){" not in html
    assert 'action("confirm-complete","check"' in html
    assert 'action("confirm-archive","box-archive"' in html
    assert 'confirmLifecycle(actions,"complete","Mark completed?")' in html
    assert 'confirmLifecycle(actions,"archive","Archive campaign?")' in html
    assert 'ok.textContent="OK"' in html
    assert 'cancel.textContent="Cancel"' in html
    assert 'row.className="campaign"+(args.active?" active":"")' in html
    assert 'badge.textContent="CURRENT"' in html
    assert '"Set current campaign"' in html
    assert '"Resume campaign"' in html
    assert '"Reopen campaign"' in html
    assert 'badge.textContent="ACTIVE"' not in html
    assert 'progress.className="progress-wrap"' in html
    assert 'progressFill.style.width=(fraction*100).toFixed(1)+"%"' in html
    assert "streamlit:setComponentValue" in html
    assert "streamlit:componentReady" in html


def test_campaign_landing_is_lightweight_and_has_empty_state():
    source = inspect.getsource(campaigns_page._render_campaign_landing)

    assert "list_campaigns(" in source
    assert "active_campaign(" in source
    assert "current_campaign(" not in source
    assert "_campaign_add_button(" not in source
    assert "_campaign_row(" in source
    assert "No campaigns yet. Use New Campaign above" in source
    assert "campaign_research_brief(" not in source
    assert "campaign_snapshot(" not in source


def _campaign_row_fixture(
    campaign_id,
    name,
    *,
    status="paused",
    target_rank=None,
    family=None,
    plugin_id=None,
    variant_id=None,
    objective="",
    notes="",
    created_at="2026-01-01T00:00:00+00:00",
    updated_at="2026-01-01T00:00:00+00:00",
):
    return {
        "id": int(campaign_id),
        "name": str(name),
        "status": str(status),
        "target_rank": target_rank,
        "family": family,
        "plugin_id": plugin_id,
        "variant_id": variant_id,
        "objective": objective,
        "notes": notes,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def test_campaign_landing_highlight_is_active_lifecycle_only():
    source = inspect.getsource(campaigns_landing_panel.render_campaign_landing)

    assert "current = active_campaign(db)" in source
    assert "current = current_campaign(db)" not in source
    assert 'current_id = None if current is None else int(current["id"])' in source


def test_campaign_filter_search_sort_and_current_pin():
    rows = [
        _campaign_row_fixture(
            1,
            "Beta Hunt",
            status="paused",
            target_rank=11,
            family="CEF",
            plugin_id="dmt",
            objective="Search the CEF corpus",
            updated_at="2026-09-20T10:00:00+00:00",
        ),
        _campaign_row_fixture(
            2,
            "Alpha Hunt",
            status="completed",
            target_rank=32,
            family="ICARM",
            updated_at="2026-09-21T10:00:00+00:00",
        ),
        _campaign_row_fixture(
            3,
            "Gamma Hunt",
            status="active",
            target_rank=20,
            family="Other",
            notes="prime experiments",
            updated_at="2026-09-19T10:00:00+00:00",
        ),
    ]

    searched = campaigns_page._filtered_campaigns(
        rows,
        query="cef",
        status="All",
        sort="Recently updated",
        current_id=3,
    )
    assert [row["id"] for row in searched] == [1]

    open_rows = campaigns_page._filtered_campaigns(
        rows,
        status="Open",
        sort="Recently updated",
    )
    assert [row["id"] for row in open_rows] == [3]

    paused = campaigns_page._filtered_campaigns(
        rows,
        status="Paused",
        sort="Recently updated",
    )
    assert [row["id"] for row in paused] == [1]

    alphabetical = campaigns_page._filtered_campaigns(
        rows,
        sort="Name A–Z",
        current_id=3,
    )
    assert [row["id"] for row in alphabetical] == [3, 2, 1]

    target_desc = campaigns_page._filtered_campaigns(
        rows,
        sort="Target rank ↓",
    )
    assert [row["id"] for row in target_desc] == [2, 3, 1]


def test_campaign_page_slice_clamps_and_limits_results():
    rows = [
        _campaign_row_fixture(index, f"Campaign {index}")
        for index in range(1, 46)
    ]

    page_rows, page, total_pages, start, end = campaigns_page._campaign_page_slice(
        rows,
        page=2,
        page_size=20,
    )
    assert [row["id"] for row in page_rows] == list(range(21, 41))
    assert (page, total_pages, start, end) == (2, 3, 20, 40)

    last_rows, page, total_pages, start, end = campaigns_page._campaign_page_slice(
        rows,
        page=99,
        page_size=20,
    )
    assert [row["id"] for row in last_rows] == list(range(41, 46))
    assert (page, total_pages, start, end) == (3, 3, 40, 45)


def test_campaign_landing_has_search_sort_status_and_pagination_controls():
    source = inspect.getsource(campaigns_page._render_campaign_landing)

    assert '"Search campaigns"' in source
    assert '["All", "Open", "Paused", "Completed", "Archived"]' in source
    assert '"Recently updated"' in source
    assert '"Name A–Z"' in source
    assert '"Target rank ↓"' in source
    assert "[10, 20, 50]" in source
    assert "_filtered_campaigns(" in source
    assert "_campaign_page_slice(" in source
    assert '"Previous"' in source
    assert '"Next"' in source


def test_campaign_header_matches_manager_page_pattern():
    header = inspect.getsource(campaigns_page._render_campaign_header)
    page = inspect.getsource(campaigns_page.page)

    assert 'title(' in header
    assert '"Campaigns"' in header
    assert '_campaign_add_button(db, ctx)' in header
    add = inspect.getsource(campaigns_page._campaign_add_button)
    assert 'st.button(' in add
    assert '"New Campaign"' in add
    assert 'icon=":material/add:"' in add
    assert '_CAMPAIGN_MANAGER(' not in add
    assert 'rh_key("campaigns-header-title")' in header
    assert 'rh_key("campaigns-header-action")' in header
    assert '_campaign_page_style()' in page
    assert '_render_campaign_header(db, ctx)' in page


def test_campaign_status_language_separates_open_from_current_context():
    assert campaigns_page._campaign_status_label("active") == "Open"
    assert campaigns_page._campaign_status_label("paused") == "Paused"
    assert campaigns_page._campaign_status_value("Open") == "active"
    assert campaigns_page._campaign_status_value("Active") == "active"

    detail = inspect.getsource(campaigns_page._overview)
    edit = inspect.getsource(campaigns_page._edit_campaign)
    landing = inspect.getsource(campaigns_page._render_campaign_landing)

    assert 'current_label = "Current campaign" if is_current else "Set current"' in detail
    assert 'lifecycle_label = "Pause"' in detail
    assert 'lifecycle_label = "Resume"' in detail
    assert 'lifecycle_label = "Reopen"' in detail
    assert "set_current_campaign(db, int(row[\"id\"]))" in detail
    assert 'status=lifecycle_next' in detail
    assert "format_func=_campaign_status_label" in edit
    assert '["All", "Open", "Paused", "Completed", "Archived"]' in landing


def test_campaign_manager_persists_exact_family_variant():
    create = inspect.getsource(campaigns_page._create_campaign_dialog)
    edit = inspect.getsource(campaigns_page._edit_campaign)
    selector = inspect.getsource(campaigns_page._campaign_family_selector)

    assert 'variant_id=family_choice["variant_id"]' in create
    assert 'current_variant_id=row["variant_id"]' in edit
    assert 'variant_id=family_choice["variant_id"]' in edit
    assert 'rec["variant_id"] == wanted_variant' in selector


def test_campaign_cards_follow_streamlit_theme_and_lifecycle_icons_are_borderless():
    asset = campaigns_page.CAMPAIGN_MANAGER_COMPONENT_DIR / "index.html"
    html = asset.read_text()

    assert 'background:var(--rh-surface);' in html
    assert 'background:#fff;' not in html
    assert 'theme.secondaryBackgroundColor||theme.backgroundColor' in html
    assert 'root.setProperty("--rh-surface",surface)' in html
    assert 'function applyTheme(theme,palette)' in html
    assert 'palette["rh-card"]' in html
    assert 'palette["rh-surface-hover"]' in html
    assert 'applyTheme(event.data.theme,args.palette||{})' in html
    assert 'root.setProperty("--rh-shadow"' in html
    assert 'button.action {' in html
    assert 'border:0;' in html
    assert 'button.add {' not in html
    assert '<span>New Campaign</span>' not in html


def test_campaign_row_passes_lightweight_progress_to_component():
    source = inspect.getsource(campaigns_page._campaign_row)

    assert "campaign_progress_summary(db, campaign_id)" in source
    assert 'progress_label=str(progress["label"])' in source
    assert 'progress_fraction=progress["fraction"]' in source
    assert 'progress_blocked=bool(progress["blocked"])' in source
    assert "palette=_component_theme_palette()" in source


def test_campaign_progress_summary_uses_rigorous_lower_over_target(monkeypatch):
    monkeypatch.setattr(
        manage_store,
        "get_campaign",
        lambda db, campaign_id: {"id": campaign_id, "target_rank": 11},
    )
    monkeypatch.setattr(
        manage_store,
        "_campaign_curve_rows",
        lambda db, campaign_id, limit=1: [
            {
                "id": 77,
                "rigorous_lower": 8,
                "rigorous_upper": None,
            }
        ],
    )

    summary = manage_store.campaign_progress_summary(object(), 2)
    assert summary["best_curve_id"] == 77
    assert summary["best_lower"] == 8
    assert summary["blocked"] is False
    assert summary["fraction"] == 8 / 11
    assert summary["label"] == "Rigorous ≥8 / target ≥11"


def test_campaign_progress_summary_marks_conflicting_evidence(monkeypatch):
    monkeypatch.setattr(
        manage_store,
        "get_campaign",
        lambda db, campaign_id: {"id": campaign_id, "target_rank": 12},
    )
    monkeypatch.setattr(
        manage_store,
        "_campaign_curve_rows",
        lambda db, campaign_id, limit=1: [
            {
                "id": 91,
                "rigorous_lower": 9,
                "rigorous_upper": 8,
            }
        ],
    )

    summary = manage_store.campaign_progress_summary(object(), 3)
    assert summary["blocked"] is True
    assert summary["fraction"] == 0.0
    assert summary["label"] == "Evidence conflict · target ≥12"


def test_campaign_row_routes_open_and_lifecycle_actions():
    source = inspect.getsource(campaigns_page._campaign_row)

    assert 'action == "open"' in source
    assert 'st.session_state["manage_campaign_id"] = campaign_id' in source
    assert 'action == "activate"' in source
    assert "set_current_campaign(db, campaign_id)" in source
    assert 'action == "pause"' in source
    assert 'status="paused"' in source
    assert 'action in {"resume", "reopen"}' in source
    assert 'status="active"' in source
    resume_pos = source.index('action in {"resume", "reopen"}')
    assert source.index("set_current_campaign(db, campaign_id)", resume_pos) > resume_pos
    assert 'action == "complete"' in source
    assert 'status="completed"' in source
    assert 'action == "archive"' in source
    assert 'status="archived"' in source


def test_campaign_handoff_is_extracted_without_changing_detail_route():
    page_source = inspect.getsource(campaigns_page)
    detail_source = inspect.getsource(campaigns_page._render_campaign_detail)
    handoff_source = inspect.getsource(campaign_handoff_panel.render_handoff)

    assert "def _handoff(db, brief):" not in page_source
    assert campaigns_page._handoff is campaign_handoff_panel.render_handoff
    assert "_handoff(db, brief)" in detail_source

    assert "campaign_handoff_markdown(" in handoff_source
    assert '"#### Research handoff"' in handoff_source
    assert '"Download Markdown handoff"' in handoff_source
    assert "campaign-handoff-download-" in handoff_source
    assert "campaign-" in handoff_source
    assert "-handoff.md" in handoff_source
    assert 'mime="text/markdown"' in handoff_source
    assert '"Raw Markdown"' in handoff_source
    assert 'language="markdown"' in handoff_source
    assert "does not persist a second scientific record" in handoff_source


def test_campaign_detail_tabs_are_prominent_and_handoff_is_last():
    source = inspect.getsource(campaigns_page._render_campaign_detail)
    style = inspect.getsource(campaigns_page._campaign_page_style)

    assert "campaign_research_brief(" in source
    assert '["Overview", "Progress", "Schedules", "Notebook", "Handoff"]' in source
    assert 'current == "Notes"' in source
    assert 'rh_key("campaign-detail-tabs")' in source
    assert 'if choice == "Overview":' in source
    assert 'elif choice == "Progress":' in source
    assert 'elif choice == "Schedules":' in source
    assert 'elif choice == "Notebook":' in source
    assert "_overview(db, ctx, snapshot, brief)" in source
    assert '_progress(brief["snapshot"], brief)' in source
    assert "_schedules(campaign_snapshot(db, campaign_id))" in source
    assert "_notes(db, campaign_snapshot(db, campaign_id))" in source
    assert "_handoff(db, brief)" in source
    assert "min-height:3rem !important;" in style
    assert "font-size:1rem !important;" in style
    assert "font-weight:700 !important;" in style


def test_campaign_notebook_tab_is_typed_journal_and_danger_zone_is_on_overview():
    notes = inspect.getsource(campaigns_page._notes)
    detail = inspect.getsource(campaigns_page._render_campaign_detail)

    assert '"#### Research Notebook"' in notes
    assert '"Entry type"' in notes
    assert '"Handoff state"' in notes
    assert '"Linked object"' in notes
    assert "NOTEBOOK_ENTRY_TYPES" in notes
    assert "NOTEBOOK_HANDOFF_STATES" in notes
    assert "campaign_notebook_entries(db, campaign_id, limit=500)" in notes
    assert "add_campaign_note(" in notes
    assert '"legacy campaign note · included in handoff for compatibility"' in notes

    overview_branch = detail.index('if choice == "Overview":')
    overview_pos = detail.index("_overview(db, ctx, snapshot, brief)", overview_branch)
    danger_pos = detail.index("_danger(db, row)", overview_pos)
    progress_branch = detail.index('elif choice == "Progress":', danger_pos)
    progress_pos = detail.index('_progress(brief["snapshot"], brief)', progress_branch)
    notes_branch = detail.index('elif choice == "Notebook":', progress_pos)
    notes_pos = detail.index(
        "_notes(db, campaign_snapshot(db, campaign_id))",
        notes_branch,
    )
    assert "st.divider()" not in detail[overview_pos:progress_branch]
    assert overview_pos < danger_pos < progress_branch < progress_pos < notes_branch < notes_pos


def test_campaign_progress_tab_has_dashboard_hierarchy_and_grouped_actions():
    progress = inspect.getsource(campaigns_page._progress)
    history = inspect.getsource(campaigns_page._strategy_history)
    style = inspect.getsource(campaigns_page._campaign_page_style)

    assert '"### Campaign progress"' in progress
    assert '"Rigorous frontier"' in progress
    assert '"Target"' in progress
    assert '"Gap"' in progress
    assert '"Candidates"' in progress
    assert '"Active work"' in progress
    assert '"Pipeline runs"' in progress
    assert "st.progress(" in progress
    assert 'key=rh_key("campaign-progress-summary")' in progress

    assert 'key=rh_key("campaign-progress-strategies")' in history
    assert 'key=rh_key("campaign-progress-frontier")' in history
    assert 'key=rh_key("campaign-progress-activity")' in history
    assert 'st.markdown("#### Strategy performance")' in history
    assert 'st.markdown("#### Frontier history")' in history
    assert 'st.markdown("#### Recent execution")' in history

    assert 'key=rh_key("campaign-progress-pipelines")' in progress
    assert 'key=rh_key("campaign-progress-jobs")' in progress
    assert '"Open in Pipelines"' in progress
    assert '"Open in Jobs"' in progress
    assert "[5.5, 1.3]" in progress

    assert "campaign-progress-summary" in style
    assert "campaign-progress-strategies" in style
    assert "margin-top:.7rem !important;" in style


def test_campaign_research_brief_exposes_explicit_curve_pins():
    source = inspect.getsource(campaigns_page._campaign_pinned_curves)
    brief = inspect.getsource(campaigns_page._research_brief)

    assert 'f"Pinned curves ({len(rows)})"' in source
    assert '"Pins record Campaign research association only.' in source
    assert "pin_campaign_curve(" in source
    assert "unpin_campaign_curve(" in source
    assert '"Curve ID"' in source
    assert "_campaign_pinned_curves(db, int(campaign[\"id\"]))" in brief


def test_campaign_edit_form_no_longer_owns_notes_field():
    source = inspect.getsource(campaigns_page._edit_campaign)

    assert 'st.text_area("Notes"' not in source
    assert "notes=notes" not in source


def test_campaign_page_uses_selected_id_as_detail_route():
    source = inspect.getsource(campaigns_page.page)

    assert 'st.session_state.get("manage_campaign_id")' in source
    assert "get_campaign(db, int(selected_id))" in source
    assert "_render_campaign_detail(db, ctx, row)" in source
    assert "_render_campaign_landing(db, ctx)" in source


def test_active_campaign_pin_routes_to_campaign_detail():
    source = inspect.getsource(common.active_campaign_pin)

    assert 'st.session_state["manage_campaign_id"] = int(payload["id"])' in source
    assert 'st.session_state["rh_page"] = "Campaigns"' in source
    assert "--rh-active-campaign-pin-bg" in source
    assert "var(--rh-success, #1fa65d) 72%" in source


def test_campaign_row_meta_separates_current_pointer_from_lifecycle():
    open_row = {
        "id": 9,
        "name": "Rank Hunt",
        "status": "active",
        "target_rank": 12,
        "family": "CEF",
    }
    paused_row = dict(open_row, status="paused")
    assert campaigns_page._campaign_row_meta(open_row, is_current=True) == (
        "#9 · Current · Open · target ≥12 · CEF"
    )
    assert campaigns_page._campaign_row_meta(open_row, is_current=False) == (
        "#9 · Open · target ≥12 · CEF"
    )
    assert campaigns_page._campaign_row_meta(paused_row, is_current=False) == (
        "#9 · Paused · target ≥12 · CEF"
    )



def test_create_campaign_dialog_opens_its_own_database_connection():
    source = inspect.getsource(campaigns_page._create_campaign_dialog)
    add = inspect.getsource(campaigns_page._campaign_add_button)

    assert "connect_existing(ctx.db_path)" in source
    assert "create_campaign(\n            live," in source
    assert "set_current_campaign(live, cid)" in source
    assert "finally:" in source
    assert "live.close()" in source
    assert "_create_campaign_dialog(ctx)" in add
    assert "_create_campaign_dialog(db, ctx)" not in add
