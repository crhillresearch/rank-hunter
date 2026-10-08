import json
from pathlib import Path

import pytest

from rank42.plugin_hooks import (
    SUPPORTED_FEATURE_HOOKS,
    SYSTEM_FEATURE_PLUGIN_ALLOWLIST,
    feature_hook_allowed,
)
from rank42.plugins import (
    EXTENSION_MENU_SECTIONS,
    ExtensionPage,
    Plugin,
    discover_plugins,
    read_plugin,
    search_presets_for_variant,
    variant_torsion_groups,
    variant_torsion_provider_role,
    variant_torsion_record,
)


def _write(root: Path, rel: str, text: str):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_core_checkout_ships_without_plugin_manifests():
    root = Path(__file__).resolve().parents[1]
    manifests = list((root / "plugins").glob("*/plugin.json"))
    bundled = [
        manifest
        for manifest in manifests
        if not manifest.parent.is_symlink()
    ]
    assert bundled == []


def test_analysis_quartics_feature_hook_is_public_and_supported():
    assert "analysis.quartics.after_header" in SUPPORTED_FEATURE_HOOKS


def test_feature_is_a_valid_plugin_type_and_declares_supported_hooks(tmp_path):
    root = tmp_path / 'feature_demo'
    root.mkdir()
    _write(root, 'feature.py', 'def render(context):\n    return context.hook_id\n')
    _write(root, 'plugin.json', json.dumps({
        'schema_version': 1,
        'plugin_type': 'feature',
        'id': 'demo_feature',
        'name': 'Demo Feature',
        'version': '1.0.0',
        'description': 'Feature description from manifest.',
        'hooks': ['curves.actions'],
        'entrypoint': 'feature.py',
    }))
    plugin = read_plugin(root)
    assert isinstance(plugin, Plugin)
    assert plugin.plugin_type == 'feature'
    assert plugin.feature_hooks == ('curves.actions',)
    assert plugin.description == 'Feature description from manifest.'
    assert 'curves.actions' in SUPPORTED_FEATURE_HOOKS


def test_feature_rejects_unknown_hook(tmp_path):
    root = tmp_path / 'bad_feature'
    root.mkdir()
    _write(root, 'feature.py', 'def render(context):\n    pass\n')
    _write(root, 'plugin.json', json.dumps({
        'schema_version': 1, 'plugin_type': 'feature', 'id': 'bad', 'name': 'Bad',
        'version': '1', 'hooks': ['made.up.hook'], 'entrypoint': 'feature.py'
    }))
    with pytest.raises(ValueError, match='unsupported feature hook'):
        read_plugin(root)


def test_extension_description_and_page_are_manifest_driven(tmp_path):
    root = tmp_path / 'ext_demo'
    root.mkdir()
    _write(root, 'extension.py', 'def render(context):\n    return None\n')
    _write(root, 'plugin.json', json.dumps({
        'schema_version': 1, 'plugin_type': 'extension', 'id': 'ext', 'name': 'Extension',
        'version': '2.0', 'description': 'Full workspace.',
        'menu': {'id': 'ext', 'label': 'My Workspace', 'section': 'Analysis'},
        'entrypoint': 'extension.py'
    }))
    plugin = read_plugin(root)
    assert plugin.plugin_type == 'extension'
    assert plugin.description == 'Full workspace.'
    assert [(p.label, p.section) for p in plugin.extension_pages] == [('My Workspace', 'Analysis')]


def _family_manifest(pid='demo_family', name='Demo Family'):
    return {
        'schema_version': 1,
        'id': pid,
        'name': name,
        'version': '1.0.0',
        'generic_rank': 1,
        'family': {'kind': 'json', 'file': 'family.json'},
    }


def test_discovery_accepts_organized_families_directory_case_insensitively(tmp_path):
    root = tmp_path / 'project'
    fam = root / 'plugins' / 'Families' / 'demo_family'
    fam.mkdir(parents=True)
    _write(fam, 'family.json', json.dumps({'name': 'demo'}))
    _write(fam, 'plugin.json', json.dumps(_family_manifest()))

    found = discover_plugins(root)
    assert [p.id for p in found if isinstance(p, Plugin)] == ['demo_family']


def test_discovery_accepts_organized_extensions_directory(tmp_path):
    root = tmp_path / 'project'
    ext = root / 'plugins' / 'extensions' / 'demo_extension'
    ext.mkdir(parents=True)
    _write(ext, 'extension.py', 'def render(context):\n    return None\n')
    _write(ext, 'plugin.json', json.dumps({
        'schema_version': 1,
        'plugin_type': 'extension',
        'id': 'demo_extension',
        'name': 'Demo Extension',
        'version': '1.0.0',
        'menu': {'id': 'demo_extension', 'label': 'Demo', 'section': 'Analysis'},
        'entrypoint': 'extension.py',
    }))

    found = discover_plugins(root)
    assert [p.id for p in found if isinstance(p, Plugin)] == ['demo_extension']


def test_discovery_deduplicates_flat_symlink_and_organized_source(tmp_path):
    root = tmp_path / 'project'
    fam = root / 'plugins' / 'families' / 'demo_family'
    fam.mkdir(parents=True)
    _write(fam, 'family.json', json.dumps({'name': 'demo'}))
    _write(fam, 'plugin.json', json.dumps(_family_manifest()))
    (root / 'plugins' / 'demo_family').symlink_to(fam, target_is_directory=True)

    found = [p for p in discover_plugins(root) if isinstance(p, Plugin)]
    assert [p.id for p in found] == ['demo_family']


def _claim_family(tmp_path, manifest_overrides=None, *, pid='claim_family'):
    root = tmp_path / pid
    root.mkdir()
    _write(root, 'family.json', json.dumps({'name': pid}))
    manifest = {
        'schema_version': 1,
        'id': pid,
        'name': pid.replace('_', ' ').title(),
        'version': '1.0.0',
        'family': {'kind': 'json', 'file': 'family.json'},
    }
    manifest.update(manifest_overrides or {})
    _write(root, 'plugin.json', json.dumps(manifest))
    return read_plugin(root)


def test_family_historical_rank_does_not_become_generic_rank(tmp_path):
    from rank42.plugins import family_rank_claim, family_rank_label
    plugin = _claim_family(tmp_path, {
        'historical_generic_rank_lower': 14,
        'verified_generic_rank_lower': None,
        'generic_rank_claim_state': 'historical_record',
    })
    assert plugin.generic_rank is None
    assert plugin.variants[0].generic_rank is None
    assert plugin.historical_generic_rank_lower == 14
    claim = family_rank_claim(plugin)
    assert claim['effective_lower'] is None
    assert claim['historical_lower'] == 14
    assert claim['verified'] is False
    assert family_rank_label(plugin, compact=True) == 'historical generic claim ≥14 · unverified'


def test_family_historical_rank_does_not_affect_default_selection(tmp_path):
    from rank42.family_loader import resolve_family_spec
    project = tmp_path / 'project'
    hist = project / 'plugins' / 'historical'
    operational = project / 'plugins' / 'operational'
    hist.mkdir(parents=True); operational.mkdir(parents=True)
    _write(hist, 'family.py', 'def name(): return "Historical"\n')
    _write(hist, 'plugin.json', json.dumps({
        'schema_version': 1, 'id': 'historical', 'name': 'Historical', 'version': '1',
        'historical_generic_rank_lower': 99,
        'generic_rank_claim_state': 'historical_record',
        'family': {'kind': 'module', 'spec': 'historical_family', 'file': 'family.py'},
    }))
    _write(operational, 'family.py', 'def name(): return "Operational"\n')
    _write(operational, 'plugin.json', json.dumps({
        'schema_version': 1, 'id': 'operational', 'name': 'Operational', 'version': '1',
        'generic_rank': 1,
        'family': {'kind': 'module', 'spec': 'operational_family', 'file': 'family.py'},
    }))
    assert resolve_family_spec(None, project) == 'operational_family'


@pytest.mark.parametrize('field,value', [
    ('historical_generic_rank_lower', -1),
    ('verified_generic_rank_lower', '14'),
    ('generic_rank', True),
])
def test_family_claim_rank_values_are_nonnegative_integers_or_null(tmp_path, field, value):
    root = tmp_path / f'bad_{field}'
    root.mkdir()
    _write(root, 'family.json', '{}')
    manifest = {
        'schema_version': 1, 'id': f'bad_{field}', 'name': 'Bad', 'version': '1',
        'family': {'kind': 'json', 'file': 'family.json'}, field: value,
    }
    if field == 'verified_generic_rank_lower':
        manifest['generic_rank_claim_state'] = 'generic_lower_bound_verified'
    _write(root, 'plugin.json', json.dumps(manifest))
    with pytest.raises(ValueError, match='integer >= 0 or null'):
        read_plugin(root)


