from __future__ import annotations

import streamlit as st

from rank42 import __description__, __repository_url__, __title__, __version__
from rank42.feature_hooks import render_feature_hook
from rank42.icarm_api import (
    delete_local_token,
    masked_token,
    read_token,
    token_source,
    write_token,
)
from rank42.ratpoints import vendored_executable
from rank42.plugins import Plugin, discover_plugins
from rank42.runtime_validation import (
    probe_runtime_bundle,
    probe_runtime_setting,
    required_runtime_failures,
)
from rank42.settings_registry import setting_value, save_setting_value
from rank42.ui_components import button, selectbox, tabs, text_input
from .common import title


_RUNTIME_LABELS = {
    "science_python": "Science Python / Sage",
    "ratpoints": "ratpoints CPU",
    "ratpoints_gpu": "ratpoints GPU",
}


def _render_runtime_probes(probes):
    labels = {
        "science_python": "Science Python / Sage",
        "ratpoints": "ratpoints CPU",
        "ratpoints_gpu": "ratpoints GPU (optional)",
    }
    for key in ("science_python", "ratpoints", "ratpoints_gpu"):
        probe = probes[key]
        location = probe.resolved or probe.configured or "not configured"
        version = f" · {probe.version}" if probe.version else ""
        message = f"{labels[key]}: {location}{version}"
        if probe.status == "pass":
            st.success(message)
        elif probe.status == "fail":
            st.error(f"{message} — {probe.detail}")
        else:
            st.warning(f"{message} — {probe.detail}")


def _runtime_resolution(values):
    return {
        key: probe_runtime_setting(
            key,
            values[key],
            run_probe=False,
        )
        for key in ("science_python", "ratpoints", "ratpoints_gpu")
    }


def _render_effective_runtime_summary(values, backend):
    resolved = _runtime_resolution(values)
    active_key = "ratpoints_gpu" if str(backend).upper() == "GPU" else "ratpoints"

    with st.container(border=True):
        st.markdown("#### Runtime status")
        engine_col, required_col, optional_col = st.columns(3)
        engine_col.metric("Default point engine", str(backend).upper())
        required_ready = all(
            resolved[key].resolved
            for key in ("science_python", "ratpoints")
        )
        required_col.metric(
            "Required runtimes",
            "Ready" if required_ready else "Needs attention",
        )
        optional_col.metric(
            "Optional GPU",
            "Ready" if resolved["ratpoints_gpu"].resolved else "Unavailable",
        )

        rows = []
        for key in ("science_python", "ratpoints", "ratpoints_gpu"):
            probe = resolved[key]
            if key == active_key:
                usage = "Active point engine"
            elif key == "science_python":
                usage = "Scientific runtime"
            elif probe.required:
                usage = "Required point engine"
            else:
                usage = "Optional accelerator"
            rows.append(
                {
                    "Runtime": _RUNTIME_LABELS[key],
                    "Requirement": "Required" if probe.required else "Optional",
                    "Status": "Ready" if probe.resolved else "Missing",
                    "Use": usage,
                }
            )
        st.dataframe(rows, width="stretch", hide_index=True)
        st.caption(
            "Status above checks executable resolution only. Use Test runtimes "
            "for actual Sage/ratpoints health probes."
        )


def _runtime_settings(db, ctx):
    stored_values = {
        "science_python": str(
            setting_value(
                db,
                "science_python",
                ctx.detected_science_python(),
            )
        ),
        "ratpoints": str(
            setting_value(
                db,
                "ratpoints",
                vendored_executable("CPU", ctx.project_root),
            )
        ),
        "ratpoints_gpu": str(
            setting_value(
                db,
                "ratpoints_gpu",
                vendored_executable("GPU", ctx.project_root),
            )
        ),
    }
    stored_backend = str(
        setting_value(db, "ratpoints_backend", "CPU") or "CPU"
    ).upper()
    if stored_backend not in {"CPU", "GPU"}:
        stored_backend = "CPU"

    _render_effective_runtime_summary(stored_values, stored_backend)

    with st.container(border=True):
        st.markdown("#### Runtime executables")
        st.caption(
            "Science Python / Sage and CPU ratpoints are required. GPU ratpoints "
            "is optional; selecting GPU makes it the default point engine when available."
        )

        science = text_input(
            "Science Python / Sage · Required",
            value=stored_values["science_python"],
            semantic="settings-science-python",
        )
        ratpoints = text_input(
            "CPU ratpoints · Required",
            value=stored_values["ratpoints"],
            semantic="settings-ratpoints",
        )
        ratpoints_gpu = text_input(
            "GPU ratpoints · Optional",
            value=stored_values["ratpoints_gpu"],
            semantic="settings-ratpoints-gpu",
        )
        backend = selectbox(
            "Default point engine",
            ["CPU", "GPU"],
            index=1 if stored_backend == "GPU" else 0,
            semantic="settings-point-engine",
        )

        runtime_values = {
            "science_python": science.strip(),
            "ratpoints": ratpoints.strip(),
            "ratpoints_gpu": ratpoints_gpu.strip(),
        }

        st.markdown("##### Test runtimes")
        st.caption(
            "Run the actual Sage and ratpoints health probes before saving changes."
        )
        if button(
            "Test runtimes",
            semantic="settings-test-runtimes",
            width="stretch",
        ):
            _render_runtime_probes(
                probe_runtime_bundle(
                    runtime_values["science_python"],
                    runtime_values["ratpoints"],
                    runtime_values["ratpoints_gpu"],
                )
            )

    with st.container(border=True):
        st.markdown("#### Default time budgets")
        st.caption(
            "Global defaults only. Family Search, Pipeline stages, and Analysis "
            "actions may override these values for a specific workflow."
        )
        t1, t2, t3 = st.columns(3)
        pari_timeout = int(
            t1.number_input(
                "PARI rank",
                min_value=10,
                value=int(setting_value(db, "pari_rank_timeout", 300)),
                step=30,
            )
        )
        mwrank_timeout = int(
            t2.number_input(
                "mwrank rank",
                min_value=10,
                value=int(setting_value(db, "mwrank_rank_timeout", 300)),
                step=30,
            )
        )
        deep_timeout = int(
            t3.number_input(
                "Deep cert",
                min_value=10,
                value=int(setting_value(db, "deep_cert_timeout", 900)),
                step=60,
            )
        )

    if button(
        "Save runtime defaults",
        semantic="settings-save-runtimes",
        type="primary",
        width="stretch",
    ):
        probes = probe_runtime_bundle(
            runtime_values["science_python"],
            runtime_values["ratpoints"],
            runtime_values["ratpoints_gpu"],
        )
        failures = required_runtime_failures(probes)
        if failures:
            _render_runtime_probes(probes)
            st.error(
                "Runtime settings were not saved. Fix the required "
                "Science Python / Sage and CPU ratpoints runtime checks."
            )
            return

        save_setting_value(
            db,
            "science_python",
            runtime_values["science_python"],
        )
        save_setting_value(db, "ratpoints", runtime_values["ratpoints"])
        save_setting_value(
            db,
            "ratpoints_gpu",
            runtime_values["ratpoints_gpu"],
        )
        save_setting_value(db, "ratpoints_backend", backend)
        save_setting_value(db, "pari_rank_timeout", pari_timeout)
        save_setting_value(db, "mwrank_rank_timeout", mwrank_timeout)
        save_setting_value(db, "deep_cert_timeout", deep_timeout)

        gpu_probe = probes["ratpoints_gpu"]
        if not gpu_probe.healthy:
            st.session_state["settings-runtime-notice"] = (
                "Saved. Required runtimes passed. Optional GPU ratpoints is "
                "unavailable, so GPU searches cannot launch until it is fixed."
            )
        else:
            st.session_state["settings-runtime-notice"] = (
                "Saved. Required runtimes passed their probes."
            )
        st.rerun()


