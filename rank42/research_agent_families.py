"""Bounded, read-only family-plugin facts for Agent conversations."""

from __future__ import annotations

import json
import re
from pathlib import Path

from rank42.plugins import Plugin, discover_plugins, is_enabled, is_archived


_FAMILY_WORDS = re.compile(r"\b(?:famil(?:y|ies)|plugin|installed)\b", re.I)
_FORMULA_WORDS = re.compile(r"\b(?:equation|formula|model|weierstrass|base.change|cover)\b", re.I)
_STOP = {"a", "an", "and", "by", "curve", "family", "first", "for", "in", "is", "of", "on",
         "plugin", "rank", "the", "to", "with", "2026", "k3", "elliptic", "quadratic"}


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z]+|\d+", value.lower()) if token not in _STOP}


def _source_equation(plugin: Plugin) -> dict | None:
    """Read declared exact equations; do not import or execute plugin code."""
    if plugin.id == "elkies_rank18_2026":
        source = plugin.root.parent / "elkies_rank17_2026" / "family.json"
        if source.is_file():
            data = json.loads(source.read_text(encoding="utf-8"))
            a = data.get("a_invariants")
            if isinstance(a, list) and len(a) == 5 and all(isinstance(x, str) for x in a):
                return {
                    "base_parameter": "t",
                    "base_weierstrass": "y^2=x^3+A(t)x+B(t)",
                    "A(t)": a[3],
                    "B(t)": a[4],
                    "rank18_model": "y^2=x^3+A(t(r))x+B(t(r))",
                    "source": "installed elkies_rank17_2026/family.json and rank18 cover provenance",
                }
    spec = plugin.family_spec or ""
    if spec.startswith("json:"):
        source = Path(spec[5:])
        if source.is_file() and source.parent.resolve().is_relative_to(plugin.root.resolve()):
            data = json.loads(source.read_text(encoding="utf-8"))
            a = data.get("a_invariants")
            if isinstance(a, list) and len(a) == 5 and all(isinstance(x, str) for x in a):
                return {"parameter": data.get("parameter"), "a_invariants": a}
    return None


def family_plugin_context(db, project_root: str | Path, message: str,
                          history: list[dict] = ()) -> dict | None:
    """Resolve a named installed family from the current message or recent user turn."""
    current = str(message)
    if not (_FAMILY_WORDS.search(current) or _FORMULA_WORDS.search(current)):
        return None
    plugins = [item for item in discover_plugins(project_root)
               if isinstance(item, Plugin) and item.plugin_type == "family" and not is_archived(db, item)]
    previous = next((str(item.get("text") or "") for item in reversed(history)
                     if item.get("role") == "user"), "")
    for question in (current, previous):
        words = _tokens(question)
        ranked = []
        for plugin in plugins:
            label_words = _tokens(f"{plugin.id} {plugin.name}")
            matches = words & label_words
            if len(matches) >= 2 and (len(matches) >= 3 or any(word.isdigit() for word in matches)):
                ranked.append((len(matches), plugin))
        ranked.sort(key=lambda item: item[0], reverse=True)
        if ranked and (len(ranked) == 1 or ranked[0][0] > ranked[1][0]):
            plugin = ranked[0][1]
            provenance = plugin.manifest.get("provenance") or {}
            return {
                "id": plugin.id,
                "name": plugin.name,
                "version": plugin.version,
                "enabled": is_enabled(db, plugin),
                "verified_generic_rank_lower": plugin.verified_generic_rank_lower,
                "description": str(plugin.manifest.get("description") or "")[:600],
                "provenance": {key: str(provenance[key])[:600] for key in
                               ("author", "paper", "source_url", "published_cover", "parameterization")
                               if key in provenance},
                "equation": _source_equation(plugin),
            }
    if _FAMILY_WORDS.search(current):
        return {"installed_family_plugins": [
            {"id": plugin.id, "name": plugin.name, "enabled": is_enabled(db, plugin)}
            for plugin in plugins[:30]
        ]}
    return None


def family_equation_answer(db, project_root: str | Path, message: str,
                           history: list[dict] = ()) -> str | None:
    """Give an exact installed formula directly, including on a clarifying turn."""
    previous = next((str(item.get("text") or "") for item in reversed(history)
                    if item.get("role") == "user"), "")
    if not (_FORMULA_WORDS.search(message) or
            (_FAMILY_WORDS.search(message) and _FORMULA_WORDS.search(previous))):
        return None
    record = family_plugin_context(db, project_root, message, history)
    if not record or not record.get("id"):
        return None
    provenance = record.get("provenance") or {}
    equation = record.get("equation") or {}
    if record["id"] == "elkies_rank18_2026" and "A(t)" in equation:
        a = equation["A(t)"].replace(" + ", "\n  + ").replace(" - ", "\n  - ")
        b = equation["B(t)"].replace(" + ", "\n  + ").replace(" - ", "\n  - ")
        return (
            "**Elkies Rank-18 First Cover** is the rank-17 K3 family after this "
            "quadratic base change:\n\n"
            "$$\nu^2=4225t^2+38636t+289444\n$$\n\n"
            "$$\nt(r)=\\frac{289444-r^2}{130r-38636},\\qquad u(r)=65t(r)+r.\n$$\n\n"
            "Its Weierstrass equation is $y^2=x^3+A(t(r))x+B(t(r))$, with "
            "these exact coefficients from the installed rank-17 family:\n\n"
            f"```text\nA(t) = {a}\n\nB(t) = {b}\n```\n\n"
            "The plugin records a **generic rank lower bound of 18**."
        )
    if provenance.get("published_cover"):
        return (f"**{record['name']}** uses the cover "
                f"`{provenance['published_cover']}`. "
                f"Parameterization: `{provenance.get('parameterization', 'not recorded')}`.")
    if equation.get("a_invariants"):
        return (f"**{record['name']}** has Weierstrass coefficients "
                f"`{equation['a_invariants']}` over `{equation.get('parameter') or 't'}`.")
    return None