def test_family_verified_rank_requires_valid_claim_state(tmp_path):
    root = tmp_path / 'bad_state'
    root.mkdir(); _write(root, 'family.json', '{}')
    _write(root, 'plugin.json', json.dumps({
        'schema_version': 1, 'id': 'bad_state', 'name': 'Bad State', 'version': '1',
        'verified_generic_rank_lower': 14,
        'generic_rank_claim_state': 'sections_verified',
        'family': {'kind': 'json', 'file': 'family.json'},
    }))
    with pytest.raises(ValueError, match='verified_generic_rank_lower requires'):
        read_plugin(root)


def test_exact_constant_curve_control_is_a_verified_claim_state(tmp_path):
    from rank42.plugins import family_rank_claim
    plugin = _claim_family(tmp_path, {
        'verified_generic_rank_lower': 5,
        'generic_rank_claim_state': 'exact_constant_curve_control',
    }, pid='exact_constant_control')
    claim = family_rank_claim(plugin)
    assert plugin.generic_rank == 5
    assert claim['verified'] is True
    assert claim['state'] == 'exact_constant_curve_control'
    assert claim['status'] == 'Exact constant-curve control · Rank Hunter verified'


def test_exact_constant_curve_control_requires_verified_rank(tmp_path):
    root = tmp_path / 'missing_constant_verified'
    root.mkdir(); _write(root, 'family.json', '{}')
    _write(root, 'plugin.json', json.dumps({
        'schema_version': 1,
        'id': 'missing_constant_verified',
        'name': 'Missing Constant Verified',
        'version': '1',
        'generic_rank_claim_state': 'exact_constant_curve_control',
        'family': {'kind': 'json', 'file': 'family.json'},
    }))
    with pytest.raises(ValueError, match='requires verified_generic_rank_lower'):
        read_plugin(root)


def test_family_verified_state_requires_verified_rank(tmp_path):
    root = tmp_path / 'missing_verified'
    root.mkdir(); _write(root, 'family.json', '{}')
    _write(root, 'plugin.json', json.dumps({
        'schema_version': 1, 'id': 'missing_verified', 'name': 'Missing', 'version': '1',
        'historical_generic_rank_lower': 14,
        'generic_rank_claim_state': 'generic_lower_bound_verified',
        'family': {'kind': 'json', 'file': 'family.json'},
    }))
    with pytest.raises(ValueError, match='requires verified_generic_rank_lower'):
        read_plugin(root)


def test_legacy_and_verified_rank_must_agree_when_both_present(tmp_path):
    root = tmp_path / 'mismatch'
    root.mkdir(); _write(root, 'family.json', '{}')
    _write(root, 'plugin.json', json.dumps({
        'schema_version': 1, 'id': 'mismatch', 'name': 'Mismatch', 'version': '1',
        'generic_rank': 13,
        'verified_generic_rank_lower': 14,
        'generic_rank_claim_state': 'generic_lower_bound_verified',
        'family': {'kind': 'json', 'file': 'family.json'},
    }))
    with pytest.raises(ValueError, match='must agree'):
        read_plugin(root)


def test_legacy_generic_rank_remains_backward_compatible(tmp_path):
    plugin = _claim_family(tmp_path, {'generic_rank': 17}, pid='legacy_rank')
    assert plugin.generic_rank == 17
    assert plugin.variants[0].generic_rank == 17
    assert plugin.verified_generic_rank_lower is None


def _fake_family(*, validator=None, generic_rank=14):
    class Curve:
        def discriminant(self): return 1
    class Family:
        def name(self): return 'Fake Family'
        def generic_rank(self): return generic_rank
        def curve(self, _t): return Curve()
    family = Family()
    if validator is not None:
        family.validate_generic_rank_claim = validator
    return family


def _verified_claim_plugin(tmp_path, *, pid='verified_claim'):
    root = tmp_path / pid
    root.mkdir()
    _write(root, 'family.py', 'def name(): return "placeholder"\n')
    _write(root, 'plugin.json', json.dumps({
        'schema_version': 1, 'id': pid, 'name': 'Verified Claim', 'version': '1',
        'historical_generic_rank_lower': 14,
        'verified_generic_rank_lower': 14,
        'generic_rank_claim_state': 'generic_lower_bound_verified',
        'family': {'kind': 'module', 'spec': f'{pid}_module', 'file': 'family.py'},
    }))
    return read_plugin(root)


def test_family_verified_rank_requires_verification_method(tmp_path, monkeypatch):
    import rank42.family_loader as family_loader
    from rank42.plugins import validate_plugin
    plugin = _verified_claim_plugin(tmp_path, pid='missing_method')
    monkeypatch.setattr(family_loader, 'load_family', lambda _spec: _fake_family(validator=None))
    with pytest.raises(ValueError, match='requires validate_generic_rank_claim'):
        validate_plugin(plugin, import_science=True)


def test_family_verified_rank_rejects_failed_certificate(tmp_path, monkeypatch):
    import rank42.family_loader as family_loader
    from rank42.plugins import validate_plugin
    plugin = _verified_claim_plugin(tmp_path, pid='failed_cert')
    monkeypatch.setattr(family_loader, 'load_family', lambda _spec: _fake_family(
        validator=lambda: {'verified': False, 'lower_bound': 14}
    ))
    with pytest.raises(ValueError, match='did not verify'):
        validate_plugin(plugin, import_science=True)


def test_family_verified_rank_accepts_matching_certificate(tmp_path, monkeypatch):
    import rank42.family_loader as family_loader
    from rank42.plugins import validate_plugin
    plugin = _verified_claim_plugin(tmp_path, pid='good_cert')
    monkeypatch.setattr(family_loader, 'load_family', lambda _spec: _fake_family(
        validator=lambda: {
            'verified': True,
            'lower_bound': 14,
            'method': 'exact specialization injectivity + certified independent specialization',
            'certificate_version': 'test-1',
        }
    ))
    result = validate_plugin(plugin, import_science=True)
    cert = result['variants'][0]['generic_rank_certificate']
    assert cert['verified'] is True
    assert cert['lower_bound'] == 14
    assert plugin.generic_rank == 14


def test_formula_family_missing_rank_is_none(monkeypatch):
    import importlib
    import sys
    import types
    fake_all = types.ModuleType('sage.all')
    for name in ('QQ', 'GF', 'EllipticCurve', 'PolynomialRing', 'sage_eval'):
        setattr(fake_all, name, object())
    fake_sage = types.ModuleType('sage')
    fake_sage.all = fake_all
    monkeypatch.setitem(sys.modules, 'sage', fake_sage)
    monkeypatch.setitem(sys.modules, 'sage.all', fake_all)
    sys.modules.pop('rank42.formula_family', None)
    formula_family = importlib.import_module('rank42.formula_family')
    family = formula_family.FormulaFamily({
        'name': 'No rank', 'a_invariants': ['0', '0', '0', '0', '1']
    })
    assert family.generic_rank() is None


def test_extension_menu_icon_is_parsed_and_retained(tmp_path):
    root = tmp_path / 'icon_extension'
    root.mkdir()
    _write(root, 'extension.py', 'def render(context):\n    return None\n')
    _write(root, 'plugin.json', json.dumps({
        'schema_version': 1, 'plugin_type': 'extension', 'id': 'icon_ext', 'name': 'Icon Extension',
        'version': '1.0.0',
        'menu': {'id': 'icon_ext', 'label': 'Icon Workspace', 'section': 'Analysis',
                 'icon': ':material/scatter_plot:'},
        'entrypoint': 'extension.py',
    }))
    plugin = read_plugin(root)
    assert plugin.extension_pages[0].icon == ':material/scatter_plot:'


