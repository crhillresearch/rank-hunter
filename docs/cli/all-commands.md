# Command Catalog

These are the command-line entry points intended for researchers and operators in Rank Hunter 0.9.2.

## Search

| Command | Use |
| --- | --- |
| `rank42.candidate_generate` | Create a Candidate Pool. |
| `rank42.search` | Low-level family candidate scoring to JSONL. |
| `rank42.geometry_search` | Start a geometry-first Family hunt. |
| `rank42.pipeline_runner` | Run/resume a saved Pipeline Run. |
| `rank42.record_search` | Search an external catalog record. |
| `rank42.quartic_search` | Search a quartic directly. |
| `rank42.quartic_workbench_runner` | Run persisted pointed/covering Quartics work. |

## Rank and Mordell-Weil work

| Command | Use |
| --- | --- |
| `rank42.cli rank-bounds` | Rigorous rank-bounds pipeline. |
| `rank42.cli saturate` | Bounded saturation. |
| `rank42.attack` | Classical descent attack. |
| `rank42.certify_curve` | Try to close exact rank. |
| `rank42.advanced_upper_bound` | Stronger upper-bound attack for hard cases. |
| `rank42.independence_check` | Exact independence work. |
| `rank42.hard_case_escalator` | Deeper independence/resolution ladder. |
| `rank42.mw_relation_attack` | Exact relation search among MW points. |
| `rank42.lattice` | Build/list Mordell-Weil height lattices. |

## Data, catalogs and metadata

| Command | Use |
| --- | --- |
| `rank42.catalog` | Sync/inspect/export external catalog data. |
| `rank42.import_result` | Import exact curve/point material without trusting external rank claims. |
| `rank42.export_best` | Export the current best candidate. |
| `rank42.curve_metadata` | Compute arithmetic metadata for one curve. |
| `rank42.curve_size_metrics` | Backfill curve-size metrics. |
| `rank42.conductor_backfill` | Backfill conductors. |
| `rank42.curve_arithmetic_backfill` | Backfill stored curve arithmetic. |
| `rank42.torsion_backfill` | Backfill exact rational torsion. |
| `rank42.icarm_api` | Prepare or explicitly submit an ICARM curve. |

## Plugins and operations

| Command | Use |
| --- | --- |
| `rank42.plugin_validate` | Validate/enable/disable a plugin. |
| `rank42.status` | Show current research/runtime status. |
| `rank42.dispatcher_service` | Manage the persistent queue dispatcher service. |
| `rank42.recover_legacy` | Guarded legacy database recovery. |

## Resume-only compatibility commands

These remain in the source because old jobs may need them:

```text
rank42.general_hunt
rank42.auto_search
rank42.fixed_curve_search
rank42.auto_analyze
```

They are not the normal starting point for new 0.9.2 work.

## Backend workers

Modules ending in `_worker`, `rank42.dispatcher`, `rank42.ui_job_runner` and similar helpers are implementation pieces. They are deliberately left out of the public command catalog.

If you are debugging the internals, they still have their own parsers where needed. For ordinary command-line research, use the entry points above.
