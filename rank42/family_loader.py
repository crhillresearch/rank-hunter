"""Generic family loading through Rank Hunter's plugin registry.

Bundled family mathematics, aliases, and source paths are owned by
``plugins/<family>/plugin.json``. Core resolves those manifest declarations
through :mod:`rank42.plugins`; it contains no bundled-family alias table.

Explicit ``json:/path`` and importable Python module specs remain supported as
CLI escape hatches for backward compatibility and local research experiments.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
from pathlib import Path

REQUIRED_SEARCH_METHODS = ("name", "curve", "curve_mod_p")


def sha256_file(path):
    path=Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() and path.is_file() else None


def _project_root(project_root=None):
    return Path(project_root or Path(__file__).resolve().parents[1]).resolve()


def _registry_families(project_root=None):
    """Yield (plugin, variant, manifest-family-definition) from the registry."""
    from rank42.plugins import Plugin, discover_plugins, variant_family_definition
    for plugin in discover_plugins(_project_root(project_root), include_superseded=True):
        if not isinstance(plugin,Plugin) or plugin.plugin_type!='family':
            continue
        for variant in plugin.variants:
            yield plugin,variant,variant_family_definition(plugin,variant)


def _resolve_plugin_family(spec, project_root=None):
    wanted=str(spec)
    for plugin,variant,family in _registry_families(project_root):
        aliases={str(x).strip() for x in (family.get('aliases') or []) if str(x).strip()}
        aliases.add(str(variant.family_spec))
        declared=str(family.get('spec') or '').strip()
        if declared:
            aliases.add(declared)
        if wanted in aliases:
            return plugin,variant,family
    return None


def resolve_family_spec(spec=None, project_root=None):
    """Return a canonical manifest-declared family spec.

    ``None`` selects the family plugin marked ``default_family``. If no plugin
    carries that marker, the highest operational generic rank is used. Historical
    claim metadata is intentionally ignored. This keeps
    no-flag CLI behavior deterministic without hardcoding a family name in core.
    """
    if spec not in {None,''}:
        rec=_resolve_plugin_family(str(spec),project_root)
        return str(rec[1].family_spec) if rec else str(spec)
    choices=[]
    for plugin,variant,family in _registry_families(project_root):
        operational_rank = variant.generic_rank if variant.generic_rank is not None else plugin.generic_rank
        choices.append((
            bool(plugin.manifest.get('default_family')),
            int(operational_rank if operational_rank is not None else -1),
            plugin.id,
            str(variant.family_spec),
        ))
    if not choices:
        raise ValueError('no family plugins are installed')
    choices.sort(key=lambda rec:(not rec[0],-rec[1],rec[2],rec[3]))
    return choices[0][3]


def family_source_path(spec, project_root=None):
    spec=resolve_family_spec(spec,project_root) if spec in {None,''} else str(spec)
    if spec.startswith('json:'):
        return Path(spec[5:]).resolve()
    rec=_resolve_plugin_family(spec,project_root)
    if rec is not None:
        plugin,variant,family=rec
        kind=str(family.get('kind') or 'json')
        if kind=='json':
            return (plugin.root/str(family.get('file') or 'family.json')).resolve()
        if kind=='module' and family.get('file'):
            return (plugin.root/str(family['file'])).resolve()
    try:
        found=importlib.util.find_spec(spec)
    except (ImportError,AttributeError,ValueError):
        found=None
    origin=getattr(found,'origin',None) if found else None
    if origin and origin not in {'built-in','frozen'}:
        return Path(origin).resolve()
    return None


def family_source_sha256(spec, project_root=None):
    path=family_source_path(spec,project_root)
    return sha256_file(path) if path else None


def _validate_family(family, *, need_sections=False):
    required=list(REQUIRED_SEARCH_METHODS)
    if need_sections:
        required.append('generic_section_points')
    missing=[name for name in required if not callable(getattr(family,name,None))]
    if missing:
        raise ValueError('family is missing required callable(s): '+', '.join(missing))
    return family


def _load_file_module(spec,path):
    path=Path(path).resolve()
    if not path.exists():
        raise ImportError(f'plugin family source not found: {path}')
    name='rank42_plugin_family_'+hashlib.sha256(f'{spec}:{path}'.encode()).hexdigest()[:16]
    module_spec=importlib.util.spec_from_file_location(name,path)
    if module_spec is None or module_spec.loader is None:
        raise ImportError(f'cannot load plugin family source {path}')
    module=importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def load_family(spec=None, *, need_sections=False, project_root=None):
    spec=resolve_family_spec(spec,project_root)
    if spec.startswith('json:'):
        from rank42.formula_family import FormulaFamily
        return _validate_family(FormulaFamily.from_json(Path(spec[5:])),need_sections=need_sections)

    rec=_resolve_plugin_family(spec,project_root)
    if rec is not None:
        plugin,variant,family=rec
        kind=str(family.get('kind') or 'json')
        if kind=='json':
            from rank42.formula_family import FormulaFamily
            obj=FormulaFamily.from_json(plugin.root/str(family.get('file') or 'family.json'))
        elif kind=='module' and family.get('file'):
            obj=_load_file_module(spec,plugin.root/str(family['file']))
        else:
            obj=importlib.import_module(str(family.get('spec') or spec))
        return _validate_family(obj,need_sections=need_sections)

    # Explicit external module spec: supported, but not a bundled alias.
    return _validate_family(importlib.import_module(spec),need_sections=need_sections)


def family_generic_rank(family):
    value=getattr(family,'generic_rank',None)
    if callable(value): value=value()
    return None if value is None else int(value)


def _declared_nagao_cache_identity(family):
    value=getattr(family,'nagao_cache_identity',None)
    if callable(value): value=value()
    value=str(value).strip() if value is not None else ''
    return value or None


def family_cache_key(spec,family):
    source=family_source_path(spec)
    if source is None:
        module_file=getattr(family,'__file__',None)
        if module_file:
            source=Path(module_file)
    identity=_declared_nagao_cache_identity(family)
    if identity is not None:
        module_name=getattr(family,'__name__',None) or family.__class__.__name__
        raw=f'nagao-cache-v1\n{spec}\n{identity}'
        digest=hashlib.sha256(raw.encode()).hexdigest()[:16]
        stem=source.stem if source is not None else module_name.rsplit('.',1)[-1]
        return f'plugin-{stem}-{digest}'
    if source is not None and source.exists():
        digest=hashlib.sha256(source.read_bytes()).hexdigest()[:16]
        return f'plugin-{source.stem}-{digest}'
    source=getattr(family,'source_path',None)
    if source is not None:
        path=Path(source)
        digest=hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        return f'json-{path.stem}-{digest}'
    module_name=getattr(family,'__name__',None) or family.__class__.__name__
    raw=f'{spec}-{module_name}-{family.name()}'
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def family_cache_keys(spec,family):
    """Return the primary Nagao cache key followed by explicit legacy aliases.

    Families may declare ``nagao_cache_identity`` to keep the primary key
    stable across source edits that do not change finite-field score tables.
    ``nagao_cache_legacy_keys`` is an explicit compatibility assertion by the
    family owner; core never guesses that two source hashes describe the same
    arithmetic.
    """
    keys=[family_cache_key(spec,family)]
    legacy=getattr(family,'nagao_cache_legacy_keys',())
    if callable(legacy): legacy=legacy()
    if isinstance(legacy,str): legacy=[legacy]
    for value in legacy or ():
        value=str(value).strip()
        if value and value not in keys:
            keys.append(value)
    return tuple(keys)
