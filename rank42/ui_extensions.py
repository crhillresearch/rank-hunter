"""Optional plugin workspace host for Rank Hunter.

Full Extensions own their workspace body, while Rank Hunter core owns the page
header and navigation shell so plugin pages render consistently with core pages.
Feature plugins are hosted separately through :mod:`rank42.feature_hooks` and
never create navigation entries.
"""
from __future__ import annotations

import streamlit as st

from rank42.plugins import (
    EXTENSION_MENU_SECTIONS,
    ExtensionPage,
    Plugin,
    discover_plugins,
    is_enabled,
    load_extension,
)
from rank42.plugin_navigation import canonical_extension_section
from rank42.plugin_api import ExtensionContextV1

# Backward-compatible import alias for pre-SDK callers.
ExtensionContext = ExtensionContextV1


def enabled_extension_pages(db, project_root):
    rows = []
    for plugin in discover_plugins(project_root):
        if not isinstance(plugin, Plugin) or plugin.plugin_type != "extension" or not is_enabled(db, plugin):
            continue
        for page in plugin.extension_pages:
            section = canonical_extension_section(page.section)
            if section not in EXTENSION_MENU_SECTIONS:
                continue
            normalized_page = (
                page
                if section == page.section
                else ExtensionPage(page.id, page.label, section, page.entrypoint, page.icon)
            )
            route = f"extension:{plugin.id}:{page.id}"
            rows.append((route, plugin, normalized_page))
    return rows


def _legacy_extension_title(page: ExtensionPage, plugin: Plugin):
    """Render the modern extension header when an older common.title is present."""
    from rank42.ui_components import rh_key

    icon = plugin.icon
    symbol = str(icon).removeprefix(":material/").removesuffix(":") if icon else "analytics"
    with st.container(key=rh_key(f"page-header-{page.label}")):
        st.title(f":material/{symbol}: {page.label}", anchor=False)
        if plugin.description:
            st.caption(str(plugin.description))


def render_extension(db, ctx, plugin: Plugin, page: ExtensionPage):
    section = canonical_extension_section(page.section)
    if section not in EXTENSION_MENU_SECTIONS:
        raise ValueError(
            f"unsupported Rank Hunter extension section {page.section!r}; "
            "System and other core-owned surfaces are not Extension targets"
        )

    from rank42.ui_pages.common import title

    # Core owns the application-level header. Extension render(context) owns the
    # body only; this avoids parent-section titles leaking into plugin pages.
    # Page headers and navigation both use the resolved plugin icon. Plugin.icon
    # prefers top-level plugin.json metadata and falls back to menu.icon for
    # legacy manifests.
    try:
        title(page.label, plugin.description or None, icon=plugin.icon)
    except TypeError as exc:
        # Compatibility with preserved pre-icon UI snapshots. Argument binding
        # fails before the older title helper renders anything, so reproduce the
        # modern header here rather than silently reverting to its analytics icon.
        if "unexpected keyword argument 'icon'" not in str(exc):
            raise
        _legacy_extension_title(page, plugin)
    ###
    # A HUMAN WAS HERE :-)
    ###
    try:
        module = load_extension(plugin, page)
        shared = ExtensionContextV1(
            project_root=ctx.project_root,
            db_path=ctx.db_path,
            db=db,
            plugin_id=plugin.id,
            plugin_version=plugin.version,
            page_id=page.id,
            page_label=page.label,
        )
        module.render(shared)
    except Exception as exc:
        st.error(f"Extension `{plugin.name}` failed to render: {exc}")
        with st.expander("Extension details"):
            st.code(f"{page.entrypoint}\n{exc!r}", language="text")