def _icarm_settings(ctx):
    st.subheader("ICARM")
    st.caption(
        "Submission credential only. The token is stored outside rank42.db."
    )
    ready = bool(read_token(ctx.project_root))
    st.write(
        f"Status: **{'configured' if ready else 'not configured'}**"
        + (f" via {token_source(ctx.project_root)}" if ready else "")
    )
    if ready:
        st.caption(f"Credential preview: {masked_token(ctx.project_root)}")
        if token_source(ctx.project_root) == "local file":
            if button(
                "Clear local token",
                semantic="settings-icarm-clear",
                width="stretch",
            ):
                delete_local_token(ctx.project_root)
                st.rerun()
        else:
            st.caption(
                "Token is provided by the environment and cannot be cleared here."
            )
        return

    token = text_input(
        "ICARM token",
        type="password",
        value="",
        semantic="settings-icarm-token",
    )
    if button(
        "Save token",
        semantic="settings-icarm-save",
        width="stretch",
        type="primary",
        disabled=not token.strip(),
    ):
        write_token(ctx.project_root, token)
        st.success("ICARM token saved locally.")
        st.rerun()


def _services_settings(ctx):
    _icarm_settings(ctx)


_PLUGIN_TYPE_LABELS = {
    "family": "Family",
    "feature": "Feature",
    "extension": "Workspace",
}

_PLUGIN_TYPE_ORDER = {
    "family": 0,
    "feature": 1,
    "extension": 2,
}


def _installed_plugin_rows(project_root):
    plugins = [
        rec
        for rec in discover_plugins(project_root)
        if isinstance(rec, Plugin)
    ]
    plugins.sort(
        key=lambda plugin: (
            _PLUGIN_TYPE_ORDER.get(plugin.plugin_type, 99),
            plugin.name.lower(),
            plugin.id,
        )
    )
    return [
        {
            "Type": _PLUGIN_TYPE_LABELS.get(
                plugin.plugin_type,
                str(plugin.plugin_type).title(),
            ),
            "Plugin": plugin.name,
            "Version": plugin.version,
        }
        for plugin in plugins
    ]


def _about_settings(ctx):
    st.markdown(f"### {__title__} · v{__version__}")
    st.write(__description__)
    st.caption(
        "Queue concurrency, dispatcher/service lifecycle, and scheduler controls "
        "are intentionally owned by Jobs rather than Settings."
    )
    st.link_button(
        "GitHub",
        __repository_url__,
        width="stretch",
        icon=":material/open_in_new:",
    )

    st.markdown("#### Installed plugins")
    rows = _installed_plugin_rows(ctx.project_root)
    if rows:
        st.dataframe(rows, width="stretch", hide_index=True)
    else:
        st.caption("No installed plugins are currently discoverable.")


def page(db, ctx):
    title("Settings")
    render_feature_hook(db, ctx, "settings.after_header")

    notice = st.session_state.pop("settings-runtime-notice", None)
    if notice:
        st.success(str(notice))

    current = str(st.session_state.get("settings_view") or "Runtimes")
    if current not in {"Runtimes", "Services", "About"}:
        current = "Runtimes"
    choice = tabs(
        ["Runtimes", "Services", "About"],
        value=current,
        key="settings-view-tabs",
        variant="line",
    )
    st.session_state["settings_view"] = choice
    st.html("<div style='height:.65rem'></div>")

    if choice == "Runtimes":
        _runtime_settings(db, ctx)
    elif choice == "Services":
        _services_settings(ctx)
    else:
        _about_settings(ctx)
