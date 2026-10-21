"""Core-owned hook host for narrow Feature plugins.

Feature plugins are grouped with Extensions in the UI, but remain a distinct
manifest type so Rank Hunter can validate their hook targets and avoid treating
them as navigation pages.  Hooks are explicit, stable insertion points; a
Feature never monkey-patches a core page.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Any

import streamlit as st

from rank42.plugin_api import FeatureContextV1
from rank42.ui_components import region


from rank42.plugin_hooks import (
    ACCEPTED_FEATURE_HOOKS,
    FEATURE_HOOK_HOST_CONTRACTS,
    canonical_feature_hook,
    feature_hook_allowed,
)


# Backward-compatible import alias for pre-SDK callers.
FeatureContext = FeatureContextV1


def render_feature_hook(db, ctx, hook_id: str, **payload: Any) -> None:
    """Render enabled Feature contributions for one supported hook.

    Rendering is failure-isolated: a broken optional Feature cannot take down
    its host core page.  The hook host performs no scientific work itself.
    """
    requested_hook_id = str(hook_id)
    if requested_hook_id not in ACCEPTED_FEATURE_HOOKS:
        raise ValueError(f"unsupported Rank Hunter feature hook {requested_hook_id!r}")
    hook_id = canonical_feature_hook(requested_hook_id)

    # Local import avoids a registry -> hook-host import cycle during manifests.
    from rank42.plugins import Plugin, discover_plugins, is_enabled, load_feature

    host_contract = FEATURE_HOOK_HOST_CONTRACTS.get(hook_id)
    max_contributions = (
        None
        if host_contract is None
        else int(host_contract.get("max_contributions") or 0) or None
    )
    contributions_attempted = 0

    for plugin in discover_plugins(ctx.project_root):
        if not isinstance(plugin, Plugin):
            continue
        if plugin.plugin_type != "feature":
            continue
        plugin_hooks = {
            canonical_feature_hook(value)
            for value in plugin.feature_hooks
        }
        if hook_id not in plugin_hooks:
            continue
        if not feature_hook_allowed(
            plugin.id,
            hook_id,
            plugin.system_privileges,
        ):
            continue
        if not is_enabled(db, plugin):
            continue
        if max_contributions is not None and contributions_attempted >= max_contributions:
            break
        contributions_attempted += 1
        try:
            module = load_feature(plugin)
            hook_payload = dict(payload)
            contract = host_contract
            if contract is not None:
                hook_payload["host_contract"] = MappingProxyType(dict(contract))
            feature_ctx = FeatureContextV1(
                project_root=ctx.project_root,
                db_path=ctx.db_path,
                db=db,
                plugin_id=plugin.id,
                plugin_version=plugin.version,
                hook_id=hook_id,
                payload=MappingProxyType(hook_payload),
            )
            max_height = (
                "content"
                if contract is None
                else int(contract.get("max_height") or 180)
            )
            with region(
                f"feature-{plugin.id}-{hook_id}",
                height=max_height,
            ):
                module.render(feature_ctx)
        except Exception as exc:
            st.error(f"Feature `{plugin.name}` failed at `{hook_id}`: {exc}")
