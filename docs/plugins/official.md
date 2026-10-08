# Official Plugins

The public Rank Hunter installer installs the official plugin collection from:

```text
https://github.com/crhillresearch/rh-plugins
```

The Rank Hunter 0.9.2 installer uses the immutable official plugin release tag `v0.9.2` by default rather than following a moving `main` branch. Plugin releases are versioned independently from Rank Hunter Core.

To intentionally install a different published tag or remote branch, set `RANK_HUNTER_OFFICIAL_PLUGINS_REF` when running `install.sh`. The installer preserves a checkout with uncommitted changes instead of overwriting it.

## Why versions matter

A retained curve can depend on:

- Rank Hunter core version;
- Family plugin version;
- exact Family spec;
- plugin manifest/fingerprint;
- candidate/search configuration.

If the Family mathematics changes in a later plugin release, the old result should still be attributable to the implementation that generated it.

## What the official collection contains

The official collection includes:

- parameterized Family plugins;
- torsion-focused Families;
- record-hunt Families reconstructed from literature;
- exact generic-section Families;
- search Features;
- optional research Workspaces.

See:

- [Official Families](families.md)
- [Official Features & Workspaces](workspaces-official.md)

## Installed version is authoritative

The docs can describe the release collection, but the UI's **Plugins** page shows what is actually installed/enabled on your machine.

Family versions can advance without a Rank Hunter core release.

For publication/reproducibility, record the installed plugin version used by the run.

## Provenance files

Official Family plugins should carry their own README/provenance material describing:

- source paper/URL;
- exact equation;
- reconstruction changes;
- generic sections;
- verified claim boundary;
- known specializations;
- Family-specific search notes.

Those plugin-local files are the primary documentation for the mathematics of one Family.

Core docs explain the plugin contract and evidence rules.

## Validation

Validate an installed plugin from CLI:

```bash
sage -python -m rank42.plugin_validate \
  --project-root . \
  --db rank42.db \
  --plugin PLUGIN_ID
```

A validation failure should be fixed before starting expensive research with that plugin.

## Updating plugins

Before updating the official collection during an active project:

1. record current plugin commit/version;
2. finish or checkpoint important runs;
3. update plugins;
4. revalidate;
5. start new runs with the new provenance.

Do not silently replace the meaning of an in-progress experiment.

## Evidence rule

Official status does not bypass proof rules.

A published generic rank claim in plugin metadata is not automatically a local exact rank for a specialization.

Exact points, independence, and rigorous upper bounds still follow core evidence paths.
