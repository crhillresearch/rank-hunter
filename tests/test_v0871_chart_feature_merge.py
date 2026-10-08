import json
import sqlite3
from fractions import Fraction

from rank42.family_charts import FamilyChart
from rank42.plugins import Plugin, apply_search_command_features, read_plugin


def test_published_chart_round_trip_exact():
    chart = FamilyChart(
        id="published", label="Published", variant_id="search", native_variant_id="native",
        native_family_spec="dummy", native_family_key="ek:native",
        matrix=("-1", "2", "1", "-6"), candidate_defaults={},
    )
    s = Fraction(-68559, 326291)
    t = chart.forward(s)
    assert t == Fraction(-721141, 2026305)
    assert chart.inverse(t) == s


def test_family_charts_and_functional_feature_hooks_coexist(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()

    fam = plugins / "fam"
    fam.mkdir()
    (fam / "family.json").write_text("{}")
    (fam / "plugin.json").write_text(json.dumps({
        "schema_version": 1, "id": "fam", "name": "Family", "version": "1",
        "plugin_type": "family", "capabilities": ["pgl2_search"],
        "family": {"kind": "json", "file": "family.json"},
        "charts": [{"id": "published", "label": "Published", "matrix": ["-1", "2", "1", "-6"]}],
    }))
    family = read_plugin(fam)
    assert isinstance(family, Plugin)
    assert len(family.charts) == 1
    assert family.feature_command_hooks == ()

    sym = plugins / "sym"
    sym.mkdir()
    (sym / "feature.py").write_text(
        "def render(context):\n    return None\n\n"
        "def on_search_command(context):\n"
        "    return {'command': context['command'] + ['--symmetry-reduced'], "
        "'note': 'reduced', 'metadata': {'exact': True}}\n"
    )
    (sym / "plugin.json").write_text(json.dumps({
        "schema_version": 1, "id": "sym", "name": "Symmetry", "version": "1",
        "plugin_type": "feature", "enabled_by_default": True, "entrypoint": "feature.py",
        "hooks": [{"name": "search_command", "entrypoint": "feature.py",
                   "function": "on_search_command", "priority": 20}],
    }))
    feature = read_plugin(sym)
    assert feature.charts == ()
    assert len(feature.feature_command_hooks) == 1

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE plugin_states(plugin_id TEXT PRIMARY KEY, enabled INTEGER, status TEXT, validation_json TEXT, updated_at TEXT)")
    command, audit = apply_search_command_features(
        tmp_path, db, ["sage", "worker.py"], family_plugin=family,
        kind="family_search", metadata={"chart_id": "published"},
    )
    assert command[-1] == "--symmetry-reduced"
    assert audit[0]["note"] == "reduced"
    assert audit[0]["metadata"]["exact"] is True
