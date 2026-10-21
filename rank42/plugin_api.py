"""Versioned public Plugin SDK contracts.

FamilyAdapterV1 is the stable v0.9.1 boundary between Rank Hunter core and
Family search adapters. Existing module-style adapters are supported through
LegacyFamilyAdapterV1; new adapters may export FAMILY_ADAPTER as a
FamilyAdapterV1 instance.

The SDK owns interface shape only. Manifest/variant metadata owns preset
identity/content, while adapter implementations own option schemas and
family-specific command/math behavior.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import Any, Mapping, Sequence

from rank42.plugin_hooks import (
    FEATURE_HOOK_ALIASES,
    FEATURE_HOOK_API_VERSION,
    PUBLIC_FEATURE_HOOKS,
    canonical_feature_hook,
    feature_hook_deprecated_alias,
)
from rank42.plugin_navigation import (
    EXTENSION_MENU_SECTIONS,
    EXTENSION_NAVIGATION_API_VERSION,
    EXTENSION_SECTION_ALIASES,
    canonical_extension_section,
)

PLUGIN_SDK_VERSION = 1
FAMILY_ADAPTER_API = "FamilyAdapterV1"
FAMILY_ADAPTER_API_VERSION = 1
EXTENSION_CONTEXT_API_VERSION = 1
FEATURE_CONTEXT_API_VERSION = 1

__all__ = [
    "PLUGIN_SDK_VERSION",
    "FAMILY_ADAPTER_API",
    "FAMILY_ADAPTER_API_VERSION",
    "EXTENSION_CONTEXT_API_VERSION",
    "FEATURE_CONTEXT_API_VERSION",
    "FEATURE_HOOK_API_VERSION",
    "EXTENSION_NAVIGATION_API_VERSION",
    "PUBLIC_FEATURE_HOOKS",
    "FEATURE_HOOK_ALIASES",
    "EXTENSION_MENU_SECTIONS",
    "EXTENSION_SECTION_ALIASES",
    "canonical_feature_hook",
    "feature_hook_deprecated_alias",
    "canonical_extension_section",
    "FamilyAdapterV1",
    "LegacyFamilyAdapterV1",
    "ExtensionContextV1",
    "FeatureContextV1",
    "coerce_family_adapter_v1",
    "validate_family_adapter_v1",
    "family_adapter_search_options",
]

FAMILY_ADAPTER_CAPABILITY_METHODS = {
    "family_search": "build_family_search_command",
    "target_search": "build_target_search_command",
}

FAMILY_ADAPTER_OPTION_CONTEXTS = frozenset({"family", "target"})

OPTIONAL_FAMILY_ADAPTER_HOOKS = frozenset({
    "derive_pipeline_coverings",
    "derive_pipeline_transform",
    "run_pipeline_higher_descent",
    "run_pipeline_padic_covering_search",
})


@dataclass(frozen=True)
class ExtensionContextV1:
    """Versioned public facade passed to Extension render(context)."""

    project_root: Path
    db_path: Path
    db: object
    plugin_id: str
    plugin_version: str
    page_id: str
    page_label: str

    api_name = "ExtensionContextV1"
    api_version = EXTENSION_CONTEXT_API_VERSION

    @property
    def science_python(self):
        from rank42.settings_registry import setting_value
        return setting_value(self.db, "science_python", "")

    def setting(self, key, default=None):
        from rank42.settings_registry import SETTING_SPECS, setting_value
        from rank42.ui_store import get_setting

        name = str(key)
        if name in SETTING_SPECS:
            return setting_value(self.db, name, default)
        return get_setting(self.db, name, default)


@dataclass(frozen=True)
class FeatureContextV1:
    """Versioned public facade passed to Feature render(context)."""

    project_root: Path
    db_path: Path
    db: object
    plugin_id: str
    plugin_version: str
    hook_id: str
    payload: MappingProxyType

    api_name = "FeatureContextV1"
    api_version = FEATURE_CONTEXT_API_VERSION

    @property
    def science_python(self):
        from rank42.settings_registry import setting_value
        return setting_value(self.db, "science_python", "")

    def setting(self, key, default=None):
        from rank42.settings_registry import SETTING_SPECS, setting_value
        from rank42.ui_store import get_setting

        name = str(key)
        if name in SETTING_SPECS:
            return setting_value(self.db, name, default)
        return get_setting(self.db, name, default)

    def get(self, key, default=None):
        return self.payload.get(key, default)


class FamilyAdapterV1:
    """Public v1 base class for Family adapters."""

    api_name = FAMILY_ADAPTER_API
    api_version = FAMILY_ADAPTER_API_VERSION
    compatibility_mode = "sdk-v1"

    def search_options(
        self,
        *,
        context: str = "family",
        variant=None,
        family_definition=None,
    ) -> Sequence[Mapping[str, Any]]:
        if context not in FAMILY_ADAPTER_OPTION_CONTEXTS:
            raise ValueError(
                "FamilyAdapterV1 search_options context must be one of "
                + repr(sorted(FAMILY_ADAPTER_OPTION_CONTEXTS))
            )
        return ()

    def build_family_search_command(
        self,
        *,
        python,
        db,
        family_spec,
        options,
    ):
        raise NotImplementedError(
            "FamilyAdapterV1 family_search capability is not implemented"
        )

    def build_target_search_command(
        self,
        *,
        python,
        db,
        curve_id,
        options,
    ):
        raise NotImplementedError(
            "FamilyAdapterV1 target_search capability is not implemented"
        )

    def implements(self, method_name: str) -> bool:
        method_name = str(method_name)
        base = getattr(FamilyAdapterV1, method_name, None)
        current = getattr(type(self), method_name, None)
        if base is not None and current is not None:
            return current is not base
        return callable(getattr(self, method_name, None))

    def optional_hook(self, name: str):
        name = str(name)
        if name not in OPTIONAL_FAMILY_ADAPTER_HOOKS:
            return None
        callback = getattr(self, name, None)
        return callback if callable(callback) else None


class LegacyFamilyAdapterV1(FamilyAdapterV1):
    """Compatibility facade for pre-SDK module-style adapters."""

    compatibility_mode = "legacy-module"

    def __init__(self, module: ModuleType, *, plugin_id: str):
        self._module = module
        self.plugin_id = str(plugin_id)

    @property
    def legacy_module(self) -> ModuleType:
        return self._module

    def _callback(self, name: str):
        callback = getattr(self._module, str(name), None)
        return callback if callable(callback) else None

    def implements(self, method_name: str) -> bool:
        if method_name == "search_options":
            return True
        return self._callback(method_name) is not None

    def search_options(
        self,
        *,
        context: str = "family",
        variant=None,
        family_definition=None,
    ):
        if context not in FAMILY_ADAPTER_OPTION_CONTEXTS:
            raise ValueError(
                "FamilyAdapterV1 search_options context must be one of "
                + repr(sorted(FAMILY_ADAPTER_OPTION_CONTEXTS))
            )
        provider = self._callback("search_options")
        if provider is None:
            return ()
        try:
            signature = inspect.signature(provider)
        except (TypeError, ValueError):
            return provider(context=context)
        params = signature.parameters
        accepts_context = (
            "context" in params
            or any(
                param.kind == inspect.Parameter.VAR_KEYWORD
                for param in params.values()
            )
        )
        kwargs = {}
        if accepts_context:
            kwargs["context"] = context
        accepts_kwargs = any(
            param.kind == inspect.Parameter.VAR_KEYWORD
            for param in params.values()
        )
        if "variant" in params or accepts_kwargs:
            kwargs["variant"] = variant
        elif "variant_id" in params:
            kwargs["variant_id"] = variant
        if "family_definition" in params or accepts_kwargs:
            kwargs["family_definition"] = dict(family_definition or {})
        return provider(**kwargs)

    def build_family_search_command(self, **kwargs):
        callback = self._callback("build_family_search_command")
        if callback is None:
            return super().build_family_search_command(**kwargs)
        return callback(**kwargs)

    def build_target_search_command(self, **kwargs):
        callback = self._callback("build_target_search_command")
        if callback is None:
            return super().build_target_search_command(**kwargs)
        return callback(**kwargs)

    def optional_hook(self, name: str):
        if name not in OPTIONAL_FAMILY_ADAPTER_HOOKS:
            return None
        return self._callback(name)

    def __getattr__(self, name: str):
        return getattr(self._module, name)


def family_adapter_search_options(
    adapter,
    *,
    context="family",
    variant=None,
    family_definition=None,
):
    """Call one adapter option provider with backward-compatible kwargs."""
    if not isinstance(adapter, FamilyAdapterV1):
        raise TypeError("adapter is not a FamilyAdapterV1")
    getter = getattr(adapter, "search_options")
    try:
        signature = inspect.signature(getter)
    except (TypeError, ValueError):
        return getter(context=context)
    params = signature.parameters
    accepts_kwargs = any(
        param.kind == inspect.Parameter.VAR_KEYWORD
        for param in params.values()
    )
    kwargs = {}
    if "context" in params or accepts_kwargs:
        kwargs["context"] = context
    if "variant" in params or accepts_kwargs:
        kwargs["variant"] = variant
    elif "variant_id" in params:
        kwargs["variant_id"] = variant
    if "family_definition" in params or accepts_kwargs:
        kwargs["family_definition"] = dict(family_definition or {})
    return getter(**kwargs)


def coerce_family_adapter_v1(raw, *, plugin_id: str) -> FamilyAdapterV1:
    """Return one SDK facade from a native object or legacy module."""
    if isinstance(raw, FamilyAdapterV1):
        if int(getattr(raw, "api_version", 0) or 0) != FAMILY_ADAPTER_API_VERSION:
            raise ValueError(
                f"plugin {plugin_id}: unsupported Family adapter API version "
                f"{getattr(raw, 'api_version', None)!r}"
            )
        return raw
    if isinstance(raw, ModuleType):
        exported = getattr(raw, "FAMILY_ADAPTER", None)
        if exported is not None:
            if not isinstance(exported, FamilyAdapterV1):
                raise ValueError(
                    f"plugin {plugin_id}: FAMILY_ADAPTER must be a "
                    "FamilyAdapterV1 instance"
                )
            if int(getattr(exported, "api_version", 0) or 0) != FAMILY_ADAPTER_API_VERSION:
                raise ValueError(
                    f"plugin {plugin_id}: unsupported Family adapter API version "
                    f"{getattr(exported, 'api_version', None)!r}"
                )
            return exported
        return LegacyFamilyAdapterV1(raw, plugin_id=plugin_id)
    raise TypeError(
        f"plugin {plugin_id}: Family adapter must be a module or "
        "FamilyAdapterV1 instance"
    )


def validate_family_adapter_v1(adapter: FamilyAdapterV1, *, capabilities=()):
    """Validate required V1 methods for declared plugin capabilities."""
    if not isinstance(adapter, FamilyAdapterV1):
        raise TypeError("adapter is not a FamilyAdapterV1")
    capability_set = set(capabilities or ())
    for capability, method_name in FAMILY_ADAPTER_CAPABILITY_METHODS.items():
        if capability in capability_set and not adapter.implements(method_name):
            raise ValueError(
                f"capability {capability} requires FamilyAdapterV1 method "
                f"{method_name}"
            )
    return {
        "api": adapter.api_name,
        "api_version": int(adapter.api_version),
        "compatibility_mode": str(adapter.compatibility_mode),
        "required_methods": {
            capability: method_name
            for capability, method_name in FAMILY_ADAPTER_CAPABILITY_METHODS.items()
            if capability in capability_set
        },
        "optional_hooks": {
            name: adapter.optional_hook(name) is not None
            for name in sorted(OPTIONAL_FAMILY_ADAPTER_HOOKS)
        },
    }
