from __future__ import annotations

import html
import json
import re
import sys

import streamlit as st

from rank42.plugins import (
    Plugin,
    archive_plugin,
    corpora_for_variant,
    discover_plugins,
    family_rank_claim,
    is_archived,
    is_enabled,
    plugin_state,
    restore_plugin,
    search_presets_for_variant,
    set_plugin_state,
    unavailable_plugin_states,
    validation_freshness,
)
from rank42.ui_components import tabs, region, button, rh_key
from .common import launch, setting, title


def _plugin_icon_html(plugin):
    icon = plugin.icon
    if not icon:
        return ""
    symbol = icon[len(":material/"):-1]
    return (
        f'<span class="rh-plugin-card-icon" aria-hidden="true" '
        f'title="{html.escape(symbol, quote=True)}">{html.escape(symbol)}</span>'
    )


def _experimental_icon_html(plugin):
    if (plugin.manifest or {}).get("experimental") is not True:
        return ""
    return (
        '<span class="rh-plugin-card-icon rh-experimental-marker" '
        'title="Experimental" aria-label="Experimental">experiment</span>'
    )


def _plugin_icon_styles():
    st.html(
        """
        <style>
        .rh-plugin-card-icon {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            flex: 0 0 auto;
            font-family: "Material Symbols Rounded", "Material Symbols Outlined", sans-serif;
            font-weight: 400;
            font-style: normal;
            font-size: 1.16rem;
            line-height: 1;
            letter-spacing: normal;
            text-transform: none;
            white-space: nowrap;
            font-feature-settings: "liga";
            -webkit-font-feature-settings: "liga";
            font-variation-settings: "FILL" 0, "wght" 400, "GRAD" 0, "opsz" 20;
            opacity: .92;
        }
        .rh-plugin-name .rh-plugin-card-icon { margin-right: .34rem; vertical-align: -.12rem; }
        .rh-family-title .rh-plugin-card-icon { margin-right: 0; }
        .rh-experimental-marker { cursor: help; opacity: .68; }
        .rh-experimental-marker:hover { opacity: .95; }

        [class*="st-key-rh-plugin-feature-"] .rh-plugin-name .rh-status-dot,
        [class*="st-key-rh-plugin-extension-"] .rh-plugin-name .rh-status-dot {
            margin-right: .48rem !important;
        }
        </style>
        """
    )


def _plugin_status(db, plugin):
    state = plugin_state(db, plugin.id)
    enabled = is_enabled(db, plugin)
    status = state["status"] if state else ("ready" if enabled else "unvalidated")
    return state, enabled, status


def _plugin_state_label(db, plugin):
    _state, enabled, status = _plugin_status(db, plugin)
    if status == "archived":
        return "Archived"
    if status == "invalid":
        return "Invalid"
    freshness = validation_freshness(db, plugin)
    if freshness["state"] == "needs_revalidation":
        return f"{'Enabled' if enabled else 'Disabled'} · Needs revalidation"
    if freshness["state"] == "ready":
        return f"{'Enabled' if enabled else 'Disabled'} · Ready"
    return f"{'Enabled' if enabled else 'Disabled'} · Unvalidated"


def _activate(db, ctx, plugin):
    py = setting(db, "science_python", ctx.detected_science_python()) if plugin.plugin_type == "family" else sys.executable
    jid = launch(
        ctx,
        db,
        kind="plugin_validate",
        label=f"Activate {plugin.plugin_type} · {plugin.name}",
        command=[
            py,
            "-m",
            "rank42.plugin_validate",
            "--project-root",
            ctx.project_root,
            "--db",
            ctx.db_path,
            "--plugin",
            plugin.id,
        ],
    )
    st.success(f"Started activation job #{jid}.")


def _family_description(plugin):
    description = str(plugin.description or "").strip()
    tooltip = html.escape(description, quote=True)
    body = html.escape(description) if description else "&nbsp;"
    st.html(f'<div class="rh-family-description" title="{tooltip}">{body}</div>')


def _two_sentence_excerpt(text):
    text = " ".join(str(text or "").split()).strip()
    if not text:
        return ""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if len(sentences) <= 2:
        return text
    return " ".join(sentences[:2]).rstrip() + " …"


def _extension_description(plugin):
    description = str(plugin.description or "").strip()
    excerpt = _two_sentence_excerpt(description)
    tooltip = html.escape(description, quote=True)
    body = html.escape(excerpt) if excerpt else "&nbsp;"
    st.html(f'<div class="rh-extension-description" title="{tooltip}">{body}</div>')


