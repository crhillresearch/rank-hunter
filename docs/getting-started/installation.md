# Install Rank Hunter

Rank Hunter needs two Python environments with different jobs:

1. a **scientific Python** capable of importing SageMath (`sage.all`) and running arithmetic workers;
2. the **UI/runtime environment** used by the Streamlit application.

The installer configures both and records the scientific Python path for later jobs.

## Linux / Ubuntu

Clone the public repository and run the installer:

```bash
git clone https://github.com/crhillresearch/rank-hunter.git
cd rank-hunter
bash install.sh
```

If Sage is not the Python currently found on `PATH`, pass it explicitly:

```bash
bash install.sh --science-python /path/to/sage/python
```

A common Conda/Sage layout looks like:

```text
/home/user/miniforge3/envs/sage/bin/python
```

Use the actual Python executable that succeeds with:

```bash
/path/to/sage/python -c "from sage.all import EllipticCurve, QQ; print(EllipticCurve(QQ,[0,0,0,-1,0]))"
```

### Lite install

To skip native ratpoints builds:

```bash
bash install.sh --lite
```

This is useful when you only need the UI/database or plan to provide point-search executables later. Search features that require ratpoints will remain unavailable until an executable is configured.

## Windows

Download the [latest Windows installer](https://github.com/crhillresearch/rank-hunter/releases/latest/download/RankHunter-Setup-x64.exe) from the public GitHub Release. The public `v0.9.2` release attaches `RankHunter-Setup-x64.exe` after its Windows build succeeds.

The Windows installer can provision or reuse:

- WSL 2;
- a Linux Rank Hunter checkout;
- SageMath 10.10 when a compatible scientific runtime is not already present;
- the Rank Hunter UI/runtime environment.

After setup, start Rank Hunter from the Windows Start menu.

Uninstalling the Windows shell does not automatically remove the Linux checkout or research database. Treat the database as research data and back it up independently.

If startup fails, inspect:

```text
%LOCALAPPDATA%\RankHunter\launch.log
```

Then see [Troubleshooting](../reference/troubleshooting.md).

## What the full installer prepares

A normal full install performs or verifies the following classes of setup:

- UI Python environment and application dependencies;
- SQLite schema initialization/migrations;
- scientific Python/Sage discovery;
- official plugin installation;
- pinned CPU ratpoints source/build;
- pinned GPU ratpoints source/build when CUDA is usable;
- bootstrap markers used by the UI to locate the scientific runtime.

GPU support is optional. CPU point search remains the fallback.

## Launch

From the repository root:

```bash
bash scripts/run-ui.sh
```

Unless configured otherwise, Rank Hunter opens `rank42.db` from the checkout.

Do not launch the UI from an arbitrary directory and assume the same database will be used. The database path matters; two different paths mean two different research records.

## Verify the installation

After the UI opens, go to **Diagnostics** and verify:

- database opens successfully;
- scientific Python is detected;
- Sage import succeeds;
- CPU ratpoints is found when installed;
- GPU ratpoints is found only when you expect GPU support;
- plugin discovery succeeds.

For a shell-level check of the scientific runtime:

```bash
SCIENCE=/path/to/sage/python
"$SCIENCE" - <<'PY'
from sage.all import QQ, EllipticCurve
E = EllipticCurve(QQ, [0, 0, 0, -1, 0])
print(E)
print("rank-hunter science runtime: OK")
PY
```

## Runtime settings

The authoritative user/runtime settings live in Rank Hunter's settings registry and are editable under **Settings**. Important keys include:

| Setting | Purpose |
| --- | --- |
| `science_python` | Python executable used for Sage/scientific jobs |
| `ratpoints` | CPU ratpoints executable |
| `ratpoints_gpu` | GPU ratpoints executable |
| `ratpoints_backend` | Default `CPU` or `GPU` point-search backend |
| `pari_rank_timeout` | Default bounded PARI rank time budget |
| `mwrank_rank_timeout` | Default bounded mwrank time budget |
| `deep_cert_timeout` | Default deep exact-certificate budget |
| `queue_max_workers` | Maximum concurrent queue workers |

See [Configuration](../reference/configuration.md).

## Updating Rank Hunter

Before updating:

1. stop or finish important jobs;
2. back up `rank42.db`;
3. record any local plugin changes;
4. update the repository;
5. rerun the installer when release notes require dependency or native-tool changes;
6. launch Rank Hunter and let database migrations complete;
7. run **Diagnostics** before resuming expensive work.

Do not copy a database backward into an older checkout and assume schema compatibility.

## Back up research data

At minimum, preserve:

- the active SQLite database;
- research exports you care about;
- private/local plugins not stored in Git;
- any manually maintained corpora or artifacts outside the database.

For SQLite, make backups while Rank Hunter is idle or use SQLite's backup facilities rather than copying a file during heavy writes.

## Next step

Continue with [Your First Hunt](first-hunt.md).
