import ast
from pathlib import Path
from types import SimpleNamespace


def _source_path():
    return Path(__file__).resolve().parents[1] / "rank42" / "ui_pages" / "plugins_page.py"


def _load_sort_key(claims):
    path = _source_path()
    tree = ast.parse(path.read_text())
    fn = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_family_sort_key"
    )
    namespace = {"family_rank_claim": lambda plugin: claims[plugin.id]}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["_family_sort_key"]


def test_families_page_uses_normalized_rank_sort_key():
    source = _source_path().read_text()
    assert "key=_family_sort_key" in source
    assert "p.generic_rank if p.generic_rank is not None" not in source


def test_sort_order_uses_effective_then_historical_then_unknown():
    claims = {
        "verified14": {"effective_lower": 14, "historical_lower": 14},
        "historical12": {"effective_lower": None, "historical_lower": 12},
        "legacy10": {"effective_lower": 10, "historical_lower": None},
        "unknown": {"effective_lower": None, "historical_lower": None},
    }
    key = _load_sort_key(claims)
    plugins = [
        SimpleNamespace(id="unknown", name="Zulu"),
        SimpleNamespace(id="historical12", name="Beta"),
        SimpleNamespace(id="verified14", name="Alpha"),
        SimpleNamespace(id="legacy10", name="Gamma"),
    ]
    assert [p.id for p in sorted(plugins, key=key)] == [
        "verified14", "historical12", "legacy10", "unknown"
    ]


def test_pending_verification_is_compact_rank_warning_not_caption():
    source = _source_path().read_text()
    assert "rh-rank-verification" in source
    assert "Rank Hunter verification pending" in source
    assert 'st.caption(claim["status"]' not in source
    assert 'claim["historical_lower"] is not None and claim["effective_lower"] is None' in source


def test_family_cards_normalize_variable_height_content_zones():
    source = _source_path().read_text()
    assert "def _family_layout_styles" in source
    assert "rh-family-title" in source
    assert "rh-family-meta" in source
    assert "rh-family-description" in source
    assert "-webkit-line-clamp: 2" in source
    assert ".rh-family-capabilities" in source
    assert "min-height: 3.15rem" in source
    assert "_family_description(plugin)" in source
    assert "_family_layout_styles()" in source


def test_family_title_and_version_stack_aligns_with_rank_box():
    source = _source_path().read_text()
    assert ".rh-family-heading" in source
    assert "flex-direction: column" in source
    assert "justify-content: center" in source
    heading_start = source.index('rh-family-header-row"><div class="rh-family-heading')
    rank_start = source.index('rh-rank-box', heading_start)
    block = source[heading_start:rank_start]
    assert "rh-family-title" in block
    assert "rh-family-meta" in block
