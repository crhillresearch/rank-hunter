"""Streamlit-free definition of the versioned Feature hook contract.

SYSTEM-21 boundary:
ordinary Feature hooks are public plugin contracts. Settings and Database
insertion points remain reserved compatibility contracts and require both an
explicit manifest privilege and a core-owned plugin-id allowlist entry.
"""
from __future__ import annotations

FEATURE_HOOK_API_VERSION = 1

PUBLIC_FEATURE_HOOKS = frozenset({
    "dashboard.after_header",
    "search.after_header",
    "target.after_header",
    "curves.after_header",
    "curves.actions",
    "candidates.after_header",
    "points.after_header",
    "manage.jobs.results.after_header",
    "data.catalogs.after_header",
    "analysis.work_center.after_header",
    "analysis.descent.after_header",
    "analysis.mw_geometry.after_header",
    "analysis.quartics.after_header",
    "analysis.independence.after_header",
    "analysis.saturation.after_header",
    "manage.campaigns.after_header",
    "manage.jobs.after_header",
})

# Public v0.9 compatibility window for hooks whose owning surface moved or was
# renamed during the v0.9.1 information-architecture audit.
FEATURE_HOOK_HOST_CONTRACTS = {
    # Dashboard is a bounded state/attention surface. Feature contributions may
    # add one compact status region but cannot grow the home page without bound.
    "dashboard.after_header": {
        "layout": "compact_status_v1",
        "max_height": 180,
        "max_contributions": 2,
    },
}

FEATURE_HOOK_ALIASES = {
    "results.after_header": "manage.jobs.results.after_header",
    "external_catalog.after_header": "data.catalogs.after_header",
    "analysis.analyze.after_header": "analysis.work_center.after_header",
    "analysis.lattices.after_header": "analysis.mw_geometry.after_header",
}

SYSTEM_PRIVILEGED_FEATURE_HOOKS = frozenset({
    "database.after_header",
    "settings.after_header",
})

SYSTEM_FEATURE_PRIVILEGES = {
    "database.after_header": "system.database",
    "settings.after_header": "system.settings",
}
SUPPORTED_SYSTEM_PRIVILEGES = frozenset(SYSTEM_FEATURE_PRIVILEGES.values())

# Core-owned trust decision. A plugin cannot self-promote by editing its
# manifest: both the manifest privilege and this allowlist must agree.
# No third-party/bundled plugin currently requires System injection.
SYSTEM_FEATURE_PLUGIN_ALLOWLIST = {}

SUPPORTED_FEATURE_HOOKS = frozenset(
    PUBLIC_FEATURE_HOOKS | SYSTEM_PRIVILEGED_FEATURE_HOOKS
)

# Accepted manifest/runtime ids include the compatibility window; callers should
# normalize immediately and store/dispatch only canonical ids.
ACCEPTED_FEATURE_HOOKS = frozenset(
    SUPPORTED_FEATURE_HOOKS | FEATURE_HOOK_ALIASES.keys()
)


def canonical_feature_hook(hook_id):
    """Normalize one public/legacy hook id to its canonical v1 semantic id."""
    hook_id = str(hook_id)
    return FEATURE_HOOK_ALIASES.get(hook_id, hook_id)


def feature_hook_deprecated_alias(hook_id):
    """Return the canonical replacement when hook_id is a deprecated alias."""
    hook_id = str(hook_id)
    return FEATURE_HOOK_ALIASES.get(hook_id)


def required_system_privilege(hook_id):
    return SYSTEM_FEATURE_PRIVILEGES.get(canonical_feature_hook(hook_id))


def feature_hook_allowed(plugin_id, hook_id, declared_privileges=()):
    hook_id = canonical_feature_hook(hook_id)
    plugin_id = str(plugin_id)
    if hook_id not in SUPPORTED_FEATURE_HOOKS:
        return False
    required = required_system_privilege(hook_id)
    if required is None:
        return True
    declared = {str(value) for value in (declared_privileges or ())}
    allowlisted = {
        str(value)
        for value in SYSTEM_FEATURE_PLUGIN_ALLOWLIST.get(plugin_id, ())
    }
    return required in declared and required in allowlisted


# Streamlit-free behavioral hooks retained for narrow search-command transforms.
# These are distinct from presentation/render hooks above. They share the
# Feature-hook API version but currently have no renamed ids.
SUPPORTED_FUNCTIONAL_FEATURE_HOOKS = frozenset({
    "search_command",
})