def _family_layout_styles():
    st.html(
        """
        <style>
        .rh-family-header-row {
            width: 100%; min-width: 0; box-sizing: border-box;
            display: flex; align-items: center; gap: .6rem;
        }
        .rh-family-heading {
            flex: 1 1 auto; min-width: 0; min-height: 3.8rem;
            display: flex; flex-direction: column; justify-content: center;
        }
        .rh-family-title {
            min-height: 1.45rem; display: flex; align-items: center;
            gap: .34rem; min-width: 0;
        }
        .rh-family-title-text {
            min-width: 0;
            font-size: 1.02rem;
            font-weight: 800;
            line-height: 1.15;
        }
        .rh-meta-provenance {
            display: inline-flex; align-items: center; justify-content: center;
            width: 1.05rem; height: 1.05rem; margin-left: .16rem;
            font-size: .86rem; line-height: 1; vertical-align: -.08rem;
            opacity: .62; cursor: help;
        }
        .rh-meta-provenance:hover { opacity: .95; }
        .rh-family-meta {
            min-height: 1.15rem; margin-top: .04rem; white-space: nowrap;
            overflow: hidden; text-overflow: ellipsis;
        }
        [class*="st-key-rh-plugins-install-action-"] {
            width: 100% !important;
            min-height: 3.15rem;
            display: flex;
            align-items: center;
            justify-content: flex-end;
        }
        [class*="st-key-rh-plugins-install-action-"] [data-testid="stVerticalBlock"] {
            width: 100% !important;
            align-items: flex-end;
        }
        .rh-rank-verification {
            position: absolute; top: .18rem; right: .22rem;
            display: inline-flex; align-items: center; justify-content: center;
            width: .85rem; height: .85rem; font-size: .68rem; line-height: 1;
            opacity: .72; cursor: help;
        }
        .rh-family-description {
            min-height: 2.35rem; max-height: 2.35rem; margin: .72rem 0 .32rem;
            overflow: hidden; display: -webkit-box; -webkit-box-orient: vertical;
            -webkit-line-clamp: 2; line-height: 1.175rem; font-size: .84rem; opacity: .68;
        }
        .rh-extension-description {
            min-height: 2.7rem; max-height: 2.7rem; margin: .85rem 0 .35rem;
            overflow: hidden; display: -webkit-box; -webkit-box-orient: vertical;
            -webkit-line-clamp: 2; text-overflow: ellipsis;
            line-height: 1.35rem; font-size: .88rem; opacity: .72;
        }
        .rh-rank-box {
            flex: 0 0 3.8rem; width: 3.8rem; height: 3.8rem; min-width: 3.8rem; min-height: 3.8rem;
            max-width: 3.8rem; max-height: 3.8rem; box-sizing: border-box; margin: 0;
            position: relative; display: flex; flex-direction: column;
            align-items: center; justify-content: center;
        }
        .rh-rank-box .value { font-size: 1.22rem; line-height: 1.05; }
        .rh-rank-box .label {
            max-width: 3.2rem; font-size: .5rem; line-height: .58rem; text-align: center;
        }
        .rh-family-capabilities {
            min-height: 1.5rem; display: flex; flex-wrap: wrap;
            justify-content: flex-start; align-content: center; gap: .26rem .34rem;
            overflow: visible; margin: .28rem 0 0;
        }
        .rh-extension-tags {
            min-height: 3.15rem; max-height: 3.15rem; display: flex; flex-wrap: wrap;
            align-content: flex-start; gap: .34rem .42rem; overflow: hidden; margin: 0;
        }
        .rh-family-capability {
            display: inline-flex; align-items: center; max-width: 100%; min-height: 1.22rem;
            box-sizing: border-box; padding: .06rem .46rem; border-radius: 999px;
            color: var(--rh-text-secondary, #5c6370);
            background: color-mix(in srgb, var(--rh-text-secondary, #5c6370) 6%, var(--rh-card, #fff));
            border: 1px solid color-mix(in srgb, var(--rh-text-secondary, #5c6370) 10%, var(--rh-card, #fff));
            font-size: .7rem; line-height: .98rem; white-space: nowrap;
            overflow: hidden; text-overflow: ellipsis; opacity: 1;
        }
        .rh-extension-tag {
            display: inline-flex; align-items: center; max-width: 100%; min-height: 1.34rem;
            box-sizing: border-box; padding: .1rem .58rem; border-radius: 999px;
            background: color-mix(in srgb, currentColor 12%, transparent);
            border: 1px solid color-mix(in srgb, currentColor 4%, transparent);
            font-size: .76rem; line-height: 1.05rem; white-space: nowrap;
            overflow: hidden; text-overflow: ellipsis; opacity: .92;
        }
        .rh-family-capability-more, .rh-extension-tag-more { opacity: .68; cursor: help; }
        [class*="st-key-rh-plugin-family-action-"] {
            display: flex; justify-content: flex-end; align-items: center;
            min-height: 3.15rem; margin-top: 0;
        }
        [class*="st-key-rh-plugin-family-action-"] > div { width: auto; }
        [class*="st-key-rh-plugin-family-action-"] button {
            width: auto !important; min-width: 4.8rem !important; min-height: 2.05rem !important;
            padding: .2rem .78rem !important; border-radius: 999px !important; font-size: .76rem !important;
        }
        .rh-family-row-title {
            display:flex; align-items:center; flex-wrap:wrap; gap:.38rem; min-width:0;
        }
        .rh-family-row-title .rh-status-dot { flex:0 0 auto; }
        .rh-family-row-meta {
            margin:.14rem 0 .18rem; font-size:.78rem;
            color:var(--rh-text-secondary,#5c6370);
        }
        .rh-plugin-status-bubbles {
            display:inline-flex; flex-wrap:wrap; align-items:center; gap:.24rem;
            margin:0 0 0 .08rem;
        }
        .rh-plugin-status-bubble {
            display:inline-flex; align-items:center; min-height:1.18rem;
            padding:.04rem .42rem; border-radius:999px; font-size:.66rem;
            font-weight:700; line-height:1rem;
            color:var(--rh-text-secondary,#5c6370);
            background:var(--rh-card-2,rgba(127,127,127,.08));
            border:1px solid var(--rh-border,rgba(127,127,127,.18));
        }
        .rh-plugin-status-bubble.enabled {
            color:var(--rh-success,#1f8f59);
            border-color:color-mix(in srgb,var(--rh-success,#1f8f59) 38%,transparent);
            background:color-mix(in srgb,var(--rh-success,#1f8f59) 10%,transparent);
        }
        .rh-plugin-status-bubble.invalid {
            color:var(--rh-danger,#c94a4a);
            border-color:color-mix(in srgb,var(--rh-danger,#c94a4a) 38%,transparent);
            background:color-mix(in srgb,var(--rh-danger,#c94a4a) 10%,transparent);
        }
        .rh-plugin-status-bubble.needs-revalidation,
        .rh-plugin-status-bubble.needs-rank-verification,
        .rh-plugin-status-bubble.experimental {
            color:var(--rh-warning,#b7791f);
            border-color:color-mix(in srgb,var(--rh-warning,#b7791f) 38%,transparent);
            background:color-mix(in srgb,var(--rh-warning,#b7791f) 10%,transparent);
        }
        .rh-family-row-rank {
            min-height:3.7rem; display:flex; flex-direction:column;
            align-items:center; justify-content:center; text-align:center;
            border-left:1px solid var(--rh-border,rgba(127,127,127,.18));
            padding-left:.5rem;
        }
        .rh-family-row-rank .label {
            font-size:.54rem; line-height:.65rem; font-weight:800;
            letter-spacing:.035em; color:var(--rh-text-secondary,#5c6370);
        }
        .rh-family-row-rank .value {
            margin-top:.12rem; font-size:1.28rem; line-height:1.1; font-weight:800;
        }
        .rh-family-row-rank .note {
            max-width:7rem; margin-top:.12rem; font-size:.56rem; line-height:.7rem;
            color:var(--rh-warning,#b7791f);
        }
        </style>
        """
    )


_PROVENANCE_KEYS = (
    "provenance", "citation", "citations", "reference", "references",
    "source", "source_url", "sources", "url", "doi", "paper",
    "publication", "author", "authors", "year", "theorem",
    "theorem_reference", "published_rank", "notes",
)


def _nonempty(value):
    return value not in (None, "", [], {}, ())


def _provenance_fields(manifest):
    manifest = manifest or {}
    return {key: manifest[key] for key in _PROVENANCE_KEYS if key in manifest and _nonempty(manifest[key])}


def _plugin_provenance(plugin):
    fields = _provenance_fields(plugin.manifest)
    return {"Plugin": fields} if fields else {}


