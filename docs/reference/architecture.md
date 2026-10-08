# Architecture

This page describes the runtime boundaries that matter when debugging, extending, or operating Rank Hunter 0.9.2.

Rank Hunter is not a single long-running Sage process. It is a local application composed of a Streamlit control surface, SQLite persistence, queue/job orchestration, isolated scientific workers, and plugins.

## High-level components

```text
Browser
  ↓
Streamlit UI (rank42.ui)
  ↓
core orchestration / persistence
  ├── SQLite rank42.db
  ├── queue / dispatcher
  ├── Pipeline runner
  ├── plugin loader / adapters
  └── isolated scientific workers
        ├── Sage/PARI/eclib
        ├── ratpoints CPU
        ├── ratpoints GPU
        └── plugin workers
```

The browser should be treated as ephemeral. SQLite and persisted artifacts are the durable research state.

## UI shell

`rank42.ui` owns:

- canonical navigation;
- legacy route normalization;
- UI context creation;
- extension/workspace hosting;
- feature hook hosting;
- active Campaign pin;
- job strip;
- server restart/shutdown controls.

Scientific algorithms should not be implemented in page-render code.

Streamlit reruns frequently; expensive work belongs in isolated workers/jobs.

## Database boundary

SQLite is the source of truth for:

- retained curves;
- exact points;
- rank evidence;
- candidate pools;
- candidates;
- jobs/results;
- campaigns;
- pipeline state;
- lattices/coverings/quartic searches;
- plugin state/settings;
- provenance.

Core migrations evolve this schema over releases.

See [Data Model](data-model.md).

## Scientific Python

The UI runtime and scientific runtime may be different Python executables.

`science_python` points to the Python that can import Sage and run scientific jobs.

Do not assume `sys.executable` inside Streamlit is the same interpreter used for Sage arithmetic.

## Isolated workers

Rank Hunter frequently uses subprocess isolation for expensive or failure-prone arithmetic.

Reasons include:

- hard timeout enforcement;
- protection from PARI/Sage/eclib crashes;
- bounded memory/process lifetime;
- clean machine-readable result protocols;
- avoiding UI rerun interference.

A wrapper typically:

1. serializes exact input;
2. starts a worker subprocess;
3. enforces a hard timeout;
4. reads a result marker;
5. validates output;
6. persists accepted evidence.

Worker timeout/error is operationally visible and mathematically inconclusive.

## ratpoints

Point search can use vendored CPU and optional GPU executables.

The point-search layer owns:

- executable discovery;
- bounded search invocation;
- chart/model preparation;
- timeout semantics;
- exact point reconstruction;
- checkpoint/progress state.

A ratpoints hit is not a rank claim until exact verification and independence promotion complete.

## Pipeline architecture

Pipeline definitions are normalized/validated by `rank42.pipeline_catalog`.

The runner:

- loads target population;
- validates stage contracts;
- persists candidate/run state;
- executes population and per-curve stages;
- records child derivations;
- aggregates stage coverage;
- preserves retry identity;
- emits a machine-readable final result.

Pipeline capabilities prevent illegal stage order.

Transforms that create children reset curve-local capabilities so parent evidence is not inherited accidentally.

## Evidence reduction

Rank evidence is append-oriented: engines record evidence, then core reduces the current live state.

This separation allows:

- multiple independent lower/upper sources;
- cached evidence;
- evidence-conflict detection;
- later engine improvements without rewriting history.

The live curve rank is not supposed to be “whatever the last job printed.”

## Point promotion

Exact point discovery and rigorous rank promotion are separate layers.

A normal point path is:

```text
search hit
→ exact reconstruction on E(Q)
→ point ledger
→ independence certificate
→ rigorous witness basis
→ rigorous lower-bound evidence
```

This is why plugins and alternative search models cannot directly claim rank.

## Plugin architecture

Core supports three public extension shapes:

- **Family** — parameterized elliptic-curve mathematics and search;
- **Feature** — bounded UI/search-command hook;
- **Workspace/extension** — full optional page.

Plugins are discovered/validated separately from core.

Family-specific equations and exact transforms belong in plugins rather than core conditionals.

Core remains responsible for persistence and proof boundaries.

## Plugin isolation

Long Family/geometry work should run through command/worker adapters rather than Streamlit render code.

Plugin hooks may return exact points/coverings/results, but core reconstructs and validates accepted mathematical objects.

## Queue/dispatcher

The persistent queue controls concurrency and resource classes.

Settings include a global worker count and resource-specific ceilings.

This prevents simultaneous Sage-heavy/GPU/database-maintenance jobs from overwhelming the machine.

## Runtime settings

Core's typed settings registry includes scientific Python, ratpoints executables/backend, timeouts, queue concurrency, and UI appearance/theme.

Runtime executable validation is separate from merely storing a path.

See [Configuration](configuration.md).

## Failure boundaries

When debugging, determine which boundary failed:

1. UI validation;
2. queue/dispatcher;
3. wrapper;
4. worker startup;
5. scientific engine;
6. output protocol;
7. exact reconstruction;
8. evidence promotion;
9. persistence.

“Search failed” is too vague to diagnose.

## Source map

Important modules include:

| Area | Module |
| --- | --- |
| UI shell | `rank42.ui` |
| Database/migrations | `rank42.db`, migration modules |
| Candidate pools | `rank42.candidates` |
| Pipeline catalog | `rank42.pipeline_catalog` |
| Pipeline runner | `rank42.pipeline_runner` |
| Rank bounds | `rank42.rank_pipeline` |
| Independence | `rank42.independence_check` |
| Saturation | `rank42.saturation` |
| Lattice work | `rank42.lattice` |
| Pointed quartics | `rank42.pointed_quartic` |
| Plugin validation | `rank42.plugin_validate` |
| Settings | `rank42.settings_registry` |

## Design rule

Core may schedule aggressively, but durable rigorous claims must pass a core-owned exact/verified evidence path.

That rule should remain true when adding new workers, engines, or plugins.
