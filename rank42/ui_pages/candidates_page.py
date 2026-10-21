"""Canonical Candidates page for Rank Hunter 0.9.1.

Generate and Pools remain separate implementation modules so their scientific
and pool-management semantics stay isolated. This shell owns only navigation.
"""
from __future__ import annotations

import streamlit as st

from rank42.ui_components import rh_key, tabs
from . import candidate_generate_page, candidate_pools_page
from .common import title

_candidate_control_keys = candidate_generate_page._candidate_control_keys
_sync_candidate_state_version = candidate_generate_page._sync_candidate_state_version
_apply_candidate_preset = candidate_generate_page._apply_candidate_preset
_candidate_preset_modified = candidate_generate_page._candidate_preset_modified
_widget_default_kwargs = candidate_generate_page._widget_default_kwargs
_reconcile_pool_for_ui = candidate_pools_page._reconcile_pool_for_ui
_candidate_corpus_view = candidate_pools_page._candidate_corpus_view


def page(db, ctx):
    title(
        "Candidates",
        "Generate ranked specialization queues and manage durable Candidate Pools.",
        icon="scatter_plot",
    )

    current = str(st.session_state.get("candidates_tab") or "Generate")
    if current == "Import / Export":
        st.session_state["candidate_pools_view"] = "Import & Export"
        current = "Pools"
    if current not in {"Generate", "Pools"}:
        current = "Generate"

    choice = tabs(
        ["Generate", "Pools"],
        value=current,
        key="candidates-main-tabs",
        variant="line",
        width="content",
    )
    st.session_state["candidates_tab"] = choice
    st.html("<div style='height:.85rem'></div>")

    if choice == "Pools":
        return candidate_pools_page.page(db, ctx, embedded=True)
    return candidate_generate_page.page(db, ctx, embedded=True)
