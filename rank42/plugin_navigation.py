"""Versioned public Extension navigation contract.

Extension navigation ids are semantic plugin contracts, not sidebar labels.
The v1 allowlist is intentionally narrow. In particular, Candidates and System
remain core-owned workflow surfaces and are not Extension placement targets.
"""
from __future__ import annotations

EXTENSION_NAVIGATION_API_VERSION = 1

EXTENSION_MENU_SECTIONS = frozenset({
    "Search",
    "Data",
    "Analysis",
    "Manage",
    "Plugins",
})

# Historical manifest compatibility. Aliases are accepted at load time and
# normalized immediately so host routing only sees canonical section ids.
EXTENSION_SECTION_ALIASES = {
    "Resources": "Plugins",
}


def canonical_extension_section(section):
    """Return the canonical v1 Extension section name."""
    raw = str(section or "Analysis").strip().title()
    return EXTENSION_SECTION_ALIASES.get(raw, raw)


def extension_section_supported(section):
    """Return whether a section resolves to a public v1 Extension target."""
    return canonical_extension_section(section) in EXTENSION_MENU_SECTIONS
