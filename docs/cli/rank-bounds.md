# `rank-bounds`

Run Rank Hunter's bounded rigorous rank-bounds pipeline on one retained curve.

This command is for **upper-bound / exact-rank work**, not rational-point discovery.

## Basic use

```bash
sage -python -m rank42.cli rank-bounds \
  --db rank42.db \
  --curve-id 123 \
  --engine auto \
  --timeout 300
```

The command prints human progress plus a final machine-readable marker:

```text
RANK42_RANK_RESULT=...
```

Parse the JSON after that marker in automation.

## Preconditions

The curve id must refer to a retained curve with a valid stored five-term Weierstrass model.

Before spending significant time, inspect the current curve state:

- rigorous lower bound;
- existing upper-bound evidence;
- exact rank if already closed;
- prior engine failures/timeouts.

## Options

| Option | Default | Meaning |
| --- | ---: | --- |
| `--db` | `rank42.db` | Rank Hunter database |
| `--curve-id` | required | retained curve id |
| `--engine` | `auto` | `auto`, `pari`, or `mwrank` |
| `--timeout` | `300` | hard timeout for PARI/selected engine |
| `--mwrank-timeout` | `300` | hard timeout for mwrank |
| `--pari-stack-max-gib` | `4` | maximum PARI stack after overflow retry |
| `--escalate` | off | request deeper fallback chain |
| `--force` | off | ignore compatible cached evidence |

Use the installed release's `--help` as parser authority.

## Automatic mode

`--engine auto` is PARI-first.

The runner reduces the live rank state after each accepted evidence record.

If PARI closes the rank interval, no deeper engine is needed.

If PARI fails/times out, available fallbacks may run according to the release logic. Explicit `--escalate` requests deeper fallback work.

## Cache behavior

Compatible prior evidence may prevent unnecessary recomputation.

Use `--force` only when you intentionally want another engine attempt on the same exact model/configuration class.

Do not use `--force` merely because you dislike the previous mathematical answer.

## PARI stack limit

PARI can fail through stack exhaustion on large curves.

`--pari-stack-max-gib` places an upper bound on controlled stack escalation after an overflow.

This is a memory-safety budget, not a mathematical parameter.

## Outcomes

### Exact rank

If accepted rigorous lower and upper bounds meet, the curve state can close to exact rank.

### Rigorous upper only

The result may improve the upper bound without closing the gap.

### Timeout

Inconclusive. The existing lower/upper evidence remains.

### Engine error

Operational failure. Do not reinterpret it as “rank = lower bound.”

### Evidence conflict

If a new rigorous upper is below the existing rigorous lower, Rank Hunter flags the state as inconsistent for investigation.

## Example: explicit PARI run

```bash
sage -python -m rank42.cli rank-bounds \
  --db "$PWD/rank42.db" \
  --curve-id 417 \
  --engine pari \
  --timeout 900 \
  --pari-stack-max-gib 6
```

## Example: deliberate deeper escalation

```bash
sage -python -m rank42.cli rank-bounds \
  --db "$PWD/rank42.db" \
  --curve-id 417 \
  --engine auto \
  --timeout 300 \
  --mwrank-timeout 900 \
  --escalate
```

## Scientific interpretation

This command can support statements such as:

> PARI produced a rigorous rank upper bound 12; together with the certified lower bound 12, Rank Hunter records rank = 12.

It cannot support:

> PARI timed out, so the rank must equal the lower bound.

See [Descent & Rank Bounds](../manual/descent.md).