def test_extension_menu_icon_is_optional(tmp_path):
    root = tmp_path / 'no_icon_extension'
    root.mkdir()
    _write(root, 'extension.py', 'def render(context):\n    return None\n')
    _write(root, 'plugin.json', json.dumps({
        'schema_version': 1, 'plugin_type': 'extension', 'id': 'no_icon', 'name': 'No Icon',
        'version': '1.0.0',
        'menu': {'id': 'no_icon', 'label': 'Workspace', 'section': 'Plugins'},
        'entrypoint': 'extension.py',
    }))
    plugin = read_plugin(root)
    assert plugin.extension_pages[0].icon is None


def test_extension_multiple_pages_keep_individual_icons(tmp_path):
    root = tmp_path / 'multi_icon_extension'
    root.mkdir()
    _write(root, 'extension.py', 'def render(context):\n    return None\n')
    _write(root, 'plugin.json', json.dumps({
        'schema_version': 1, 'plugin_type': 'extension', 'id': 'multi_icon', 'name': 'Multi Icon',
        'version': '1.0.0',
        'menu': [
            {'id': 'one', 'label': 'One', 'section': 'Analysis', 'icon': ':material/explore:'},
            {'id': 'two', 'label': 'Two', 'section': 'Data', 'icon': ':material/scatter_plot:'},
        ],
        'entrypoint': 'extension.py',
    }))
    plugin = read_plugin(root)
    assert [(p.id, p.icon) for p in plugin.extension_pages] == [
        ('one', ':material/explore:'), ('two', ':material/scatter_plot:')
    ]


def test_extension_invalid_menu_icon_is_rejected(tmp_path):
    root = tmp_path / 'bad_icon_extension'
    root.mkdir()
    _write(root, 'extension.py', 'def render(context):\n    return None\n')
    _write(root, 'plugin.json', json.dumps({
        'schema_version': 1, 'plugin_type': 'extension', 'id': 'bad_icon', 'name': 'Bad Icon',
        'version': '1.0.0',
        'menu': {'id': 'bad_icon', 'label': 'Bad', 'section': 'Analysis', 'icon': 'scatter_plot'},
        'entrypoint': 'extension.py',
    }))
    with pytest.raises(ValueError, match='expected :material/<icon_name>:'):
        read_plugin(root)


def test_extension_validation_reports_menu_icon(tmp_path):
    from rank42.plugins import validate_plugin
    root = tmp_path / 'validated_icon_extension'
    root.mkdir()
    _write(root, 'extension.py', 'def render(context):\n    return None\n')
    _write(root, 'plugin.json', json.dumps({
        'schema_version': 1, 'plugin_type': 'extension', 'id': 'validated_icon', 'name': 'Validated Icon',
        'version': '1.0.0',
        'menu': {'id': 'validated_icon', 'label': 'Validated', 'section': 'Analysis',
                 'icon': ':material/scatter_plot:'},
        'entrypoint': 'extension.py',
    }))
    plugin = read_plugin(root)
    result = validate_plugin(plugin, import_science=False)
    assert result['pages'][0]['icon'] == ':material/scatter_plot:'
    assert result["navigation_contract"]["api_version"] == 1
    assert result["navigation_contract"]["sections"] == sorted(EXTENSION_MENU_SECTIONS)


def test_plugin_level_icon_is_available_for_family_card(tmp_path):
    root = tmp_path / 'family_icon'
    root.mkdir()
    _write(root, 'family.json', '{}')
    _write(root, 'plugin.json', json.dumps({
        'schema_version': 1, 'id': 'family_icon', 'name': 'Family Icon', 'version': '1',
        'icon': ':material/function:',
        'family': {'kind': 'json', 'file': 'family.json'},
    }))
    plugin = read_plugin(root)
    assert plugin.icon == ':material/function:'


def test_plugin_level_icon_is_available_for_feature_card(tmp_path):
    root = tmp_path / 'feature_icon'
    root.mkdir()
    _write(root, 'feature.py', 'def render(context):\n    return None\n')
    _write(root, 'plugin.json', json.dumps({
        'schema_version': 1, 'plugin_type': 'feature', 'id': 'feature_icon',
        'name': 'Feature Icon', 'version': '1', 'icon': ':material/bolt:',
        'hooks': ['curves.actions'], 'entrypoint': 'feature.py',
    }))
    plugin = read_plugin(root)
    assert plugin.icon == ':material/bolt:'


def test_extension_card_icon_falls_back_to_first_menu_icon(tmp_path):
    root = tmp_path / 'extension_card_icon'
    root.mkdir()
    _write(root, 'extension.py', 'def render(context):\n    return None\n')
    _write(root, 'plugin.json', json.dumps({
        'schema_version': 1, 'plugin_type': 'extension', 'id': 'extension_card_icon',
        'name': 'Extension Card Icon', 'version': '1',
        'menu': {'id': 'workspace', 'label': 'Workspace', 'section': 'Analysis',
                 'icon': ':material/scatter_plot:'},
        'entrypoint': 'extension.py',
    }))
    plugin = read_plugin(root)
    assert plugin.icon == ':material/scatter_plot:'


def test_extension_top_level_icon_overrides_menu_icon_for_card(tmp_path):
    root = tmp_path / 'extension_top_icon'
    root.mkdir()
    _write(root, 'extension.py', 'def render(context):\n    return None\n')
    _write(root, 'plugin.json', json.dumps({
        'schema_version': 1, 'plugin_type': 'extension', 'id': 'extension_top_icon',
        'name': 'Extension Top Icon', 'version': '1', 'icon': ':material/explore:',
        'menu': {'id': 'workspace', 'label': 'Workspace', 'section': 'Analysis',
                 'icon': ':material/scatter_plot:'},
        'entrypoint': 'extension.py',
    }))
    plugin = read_plugin(root)
    assert plugin.icon == ':material/explore:'
    assert plugin.extension_pages[0].icon == ':material/scatter_plot:'


def test_invalid_plugin_level_icon_is_rejected(tmp_path):
    root = tmp_path / 'bad_plugin_icon'
    root.mkdir()
    _write(root, 'family.json', '{}')
    _write(root, 'plugin.json', json.dumps({
        'schema_version': 1, 'id': 'bad_plugin_icon', 'name': 'Bad Plugin Icon', 'version': '1',
        'icon': 'function',
        'family': {'kind': 'json', 'file': 'family.json'},
    }))
    with pytest.raises(ValueError, match='expected :material/<icon_name>:'):
        read_plugin(root)


def test_plugin_validation_reports_card_icon(tmp_path):
    from rank42.plugins import validate_plugin
    root = tmp_path / 'validation_card_icon'
    root.mkdir()
    _write(root, 'extension.py', 'def render(context):\n    return None\n')
    _write(root, 'plugin.json', json.dumps({
        'schema_version': 1, 'plugin_type': 'extension', 'id': 'validation_card_icon',
        'name': 'Validation Card Icon', 'version': '1', 'icon': ':material/extension:',
        'menu': {'id': 'workspace', 'label': 'Workspace', 'section': 'Analysis'},
        'entrypoint': 'extension.py',
    }))
    plugin = read_plugin(root)
    result = validate_plugin(plugin, import_science=False)
    assert result['icon'] == ':material/extension:'


def test_family_search_presets_are_manifest_driven(tmp_path):
    root = tmp_path / "preset_family"
    root.mkdir()
    _write(root, "family.json", json.dumps({"name": "preset"}))
    _write(root, "plugin.json", json.dumps({
        "schema_version": 1,
        "id": "preset_family",
        "name": "Preset Family",
        "version": "1",
        "family": {"kind": "json", "file": "family.json"},
        "search_presets": [
            {
                "id": "scan",
                "label": "Scan",
                "description": "Cheap broad screening.",
                "candidate": {
                    "a_min": -5000, "a_max": 5000, "b_min": 1, "b_max": 500,
                    "stage_bounds": "200,500,1000",
                    "stage_keeps": "10000,2500,500",
                    "top": 500, "engine": "sieve",
                },
                "family": {"limit": 50, "descent_timeout": 30},
            },
        ],
    }))
    plugin = read_plugin(root)
    presets = search_presets_for_variant(plugin, plugin.variants[0])
    assert len(presets) == 1
    assert presets[0]["id"] == "scan"
    assert presets[0]["candidate"]["b_max"] == 500
    assert presets[0]["family"]["descent_timeout"] == 30


