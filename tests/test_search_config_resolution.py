from pathlib import Path

from rank42.plugin_api import FamilyAdapterV1
from rank42.plugins import (
    Plugin,
    PluginVariant,
    _validate_manifest_search_options,
)
from rank42.search_config import (
    adapter_search_option_defs,
    pipeline_search_config,
    resolve_plugin_search_config,
    search_preset_provenance,
)


def _plugin():
    parent = {
        "name": "Demo",
        "search_presets": [
            {
                "id": "scan",
                "label": "Scan",
                "family": {"shared": 5},
                "target": {"shared": 7},
            },
        ],
    }
    v1_manifest = {
        "id": "v1",
        "family": {"kind": "module", "spec": "demo:v1"},
        "search_presets": [
            {
                "id": "scan",
                "label": "Scan",
                "family": {"v1_only": 11, "limit": 12},
                "target": {"v1_only": 13},
            },
        ],
    }
    v2_manifest = {
        "id": "v2",
        "family": {"kind": "module", "spec": "demo:v2"},
        "search_presets": [
            {
                "id": "scan",
                "label": "Scan",
                "family": {"v2_only": 21, "limit": 22},
                "target": {"v2_only": 23},
            },
        ],
    }
    v1 = PluginVariant("v1", "V1", "demo:v1", v1_manifest, parent)
    v2 = PluginVariant("v2", "V2", "demo:v2", v2_manifest, parent)
    return Plugin(
        id="demo",
        name="Demo",
        version="1.2.3",
        plugin_type="family",
        root=Path("."),
        family_spec=v1.family_spec,
        manifest=parent,
        capabilities=frozenset({"family_search", "target_search"}),
        adapter_path=None,
        variants=(v1, v2),
        default_variant_id="v1",
    ), v1, v2


class VariantAwareAdapter(FamilyAdapterV1):
    def search_options(
        self,
        *,
        context="family",
        variant=None,
        family_definition=None,
    ):
        assert family_definition["spec"] == f"demo:{variant}"
        if variant == "v1":
            return [
                {"key": "v1_only", "type": "int", "default": 1},
                {"key": "shared", "type": "int", "default": 2},
            ]
        return [
            {"key": "v2_only", "type": "int", "default": 3},
            {"key": "shared", "type": "int", "default": 4},
        ]


class OldStyleAdapter(FamilyAdapterV1):
    def search_options(self, *, context="family"):
        return [{"key": "legacy", "type": "int", "default": 9}]


def test_pipeline_search_config_merges_plugin_and_variant_without_legacy_auto():
    plugin, v1, v2 = _plugin()
    plugin.manifest["pipeline_search"] = {
        "target_rank": 20,
        "certificate_timeout": 120,
        "exact_candidates": 48,
    }
    v1.manifest["pipeline_search"] = {
        "target_rank": 31,
        "exact_candidates": 96,
    }
    v2.manifest["pipeline_search"] = {
        "recommended_preset": "geometry_grinder",
    }

    first = pipeline_search_config(plugin, v1)
    second = pipeline_search_config(plugin, v2)

    assert first == {
        "target_rank": 31,
        "certificate_timeout": 120,
        "exact_candidates": 96,
    }
    assert second == {
        "target_rank": 20,
        "certificate_timeout": 120,
        "exact_candidates": 48,
        "recommended_preset": "geometry_grinder",
    }


def test_manifest_search_options_override_adapter_metadata_by_context():
    plugin, v1, _ = _plugin()
    plugin.manifest["search_options"] = {
        "family": [
            {"key": "manifest_only", "type": "int", "default": 17},
        ],
    }

    class ShouldNotRun(FamilyAdapterV1):
        def search_options(self, **kwargs):
            raise AssertionError("adapter metadata should not run for declared context")

    family_defs = adapter_search_option_defs(
        plugin,
        v1,
        ShouldNotRun(),
        context="family",
    )
    assert family_defs == (
        {"key": "manifest_only", "type": "int", "default": 17},
    )


def test_manifest_search_options_do_not_require_adapter_metadata():
    plugin, v1, _ = _plugin()
    plugin.manifest["search_options"] = {
        "family": [
            {"key": "manifest_only", "type": "int", "default": 17},
        ],
    }

    defs = adapter_search_option_defs(
        plugin,
        v1,
        None,
        context="family",
    )

    assert defs == (
        {"key": "manifest_only", "type": "int", "default": 17},
    )


