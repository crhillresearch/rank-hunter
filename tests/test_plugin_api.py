import json

import pytest

from rank42.plugin_api import (
    FAMILY_ADAPTER_API,
    FAMILY_ADAPTER_API_VERSION,
    FEATURE_HOOK_API_VERSION,
    EXTENSION_NAVIGATION_API_VERSION,
    EXTENSION_MENU_SECTIONS,
    FEATURE_HOOK_ALIASES,
    PLUGIN_SDK_VERSION,
    canonical_extension_section,
    canonical_feature_hook,
    ExtensionContextV1,
    FamilyAdapterV1,
    FeatureContextV1,
    LegacyFamilyAdapterV1,
    coerce_family_adapter_v1,
    validate_family_adapter_v1,
)
from rank42.plugins import load_adapter, read_plugin, validate_plugin


def _write(path, name, content):
    (path / name).write_text(content, encoding="utf-8")


def _family_plugin(tmp_path, *, pid, adapter_source, capabilities):
    root = tmp_path / pid
    root.mkdir()
    _write(root, "family.json", "{}")
    _write(root, "adapter.py", adapter_source)
    _write(
        root,
        "plugin.json",
        json.dumps({
            "schema_version": 1,
            "id": pid,
            "name": pid,
            "version": "1.0.0",
            "family": {"kind": "json", "file": "family.json"},
            "search_adapter": "adapter.py",
            "capabilities": list(capabilities),
        }),
    )
    return read_plugin(root)


def test_family_adapter_v1_public_version_constants_are_frozen():
    assert PLUGIN_SDK_VERSION == 1
    assert FAMILY_ADAPTER_API == "FamilyAdapterV1"
    assert FAMILY_ADAPTER_API_VERSION == 1


def test_public_hook_and_navigation_contract_versions_are_frozen():
    assert FEATURE_HOOK_API_VERSION == 1
    assert EXTENSION_NAVIGATION_API_VERSION == 1
    assert canonical_feature_hook("results.after_header") == "manage.jobs.results.after_header"
    assert canonical_feature_hook("external_catalog.after_header") == "data.catalogs.after_header"
    assert canonical_feature_hook("analysis.analyze.after_header") == "analysis.work_center.after_header"
    assert canonical_feature_hook("analysis.lattices.after_header") == "analysis.mw_geometry.after_header"
    assert FEATURE_HOOK_ALIASES["results.after_header"] == "manage.jobs.results.after_header"
    assert canonical_extension_section("Resources") == "Plugins"
    assert "Candidates" not in EXTENSION_MENU_SECTIONS
    assert "System" not in EXTENSION_MENU_SECTIONS


def test_legacy_module_is_wrapped_as_family_adapter_v1(tmp_path):
    plugin = _family_plugin(
        tmp_path,
        pid="legacy_adapter",
        capabilities=("family_search", "target_search"),
        adapter_source=(
            "def search_options(context='family'):\n"
            "    return [{'key':'limit2','type':'int','default':2,'min':1}]\n"
            "def build_family_search_command(**kwargs): return ['legacy-family']\n"
            "def build_target_search_command(**kwargs): return ['legacy-target']\n"
            "def derive_pipeline_coverings(payload): return []\n"
            "def derive_pipeline_transform(payload): return {'status':'ok'}\n"
        ),
    )

    adapter = load_adapter(plugin)

    assert isinstance(adapter, FamilyAdapterV1)
    assert isinstance(adapter, LegacyFamilyAdapterV1)
    assert adapter.compatibility_mode == "legacy-module"
    assert adapter.search_options(context="family")[0]["key"] == "limit2"
    assert adapter.build_family_search_command(
        python="python",
        db="db",
        family_spec="family",
        options={},
    ) == ["legacy-family"]
    assert adapter.build_target_search_command(
        python="python",
        db="db",
        curve_id=7,
        options={},
    ) == ["legacy-target"]
    contract = validate_family_adapter_v1(
        adapter,
        capabilities=plugin.capabilities,
    )
    assert contract["api"] == "FamilyAdapterV1"
    assert contract["api_version"] == 1
    assert contract["compatibility_mode"] == "legacy-module"
    assert contract["optional_hooks"]["derive_pipeline_coverings"] is True
    assert contract["optional_hooks"]["derive_pipeline_transform"] is True


