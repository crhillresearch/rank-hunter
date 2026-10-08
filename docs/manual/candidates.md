# Candidate Pools

A Candidate Pool is Rank Hunter's persistent search population.

It separates **cheap candidate generation** from **expensive scientific work** and makes the exact same population reusable across Search and Pipeline strategies.

## What a pool stores

Core persists pool-level information such as:

- pool name/id;
- plugin id and plugin version;
- Family spec;
- generation configuration;
- source/import path when applicable;
- Family/adapter/manifest fingerprints;
- status;
- candidate count;
- timestamps.

Each candidate row can store:

- deterministic rank order;
- parameter;
- optional `a` and `b` source coordinates;
- heuristic score;
- prime bound and number of prime terms;
- status such as `unsearched`;
- linked retained curve id;
- native Family/fiber identity;
- chart identity and map fingerprint;
- plugin-specific metadata.

The candidate table is an orchestration queue. It is not the authoritative curve evidence table.

## Generate a pool

Open **Candidates → Generate**.

The available controls depend on the selected Family/plugin. A generator may ask for parameter bounds, sample size, prime bound, score settings, or a top-N cutoff.

Use a descriptive pool name. Good names include the Family and purpose:

```text
mestre-sextuple-p523-top2000
x1152-neighborhood-oct06
general-short-A1e5-B1e5
```

Avoid names such as `test` once a pool becomes part of serious research.

## Pool ordering

Candidates are stored in descending score order when a pool is replaced/generated.

Rank order is persistent and can be used for deterministic slicing/resume.

Do not assume score values from two different algorithms or versions are directly comparable. Inspect scoring provenance.

## Candidate lifecycle

A typical candidate moves through:

```text
unsearched
→ searched
→ linked to a retained curve (if evidence survives)
```

A searched candidate may have no retained curve if the search produced no durable evidence.

The UI's human-readable outcome can distinguish:

- unsearched;
- searched with no retained evidence;
- timeout with no retained rank evidence;
- retained rank lower bound;
- retained exact rank;
- evidence conflict.

## Candidate vs. curve

This distinction is important.

Deleting a Candidate Pool deletes the pool's queue rows, but retained scientific curve rows are intentionally independent.

That means you can safely retire an orchestration population without erasing curves, points, lattices, or rank evidence already retained elsewhere.

## Provenance transfer

When a candidate becomes a retained curve, core copies available pool/candidate provenance onto the curve without blindly overwriting earlier provenance.

Possible transferred fields include:

- plugin id/version;
- Family spec;
- Family/adapter/manifest fingerprints;
- native fiber identity;
- native parameter;
- chart id/parameter/fingerprint.

This makes it possible to reconstruct where a curve came from later.

## Reconciliation

Core includes reconciliation paths for attaching candidate rows to already-existing matching curves.

UI render paths should not perform unbounded full-pool reconciliation implicitly. Maintenance/finalization paths reconcile bounded candidate slices when appropriate.

## Import/export

Candidate Pools can be exported as JSONL for reproducible interchange.

The exported records include pool identity and candidate metadata such as parameter, score, Family spec, and plugin id.

Imported JSONL becomes a new persistent pool.

CLI entry point:

```bash
sage -python -m rank42.candidate_generate --help
```

Use `--help` from the release you are running; Family-specific arguments can change with plugin capability and version.

## Reusing a pool

Reusing one pool is the cleanest way to compare:

- two Pipeline stage orders;
- CPU vs GPU point search;
- different time budgets;
- different retention thresholds;
- different exact-certification strategies.

Keep the population fixed and change one research variable at a time.

## Resume semantics

Core tracks absolute pool rank order and the first unfinished candidate.

When resuming, distinguish:

- “offset in the whole pool” from
- “offset in the filtered unsearched subset.”

Rank Hunter's pool helpers use absolute rank order for deterministic resume behavior.

## Scientific interpretation

A Candidate Pool is not a collection of proven elliptic curves.

A score can tell Rank Hunter **where to look**. Only exact/rigorous evidence attached to retained curves can support a rank claim.

## Related documentation

- [Search](search.md)
- [Pipelines](pipelines.md)
- [Curves & Points](curves.md)
- [Data Model](../reference/data-model.md)
