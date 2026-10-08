# Descent & Rank Bounds

Descent is used primarily to obtain a rigorous upper bound on Mordell–Weil rank.

It is most valuable after you already have a meaningful rigorous lower bound.

## Rank interval

Rank Hunter tracks a rigorous interval

\[
r_{\min} \le \operatorname{rank} E(\mathbf Q) \le r_{\max}.
\]

The lower bound usually comes from exact independent points.

The upper bound comes from a rigorous rank/descent method.

When the two agree, exact rank is known.

## Normal CLI path

```bash
sage -python -m rank42.cli rank-bounds \
  --db rank42.db \
  --curve-id 123 \
  --engine auto \
  --timeout 300
```

The command emits a machine-readable result marker:

```text
RANK42_RANK_RESULT=...
```

## Engines

The 0.9.2 rank pipeline supports engine selection:

```text
auto
pari
mwrank
```

Core also contains an optional eclib-rh integration when available, but public Rank Hunter does not require eclib-rh to operate.

### `auto`

Automatic mode is PARI-first.

If PARI closes the interval, no deeper fallback is necessary.

If PARI times out/errors, the release can use lighter/available fallbacks and, when explicitly escalated or necessary, mwrank.

The runner also remembers some deterministic engine-limit failures to avoid blindly repeating the same impossible call.

### `pari`

Use when you specifically want the PARI rank-bound path.

### `mwrank`

Use when you intentionally want the mwrank path and accept its cost/size constraints.

## Important options

From `rank42.cli rank-bounds`:

| Option | Meaning |
| --- | --- |
| `--curve-id` | retained curve id |
| `--engine` | `auto`, `pari`, or `mwrank` |
| `--timeout` | selected/PARI hard timeout |
| `--mwrank-timeout` | mwrank hard timeout |
| `--pari-stack-max-gib` | maximum PARI stack after overflow retry |
| `--escalate` | request deeper fallback chain |
| `--force` | ignore compatible cached evidence |

Use `--help` for the exact release parser.

## Caching

Compatible completed/inconclusive evidence can be reused or recognized so expensive work is not repeated without reason.

Use `--force` only when you intentionally want to recompute despite compatible cached state.

## Stronger research attacks

The source tree includes additional research commands such as:

```bash
sage -python -m rank42.attack --db rank42.db --id 123 --timeout 900
```

and:

```bash
sage -python -m rank42.advanced_upper_bound \
  --db rank42.db \
  --curve-id 123 \
  --timeout 900
```

Treat these as targeted research tools, not mandatory steps for every curve.

## Reading results

### Rigorous upper returned

If the engine returns a rigorous upper bound \(u\), Rank Hunter records it as rank evidence and reduces the live curve interval.

### Exact rank

If the live lower and upper bounds agree, Rank Hunter can record exact rank.

### Timeout

A timeout is inconclusive.

Do not publish “rank equals lower bound” because the upper-bound engine ran out of time.

### Error

An engine error is operational/implementation failure, not mathematical evidence.

### Evidence conflict

If a rigorous upper appears below the stored rigorous lower, Rank Hunter marks an inconsistent evidence state.

Investigate the exact model and provenance before trusting either side.

## Descent that returns points

Some descent/covering engines can also produce rational points.

Returned points still go through exact reconstruction and independence certification before raising the lower bound.

The engine's own reported lower bound is not automatically trusted as a Rank Hunter lower-bound promotion.

## When to escalate

Escalate when:

- the lower bound is significant;
- closing exact rank matters;
- the previous engine failed for a reason a different engine can avoid;
- the curve/model size remains feasible.

Do not escalate simply because the first engine timed out once on an unimportant curve.

## Related documentation

- [What Counts as Proof?](../getting-started/evidence.md)
- [Rank & Evidence](../mathematics/rank.md)
- [Independence](independence.md)
