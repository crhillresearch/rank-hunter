from pathlib import Path

import pytest

from rank42.icon_sprite import rh_css_mask_uri, rh_icon_names, rh_icon_svg


ROOT = Path(__file__).resolve().parents[1]
SPRITE = ROOT / "rank42" / "ui_assets" / "rank_hunter_icons.svg"


EXPECTED_NAV_ICONS = {
    "dashboard",
    "auto",
    "search",
    "target",
    "candidates",
    "descent",
    "quartics",
    "points",
    "independence",
    "saturation",
    "lattices",
    "curves",
    "libraries",
    "catalogs",
    "jobs",
    "campaigns",
    "pipelines",
    "plugins",
    "diagnostics",
    "database",
    "settings",
    "light",
    "dark",
    "restart",
    "power-off",
    "candidate-generate",
    "candidate-pools",
}


def test_rank_hunter_sprite_contains_complete_nav_and_utility_set():
    assert SPRITE.is_file()
    names = set(rh_icon_names())
    assert EXPECTED_NAV_ICONS <= names
    assert len(names) == len(set(names))


def test_rank_hunter_sprite_expands_symbols_into_standalone_svg_masks():
    svg = rh_icon_svg("curves")

    assert svg.startswith("<svg ")
    assert "viewBox='0 0 24 24'" in svg
    assert "rh-s" in svg
    assert "<style>" in svg

    uri = rh_css_mask_uri("curves")
    assert uri.startswith("data:image/svg+xml;base64,")


def test_rank_hunter_sprite_rejects_unknown_icons():
    with pytest.raises(KeyError, match="unknown Rank Hunter icon"):
        rh_icon_svg("definitely-not-an-icon")


def test_rank_hunter_sprite_uses_small_ui_optical_stroke_weight():
    source = SPRITE.read_text(encoding="utf-8")

    assert "stroke-width:2.25" in source
    assert "stroke-width:1.9" not in source
