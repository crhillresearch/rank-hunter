"""Prepare Agent replies for Streamlit's Markdown math renderer."""

from __future__ import annotations

import re


_PROTECTED = re.compile(r"(`+[^`\n]*`+|\$\$[\s\S]*?\$\$|\$[^$\n]+\$)")
_DISPLAY = re.compile(r"\\\[([\s\S]*?)\\\]")
_INLINE = re.compile(r"\\\(([^\n]*?)\\\)")
_BARE = re.compile(r"\(([^()\n]{1,180})\)")
_MATH_CUE = re.compile(r"\\[A-Za-z]+|(?<!\w)[A-Za-z][A-Za-z0-9]*[_^](?:\{[^}]+\}|[A-Za-z0-9]+)")


def _plain_math(text: str) -> str:
    text = _DISPLAY.sub(lambda match: f"\n$$\n{match[1]}\n$$\n", text)
    text = _INLINE.sub(lambda match: f"${match[1]}$", text)
    return _BARE.sub(
        lambda match: f"${match[1]}$" if _MATH_CUE.search(match[1]) else match[0],
        text,
    )


def renderable_agent_math(text: str) -> str:
    """Convert common LLM TeX delimiters without changing code or dollar math."""
    rendered: list[str] = []
    plain: list[str] = []
    fence: str | None = None

    def flush() -> None:
        if plain:
            for part in _PROTECTED.split("".join(plain)):
                rendered.append(part if _PROTECTED.fullmatch(part) else _plain_math(part))
            plain.clear()

    for line in text.splitlines(keepends=True):
        marker = re.match(r"^[ \t]{0,3}(`{3,}|~{3,})", line)
        if fence:
            rendered.append(line)
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = None
        elif marker:
            flush()
            fence = marker[1]
            rendered.append(line)
        else:
            plain.append(line)
    flush()
    return "".join(rendered)