def test_variant_search_presets_override_top_level(tmp_path):
    root = tmp_path / "variant_presets"
    root.mkdir()
    _write(root, "family.json", "{}")
    _write(root, "family2.json", "{}")
    _write(root, "plugin.json", json.dumps({
        "schema_version": 1,
        "id": "variant_presets",
        "name": "Variant Presets",
        "version": "1",
        "search_presets": [
            {"id": "scan", "label": "Scan", "candidate": {"top": 100}},
        ],
        "variants": [
            {
                "id": "one", "name": "One",
                "family": {"kind": "json", "file": "family.json"},
            },
            {
                "id": "two", "name": "Two",
                "family": {"kind": "json", "file": "family2.json"},
                "search_presets": [
                    {"id": "deep", "label": "Deep", "candidate": {"top": 900}},
                ],
            },
        ],
    }))
    plugin = read_plugin(root)
    one, two = plugin.variants
    assert [p["id"] for p in search_presets_for_variant(plugin, one)] == ["scan"]
    assert [p["id"] for p in search_presets_for_variant(plugin, two)] == ["deep"]


def test_family_search_presets_reject_bad_candidate_keys(tmp_path):
    root = tmp_path / "bad_presets"
    root.mkdir()
    _write(root, "family.json", "{}")
    _write(root, "plugin.json", json.dumps({
        "schema_version": 1,
        "id": "bad_presets",
        "name": "Bad Presets",
        "version": "1",
        "family": {"kind": "json", "file": "family.json"},
        "search_presets": [
            {"id": "oops", "candidate": {"mystery_knob": 123}},
        ],
    }))
    with pytest.raises(ValueError, match="unknown candidate keys"):
        read_plugin(root)


def test_family_search_presets_reject_duplicate_ids(tmp_path):
    root = tmp_path / "duplicate_presets"
    root.mkdir()
    _write(root, "family.json", "{}")
    _write(root, "plugin.json", json.dumps({
        "schema_version": 1,
        "id": "duplicate_presets",
        "name": "Duplicate Presets",
        "version": "1",
        "family": {"kind": "json", "file": "family.json"},
        "search_presets": [
            {"id": "scan", "candidate": {"top": 100}},
            {"id": "scan", "candidate": {"top": 200}},
        ],
    }))
    with pytest.raises(ValueError, match="missing/duplicate id"):
        read_plugin(root)


def test_family_corpus_manifest_is_parsed(tmp_path):
    root = tmp_path / "corpus_family"
    root.mkdir()
    _write(root, "family.json", "{}")
    _write(root, "build_corpus.py", "def main():\n    return 0\n")
    _write(root, "plugin.json", json.dumps({
        "schema_version": 1,
        "id": "corpus_family",
        "name": "Corpus Family",
        "version": "1",
        "family": {"kind": "json", "file": "family.json"},
        "corpora": [{
            "id": "history",
            "name": "Historical Corpus",
            "cache_file": "curves2.db",
            "builder": "build_corpus.py",
            "variant_family_keys": {"default": "demo"},
        }],
    }))
    plugin = read_plugin(root)
    assert len(plugin.corpora) == 1
    corpus = plugin.corpora[0]
    assert corpus.id == "history"
    assert corpus.cache_file == "curves2.db"
    assert corpus.builder_path == (root / "build_corpus.py").resolve()
    assert corpus.family_key("default") == "demo"


def test_plugin_corpus_rejects_nested_cache_path(tmp_path):
    root = tmp_path / "bad_corpus"
    root.mkdir()
    _write(root, "family.json", "{}")
    _write(root, "plugin.json", json.dumps({
        "schema_version": 1,
        "id": "bad_corpus",
        "name": "Bad Corpus",
        "version": "1",
        "family": {"kind": "json", "file": "family.json"},
        "corpora": [{
            "id": "history",
            "name": "Historical Corpus",
            "cache_file": "../curves2.db",
        }],
    }))
    with pytest.raises(ValueError, match="plain filename"):
        read_plugin(root)


def test_family_torsion_provider_hints_are_normalized(tmp_path):
    root = tmp_path / "torsion_family"
    root.mkdir()
    _write(root, "family.json", "{}")
    _write(root, "plugin.json", json.dumps({
        "schema_version": 1,
        "id": "torsion_family",
        "name": "Torsion Family",
        "version": "1",
        "torsion_groups": ["Z/7Z", "C2xC6"],
        "family": {"kind": "json", "file": "family.json"},
    }))
    plugin = read_plugin(root)
    assert variant_torsion_groups(plugin, plugin.variants[0]) == ("C7", "C2 × C6")


def test_variant_torsion_provider_hints_override_plugin_level(tmp_path):
    root = tmp_path / "torsion_variants"
    root.mkdir()
    _write(root, "one.json", "{}")
    _write(root, "two.json", "{}")
    _write(root, "plugin.json", json.dumps({
        "schema_version": 1,
        "id": "torsion_variants",
        "name": "Torsion Variants",
        "version": "1",
        "torsion_groups": ["C2"],
        "variants": [
            {
                "id": "one",
                "name": "One",
                "family": {"kind": "json", "file": "one.json"},
            },
            {
                "id": "two",
                "name": "Two",
                "torsion_groups": ["C2xC4"],
                "family": {"kind": "json", "file": "two.json"},
            },
        ],
    }))
    plugin = read_plugin(root)
    one, two = plugin.variants
    assert variant_torsion_groups(plugin, one) == ("C2",)
    assert variant_torsion_groups(plugin, two) == ("C2 × C4",)


def test_family_torsion_provider_hints_reject_non_mazur_group(tmp_path):
    root = tmp_path / "bad_torsion_family"
    root.mkdir()
    _write(root, "family.json", "{}")
    _write(root, "plugin.json", json.dumps({
        "schema_version": 1,
        "id": "bad_torsion_family",
        "name": "Bad Torsion Family",
        "version": "1",
        "torsion_groups": ["C11"],
        "family": {"kind": "json", "file": "family.json"},
    }))
    with pytest.raises(ValueError, match="unsupported rational torsion group"):
        read_plugin(root)



def test_prescribed_torsion_provider_record_metadata(tmp_path):
    root = tmp_path / "torsion_record_family"
    root.mkdir()
    _write(root, "family.json", "{}")
    _write(root, "plugin.json", json.dumps({
        "schema_version": 1,
        "id": "torsion_record_family",
        "name": "Torsion Record Family",
        "version": "1",
        "torsion_groups": ["C2xC8"],
        "torsion_provider_role": "canonical_universal",
        "torsion_record": {
            "rank_lower": 3,
            "goal_rank": 4,
            "source": {"name": "record table", "as_of": "2026-09-19"},
            "secondary_family_search_recommended": False,
        },
        "family": {"kind": "json", "file": "family.json"},
    }))
    plugin = read_plugin(root)
    variant = plugin.variants[0]
    assert variant_torsion_groups(plugin, variant) == ("C2 × C8",)
    assert variant_torsion_provider_role(plugin, variant) == "canonical_universal"
    assert variant_torsion_record(plugin, variant) == {
        "rank_lower": 3,
        "goal_rank": 4,
        "source": {"name": "record table", "as_of": "2026-09-19"},
        "secondary_family_search_recommended": False,
    }


def test_torsion_record_goal_must_exceed_record(tmp_path):
    root = tmp_path / "bad_torsion_record"
    root.mkdir()
    _write(root, "family.json", "{}")
    _write(root, "plugin.json", json.dumps({
        "schema_version": 1,
        "id": "bad_torsion_record",
        "name": "Bad Torsion Record",
        "version": "1",
        "torsion_groups": ["C8"],
        "torsion_provider_role": "canonical_universal",
        "torsion_record": {"rank_lower": 6, "goal_rank": 6},
        "family": {"kind": "json", "file": "family.json"},
    }))
    with pytest.raises(ValueError, match="goal_rank"):
        read_plugin(root)


def test_torsion_provider_role_requires_declared_group(tmp_path):
    root = tmp_path / "bad_torsion_provider"
    root.mkdir()
    _write(root, "family.json", "{}")
    _write(root, "plugin.json", json.dumps({
        "schema_version": 1,
        "id": "bad_torsion_provider",
        "name": "Bad Torsion Provider",
        "version": "1",
        "torsion_provider_role": "canonical_universal",
        "family": {"kind": "json", "file": "family.json"},
    }))
    with pytest.raises(ValueError, match="requires torsion_groups"):
        read_plugin(root)



