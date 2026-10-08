# `saturate`

Saturate the stored rigorous point basis over a bounded prime range without requiring full mwrank descent.

Saturation improves/checks subgroup index. It does not normally add a new free-rank direction.

## Range mode

```bash
sage -python -m rank42.cli saturate \
  --db rank42.db \
  --curve-id 123 \
  --min-prime 2 \
  --max-prime 100 \
  --timeout 900
```

Range mode asks the saturation engine to handle the configured range under one hard timeout.

## Ladder mode

```bash
sage -python -m rank42.cli saturate \
  --db rank42.db \
  --curve-id 123 \
  --mode ladder \
  --min-prime 2 \
  --max-prime 100 \
  --timeout 120
```

In ladder mode, the timeout applies per-prime step.

This is useful when you want progress/failure associated with individual primes.

## Options

| Option | Default | Meaning |
| --- | ---: | --- |
| `--db` | `rank42.db` | Rank Hunter database |
| `--curve-id` | required | retained curve id |
| `--min-prime` | `2` | first saturation prime |
| `--max-prime` | `100` | final saturation prime |
| `--mode` | `range` | `range` or `ladder` |
| `--timeout` | `900` | range timeout or per-prime ladder timeout |

Final output begins with:

```text
RANK42_SATURATION=
```

## Preconditions

The useful input is the current rigorous witness basis.

If the curve has many exact points but no complete certified basis, resolve Independence first.

## Outcomes

A completed saturation result can report:

- saturated/replacement basis points;
- subgroup index information;
- prime/range coverage;
- elapsed time;
- evidence/provenance.

A nontrivial index means the original subgroup was not primitive over the searched range.

It does not mean rank increased.

## Timeout

A timeout means the requested range/step was not completed.

Do not mark the whole range “saturated” after a timeout.

## Why run it before hard independence/descent?

A better basis can reduce arithmetic complexity and improve:

- hard-case independence;
- height-lattice conditioning;
- known-subgroup computations.

This is a conditioning benefit, not a new rank theorem.

## Reproducibility

Record:

- curve id/model;
- basis/evidence state before the run;
- prime range;
- mode;
- timeout;
- final `RANK42_SATURATION` JSON.

See [Manual: Saturation](../manual/saturation.md).