def _family_provenance(plugin):
    payload = _plugin_provenance(plugin)
    variants = {}
    for variant in plugin.variants:
        fields = _provenance_fields(variant.manifest)
        if fields:
            variants[variant.name] = fields
    if variants:
        payload["Families"] = variants
    return payload


def _pretty_provenance_key(key):
    return str(key).replace("_", " ").strip().title()


def _provenance_summary(payload):
    if not payload:
        return "No provenance information is declared in this plugin manifest."
    parts = []

    def walk(value, path=()):
        if len(parts) >= 12:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, path + (_pretty_provenance_key(key),))
            return
        if isinstance(value, (list, tuple, set)):
            for child in value:
                walk(child, path)
            return
        text = str(value).strip()
        if text:
            prefix = " · ".join(path[-2:])
            parts.append(f"{prefix}: {text}" if prefix else text)

    walk(payload)
    return ("\n".join(parts) or "Provenance metadata available.")[:1200]


def _chips(values, css_class, *, show=4, collapse_after=5):
    values = [str(x).strip() for x in values if str(x).strip()]
    if len(values) <= collapse_after:
        shown, hidden = values, []
    else:
        shown, hidden = values[:show], values[show:]
    chunks = [
        f'<span class="{css_class}" title="{html.escape(v, quote=True)}">{html.escape(v)}</span>'
        for v in shown
    ]
    if hidden:
        chunks.append(
            f'<span class="{css_class} {css_class}-more" title="{html.escape(" · ".join(hidden), quote=True)}">'
            f'+{len(hidden)} more</span>'
        )
    return "".join(chunks)


def _family_capabilities(plugin):
    values = sorted(str(x) for x in plugin.capabilities if str(x).strip()) or ["family definition"]
    st.html(
        f'<div class="rh-family-capabilities">'
        f'{_chips(values, "rh-family-capability", show=len(values), collapse_after=len(values))}</div>'
    )


def _extension_tags(values):
    st.html(f'<div class="rh-extension-tags">{_chips(values, "rh-extension-tag")}</div>')


def _toggle(db, ctx, plugin):
    _state, enabled, _status = _plugin_status(db, plugin)
    freshness = validation_freshness(db, plugin)
    if is_archived(db, plugin):
        if button("Restore", semantic=f"plugin-restore-{plugin.id}", type="primary", width="stretch"):
            restore_plugin(db, plugin)
            st.rerun()
        return
    if enabled:
        if button("Disable", semantic=f"plugin-disable-{plugin.id}", width="stretch"):
            set_plugin_state(db, plugin.id, enabled=False, status="disabled")
            st.rerun()
    else:
        if button("Activate", semantic=f"plugin-activate-{plugin.id}", type="primary", width="stretch"):
            _activate(db, ctx, plugin)
    if freshness["state"] == "needs_revalidation":
        if button("Revalidate", semantic=f"plugin-revalidate-{plugin.id}", width="stretch"):
            _activate(db, ctx, plugin)
    if button("Archive", semantic=f"plugin-archive-{plugin.id}", width="stretch"):
        archive_plugin(db, plugin)
        st.rerun()


def _family_toggle(db, ctx, plugin):
    _state, enabled, _status = _plugin_status(db, plugin)
    freshness = validation_freshness(db, plugin)
    if is_archived(db, plugin):
        if button("Restore", semantic=f"plugin-restore-{plugin.id}", type="primary", width="stretch"):
            restore_plugin(db, plugin)
            st.rerun()
        return
    if enabled:
        if button("Disable", semantic=f"plugin-disable-{plugin.id}", width="stretch"):
            set_plugin_state(db, plugin.id, enabled=False, status="disabled")
            st.rerun()
    else:
        if button("Enable", semantic=f"plugin-activate-{plugin.id}", type="primary", width="stretch"):
            _activate(db, ctx, plugin)
    if freshness["state"] == "needs_revalidation":
        if button("Revalidate", semantic=f"plugin-revalidate-{plugin.id}", width="stretch"):
            _activate(db, ctx, plugin)
    if button("Archive", semantic=f"plugin-archive-{plugin.id}", width="stretch"):
        archive_plugin(db, plugin)
        st.rerun()


def _family_rank_display(plugin):
    claim = family_rank_claim(plugin)
    if claim["effective_lower"] is not None:
        return claim, "GENERIC RANK", f"≥{claim['effective_lower']}"
    if claim["historical_lower"] is not None:
        return claim, "HISTORICAL CLAIM", f"≥{claim['historical_lower']}"
    return claim, "GENERIC RANK", "—"


def _plugin_status_bubbles_html(db, plugin, *, superseded=False):
    tags = _family_status_tags(db, plugin, superseded=superseded)
    chunks = []
    for tag in tags:
        slug = re.sub(r"[^a-z0-9]+", "-", str(tag).lower()).strip("-")
        chunks.append(
            f'<span class="rh-plugin-status-bubble {slug}">'
            f'{html.escape(str(tag))}</span>'
        )
    return '<span class="rh-plugin-status-bubbles">' + "".join(chunks) + "</span>"


def _family_row_actions(db, ctx, plugin):
    _state, enabled, _status = _plugin_status(db, plugin)
    freshness = validation_freshness(db, plugin)
    archived = is_archived(db, plugin)

    primary_col, details_col, more_col = st.columns(
        [1.1, 0.95, 0.38],
        gap="small",
        vertical_alignment="center",
    )
    with primary_col:
        if archived:
            if button(
                "Restore",
                semantic=f"plugin-family-row-restore-{plugin.id}",
                type="primary",
                width="stretch",
                icon=":material/settings_backup_restore:",
            ):
                restore_plugin(db, plugin)
                st.rerun()
        elif enabled:
            if button(
                "Disable",
                semantic=f"plugin-family-row-disable-{plugin.id}",
                width="stretch",
                icon=":material/toggle_off:",
            ):
                set_plugin_state(db, plugin.id, enabled=False, status="disabled")
                st.rerun()
        else:
            if button(
                "Enable",
                semantic=f"plugin-family-row-activate-{plugin.id}",
                type="primary",
                width="stretch",
                icon=":material/toggle_on:",
            ):
                _activate(db, ctx, plugin)

    with details_col:
        if button(
            "About",
            semantic=f"plugin-family-details-{plugin.id}",
            width="stretch",
            icon=":material/info:",
        ):
            st.session_state["plugin-family-manager-selected"] = plugin.id
            st.session_state["plugins_tab"] = "Families"
            st.rerun()

    with more_col:
        if archived:
            return
        with st.popover(
            "⋯",
            help=f"More actions for {plugin.name}",
            key=f"plugin-family-more-{plugin.id}",
            type="tertiary",
            width="content",
        ):
            if freshness["state"] == "needs_revalidation":
                if button(
                    "Revalidate",
                    semantic=f"plugin-family-row-revalidate-{plugin.id}",
                    width="stretch",
                    icon=":material/verified:",
                ):
                    _activate(db, ctx, plugin)
            if button(
                "Archive",
                semantic=f"plugin-family-row-archive-{plugin.id}",
                width="stretch",
                icon=":material/archive:",
            ):
                archive_plugin(db, plugin)
                st.rerun()