def test_legacy_verified_exact_claim_state_remains_loadable(tmp_path):
    root = tmp_path / "legacy_verified_exact"
    root.mkdir()
    _write(root, "family.json", "{}")
    _write(root, "plugin.json", json.dumps({
        "schema_version": 1,
        "id": "legacy_verified_exact",
        "name": "Legacy Verified Exact",
        "version": "1",
        "generic_rank": 17,
        "generic_rank_claim_state": "verified_exact",
        "family": {"kind": "json", "file": "family.json"},
    }))
    plugin = read_plugin(root)
    assert plugin.generic_rank == 17
    assert plugin.generic_rank_claim_state == "verified_exact"



def test_discovery_reuses_cached_plugin_parse_until_manifest_changes(tmp_path, monkeypatch):
    import rank42.plugins as plugins

    project = tmp_path / "project"
    root = project / "plugins" / "families" / "cached_family"
    root.mkdir(parents=True)
    _write(root, "family.json", json.dumps({"name": "cached"}))
    manifest_path = _write(
        root,
        "plugin.json",
        json.dumps(_family_manifest(pid="cached_family", name="Cached Family")),
    )

    plugins._cached_plugin_record.cache_clear()
    real_read_plugin = plugins.read_plugin
    calls = []

    def tracked_read_plugin(plugin_root):
        calls.append(Path(plugin_root))
        return real_read_plugin(plugin_root)

    monkeypatch.setattr(plugins, "read_plugin", tracked_read_plugin)

    first = plugins.discover_plugins(project)
    second = plugins.discover_plugins(project)
    assert [p.name for p in first if isinstance(p, plugins.Plugin)] == ["Cached Family"]
    assert [p.name for p in second if isinstance(p, plugins.Plugin)] == ["Cached Family"]
    assert len(calls) == 1

    updated = _family_manifest(pid="cached_family", name="Cached Family Updated")
    manifest_path.write_text(json.dumps(updated), encoding="utf-8")

    third = plugins.discover_plugins(project)
    assert [p.name for p in third if isinstance(p, plugins.Plugin)] == [
        "Cached Family Updated"
    ]
    assert len(calls) == 2


def test_discovery_cache_preserves_symlink_runtime_root(tmp_path):
    import rank42.plugins as plugins

    project = tmp_path / "project"
    source = project / "plugins" / "families" / "demo_family"
    source.mkdir(parents=True)
    _write(source, "family.json", json.dumps({"name": "demo"}))
    _write(source, "plugin.json", json.dumps(_family_manifest()))
    link = project / "plugins" / "demo_family"
    link.symlink_to(source, target_is_directory=True)

    plugins._cached_plugin_record.cache_clear()
    expected_root = plugins.read_plugin(link).root
    found = [p for p in plugins.discover_plugins(project) if isinstance(p, plugins.Plugin)]
    assert len(found) == 1
    assert found[0].root == expected_root == source.resolve()



def test_family_validation_reports_research_hook_capabilities(tmp_path):
    from rank42.plugins import validate_plugin

    root = tmp_path / "covering_hooks"
    root.mkdir()
    _write(root, "family.json", json.dumps({
        "name": "Covering Hooks",
        "a_invariants": ["0", "0", "0", "0", "1"],
    }))
    _write(
        root,
        "adapter.py",
        "def derive_pipeline_coverings(payload):\n"
        "    return []\n"
        "def run_pipeline_padic_covering_search(payload):\n"
        "    return {'status': 'unsupported'}\n",
    )
    _write(root, "plugin.json", json.dumps({
        "schema_version": 1,
        "id": "covering_hooks",
        "name": "Covering Hooks",
        "version": "1.0.0",
        "family": {"kind": "json", "file": "family.json"},
        "search_adapter": "adapter.py",
    }))

    plugin = read_plugin(root)
    result = validate_plugin(plugin, import_science=False)
    assert result["research_hooks"] == {
        "derive_pipeline_coverings": True,
        "derive_pipeline_transform": False,
        "run_pipeline_higher_descent": False,
        "run_pipeline_padic_covering_search": True,
    }



def test_extension_cannot_register_under_core_owned_system_section(tmp_path):
    root = tmp_path / "system_extension"
    root.mkdir()
    _write(root, "extension.py", "def render(context):\n    return None\n")
    _write(root, "plugin.json", json.dumps({
        "schema_version": 1,
        "plugin_type": "extension",
        "id": "system_extension",
        "name": "System Extension",
        "version": "1.0.0",
        "menu": {
            "id": "system_extension",
            "label": "System Extension",
            "section": "System",
        },
        "entrypoint": "extension.py",
    }))

    with pytest.raises(ValueError, match="core-owned and privileged"):
        read_plugin(root)
    assert "System" not in EXTENSION_MENU_SECTIONS


def test_privileged_feature_hook_requires_manifest_privilege(tmp_path):
    root = tmp_path / "system_feature_missing_privilege"
    root.mkdir()
    _write(root, "feature.py", "def render(context):\n    return None\n")
    _write(root, "plugin.json", json.dumps({
        "schema_version": 1,
        "plugin_type": "feature",
        "id": "system_feature_missing_privilege",
        "name": "System Feature",
        "version": "1.0.0",
        "hooks": ["settings.after_header"],
        "entrypoint": "feature.py",
    }))

    with pytest.raises(ValueError, match="requires system privilege"):
        read_plugin(root)


def test_manifest_cannot_self_allowlist_privileged_system_hook(tmp_path):
    root = tmp_path / "system_feature_untrusted"
    root.mkdir()
    _write(root, "feature.py", "def render(context):\n    return None\n")
    _write(root, "plugin.json", json.dumps({
        "schema_version": 1,
        "plugin_type": "feature",
        "id": "system_feature_untrusted",
        "name": "System Feature",
        "version": "1.0.0",
        "hooks": ["settings.after_header"],
        "system_privileges": ["system.settings"],
        "entrypoint": "feature.py",
    }))

    assert SYSTEM_FEATURE_PLUGIN_ALLOWLIST == {}
    with pytest.raises(ValueError, match="not core-allowlisted"):
        read_plugin(root)
    assert feature_hook_allowed(
        "system_feature_untrusted",
        "settings.after_header",
        {"system.settings"},
    ) is False


def test_core_allowlist_and_manifest_privilege_must_both_agree(
    tmp_path,
    monkeypatch,
):
    from rank42.plugins import validate_plugin

    root = tmp_path / "trusted_system_feature"
    root.mkdir()
    _write(root, "feature.py", "def render(context):\n    return None\n")
    _write(root, "plugin.json", json.dumps({
        "schema_version": 1,
        "plugin_type": "feature",
        "id": "trusted_system_feature",
        "name": "Trusted System Feature",
        "version": "1.0.0",
        "hooks": ["settings.after_header"],
        "system_privileges": ["system.settings"],
        "entrypoint": "feature.py",
    }))
    monkeypatch.setitem(
        SYSTEM_FEATURE_PLUGIN_ALLOWLIST,
        "trusted_system_feature",
        frozenset({"system.settings"}),
    )

    plugin = read_plugin(root)
    assert plugin.feature_hooks == ("settings.after_header",)
    assert plugin.system_privileges == frozenset({"system.settings"})
    assert feature_hook_allowed(
        plugin.id,
        "settings.after_header",
        plugin.system_privileges,
    ) is True
    validation = validate_plugin(plugin)
    assert validation["system_privileges"] == ["system.settings"]
    assert validation["hook_contract"]["api_version"] == 1
    assert validation["hook_contract"]["hooks"] == ["settings.after_header"]
    assert validation["hook_contract"]["deprecated_aliases_used"] == []


def test_unused_system_privilege_is_rejected(tmp_path, monkeypatch):
    root = tmp_path / "unused_system_privilege"
    root.mkdir()
    _write(root, "feature.py", "def render(context):\n    return None\n")
    _write(root, "plugin.json", json.dumps({
        "schema_version": 1,
        "plugin_type": "feature",
        "id": "unused_system_privilege",
        "name": "Unused Privilege",
        "version": "1.0.0",
        "hooks": ["curves.actions"],
        "system_privileges": ["system.settings"],
        "entrypoint": "feature.py",
    }))
    monkeypatch.setitem(
        SYSTEM_FEATURE_PLUGIN_ALLOWLIST,
        "unused_system_privilege",
        frozenset({"system.settings"}),
    )

    with pytest.raises(ValueError, match="unused feature system privilege"):
        read_plugin(root)


