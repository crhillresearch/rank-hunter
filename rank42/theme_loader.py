"""Filesystem-backed UI themes for Rank Hunter.

Themes are presentation-only resources stored under ``<project>/themes``.
Core code owns discovery, validation, selection, and stable UI hooks; theme
packages own colors, spacing, typography, component styling, and branding
assets. Themes never execute Python and cannot alter scientific behavior.
"""
from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

THEME_SCHEMA_VERSION = 1
DEFAULT_THEME_ID = "axiom"
RELEASE_THEME_ID = "axiom"
_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


class ThemeError(ValueError):
    """Raised when a theme package is missing or invalid."""


@dataclass(frozen=True)
class ThemeAppearance:
    tokens: dict[str, str]
    stylesheets: tuple[Path, ...] = ()
    streamlit_theme_path: Path | None = None


@dataclass(frozen=True)
class Theme:
    id: str
    name: str
    version: str
    root: Path
    manifest: dict[str, Any]
    tokens: dict[str, str]
    stylesheets: tuple[Path, ...]
    logo_path: Path | None = None
    logo_width: int = 96
    streamlit_theme_path: Path | None = None
    appearances: dict[str, ThemeAppearance] = field(default_factory=dict)

    def _appearance(self, appearance: str | None) -> ThemeAppearance | None:
        name = str(appearance or "light").strip().lower() or "light"
        return self.appearances.get(name)

    def tokens_for(self, appearance: str | None = "light") -> dict[str, str]:
        """Return base semantic tokens overlaid by one optional appearance."""
        tokens = dict(self.tokens)
        variant = self._appearance(appearance)
        if variant is not None:
            tokens.update(variant.tokens)
        return tokens

    def resolved_tokens(self, appearance: str | None = "light") -> dict[str, str]:
        """Resolve simple var(--token) aliases for non-CSS consumers."""
        tokens = self.tokens_for(appearance)
        resolved: dict[str, str] = {}

        def resolve(key: str, stack: tuple[str, ...] = ()) -> str:
            if key in resolved:
                return resolved[key]
            value = tokens.get(key, "")
            match = re.fullmatch(r"var\(--([A-Za-z][A-Za-z0-9_-]*)\)", value)
            if match and match.group(1) not in stack:
                value = resolve(match.group(1), stack + (key,))
            resolved[key] = value
            return value

        for key in tokens:
            resolve(key)
        return resolved

    def streamlit_theme_for(self, appearance: str | None = "light") -> Path | None:
        variant = self._appearance(appearance)
        if variant is not None and variant.streamlit_theme_path is not None:
            return variant.streamlit_theme_path
        return self.streamlit_theme_path

    def css_text(self, appearance: str | None = "light") -> str:
        """Return theme CSS with one optional appearance layered last."""
        variables = [":root {"]
        for key, value in self.tokens_for(appearance).items():
            variables.append(f"    --{key}: {value};")
        variables.append("}")
        chunks = ["\n".join(variables)]
        for path in self.stylesheets:
            chunks.append(path.read_text(encoding="utf-8"))
        variant = self._appearance(appearance)
        if variant is not None:
            for path in variant.stylesheets:
                chunks.append(path.read_text(encoding="utf-8"))
        return "\n\n".join(chunks)


def themes_root(project_root: str | Path) -> Path:
    return Path(project_root).resolve() / "themes"


def _inside(root: Path, relative: str, *, field: str) -> Path:
    candidate = (root / relative).resolve()
    resolved_root = root.resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise ThemeError(f"{field} escapes the theme directory: {relative!r}")
    return candidate


def _validate_streamlit_theme(path: Path) -> None:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ThemeError(f"cannot parse Streamlit theme {path}: {exc}") from exc
    if not isinstance(data, dict) or set(data) != {"theme"}:
        raise ThemeError("Streamlit theme files may only contain a [theme] table")
    theme = data.get("theme")
    if not isinstance(theme, dict):
        raise ThemeError("Streamlit theme file must contain a [theme] table")
    base = theme.get("base")
    if base is not None and str(base).strip().lower() not in {"dark", "light"}:
        raise ThemeError("Streamlit theme.base must be 'dark' or 'light' inside a theme package")