def test_native_family_adapter_v1_export_is_loaded_directly(tmp_path):
    plugin = _family_plugin(
        tmp_path,
        pid="native_adapter",
        capabilities=("family_search", "target_search"),
        adapter_source=(
            "from rank42.plugin_api import FamilyAdapterV1\n"
            "class NativeAdapter(FamilyAdapterV1):\n"
            "    def search_options(self, *, context='family'):\n"
            "        if context == 'target':\n"
            "            return [{'key':'depth','type':'int','default':3,'min':1}]\n"
            "        return [{'key':'width','type':'int','default':2,'min':1}]\n"
            "    def build_family_search_command(self, **kwargs):\n"
            "        return ['native-family']\n"
            "    def build_target_search_command(self, **kwargs):\n"
            "        return ['native-target']\n"
            "FAMILY_ADAPTER = NativeAdapter()\n"
        ),
    )

    adapter = load_adapter(plugin)
    result = validate_plugin(plugin, import_science=False)

    assert isinstance(adapter, FamilyAdapterV1)
    assert not isinstance(adapter, LegacyFamilyAdapterV1)
    assert adapter.compatibility_mode == "sdk-v1"
    assert adapter.build_family_search_command(
        python="python",
        db="db",
        family_spec="family",
        options={},
    ) == ["native-family"]
    assert adapter.build_target_search_command(
        python="python",
        db="db",
        curve_id=7,
        options={},
    ) == ["native-target"]
    assert result["adapter_contract"] == {
        "api": "FamilyAdapterV1",
        "api_version": 1,
        "compatibility_mode": "sdk-v1",
        "required_methods": {
            "family_search": "build_family_search_command",
            "target_search": "build_target_search_command",
        },
        "optional_hooks": {
            "derive_pipeline_coverings": False,
            "derive_pipeline_transform": False,
            "run_pipeline_higher_descent": False,
            "run_pipeline_padic_covering_search": False,
        },
    }
    assert result["adapter_option_schemas"]["family"][0]["key"] == "width"
    assert result["adapter_option_schemas"]["target"][0]["key"] == "depth"


@pytest.mark.parametrize(
    "capability,method",
    [
        ("family_search", "build_family_search_command"),
        ("target_search", "build_target_search_command"),
    ],
)
def test_declared_capability_requires_family_adapter_v1_method(
    tmp_path,
    capability,
    method,
):
    plugin = _family_plugin(
        tmp_path,
        pid=f"missing_{capability}",
        capabilities=(capability,),
        adapter_source=(
            "def search_options(context='family'): return []\n"
        ),
    )

    with pytest.raises(
        ValueError,
        match=rf"capability {capability} requires FamilyAdapterV1 method {method}",
    ):
        validate_plugin(plugin, import_science=False)


def test_native_family_adapter_rejects_unsupported_api_version(tmp_path):
    plugin = _family_plugin(
        tmp_path,
        pid="future_adapter",
        capabilities=(),
        adapter_source=(
            "from rank42.plugin_api import FamilyAdapterV1\n"
            "class FutureAdapter(FamilyAdapterV1):\n"
            "    api_version = 2\n"
            "FAMILY_ADAPTER = FutureAdapter()\n"
        ),
    )

    with pytest.raises(
        ValueError,
        match="unsupported Family adapter API version 2",
    ):
        load_adapter(plugin)


def test_family_adapter_export_must_be_sdk_instance(tmp_path):
    plugin = _family_plugin(
        tmp_path,
        pid="bad_export",
        capabilities=(),
        adapter_source="FAMILY_ADAPTER = object()\n",
    )

    with pytest.raises(
        ValueError,
        match="FAMILY_ADAPTER must be a FamilyAdapterV1 instance",
    ):
        load_adapter(plugin)


def test_legacy_no_context_search_options_remain_v1_compatible(tmp_path):
    plugin = _family_plugin(
        tmp_path,
        pid="legacy_no_context_v1",
        capabilities=("family_search",),
        adapter_source=(
            "def search_options():\n"
            "    return [{'key':'timeout','type':'int','default':10,'min':1}]\n"
            "def build_family_search_command(**kwargs): return ['family']\n"
        ),
    )

    adapter = load_adapter(plugin)

    assert adapter.search_options(context="family") == (
        adapter.search_options(context="target")
    )
    result = validate_plugin(plugin, import_science=False)
    assert result["adapter_contract"]["compatibility_mode"] == "legacy-module"


