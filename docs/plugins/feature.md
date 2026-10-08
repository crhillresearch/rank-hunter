# Write a Feature Plugin

A Feature adds something small to an existing Rank Hunter page or launch path.

If it needs a whole page, use a [Workspace](workspace.md).

## Layout

```text
extensions/my_feature/
├── plugin.json
├── feature_ui.py
├── feature.py
└── tests/
```

## Render Feature

```json
{
  "schema_version": 1,
  "plugin_type": "feature",
  "id": "my_feature",
  "name": "My Feature",
  "version": "0.1.0",
  "entrypoint": "feature_ui.py",
  "hooks": ["analysis.work_center.after_header"]
}
```

```python
import streamlit as st

def render(context):
    st.caption("Feature content")
```

Keep it small. A Feature lives inside somebody else's page.

## Public render hooks

Current hooks include:

```text
dashboard.after_header
search.after_header
target.after_header
curves.after_header
curves.actions
candidates.after_header
points.after_header
manage.jobs.results.after_header
data.catalogs.after_header
analysis.work_center.after_header
analysis.descent.after_header
analysis.mw_geometry.after_header
analysis.quartics.after_header
analysis.independence.after_header
analysis.saturation.after_header
manage.campaigns.after_header
manage.jobs.after_header
```

Settings/Database hooks are privileged compatibility hooks, not normal third-party extension points.

## Context

Render hooks receive `FeatureContextV1`.

Useful fields:

```text
context.project_root
context.db_path
context.db
context.plugin_id
context.plugin_version
context.hook_id
context.payload
context.science_python
```

The payload is read-only.

## Search-command Feature

A Feature can transform a command just before Rank Hunter queues it.

Return either:

- `None` — no change;
- a mapping with a non-empty `command` argument list.

Keep transforms deterministic. If the Feature fails, Rank Hunter records the error and keeps the current command.

## UI rules

- prefix widget keys with the plugin id;
- avoid global CSS;
- do not run long science during render;
- do not build a second navigation system inside a hook.

## Proof boundary

A Feature can change search geometry or scheduling.

It cannot declare points independent, set exact rank, or create a rigorous upper bound outside the normal core evidence path.

## Validate

```bash
sage -python -m rank42.plugin_validate \
  --project-root . \
  --db rank42.db \
  --plugin my_feature
```