def read_theme(theme_dir: str | Path) -> Theme:
    root = Path(theme_dir).resolve()
    manifest_path = root / "theme.json"
    if not manifest_path.is_file():
        raise ThemeError(f"missing theme.json in {root}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ThemeError(f"cannot parse {manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ThemeError(f"{manifest_path} must contain a JSON object")
    if int(manifest.get("schema_version") or 0) != THEME_SCHEMA_VERSION:
        raise ThemeError(
            f"unsupported theme schema {manifest.get('schema_version')!r}; "
            f"expected {THEME_SCHEMA_VERSION}"
        )

    theme_id = str(manifest.get("id") or "").strip()
    name = str(manifest.get("name") or "").strip()
    version = str(manifest.get("version") or "").strip()
    if not theme_id or not name or not version:
        raise ThemeError("theme id, name, and version are required")

    raw_tokens = manifest.get("tokens") or {}
    if not isinstance(raw_tokens, dict):
        raise ThemeError("theme tokens must be an object")
    tokens: dict[str, str] = {}
    for raw_key, raw_value in raw_tokens.items():
        key = str(raw_key).strip()
        if not _TOKEN_RE.fullmatch(key):
            raise ThemeError(f"invalid CSS token name: {raw_key!r}")
        value = str(raw_value).strip()
        if not value:
            raise ThemeError(f"empty CSS token value: {key}")
        tokens[key] = value

    raw_styles = manifest.get("stylesheets", ["theme.css"])
    if isinstance(raw_styles, str):
        raw_styles = [raw_styles]
    if not isinstance(raw_styles, list) or not raw_styles:
        raise ThemeError("stylesheets must be a non-empty string/list")
    stylesheets: list[Path] = []
    for entry in raw_styles:
        path = _inside(root, str(entry), field="stylesheet")
        if not path.is_file():
            raise ThemeError(f"missing stylesheet: {path}")
        stylesheets.append(path)

    streamlit_theme_path: Path | None = None
    raw_streamlit_theme = str(manifest.get("streamlit_theme") or "").strip()
    if raw_streamlit_theme:
        streamlit_theme_path = _inside(root, raw_streamlit_theme, field="streamlit_theme")
        if not streamlit_theme_path.is_file():
            raise ThemeError(f"missing Streamlit theme: {streamlit_theme_path}")
        _validate_streamlit_theme(streamlit_theme_path)

    appearances: dict[str, ThemeAppearance] = {}
    raw_appearances = manifest.get("appearances") or {}
    if not isinstance(raw_appearances, dict):
        raise ThemeError("theme appearances must be an object")
    for raw_name, raw_appearance in raw_appearances.items():
        appearance_name = str(raw_name).strip().lower()
        if not _TOKEN_RE.fullmatch(appearance_name):
            raise ThemeError(f"invalid appearance name: {raw_name!r}")
        if not isinstance(raw_appearance, dict):
            raise ThemeError(f"appearance {appearance_name!r} must be an object")

        appearance_tokens: dict[str, str] = {}
        raw_appearance_tokens = raw_appearance.get("tokens") or {}
        if not isinstance(raw_appearance_tokens, dict):
            raise ThemeError(f"appearance {appearance_name!r} tokens must be an object")
        for raw_key, raw_value in raw_appearance_tokens.items():
            key = str(raw_key).strip()
            if not _TOKEN_RE.fullmatch(key):
                raise ThemeError(f"invalid CSS token name: {raw_key!r}")
            value = str(raw_value).strip()
            if not value:
                raise ThemeError(f"empty CSS token value: {key}")
            appearance_tokens[key] = value

        raw_appearance_styles = raw_appearance.get("stylesheets") or []
        if isinstance(raw_appearance_styles, str):
            raw_appearance_styles = [raw_appearance_styles]
        if not isinstance(raw_appearance_styles, list):
            raise ThemeError(
                f"appearance {appearance_name!r} stylesheets must be a string/list"
            )
        appearance_stylesheets: list[Path] = []
        for entry in raw_appearance_styles:
            path = _inside(root, str(entry), field=f"appearance.{appearance_name}.stylesheet")
            if not path.is_file():
                raise ThemeError(f"missing stylesheet: {path}")
            appearance_stylesheets.append(path)

        appearance_streamlit_theme_path: Path | None = None
        raw_appearance_streamlit = str(
            raw_appearance.get("streamlit_theme") or ""
        ).strip()
        if raw_appearance_streamlit:
            appearance_streamlit_theme_path = _inside(
                root,
                raw_appearance_streamlit,
                field=f"appearance.{appearance_name}.streamlit_theme",
            )
            if not appearance_streamlit_theme_path.is_file():
                raise ThemeError(
                    f"missing Streamlit theme: {appearance_streamlit_theme_path}"
                )
            _validate_streamlit_theme(appearance_streamlit_theme_path)

        appearances[appearance_name] = ThemeAppearance(
            tokens=appearance_tokens,
            stylesheets=tuple(appearance_stylesheets),
            streamlit_theme_path=appearance_streamlit_theme_path,
        )

    branding = manifest.get("branding") or {}
    if not isinstance(branding, dict):
        raise ThemeError("branding must be an object")
    logo_path: Path | None = None
    logo = str(branding.get("logo") or "").strip()
    if logo:
        logo_path = _inside(root, logo, field="branding.logo")
        if not logo_path.is_file():
            raise ThemeError(f"missing logo asset: {logo_path}")
    try:
        logo_width = int(branding.get("logo_width") or 96)
    except Exception as exc:
        raise ThemeError("branding.logo_width must be an integer") from exc
    logo_width = max(24, min(512, logo_width))

    return Theme(
        id=theme_id,
        name=name,
        version=version,
        root=root,
        manifest=manifest,
        tokens=tokens,
        stylesheets=tuple(stylesheets),
        logo_path=logo_path,
        logo_width=logo_width,
        streamlit_theme_path=streamlit_theme_path,
        appearances=appearances,
    )


def discover_themes(project_root: str | Path) -> list[Theme]:
    root = themes_root(project_root)
    if not root.is_dir():
        return []
    themes: list[Theme] = []
    for child in sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name.lower()):
        if not (child / "theme.json").is_file():
            continue
        try:
            themes.append(read_theme(child))
        except ThemeError:
            # Invalid themes are ignored for normal selection. ``theme_records``
            # exposes their errors for Settings/audit surfaces.
            continue
    return themes


def theme_records(project_root: str | Path) -> list[Theme | dict[str, str]]:
    root = themes_root(project_root)
    if not root.is_dir():
        return []
    rows: list[Theme | dict[str, str]] = []
    for child in sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name.lower()):
        if not (child / "theme.json").is_file():
            continue
        try:
            rows.append(read_theme(child))
        except ThemeError as exc:
            rows.append({"path": str(child), "error": str(exc)})
    return rows


