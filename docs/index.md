# Rank Hunter Documentation

Rank Hunter is a local research environment for searching, investigating, and certifying elliptic curves over \(\mathbf Q\). It combines candidate generation, family-specific search, rational-point discovery, exact point verification, Mordell–Weil work, rank bounds, quartics and coverings, pipelines, jobs, campaigns, external catalogs, and plugins in one application.

These docs describe **how the released system actually behaves**. They are organized for three audiences:

- **Researchers using the UI** — start with Getting Started and Using Rank Hunter.
- **Researchers scripting or automating work** — use the Command Line section.
- **Developers extending or debugging Rank Hunter** — use Plugins and Reference.

The public release documented here is **Rank Hunter 0.9.2**.

## Start here

If this is your first time using Rank Hunter:

1. [Install Rank Hunter](getting-started/installation.md) and verify the scientific runtime.
2. Read [What Counts as Proof?](getting-started/evidence.md) before interpreting ranks.
3. Follow [Your First Hunt](getting-started/first-hunt.md) from Candidate Pool to retained curve.
4. Read [The Interface](manual/interface.md) for the page-by-page map.
5. Use [Troubleshooting](reference/troubleshooting.md) when a job, runtime, or engine behaves unexpectedly.

If you are developing Rank Hunter or a plugin, also read:

- [Architecture](reference/architecture.md)
- [Data Model](reference/data-model.md)
- [Plugin Overview](plugins/index.md)

## The research workflow

The normal data flow is:

```text
Family / generator
    ↓
Candidate Pool
    ↓
screening / Pipeline / Search
    ↓
retained Curve
    ↓
exact Point ledger
    ↓
independence / saturation / descent / coverings / lattices
    ↓
rigorous rank evidence
```

Each layer has a different role.

A **Candidate** is something worth spending time on. It is not automatically a retained curve and carries no rank claim by itself.

A **Curve** is durable scientific state: an exact Weierstrass model plus provenance and attached research results.

A **Point** is stored separately. Exact membership on the curve and independence are distinct questions.

A **Job** records work that was queued or executed. A **Pipeline Run** records stage-by-stage orchestration over a population.

A **Campaign** groups related research without changing the mathematics.

## Evidence is deliberately conservative

Rank Hunter searches aggressively but promotes evidence conservatively.

| State | Meaning |
| --- | --- |
| Heuristic score | Ranking/scheduling signal only. |
| Exact point | Rational coordinates verified exactly on the stored curve. |
| Independent exact points | Exact points certified to contribute distinct Mordell–Weil directions. |
| Rigorous lower bound | Rank is proved to be at least this value. |
| Rigorous upper bound | A rigorous engine proved rank is at most this value. |
| Exact rank | Rigorous lower and upper bounds agree. |
| Timeout / error | Computation did not finish; mathematically inconclusive. |

Do not infer rank from raw point count, Nagao score, a numerical height matrix, an external leaderboard entry, or a timeout. See [What Counts as Proof?](getting-started/evidence.md) and [Rank & Evidence](mathematics/rank.md).

## Where the main tasks live

| Goal | UI | Documentation |
| --- | --- | --- |
| Generate/search candidates | **Search**, **Candidates**, **Auto** | [Search](manual/search.md), [Candidate Pools](manual/candidates.md) |
| Build reusable workflows | **Pipelines** | [Pipelines](manual/pipelines.md) |
| Deep-search one curve | **Target** | [Target Search](manual/target.md) |
| Inspect curves and points | **Curves**, **Points** | [Curves & Points](manual/curves.md) |
| Prove point independence | **Independence** | [Independence](manual/independence.md) |
| Search coverings/quartics | **Quartics** | [Quartics & Coverings](manual/quartics.md) |
| Saturate a known subgroup | **Saturation** | [Saturation](manual/saturation.md) |
| Study heights/lattices | **Lattices** | [Lattices & Heights](manual/lattices.md) |
| Bound or close the rank | **Descent** | [Descent & Rank Bounds](manual/descent.md) |
| Manage long-running work | **Jobs**, **Campaigns** | [Jobs & Campaigns](manual/jobs-campaigns.md) |
| Extend Rank Hunter | **Plugins** | [Plugins](plugins/index.md) |

## Operational model

The browser is a control surface. SQLite is the durable research record.

Closing a browser tab does not erase scientific state. Jobs may continue according to how they were launched and queued. The default database is `rank42.db` in the checkout unless another path is configured.

For a developer-level explanation of the UI shell, queue, plugin loader, evidence reducers, and workers, see [Architecture](reference/architecture.md).

For the important persisted entities and their relationships, see [Data Model](reference/data-model.md).
