"""Rank Hunter first-party SVG icon sprite.

The visual source of truth is rank42/ui_assets/rank_hunter_icons.svg.
Native Streamlit controls cannot consume SVG symbol/use references directly,
so this module expands one named symbol into a standalone SVG data URI for
CSS masks while developers maintain one sprite file.
"""
from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path
import xml.etree.ElementTree as ET


_SPRITE_PATH = Path(__file__).resolve().parent / "ui_assets" / "rank_hunter_icons.svg"
_SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", _SVG_NS)


@lru_cache(maxsize=1)
def _sprite_document() -> tuple[ET.Element, str]:
    root = ET.parse(_SPRITE_PATH).getroot()
    style = root.find(f".//{{{_SVG_NS}}}style")
    style_text = "" if style is None else str(style.text or "")
    return root, style_text


@lru_cache(maxsize=1)
def rh_icon_names() -> tuple[str, ...]:
    root, _ = _sprite_document()
    names = [
        str(symbol.attrib.get("id") or "")
        for symbol in root.findall(f".//{{{_SVG_NS}}}symbol")
        if str(symbol.attrib.get("id") or "")
    ]
    return tuple(names)


@lru_cache(maxsize=128)
def rh_icon_svg(name: str) -> str:
    requested = str(name or "").strip()
    root, style_text = _sprite_document()
    symbol = next(
        (
            node
            for node in root.findall(f".//{{{_SVG_NS}}}symbol")
            if node.attrib.get("id") == requested
        ),
        None,
    )
    if symbol is None:
        raise KeyError(f"unknown Rank Hunter icon: {requested}")

    view_box = str(symbol.attrib.get("viewBox") or "0 0 24 24")
    body = "".join(ET.tostring(child, encoding="unicode") for child in symbol)
    return (
        f"<svg xmlns='{_SVG_NS}' viewBox='{view_box}' aria-hidden='true'>"
        f"<style>{style_text}</style>{body}</svg>"
    )


@lru_cache(maxsize=128)
def rh_css_mask_uri(name: str) -> str:
    encoded = base64.b64encode(rh_icon_svg(name).encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"
