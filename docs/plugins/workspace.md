# Write a Workspace Plugin

A Workspace owns a full optional Rank Hunter page. The manifest type is `extension`.

Use one for an explorer, visualization, comparison tool or other self-contained research surface.

## Layout

```text
extensions/my_workspace/
├── plugin.json
├── extension.py
├── backend.py
└── tests/
```

## Manifest

```json
{
  "schema_version": 1,
  "plugin_type": "extension",
  "id": "my_workspace",
  "name": "My Workspace",
  "version": "0.1.0",
  "enabled_by_default": false,
  "entrypoint": "extension.py",
  "menu": {
    "id": "my_workspace",
    "label": "My Workspace"
  }
}
```

The entrypoint exports:

```python
def render(context):
    ...
```

Enabled Workspaces appear under **Workspaces** in the sidebar.

## Context

`render(context)` receives `ExtensionContextV1`.

Useful fields:

```text
context.project_root
context.db_path
context.db
context.plugin_id
context.plugin_version
context.page_id
context.page_label
context.science_python
```

Reading `context.db` is fine. Prefer core-owned actions when changing Rank Hunter state.

## Keep render fast

A Streamlit page reruns often.

Good render work:

- bounded database queries;
- formatting;
- charts;
- launching a Job.

Bad render work:

- long Sage calculations;
- unbounded scans;
- hidden scientific writes.

## Theme support

Use Rank Hunter's semantic CSS tokens:

```text
--rh-bg
--rh-card
--rh-card-2
--rh-border
--rh-text
--rh-text-secondary
--rh-muted
--rh-primary
--rh-surface-active
```

Scope CSS to your Workspace. Do not depend on generated Streamlit/Emotion class names.

## Widget keys

Prefix keys with the plugin id.

```python
st.selectbox("Curve", options, key="my-workspace-curve")
```

Duplicate keys crash the page.

## Local modules

Workspace entrypoints are loaded as standalone modules. Give local helper modules unique import names instead of relying on a generic `import backend` that may collide with another plugin.

## Browser-side code

Custom HTML/JavaScript is fine for presentation.

Keep exact mathematical values as strings when JavaScript number precision is not enough, and do not let browser-only arithmetic become scientific evidence without core validation.

## Test

At minimum test:

- manifest/menu/entrypoint;
- empty database state;
- one realistic populated state;
- unique widget keys;
- Light/Dark rendering;
- missing or malformed source data.

Validate with:

```bash
sage -python -m rank42.plugin_validate \
  --project-root . \
  --db rank42.db \
  --plugin my_workspace
```