def test_extension_host_defensively_filters_stale_system_page(
    tmp_path,
    monkeypatch,
):
    from rank42 import ui_extensions

    entry = tmp_path / "extension.py"
    entry.write_text("def render(context):\n    return None\n", encoding="utf-8")
    page = ExtensionPage(
        "stale-system",
        "Stale System",
        "System",
        entry,
        None,
    )
    plugin = Plugin(
        id="stale_system_extension",
        name="Stale System Extension",
        version="1",
        plugin_type="extension",
        root=tmp_path,
        family_spec=None,
        manifest={"enabled_by_default": True},
        capabilities=frozenset(),
        adapter_path=None,
        extension_pages=(page,),
    )
    monkeypatch.setattr(ui_extensions, "discover_plugins", lambda root: [plugin])
    monkeypatch.setattr(ui_extensions, "is_enabled", lambda db, item: True)

    assert ui_extensions.enabled_extension_pages(None, tmp_path) == []


def test_extension_render_host_rejects_stale_system_page(
    tmp_path,
    monkeypatch,
):
    from types import SimpleNamespace

    from rank42 import ui_extensions

    entry = tmp_path / "extension.py"
    entry.write_text(
        "def render(context):\n    raise AssertionError('must not render')\n",
        encoding="utf-8",
    )
    page = ExtensionPage(
        "stale-system-direct",
        "Stale System Direct",
        "System",
        entry,
        None,
    )
    plugin = Plugin(
        id="stale_system_extension_direct",
        name="Stale System Extension Direct",
        version="1",
        plugin_type="extension",
        root=tmp_path,
        family_spec=None,
        manifest={"enabled_by_default": True},
        capabilities=frozenset(),
        adapter_path=None,
        extension_pages=(page,),
    )
    monkeypatch.setattr(
        ui_extensions,
        "load_extension",
        lambda item, item_page: (_ for _ in ()).throw(
            AssertionError("stale System Extension was loaded")
        ),
    )
    ctx = SimpleNamespace(
        project_root=tmp_path,
        db_path=tmp_path / "rank42.db",
    )

    with pytest.raises(ValueError, match="core-owned surfaces"):
        ui_extensions.render_extension(None, ctx, plugin, page)


def test_feature_host_defensively_skips_unapproved_system_hook(
    tmp_path,
    monkeypatch,
):
    from types import SimpleNamespace

    from rank42 import feature_hooks
    import rank42.plugins as plugins_module

    entry = tmp_path / "feature.py"
    entry.write_text("def render(context):\n    raise AssertionError('must not render')\n", encoding="utf-8")
    plugin = Plugin(
        id="stale_system_feature",
        name="Stale System Feature",
        version="1",
        plugin_type="feature",
        root=tmp_path,
        family_spec=None,
        manifest={"enabled_by_default": True},
        capabilities=frozenset(),
        adapter_path=None,
        feature_entrypoint=entry,
        feature_hooks=("settings.after_header",),
        system_privileges=frozenset({"system.settings"}),
    )
    monkeypatch.setattr(
        plugins_module,
        "discover_plugins",
        lambda root: [plugin],
    )
    monkeypatch.setattr(
        plugins_module,
        "is_enabled",
        lambda db, item: True,
    )
    monkeypatch.setattr(
        plugins_module,
        "load_feature",
        lambda item: (_ for _ in ()).throw(
            AssertionError("unapproved System Feature was loaded")
        ),
    )

    ctx = SimpleNamespace(
        project_root=tmp_path,
        db_path=tmp_path / "rank42.db",
    )
    feature_hooks.render_feature_hook(
        None,
        ctx,
        "settings.after_header",
    )



def _write_search_adapter_family(
    tmp_path,
    *,
    pid,
    adapter_source,
    search_presets=None,
    capabilities=("family_search", "target_search"),
):
    root = tmp_path / pid
    root.mkdir()
    _write(root, "family.json", json.dumps({"name": pid}))
    _write(root, "adapter.py", adapter_source)
    manifest = {
        "schema_version": 1,
        "id": pid,
        "name": pid.replace("_", " ").title(),
        "version": "1.0.0",
        "family": {"kind": "json", "file": "family.json"},
        "search_adapter": "adapter.py",
        "capabilities": list(capabilities),
    }
    if search_presets is not None:
        manifest["search_presets"] = search_presets
    _write(root, "plugin.json", json.dumps(manifest))
    return read_plugin(root)


def test_plugin_validation_checks_family_and_target_option_schemas_separately(
    tmp_path,
):
    from rank42.plugins import validate_plugin

    plugin = _write_search_adapter_family(
        tmp_path,
        pid="context_schemas",
        adapter_source=(
            "def search_options(context='family'):\n"
            "    if context == 'target':\n"
            "        return [\n"
            "            {'key':'stages','type':'str','default':'1000'},\n"
            "            {'key':'exact_candidates','type':'int','default':8,'min':0,'max':32},\n"
            "        ]\n"
            "    return [\n"
            "        {'key':'quick_timeout','type':'int','default':30,'min':1,'max':60},\n"
            "        {'key':'fast_screen','type':'bool','default':True},\n"
            "    ]\n"
            "def build_family_search_command(**kwargs): return ['family']\n"
            "def build_target_search_command(**kwargs): return ['target']\n"
        ),
        search_presets=[
            {
                "id": "balanced",
                "label": "Balanced",
                "family": {
                    "quick_timeout": 40,
                    "fast_screen": False,
                    "limit": 12,
                },
                "target": {
                    "stages": "1000,10000",
                    "exact_candidates": 16,
                    "backend": "Plugin",
                    "ratpoints_backend": "CPU",
                    "goal_rank": 5,
                },
            },
        ],
    )

    result = validate_plugin(plugin, import_science=False)

    assert [rec["key"] for rec in result["adapter_options"]] == [
        "quick_timeout",
        "fast_screen",
    ]
    assert [
        rec["key"]
        for rec in result["adapter_option_schemas"]["family"]
    ] == ["quick_timeout", "fast_screen"]
    assert [
        rec["key"]
        for rec in result["adapter_option_schemas"]["target"]
    ] == ["stages", "exact_candidates"]


@pytest.mark.parametrize(
    "section,bad_key",
    [
        ("family", "quick_timeuot"),
        ("target", "exact_candiates"),
    ],
)
def test_plugin_validation_rejects_unknown_family_or_target_preset_keys(
    tmp_path,
    section,
    bad_key,
):
    from rank42.plugins import validate_plugin

    plugin = _write_search_adapter_family(
        tmp_path,
        pid=f"bad_{section}_preset",
        adapter_source=(
            "def search_options(context='family'):\n"
            "    if context == 'target':\n"
            "        return [{'key':'exact_candidates','type':'int','default':8,'min':0}]\n"
            "    return [{'key':'quick_timeout','type':'int','default':30,'min':1}]\n"
            "def build_family_search_command(**kwargs): return ['family']\n"
            "def build_target_search_command(**kwargs): return ['target']\n"
        ),
        search_presets=[
            {
                "id": "oops",
                "label": "Oops",
                section: {bad_key: 10},
            },
        ],
    )

    with pytest.raises(
        ValueError,
        match=rf"unknown {section} keys: {bad_key}",
    ):
        validate_plugin(plugin, import_science=False)


def test_plugin_validation_rejects_duplicate_context_option_keys(tmp_path):
    from rank42.plugins import validate_plugin

    plugin = _write_search_adapter_family(
        tmp_path,
        pid="duplicate_option_keys",
        adapter_source=(
            "def search_options(context='family'):\n"
            "    if context == 'target': return []\n"
            "    return [\n"
            "        {'key':'timeout','type':'int','default':10,'min':1},\n"
            "        {'key':'timeout','type':'int','default':20,'min':1},\n"
            "    ]\n"
            "def build_family_search_command(**kwargs): return ['family']\n"
            "def build_target_search_command(**kwargs): return ['target']\n"
        ),
    )

    with pytest.raises(
        ValueError,
        match="duplicate family search option key 'timeout'",
    ):
        validate_plugin(plugin, import_science=False)


