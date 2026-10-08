# Configuration

Most Rank Hunter configuration is stored through the typed settings registry and edited under **Settings**.

The default working database is `rank42.db` in the checkout unless another database path is supplied.

## Registered settings

The 0.9.2 core registry includes:

| Key | Type/default | Purpose |
| --- | --- | --- |
| `ui_theme` | theme id | selected Rank Hunter theme |
| `ui_appearance` | `light` | light/dark appearance |
| `science_python` | detected path | Python/Sage executable for scientific jobs |
| `ratpoints` | detected vendored CPU path | CPU point-search executable |
| `ratpoints_gpu` | optional detected path | GPU point-search executable |
| `ratpoints_backend` | `CPU` | default CPU/GPU backend |
| `pari_rank_timeout` | 300 s | default PARI rank budget |
| `mwrank_rank_timeout` | 300 s | default mwrank rank budget |
| `deep_cert_timeout` | 900 s | deep exact-certificate budget |
| `queue_max_workers` | 2 | global concurrent queue workers |
| `queue_resource_limits` | mapping | per-resource-class concurrency ceilings |

The registry validates type/range before storing values.

## Scientific Python

`science_python` must be an executable that can import Sage.

Changing the string to an arbitrary Python path is not enough; runtime validation must succeed.

Example check:

```bash
/path/to/python -c "from sage.all import QQ, EllipticCurve; print('OK')"
```

## Point-search executables

`ratpoints` and `ratpoints_gpu` point to the CPU and GPU executables.

GPU is optional.

If `ratpoints_backend=GPU` but GPU validation fails, inspect Diagnostics and either repair CUDA/native build or switch the default back to CPU.

## Timeouts

Global timeout settings are defaults.

Specific UI/Pipeline/CLI commands may override them.

A timeout is always an operational budget, not a mathematical cutoff.

Increasing `pari_rank_timeout` does not change what a completed PARI proof means; it only gives the engine more time.

## Queue limits

`queue_max_workers` is constrained to a bounded range by core.

`queue_resource_limits` can prevent too many jobs of one resource class from running simultaneously.

Typical resource classes include:

- GPU ratpoints;
- Sage-heavy work;
- database maintenance.

Set these based on actual RAM/GPU/CPU capacity rather than logical CPU count alone.

## Database path

Many commands accept:

```text
--db rank42.db
```

The UI launcher also supplies its configured database path.

Two different database paths are two different Rank Hunter research states.

Always confirm the path before running manual CLI commands.

## Database environment variables

Compatibility/runtime paths may recognize database environment variables such as:

```text
RANK_HUNTER_DB
RANK42_DB
```

Prefer explicit `--db` in scripts when reproducibility matters.

## Bootstrap marker

The installer can create:

```text
.rank42-ui/science-python
```

This is bootstrap input for the scientific Python setting. It should not silently override an already-persisted valid user setting.

## Secrets

The current core settings registry distinguishes secret/non-secret settings. Do not add service credentials as arbitrary plain-text config files if a registered secure path exists.

## Configuration vs application state

User/runtime configuration belongs in settings.

Current application state (selected page, temporary UI state) and dispatcher heartbeat/service state are separate concerns.

Do not persist transient application state as if it were user configuration.

## Reproducible scripts

For reproducible command-line research:

- pass `--db` explicitly;
- record the command;
- record plugin versions;
- record important timeout/search bounds;
- do not depend on an unrecorded UI selection.

## Related documentation

- [Installation](../getting-started/installation.md)
- [Troubleshooting](troubleshooting.md)
- [Architecture](architecture.md)