def _family_manager_row(db, ctx, plugin, *, superseded=False):
    _state, enabled, _status = _plugin_status(db, plugin)
    claim, rank_box_label, rank_value = _family_rank_display(plugin)
    family_count = len(plugin.variants)
    family_word = "family" if family_count == 1 else "families"
    dot_class = "on" if enabled else "off"
    provenance_tip = html.escape(
        _provenance_summary(_family_provenance(plugin)),
        quote=True,
    )

    with region(f"plugin-family-manager-row-{plugin.id}", border=True):
        info_col, rank_col, action_col = st.columns(
            [4.8, 1.05, 2.35],
            gap="medium",
            vertical_alignment="center",
        )
        with info_col:
            st.markdown(
                f'<div class="rh-family-row-title">'
                f'<span class="rh-status-dot {dot_class}"></span>'
                f'{_plugin_icon_html(plugin)}'
                f'<span class="rh-family-title-text">{html.escape(plugin.name)}</span>'
                f'{_experimental_icon_html(plugin)}'
                f'{_plugin_status_bubbles_html(db, plugin, superseded=superseded)}'
                f'</div>'
                f'<div class="rh-family-row-meta">'
                f'<strong>v{html.escape(plugin.version)}</strong> · '
                f'{family_count} {family_word}'
                f'<span class="rh-meta-provenance" title="{provenance_tip}" '
                f'aria-label="Plugin provenance">&#9432;</span></div>',
                unsafe_allow_html=True,
            )
            _family_capabilities(plugin)
        with rank_col:
            verification_note = ""
            if (
                claim["historical_lower"] is not None
                and claim["effective_lower"] is None
            ):
                verification_note = (
                    claim["status"] or "Rank Hunter verification pending"
                )
            st.html(
                f'<div class="rh-family-row-rank" '
                f'title="{html.escape(claim["label"], quote=True)}">'
                f'<span class="label">{html.escape(rank_box_label)}</span>'
                f'<span class="value">{html.escape(rank_value)}</span>'
                + (
                    f'<span class="note">{html.escape(verification_note)}</span>'
                    if verification_note
                    else ""
                )
                + '</div>'
            )
        with action_col:
            _family_row_actions(db, ctx, plugin)


def _family_card(db, ctx, plugin):
    _state, enabled, _status = _plugin_status(db, plugin)
    claim, rank_box_label, rank_value = _family_rank_display(plugin)
    family_count = len(plugin.variants)
    dot_class = "on" if enabled else "off"
    verification_icon = ""
    if claim["historical_lower"] is not None and claim["effective_lower"] is None:
        pending_status = claim["status"] or "Rank Hunter verification pending"
        verification_icon = (
            f'<span class="rh-rank-verification" title="{html.escape(pending_status, quote=True)}" '
            f'aria-label="{html.escape(pending_status, quote=True)}">&#9888;</span>'
        )
    provenance_tip = html.escape(_provenance_summary(_family_provenance(plugin)), quote=True)

    with region(f"plugin-family-{plugin.id}", border=True):
        family_word = "family" if family_count == 1 else "families"
        st.markdown(
            f'<div class="rh-family-header-row"><div class="rh-family-heading">'
            f'<div class="rh-plugin-name rh-family-title"><span class="rh-status-dot {dot_class}"></span>'
            f'{_plugin_icon_html(plugin)}<span class="rh-family-title-text">{html.escape(plugin.name)}</span>'
            f'{_experimental_icon_html(plugin)}</div>'
            f'<div class="rh-plugin-meta rh-family-meta"><strong>v{html.escape(plugin.version)}</strong> · '
            f'{family_count} {family_word}<span class="rh-meta-provenance" title="{provenance_tip}" '
            f'aria-label="Plugin provenance">&#9432;</span></div></div>'
            f'<div class="rh-rank-box" title="{html.escape(claim["label"], quote=True)}">{verification_icon}'
            f'<span class="label">{html.escape(rank_box_label)}</span><span class="value">{html.escape(rank_value)}</span>'
            f'</div></div>',
            unsafe_allow_html=True,
        )
        _family_description(plugin)
        _family_capabilities(plugin)


def _feature_card(db, ctx, plugin):
    _state, enabled, _status = _plugin_status(db, plugin)
    dot_class = "on" if enabled else "off"
    provenance_tip = html.escape(_provenance_summary(_plugin_provenance(plugin)), quote=True)
    with region(f"plugin-feature-{plugin.id}", border=True):
        st.markdown(
            f'<div class="rh-plugin-name"><span class="rh-status-dot {dot_class}"></span>'
            f'{_plugin_icon_html(plugin)}{html.escape(plugin.name)}</div>',
            unsafe_allow_html=True,
        )
        st.html(
            f'<div class="rh-plugin-meta rh-family-meta"><strong>v{html.escape(plugin.version)}</strong> · Feature'
            f'<span class="rh-meta-provenance" title="{provenance_tip}" aria-label="Plugin provenance">&#9432;</span></div>'
        )
        _extension_description(plugin)
        _extension_tags(list(plugin.feature_hooks))
        _toggle(db, ctx, plugin)


def _extension_card(db, ctx, plugin):
    _state, enabled, _status = _plugin_status(db, plugin)
    dot_class = "on" if enabled else "off"
    provenance_tip = html.escape(_provenance_summary(_plugin_provenance(plugin)), quote=True)
    page_count = len(plugin.extension_pages)
    page_word = "page" if page_count == 1 else "pages"
    with region(f"plugin-extension-{plugin.id}", border=True):
        st.markdown(
            f'<div class="rh-plugin-name"><span class="rh-status-dot {dot_class}"></span>'
            f'{_plugin_icon_html(plugin)}{html.escape(plugin.name)}</div>',
            unsafe_allow_html=True,
        )
        st.html(
            f'<div class="rh-plugin-meta rh-family-meta"><strong>v{html.escape(plugin.version)}</strong> · '
            f'Workspace · {page_count} {page_word}'
            f'<span class="rh-meta-provenance" title="{provenance_tip}" aria-label="Plugin provenance">&#9432;</span></div>'
        )
        _extension_description(plugin)
        _extension_tags([f"{p.section} → {p.label}" for p in plugin.extension_pages])
        _toggle(db, ctx, plugin)


