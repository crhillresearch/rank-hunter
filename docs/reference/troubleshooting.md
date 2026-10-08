# Troubleshooting

Troubleshooting Rank Hunter is easier when you identify **which boundary failed**: UI, queue, worker launch, scientific engine, map-back, evidence promotion, or database persistence.

Start with **Diagnostics**.

## UI will not start

From the checkout:

```bash
bash scripts/run-ui.sh
```

Read the terminal output.

Common causes:

- UI virtual environment missing/broken;
- scientific Python marker points to a removed environment;
- database migration failure;
- dependency mismatch;
- wrong working directory.

Do not delete `rank42.db` as a first troubleshooting step.

## Windows launcher fails

Inspect:

```text
%LOCALAPPDATA%\RankHunter\launch.log
```

Rerunning the Windows installer is preferred to manually recreating pieces of the WSL/Sage/UI environment.

## Sage/scientific Python failure

Verify the configured executable:

```bash
/path/to/sage/python - <<'PY'
from sage.all import QQ, EllipticCurve
print(EllipticCurve(QQ,[0,0,0,-1,0]))
PY
```

If this fails outside Rank Hunter, repair the scientific environment first.

Then update **Settings → science_python** or rerun installation.

## CPU ratpoints missing

Check Diagnostics and the `ratpoints` setting.

A full install normally builds the pinned vendored CPU implementation.

If you installed with `--lite`, a missing native point-search executable is expected until configured.

## GPU ratpoints missing

GPU support requires compatible CUDA hardware/toolchain and a successful build.

If GPU is unavailable:

- use CPU backend;
- do not treat GPU absence as a Rank Hunter scientific failure.

## Job is stuck or running too long

Open **Jobs** and inspect:

- stage/command;
- elapsed time;
- hard timeout;
- heartbeat;
- worker log.

Some engines legitimately spend long periods without new console output.

If the hard timeout is functioning, allow it to classify the attempt rather than killing random child processes.

## Job timed out

Interpretation: **inconclusive**.

Next questions:

- Did the search resolve any subregions before timeout?
- Is the job checkpointed?
- Is retry supported?
- Would a different model/geometry be better than more time?
- Is this candidate worth escalation?

## Engine returned an error

An error is not “rank did not grow.”

Capture:

- exact error text;
- engine;
- model;
- timeout;
- plugin/version;
- worker tail.

Then determine whether the failure is reproducible on the same exact input.

## Points were found but rank did not increase

Possible reasons:

- duplicate/sign-equivalent points;
- points are dependent;
- points are exact but independence is unresolved;
- current rigorous basis is incomplete/inconsistent;
- certificate timed out.

Open **Points** and **Independence**.

## Rigorous upper is below rigorous lower

This is an evidence conflict.

Do not manually overwrite one value.

Check:

1. stored exact model;
2. point coordinates/model transport;
3. engine evidence record;
4. imported/plugin provenance;
5. whether the upper bound actually claimed rigor;
6. database integrity.

## Candidate says searched but no curve exists

A searched candidate can legitimately produce no retained scientific evidence.

Candidate rows are orchestration state. Not every searched candidate materializes a persistent curve.

Inspect the candidate's last search metadata/job.

## Candidate pool looks stale

Use bounded reconciliation/finalization paths.

Do not write UI code that silently scans/reconciles every row on each Streamlit render.

For developer debugging, compare candidate `curve_id`, status, parameter/native-fiber identity, and matching curve provenance.

## Pipeline validation error

If Builder says a stage is missing a prerequisite capability, the stage order is invalid.

Do not bypass validation.

Open [Pipelines](../manual/pipelines.md) and identify which earlier stage should provide the required capability.

## Pipeline resumed but repeats work

Check whether you resumed the same Pipeline Run id or started a new run with similar settings.

Checkpoint/cached-attempt identity belongs to the original run.

## Pointed quartic search finds zero

Distinguish:

- completed no-hit;
- local obstruction;
- timeout;
- map-back failure;
- reduction failure with raw fallback.

Look at the round/coverage summary rather than only the final rank.

## mwrank/PARI size or memory problems

A different engine may handle the exact same curve more gracefully.

The rank pipeline records engine-specific failures and may avoid repeating known deterministic limits.

For PARI stack overflows, the rank pipeline has bounded stack escalation controlled by `--pari-stack-max-gib`.

## Database locked / maintenance issue

Stop or reduce concurrent database-maintenance work.

Check whether another Rank Hunter instance is using the same SQLite file.

Do not copy or replace the database while active jobs are writing.

## Foreign-key problems

Back up the database before manual repair.

Use SQLite integrity/foreign-key checks and identify the exact table relationship before deleting rows.

Scientific child rows should not be discarded casually.

## Plugin will not enable

Run validation:

```bash
sage -python -m rank42.plugin_validate \
  --project-root . \
  --db rank42.db \
  --plugin PLUGIN_ID
```

Validation failure disables/marks the plugin invalid and preserves the error in plugin state.

Check:

- manifest schema;
- entrypoint path;
- validation specialization;
- exact Family model;
- optional hook imports;
- plugin-local dependency errors.

## Wrong database

If expected curves “disappeared,” confirm the exact database path before assuming data loss.

Print or inspect the `--db` argument used by the UI/CLI.

`rank42.db` is relative to the current working directory when passed as a relative path.

## Safe recovery sequence

When something serious breaks:

1. stop new jobs;
2. preserve logs;
3. back up the database;
4. run Diagnostics;
5. reproduce the failure with one small command;
6. classify UI/worker/engine/database;
7. fix one layer;
8. rerun the smallest relevant test;
9. resume research only after the failure mode is understood.