def test_manifest_search_options_fall_back_per_context_and_variant():
    plugin, v1, v2 = _plugin()
    plugin.manifest["search_options"] = {
        "family": [
            {"key": "shared_manifest", "type": "bool", "default": True},
        ],
    }
    v2.manifest["search_options"] = {
        "family": [
            {"key": "v2_manifest", "type": "int", "default": 22},
        ],
    }
    adapter = VariantAwareAdapter()

    assert {rec["key"] for rec in adapter_search_option_defs(
        plugin, v1, adapter, context="family"
    )} == {"shared_manifest"}
    assert {rec["key"] for rec in adapter_search_option_defs(
        plugin, v2, adapter, context="family"
    )} == {"v2_manifest"}
    assert {rec["key"] for rec in adapter_search_option_defs(
        plugin, v1, adapter, context="target"
    )} == {"v1_only", "shared"}


def test_manifest_search_options_validation_is_strict():
    _validate_manifest_search_options(
        {
            "family": [
                {"key": "quick", "type": "bool", "default": True},
            ],
            "target": [],
        },
        where="demo",
    )
    import pytest

    with pytest.raises(ValueError, match="unknown contexts"):
        _validate_manifest_search_options(
            {"curve": []},
            where="demo",
        )
    with pytest.raises(ValueError, match="unsupported type"):
        _validate_manifest_search_options(
            {"family": [{"key": "bad", "type": "float", "default": 1.0}]},
            where="demo",
        )
    with pytest.raises(ValueError, match="orchestration keys"):
        _validate_manifest_search_options(
            {"family": [{"key": "target_lower", "type": "int", "default": 31}]},
            where="demo",
        )
    with pytest.raises(ValueError, match="orchestration keys"):
        _validate_manifest_search_options(
            {"family": [{"key": "limit", "type": "int", "default": 20}]},
            where="demo",
        )


def test_variant_aware_option_schema_does_not_leak_between_variants():
    plugin, v1, v2 = _plugin()
    adapter = VariantAwareAdapter()

    v1_defs = adapter_search_option_defs(
        plugin, v1, adapter, context="family"
    )
    v2_defs = adapter_search_option_defs(
        plugin, v2, adapter, context="family"
    )

    assert {rec["key"] for rec in v1_defs} == {"v1_only", "shared"}
    assert {rec["key"] for rec in v2_defs} == {"v2_only", "shared"}


def test_legacy_context_only_adapter_remains_compatible():
    plugin, v1, _ = _plugin()

    defs = adapter_search_option_defs(
        plugin, v1, OldStyleAdapter(), context="target"
    )

    assert defs == ({"key": "legacy", "type": "int", "default": 9},)


def test_profile_resolution_is_deterministic_and_freezes_variant_identity():
    plugin, v1, _ = _plugin()
    adapter = VariantAwareAdapter()

    one = resolve_plugin_search_config(
        plugin,
        v1,
        adapter,
        context="family",
        preset_id="scan",
        overrides={"shared": 99, "ratpoints_backend": "GPU"},
    )
    two = resolve_plugin_search_config(
        plugin,
        v1,
        adapter,
        context="family",
        preset_id="scan",
        overrides={"ratpoints_backend": "GPU", "shared": 99},
    )

    assert one["plugin_id"] == "demo"
    assert one["plugin_version"] == "1.2.3"
    assert one["variant_id"] == "v1"
    assert one["family_spec"] == "demo:v1"
    assert one["preset_id"] == "scan"
    assert one["options"] == {"v1_only": 11, "shared": 99}
    assert one["controls"] == {"limit": 12, "ratpoints_backend": "GPU"}
    assert one["config_hash"] == two["config_hash"]


def test_different_variant_profile_produces_different_frozen_config():
    plugin, v1, v2 = _plugin()
    adapter = VariantAwareAdapter()

    first = resolve_plugin_search_config(
        plugin, v1, adapter, context="family", preset_id="scan"
    )
    second = resolve_plugin_search_config(
        plugin, v2, adapter, context="family", preset_id="scan"
    )

    assert first["options"] == {"v1_only": 11, "shared": 2}
    assert second["options"] == {"v2_only": 21, "shared": 4}
    assert first["config_hash"] != second["config_hash"]



def test_search_preset_provenance_freezes_identity_label_settings_and_modified():
    plugin, v1, _ = _plugin()

    provenance = search_preset_provenance(
        plugin,
        v1,
        context="family",
        preset_id="scan",
        effective_settings={
            "v1_only": 11,
            "limit": 20,
            "ratpoints_backend": "GPU",
        },
        modified=True,
    )

    assert provenance == {
        "plugin_id": "demo",
        "plugin_version": "1.2.3",
        "variant_id": "v1",
        "context": "family",
        "preset_id": "scan",
        "preset_label": "Scan",
        "modified": True,
        "effective_settings": {
            "v1_only": 11,
            "limit": 20,
            "ratpoints_backend": "GPU",
        },
    }
    assert search_preset_provenance(
        plugin,
        v1,
        context="family",
        preset_id=None,
        effective_settings={},
        modified=False,
    ) is None
