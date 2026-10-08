"""Rank Hunter plugin registry.

Rank Hunter supports three deliberately bounded plugin manifest types:

* ``family`` (the legacy/default): mathematical search families and adapters.
* ``feature``: narrow contributions to explicit core-owned hook points.
* ``extension``: full, self-contained Streamlit workspaces registered as pages.

Features are presented under the Extensions surface in the UI; the separate
manifest type exists so hook targets can be validated and failure-isolated.
Missing ``plugin_type`` remains ``family`` for backward compatibility.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from functools import lru_cache
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from rank42.db import now
from rank42.family_charts import FamilyChart, qstr
from rank42.plugin_api import (
    EXTENSION_NAVIGATION_API_VERSION,
    FEATURE_HOOK_API_VERSION,
    FamilyAdapterV1,
    coerce_family_adapter_v1,
    family_adapter_search_options,
    validate_family_adapter_v1,
)
from rank42.plugin_navigation import (
    EXTENSION_MENU_SECTIONS,
    EXTENSION_SECTION_ALIASES,
    canonical_extension_section,
)
from rank42.torsion import canonical_torsion_label

CAPABILITIES = {
    "candidate_generation", "family_search", "target_search", "known_subgroup",
    "quartic_search", "pgl2_search", "free_search",
    "pipeline_transform", "constructive_family",
}
PLUGIN_TYPES = {"family", "feature", "extension"}
# Compatibility name retained for callers/tests that import the historical
# registry constant. The versioned authority lives in plugin_navigation.py.
MENU_SECTIONS = EXTENSION_MENU_SECTIONS
_MATERIAL_ICON_RE = re.compile(r"^:material/[a-z0-9_]+:$")

GENERIC_RANK_CLAIM_STATES = {
    "historical_record", "reconstructed_model", "sections_verified",
    "generic_lower_bound_verified", "exact_constant_curve_control",
    # Legacy plugin metadata: accepted for backward compatibility only.
    # This does not synthesize verified_generic_rank_lower or exact-rank evidence.
    "verified_exact",
}

SEARCH_PRESET_CANDIDATE_KEYS = {
    "a_min", "a_max", "b_min", "b_max", "stage_bounds", "stage_keeps",
    "top", "engine", "sample_count", "sample_seed",
}


AUTO_SEARCH_POLICY_KEYS = {
    "target_rank", "baseline_certificate_timeout",
    "integral_minimal_until_rank", "native_integral_until_rank",
    "pool_size", "deep_keep", "upper_timeout", "final_upper_timeout",
    "exact_candidates", "strategy_note", "direct_affine_enabled",
    "classical_covering_enabled", "classical_covering_engine",
    "classical_covering_min_rank", "classical_covering_top_fresh",
    "classical_covering_timeout", "classical_covering_lim1",
    "classical_covering_lim3", "classical_covering_first_limit",
    "classical_covering_second_limit", "classical_covering_n_aux",
    "pointed_quartic_enabled", "pointed_quartic_min_rank",
    "pointed_quartic_top_fresh", "pointed_quartic_anchors",
    "pointed_quartic_pool_size", "pointed_quartic_rounds",
    "pointed_quartic_deep_keep",
    "pointed_quartic_timeout", "pointed_quartic_reduce_timeout",
    "pointed_quartic_heights",
}


PIPELINE_SEARCH_CONFIG_KEYS = {
    "target_rank",
    "recommended_preset",
    "certificate_timeout",
    "exact_candidates",
    "retention_floor",
    "strategy_note",
}


def _validate_pipeline_search_config(raw, *, where):
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: pipeline_search must be an object")
    unknown = set(raw) - PIPELINE_SEARCH_CONFIG_KEYS
    if unknown:
        raise ValueError(
            f"{where}: pipeline_search has unknown keys: "
            + ", ".join(sorted(unknown))
        )
    for key in ("target_rank", "certificate_timeout", "exact_candidates"):
        if key not in raw:
            continue
        value = raw[key]
        if isinstance(value, bool) or not isinstance(value, int) or int(value) < 1:
            raise ValueError(
                f"{where}: pipeline_search.{key} must be an integer >= 1"
            )
    if "retention_floor" in raw:
        value = raw["retention_floor"]
        if isinstance(value, bool) or not isinstance(value, int) or int(value) < 0:
            raise ValueError(
                f"{where}: pipeline_search.retention_floor must be an integer >= 0"
            )
    if (
        "target_rank" in raw
        and "retention_floor" in raw
        and int(raw["retention_floor"]) > int(raw["target_rank"])
    ):
        raise ValueError(
            f"{where}: pipeline_search.retention_floor cannot exceed target_rank"
        )
    if "recommended_preset" in raw and (
        not isinstance(raw["recommended_preset"], str)
        or not raw["recommended_preset"].strip()
    ):
        raise ValueError(
            f"{where}: pipeline_search.recommended_preset must be a nonempty string"
        )
    if "strategy_note" in raw and not isinstance(raw["strategy_note"], str):
        raise ValueError(
            f"{where}: pipeline_search.strategy_note must be a string"
        )


def _validate_auto_search_policy(raw, *, where):
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: auto_search must be an object")
    unknown = set(raw) - AUTO_SEARCH_POLICY_KEYS
    if unknown:
        raise ValueError(
            f"{where}: auto_search has unknown keys: "
            + ", ".join(sorted(unknown))
        )
    positive = {
        "target_rank", "baseline_certificate_timeout", "pool_size",
        "upper_timeout", "final_upper_timeout", "exact_candidates",
        "classical_covering_min_rank", "classical_covering_timeout",
        "classical_covering_lim1", "classical_covering_lim3",
        "classical_covering_first_limit", "classical_covering_second_limit",
        "pointed_quartic_min_rank", "pointed_quartic_anchors",
        "pointed_quartic_pool_size", "pointed_quartic_rounds",
        "pointed_quartic_deep_keep", "pointed_quartic_timeout",
    }
    nonnegative = {
        "deep_keep", "classical_covering_top_fresh",
        "pointed_quartic_top_fresh", "pointed_quartic_reduce_timeout",
    }
    thresholds = {"integral_minimal_until_rank", "native_integral_until_rank"}
    for key in positive | nonnegative | thresholds:
        if key not in raw:
            continue
        value = raw[key]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{where}: auto_search.{key} must be an integer")
        floor = 0 if key in nonnegative else 1
        if int(value) < floor:
            raise ValueError(f"{where}: auto_search.{key} must be >= {floor}")
    if (
        "integral_minimal_until_rank" in raw
        and "native_integral_until_rank" in raw
        and int(raw["integral_minimal_until_rank"]) != int(raw["native_integral_until_rank"])
    ):
        raise ValueError(
            f"{where}: integral_minimal_until_rank and legacy "
            "native_integral_until_rank must agree when both are declared"
        )
    if "strategy_note" in raw and not isinstance(raw["strategy_note"], str):
        raise ValueError(f"{where}: auto_search.strategy_note must be a string")
    if "classical_covering_enabled" in raw and not isinstance(raw["classical_covering_enabled"], bool):
        raise ValueError(f"{where}: auto_search.classical_covering_enabled must be boolean")
    if "classical_covering_engine" in raw and str(raw["classical_covering_engine"]) not in {
        "simon_known", "mwrank_coverings"
    }:
        raise ValueError(
            f"{where}: auto_search.classical_covering_engine must be "
            "'simon_known' or 'mwrank_coverings'"
        )
    if "classical_covering_n_aux" in raw and (
        isinstance(raw["classical_covering_n_aux"], bool)
        or not isinstance(raw["classical_covering_n_aux"], int)
    ):
        raise ValueError(f"{where}: auto_search.classical_covering_n_aux must be an integer")
    if "direct_affine_enabled" in raw and not isinstance(raw["direct_affine_enabled"], bool):
        raise ValueError(f"{where}: auto_search.direct_affine_enabled must be boolean")
    if "pointed_quartic_enabled" in raw and not isinstance(raw["pointed_quartic_enabled"], bool):
        raise ValueError(f"{where}: auto_search.pointed_quartic_enabled must be boolean")
    if "pointed_quartic_heights" in raw:
        heights = raw["pointed_quartic_heights"]
        if not isinstance(heights, list) or not heights:
            raise ValueError(f"{where}: auto_search.pointed_quartic_heights must be a nonempty list")
        for value in heights:
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(
                    f"{where}: auto_search.pointed_quartic_heights entries must be integers >= 1"
                )


TORSION_PROVIDER_ROLES = {"canonical_universal", "prescribed_subfamily", "general"}


def _validate_torsion_record(raw, *, where):
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: torsion_record must be an object")
    allowed = {
        "rank_lower", "goal_rank", "source",
        "secondary_family_search_recommended",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(
            f"{where}: torsion_record has unknown keys: "
            + ", ".join(sorted(unknown))
        )
    rank = raw.get("rank_lower")
    goal = raw.get("goal_rank")
    if isinstance(rank, bool) or not isinstance(rank, int) or rank < 0:
        raise ValueError(f"{where}: torsion_record.rank_lower must be an integer >= 0")
    if goal is None:
        goal = rank + 1
    if isinstance(goal, bool) or not isinstance(goal, int) or goal <= rank:
        raise ValueError(
            f"{where}: torsion_record.goal_rank must be an integer > rank_lower"
        )
    source = raw.get("source")
    if source is not None and not isinstance(source, dict):
        raise ValueError(f"{where}: torsion_record.source must be an object")
    secondary = raw.get("secondary_family_search_recommended")
    if secondary is not None and not isinstance(secondary, bool):
        raise ValueError(
            f"{where}: torsion_record.secondary_family_search_recommended must be boolean"
        )


def _validate_torsion_provider_role(raw, *, where, torsion_groups=None):
    if raw is None:
        return
    role = str(raw).strip()
    if role not in TORSION_PROVIDER_ROLES:
        raise ValueError(
            f"{where}: torsion_provider_role must be one of "
            + ", ".join(sorted(TORSION_PROVIDER_ROLES))
        )
    if not torsion_groups:
        raise ValueError(
            f"{where}: torsion_provider_role requires torsion_groups"
        )


def _validate_torsion_groups(raw, *, where):
    if raw is None:
        return
    values = [raw] if isinstance(raw, str) else raw
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError(f"{where}: torsion_groups must be a non-empty string/list")
    seen = set()
    for value in values:
        label = canonical_torsion_label(value)
        if label in seen:
            raise ValueError(f"{where}: duplicate torsion group {label!r}")
        seen.add(label)


def _validate_search_presets(raw, *, where):
    """Validate manifest-driven search presets without importing plugin code."""
    if raw is None:
        return
    if not isinstance(raw, list):
        raise ValueError(f"{where}: search_presets must be a list")
    seen = set()
    for index, rec in enumerate(raw, 1):
        if not isinstance(rec, dict):
            raise ValueError(f"{where}: search preset #{index} must be an object")
        pid = str(rec.get("id") or "").strip()
        label = str(rec.get("label") or pid).strip()
        if not pid or not label or pid in seen:
            raise ValueError(f"{where}: search preset #{index} has missing/duplicate id or label")
        seen.add(pid)
        sections = 0
        for section in ("candidate", "family", "target"):
            value = rec.get(section)
            if value is None:
                continue
            if not isinstance(value, dict):
                raise ValueError(f"{where}: search preset {pid!r} {section} must be an object")
            sections += 1
        if sections == 0:
            raise ValueError(
                f"{where}: search preset {pid!r} must declare candidate, family, or target settings"
            )
        candidate = rec.get("candidate") or {}
        unknown = set(candidate) - SEARCH_PRESET_CANDIDATE_KEYS
        if unknown:
            raise ValueError(
                f"{where}: search preset {pid!r} has unknown candidate keys: "
                + ", ".join(sorted(unknown))
            )
        for key in ("a_min", "a_max", "b_min", "b_max", "top"):
            if key in candidate and (
                isinstance(candidate[key], bool) or not isinstance(candidate[key], int)
            ):
                raise ValueError(f"{where}: search preset {pid!r} candidate.{key} must be an integer")
        for key in ("b_min", "b_max", "top"):
            if key in candidate and int(candidate[key]) < 1:
                raise ValueError(f"{where}: search preset {pid!r} candidate.{key} must be >= 1")
        if "engine" in candidate and str(candidate["engine"]) not in {"sieve", "scalar", "sampled"}:
            raise ValueError(
                f"{where}: search preset {pid!r} candidate.engine must be "
                "'sieve', 'scalar', or 'sampled'"
            )
        for key in ("sample_count", "sample_seed"):
            if key in candidate and (
                isinstance(candidate[key], bool) or not isinstance(candidate[key], int)
            ):
                raise ValueError(
                    f"{where}: search preset {pid!r} candidate.{key} must be an integer"
                )
        if "sample_count" in candidate and int(candidate["sample_count"]) < 1:
            raise ValueError(
                f"{where}: search preset {pid!r} candidate.sample_count must be >= 1"
            )


SEARCH_OPTION_TYPES = frozenset({"int", "bool", "choice", "str"})
SEARCH_OPTION_KEYS = frozenset({
    "key", "type", "label", "default", "min", "max", "choices", "help",
    "group", "group_help", "group_columns", "group_expanded",
})
FAMILY_PRESET_SPECIAL_KEYS = frozenset({"limit"})
TARGET_PRESET_SPECIAL_KEYS = frozenset({
    "ratpoints_backend", "backend", "pipeline_preset", "goal_rank",
})


MANIFEST_SEARCH_OPTION_ORCHESTRATION_KEYS = frozenset({
    "limit",
    "target_lower",
})


def _validate_manifest_search_options(raw, *, where):
    """Validate declarative Family/Target option schemas from plugin.json."""
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: search_options must be an object")
    unknown = set(raw) - {"family", "target"}
    if unknown:
        raise ValueError(
            f"{where}: search_options has unknown contexts: "
            + ", ".join(sorted(unknown))
        )
    for context in ("family", "target"):
        if context in raw:
            schema = _validate_search_option_schema(
                raw.get(context),
                context=context,
                where=where,
            )
            duplicate_authority = {
                str(rec["key"])
                for rec in schema
            } & MANIFEST_SEARCH_OPTION_ORCHESTRATION_KEYS
            if duplicate_authority:
                raise ValueError(
                    f"{where}: {context} search_options duplicates Pipeline/page "
                    "orchestration keys: "
                    + ", ".join(sorted(duplicate_authority))
                )


def _manifest_search_options(plugin, variant, context):
    """Return manifest schema for one context, or None when adapter owns it."""
    context = str(context)
    variant_raw = (getattr(variant, "manifest", {}) or {}).get("search_options")
    if isinstance(variant_raw, dict) and context in variant_raw:
        return variant_raw.get(context)
    plugin_raw = (getattr(plugin, "manifest", {}) or {}).get("search_options")
    if isinstance(plugin_raw, dict) and context in plugin_raw:
        return plugin_raw.get(context)
    return None


def search_option_schema_for_variant(
    plugin,
    variant,
    adapter,
    *,
    context,
):
    """Resolve one exact option schema: manifest first, adapter fallback."""
    family_definition = variant_family_definition(plugin, variant)
    raw = _manifest_search_options(plugin, variant, context)
    if raw is None:
        raw = _adapter_search_options(
            adapter,
            context,
            variant=variant.id,
            family_definition=family_definition,
        )
    return _validate_search_option_schema(
        raw,
        context=context,
        where=f"plugin {plugin.id} variant {variant.id}",
    )


def _adapter_search_options(
    adapter,
    context,
    *,
    variant=None,
    family_definition=None,
):
    """Return one V1 option schema; legacy signature handling lives in the bridge."""
    if not isinstance(adapter, FamilyAdapterV1):
        raise TypeError("adapter is not a FamilyAdapterV1")
    return family_adapter_search_options(
        adapter,
        context=context,
        variant=variant,
        family_definition=family_definition,
    )


def _validate_search_option_value(value, spec, *, where):
    typ = str(spec.get("type") or "str")
    key = str(spec.get("key") or "")
    if typ == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{where}: {key} must be an integer")
        if spec.get("min") is not None and int(value) < int(spec["min"]):
            raise ValueError(
                f"{where}: {key} must be >= {int(spec['min'])}"
            )
        if spec.get("max") is not None and int(value) > int(spec["max"]):
            raise ValueError(
                f"{where}: {key} must be <= {int(spec['max'])}"
            )
        return
    if typ == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{where}: {key} must be boolean")
        return
    if typ == "choice":
        choices = list(spec.get("choices") or [])
        if value not in choices:
            raise ValueError(
                f"{where}: {key} must be one of "
                + ", ".join(repr(choice) for choice in choices)
            )
        return
    if not isinstance(value, str):
        raise ValueError(f"{where}: {key} must be a string")


def _validate_search_option_schema(raw, *, context, where):
    """Validate the adapter-owned option schema for one search surface."""
    if raw is None:
        raw = []
    if not isinstance(raw, (list, tuple)):
        raise ValueError(
            f"{where}: {context} search_options must return a list"
        )
    seen = set()
    out = []
    for index, rec in enumerate(raw, 1):
        if not isinstance(rec, dict):
            raise ValueError(
                f"{where}: {context} search option #{index} must be an object"
            )
        unknown_fields = set(rec) - SEARCH_OPTION_KEYS
        if unknown_fields:
            raise ValueError(
                f"{where}: {context} search option #{index} has unknown fields: "
                + ", ".join(sorted(unknown_fields))
            )
        key = str(rec.get("key") or "").strip()
        if not key:
            raise ValueError(
                f"{where}: {context} search option #{index} requires key"
            )
        if key in seen:
            raise ValueError(
                f"{where}: duplicate {context} search option key {key!r}"
            )
        seen.add(key)
        typ = str(rec.get("type") or "str").strip()
        if typ not in SEARCH_OPTION_TYPES:
            raise ValueError(
                f"{where}: {context} search option {key!r} has unsupported "
                f"type {typ!r}"
            )

        spec = dict(rec)
        spec["key"] = key
        spec["type"] = typ
        if rec.get("label") is not None and not isinstance(rec["label"], str):
            raise ValueError(
                f"{where}: {context} search option {key!r} label must be a string"
            )
        for field in ("help", "group", "group_help"):
            if rec.get(field) is not None and not isinstance(rec[field], str):
                raise ValueError(
                    f"{where}: {context} search option {key!r} "
                    f"{field} must be a string"
                )
        if "group_columns" in rec:
            value = rec["group_columns"]
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= int(value) <= 4
            ):
                raise ValueError(
                    f"{where}: {context} search option {key!r} "
                    "group_columns must be an integer from 1 to 4"
                )
        if "group_expanded" in rec and not isinstance(
            rec["group_expanded"], bool
        ):
            raise ValueError(
                f"{where}: {context} search option {key!r} "
                "group_expanded must be boolean"
            )

        if typ == "int":
            for field in ("min", "max"):
                if field in rec and rec[field] is not None and (
                    isinstance(rec[field], bool)
                    or not isinstance(rec[field], int)
                ):
                    raise ValueError(
                        f"{where}: {context} search option {key!r} "
                        f"{field} must be an integer"
                    )
            if (
                rec.get("min") is not None
                and rec.get("max") is not None
                and int(rec["min"]) > int(rec["max"])
            ):
                raise ValueError(
                    f"{where}: {context} search option {key!r} "
                    "min must be <= max"
                )
            if "choices" in rec:
                raise ValueError(
                    f"{where}: {context} integer option {key!r} "
                    "cannot declare choices"
                )
        elif typ == "choice":
            if "min" in rec or "max" in rec:
                raise ValueError(
                    f"{where}: {context} choice option {key!r} "
                    "cannot declare min/max"
                )
            choices = rec.get("choices")
            if not isinstance(choices, (list, tuple)) or not choices:
                raise ValueError(
                    f"{where}: {context} choice option {key!r} "
                    "requires non-empty choices"
                )
            if len({repr(value) for value in choices}) != len(choices):
                raise ValueError(
                    f"{where}: {context} choice option {key!r} "
                    "has duplicate choices"
                )
        else:
            if "min" in rec or "max" in rec or "choices" in rec:
                raise ValueError(
                    f"{where}: {context} {typ} option {key!r} "
                    "cannot declare min/max/choices"
                )

        if "default" in rec:
            _validate_search_option_value(
                rec["default"],
                spec,
                where=f"{where}: {context} default",
            )
        elif typ == "choice":
            raise ValueError(
                f"{where}: {context} choice option {key!r} requires default"
            )
        out.append(spec)
    return tuple(out)


def _validate_special_preset_value(section, key, value, *, where):
    if section == "family" and key == "limit":
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{where}: family.limit must be an integer >= 1")
        return
    if section == "target" and key == "goal_rank":
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(
                f"{where}: target.goal_rank must be an integer >= 1"
            )
        return
    if section == "target" and key == "backend":
        if str(value).lower() not in {"plugin", "free", "torsion"}:
            raise ValueError(
                f"{where}: target.backend must be Plugin, FREE, or Torsion"
            )
        return
    if section == "target" and key in {
        "ratpoints_backend", "pipeline_preset"
    }:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"{where}: target.{key} must be a non-empty string"
            )
        return


def _validate_effective_search_presets(
    plugin,
    *,
    family_schema,
    target_schema,
    variant=None,
):
    family_by_key = {spec["key"]: spec for spec in family_schema}
    target_by_key = {spec["key"]: spec for spec in target_schema}
    variants = (variant,) if variant is not None else plugin.variants
    for variant in variants:
        for preset in search_presets_for_variant(plugin, variant):
            pid = str(preset.get("id") or "")
            where = (
                f"plugin {plugin.id} variant {variant.id} "
                f"search preset {pid!r}"
            )
            for section, schema, specials in (
                (
                    "family",
                    family_by_key,
                    FAMILY_PRESET_SPECIAL_KEYS,
                ),
                (
                    "target",
                    target_by_key,
                    TARGET_PRESET_SPECIAL_KEYS,
                ),
            ):
                values = preset.get(section)
                if values is None:
                    continue
                unknown = set(values) - set(schema) - set(specials)
                if unknown:
                    raise ValueError(
                        f"{where} has unknown {section} keys: "
                        + ", ".join(sorted(unknown))
                    )
                for key, value in values.items():
                    if key in schema:
                        _validate_search_option_value(
                            value,
                            schema[key],
                            where=f"{where} {section}",
                        )
                    else:
                        _validate_special_preset_value(
                            section,
                            key,
                            value,
                            where=where,
                        )


def search_presets_for_variant(plugin: "Plugin", variant: "PluginVariant | None" = None):
    """Return manifest search presets for a family/variant.

    Variant presets replace top-level presets when explicitly declared.  This
    lets one plugin give different meanings to Scan/Widen/Deep for different
    parameterizations while keeping a simple top-level default for ordinary
    one-family plugins.
    """
    if plugin.plugin_type != "family":
        return ()
    target = variant or get_variant(plugin)
    raw = (
        target.manifest.get("search_presets")
        if isinstance(target, PluginVariant) and "search_presets" in target.manifest
        else plugin.manifest.get("search_presets")
    )
    if not raw:
        return ()
    return tuple(dict(rec) for rec in raw)



def _normalize_material_icon(value, *, where):
    """Return a validated Material Symbol manifest icon or ``None``."""
    if value is None:
        return None
    icon = str(value).strip() or None
    if icon is not None and not _MATERIAL_ICON_RE.fullmatch(icon):
        raise ValueError(
            f"{where} has unsupported icon {icon!r}; expected :material/<icon_name>:"
        )
    return icon


def _optional_nonnegative_int(value, *, field, where):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{where}: {field} must be an integer >= 0 or null")
    return int(value)


def _claim_value(manifest, parent_manifest, key):
    if key in manifest:
        return manifest.get(key)
    return (parent_manifest or {}).get(key)


def _validate_rank_claim_metadata(manifest, *, where, parent_manifest=None):
    """Validate one Family claim view without turning historical metadata into evidence."""
    legacy = _optional_nonnegative_int(
        _claim_value(manifest, parent_manifest, "generic_rank"), field="generic_rank", where=where
    )
    historical = _optional_nonnegative_int(
        _claim_value(manifest, parent_manifest, "historical_generic_rank_lower"),
        field="historical_generic_rank_lower", where=where,
    )
    verified = _optional_nonnegative_int(
        _claim_value(manifest, parent_manifest, "verified_generic_rank_lower"),
        field="verified_generic_rank_lower", where=where,
    )
    state = _claim_value(manifest, parent_manifest, "generic_rank_claim_state")
    if state is not None:
        state = str(state).strip()
        if state not in GENERIC_RANK_CLAIM_STATES:
            raise ValueError(f"{where}: unknown generic_rank_claim_state {state!r}")
    if legacy is not None and verified is not None and legacy != verified:
        raise ValueError(f"{where}: generic_rank and verified_generic_rank_lower must agree")
    verified_states = {
        "generic_lower_bound_verified",
        "exact_constant_curve_control",
    }
    if verified is not None and state not in verified_states:
        raise ValueError(
            f"{where}: verified_generic_rank_lower requires a verified claim state "
            "('generic_lower_bound_verified' or 'exact_constant_curve_control')"
        )
    if state in verified_states and verified is None:
        raise ValueError(
            f"{where}: {state} requires verified_generic_rank_lower"
        )
    return {
        "legacy": legacy, "historical": historical, "verified": verified, "state": state,
    }


def _claim_state_status(state):
    return {
        "historical_record": "Rank Hunter verification pending",
        "reconstructed_model": "Reconstructed model · verification pending",
        "sections_verified": "Sections verified · generic independence pending",
        "generic_lower_bound_verified": "Rank Hunter verified",
        "exact_constant_curve_control": "Exact constant-curve control · Rank Hunter verified",
        "verified_exact": "Legacy verified-exact claim",
    }.get(state, "")


def sha256_file(path):
    path=Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() and path.is_file() else None


@dataclass(frozen=True)
class PluginVariant:
    id: str
    name: str
    family_spec: str
    manifest: dict
    parent_manifest: dict | None = None

    def _claim(self, key):
        return _claim_value(self.manifest, self.parent_manifest, key)

    @property
    def historical_generic_rank_lower(self):
        value = self._claim("historical_generic_rank_lower")
        return int(value) if value is not None else None

    @property
    def verified_generic_rank_lower(self):
        value = self._claim("verified_generic_rank_lower")
        return int(value) if value is not None else None

    @property
    def generic_rank_claim_state(self):
        value = self._claim("generic_rank_claim_state")
        return str(value) if value is not None else None

    @property
    def generic_rank(self):
        verified = self.verified_generic_rank_lower
        if verified is not None:
            return verified
        value = self._claim("generic_rank")
        return int(value) if value is not None else None

    @property
    def curve_family_name(self):
        return str(
            self.manifest.get("curve_family_name")
            or (self.parent_manifest or {}).get("curve_family_name")
            or ""
        )


@dataclass(frozen=True)
class ExtensionPage:
    id: str
    label: str
    section: str
    entrypoint: Path
    icon: str | None = None


@dataclass(frozen=True)
class FeatureCommandHook:
    name: str
    entrypoint: Path
    function: str
    priority: int = 100


@dataclass(frozen=True)
class CorpusSpec:
    """One plugin-owned external research corpus.

    Core owns discovery/read-only access; the plugin owns how the corpus is
    materialized and how its variants map into corpus family keys.
    """
    id: str
    name: str
    cache_file: str
    description: str = ""
    builder_path: Path | None = None
    variant_family_keys: tuple[tuple[str, str], ...] = ()

    def family_key(self, variant_id):
        wanted = str(variant_id or "default")
        mapping = dict(self.variant_family_keys)
        return str(mapping.get(wanted, mapping.get("default", wanted)))


@dataclass(frozen=True)
class Plugin:
    id: str
    name: str
    version: str
    plugin_type: str
    root: Path
    family_spec: str | None
    manifest: dict
    capabilities: frozenset[str]
    adapter_path: Path | None
    variants: tuple[PluginVariant, ...] = ()
    default_variant_id: str | None = None
    extension_pages: tuple[ExtensionPage, ...] = ()
    feature_entrypoint: Path | None = None
    feature_hooks: tuple[str, ...] = ()
    feature_command_hooks: tuple[FeatureCommandHook, ...] = ()
    charts: tuple[FamilyChart, ...] = ()
    default_chart_id: str | None = None
    corpora: tuple[CorpusSpec, ...] = ()
    system_privileges: frozenset[str] = frozenset()

    @property
    def historical_generic_rank_lower(self):
        if self.plugin_type != "family":
            return None
        value = self.manifest.get("historical_generic_rank_lower")
        if value is not None:
            return int(value)
        vals = [
            v.historical_generic_rank_lower
            for v in self.variants
            if v.historical_generic_rank_lower is not None
        ]
        return max(vals) if vals else None

    @property
    def verified_generic_rank_lower(self):
        if self.plugin_type != "family":
            return None
        value = self.manifest.get("verified_generic_rank_lower")
        if value is not None:
            return int(value)
        vals = [
            v.verified_generic_rank_lower
            for v in self.variants
            if v.verified_generic_rank_lower is not None
        ]
        return max(vals) if vals else None

    @property
    def generic_rank_claim_state(self):
        if self.plugin_type != "family":
            return None
        value = self.manifest.get("generic_rank_claim_state")
        if value is not None:
            return str(value)
        states = [
            v.generic_rank_claim_state
            for v in self.variants
            if v.generic_rank_claim_state is not None
        ]
        if not states:
            return None
        order = {
            "historical_record": 0, "reconstructed_model": 1,
            "sections_verified": 2, "generic_lower_bound_verified": 3,
            "exact_constant_curve_control": 3, "verified_exact": 3,
        }
        return max(states, key=lambda x: order.get(x, -1))

    @property
    def generic_rank(self):
        if self.plugin_type != "family":
            return None
        verified = self.manifest.get("verified_generic_rank_lower")
        if verified is not None:
            return int(verified)
        value = self.manifest.get("generic_rank")
        if value is not None:
            return int(value)
        vals = [v.generic_rank for v in self.variants if v.generic_rank is not None]
        return max(vals) if vals else None

    @property
    def icon(self):
        """Icon used by shared plugin surfaces.

        Any plugin type may declare a top-level ``icon``.  Extensions that do
        not declare one inherit the first page ``menu.icon`` so existing
        navigation-icon manifests also render consistently on the Plugins page.
        """
        icon = _normalize_material_icon(self.manifest.get("icon"), where=f"plugin {self.id!r}")
        if icon is not None:
            return icon
        if self.plugin_type == "extension":
            for page in self.extension_pages:
                if page.icon is not None:
                    return page.icon
        return None

    @property
    def description(self):
        return str(self.manifest.get("description") or "")

    @property
    def has_variants(self):
        return self.plugin_type == "family" and len(self.variants) > 1


def family_rank_claim(plugin: Plugin, variant: PluginVariant | None = None):
    """Normalize Family rank metadata while keeping historical claims non-operational."""
    if plugin.plugin_type != "family":
        return {
            "effective_lower": None, "historical_lower": None, "verified_lower": None,
            "state": None, "verified": False, "legacy": False, "status": "",
            "label": "Rank —",
        }
    target = variant or plugin
    effective = target.generic_rank
    historical = target.historical_generic_rank_lower
    verified = target.verified_generic_rank_lower
    state = target.generic_rank_claim_state
    if isinstance(target, PluginVariant):
        legacy_value = _claim_value(target.manifest, target.parent_manifest, "generic_rank")
    else:
        legacy_value = plugin.manifest.get("generic_rank")
    legacy = verified is None and legacy_value is not None
    status = _claim_state_status(state)
    if verified is not None:
        label = f"Verified generic lower ≥{verified}"
    elif legacy and effective is not None:
        label = f"Generic rank ≥{effective}"
    elif historical is not None:
        label = f"Historical generic claim ≥{historical} · unverified"
    else:
        label = "Generic rank —"
    return {
        "effective_lower": effective, "historical_lower": historical,
        "verified_lower": verified, "state": state, "verified": verified is not None,
        "legacy": legacy, "status": status, "label": label,
    }


def family_rank_label(plugin: Plugin, variant: PluginVariant | None = None, *, compact=False):
    claim = family_rank_claim(plugin, variant)
    if compact and claim["historical_lower"] is not None and claim["effective_lower"] is None:
        return f"historical generic claim ≥{claim['historical_lower']} · unverified"
    if compact and claim["verified_lower"] is not None:
        return f"verified generic lower ≥{claim['verified_lower']}"
    if compact and claim["legacy"] and claim["effective_lower"] is not None:
        return f"generic rank ≥{claim['effective_lower']}"
    return claim["label"]


def plugin_roots(project_root):
    return [Path(project_root).resolve() / "plugins"]


def _family_spec(root: Path, family: dict):
    family = family or {}
    kind = str(family.get("kind") or "json")
    if kind == "json":
        path = (root / str(family.get("file") or "family.json")).resolve()
        if not path.exists():
            raise ValueError(f"family JSON not found: {path}")
        return f"json:{path}"
    if kind == "module":
        spec = str(family.get("spec") or "").strip()
        if not spec:
            raise ValueError("module family requires family.spec")
        if family.get("file"):
            path = (root / str(family["file"])).resolve()
            if not path.exists():
                raise ValueError(f"module family source not found: {path}")
        return spec
    raise ValueError(f"unsupported family kind {kind!r}")


def _variants(root: Path, manifest: dict):
    raw = manifest.get("variants")
    if raw is None:
        spec = _family_spec(root, manifest.get("family") or {})
        synthetic = {
            "id": "default", "name": str(manifest.get("name") or "Default"),
            "generic_rank": manifest.get("generic_rank"),
            "historical_generic_rank_lower": manifest.get("historical_generic_rank_lower"),
            "verified_generic_rank_lower": manifest.get("verified_generic_rank_lower"),
            "generic_rank_claim_state": manifest.get("generic_rank_claim_state"),
            "curve_family_name": manifest.get("curve_family_name"),
            "candidate_defaults": manifest.get("candidate_defaults") or {},
            "validation_parameter": manifest.get("validation_parameter", "1"),
            "family": manifest.get("family") or {},
        }
        return (PluginVariant("default", synthetic["name"], spec, synthetic, manifest),), "default"
    if not isinstance(raw, list) or not raw:
        raise ValueError("plugin variants must be a non-empty list")
    out, seen = [], set()
    for index, rec in enumerate(raw, 1):
        if not isinstance(rec, dict):
            raise ValueError(f"variant #{index} must be an object")
        vid = str(rec.get("id") or "").strip()
        name = str(rec.get("name") or vid).strip()
        if not vid or not name or vid in seen:
            raise ValueError(f"variant #{index} has missing/duplicate id or name")
        if "family" not in rec:
            raise ValueError(f"variant {vid!r} requires family")
        seen.add(vid)
        out.append(PluginVariant(vid, name, _family_spec(root, rec.get("family") or {}), rec, manifest))
    requested = str(manifest.get("default_variant") or out[0].id)
    if requested not in seen:
        raise ValueError(f"default_variant {requested!r} is not declared")
    return tuple(out), requested



def _charts(plugin_id: str, manifest: dict, variants: tuple[PluginVariant, ...], default_variant_id: str):
    raw = manifest.get("charts") or []
    if not isinstance(raw, list):
        raise ValueError("plugin charts must be a list")
    by_id = {v.id: v for v in variants}
    out, seen = [], set()
    for index, rec in enumerate(raw, 1):
        if not isinstance(rec, dict):
            raise ValueError(f"chart #{index} must be an object")
        cid = str(rec.get("id") or "").strip()
        label = str(rec.get("label") or cid).strip()
        if not cid or not label or cid in seen:
            raise ValueError(f"chart #{index} has missing/duplicate id or label")
        variant_id = str(rec.get("variant") or default_variant_id)
        native_variant_id = str(rec.get("native_variant") or variant_id)
        if variant_id not in by_id:
            raise ValueError(f"chart {cid!r} references unknown variant {variant_id!r}")
        if native_variant_id not in by_id:
            raise ValueError(f"chart {cid!r} references unknown native_variant {native_variant_id!r}")
        matrix = rec.get("matrix") or rec.get("pgl2_matrix")
        if not isinstance(matrix, (list, tuple)) or len(matrix) != 4:
            raise ValueError(f"chart {cid!r} requires exact matrix [A,B,C,D]")
        # Normalize exact rational strings and validate nonsingularity through FamilyChart.
        matrix = tuple(qstr(x) for x in matrix)
        symmetry = rec.get("symmetry_matrices") or []
        if not isinstance(symmetry, list):
            raise ValueError(f"chart {cid!r} symmetry_matrices must be a list")
        normalized_symmetry=[]
        for j, m in enumerate(symmetry, 1):
            if not isinstance(m, (list, tuple)) or len(m) != 4:
                raise ValueError(f"chart {cid!r} symmetry matrix #{j} requires [A,B,C,D]")
            normalized_symmetry.append(tuple(qstr(x) for x in m))
        native_spec = by_id[native_variant_id].family_spec
        native_key = str(rec.get("native_family_key") or f"{plugin_id}:{native_variant_id}")
        chart = FamilyChart(
            id=cid, label=label, variant_id=variant_id, native_variant_id=native_variant_id,
            native_family_spec=native_spec, native_family_key=native_key, matrix=matrix,
            candidate_defaults=dict(rec.get("candidate_defaults") or {}),
            symmetry_matrices=tuple(normalized_symmetry), metadata=dict(rec),
        )
        # force exact matrix validation now
        _ = chart.fingerprint
        seen.add(cid); out.append(chart)
    requested = str(manifest.get("default_chart") or (out[0].id if out else "")) or None
    if requested is not None and requested not in seen:
        raise ValueError(f"default_chart {requested!r} is not declared")
    return tuple(out), requested


def get_chart(plugin: Plugin, chart_id=None):
    if not plugin.charts:
        return None
    wanted = str(chart_id or plugin.default_chart_id or plugin.charts[0].id)
    for chart in plugin.charts:
        if chart.id == wanted:
            return chart
    raise KeyError(f"plugin {plugin.id!r} has no chart {wanted!r}")


def charts_for_variant(plugin: Plugin, variant_id: str):
    return tuple(c for c in plugin.charts if c.variant_id == str(variant_id))


def _corpora(root: Path, manifest: dict):
    raw = manifest.get("corpora") or []
    if not isinstance(raw, list):
        raise ValueError("plugin corpora must be a list")
    out, seen = [], set()
    for index, rec in enumerate(raw, 1):
        if not isinstance(rec, dict):
            raise ValueError(f"corpus #{index} must be an object")
        cid = str(rec.get("id") or "").strip()
        name = str(rec.get("name") or cid).strip()
        cache_file = str(rec.get("cache_file") or f"{cid}.db").strip()
        if not cid or not name or cid in seen:
            raise ValueError(f"corpus #{index} has missing/duplicate id or name")
        if not cache_file or Path(cache_file).name != cache_file or cache_file in {".", ".."}:
            raise ValueError(f"corpus {cid!r} cache_file must be a plain filename")
        builder_path = None
        builder = rec.get("builder")
        if builder:
            builder_path = (root / str(builder)).resolve()
            if not builder_path.exists() or not builder_path.is_file():
                raise ValueError(f"corpus {cid!r} builder not found: {builder_path}")
        mapping = rec.get("variant_family_keys") or {}
        if not isinstance(mapping, dict):
            raise ValueError(f"corpus {cid!r} variant_family_keys must be an object")
        pairs = []
        for variant_id, family_key in mapping.items():
            variant_id = str(variant_id).strip()
            family_key = str(family_key).strip()
            if not variant_id or not family_key:
                raise ValueError(f"corpus {cid!r} has an empty variant/family key")
            pairs.append((variant_id, family_key))
        seen.add(cid)
        out.append(CorpusSpec(
            id=cid,
            name=name,
            cache_file=cache_file,
            description=str(rec.get("description") or ""),
            builder_path=builder_path,
            variant_family_keys=tuple(sorted(pairs)),
        ))
    return tuple(out)


def get_corpus(plugin: Plugin, corpus_id=None):
    if not plugin.corpora:
        return None
    wanted = str(corpus_id or plugin.corpora[0].id)
    for corpus in plugin.corpora:
        if corpus.id == wanted:
            return corpus
    raise KeyError(f"plugin {plugin.id!r} has no corpus {wanted!r}")


def corpora_for_variant(plugin: Plugin, variant: PluginVariant | None = None):
    if plugin.plugin_type != "family":
        return plugin.corpora
    variant = variant or get_variant(plugin)
    return tuple(
        (corpus, corpus.family_key(variant.id))
        for corpus in plugin.corpora
    )


def _extension_pages(root: Path, manifest: dict):
    default_entrypoint = str(manifest.get("entrypoint") or "extension.py")
    menu = manifest.get("menu")
    raw = menu if isinstance(menu, list) else [menu]
    if not raw or raw == [None]:
        raise ValueError("extension plugin requires menu")
    pages, seen = [], set()
    for index, rec in enumerate(raw, 1):
        if not isinstance(rec, dict):
            raise ValueError(f"extension menu #{index} must be an object")
        label = str(rec.get("label") or manifest.get("name") or "").strip()
        page_id = str(rec.get("id") or label.lower().replace(" ", "_")).strip()
        section = canonical_extension_section(rec.get("section") or "Analysis")
        if not label or not page_id or page_id in seen:
            raise ValueError(f"extension menu #{index} has missing/duplicate id or label")
        if section == "System":
            raise ValueError(
                "extension menu section 'System' is core-owned and privileged"
            )
        if section not in EXTENSION_MENU_SECTIONS:
            raise ValueError(f"unsupported extension menu section {section!r}")
        icon = _normalize_material_icon(
            rec.get("icon"), where=f"extension menu #{index}"
        )
        entry = (root / str(rec.get("entrypoint") or default_entrypoint)).resolve()
        if not entry.exists():
            raise ValueError(f"extension entrypoint not found: {entry}")
        seen.add(page_id)
        pages.append(ExtensionPage(page_id, label, section, entry, icon))
    return tuple(pages)


def read_plugin(path: str | Path) -> Plugin:
    root = Path(path).resolve()
    data = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
    if int(data.get("schema_version", 1)) != 1:
        raise ValueError("unsupported plugin schema_version")
    pid = str(data.get("id") or "").strip()
    name = str(data.get("name") or "").strip()
    version = str(data.get("version") or "0")
    ptype = str(data.get("plugin_type") or "family").strip().lower()
    if not pid or not name:
        raise ValueError("plugin requires id and name")
    if ptype not in PLUGIN_TYPES:
        raise ValueError(f"unsupported plugin_type {ptype!r}")

    # Validate the shared card icon for every plugin type during manifest load.
    _normalize_material_icon(data.get("icon"), where=f"plugin {pid!r}")
    corpora = _corpora(root, data)

    if ptype == "extension":
        pages = _extension_pages(root, data)
        return Plugin(
            id=pid, name=name, version=version, plugin_type=ptype, root=root,
            family_spec=None, manifest=data, capabilities=frozenset(), adapter_path=None,
            extension_pages=pages, corpora=corpora,
        )

    if ptype == "feature":
        from rank42.plugin_hooks import (
            ACCEPTED_FEATURE_HOOKS,
            SUPPORTED_FUNCTIONAL_FEATURE_HOOKS,
            SUPPORTED_SYSTEM_PRIVILEGES,
            canonical_feature_hook,
            feature_hook_allowed,
            required_system_privilege,
        )
        raw_privileges = data.get("system_privileges") or []
        if isinstance(raw_privileges, str):
            raw_privileges = [raw_privileges]
        if not isinstance(raw_privileges, list):
            raise ValueError("feature system_privileges must be a list")
        system_privileges = frozenset(
            str(value).strip()
            for value in raw_privileges
            if str(value).strip()
        )
        unknown_privileges = system_privileges - SUPPORTED_SYSTEM_PRIVILEGES
        if unknown_privileges:
            raise ValueError(
                "unsupported feature system privilege(s): "
                + ", ".join(sorted(unknown_privileges))
            )

        raw_hooks = data.get("hooks") or []
        if isinstance(raw_hooks, (str, dict)):
            raw_hooks = [raw_hooks]
        if not isinstance(raw_hooks, list) or not raw_hooks:
            raise ValueError("feature plugin requires a non-empty hooks list")

        render_hooks = []
        command_hooks = []
        for index, rec in enumerate(raw_hooks, 1):
            if isinstance(rec, str):
                hook_id = rec.strip()
                if not hook_id:
                    continue
                if hook_id not in ACCEPTED_FEATURE_HOOKS:
                    raise ValueError(f"unsupported feature hook(s): {hook_id}")
                hook_id = canonical_feature_hook(hook_id)
                required_privilege = required_system_privilege(hook_id)
                if required_privilege is not None:
                    if required_privilege not in system_privileges:
                        raise ValueError(
                            f"feature hook {hook_id!r} requires system privilege "
                            f"{required_privilege!r}"
                        )
                    if not feature_hook_allowed(
                        pid,
                        hook_id,
                        system_privileges,
                    ):
                        raise ValueError(
                            f"feature plugin {pid!r} is not core-allowlisted "
                            f"for privileged hook {hook_id!r}"
                        )
                if hook_id not in render_hooks:
                    render_hooks.append(hook_id)
                continue

            if not isinstance(rec, dict):
                raise ValueError(f"feature hook #{index} must be a string or object")
            hook_name = str(rec.get("name") or "").strip()
            if hook_name not in SUPPORTED_FUNCTIONAL_FEATURE_HOOKS:
                raise ValueError(f"unsupported functional feature hook {hook_name!r}")
            hook_entry = (root / str(rec.get("entrypoint") or "feature.py")).resolve()
            if not hook_entry.exists():
                raise ValueError(f"feature hook entrypoint not found: {hook_entry}")
            function = str(rec.get("function") or "on_search_command").strip()
            if not function:
                raise ValueError("search_command hook requires function")
            command_hooks.append(FeatureCommandHook(
                hook_name, hook_entry, function, int(rec.get("priority", 100))
            ))

        used_system_privileges = frozenset(
            privilege
            for privilege in (
                required_system_privilege(hook_id)
                for hook_id in render_hooks
            )
            if privilege is not None
        )
        unused_system_privileges = (
            system_privileges - used_system_privileges
        )
        if unused_system_privileges:
            raise ValueError(
                "unused feature system privilege(s): "
                + ", ".join(sorted(unused_system_privileges))
            )

        entry = (root / str(data.get("entrypoint") or "feature.py")).resolve()
        if not entry.exists():
            raise ValueError(f"feature entrypoint not found: {entry}")
        if not render_hooks and not command_hooks:
            raise ValueError("feature plugin requires at least one valid hook")
        return Plugin(
            id=pid, name=name, version=version, plugin_type=ptype, root=root,
            family_spec=None, manifest=data, capabilities=frozenset(), adapter_path=None,
            feature_entrypoint=entry, feature_hooks=tuple(render_hooks),
            feature_command_hooks=tuple(command_hooks),
            system_privileges=system_privileges,
            corpora=corpora,
        )

    _validate_rank_claim_metadata(data, where=f"plugin {pid}")
    _validate_torsion_groups(data.get("torsion_groups"), where=f"plugin {pid}")
    _validate_torsion_provider_role(
        data.get("torsion_provider_role"),
        where=f"plugin {pid}",
        torsion_groups=data.get("torsion_groups"),
    )
    _validate_torsion_record(data.get("torsion_record"), where=f"plugin {pid}")
    _validate_search_presets(data.get("search_presets"), where=f"plugin {pid}")
    _validate_manifest_search_options(
        data.get("search_options"),
        where=f"plugin {pid}",
    )
    _validate_pipeline_search_config(
        data.get("pipeline_search"),
        where=f"plugin {pid}",
    )
    _validate_auto_search_policy(data.get("auto_search"), where=f"plugin {pid}")
    raw_variants = data.get("variants")
    if isinstance(raw_variants, list):
        for index, rec in enumerate(raw_variants, 1):
            if isinstance(rec, dict):
                variant_where = f"plugin {pid} variant {rec.get('id') or index}"
                _validate_rank_claim_metadata(
                    rec,
                    where=variant_where,
                    parent_manifest=data,
                )
                _validate_torsion_groups(rec.get("torsion_groups"), where=variant_where)
                effective_torsion_groups = (
                    rec.get("torsion_groups")
                    if "torsion_groups" in rec
                    else data.get("torsion_groups")
                )
                _validate_torsion_provider_role(
                    rec.get("torsion_provider_role"),
                    where=variant_where,
                    torsion_groups=effective_torsion_groups,
                )
                _validate_torsion_record(rec.get("torsion_record"), where=variant_where)
                _validate_search_presets(rec.get("search_presets"), where=variant_where)
                _validate_manifest_search_options(
                    rec.get("search_options"),
                    where=variant_where,
                )
                _validate_pipeline_search_config(
                    rec.get("pipeline_search"),
                    where=variant_where,
                )
                _validate_auto_search_policy(rec.get("auto_search"), where=variant_where)

    caps = frozenset(str(x) for x in (data.get("capabilities") or []))
    unknown = caps - CAPABILITIES
    if unknown:
        raise ValueError("unknown capabilities: " + ", ".join(sorted(unknown)))
    adapter = None
    if data.get("search_adapter"):
        adapter = (root / str(data["search_adapter"])).resolve()
        if not adapter.exists():
            raise ValueError(f"search adapter not found: {adapter}")
    variants, default_id = _variants(root, data)
    charts, default_chart_id = _charts(pid, data, variants, default_id)
    if charts and "pgl2_search" not in caps:
        raise ValueError("declared charts require pgl2_search capability")
    default = next(v for v in variants if v.id == default_id)
    return Plugin(
        id=pid, name=name, version=version, plugin_type=ptype, root=root,
        family_spec=default.family_spec, manifest=data, capabilities=caps, adapter_path=adapter,
        variants=variants, default_variant_id=default_id, charts=charts,
        default_chart_id=default_chart_id, corpora=corpora,
    )


def _plugin_manifests(base: Path):
    """Yield supported plugin manifests below one runtime plugin root.

    The canonical runtime layout remains flat::

        plugins/<plugin>/plugin.json

    For development checkouts we also accept one organizational category layer
    such as ``families/`` and ``extensions/`` (case-insensitive).  This keeps
    the scientific registry independent of a repository's presentation layout
    without recursively treating backups or arbitrary nested data as plugins.
    """
    base = Path(base)
    seen = set()

    def emit(pattern):
        for manifest in sorted(base.glob(pattern)):
            try:
                key = manifest.resolve()
            except OSError:
                key = manifest.absolute()
            if key in seen:
                continue
            seen.add(key)
            yield manifest

    yield from emit("*/plugin.json")

    if not base.exists():
        return
    for category in sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.name.lower()):
        if category.name.lower() not in {"families", "extensions", "features"}:
            continue
        for manifest in sorted(category.glob("*/plugin.json")):
            try:
                key = manifest.resolve()
            except OSError:
                key = manifest.absolute()
            if key in seen:
                continue
            seen.add(key)
            yield manifest


def _plugin_manifest_signature(manifest: Path):
    """Return a cheap cache signature for one plugin manifest.

    The manifest stat invalidates content edits while the plugin-directory mtime
    invalidates add/remove/rename operations for referenced local files. Source
    files may change without forcing a manifest reparse because Plugin records
    retain paths rather than importing those modules during discovery.
    """
    resolved = manifest.resolve()
    stat = resolved.stat()
    parent_stat = resolved.parent.stat()
    return (
        str(manifest),
        str(resolved),
        int(stat.st_mtime_ns),
        int(stat.st_ctime_ns),
        int(stat.st_size),
        int(parent_stat.st_mtime_ns),
    )


@lru_cache(maxsize=512)
def _cached_plugin_record(
    manifest_path,
    resolved_manifest_path,
    manifest_mtime_ns,
    manifest_ctime_ns,
    manifest_size,
    plugin_dir_mtime_ns,
):
    """Parse one unchanged plugin manifest once per Python process."""
    # Parse through the discovered path so Plugin.root/provenance retains the
    # same symlink/runtime path behavior as uncached discovery.
    path = Path(manifest_path)
    try:
        return read_plugin(path.parent)
    except Exception as exc:
        return {"path": str(path.parent), "error": str(exc)}


def _discovered_plugin_record(manifest: Path):
    try:
        signature = _plugin_manifest_signature(manifest)
    except OSError as exc:
        return {"path": str(manifest.parent), "error": str(exc)}
    return _cached_plugin_record(*signature)


def discover_plugins(project_root, *, include_superseded=False):
    found = []
    seen_ids = set()
    seen_manifests = set()
    for base in plugin_roots(project_root):
        if not base.exists():
            continue
        for manifest in _plugin_manifests(base):
            try:
                manifest_key = manifest.resolve()
            except OSError:
                manifest_key = manifest.absolute()
            if manifest_key in seen_manifests:
                continue
            seen_manifests.add(manifest_key)
            rec = _discovered_plugin_record(manifest)
            if isinstance(rec, Plugin):
                # Local plugins directly under plugins/ take precedence over the
                # official plugins/rh-plugins checkout when IDs collide.
                if rec.id in seen_ids:
                    continue
                seen_ids.add(rec.id)
            found.append(rec)
    if include_superseded:
        return found
    hidden = set()
    for rec in found:
        if isinstance(rec, Plugin):
            hidden.update(str(x) for x in (rec.manifest.get("supersedes_plugin_ids") or []))
    return [rec for rec in found if not isinstance(rec, Plugin) or rec.id not in hidden]


def get_plugin(project_root, plugin_id) -> Plugin:
    for rec in discover_plugins(project_root, include_superseded=True):
        if isinstance(rec, Plugin) and rec.id == str(plugin_id):
            return rec
    raise KeyError(f"plugin {plugin_id!r} not found")


def get_variant(plugin: Plugin, variant_id=None) -> PluginVariant:
    if plugin.plugin_type != "family":
        raise TypeError(f"plugin {plugin.id!r} is {plugin.plugin_type}, not a family")
    wanted = str(variant_id or plugin.default_variant_id or "default")
    for variant in plugin.variants:
        if variant.id == wanted:
            return variant
    raise KeyError(f"plugin {plugin.id!r} has no variant {wanted!r}")


def variant_for_family_spec(plugin: Plugin, family_spec):
    if plugin.plugin_type != "family":
        return None
    spec = str(family_spec or "")
    return next((v for v in plugin.variants if v.family_spec == spec), None)


def variant_torsion_provider_role(plugin, variant=None):
    if plugin.plugin_type != "family":
        return None
    target = variant or get_variant(plugin)
    raw = (
        target.manifest.get("torsion_provider_role")
        if "torsion_provider_role" in target.manifest
        else plugin.manifest.get("torsion_provider_role")
    )
    return None if raw is None else str(raw)


def variant_torsion_record(plugin, variant=None):
    """Return normalized prescribed-torsion record metadata, if declared."""
    if plugin.plugin_type != "family":
        return None
    target = variant or get_variant(plugin)
    raw = (
        target.manifest.get("torsion_record")
        if "torsion_record" in target.manifest
        else plugin.manifest.get("torsion_record")
    )
    if raw is None:
        return None
    rec = dict(raw)
    rank = int(rec["rank_lower"])
    goal = int(rec.get("goal_rank", rank + 1))
    source = dict(rec.get("source") or {})
    return {
        "rank_lower": rank,
        "goal_rank": goal,
        "source": source,
        "secondary_family_search_recommended": bool(
            rec.get("secondary_family_search_recommended", False)
        ),
    }


def variant_torsion_groups(plugin, variant=None):
    """Return manifest-declared exact torsion provider hints, if any."""
    if plugin.plugin_type != "family":
        return ()
    target = variant or get_variant(plugin)
    raw = (
        target.manifest.get("torsion_groups")
        if "torsion_groups" in target.manifest
        else plugin.manifest.get("torsion_groups")
    )
    if raw is None:
        return ()
    values = [raw] if isinstance(raw, str) else list(raw)
    return tuple(canonical_torsion_label(value) for value in values)


def variant_candidate_defaults(plugin, variant):
    values = dict(plugin.manifest.get("candidate_defaults") or {})
    values.update(dict(variant.manifest.get("candidate_defaults") or {}))
    return values


def variant_value(plugin, variant, key, default=None):
    return variant.manifest.get(key, plugin.manifest.get(key, default))


def variant_family_definition(plugin: Plugin, variant: PluginVariant):
    """Return the manifest-owned family definition for one variant."""
    raw=variant.manifest.get("family")
    if raw is None and len(plugin.variants)==1:
        raw=plugin.manifest.get("family")
    return dict(raw or {})


def _load_file_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load plugin module {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_adapter(plugin: Plugin) -> FamilyAdapterV1 | None:
    """Load one Family adapter through the versioned V1 SDK facade."""
    if plugin.plugin_type != "family" or plugin.adapter_path is None:
        return None
    module = _load_file_module(
        f"rank42_user_plugin_{plugin.id.replace('-', '_')}",
        plugin.adapter_path,
    )
    return coerce_family_adapter_v1(module, plugin_id=plugin.id)


def load_feature(plugin: Plugin) -> ModuleType:
    if plugin.plugin_type != "feature" or plugin.feature_entrypoint is None:
        raise TypeError(f"plugin {plugin.id!r} is not a feature")
    module = _load_file_module(
        f"rank42_feature_{plugin.id.replace('-', '_')}",
        plugin.feature_entrypoint,
    )
    if not callable(getattr(module, "render", None)):
        raise ValueError(f"feature entrypoint {plugin.feature_entrypoint.name} must define render(context)")
    return module


def apply_search_command_features(
    project_root,
    db,
    command,
    *,
    family_plugin=None,
    kind=None,
    metadata=None,
    plugin_ids=None,
):
    """Apply enabled legacy/functional search-command Feature hooks in priority order.

    This is the v0.8.7 compatibility bridge for narrow command transforms such
    as Symmetry Reducer.  Hooks receive plain structured data and may return
    ``None`` (no change) or a mapping containing a replacement ``command``.
    Scientific semantics stay in the Feature plugin; core only hosts ordering,
    enable-state checks, provenance, and failure isolation.
    """
    current = [str(x) for x in command]
    audit = []
    jobs = []
    allowed_ids = None if plugin_ids is None else {str(x) for x in plugin_ids}
    for plugin in discover_plugins(project_root):
        if not isinstance(plugin, Plugin) or plugin.plugin_type != "feature":
            continue
        if not plugin.feature_command_hooks or not is_enabled(db, plugin):
            continue
        if allowed_ids is not None and plugin.id not in allowed_ids:
            continue
        for hook in plugin.feature_command_hooks:
            if hook.name == "search_command":
                jobs.append((hook.priority, plugin.id, plugin, hook))

    for _, _, plugin, hook in sorted(jobs, key=lambda x: (x[0], x[1])):
        try:
            module = _load_file_module(
                f"rank42_feature_cmd_{plugin.id.replace('-', '_')}_{hook.priority}",
                hook.entrypoint,
            )
            callback = getattr(module, hook.function, None)
            if not callable(callback):
                raise ValueError(f"{hook.entrypoint.name} does not define callable {hook.function}()")
            fam = None
            if family_plugin is not None:
                fam = {
                    "id": family_plugin.id,
                    "name": family_plugin.name,
                    "version": family_plugin.version,
                    "plugin_type": family_plugin.plugin_type,
                }
            context = {
                "project_root": str(Path(project_root).resolve()),
                "command": list(current),
                "family_plugin": fam or {},
                "kind": str(kind or ""),
                "metadata": dict(metadata or {}),
            }
            result = callback(context)
            if result is None:
                continue
            if not isinstance(result, dict) or not isinstance(result.get("command"), (list, tuple)):
                raise ValueError("search_command hook must return None or {'command': [...], ...}")
            replacement = [str(x) for x in result["command"]]
            if not replacement:
                raise ValueError("search_command hook returned an empty command")
            current = replacement
            audit.append({
                "plugin_id": plugin.id,
                "plugin_version": plugin.version,
                "priority": hook.priority,
                "note": str(result.get("note") or ""),
                "metadata": dict(result.get("metadata") or {}),
            })
        except Exception as exc:
            audit.append({
                "plugin_id": plugin.id,
                "plugin_version": plugin.version,
                "priority": hook.priority,
                "error": str(exc),
            })
    return current, audit


def load_extension(plugin: Plugin, page: ExtensionPage) -> ModuleType:
    if plugin.plugin_type != "extension":
        raise TypeError(f"plugin {plugin.id!r} is not an extension")
    module = _load_file_module(
        f"rank42_extension_{plugin.id.replace('-', '_')}_{page.id.replace('-', '_')}",
        page.entrypoint,
    )
    if not callable(getattr(module, "render", None)):
        raise ValueError(f"extension entrypoint {page.entrypoint.name} must define render(context)")
    return module


def plugin_fingerprints(plugin: Plugin, variant: PluginVariant | None = None):
    from rank42.family_loader import family_source_sha256
    manifest_hash = sha256_file(plugin.root / "plugin.json")
    if plugin.plugin_type in {"extension", "feature"}:
        entries = (
            sorted({str(p.entrypoint) for p in plugin.extension_pages})
            if plugin.plugin_type == "extension"
            else sorted({
                *([str(plugin.feature_entrypoint)] if plugin.feature_entrypoint else []),
                *(str(h.entrypoint) for h in plugin.feature_command_hooks),
            })
        )
        payload = "\n".join((sha256_file(x) or "") for x in entries)
        return {"plugin_manifest_sha256": manifest_hash,
                "family_sha256": None,
                "adapter_sha256": hashlib.sha256(payload.encode()).hexdigest() if payload else None}
    variant = variant or get_variant(plugin)
    return {
        "plugin_manifest_sha256": manifest_hash,
        "family_sha256": family_source_sha256(variant.family_spec),
        "adapter_sha256": sha256_file(plugin.adapter_path) if plugin.adapter_path else None,
    }


def plugin_validation_fingerprints(plugin: Plugin):
    """Return the complete content fingerprint set used to judge validation freshness."""
    if plugin.plugin_type != "family":
        return plugin_fingerprints(plugin)

    from rank42.family_loader import family_source_sha256

    return {
        "plugin_manifest_sha256": sha256_file(plugin.root / "plugin.json"),
        "adapter_sha256": sha256_file(plugin.adapter_path) if plugin.adapter_path else None,
        "family_sha256_by_variant": {
            variant.id: family_source_sha256(variant.family_spec)
            for variant in plugin.variants
        },
    }


def plugin_state(db, plugin_id):
    return db.execute("SELECT * FROM plugin_states WHERE plugin_id=?", (str(plugin_id),)).fetchone()


def unavailable_plugin_states(db, installed_plugin_ids=()):
    """Return persisted Plugin lifecycle records whose package is no longer discoverable."""
    installed = {str(value) for value in (installed_plugin_ids or ())}
    rows = db.execute(
        "SELECT * FROM plugin_states ORDER BY plugin_id"
    ).fetchall()
    out = []
    for row in rows:
        plugin_id = str(row["plugin_id"])
        if plugin_id in installed:
            continue
        validation = _validation_payload(row)
        fingerprints = (
            validation.get("validation_fingerprints")
            or validation.get("fingerprints")
            or {}
        )
        out.append({
            "plugin_id": plugin_id,
            "plugin_name": str(validation.get("plugin_name") or plugin_id),
            "plugin_version": str(validation.get("plugin_version") or ""),
            "plugin_type": str(validation.get("plugin_type") or "unknown"),
            "status": "unavailable",
            "previous_status": str(row["status"] or ""),
            "enabled": bool(row["enabled"]),
            "source_path": validation.get("source_path"),
            "fingerprints": dict(fingerprints) if isinstance(fingerprints, dict) else {},
            "validation": dict(validation),
        })
    return tuple(out)


_KEEP_PLUGIN_VALIDATION = object()


def _validation_payload(row):
    if row is None:
        return {}
    try:
        payload = json.loads(row["validation_json"] or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def set_plugin_state(
    db,
    plugin_id,
    *,
    enabled,
    status,
    validation=_KEEP_PLUGIN_VALIDATION,
):
    """Persist lifecycle state without discarding the last validation by default."""
    current = plugin_state(db, plugin_id)
    if validation is _KEEP_PLUGIN_VALIDATION:
        validation = _validation_payload(current)
    validation = validation or {}
    ts = now()
    db.execute(
        """INSERT INTO plugin_states(plugin_id,enabled,status,validation_json,updated_at)
           VALUES(?,?,?,?,?)
           ON CONFLICT(plugin_id) DO UPDATE SET enabled=excluded.enabled,status=excluded.status,
               validation_json=excluded.validation_json,updated_at=excluded.updated_at""",
        (str(plugin_id), 1 if enabled else 0, str(status), json.dumps(validation, sort_keys=True), ts),
    )
    db.commit()


def is_enabled(db, plugin: Plugin):
    row = plugin_state(db, plugin.id)
    if row is None:
        return bool(plugin.manifest.get("enabled_by_default", False))
    return bool(row["enabled"])


def is_archived(db, plugin: Plugin):
    row = plugin_state(db, plugin.id)
    return bool(row is not None and str(row["status"]) == "archived")


def archive_plugin(db, plugin: Plugin):
    """Archive an installed plugin without erasing validation/provenance state."""
    set_plugin_state(db, plugin.id, enabled=False, status="archived")


def restore_plugin(db, plugin: Plugin):
    """Restore an archived plugin to the installed-but-disabled lifecycle state."""
    set_plugin_state(db, plugin.id, enabled=False, status="disabled")


def validation_freshness(db, plugin: Plugin):
    """Compare the last successful validation fingerprints with installed content."""
    row = plugin_state(db, plugin.id)
    payload = _validation_payload(row)
    saved = payload.get("validation_fingerprints")
    if not isinstance(saved, dict):
        # Compatibility with validations written before PLUGIN-R3.
        saved = payload.get("fingerprints")
    if not isinstance(saved, dict) or not saved:
        state = "invalid" if row is not None and str(row["status"]) == "invalid" else "unvalidated"
        return {
            "state": state,
            "fresh": None,
            "changed": (),
            "saved": saved if isinstance(saved, dict) else {},
            "current": {},
        }

    current = (
        plugin_validation_fingerprints(plugin)
        if "validation_fingerprints" in payload
        else plugin_fingerprints(plugin)
    )
    keys = sorted(set(saved) | set(current))
    changed = tuple(key for key in keys if saved.get(key) != current.get(key))
    fresh = not changed
    return {
        "state": "ready" if fresh else "needs_revalidation",
        "fresh": fresh,
        "changed": changed,
        "saved": saved,
        "current": current,
    }


def validate_plugin(plugin: Plugin, *, import_science=True):
    result = {
        "plugin_id": plugin.id, "plugin_name": plugin.name,
        "plugin_version": plugin.version,
        "plugin_type": plugin.plugin_type,
        "source_path": str(plugin.root),
        "manifest": "ok",
        "icon": plugin.icon,
        "fingerprints": plugin_fingerprints(plugin),
        "validation_fingerprints": plugin_validation_fingerprints(plugin),
    }
    if plugin.plugin_type == "extension":
        pages = []
        for page in plugin.extension_pages:
            module = load_extension(plugin, page)
            pages.append({"id": page.id, "label": page.label, "section": page.section,
                          "icon": page.icon, "entrypoint": str(page.entrypoint),
                          "render": callable(module.render)})
        result["pages"] = pages
        raw_menu = plugin.manifest.get("menu")
        raw_menu_rows = raw_menu if isinstance(raw_menu, list) else [raw_menu]
        deprecated_sections = []
        for raw_page in raw_menu_rows:
            if not isinstance(raw_page, dict):
                continue
            raw_section = str(raw_page.get("section") or "Analysis").strip().title()
            canonical_section = canonical_extension_section(raw_section)
            if raw_section != canonical_section:
                deprecated_sections.append({
                    "from": raw_section,
                    "to": canonical_section,
                })
        result["navigation_contract"] = {
            "api_version": EXTENSION_NAVIGATION_API_VERSION,
            "sections": sorted(EXTENSION_MENU_SECTIONS),
            "accepted_aliases": dict(EXTENSION_SECTION_ALIASES),
            "deprecated_aliases_used": deprecated_sections,
        }
        result["status"] = "ready"
        return result

    if plugin.plugin_type == "feature":
        from rank42.plugin_hooks import FEATURE_HOOK_ALIASES

        module = load_feature(plugin)
        result["hooks"] = list(plugin.feature_hooks)
        raw_hooks = [
            str(value)
            for value in (plugin.manifest.get("hooks") or ())
        ]
        deprecated_hooks = [
            {"from": hook_id, "to": FEATURE_HOOK_ALIASES[hook_id]}
            for hook_id in raw_hooks
            if hook_id in FEATURE_HOOK_ALIASES
        ]
        result["hook_contract"] = {
            "api_version": FEATURE_HOOK_API_VERSION,
            "hooks": list(plugin.feature_hooks),
            "accepted_aliases": dict(FEATURE_HOOK_ALIASES),
            "deprecated_aliases_used": deprecated_hooks,
        }
        result["system_privileges"] = sorted(plugin.system_privileges)
        result["functional_hooks"] = [
            {"name": h.name, "entrypoint": str(h.entrypoint), "function": h.function, "priority": h.priority}
            for h in plugin.feature_command_hooks
        ]
        result["entrypoint"] = str(plugin.feature_entrypoint)
        result["render"] = callable(module.render)
        result["status"] = "ready"
        return result

    result.update({"family_spec": plugin.family_spec,
                   "capabilities": sorted(plugin.capabilities),
                   "default_variant": plugin.default_variant_id})
    if import_science:
        from rank42.family_loader import load_family, family_generic_rank, family_source_sha256
        checked = []
        for variant in plugin.variants:
            family = load_family(variant.family_spec)
            rec = {"id": variant.id, "name": variant.name, "family_spec": variant.family_spec,
                   "family_name": family.name(), "family_generic_rank": family_generic_rank(family),
                   "family_sha256": family_source_sha256(variant.family_spec)}
            verified_lower = variant.verified_generic_rank_lower
            if verified_lower is not None:
                validator = getattr(family, "validate_generic_rank_claim", None)
                if not callable(validator):
                    raise ValueError(
                        f"variant {variant.id}: verified_generic_rank_lower requires "
                        "validate_generic_rank_claim()"
                    )
                certificate = validator()
                if not isinstance(certificate, dict):
                    raise ValueError(
                        f"variant {variant.id}: validate_generic_rank_claim() must return an object"
                    )
                if certificate.get("verified") is not True:
                    raise ValueError(
                        f"variant {variant.id}: generic-rank verification certificate did not verify"
                    )
                lower = _optional_nonnegative_int(
                    certificate.get("lower_bound"), field="lower_bound",
                    where=f"variant {variant.id} certificate",
                )
                if lower is None or lower < verified_lower:
                    raise ValueError(
                        f"variant {variant.id}: certificate lower_bound must be >= "
                        "verified_generic_rank_lower"
                    )
                rec["generic_rank_certificate"] = dict(certificate)
            if variant.family_spec.startswith("json:") and hasattr(family, "validate_symbolically"):
                rec["symbolic_validation"] = family.validate_symbolically()
            sample = variant_value(plugin, variant, "validation_parameter", "1")
            E = family.curve(sample)
            if E is None:
                raise ValueError(f"variant {variant.id}: validation specialization t={sample} is undefined or singular")
            rec["validation_parameter"] = str(sample)
            rec["validation_discriminant_nonzero"] = bool(E.discriminant() != 0)
            declared_torsion = variant_torsion_groups(plugin, variant)
            if declared_torsion:
                from rank42.torsion import compute_torsion_data
                exact_torsion = str(compute_torsion_data(E)["torsion_label"])
                rec["validation_torsion"] = exact_torsion
                if exact_torsion not in declared_torsion:
                    raise ValueError(
                        f"variant {variant.id}: validation specialization has exact torsion "
                        f"{exact_torsion}, outside declared torsion_groups "
                        f"{list(declared_torsion)!r}"
                    )
                role = variant_torsion_provider_role(plugin, variant)
                if role == "canonical_universal" and len(declared_torsion) != 1:
                    raise ValueError(
                        f"variant {variant.id}: canonical_universal provider must declare "
                        "exactly one torsion group"
                    )
            checked.append(rec)
        result["variants"] = checked
        if plugin.charts:
            result["charts"] = [{
                "id": c.id, "label": c.label, "variant_id": c.variant_id,
                "native_variant_id": c.native_variant_id, "native_family_spec": c.native_family_spec,
                "native_family_key": c.native_family_key, "matrix": list(c.matrix),
                "pgl2_fingerprint": c.fingerprint,
            } for c in plugin.charts]
        if len(checked) == 1:
            result.update({k: v for k, v in checked[0].items() if k in {
                "family_name", "family_generic_rank", "symbolic_validation",
                "validation_parameter", "validation_discriminant_nonzero", "family_sha256"}})
    adapter = load_adapter(plugin)
    if adapter is not None:
        adapter_contract = validate_family_adapter_v1(
            adapter,
            capabilities=plugin.capabilities,
        )
        result["adapter_contract"] = adapter_contract
        result["research_hooks"] = dict(
            adapter_contract["optional_hooks"]
        )
        variant_schemas = {}
        for variant in plugin.variants:
            variant_where = f"plugin {plugin.id} variant {variant.id}"
            family_schema = search_option_schema_for_variant(
                plugin,
                variant,
                adapter,
                context="family",
            )
            target_schema = search_option_schema_for_variant(
                plugin,
                variant,
                adapter,
                context="target",
            )
            _validate_effective_search_presets(
                plugin,
                family_schema=family_schema,
                target_schema=target_schema,
                variant=variant,
            )
            variant_schemas[str(variant.id)] = {
                "family": [dict(rec) for rec in family_schema],
                "target": [dict(rec) for rec in target_schema],
            }
        default_schemas = variant_schemas[str(plugin.default_variant_id)]
        # Preserve the historical adapter_options field as the default
        # variant's Family schema while exposing exact variant schemas.
        result["adapter_options"] = list(default_schemas["family"])
        result["adapter_option_schemas"] = {
            "family": list(default_schemas["family"]),
            "target": list(default_schemas["target"]),
        }
        result["adapter_option_schemas_by_variant"] = variant_schemas
    elif {"family_search", "target_search"} & plugin.capabilities:
        raise ValueError("search capability declared but search_adapter is missing")
    else:
        _validate_effective_search_presets(
            plugin,
            family_schema=(),
            target_schema=(),
        )
    result["status"] = "ready"
    return result
