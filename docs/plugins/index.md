# Plugins

Rank Hunter Core owns generic orchestration, persistence, exact evidence rules, and common arithmetic infrastructure.

Family-specific mathematics and optional research surfaces live in plugins.

## Plugin types

| Type | Purpose | Typical contents |
| --- | --- | --- |
| **Family** | Parameterized elliptic-curve mathematics and search | equations, modular model, sections, candidate generation, search adapter |
| **Feature** | Small hook into an existing core page or search command | render hook, command transform |
| **Workspace / extension** | Full optional research page | Streamlit page, backend helpers, jobs |

Use the narrowest type that fits.

Do not build a full navigation subsystem inside a Feature.

## Discovery and validation

Plugins are discovered from the configured project/plugin locations and validated before normal use.

CLI validation:

```bash
sage -python -m rank42.plugin_validate \
  --project-root . \
  --db rank42.db \
  --plugin PLUGIN_ID
```

Validation success can mark a plugin ready/enabled.

Validation failure records the error and marks the plugin invalid/disabled rather than allowing a broken plugin to fail later inside a long search.

## Provenance

Rank Hunter stores plugin identity/version and, where available, file/manifest fingerprints on candidate pools and retained curves.

This matters because the same Family id can change implementation over time.

A serious result should be reproducible against the plugin version that generated it.

## Family plugins

Use a Family plugin for:

- `curve(t)`;
- modular `curve_mod_p`;
- exact generic sections;
- candidate generation;
- Family Search;
- Target Search;
- exact charts/transforms;
- known subgroup metadata;
- family-specific coverings/geometry.

See [Write a Family Plugin](family.md).

## Feature plugins

Feature hooks are for bounded additions to existing pages or command-launch paths.

They should not run long arithmetic during Streamlit render.

See [Write a Feature Plugin](feature.md).

## Workspace plugins

A Workspace owns a full optional page.

Use it for an explorer or substantial specialized workflow that does not belong in core navigation.

Long computations should launch jobs/workers.

See [Write a Workspace Plugin](workspace.md).

## Proof boundary

Plugins may change **where** Rank Hunter searches and may provide exact mathematical objects.

They cannot bypass core evidence promotion.

A plugin-returned point must still be reconstructed/verified.

A plugin's heuristic rank field is not a rigorous lower bound.

A plugin's rigorous upper claim needs an accepted verification/certificate path.

## Optional pipeline hooks

Core can discover optional research hooks for capabilities such as:

- deriving exact coverings;
- higher descent;
- p-adic covering point search;
- exact pipeline transforms.

If the hook does not exist, the stage should report unsupported/capability absent.

Do not emulate a missing specialized engine with unrelated arithmetic while keeping the same stage name.

## Long-running science

Plugin UI code should launch a worker/job rather than execute expensive Sage code during page render.

Benefits:

- hard timeouts;
- logs;
- job status;
- browser independence;
- reproducibility.

## Imports and module names

Plugin entrypoints may be loaded as standalone modules.

Use plugin-specific module names/import paths. Generic local names such as `backend` can collide when multiple plugins are loaded.

## Database writes

Prefer core-owned persistence/evidence APIs for scientific state.

Direct plugin SQL can accidentally bypass:

- provenance;
- evidence reduction;
- conflict detection;
- migrations;
- exact verification.

A Workspace may read `context.db`, but scientific writes should follow core contracts.

## Official collection

The public installer pulls the official plugin collection from:

```text
https://github.com/crhillresearch/rh-plugins
```

Open **Plugins** in the UI to see the versions actually installed.

## Development checklist

Before shipping a plugin:

1. validate manifest;
2. test empty and realistic states;
3. test one invalid/singular input;
4. ensure widget keys are unique;
5. keep render bounded;
6. exact-check Family maps/sections;
7. keep heuristic and proof metadata separate;
8. ensure timeouts remain inconclusive;
9. record provenance/citations with the plugin;
10. run `rank42.plugin_validate`.

## Detailed guides

- [Official Plugins](official.md)
- [Official Families](families.md)
- [Features & Workspaces](workspaces-official.md)
- [Write a Family Plugin](family.md)
- [Write a Feature Plugin](feature.md)
- [Write a Workspace Plugin](workspace.md)
