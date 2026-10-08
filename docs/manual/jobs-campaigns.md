# Jobs & Campaigns

Jobs and Campaigns solve different problems.

A **Job** is an execution record: something was queued, started, finished, timed out, failed, or was cancelled.

A **Campaign** is research organization: several pools, searches, pipelines, target runs, and analyses belong to one question.

Neither object is rank evidence by itself.

## Jobs

Open **Jobs** to inspect current and historical work.

A job record can carry:

- command / worker identity;
- arguments/configuration;
- queue state;
- resource class;
- start/finish timestamps;
- exit status;
- logs;
- result payload;
- links to candidate pools, curves, campaigns, or pipeline runs.

The exact fields depend on the job type.

## Queue and concurrency

Rank Hunter has a persistent dispatcher/queue model.

Global settings include:

- `queue_max_workers`;
- per-resource ceilings such as GPU, Sage-heavy, and database-maintenance work.

This prevents every queued calculation from starting simultaneously and exhausting memory/GPU/database resources.

See [Configuration](../reference/configuration.md).

## Job status vs mathematical status

Operational status and mathematical outcome are not the same thing.

| Job status | Mathematical interpretation |
| --- | --- |
| completed | worker finished; inspect result |
| timeout | unresolved |
| error | unresolved / implementation/runtime problem |
| cancelled | no conclusion unless evidence was already durably persisted |
| partial | inspect coverage/results |
| completed with zero hits | only the configured bounded search completed with no hit |

Always inspect the stage/engine payload when the distinction matters.

## Closing the browser

Closing a browser tab does not necessarily stop the server, dispatcher, or already-running scientific workers.

Before shutting down for the day:

1. open **Jobs**;
2. identify running/queued work;
3. cancel what should not continue;
4. allow important writes to finish;
5. then stop the Rank Hunter server/UI.

## Logs

Logs are operational evidence, not the authoritative rank state.

Use logs to answer questions such as:

- Did the worker start?
- Which model/height was searched?
- Did the engine time out?
- Which stage produced a point?
- Was a point exact-certified?

Then confirm the durable result on the curve/evidence page.

## Re-running failed work

Before retrying a failed job, classify the failure:

- timeout;
- missing executable;
- engine-specific size limit;
- malformed input;
- plugin exception;
- database lock/maintenance issue;
- proof/certificate inconclusive.

A larger timeout only addresses the first class.

## Campaigns

Use a Campaign when several pieces of work answer the same research question.

Examples:

- “Search Mestre sextuple family for rank ≥ 20”;
- “Reproduce ICARM curve #302 neighborhood”;
- “Compare three rank-31 X1152 fibrations”;
- “Close exact rank on curve #417.”

## Active Campaign

Rank Hunter pins the active Campaign across pages so new work can keep the same context.

This reduces provenance mistakes when moving among Search, Curves, Target, Jobs, and Analysis.

Campaign context should never be used as a substitute for explicit curve/pool ids in scripts.

## What belongs in a Campaign

A useful Campaign can group:

- Candidate Pools;
- Pipeline Runs;
- Target searches;
- retained curves;
- follow-up independence/descent work;
- exports and notes.

The Campaign should make it possible to reconstruct the sequence of experiments later.

## Naming Campaigns

Prefer a research question over a date-only label.

Good:

```text
X1152 rank-31 neighborhood recovery
Mestre sextuple 20+ hunt
Curve 417 exact-rank closure
```

Weak:

```text
test2
today
new run
```

Dates already exist in job/run timestamps.

## Reproducibility checklist

For a significant run, preserve:

- campaign;
- pool id/name;
- pipeline/run id;
- plugin versions;
- exact command/config;
- target rank/retention rule;
- timeout values;
- result/log;
- curve ids that survived.

## Related documentation

- [Pipelines](pipelines.md)
- [Curves & Points](curves.md)
- [Architecture](../reference/architecture.md)
