# Command Line

Rank Hunter's scientific and operational tools are exposed as Python modules so searches can be reproduced without clicking through the UI.

Run commands from the Rank Hunter checkout with the same Sage-capable scientific Python used by Rank Hunter.

Depending on your Sage installation, that may be:

```bash
sage -python -m rank42.<module> ...
```

or:

```bash
/path/to/sage/python -m rank42.<module> ...
```

## Always identify the database

Most examples use:

```text
--db rank42.db
```

For serious scripts, prefer an absolute path:

```bash
DB="$PWD/rank42.db"
sage -python -m rank42.status --db "$DB"
```

Running the right command against the wrong SQLite file is a common source of confusion.

## Use the installed release's `--help`

Documentation describes supported workflows, but individual Family plugins and research modules evolve.

Before scripting a command:

```bash
sage -python -m rank42.candidate_generate --help
sage -python -m rank42.pipeline_runner --help
sage -python -m rank42.independence_check --help
sage -python -m rank42.cli rank-bounds --help
```

Treat `--help` from the installed release as the parser authority.

## Command classes

### Search/population

- `rank42.candidate_generate`
- `rank42.search`
- `rank42.geometry_search`
- `rank42.pipeline_runner`
- `rank42.record_search`

See [Search & Candidates](searching.md).

### Curve research

- `rank42.cli rank-bounds`
- `rank42.cli saturate`
- `rank42.independence_check`
- `rank42.lattice`
- `rank42.quartic_search`
- `rank42.quartic_workbench_runner`
- `rank42.attack`
- `rank42.certify_curve`
- `rank42.hard_case_escalator`
- `rank42.mw_relation_attack`

See [Curve Research](curve-research.md).

### Data / operations

- `rank42.catalog`
- `rank42.import_result`
- `rank42.curve_metadata`
- backfill modules;
- `rank42.plugin_validate`
- `rank42.status`
- `rank42.dispatcher_service`
- `rank42.recover_legacy`.

See [Pipelines, Data & Operations](operations.md).

## Machine-readable result markers

Several important commands print a final marker followed by JSON.

Examples include:

```text
RANK42_RANK_RESULT=...
RANK42_SATURATION=...
RANK42_PIPELINE_RESULT=...
RANK42_INDEPENDENCE_RESULT=...
```

For automation, parse the explicit marker rather than scraping human progress lines.

## Progress output vs final state

A worker may print intermediate discoveries or heartbeat lines before final persistence/certification.

Do not turn a progress line into a rank claim.

After the command finishes, inspect:

- final machine-readable result;
- curve's live research state;
- exact point/evidence rows.

## Timeouts and exit behavior

A hard timeout usually appears as an inconclusive scientific attempt.

Do not write shell scripts that translate “nonzero exit” or “timeout text” into “rank did not grow.”

Engine failure and mathematical negative result are different states.

## Project root

Some commands need:

```text
--project-root .
```

This is used to find plugins, vendored tools, and project-relative resources.

Run such commands from the checkout or pass an explicit absolute project root.

## Windows

Scientific CLI commands run inside the WSL/Sage environment used by Rank Hunter.

The Windows launcher is not a replacement for the Linux scientific shell.

## Reproducible command record

For important runs, save:

- exact command;
- working directory/project root;
- database path;
- plugin versions;
- environment/Sage version;
- result marker;
- log file if long-running.

## Guides

- [Search & Candidates](searching.md)
- [Curve Research](curve-research.md)
- [Pipelines, Data & Operations](operations.md)
- [Command Catalog](all-commands.md)
- [UI → CLI Map](ui-map.md)
