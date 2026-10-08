# The Interface

Rank Hunter's Streamlit UI is a control surface over persistent SQLite state and isolated scientific workers. Pages launch or inspect work; the browser itself is not the scientific record.

Navigation names below refer to the canonical 0.9.2 routes. Older bookmarks may still be normalized through compatibility aliases.

## Dashboard

**Dashboard** summarizes current research state, jobs, plugins, and recent activity.

Use it for orientation, not for detailed evidence review. Open the underlying curve, job, or campaign when you need exact provenance.

## Search

**Search** is the primary entry point for new work.

It contains general and Family-driven search flows. Family plugins can provide parameter controls, candidate generation, presets, and search adapters.

Use **Search** when you know the population or Family you want to explore.

See [Search](search.md).

## Auto

**Auto** launches prebuilt search strategies with less manual stage configuration.

Use it when the preset matches your goal. For exact control over the stage graph, use **Pipelines**.

## Pipelines

**Pipelines** builds and runs reusable stage sequences.

A Pipeline can combine:

- candidate screening;
- point search;
- local/prime heuristics;
- exact transforms;
- geometry/coverings;
- independence;
- saturation;
- descent/rank evidence;
- control stages.

The Builder validates stage contracts before execution.

See [Pipelines](pipelines.md).

## Candidates

The canonical **Candidates** page contains generation and pool-management views.

Candidate Pools are durable search populations with plugin/family provenance and per-candidate status.

See [Candidate Pools](candidates.md).

## Curves

**Curves** is the durable scientific inventory.

Open a curve here when you need:

- exact Weierstrass model;
- current rank interval;
- point ledger;
- provenance;
- arithmetic metadata;
- attached search/evidence history.

Legacy routes such as “Work Center” and some analysis deep links normalize to Curves.

See [Curves & Points](curves.md).

## Points

**Points** is the point ledger.

Use it to inspect exact coordinates, discovery source, independence state, rigorous-witness role, and point-specific metadata.

Do not infer rank from row count.

## Target

**Target** runs deeper work on one retained curve while preserving its existing evidence and provenance.

See [Target Search](target.md).

## Descent

**Descent** runs rank-bound/descent work.

Use it when you have a meaningful lower bound and want a rigorous upper bound or exact rank.

See [Descent & Rank Bounds](descent.md).

## Independence

**Independence** checks whether exact points add genuinely new Mordell–Weil directions.

See [Independence](independence.md).

## Saturation

**Saturation** studies whether the current rigorous subgroup is missing divisible points over a bounded prime range.

A saturation result can improve the subgroup without changing rank.

See [Saturation](saturation.md).

## Lattices

**Lattices** builds/stores height-pairing data and supports Mordell–Weil geometry analysis.

Numerical lattice geometry is search guidance unless followed by exact certification.

See [Lattices & Heights](lattices.md).

## Quartics

**Quartics** manages pointed quartic and covering searches, including stored workbench actions and local planning.

See [Quartics & Coverings](quartics.md).

## Campaigns

**Campaigns** groups related searches and analyses under a research objective.

An active Campaign is pinned across the application so new work can keep the same context.

Campaign membership is provenance, not mathematical evidence.

## Jobs

**Jobs** shows queued, running, completed, timed-out, and failed work.

Use job detail/log views when diagnosing a scientific worker.

See [Jobs & Campaigns](jobs-campaigns.md).

## External Catalog

External catalog pages are reference/comparison surfaces. External rank labels are not silently promoted into local rigorous evidence.

## Plugins

**Plugins** shows installed Family, Feature, and Workspace plugins and their validation/enabled state.

See [Plugins](../plugins/index.md).

## Diagnostics

Start here for runtime failures.

Diagnostics checks the current database/runtime and important external executables without intentionally changing scientific state.

## Database

Database tools are administrative. Back up the database before destructive maintenance.

The live SQLite database is authoritative for persisted research state.

## Settings

Settings controls the scientific Python, point-search executables/backends, global time budgets, queue concurrency, and UI preferences registered by core.

See [Configuration](../reference/configuration.md).

## Active Campaign pin

When an active Campaign is selected, Rank Hunter keeps that context visible across pages. This is meant to reduce provenance mistakes when moving among Search, Curves, Target, Jobs, and Analysis.

## Browser/session behavior

Streamlit reruns page code frequently. Long scientific calculations should run as jobs/workers, not inside page rendering.

Closing a browser tab does not imply that queued or detached work has stopped. Check **Jobs** before shutting down or moving a database.

## UI-to-CLI mapping

Many important operations have CLI equivalents for reproducibility and remote execution.

See [UI → CLI Map](../cli/ui-map.md).
