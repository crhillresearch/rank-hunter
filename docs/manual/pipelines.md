# Pipelines

A Pipeline is a persisted, validated sequence of research stages applied to a population.

It is not just a macro. Pipelines give Rank Hunter a durable stage graph with explicit contracts, configuration, retry behavior, population lineage, and evidence boundaries.

## What a Pipeline controls

A Pipeline can coordinate:

- candidate screening;
- candidate rescoring;
- population filtering;
- exact curve materialization;
- bounded rational-point search;
- prime/local arithmetic;
- family-specific search hooks;
- transforms that create child populations;
- pointed quartic / covering work;
- independence;
- saturation;
- descent / upper bounds;
- stop conditions.

Each stage has a declared category, supported target modes, required capabilities, provided capabilities, default configuration, and evidence class.

## Why stage contracts matter

The Builder validates a Pipeline before execution.

A stage that requires a capability cannot legally appear before a stage that provides it.

Examples of capabilities include:

- `source`;
- `exact_curve`;
- `point_source`;
- `rigorous_lower_possible`;
- `upper_bound_possible`;
- `derived_population`.

This prevents a UI recipe from looking sensible while being impossible for the runner to execute.

If validation reports:

```text
Step N · <stage>: move/add a prerequisite stage providing <capability>
```

fix the stage order or add the missing producer. Do not bypass the validator by pretending the capability already exists.

## Stage categories

The exact catalog evolves, but stages broadly fall into these groups.

### Candidates

Operate on a pool before per-curve work.

Examples include Nagao screening/rescoring and corpus filters.

Candidate stages must remain before per-curve stages.

### Points

Search or expose rational points.

Examples include direct/native searches and bounded chart searches.

A point-producing stage does not automatically raise rank. Returned points still enter the exact ledger/certification path.

### Geometry

Create/search alternative models such as coverings, quartics, or Family-defined geometry.

These stages may be exact in their transforms while still using heuristic search budgets.

### Evidence

Attempt exact independence, rank bounds, saturation-derived evidence, or other proof-oriented work.

### Transforms / Strategies

May create child curves/populations or orchestrate capability-driven sub-strategies.

When a stage fans out into child curves, Rank Hunter resets curve-local capabilities so evidence from the parent is not accidentally inherited.

### Control

Stop/selection stages decide whether execution continues; they do not themselves prove mathematics.

## A practical Pipeline

A high-rank Family Pipeline might look like:

```text
Candidate Pool
→ Nagao Screen
→ local/prime screen
→ exact point search
→ exact independence
→ geometry / covering search
→ exact independence
→ Stop at Rank Goal
```

A proof-oriented Pipeline on already-retained curves might instead look like:

```text
Exact curve
→ expose/certify point source
→ independence
→ saturation
→ descent / rank bound
```

Do not add expensive stages simply because they exist. Every stage should answer a specific research question.

## Point-search stages

Bounded point searches normally specify:

- heights;
- chart budget;
- denominator range where relevant;
- model mode;
- timeout;
- retry policy;
- exact-certificate budget.

Timeout/error outcomes are incomplete coverage, not completed zero-yield searches.

A completed no-hit search means only that no finite point was found in the configured region.

## Retry semantics

Expensive stages can declare retry behavior.

Typical policies:

- `manual` — do not automatically rerun;
- `automatic` — retry at the configured retry budget;
- `escalated` — retry with a larger budget.

For escalated retries, the retry timeout must actually exceed the first timeout.

Retries are operational policy. They do not alter the mathematical meaning of a timeout.

## Population transforms

Some stages create children rather than merely annotate the current curve.

Examples may include twists, base changes, Family fibration changes, isogeny walks, or plugin-defined exact transforms.

Rank Hunter records parent/child derivation provenance. A child begins with its own exact-curve state; it does not automatically inherit the parent's points or rank evidence.

## Family/plugin hooks

Core can call isolated plugin hooks for capabilities such as:

- deriving exact coverings;
- higher descent;
- p-adic covering search;
- pipeline transforms.

If a plugin does not implement a hook, the stage should report unsupported/capability absent rather than silently substitute unrelated mathematics.

Plugin-returned rational points still undergo exact reconstruction and certification in core.

## Pipeline state

A Pipeline definition and a Pipeline Run are different objects.

The definition describes the intended stage sequence.

A Run records execution over a specific target population, including:

- stage index;
- candidate state;
- cached/completed attempts;
- child derivations;
- retry attempts;
- stage result payloads;
- final best candidate/lower bound.

## Resume

Resume the existing Run when you want to continue the same experiment.

Starting a new run with visually identical settings creates a new provenance record and may repeat work that the old run had already checkpointed.

## CLI

Run or resume a saved Pipeline Run:

```bash
sage -python -m rank42.pipeline_runner \
  --db rank42.db \
  --project-root . \
  --run-id 42
```

The runner emits a machine-readable final marker:

```text
RANK42_PIPELINE_RESULT=...
```

Use `--help` against the release you are running for current options.

## How to debug a Pipeline

When a Pipeline behaves unexpectedly, check in this order:

1. Builder validation error;
2. exact stage list/config actually saved;
3. target mode and population;
4. stage result/status;
5. worker log;
6. current curve research state;
7. whether the stage timed out or completed;
8. whether any returned point was exact but dependent.

Avoid diagnosing only from the last line of console output.

## Proof boundary

A Pipeline automates work. It does not weaken the evidence model.

A heuristic stage remains heuristic when embedded in a Pipeline. A plugin stage remains subject to core exact verification. Exact rank still requires a closed rigorous interval.

## Related documentation

- [Search](search.md)
- [Candidate Pools](candidates.md)
- [Jobs & Campaigns](jobs-campaigns.md)
- [Architecture](../reference/architecture.md)
- [Data Model](../reference/data-model.md)
