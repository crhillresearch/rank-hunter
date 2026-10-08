from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_docs_math_renderer_is_configured():
    config = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    script = (ROOT / "docs" / "javascripts" / "mathjax.js").read_text(
        encoding="utf-8"
    )

    assert "pymdownx.arithmatex:" in config
    assert "generic: true" in config
    assert "javascripts/mathjax.js" in config
    assert "mathjax@3/es5/tex-mml-chtml.js" in config
    assert "MathJax.typesetPromise()" in script
    assert "processHtmlClass: \"arithmatex\"" in script


def test_public_libraries_page_has_no_plugin_corpus_surface():
    source = (ROOT / "rank42" / "ui_pages" / "corpus_page.py").read_text(
        encoding="utf-8"
    )

    assert '"Plugin Libraries"' not in source
    assert "_render_plugin_libraries" not in source
    assert "build_corpus_command" not in source
    assert "corpus_status" not in source
    assert '["Browse Library", "Build Library"]' in source
    assert 'value="Browse Library"' in source
