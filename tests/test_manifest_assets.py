from pathlib import Path

import pytest

from rank42.manifest_assets import ManifestAssetError, preview_image_path


def test_preview_image_metadata_resolves_inside_package(tmp_path):
    image = tmp_path / "assets" / "preview.png"
    image.parent.mkdir()
    image.write_bytes(b"preview")
    assert preview_image_path(tmp_path, {"preview_image": "assets/preview.png"}) == image.resolve()


def test_preview_image_metadata_is_optional(tmp_path):
    assert preview_image_path(tmp_path, {}) is None


def test_preview_image_metadata_cannot_escape_package(tmp_path):
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(b"preview")
    with pytest.raises(ManifestAssetError):
        preview_image_path(tmp_path, {"preview_image": "../outside.png"})