def _grid(db, ctx, plugins, renderer, *, empty_message="None installed.", columns=2):
    if not plugins:
        st.info(empty_message)
        return
    cols = st.columns(columns, gap="medium")
    for i, plugin in enumerate(plugins):
        with cols[i % columns]:
            renderer(db, ctx, plugin)


def _family_sort_key(plugin):
    claim = family_rank_claim(plugin)
    rank = claim["effective_lower"] if claim["effective_lower"] is not None else claim["historical_lower"]
    return (-(rank if rank is not None else -1), plugin.name.lower())


def _superseded_plugin_ids(records):
    hidden = set()
    for plugin in records:
        if isinstance(plugin, Plugin):
            hidden.update(str(value) for value in (plugin.manifest.get("supersedes_plugin_ids") or ()))
    return hidden


def _family_status_tags(db, plugin, *, superseded=False):
    _state, enabled, status = _plugin_status(db, plugin)
    tags = []
    if superseded:
        tags.append("Superseded")
    if status == "archived":
        tags.append("Archived")
    elif status == "invalid":
        tags.append("Invalid")
    else:
        tags.append("Enabled" if enabled else "Disabled")
        if validation_freshness(db, plugin)["state"] == "needs_revalidation":
            tags.append("Needs revalidation")
    claim = family_rank_claim(plugin)
    if (
        claim["historical_lower"] is not None
        and claim["effective_lower"] is None
    ):
        tags.append("Needs rank verification")
    if (plugin.manifest or {}).get("experimental") is True:
        tags.append("Experimental")
    return tuple(dict.fromkeys(tags))


def _family_status_text(db, plugin, *, superseded=False):
    return " · ".join(_family_status_tags(db, plugin, superseded=superseded))


