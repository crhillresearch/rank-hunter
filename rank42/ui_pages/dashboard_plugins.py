from __future__ import annotations

import html

import streamlit as st

from rank42.plugins import Plugin, discover_plugins, is_archived, is_enabled, plugin_state, validation_freshness
from rank42.ui_components import region
from .common import PAGE_ICONS


_TYPE_LABELS = {
    "family": ("Families", "Family"),
    "feature": ("Features", "Feature"),
    "extension": ("Workspaces", "Workspace"),
}
_TYPE_ORDER = {"family": 0, "feature": 1, "extension": 2}


def _go_to(page):
    st.session_state["rh_page"] = page


def _legacy_health_snapshot(db, ctx):
    records = discover_plugins(ctx.project_root)
    archived_ids = {
        str(row["plugin_id"])
        for row in db.execute(
            "SELECT plugin_id FROM plugin_states WHERE status='archived'"
        ).fetchall()
    }
    installed_plugins = [rec for rec in records if isinstance(rec, Plugin)]
    invalid_manifests = [
        rec
        for rec in records
        if not isinstance(rec, Plugin)
        and not (
            isinstance(rec, dict)
            and str(rec.get("id") or rec.get("plugin_id") or "") in archived_ids
        )
    ]
    plugins = [plugin for plugin in installed_plugins if not is_archived(db, plugin)]
    active = [plugin for plugin in plugins if is_enabled(db, plugin)]

    invalid_states = []
    needs_revalidation = []
    for plugin in plugins:
        state = plugin_state(db, plugin.id)
        if state is not None and str(state["status"] or "").lower() == "invalid":
            invalid_states.append(plugin)
            continue
        if validation_freshness(db, plugin).get("state") == "needs_revalidation":
            needs_revalidation.append(plugin)

    counts = {}
    for plugin_type in _TYPE_LABELS:
        counts[plugin_type] = {
            "installed": sum(1 for plugin in plugins if plugin.plugin_type == plugin_type),
            "enabled": sum(1 for plugin in active if plugin.plugin_type == plugin_type),
        }
    preview = sorted(
        active,
        key=lambda plugin: (_TYPE_ORDER.get(plugin.plugin_type, 99), plugin.name.lower(), plugin.id),
    )[:3]
    return {
        "counts": counts,
        "installed": len(plugins),
        "enabled": len(active),
        "invalid_manifests": len(invalid_manifests),
        "invalid_states": len(invalid_states),
        "needs_revalidation": len(needs_revalidation),
        "attention": len(invalid_manifests) + len(invalid_states) + len(needs_revalidation),
        "active_preview": [
            {"id": plugin.id, "name": plugin.name, "type": plugin.plugin_type}
            for plugin in preview
        ],
    }


def render_plugins_panel(db, ctx, *, health=None):
    health = dict(health or _legacy_health_snapshot(db, ctx))
    counts = health["counts"]
    attention = int(health.get("attention") or 0)
    health_class = "warning" if attention else "ok"
    health_text = f"{attention} Need Attention" if attention else "Healthy"
    health_title = (
        f'{int(health.get("invalid_manifests") or 0)} invalid manifest(s) · '
        f'{int(health.get("invalid_states") or 0)} invalid plugin state(s) · '
        f'{int(health.get("needs_revalidation") or 0)} need revalidation'
        if attention
        else "No invalid manifests, invalid plugin states, or stale validations."
    )

    stats_html = "".join(
        f'<div><span>{html.escape(plural)}</span>'
        f'<strong>{int(counts.get(plugin_type, {}).get("enabled") or 0)}/'
        f'{int(counts.get(plugin_type, {}).get("installed") or 0)}</strong>'
        f'<small>active</small></div>'
        for plugin_type, (plural, _singular) in _TYPE_LABELS.items()
    )

    preview = list(health.get("active_preview") or [])
    active_rows = []
    for plugin in preview:
        _plural, singular = _TYPE_LABELS.get(str(plugin.get("type") or ""), ("Plugins", "Plugin"))
        active_rows.append(
            '<div class="rh-dashboard-plugin-row">'
            '<i></i>'
            f'<span>{html.escape(str(plugin.get("name") or plugin.get("id") or "Plugin"))}</span>'
            f'<small>{html.escape(singular)}</small>'
            '</div>'
        )
    if not active_rows:
        active_rows.append('<div class="rh-dashboard-plugin-empty">No active plugins.</div>')

    remainder = max(0, int(health.get("enabled") or 0) - len(preview))
    more_html = f'<div class="rh-dashboard-plugin-more">+{remainder} more active</div>' if remainder else ""

    with region("dashboard-plugins", border=True):
        st.html(
            '<div class="rh-dashboard-section-heading rh-dashboard-plugin-heading">'
            '<span class="rh-dashboard-section-title">PLUGINS</span>'
            f'<span class="rh-dashboard-plugin-health {health_class}" title="{html.escape(health_title, quote=True)}">'
            f'{html.escape(health_text)}</span></div>'
            f'<div class="rh-dashboard-plugin-stats">{stats_html}</div>'
            '<div class="rh-dashboard-plugin-active-label">ACTIVE PLUGINS</div>'
            f'<div class="rh-dashboard-plugin-list">{"".join(active_rows)}</div>'
            f'{more_html}'
            f'<div class="rh-dashboard-plugin-summary"><strong>{int(health.get("installed") or 0)}</strong> installed · '
            f'<strong>{int(health.get("enabled") or 0)}</strong> active</div>'
        )
        st.button(
            "Manage plugins",
            key="rh-dashboard-manage-plugins",
            width="stretch",
            icon=PAGE_ICONS["Plugins"],
            on_click=_go_to,
            args=("Plugin Families",),
        )
