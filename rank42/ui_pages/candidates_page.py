"""Canonical Candidates page.

Generate and Pools remain separate implementation modules so their scientific
and pool-management semantics stay isolated. This shell owns only navigation.
"""
from __future__ import annotations

import html

import streamlit as st

from rank42.ui_components import tabs
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
    totals = db.execute("SELECT COUNT(*) AS pools FROM candidate_pools").fetchone()
    queue = {
        str(row["status"]): int(row["count"])
        for row in db.execute(
            "SELECT status,COUNT(*) AS count FROM candidates GROUP BY status"
        ).fetchall()
    }
    latest = db.execute(
        "SELECT id,name,created_at FROM candidate_pools ORDER BY id DESC LIMIT 1"
    ).fetchone()
    latest_label = (
        f"Pool #{int(latest['id'])} · {html.escape(str(latest['name']))}"
        if latest is not None else "No pools saved yet"
    )
    latest_date = str(latest["created_at"] or "")[:10] if latest is not None else "—"
    unsearched = queue.get("unsearched", 0)
    searched = queue.get("searched", 0)
    st.html(
        '<section class="rh-candidates-hero" aria-label="Candidate workbench">'
        '<div class="rh-candidates-hero-main">'
        '<div><span class="rh-candidates-eyebrow">CANDIDATE INVENTORY</span>'
        f'<h2>{unsearched:,} unsearched candidates</h2>'
        f'<p>Newest: {latest_label}</p></div>'
        '</div>'
        '<div class="rh-candidates-hero-facts">'
        f'<div><span>STORED POOLS</span><strong>{int(totals["pools"] or 0):,}</strong></div>'
        f'<div><span>SEARCHED</span><strong>{searched:,}</strong></div>'
        f'<div><span>LATEST POOL DATE</span><strong>{latest_date}</strong></div>'
        '</div></section>'
    )
    if choice == "Pools":
        return candidate_pools_page.page(db, ctx, embedded=True)
    return candidate_generate_page.page(db, ctx, embedded=True)