@pytest.mark.parametrize(
    "option_source,match",
    [
        (
            "{'key':'timeout','type':'int','default':0,'min':1}",
            "timeout must be >= 1",
        ),
        (
            "{'key':'mode','type':'choice','default':'bad','choices':['a','b']}",
            "mode must be one of",
        ),
        (
            "{'key':'mode','type':'choice','default':'a','choices':[]}",
            "requires non-empty choices",
        ),
        (
            "{'key':'flag','type':'bool','default':'yes'}",
            "flag must be boolean",
        ),
        (
            "{'key':'mystery','type':'float','default':1.5}",
            "unsupported type 'float'",
        ),
    ],
)
def test_plugin_validation_rejects_invalid_option_schema(
    tmp_path,
    option_source,
    match,
):
    from rank42.plugins import validate_plugin

    plugin = _write_search_adapter_family(
        tmp_path,
        pid="bad_option_schema_" + str(abs(hash(option_source))),
        adapter_source=(
            "def search_options(context='family'):\n"
            f"    return [{option_source}] if context == 'family' else []\n"
            "def build_family_search_command(**kwargs): return ['family']\n"
            "def build_target_search_command(**kwargs): return ['target']\n"
        ),
    )

    with pytest.raises(ValueError, match=match):
        validate_plugin(plugin, import_science=False)


def test_plugin_validation_supports_legacy_no_context_option_provider(
    tmp_path,
):
    from rank42.plugins import validate_plugin

    plugin = _write_search_adapter_family(
        tmp_path,
        pid="legacy_no_context_options",
        adapter_source=(
            "def search_options():\n"
            "    return [{'key':'timeout','type':'int','default':10,'min':1,'max':60}]\n"
            "def build_family_search_command(**kwargs): return ['family']\n"
            "def build_target_search_command(**kwargs): return ['target']\n"
        ),
        search_presets=[
            {
                "id": "shared",
                "family": {"timeout": 20, "limit": 5},
                "target": {"timeout": 30, "backend": "Plugin"},
            },
        ],
    )

    result = validate_plugin(plugin, import_science=False)

    assert result["adapter_option_schemas"]["family"] == (
        result["adapter_option_schemas"]["target"]
    )
    assert result["adapter_option_schemas"]["family"][0]["key"] == "timeout"


def test_plugin_validation_rejects_invalid_special_preset_values(tmp_path):
    from rank42.plugins import validate_plugin

    plugin = _write_search_adapter_family(
        tmp_path,
        pid="bad_special_preset",
        adapter_source=(
            "def search_options(context='family'): return []\n"
            "def build_family_search_command(**kwargs): return ['family']\n"
            "def build_target_search_command(**kwargs): return ['target']\n"
        ),
        search_presets=[
            {
                "id": "bad",
                "family": {"limit": 0},
                "target": {"goal_rank": 0},
            },
        ],
    )

    with pytest.raises(ValueError, match="family.limit must be an integer >= 1"):
        validate_plugin(plugin, import_science=False)


def test_family_and_target_preset_apply_paths_do_not_silently_drop_unknown_keys():
    root = Path(__file__).resolve().parents[1]
    family_source = (
        root / "rank42" / "ui_pages" / "family_search.py"
    ).read_text(encoding="utf-8")
    target_source = (
        root / "rank42" / "ui_pages" / "target_curve.py"
    ).read_text(encoding="utf-8")

    assert "preset contains unknown Family search option" in family_source
    assert "preset contains unknown Target search option" in target_source
    assert "if rec is None:\n            continue" not in family_source[
        family_source.index("def _apply_family_preset"):
        family_source.index("def _family_preset_modified")
    ]
    assert "if rec is None:\n            continue" not in target_source[
        target_source.index("def _apply_target_preset"):
        target_source.index("def _target_preset_modified")
    ]



def test_plugin_validation_rejects_invalid_target_special_preset_value(tmp_path):
    from rank42.plugins import validate_plugin

    plugin = _write_search_adapter_family(
        tmp_path,
        pid="bad_target_special_preset",
        adapter_source=(
            "def search_options(context='family'): return []\n"
            "def build_family_search_command(**kwargs): return ['family']\n"
            "def build_target_search_command(**kwargs): return ['target']\n"
        ),
        search_presets=[
            {
                "id": "bad-target",
                "target": {"goal_rank": 0},
            },
        ],
    )

    with pytest.raises(
        ValueError,
        match="target.goal_rank must be an integer >= 1",
    ):
        validate_plugin(plugin, import_science=False)


def test_plugin_archive_restore_preserves_validation_and_historical_resolution(tmp_path):
    from rank42.db import connect
    from rank42.plugins import (
        archive_plugin,
        get_plugin,
        is_archived,
        is_enabled,
        plugin_state,
        restore_plugin,
        set_plugin_state,
    )

    project = tmp_path / "project"
    root = project / "plugins" / "extensions" / "archive_demo"
    root.mkdir(parents=True)
    _write(root, "extension.py", "def render(context):\n    return None\n")
    _write(root, "plugin.json", json.dumps({
        "schema_version": 1,
        "plugin_type": "extension",
        "id": "archive_demo",
        "name": "Archive Demo",
        "version": "1.0.0",
        "menu": {"id": "archive-demo", "label": "Archive Demo", "section": "Analysis"},
        "entrypoint": "extension.py",
    }))
    plugin = read_plugin(root)
    db = connect(tmp_path / "plugin-state.db")
    validation = {
        "status": "ready",
        "fingerprints": {"plugin_manifest_sha256": "manifest"},
    }
    set_plugin_state(
        db,
        plugin.id,
        enabled=True,
        status="ready",
        validation=validation,
    )

    archive_plugin(db, plugin)

    archived = plugin_state(db, plugin.id)
    assert archived["status"] == "archived"
    assert archived["enabled"] == 0
    assert json.loads(archived["validation_json"]) == validation
    assert is_archived(db, plugin) is True
    assert is_enabled(db, plugin) is False
    assert get_plugin(project, plugin.id).id == plugin.id

    restore_plugin(db, plugin)

    restored = plugin_state(db, plugin.id)
    assert restored["status"] == "disabled"
    assert restored["enabled"] == 0
    assert json.loads(restored["validation_json"]) == validation
    assert is_archived(db, plugin) is False
    assert get_plugin(project, plugin.id).id == plugin.id


def test_missing_plugin_package_remains_available_as_historical_provenance(tmp_path):
    import shutil

    from rank42.db import connect
    from rank42.plugins import (
        set_plugin_state,
        unavailable_plugin_states,
        validate_plugin,
    )

    project = tmp_path / "project"
    root = project / "plugins" / "extensions" / "missing_demo"
    root.mkdir(parents=True)
    _write(root, "extension.py", "def render(context):\n    return None\n")
    _write(root, "plugin.json", json.dumps({
        "schema_version": 1,
        "plugin_type": "extension",
        "id": "missing_demo",
        "name": "Missing Demo",
        "version": "2.3.4",
        "menu": {"id": "missing-demo", "label": "Missing Demo", "section": "Analysis"},
        "entrypoint": "extension.py",
    }))
    plugin = read_plugin(root)
    db = connect(tmp_path / "missing-plugin.db")
    validation = validate_plugin(plugin, import_science=False)
    set_plugin_state(
        db,
        plugin.id,
        enabled=True,
        status="ready",
        validation=validation,
    )

    assert validation["plugin_name"] == "Missing Demo"
    assert validation["source_path"] == str(root.resolve())

    shutil.rmtree(root)
    installed = [
        rec.id
        for rec in discover_plugins(project, include_superseded=True)
        if isinstance(rec, Plugin)
    ]
    unavailable = unavailable_plugin_states(db, installed)

    assert len(unavailable) == 1
    record = unavailable[0]
    assert record["status"] == "unavailable"
    assert record["plugin_id"] == "missing_demo"
    assert record["plugin_name"] == "Missing Demo"
    assert record["plugin_version"] == "2.3.4"
    assert record["plugin_type"] == "extension"
    assert record["previous_status"] == "ready"
    assert record["enabled"] is True
    assert record["source_path"] == str(root.resolve())
    assert record["fingerprints"] == validation["validation_fingerprints"]


