"""Resolve one immutable Plugin Search configuration view.

This module is the core-owned bridge between declarative manifest Search
metadata, variant identity, and legacy FamilyAdapterV1 metadata fallback.
Search surfaces and Pipeline hosts use the same resolver so a variant cannot
see a different option/default universe at execution time than it saw in the UI.
"""
from __future__ import annotations

import hashlib
import json

from rank42.plugins import (
    get_variant,
    search_option_schema_for_variant,
    search_presets_for_variant,
)


def adapter_search_option_defs(plugin, variant=None, adapter=None, *, context):
    """Return normalized variant-aware adapter option definitions."""
    if plugin is None:
        return ()
    variant = variant or get_variant(plugin)
    return tuple(
        dict(rec)
        for rec in search_option_schema_for_variant(
            plugin,
            variant,
            adapter,
            context=str(context),
        )
    )


def adapter_option_defaults(defs):
    """Return executable defaults from one normalized option schema."""
    return {
        str(rec["key"]): rec.get("default")
        for rec in defs or ()
        if "default" in rec
    }


def pipeline_search_config(plugin, variant=None):
    """Return active Pipeline-native Search defaults for one exact variant."""
    if plugin is None:
        return {}
    variant = variant or get_variant(plugin)
    out = dict((getattr(plugin, "manifest", {}) or {}).get("pipeline_search") or {})
    out.update(
        dict((getattr(variant, "manifest", {}) or {}).get("pipeline_search") or {})
    )
    return out


def plugin_search_preset(plugin, variant=None, preset_id=None):
    if not preset_id:
        return None
    variant = variant or get_variant(plugin)
    wanted = str(preset_id)
    return next(
        (
            dict(rec)
            for rec in search_presets_for_variant(plugin, variant)
            if str(rec.get("id") or "") == wanted
        ),
        None,
    )


def resolve_plugin_search_config(
    plugin,
    variant=None,
    adapter=None,
    *,
    context,
    preset_id=None,
    overrides=None,
):
    """Resolve defaults, one manifest profile, and explicit values."""
    if plugin is None:
        raise ValueError("plugin search config requires a plugin")
    variant = variant or get_variant(plugin)
    context = str(context)
    if context not in {"family", "target"}:
        raise ValueError("plugin search config context must be family or target")

    defs = adapter_search_option_defs(
        plugin,
        variant,
        adapter,
        context=context,
    )
    known = {str(rec["key"]) for rec in defs}
    options = adapter_option_defaults(defs)
    controls = {}

    preset = plugin_search_preset(plugin, variant, preset_id)
    if preset is not None:
        section = dict(preset.get(context) or {})
        for key, value in section.items():
            if str(key) in known:
                options[str(key)] = value
            else:
                controls[str(key)] = value

    for key, value in dict(overrides or {}).items():
        if str(key) in known:
            options[str(key)] = value
        else:
            controls[str(key)] = value

    snapshot = {
        "plugin_id": str(plugin.id),
        "plugin_version": str(plugin.version),
        "variant_id": str(variant.id),
        "family_spec": str(variant.family_spec),
        "context": context,
        "preset_id": None if preset is None else str(preset.get("id") or ""),
        "options": dict(options),
        "controls": dict(controls),
    }
    encoded = json.dumps(
        snapshot,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    snapshot["config_hash"] = hashlib.sha256(encoded).hexdigest()
    snapshot["option_defs"] = tuple(dict(rec) for rec in defs)
    return snapshot


def search_preset_provenance(
    plugin,
    variant=None,
    *,
    context,
    preset_id,
    effective_settings,
    modified,
):
    """Freeze one applied Family-owned preset as launch provenance.

    Preset identity is operator provenance, not scientific truth.  The frozen
    effective settings record what actually launched after any user edits.
    """
    if plugin is None or not preset_id:
        return None
    variant = variant or get_variant(plugin)
    preset = plugin_search_preset(plugin, variant, preset_id)
    if preset is None:
        return None
    return {
        "plugin_id": str(plugin.id),
        "plugin_version": str(plugin.version),
        "variant_id": str(variant.id),
        "context": str(context),
        "preset_id": str(preset.get("id") or ""),
        "preset_label": str(preset.get("label") or preset.get("id") or ""),
        "modified": bool(modified),
        "effective_settings": dict(effective_settings or {}),
    }


__all__ = [
    "adapter_option_defaults",
    "adapter_search_option_defs",
    "pipeline_search_config",
    "plugin_search_preset",
    "resolve_plugin_search_config",
    "search_preset_provenance",
]
