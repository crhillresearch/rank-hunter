"""One search workbench built from existing search engines."""

from __future__ import annotations

import html
import json
from pathlib import Path

import streamlit as st

from rank42.feature_hooks import render_feature_hook

from . import (
    auto_search,
    candidate_generate_page,
    family_search,
    general_hunt,
    target_curve,
)
from .common import embedded_page_title, title


TARGETS = ("Family", "Torsion", "Candidates", "Target", "General")
_NAV_GROUPS = (
    ("New Search", ("Family", "Torsion", "General")),
    ("Saved Targets", ("Candidates", "Target")),
)
_NAV_ITEMS = {
    "Family": ("account_tree", "Search or generate from a family"),
    "Torsion": ("hub", "Search an exact torsion group"),
    "Candidates": ("filter_alt", "Search a stored candidate pool"),
    "Target": ("target", "Attack a stored curve"),
    "General": ("scatter_plot", "Search without a family"),
}
_COMMAND_BAR_JS = (
    Path(__file__).resolve().parents[1] / "ui_assets" / "tools_command_bar.js"
).read_text(encoding="utf-8")
_SEARCH_COMMAND_BAR = st.components.v2.component(
    "rh_new_search_command_bar", js=_COMMAND_BAR_JS, isolate_styles=False
)


def _set_view(name):
    st.session_state["new_search_view"] = str(name)


def _page_nav(selected):
    groups = [
        {
            "label": group_name,
            "tools": [
                {
                    "name": name,
                    "icon": _NAV_ITEMS[name][0],
                    "description": _NAV_ITEMS[name][1],
                }
                for name in names
            ],
        }
        for group_name, names in _NAV_GROUPS
    ]
    config = json.dumps({
        "selected": selected,
        "groups": groups,
        "rootId": "rh-new-search-commandbar",
        "bridgePrefix": "rh-new-search-view-",
        "controllerSlot": "__rhToolsCommandBar",
        "queryParam": None,
        "toolbarLabel": "Rank Hunter search views",
        "itemNoun": "views",
        "currentNoun": "search view",
        "mobileLabel": "Search views",
        "pageTitle": "Search",
        "pageIcon": "search",
    }).replace("<", "\\u003c")
    st.html(
        '<div id="rh-new-search-commandbar" class="rh-tools-commandbar-host" '
        f'aria-label="Search views; current view {html.escape(selected)}"></div>'
    )
    _SEARCH_COMMAND_BAR(data=json.loads(config), key="rh-new-search-command-runtime")
    with st.container(key="rh-new-search-command-bridge"):
        for name in TARGETS:
            slug = name.lower().replace(" ", "-")
            st.button(
                name,
                key=f"rh-new-search-view-{slug}",
                on_click=_set_view,
                args=(name,),
            )


def page(db, ctx):
    render_feature_hook(db, ctx, "search.after_header")
    current = str(st.session_state.get("new_search_view") or "Family")
    current = {
        "Torsion Group": "Torsion",
        "Candidate Pool": "Candidates",
        "Stored Curve": "Target",
        "General Curves": "General",
    }.get(current, current)
    selected = current if current in TARGETS else "Family"
    st.session_state["new_search_view"] = selected
    _page_nav(selected)

    if selected == "Family":
        if st.session_state.get("new-search-family-workflow") == "Detailed":
            st.session_state["new-search-family-workflow"] = "Pool Search"
        workflow = st.segmented_control(
            "Family workflow",
            ["Automatic", "Pool Search", "Generate"],
            default="Automatic",
            key="new-search-family-workflow",
            width="content",
            label_visibility="collapsed",
        ) or "Automatic"
        if workflow == "Generate":
            source = st.segmented_control(
                "Family source",
                ["Plugin family", "Custom equation"],
                default="Plugin family",
                key="new-search-family-generate-source",
                width="content",
                label_visibility="collapsed",
            ) or "Plugin family"
            candidate_generate_page.page(
                db,
                ctx,
                embedded=True,
                source_method=(
                    "Custom Equation" if source == "Custom equation" else "Family"
                ),
                new_search=True,
            )
        elif workflow == "Pool Search":
            family_search.render(
                db,
                ctx,
                embedded=True,
                compact=False,
                prefer_plugin_presets=True,
                new_search=True,
            )
        else:
            with st.container(key="rh-new-search-setup", border=True):
                st.markdown("### Search a family")
                auto_search._render_auto(db, ctx, search_by="Family", embedded=True)
    elif selected == "Torsion":
        workflow = st.segmented_control(
            "Torsion workflow",
            ["Automatic", "Generate"],
            default="Automatic",
            key="new-search-torsion-workflow",
            width="content",
            label_visibility="collapsed",
        ) or "Automatic"
        if workflow == "Generate":
            candidate_generate_page.page(
                db,
                ctx,
                embedded=True,
                source_method="Torsion Group",
                new_search=True,
            )
        else:
            with st.container(key="rh-new-search-setup", border=True):
                st.markdown("### Search a torsion group")
                auto_search._render_auto(db, ctx, search_by="Torsion", embedded=True)
    elif selected == "Candidates":
        with st.container(key="rh-new-search-setup", border=True):
            st.markdown("### Search a candidate pool")
            family_search.render(
                db,
                ctx,
                embedded=True,
                compact=True,
                prefer_plugin_presets=True,
                new_search=True,
            )
    elif selected == "Target":
        with embedded_page_title():
            target_curve.page(db, ctx, new_search=True)
    else:
        with st.container(key="rh-new-search-setup", border=True):
            st.markdown("### Search general curves")
            general_hunt.render(db, ctx, embedded=True, compact=True)