def test_failed_revalidation_preserves_previous_plugin_provenance(tmp_path, monkeypatch):
    import sys

    import rank42.plugin_validate as plugin_validate
    from rank42.db import connect
    from rank42.plugins import plugin_state, set_plugin_state

    db = connect(tmp_path / "plugin-validate.db")
    previous = {
        "plugin_id": "missing_demo",
        "plugin_name": "Missing Demo",
        "plugin_version": "2.3.4",
        "plugin_type": "extension",
        "source_path": "/tmp/missing-demo",
        "validation_fingerprints": {"plugin_manifest_sha256": "abc"},
    }
    set_plugin_state(
        db,
        "missing_demo",
        enabled=True,
        status="ready",
        validation=previous,
    )
    monkeypatch.setattr(plugin_validate, "connect", lambda path: db)
    monkeypatch.setattr(
        plugin_validate,
        "get_plugin",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            KeyError("plugin package missing")
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "plugin_validate",
            "--db",
            str(tmp_path / "plugin-validate.db"),
            "--project-root",
            str(tmp_path),
            "--plugin",
            "missing_demo",
        ],
    )

    with pytest.raises(KeyError, match="plugin package missing"):
        plugin_validate.main()

    row = plugin_state(db, "missing_demo")
    payload = json.loads(row["validation_json"])
    assert row["status"] == "invalid"
    assert row["enabled"] == 0
    assert payload["plugin_name"] == "Missing Demo"
    assert payload["plugin_version"] == "2.3.4"
    assert payload["source_path"] == "/tmp/missing-demo"
    assert payload["validation_fingerprints"] == {
        "plugin_manifest_sha256": "abc"
    }
    assert "plugin package missing" in payload["last_validation_error"]


def test_validation_freshness_detects_installed_content_drift(tmp_path):
    from rank42.db import connect
    from rank42.plugins import (
        set_plugin_state,
        validate_plugin,
        validation_freshness,
    )

    root = tmp_path / "freshness_demo"
    root.mkdir()
    entry = _write(root, "extension.py", "def render(context):\n    return None\n")
    _write(root, "plugin.json", json.dumps({
        "schema_version": 1,
        "plugin_type": "extension",
        "id": "freshness_demo",
        "name": "Freshness Demo",
        "version": "1.0.0",
        "menu": {"id": "freshness-demo", "label": "Freshness Demo", "section": "Analysis"},
        "entrypoint": "extension.py",
    }))
    plugin = read_plugin(root)
    db = connect(tmp_path / "validation-freshness.db")
    validation = validate_plugin(plugin, import_science=False)
    set_plugin_state(
        db,
        plugin.id,
        enabled=True,
        status="ready",
        validation=validation,
    )

    fresh = validation_freshness(db, plugin)
    assert fresh["state"] == "ready"
    assert fresh["fresh"] is True
    assert fresh["changed"] == ()

    entry.write_text(
        "def render(context):\n    return context.plugin_id\n",
        encoding="utf-8",
    )
    changed_plugin = read_plugin(root)
    stale = validation_freshness(db, changed_plugin)

    assert stale["state"] == "needs_revalidation"
    assert stale["fresh"] is False
    assert "adapter_sha256" in stale["changed"]


@pytest.mark.parametrize(
    "legacy,canonical",
    [
        ("results.after_header", "manage.jobs.results.after_header"),
        ("external_catalog.after_header", "data.catalogs.after_header"),
        ("analysis.analyze.after_header", "analysis.work_center.after_header"),
        ("analysis.lattices.after_header", "analysis.mw_geometry.after_header"),
    ],
)
def test_legacy_feature_hook_ids_normalize_to_v1_semantic_contract(
    tmp_path,
    legacy,
    canonical,
):
    root = tmp_path / legacy.replace(".", "_")
    root.mkdir()
    _write(root, "feature.py", "def render(context):\n    return None\n")
    _write(root, "plugin.json", json.dumps({
        "schema_version": 1,
        "plugin_type": "feature",
        "id": root.name,
        "name": "Legacy Hook",
        "version": "1.0.0",
        "hooks": [legacy],
        "entrypoint": "feature.py",
    }))

    plugin = read_plugin(root)

    assert plugin.feature_hooks == (canonical,)
    assert canonical in SUPPORTED_FEATURE_HOOKS
    assert legacy not in SUPPORTED_FEATURE_HOOKS

    from rank42.plugins import validate_plugin
    validation = validate_plugin(plugin, import_science=False)
    assert validation["hook_contract"]["api_version"] == 1
    assert validation["hook_contract"]["accepted_aliases"][legacy] == canonical
    assert validation["hook_contract"]["deprecated_aliases_used"] == [
        {"from": legacy, "to": canonical}
    ]


def test_extension_resources_section_normalizes_to_plugins(tmp_path):
    root = tmp_path / "legacy_resources_extension"
    root.mkdir()
    _write(root, "extension.py", "def render(context):\n    return None\n")
    _write(root, "plugin.json", json.dumps({
        "schema_version": 1,
        "plugin_type": "extension",
        "id": "legacy_resources_extension",
        "name": "Legacy Resources Extension",
        "version": "1.0.0",
        "menu": {
            "id": "legacy-resources",
            "label": "Legacy Resources",
            "section": "Resources",
        },
        "entrypoint": "extension.py",
    }))

    plugin = read_plugin(root)

    assert plugin.extension_pages[0].section == "Plugins"

    from rank42.plugins import validate_plugin
    validation = validate_plugin(plugin, import_science=False)
    assert validation["navigation_contract"]["api_version"] == 1
    assert validation["navigation_contract"]["accepted_aliases"]["Resources"] == "Plugins"
    assert validation["navigation_contract"]["deprecated_aliases_used"] == [
        {"from": "Resources", "to": "Plugins"}
    ]


@pytest.mark.parametrize("section", ["Candidates", "System"])
def test_extension_navigation_v1_keeps_core_owned_sections_closed(tmp_path, section):
    root = tmp_path / f"closed_{section.lower()}"
    root.mkdir()
    _write(root, "extension.py", "def render(context):\n    return None\n")
    _write(root, "plugin.json", json.dumps({
        "schema_version": 1,
        "plugin_type": "extension",
        "id": f"closed_{section.lower()}",
        "name": "Closed Section",
        "version": "1.0.0",
        "menu": {
            "id": "closed-section",
            "label": "Closed Section",
            "section": section,
        },
        "entrypoint": "extension.py",
    }))

    with pytest.raises(ValueError):
        read_plugin(root)


def test_feature_host_dispatches_stale_alias_object_to_canonical_hook(
    tmp_path,
    monkeypatch,
):
    from contextlib import nullcontext
    from types import SimpleNamespace

    from rank42 import feature_hooks
    import rank42.plugins as plugins_module

    rendered = []
    entry = tmp_path / "feature.py"
    entry.write_text("def render(context):\n    return None\n", encoding="utf-8")
    plugin = Plugin(
        id="stale_results_hook",
        name="Stale Results Hook",
        version="1",
        plugin_type="feature",
        root=tmp_path,
        family_spec=None,
        manifest={"enabled_by_default": True},
        capabilities=frozenset(),
        adapter_path=None,
        feature_entrypoint=entry,
        feature_hooks=("results.after_header",),
    )

    monkeypatch.setattr(plugins_module, "discover_plugins", lambda root: [plugin])
    monkeypatch.setattr(plugins_module, "is_enabled", lambda db, item: True)
    monkeypatch.setattr(
        plugins_module,
        "load_feature",
        lambda item: SimpleNamespace(
            render=lambda context: rendered.append(context.hook_id)
        ),
    )
    monkeypatch.setattr(feature_hooks, "region", lambda *args, **kwargs: nullcontext())

    ctx = SimpleNamespace(
        project_root=tmp_path,
        db_path=tmp_path / "rank42.db",
    )
    feature_hooks.render_feature_hook(
        None,
        ctx,
        "manage.jobs.results.after_header",
    )

    assert rendered == ["manage.jobs.results.after_header"]


def test_extension_host_normalizes_stale_resources_page(tmp_path, monkeypatch):
    from rank42 import ui_extensions

    entry = tmp_path / "extension.py"
    entry.write_text("def render(context):\n    return None\n", encoding="utf-8")
    page = ExtensionPage(
        "legacy-resources",
        "Legacy Resources",
        "Resources",
        entry,
        None,
    )
    plugin = Plugin(
        id="stale_resources_extension",
        name="Stale Resources Extension",
        version="1",
        plugin_type="extension",
        root=tmp_path,
        family_spec=None,
        manifest={"enabled_by_default": True},
        capabilities=frozenset(),
        adapter_path=None,
        extension_pages=(page,),
    )
    monkeypatch.setattr(ui_extensions, "discover_plugins", lambda root: [plugin])
    monkeypatch.setattr(ui_extensions, "is_enabled", lambda db, item: True)

    rows = ui_extensions.enabled_extension_pages(None, tmp_path)

    assert len(rows) == 1
    assert rows[0][2].section == "Plugins"
