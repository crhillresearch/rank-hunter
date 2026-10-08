from pathlib import Path

import rank42.torsion_auto_search as torsion_auto
from rank42.plugins import Plugin, PluginVariant


def _plugin(pid, *, torsion=None, role=None):
    parent = {}
    variant_manifest = {
        "id": "default",
        "name": "Default",
        "curve_family_name": pid,
    }
    if torsion is not None:
        variant_manifest["torsion_groups"] = [torsion]
    if role is not None:
        variant_manifest["torsion_provider_role"] = role
    variant = PluginVariant(
        "default",
        "Default",
        f"{pid}_family",
        variant_manifest,
        parent,
    )
    return Plugin(
        id=pid,
        name=pid,
        version="1",
        plugin_type="family",
        root=Path("."),
        family_spec=variant.family_spec,
        manifest=parent,
        capabilities=frozenset({"candidate_generation"}),
        adapter_path=None,
        variants=(variant,),
        default_variant_id="default",
    )


def test_direct_torsion_provider_excludes_unrelated_families_by_default(monkeypatch):
    direct = _plugin(
        "mazur",
        torsion="C2xC8",
        role="canonical_universal",
    )
    unrelated = _plugin("elkies")
    monkeypatch.setattr(
        torsion_auto,
        "discover_plugins",
        lambda _root: [unrelated, direct],
    )
    monkeypatch.setattr(torsion_auto, "is_enabled", lambda _db, _plugin: True)

    def fake_torsion(plugin, _variant):
        if plugin.id == "mazur":
            return ("C2 × C8",), "test"
        return ("Trivial",), "test"

    monkeypatch.setattr(torsion_auto, "_provider_torsion", fake_torsion)

    providers = torsion_auto.discover_providers(
        Path("."),
        object(),
        "C2 × C8",
        include_secondary=False,
    )
    assert [p["plugin_id"] for p in providers] == ["mazur"]
    assert providers[0]["provider_lane"] == "canonical-universal"


def test_secondary_family_lane_is_explicit_and_runs_after_direct(monkeypatch):
    direct = _plugin(
        "mazur",
        torsion="C2xC8",
        role="canonical_universal",
    )
    unrelated = _plugin("elkies")
    monkeypatch.setattr(
        torsion_auto,
        "discover_plugins",
        lambda _root: [unrelated, direct],
    )
    monkeypatch.setattr(torsion_auto, "is_enabled", lambda _db, _plugin: True)
    monkeypatch.setattr(
        torsion_auto,
        "_provider_torsion",
        lambda plugin, _variant: (
            ("C2 × C8",), "test"
        ) if plugin.id == "mazur" else (("Trivial",), "test"),
    )

    providers = torsion_auto.discover_providers(
        Path("."),
        object(),
        "C2 × C8",
        include_secondary=True,
    )
    assert [p["plugin_id"] for p in providers] == ["mazur", "elkies"]
    assert providers[0]["torsion_priority"] < providers[1]["torsion_priority"]


def test_declared_provider_with_wrong_validation_torsion_is_rejected(monkeypatch):
    bad = _plugin(
        "bad_direct",
        torsion="C2xC8",
        role="canonical_universal",
    )
    monkeypatch.setattr(torsion_auto, "discover_plugins", lambda _root: [bad])
    monkeypatch.setattr(torsion_auto, "is_enabled", lambda _db, _plugin: True)
    monkeypatch.setattr(
        torsion_auto,
        "_provider_torsion",
        lambda _plugin, _variant: (("Trivial",), "test"),
    )

    providers = torsion_auto.discover_providers(
        Path("."),
        object(),
        "C2 × C8",
        include_secondary=False,
    )
    assert providers == []



def test_geometry_torsion_provider_uses_geometry_search_child():
    args = type("Args", (), {
        "project_root": ".",
        "db": "rank42.db",
        "target_rank": 4,
        "torsion_group": "C2 × C8",
        "retention_floor": 3,
        "pool_size": 250,
        "deep_keep": 24,
        "upper_timeout": 5,
        "final_upper_timeout": 20,
        "certificate_timeout": 120,
        "exact_candidates": 48,
        "ratpoints": "/tmp/ratpoints_gpu",
        "stop_on_target": True,
        "geometry_first": True,
        "geometry_keep": 48,
        "geometry_triage_timeout": 2,
        "geometry_timeout": 180,
        "geometry_stage_bounds": "523,1979",
        "geometry_stage_keeps": "5000,250",
    })()
    child = {"id": 12}
    provider = {"plugin_id": "mazur", "variant_id": "c2xc8"}
    command = torsion_auto._child_command(args, child, provider)
    assert command[1:3] == ["-m", "rank42.geometry_search"]
    assert "--geometry-keep" in command
    assert "--stage-bounds" in command
    assert "--torsion-group" in command
