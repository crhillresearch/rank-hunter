"""Safe preview-image metadata shared by plugin-like filesystem packages.

Plugin manifests and theme manifests may declare a top-level ``preview_image``
path relative to their package root.  The resolver is presentation-only: it
never executes package code and never permits paths to escape the package.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping, Any


class ManifestAssetError(ValueError):
    """Raised when manifest asset metadata is invalid."""


def preview_image_path(package_root: str | Path, manifest: Mapping[str, Any]) -> Path | None:
    """Resolve an optional top-level ``preview_image`` asset safely."""
    raw = str((manifest or {}).get("preview_image") or "").strip()
    if not raw:
        return None

    root = Path(package_root).resolve()
    candidate = (root / raw).resolve()
    if candidate != root and root not in candidate.parents:
        raise ManifestAssetError(f"preview_image escapes package directory: {raw!r}")
    if not candidate.is_file():
        raise ManifestAssetError(f"preview_image does not exist: {candidate}")
    return candidate