def load_theme(project_root: str | Path, theme_id: str | None = None) -> Theme:
    requested = str(theme_id or DEFAULT_THEME_ID).strip().lower()
    found = discover_themes(project_root)
    for theme in found:
        if theme.id.lower() == requested or theme.root.name.lower() == requested:
            return theme
    available = ", ".join(theme.id for theme in found) or "none"
    raise ThemeError(f"theme {requested!r} is not installed (available: {available})")


def resolve_release_theme(
    project_root: str | Path,
    release_theme_id: str = RELEASE_THEME_ID,
) -> tuple[Theme | None, ThemeError | None]:
    """Resolve the one release-visible theme without selecting another fallback.

    The generic discovery/selection system remains intact for future releases.
    Rank Hunter 0.9.1 deliberately exposes only Axiom, so a missing Axiom
    package falls back to the core baseline instead of silently activating a
    different installed theme.
    """

    root = Path(project_root).resolve()
    requested = str(release_theme_id or RELEASE_THEME_ID).strip().lower()
    installed = discover_themes(root)
    for theme in installed:
        if theme.id.lower() == requested or theme.root.name.lower() == requested:
            return theme, None
    if not installed:
        return None, ThemeError(
            f"release theme {requested!r} is not installed; using core baseline"
        )
    available = ", ".join(theme.id for theme in installed) or "none"
    return None, ThemeError(
        f"release theme {requested!r} is not installed "
        f"(available: {available}); using core baseline"
    )


def resolve_theme(
    project_root: str | Path,
    requested_id: str | None = None,
) -> tuple[Theme | None, ThemeError | None]:
    """Resolve one complete theme with the canonical fallback policy.

    Persistence/environment lookup intentionally lives outside this function.
    Both pre-Streamlit startup and the running UI pass their requested theme id
    here so selection, validation, and fallback semantics cannot diverge.
    """
    root = Path(project_root).resolve()
    installed = discover_themes(root)
    if not installed:
        return None, None

    requested = str(requested_id or DEFAULT_THEME_ID).strip().lower() or DEFAULT_THEME_ID
    for theme in installed:
        if theme.id.lower() == requested or theme.root.name.lower() == requested:
            return theme, None

    available = ", ".join(theme.id for theme in installed) or "none"
    warning = ThemeError(
        f"theme {requested!r} is not installed (available: {available})"
    )
    fallback = next(
        (theme for theme in installed if theme.id == DEFAULT_THEME_ID),
        installed[0],
    )
    return fallback, warning