def _stored_validation(state):
    if state is None:
        return {}
    try:
        payload = json.loads(state["validation_json"] or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _family_variant_rows(plugin):
    rows = []
    for variant in plugin.variants:
        claim = family_rank_claim(plugin, variant)
        presets = search_presets_for_variant(plugin, variant)
        libraries = corpora_for_variant(plugin, variant)
        rows.append({
            "Variant": variant.name,
            "ID": variant.id,
            "Family spec": variant.family_spec,
            "Rank claim": claim["label"],
            "Presets": len(presets),
            "Libraries": len(libraries),
        })
    return rows


def _family_preset_rows(plugin):
    rows = []
    for variant in plugin.variants:
        for preset in search_presets_for_variant(plugin, variant):
            surfaces = [
                label
                for key, label in (
                    ("candidate", "Candidates"),
                    ("family", "Family Search"),
                    ("target", "Target"),
                )
                if preset.get(key)
            ]
            rows.append({
                "Variant": variant.name,
                "Preset": str(preset.get("label") or preset.get("name") or preset.get("id") or "Preset"),
                "ID": str(preset.get("id") or ""),
                "Surfaces": " · ".join(surfaces) or "—",
            })
    return rows


def _family_library_rows(plugin):
    rows = []
    for variant in plugin.variants:
        for corpus, family_key in corpora_for_variant(plugin, variant):
            rows.append({
                "Library": corpus.name,
                "ID": corpus.id,
                "Variant": variant.name,
                "Family key": family_key,
                "Cache": corpus.cache_file,
            })
    return rows


def _family_detail(db, ctx, plugin, *, superseded=False):
    state = plugin_state(db, plugin.id)
    freshness = validation_freshness(db, plugin)
    validation = _stored_validation(state)

    _family_card(db, ctx, plugin)
    _family_toggle(db, ctx, plugin)
    st.caption(
        f"Manager status: **{_family_status_text(db, plugin, superseded=superseded)}**"
    )

    if freshness["state"] == "needs_revalidation":
        changed = ", ".join(freshness["changed"]) or "installed content"
        st.warning(f"Needs revalidation: {changed} changed since the last successful validation.")
    elif freshness["state"] == "ready":
        st.success("Ready · installed content matches the last successful validation fingerprints.")
    elif freshness["state"] == "invalid":
        st.error("Latest activation/validation state is Invalid.")
    else:
        st.info("No successful validation fingerprint is stored yet.")

    variant_rows = _family_variant_rows(plugin)
    st.markdown("#### Variants")
    if variant_rows:
        st.dataframe(variant_rows, width="stretch", hide_index=True)
    else:
        st.caption("No Family variants declared.")

    preset_rows = _family_preset_rows(plugin)
    library_rows = _family_library_rows(plugin)

    with st.container(border=True):
        st.markdown("#### Research assets")
        st.markdown("**Capabilities**")
        _family_capabilities(plugin)
        st.markdown("**Research presets**")
        if preset_rows:
            st.dataframe(preset_rows, width="stretch", hide_index=True)
        else:
            st.caption("No named research presets declared.")
        st.markdown("**Libraries**")
        if library_rows:
            st.dataframe(library_rows, width="stretch", hide_index=True)
            for corpus in plugin.corpora:
                if corpus.description:
                    st.caption(f"**{corpus.name}:** {corpus.description}")
        else:
            st.caption("No plugin-owned research Libraries declared.")

    with st.container(border=True):
        st.markdown("#### Provenance & trust")
        provenance = _family_provenance(plugin)
        if provenance:
            st.json(provenance)
        else:
            st.caption("No provenance information is declared in this Family manifest.")
        st.warning(
            "Family Plugins may execute local Python code during validation and search. "
            "Plugin validation is a trust boundary, not a security sandbox."
        )
        st.caption(f"Source folder: `{plugin.root}`")
        if plugin.adapter_path:
            st.caption(f"Search adapter: `{plugin.adapter_path}`")
        st.caption(
            "Disable or Archive stops new work without deleting historical "
            "curves, points, evidence, pools, runs, or Jobs."
        )

    with st.container(border=True):
        st.markdown("#### Validation record")
        if validation:
            st.json(validation)
        else:
            st.caption("No stored validation record.")


def _extension_type_label(plugin):
    return "Feature" if plugin.plugin_type == "feature" else "Workspace"


def _extension_search_text(plugin):
    values = [
        plugin.name,
        plugin.id,
        plugin.version,
        plugin.plugin_type,
        str(plugin.root),
        " ".join(plugin.feature_hooks),
        " ".join(hook.name for hook in plugin.feature_command_hooks),
        " ".join(page.label for page in plugin.extension_pages),
        " ".join(page.section for page in plugin.extension_pages),
    ]
    return " ".join(values).lower()


def _extension_interface_rows(plugin):
    if plugin.plugin_type == "extension":
        return [
            {
                "Page": page.label,
                "ID": page.id,
                "Section": page.section,
                "Entrypoint": str(page.entrypoint),
            }
            for page in plugin.extension_pages
        ]

    rows = [
        {
            "Hook": hook_id,
            "Kind": "UI render hook",
            "Function": "render(context)",
            "Entrypoint": str(plugin.feature_entrypoint or ""),
        }
        for hook_id in plugin.feature_hooks
    ]
    rows.extend({
        "Hook": hook.name,
        "Kind": "Functional hook",
        "Function": hook.function,
        "Entrypoint": str(hook.entrypoint),
    } for hook in plugin.feature_command_hooks)
    return rows


def _extension_row_actions(db, ctx, plugin):
    _state, enabled, _status = _plugin_status(db, plugin)
    freshness = validation_freshness(db, plugin)
    archived = is_archived(db, plugin)

    primary_col, about_col, more_col = st.columns(
        [1.1, 0.95, 0.38],
        gap="small",
        vertical_alignment="center",
    )
    with primary_col:
        if archived:
            if button(
                "Restore",
                semantic=f"plugin-extension-row-restore-{plugin.id}",
                type="primary",
                width="stretch",
                icon=":material/settings_backup_restore:",
            ):
                restore_plugin(db, plugin)
                st.rerun()
        elif enabled:
            if button(
                "Disable",
                semantic=f"plugin-extension-row-disable-{plugin.id}",
                width="stretch",
                icon=":material/toggle_off:",
            ):
                set_plugin_state(db, plugin.id, enabled=False, status="disabled")
                st.rerun()
        else:
            if button(
                "Enable",
                semantic=f"plugin-extension-row-activate-{plugin.id}",
                type="primary",
                width="stretch",
                icon=":material/toggle_on:",
            ):
                _activate(db, ctx, plugin)

    with about_col:
        if button(
            "About",
            semantic=f"plugin-extension-details-{plugin.id}",
            width="stretch",
            icon=":material/info:",
        ):
            st.session_state["plugin-extension-manager-selected"] = plugin.id
            st.session_state["plugins_tab"] = "Extensions"
            st.rerun()

    with more_col:
        if archived:
            return
        with st.popover(
            "⋯",
            help=f"More actions for {plugin.name}",
            key=f"plugin-extension-more-{plugin.id}",
            type="tertiary",
            width="content",
        ):
            if freshness["state"] == "needs_revalidation":
                if button(
                    "Revalidate",
                    semantic=f"plugin-extension-row-revalidate-{plugin.id}",
                    width="stretch",
                    icon=":material/verified:",
                ):
                    _activate(db, ctx, plugin)
            if button(
                "Archive",
                semantic=f"plugin-extension-row-archive-{plugin.id}",
                width="stretch",
                icon=":material/archive:",
            ):
                archive_plugin(db, plugin)
                st.rerun()


def _extension_manager_row(db, ctx, plugin, *, superseded=False):
    _state, enabled, _status = _plugin_status(db, plugin)
    dot_class = "on" if enabled else "off"
    provenance_tip = html.escape(
        _provenance_summary(_plugin_provenance(plugin)),
        quote=True,
    )

    if plugin.plugin_type == "feature":
        meta = f"v{plugin.version} · Feature"
    else:
        page_count = len(plugin.extension_pages)
        page_word = "page" if page_count == 1 else "pages"
        meta = f"v{plugin.version} · Workspace · {page_count} {page_word}"

    with region(f"plugin-extension-manager-row-{plugin.id}", border=True):
        info_col, action_col = st.columns(
            [5.8, 2.2],
            gap="medium",
            vertical_alignment="center",
        )
        with info_col:
            st.markdown(
                f'<div class="rh-family-row-title">'
                f'<span class="rh-status-dot {dot_class}"></span>'
                f'{_plugin_icon_html(plugin)}'
                f'<span class="rh-family-title-text">{html.escape(plugin.name)}</span>'
                f'{_experimental_icon_html(plugin)}'
                f'{_plugin_status_bubbles_html(db, plugin, superseded=superseded)}'
                f'</div>'
                f'<div class="rh-family-row-meta"><strong>{html.escape(meta)}</strong>'
                f'<span class="rh-meta-provenance" title="{provenance_tip}" '
                f'aria-label="Plugin provenance">&#9432;</span></div>',
                unsafe_allow_html=True,
            )
        with action_col:
            _extension_row_actions(db, ctx, plugin)


def _extension_detail(db, ctx, plugin, *, superseded=False):
    state = plugin_state(db, plugin.id)
    freshness = validation_freshness(db, plugin)
    validation = _stored_validation(state)

    if plugin.plugin_type == "feature":
        _feature_card(db, ctx, plugin)
    else:
        _extension_card(db, ctx, plugin)

    st.caption(
        f"Manager status: **{_family_status_text(db, plugin, superseded=superseded)}** · "
        f"Type: **{_extension_type_label(plugin)}**"
    )

    if freshness["state"] == "needs_revalidation":
        changed = ", ".join(freshness["changed"]) or "installed content"
        st.warning(f"Needs revalidation: {changed} changed since the last successful validation.")
    elif freshness["state"] == "ready":
        st.success("Ready · installed content matches the last successful validation fingerprints.")
    elif freshness["state"] == "invalid":
        st.error("Latest activation/validation state is Invalid.")
    else:
        st.info("No successful validation fingerprint is stored yet.")

    interface_rows = _extension_interface_rows(plugin)
    interface_label = "Hooks" if plugin.plugin_type == "feature" else "Pages"
    st.markdown(f"#### {interface_label}")
    if interface_rows:
        st.dataframe(interface_rows, width="stretch", hide_index=True)
    else:
        st.caption(f"No {interface_label.lower()} declared.")

    with st.container(border=True):
        st.markdown("#### Provenance & trust")
        provenance = _plugin_provenance(plugin)
        if provenance:
            st.json(provenance)
        else:
            st.caption("No provenance information is declared in this Plugin manifest.")
        st.warning(
            "Extensions and Features execute local Python code. Plugin execution is "
            "validated and failure-isolated, but it is not a security sandbox."
        )
        st.caption(f"Source folder: `{plugin.root}`")
        if plugin.plugin_type == "extension":
            sections = sorted({page.section for page in plugin.extension_pages})
            st.caption("Allowed navigation sections: " + (", ".join(sections) or "—"))
        else:
            st.caption(
                "System privileges: "
                + (", ".join(sorted(plugin.system_privileges)) or "none")
            )

    with st.container(border=True):
        st.markdown("#### Validation record")
        if validation:
            st.json(validation)
        else:
            st.caption("No stored validation record.")


def _render_unavailable_plugins(records, label, *, expanded=True):
    records = list(records or ())
    if not records:
        return
    with st.expander(
        f"Unavailable historical {label} · {len(records)}",
        expanded=bool(expanded),
    ):
        st.warning(
            "These Plugin packages are no longer discoverable on disk. Their last stored identity, validation fingerprints, and provenance remain available for historical interpretation, but they are not eligible for new work."
        )
        st.dataframe(
            [
                {
                    "Plugin": rec["plugin_name"],
                    "ID": rec["plugin_id"],
                    "Version": rec["plugin_version"] or "—",
                    "Type": rec["plugin_type"],
                    "Previous state": rec["previous_status"] or "—",
                    "Source": rec["source_path"] or "—",
                }
                for rec in records
            ],
            width="stretch",
            hide_index=True,
        )
        for rec in records:
            with st.expander(
                f"{rec['plugin_name']} · {rec['plugin_id']}",
                expanded=False,
            ):
                st.caption(
                    "Status: **Unavailable** · "
                    f"previous lifecycle state: **{rec['previous_status'] or 'unknown'}**"
                )
                if rec["source_path"]:
                    st.caption(f"Last validated source folder: `{rec['source_path']}`")
                if rec["fingerprints"]:
                    st.caption("Last stored validation fingerprints")
                    st.json(rec["fingerprints"])
                if rec["validation"]:
                    st.caption("Last stored validation record")
                    st.json(rec["validation"])
        st.caption(
            "Reinstall the matching Plugin package to restore executable code. "
            "Rank Hunter does not delete historical curves, points, evidence, pools, runs, or Jobs."
        )


def _render_family_detail_page(db, ctx, plugin, *, superseded=False):
    back_col, title_col = st.columns([1.35, 8.65], vertical_alignment="center")
    with back_col:
        if st.button(
            "All families",
            icon=":material/arrow_back:",
            width="stretch",
            key=f"plugin-family-back-{plugin.id}",
        ):
            st.session_state.pop("plugin-family-manager-selected", None)
            st.session_state["plugins_tab"] = "Families"
            st.rerun()
    with title_col:
        st.subheader(f"About · {plugin.name}")
        st.caption(
            f"v{plugin.version} · "
            f"{_family_status_text(db, plugin, superseded=superseded)}"
        )

    _family_detail(
        db,
        ctx,
        plugin,
        superseded=superseded,
    )


def _render_extension_detail_page(db, ctx, plugin, *, superseded=False):
    back_col, title_col = st.columns([1.35, 8.65], vertical_alignment="center")
    with back_col:
        if st.button(
            "All extensions",
            icon=":material/arrow_back:",
            width="stretch",
            key=f"plugin-extension-back-{plugin.id}",
        ):
            st.session_state.pop("plugin-extension-manager-selected", None)
            st.session_state["plugins_tab"] = "Extensions"
            st.rerun()
    with title_col:
        st.subheader(f"About · {plugin.name}")
        st.caption(
            f"{_extension_type_label(plugin)} · v{plugin.version} · "
            f"{_family_status_text(db, plugin, superseded=superseded)}"
        )

    _extension_detail(
        db,
        ctx,
        plugin,
        superseded=superseded,
    )


def page(db, ctx, initial_tab=None):
    _plugin_icon_styles()
    title(
        "Plugins",
        "Manage search Families and optional Extension workspaces.",
        icon="extension",
    )

    if initial_tab in {"Families", "Extensions"}:
        # Compatibility for preserved callers outside the canonical shell.
        st.session_state["plugins_tab"] = str(initial_tab)
        st.session_state["_rh-plugins-main-tabs_value"] = str(initial_tab)
        if initial_tab == "Extensions":
            st.session_state.pop("plugin-family-manager-selected", None)
        else:
            st.session_state.pop("plugin-extension-manager-selected", None)

    records = discover_plugins(ctx.project_root, include_superseded=True)
    valid = [p for p in records if isinstance(p, Plugin)]
    bad = [p for p in records if not isinstance(p, Plugin)]
    superseded_ids = _superseded_plugin_ids(valid)
    _family_layout_styles()

    selected_family_id = st.session_state.get("plugin-family-manager-selected")
    if (
        selected_family_id is not None
        and str(st.session_state.get("plugins_tab") or "Families") == "Families"
    ):
        selected_family = next(
            (
                plugin
                for plugin in valid
                if plugin.plugin_type == "family"
                and plugin.id == str(selected_family_id)
            ),
            None,
        )
        if selected_family is not None:
            _render_family_detail_page(
                db,
                ctx,
                selected_family,
                superseded=selected_family.id in superseded_ids,
            )
            return
        st.session_state.pop("plugin-family-manager-selected", None)

    selected_extension_id = st.session_state.get("plugin-extension-manager-selected")
    if (
        selected_extension_id is not None
        and str(st.session_state.get("plugins_tab") or "Families") == "Extensions"
    ):
        selected_extension = next(
            (
                plugin
                for plugin in valid
                if plugin.plugin_type in {"feature", "extension"}
                and plugin.id == str(selected_extension_id)
            ),
            None,
        )
        if selected_extension is not None:
            _render_extension_detail_page(
                db,
                ctx,
                selected_extension,
                superseded=selected_extension.id in superseded_ids,
            )
            return
        st.session_state.pop("plugin-extension-manager-selected", None)

    current = str(st.session_state.get("plugins_tab") or "Families")
    if current not in {"Families", "Extensions"}:
        current = "Families"
    choice = tabs(
        ["Families", "Extensions"],
        value=current,
        key="plugins-main-tabs",
        variant="line",
        width="content",
    )
    st.session_state["plugins_tab"] = choice
    st.html("<div style='height:.85rem'></div>")

    unavailable = unavailable_plugin_states(
        db,
        [plugin.id for plugin in valid],
    )
    unavailable_families = [
        rec for rec in unavailable
        if rec["plugin_type"] == "family"
    ]
    if choice == "Families":
        all_families = sorted(
            (p for p in valid if p.plugin_type == "family"),
            key=_family_sort_key,
        )
    else:
        all_features = sorted(
            (p for p in valid if p.plugin_type == "feature"),
            key=lambda p: p.name.lower(),
        )
        all_extensions = sorted(
            (p for p in valid if p.plugin_type == "extension"),
            key=lambda p: p.name.lower(),
        )

    for rec in bad:
        st.error(f"Invalid plugin at `{rec['path']}`: {rec['error']}")
    if not valid and not unavailable:
        st.info("No plugins installed in `plugins/`.")
        return

    if choice == "Families":
        families = all_families
        family_capabilities = sorted({
            str(capability)
            for plugin in all_families
            for capability in plugin.capabilities
            if str(capability).strip()
        })
        search_col, status_col, capability_col, sort_col = st.columns(
            [2.3, 1.15, 1.35, 0.9],
            gap="small",
            vertical_alignment="center",
        )
        with search_col:
            family_query = st.text_input(
                "Search Families",
                key="plugin-family-manager-search",
                placeholder="Search name, id, variant, capability…",
                label_visibility="collapsed",
            ).strip().lower()
        with status_col:
            family_status_filter = st.selectbox(
                "Status",
                [
                    "All statuses",
                    "Enabled",
                    "Disabled",
                    "Archived",
                    "Invalid",
                    "Needs revalidation",
                    "Needs rank verification",
                    "Unavailable",
                    "Experimental",
                    "Superseded",
                ],
                key="plugin-family-manager-status",
                label_visibility="collapsed",
            )
        with capability_col:
            family_capability_filter = st.selectbox(
                "Capability",
                ["All capabilities", *family_capabilities],
                key="plugin-family-manager-capability",
                format_func=lambda value: (
                    value
                    if value == "All capabilities"
                    else str(value).replace("_", " ").title()
                ),
                label_visibility="collapsed",
            )
        with sort_col:
            family_sort = st.selectbox(
                "Sort",
                ["Rank", "Name", "Status", "Version"],
                key="plugin-family-manager-sort",
                label_visibility="collapsed",
            )
        if family_status_filter == "Unavailable":
            rows = unavailable_families
            if family_query:
                rows = [
                    rec for rec in rows
                    if family_query in " ".join([
                        rec["plugin_name"],
                        rec["plugin_id"],
                        rec["plugin_version"],
                        rec["plugin_type"],
                        str(rec["source_path"] or ""),
                    ]).lower()
                ]
            if rows:
                _render_unavailable_plugins(rows, "Families")
            else:
                st.info("No unavailable Families match the current manager filters.")
            return

        if family_query:
            families = [
                plugin
                for plugin in families
                if family_query in " ".join([
                    plugin.name,
                    plugin.id,
                    plugin.version,
                    " ".join(plugin.capabilities),
                    " ".join(v.name for v in plugin.variants),
                    " ".join(v.id for v in plugin.variants),
                ]).lower()
            ]

        if family_status_filter != "All statuses":
            families = [
                plugin
                for plugin in families
                if family_status_filter in _family_status_tags(
                    db,
                    plugin,
                    superseded=plugin.id in superseded_ids,
                )
            ]

        if family_capability_filter != "All capabilities":
            families = [
                plugin
                for plugin in families
                if family_capability_filter in {
                    str(value) for value in plugin.capabilities
                }
            ]

        if family_sort == "Name":
            families = sorted(families, key=lambda p: p.name.lower())
        elif family_sort == "Status":
            families = sorted(
                families,
                key=lambda p: (
                    _family_status_text(db, p, superseded=p.id in superseded_ids).lower(),
                    p.name.lower(),
                ),
            )
        elif family_sort == "Version":
            families = sorted(families, key=lambda p: (p.version.lower(), p.name.lower()))
        else:
            families = sorted(families, key=_family_sort_key)

        if not families:
            if (
                family_status_filter == "All statuses"
                and unavailable_families
            ):
                _render_unavailable_plugins(
                    unavailable_families,
                    "Families",
                )
            else:
                st.info("No Families match the current manager filters.")
            return

        st.caption(
            f"Showing **{len(families)}** of **{len(all_families)}** installed Families"
        )
        for plugin in families:
            _family_manager_row(
                db,
                ctx,
                plugin,
                superseded=plugin.id in superseded_ids,
            )
        if (
            family_status_filter == "All statuses"
            and unavailable_families
        ):
            _render_unavailable_plugins(
                unavailable_families,
                "Families",
            )
        return

    extension_plugins = [*all_features, *all_extensions]

    search_col, status_col, sort_col = st.columns(
        [2.4, 1.15, 0.9],
        gap="small",
        vertical_alignment="center",
    )
    with search_col:
        extension_query = st.text_input(
            "Search Extensions",
            key="plugin-extension-manager-search",
            placeholder="Search name, id, page, hook, section…",
            label_visibility="collapsed",
        ).strip().lower()
    with status_col:
        extension_status_filter = st.selectbox(
            "Status",
            [
                "All statuses",
                "Enabled",
                "Disabled",
                "Archived",
                "Invalid",
                "Needs revalidation",
                "Experimental",
                "Superseded",
            ],
            key="plugin-extension-manager-status",
            label_visibility="collapsed",
        )
    with sort_col:
        extension_sort = st.selectbox(
            "Sort",
            ["Name", "Type", "Status", "Version"],
            key="plugin-extension-manager-sort",
            label_visibility="collapsed",
        )
    if extension_query:
        extension_plugins = [
            plugin for plugin in extension_plugins
            if extension_query in _extension_search_text(plugin)
        ]

    if extension_status_filter != "All statuses":
        extension_plugins = [
            plugin
            for plugin in extension_plugins
            if extension_status_filter in _family_status_tags(
                db,
                plugin,
                superseded=plugin.id in superseded_ids,
            )
        ]

    if extension_sort == "Type":
        extension_plugins = sorted(
            extension_plugins,
            key=lambda p: (_extension_type_label(p).lower(), p.name.lower()),
        )
    elif extension_sort == "Status":
        extension_plugins = sorted(
            extension_plugins,
            key=lambda p: (
                _family_status_text(db, p, superseded=p.id in superseded_ids).lower(),
                p.name.lower(),
            ),
        )
    elif extension_sort == "Version":
        extension_plugins = sorted(
            extension_plugins,
            key=lambda p: (p.version.lower(), p.name.lower()),
        )
    else:
        extension_plugins = sorted(extension_plugins, key=lambda p: p.name.lower())

    if not extension_plugins:
        st.info("No Extensions match the current manager filters.")
        return

    feature_plugins = [
        plugin for plugin in extension_plugins
        if plugin.plugin_type == "feature"
    ]
    workspace_plugins = [
        plugin for plugin in extension_plugins
        if plugin.plugin_type == "extension"
    ]

    st.markdown("### Features")
    st.caption(
        "Functional and UI hooks that extend Rank Hunter without adding a workspace page."
    )
    if feature_plugins:
        for plugin in feature_plugins:
            _extension_manager_row(
                db,
                ctx,
                plugin,
                superseded=plugin.id in superseded_ids,
            )
    else:
        st.caption("No Features match the current manager filters.")

    st.markdown("### Workspaces")
    st.caption(
        "Plugin-owned workspace pages that appear in Rank Hunter navigation."
    )
    if workspace_plugins:
        for plugin in workspace_plugins:
            _extension_manager_row(
                db,
                ctx,
                plugin,
                superseded=plugin.id in superseded_ids,
            )
    else:
        st.caption("No Workspaces match the current manager filters.")
