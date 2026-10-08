# Pipelines, Data & Operations from the CLI

## Run a saved Pipeline

```bash
sage -python -m rank42.pipeline_runner \
  --db rank42.db \
  --project-root . \
  --run-id 42
```

Replan against a current preset:

```bash
sage -python -m rank42.pipeline_runner \
  --db rank42.db \
  --project-root . \
  --run-id 42 \
  --replan-preset deep
```

## External catalogs

Sync ICARM:

```bash
sage -python -m rank42.catalog --db rank42.db sync --source icarm
```

Status:

```bash
sage -python -m rank42.catalog --db rank42.db status
```

List records:

```bash
sage -python -m rank42.catalog --db rank42.db list --rank-at-least 20 --limit 50
```

Export a witness:

```bash
sage -python -m rank42.catalog --db rank42.db export-witness 302 --out curve302.json
```

## Import exact curve/point material

`rank42.import_result` checks the curve and points exactly before storing them.

```bash
sage -python -m rank42.import_result result.json \
  --db rank42.db \
  --family imported
```

An external exact-rank claim is stored as reference provenance only. It is not promoted into local proof.

## Export the current best candidate

```bash
python -m rank42.export_best --db rank42.db --out best-candidate.json
```

## Curve metadata

One curve:

```bash
sage -python -m rank42.curve_metadata --db rank42.db --id 123
```

Core arithmetic only:

```bash
sage -python -m rank42.curve_metadata --db rank42.db --id 123 --core-only
```

Size metrics:

```bash
sage -python -m rank42.curve_size_metrics --db rank42.db --limit 100
```

## Backfills

Conductors:

```bash
sage -python -m rank42.conductor_backfill --db rank42.db --rank-at-least 10
```

Stored curve arithmetic:

```bash
sage -python -m rank42.curve_arithmetic_backfill --db rank42.db --limit 25
```

Torsion:

```bash
sage -python -m rank42.torsion_backfill --db rank42.db
```

## Plugin validation

```bash
sage -python -m rank42.plugin_validate \
  --project-root . \
  --db rank42.db \
  --plugin my_family
```

Add `--disable` to disable that plugin.

## ICARM

Prepare a curve:

```bash
sage -python -m rank42.icarm_api --db rank42.db --id 123 --project-root .
```

Submission is an external write and needs both confirmation flags:

```bash
sage -python -m rank42.icarm_api \
  --db rank42.db \
  --id 123 \
  --project-root . \
  --submit --yes
```

## Status

```bash
python -m rank42.status --db rank42.db
```

Use `--events N` to change how much recent history is shown.

## Dispatcher service

On Linux/WSL with a user systemd session:

```bash
python -m rank42.dispatcher_service status \
  --db /full/path/rank42.db \
  --project-root /full/path/rank-hunter
```

Actions are:

```text
install
status
start
restart
uninstall
```

Normal desktop users can let Rank Hunter manage this from Jobs.

## Legacy database recovery

Start with a collision check:

```bash
sage -python -m rank42.recover_legacy preflight \
  --live rank42.db \
  --legacy old-rank42.db
```

The recovery path uses a disposable staging database. Run `--help` for `stage`, `validate`, `report` and `merge`.
