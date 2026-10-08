# Write a Family Plugin

Use a Family plugin when the mathematics belongs to a parameterized elliptic-curve family.

## Layout

```text
families/my_family/
├── plugin.json
├── family.py
├── search_adapter.py
├── README.md
└── PROVENANCE.md
```

A simple formula family can use `family.json` instead of Python.

## Manifest

```json
{
  "schema_version": 1,
  "plugin_type": "family",
  "id": "my_family",
  "name": "My Family",
  "version": "0.1.0",
  "description": "One-parameter family for rank searches.",
  "enabled_by_default": false,
  "family": {
    "kind": "module",
    "spec": "my_family",
    "file": "family.py"
  },
  "validation_parameter": "1"
}
```

Use a stable lowercase id. Jobs and stored provenance may refer to it later.

## Family contract

A module-backed Family provides:

```python
def name():
    return "My Family"

def curve(t):
    # Sage EllipticCurve over QQ, or None at a bad specialization.
    ...

def curve_mod_p(r, p):
    # Matching curve over GF(p), or None when invalid mod p.
    ...
```

If you have exact generic sections:

```python
def generic_section_points(t):
    return [...]
```

Do not return numerically guessed sections here.

## Capabilities

Declare only what the plugin really supports.

Common capabilities:

```text
candidate_generation
family_search
target_search
known_subgroup
quartic_search
pgl2_search
free_search
pipeline_transform
constructive_family
```

If you declare Family/Target search, provide a search adapter.

## Search adapters

Return argument lists, not shell strings.

```python
def build_family_search_command(*, python, db, candidate_file, options):
    return [
        str(python),
        "my_worker.py",
        "--db", str(db),
        "--input", str(candidate_file),
    ]
```

Long arithmetic belongs in the worker, not in Streamlit render code.

## Search options and presets

Put user-facing controls in `plugin.json`.

Supported option types:

```text
int
bool
choice
str
```

Named presets can fill candidate, Family-search and Target-search controls together.

A preset is a convenience profile. It does not change the evidence rules.

## Variants

One plugin may own several related families.

Use variants when the equations/parameterizations belong together but need separate names, validation points or search settings.

Each variant needs:

- `id`;
- `name`;
- `family`.

## Generic-rank claims

Keep historical claims and Rank Hunter-verified claims separate.

Useful claim states include:

```text
historical_record
reconstructed_model
sections_verified
generic_lower_bound_verified
exact_constant_curve_control
```

If you declare `verified_generic_rank_lower`, provide a validator/certificate path. Do not turn a paper citation into local proof metadata.

## Torsion

A Family or variant can declare exact rational torsion targets, for example:

```json
{
  "torsion_groups": ["C2 × C4"],
  "torsion_provider_role": "canonical_universal"
}
```

Rank Hunter checks the validation specialization against the declaration.

## PGL2 charts

Advanced Families may publish exact Möbius charts.

Each chart uses an exact matrix `[A,B,C,D]`. Singular matrices are rejected. Declaring charts requires `pgl2_search`.

## Read-only corpora

A Family can declare a plugin-owned research corpus. Core owns read-only discovery/browsing; the plugin owns the builder and family-key mapping.

Corpus rank claims stay reference data until Rank Hunter reproduces the evidence.

## Provenance

Keep the paper, equations, reconstruction notes and claim boundary with the plugin.

A useful provenance note answers:

> What is proved, what was reconstructed, and what is only search guidance?

## Validate

```bash
sage -python -m rank42.plugin_validate \
  --project-root . \
  --db rank42.db \
  --plugin my_family
```

Before shipping, check:

- the validation specialization is nonsingular;
- `curve(t)` and `curve_mod_p` describe the same family;
- generic sections are exactly on the specialization;
- declared torsion matches;
- exact maps round-trip;
- heuristic screens never write rigorous rank evidence;
- at least one known-good and one failure/singular case are tested.
