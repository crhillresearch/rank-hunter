# Search & Candidates from the CLI

## Generate a Candidate Pool

`rank42.candidate_generate` creates a normal Rank Hunter Candidate Pool in the database.

```bash
sage -python -m rank42.candidate_generate \
  --db rank42.db \
  --plugin mestre_sextuple_rank11 \
  --pool-name mestre-scan-01 \
  --a-min -500 \
  --a-max 500 \
  --b-max 500 \
  --engine sieve \
  --top 1000
```

Useful options include `--variant`, `--chart`, staged prime bounds, sampled generation, cache control and JSONL export.

## Write a raw candidate file

`rank42.search` runs the low-level family scorer and writes JSONL.

```bash
sage -python -m rank42.search \
  --family my_family \
  --a-min -1000 \
  --a-max 1000 \
  --b-max 500 \
  --engine sieve \
  --top 500 \
  --out candidates.jsonl
```

Use `candidate_generate` when you want the candidates stored as a Rank Hunter Pool.

## Geometry-first Family hunt

`rank42.geometry_search` can start a new command-line Family hunt and create its Campaign automatically.

```bash
sage -python -m rank42.geometry_search \
  --project-root . \
  --db rank42.db \
  --plugin my_family \
  --target-rank 20 \
  --ratpoints vendor/ratpoints/ratpoints
```

It combines staged candidate scoring, exact family baseline/torsion work, bounded upper-bound gates, geometry, point search and certification.

## Run a saved Pipeline

```bash
sage -python -m rank42.pipeline_runner \
  --db rank42.db \
  --project-root . \
  --run-id 42
```

This is the main command behind current Pipeline execution.

## Search an external record

```bash
sage -python -m rank42.record_search \
  --db rank42.db \
  --source icarm \
  --id 302 \
  --stages 1000,10000,100000
```

## Search a quartic directly

```bash
sage -python -m rank42.quartic_search \
  --db rank42.db \
  --curve-id 123 \
  --coefficients 1,0,-4,0,1 \
  --height 100000 \
  --timeout 300
```

For the persisted Quartics workbench modes, use `rank42.quartic_workbench_runner`.

## Compatibility commands

These modules still exist so old jobs can resume, but they reject new standalone work unless their hidden compatibility/resume flag is used:

- `rank42.general_hunt`
- `rank42.auto_search`
- `rank42.fixed_curve_search`
- `rank42.auto_analyze`

For new work, use Candidate Pools, Pipelines, `geometry_search`, or the current Analysis commands.