def test_coerce_family_adapter_v1_rejects_arbitrary_objects():
    with pytest.raises(
        TypeError,
        match="Family adapter must be a module or FamilyAdapterV1 instance",
    ):
        coerce_family_adapter_v1(object(), plugin_id="bad")



def test_versioned_feature_and_extension_context_facades_keep_legacy_aliases(
    tmp_path,
):
    from types import MappingProxyType

    from rank42.feature_hooks import FeatureContext
    from rank42.ui_extensions import ExtensionContext

    assert FeatureContext is FeatureContextV1
    assert ExtensionContext is ExtensionContextV1
    assert FeatureContextV1.api_name == "FeatureContextV1"
    assert FeatureContextV1.api_version == 1
    assert ExtensionContextV1.api_name == "ExtensionContextV1"
    assert ExtensionContextV1.api_version == 1

    feature = FeatureContextV1(
        project_root=tmp_path,
        db_path=tmp_path / "rank42.db",
        db=object(),
        plugin_id="feature",
        plugin_version="1",
        hook_id="curves.actions",
        payload=MappingProxyType({"curve_id": 7}),
    )
    extension = ExtensionContextV1(
        project_root=tmp_path,
        db_path=tmp_path / "rank42.db",
        db=object(),
        plugin_id="extension",
        plugin_version="1",
        page_id="workspace",
        page_label="Workspace",
    )
    assert feature.get("curve_id") == 7
    assert feature.get("missing", 9) == 9
    assert extension.page_id == "workspace"


def test_core_option_validation_calls_family_adapter_v1_not_signature_introspection():
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    source = (root / "rank42" / "plugins.py").read_text(encoding="utf-8")

    start = source.index("def _adapter_search_options")
    end = source.index("def _validate_search_option_value", start)
    block = source[start:end]
    assert "isinstance(adapter, FamilyAdapterV1)" in block
    assert "family_adapter_search_options(" in block
    assert "variant=variant" in block
    assert "family_definition=family_definition" in block
    assert "inspect.signature" not in block


def test_family_and_target_paths_consume_loaded_v1_adapter_facade():
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    family = (
        root / "rank42" / "ui_pages" / "family_search.py"
    ).read_text(encoding="utf-8")
    target = (
        root / "rank42" / "ui_pages" / "target_curve.py"
    ).read_text(encoding="utf-8")
    geometry = (
        root / "rank42" / "search_plugin_geometry.py"
    ).read_text(encoding="utf-8")

    assert "adapter=load_adapter(plugin)" in family.replace(" ", "")
    assert "adapter=load_adapter(plugin)" in target.replace(" ", "")
    assert "adapter_search_option_defs(" in family
    assert 'context="family"' in family
    assert "adapter_search_option_defs(" in target
    assert "context='target'" in target
    family_host = (
        root / "rank42" / "search_plugin_family.py"
    ).read_text(encoding="utf-8")

    # SEARCH-R7 ownership boundary: neither Search page executes adapter
    # commands directly. Pipeline-owned hosts invoke the V1 builders behind
    # temporary-DB scientific-write boundaries.
    assert "adapter.build_family_search_command(" not in family
    assert 'getattr(adapter, "build_family_search_command", None)' in family_host
    assert "command = builder(" in family_host

    # SEARCH-R6 ownership boundary for Target remains unchanged.
    assert "adapter.build_target_search_command(" not in target
    assert 'getattr(adapter, "build_target_search_command", None)' in geometry
    assert "command = builder(" in geometry



def test_pipeline_capability_discovery_uses_versioned_optional_hook_registry():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "rank42" / "pipeline_runner.py").read_text(encoding="utf-8")

    start = source.index("def _runtime_hunt_capabilities")
    end = source.index("def _append_skipped_strategy_step", start)
    block = source[start:end]

    assert '"derive_pipeline_transform": "pipeline_transform_hook"' in block
    assert "adapter.optional_hook(hook_name)" in block
    assert "getattr(adapter, hook_name" not in block
