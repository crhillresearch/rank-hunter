"""Typed settings inventory and compatibility resolver.

SYSTEM-12/13 boundary: ``ui_settings`` now contains only user/runtime
configuration. Application state lives in ``ui_application_state`` and
ephemeral dispatcher heartbeat state lives in its dedicated service table.
Runtime executable health/probing is owned by rank42.runtime_validation.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any


USER_SETTING = "user_setting"
RUNTIME_DEFAULT = "runtime_default"
SETTING_SCOPES = frozenset({
    USER_SETTING,
    RUNTIME_DEFAULT,
})
GLOBAL_TIME_BUDGET_KEYS = frozenset({
    "pari_rank_timeout",
    "mwrank_rank_timeout",
    "deep_cert_timeout",
})
_MISSING = object()


@dataclass(frozen=True)
class SettingSpec:
    key: str
    value_types: tuple[type, ...]
    default_resolver: str
    default_value: Any
    validator: str
    scope: str
    secret: bool
    restart_required: bool
    description: str

    @property
    def type_names(self) -> tuple[str, ...]:
        return tuple(value_type.__name__ for value_type in self.value_types)


@dataclass(frozen=True)
class SettingResolution:
    key: str
    value: Any
    present: bool
    valid: bool
    source: str
    error: str | None = None


SETTING_SPECS = {
    "ui_theme": SettingSpec(
        "ui_theme", (str,), "theme_loader.DEFAULT_THEME_ID", None,
        "theme_id", USER_SETTING, False, False,
        "Selected Rank Hunter UI theme.",
    ),
    "ui_appearance": SettingSpec(
        "ui_appearance", (str,), "literal", "light",
        "enum:light,dark", USER_SETTING, False, False,
        "Selected Rank Hunter light/dark appearance.",
    ),
    "science_python": SettingSpec(
        "science_python", (str,), "ui_context.detected_science_python", None,
        "runtime_probe_required", RUNTIME_DEFAULT, False, False,
        "Default Python/Sage executable for scientific jobs.",
    ),
    "ratpoints": SettingSpec(
        "ratpoints", (str,), "vendored_executable:CPU", None,
        "runtime_probe_required", RUNTIME_DEFAULT, False, False,
        "Default CPU ratpoints executable.",
    ),
    "ratpoints_gpu": SettingSpec(
        "ratpoints_gpu", (str,), "vendored_executable:GPU", None,
        "runtime_probe_optional", RUNTIME_DEFAULT, False, False,
        "Default GPU ratpoints executable.",
    ),
    "ratpoints_backend": SettingSpec(
        "ratpoints_backend", (str,), "literal", "CPU",
        "enum:CPU,GPU", RUNTIME_DEFAULT, False, False,
        "Default point-search backend; explicit workflows may override it.",
    ),
    "pari_rank_timeout": SettingSpec(
        "pari_rank_timeout", (int,), "literal", 300,
        "integer:min=10", RUNTIME_DEFAULT, False, False,
        "Global default PARI rank time budget in seconds.",
    ),
    "mwrank_rank_timeout": SettingSpec(
        "mwrank_rank_timeout", (int,), "literal", 300,
        "integer:min=10", RUNTIME_DEFAULT, False, False,
        "Global default mwrank rank time budget in seconds.",
    ),
    "deep_cert_timeout": SettingSpec(
        "deep_cert_timeout", (int,), "literal", 900,
        "integer:min=10", RUNTIME_DEFAULT, False, False,
        "Global default deep-certificate time budget in seconds.",
    ),
    "queue_max_workers": SettingSpec(
        "queue_max_workers", (int,), "literal", 2,
        "integer:min=1,max=32", RUNTIME_DEFAULT, False, False,
        "Global maximum concurrent queue-owned jobs.",
    ),
    "queue_resource_limits": SettingSpec(
        "queue_resource_limits", (dict,), "ui_store.DEFAULT_RESOURCE_LIMITS",
        {"gpu_ratpoints": 1, "sage_heavy": 2, "database_maintenance": 1},
        "resource_limit_mapping", RUNTIME_DEFAULT, False, False,
        "Per-resource-class concurrency ceilings.",
    ),
}


SCIENCE_PYTHON_BOOTSTRAP_RELATIVE_PATH = Path(".rank42-ui") / "science-python"


def science_python_bootstrap_value(project_root, *, fallback=None):
    """Return the installer/bootstrap candidate for science_python.

    The marker is one-way initialization input only. Callers must not use this
    helper to override an existing persisted `ui_settings.science_python` row.
    """
    marker = Path(project_root) / SCIENCE_PYTHON_BOOTSTRAP_RELATIVE_PATH
    if marker.exists():
        value = marker.read_text(encoding="utf-8").strip()
        if value:
            return value
    if fallback is not None:
        return str(fallback)
    return sys.executable


def setting_spec(key: str) -> SettingSpec:
    try:
        return SETTING_SPECS[str(key)]
    except KeyError as exc:
        raise KeyError(f"unknown Rank Hunter setting {key!r}") from exc


def _type_matches(value, spec):
    if isinstance(value, bool) and int in spec.value_types and bool not in spec.value_types:
        return False
    return isinstance(value, spec.value_types)


def validate_setting_value(key, value):
    """Validate storage shape; runtime executable health is a separate probe."""
    spec = setting_spec(key)
    if not _type_matches(value, spec):
        expected = ", ".join(spec.type_names)
        raise ValueError(f"{spec.key} must be {expected}")

    policy = spec.validator
    if policy == "enum:light,dark":
        value = str(value).lower()
        if value not in {"light", "dark"}:
            raise ValueError("ui_appearance must be light or dark")
    elif policy == "enum:CPU,GPU":
        value = str(value).upper()
        if value not in {"CPU", "GPU"}:
            raise ValueError("ratpoints_backend must be CPU or GPU")
    elif policy == "integer:min=10":
        if int(value) < 10:
            raise ValueError(f"{spec.key} must be at least 10")
    elif policy == "integer:min=1,max=32":
        if not 1 <= int(value) <= 32:
            raise ValueError(f"{spec.key} must be between 1 and 32")
    elif policy == "nullable_positive_integer":
        if value is not None and int(value) <= 0:
            raise ValueError(f"{spec.key} must be a positive integer or null")
    elif policy == "resource_limit_mapping":
        for resource, limit in value.items():
            if not isinstance(resource, str) or not resource:
                raise ValueError("resource limit keys must be nonempty strings")
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
                raise ValueError("resource limits must be positive integers")
    # Executable settings remain strings in storage. Runtime health is checked
    # by rank42.runtime_validation before Settings marks required paths healthy.
    return value


def _fallback_value(spec, default):
    if default is not _MISSING:
        return default
    return spec.default_value


def resolve_setting(db, key, default=_MISSING):
    """Resolve one typed setting without mutating legacy storage."""
    spec = setting_spec(key)
    row = db.execute(
        "SELECT value_json FROM ui_settings WHERE key=?",
        (spec.key,),
    ).fetchone()
    fallback = _fallback_value(spec, default)
    if row is None:
        return SettingResolution(
            spec.key, fallback, False, True, "default"
        )

    try:
        raw = json.loads(row["value_json"])
    except Exception as exc:
        return SettingResolution(
            spec.key,
            fallback,
            True,
            False,
            "default",
            f"invalid JSON: {exc}",
        )

    try:
        value = validate_setting_value(spec.key, raw)
    except ValueError as exc:
        return SettingResolution(
            spec.key, fallback, True, False, "default", str(exc)
        )
    return SettingResolution(spec.key, value, True, True, "stored")


def setting_value(db, key, default=_MISSING):
    return resolve_setting(db, key, default).value


def save_setting_value(db, key, value):
    value = validate_setting_value(key, value)
    from rank42.ui_store import set_setting

    set_setting(db, key, value)
    return value


def seed_setting_if_missing(db, key, value):
    """Persist a bootstrap value only when no row exists for this key."""
    spec = setting_spec(key)
    row = db.execute(
        "SELECT 1 FROM ui_settings WHERE key=?",
        (spec.key,),
    ).fetchone()
    if row is not None:
        return False
    save_setting_value(db, spec.key, value)
    return True


def setting_inventory() -> list[dict[str, object]]:
    rows = []
    for key in sorted(SETTING_SPECS):
        spec = SETTING_SPECS[key]
        rows.append({
            "key": spec.key,
            "types": list(spec.type_names),
            "default_resolver": spec.default_resolver,
            "default_value": spec.default_value,
            "validator": spec.validator,
            "scope": spec.scope,
            "secret": spec.secret,
            "restart_required": spec.restart_required,
            "description": spec.description,
        })
    return rows
