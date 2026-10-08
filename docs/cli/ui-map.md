# UI → CLI Map

Rank Hunter's browser is a control surface for persisted command/job workflows.

The table below maps major UI actions to the closest public CLI entry points.

| UI action | CLI |
| --- | --- |
| Candidates → Generate | `rank42.candidate_generate` |
| Search / Auto / Target Pipeline run | `rank42.pipeline_runner` |
| Descent → Rank bounds | `rank42.cli rank-bounds` |
| Descent → Classical attack | `rank42.attack` |
| Descent → Exact-rank attempt | `rank42.certify_curve` |
| Independence | `rank42.independence_check` |
| Independence → hard case | `rank42.hard_case_escalator` |
| Independence → relation attack | `rank42.mw_relation_attack` |
| Saturation | `rank42.cli saturate` |
| Lattices | `rank42.lattice` |
| Quartics | `rank42.quartic_workbench_runner`, `rank42.quartic_search` |
| Catalog sync/list/export | `rank42.catalog` |
| ICARM prepare/submit | `rank42.icarm_api` |
| Plugin validation | `rank42.plugin_validate` |
| Dispatcher service | `rank42.dispatcher_service` |
| Status | `rank42.status` |

## Pipeline-owned search

Current Search/Auto/Target scientific execution is Pipeline-owned.

The UI creates or selects a saved Pipeline Run, then the actual stage execution uses:

```bash
sage -python -m rank42.pipeline_runner \
  --db rank42.db \
  --project-root . \
  --run-id 42
```

This means a run created in the browser can be continued from a terminal.

## What the UI still owns

Some setup is intentionally easier/only available in the browser in 0.9.2, including creation/editing of saved Pipeline definitions and Campaign organization.

Once the durable run/object exists, many scientific actions can be launched or inspected from CLI.

## Database consistency

The UI and CLI must use the same database path if you expect to see the same curves/jobs.

A common mistake is:

- UI uses `/home/me/rank-hunter/rank42.db`;
- terminal runs from another directory with `--db rank42.db`;
- terminal creates/opens a different file.

Prefer an explicit absolute `--db` in scripts.

## Scientific Python consistency

Run CLI jobs with the same Sage-capable scientific Python configured in Rank Hunter.

The Streamlit UI interpreter may not be Sage-capable.

## Logs/results

UI job detail provides a convenient log viewer.

From CLI, preserve stdout/stderr when a run matters:

```bash
sage -python -m rank42.pipeline_runner ... 2>&1 | tee pipeline-42.log
```

Still treat the database/evidence rows as authoritative scientific state.

## Old direct search modules

Modules such as:

```text
rank42.general_hunt
rank42.auto_search
rank42.fixed_curve_search
rank42.auto_analyze
```

remain for compatibility/resume behavior.

Do not use them as the default entry point for new 0.9.2 work unless the module explicitly supports your compatibility case.

## See also

- [CLI Overview](index.md)
- [Command Catalog](all-commands.md)
- [Pipelines](../manual/pipelines.md)
