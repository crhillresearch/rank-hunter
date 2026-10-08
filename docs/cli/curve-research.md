# Curve Research from the CLI

## Rank bounds

```bash
sage -python -m rank42.cli rank-bounds \
  --db rank42.db \
  --curve-id 123 \
  --engine auto \
  --timeout 300
```

See [rank-bounds](rank-bounds.md).

## Classical descent attack

```bash
sage -python -m rank42.attack \
  --db rank42.db \
  --id 123 \
  --timeout 900
```

## Exact-rank certification attempt

```bash
sage -python -m rank42.certify_curve \
  --db rank42.db \
  --id 123 \
  --timeout 120
```

It only records exact rank when the rigorous evidence closes.

## Advanced upper-bound attack

```bash
sage -python -m rank42.advanced_upper_bound \
  --db rank42.db \
  --curve-id 123 \
  --backend simon_strong \
  --timeout 900
```

Available backends are `simon_strong` and `sage_proof_rank`.

This is for hard cases. A timeout remains inconclusive.

## Independence

```bash
sage -python -m rank42.independence_check \
  --db rank42.db \
  --curve-id 123 \
  --timeout 120
```

Restrict the attempt to selected Point Ledger rows by repeating `--point-id`.

Strategies:

```text
standard
saturation
trial-saturation
```

## Hard-case independence

```bash
sage -python -m rank42.hard_case_escalator \
  --db rank42.db \
  --curve-id 123 \
  --certificate-timeout 600
```

## Mordell-Weil relation attack

```bash
sage -python -m rank42.mw_relation_attack \
  --db rank42.db \
  --curve-id 123 \
  --timeout 180
```

## Saturation

```bash
sage -python -m rank42.cli saturate \
  --db rank42.db \
  --curve-id 123 \
  --max-prime 100 \
  --timeout 900
```

See [saturate](saturate.md).

## Height lattice

```bash
sage -python -m rank42.lattice \
  --db rank42.db \
  --curve-id 123 \
  --source stored-generators \
  --precision 256
```

List saved lattices:

```bash
sage -python -m rank42.lattice --db rank42.db --list
```

## Quartics workbench

Run a pointed search from a saved quartic search:

```bash
sage -python -m rank42.quartic_workbench_runner \
  --db rank42.db \
  --mode pointed \
  --search-id 17 \
  --height 100000 \
  --timeout 60
```

Search a stored covering:

```bash
sage -python -m rank42.quartic_workbench_runner \
  --db rank42.db \
  --mode covering \
  --covering-id 8 \
  --height 100000
```

Plan covering-local searches for a curve:

```bash
sage -python -m rank42.quartic_workbench_runner \
  --db rank42.db \
  --mode covering-local \
  --curve-id 123
```
