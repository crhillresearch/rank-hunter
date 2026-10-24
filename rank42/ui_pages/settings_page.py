from __future__ import annotations

import html

import streamlit as st

from rank42 import __description__, __repository_url__, __title__, __version__
from rank42.ai_provider import (SERVICES, default_model, key_source as ai_key_source,
                                models_for_service, selected_service)
from rank42.feature_hooks import render_feature_hook
from rank42.gemini_api import (
    GeminiAPIError,
    check_connection as check_gemini_connection,
    delete_local_key as delete_gemini_key,
    key_source as gemini_key_source,
    write_key as write_gemini_key,
)
from rank42.icarm_api import (
    delete_local_token,
    masked_token,
    read_token,
    token_source,
    write_token,
)
from rank42.openai_api import (
    OpenAIAPIError,
    check_connection as check_openai_connection,
    delete_local_key as delete_openai_key,
    key_source as openai_key_source,
    write_key as write_openai_key,
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
    required_ready = all(resolved[key].resolved for key in ("science_python", "ratpoints"))
    rows = []
    for key in ("science_python", "ratpoints", "ratpoints_gpu"):
        probe = resolved[key]
        status = "Found" if probe.resolved else "Missing"
        tone = "ready" if probe.resolved else "missing"
        path = html.escape(str(probe.resolved or probe.configured or "No executable set"))
        rows.append(
            f'<div class="rh-settings-runtime-row"><span class="rh-settings-runtime-label">'
            f'{html.escape(_RUNTIME_LABELS[key])}</span>'
            f'<span class="rh-settings-runtime-path" title="{path}">{path}</span>'
            f'<span class="rh-settings-status rh-settings-status--{tone}">{status}</span></div>'
        )
    state = "Paths found" if required_ready else "Needs attention"
    tone = "ready" if required_ready else "missing"
    st.html(
        '<section class="rh-settings-runtime-status" aria-label="Runtime status">'
        '<div class="rh-settings-runtime-heading"><div>'
        '<span class="rh-settings-eyebrow">RUNTIME STATUS</span>'
        f'<h3>{state}</h3><p>Executable paths are checked here. Run the health test below to verify Sage and ratpoints.</p>'
        '</div>'
        f'<span class="rh-settings-status rh-settings-status--{tone}">'
        f'{html.escape(str(backend).upper())} point engine</span></div>'
        '<div class="rh-settings-runtime-list">' + "".join(rows) + '</div></section>'
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

    with st.container(key="rh-settings-runtime-controls", border=True):
        heading, engine = st.columns([3, 1], vertical_alignment="bottom")
        heading.markdown("#### Runtime executables")
        with engine:
            backend = selectbox(
                "Default point engine", ["CPU", "GPU"],
                index=1 if stored_backend == "GPU" else 0,
                semantic="settings-point-engine",
            )
        st.caption("Set the executables used by searches. Sage and CPU ratpoints are required; GPU ratpoints is optional.")

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
        runtime_values = {
            "science_python": science.strip(),
            "ratpoints": ratpoints.strip(),
            "ratpoints_gpu": ratpoints_gpu.strip(),
        }

        with st.expander("Default time budgets", expanded=False, icon=":material/timer:"):
            st.caption("Global defaults; individual searches can override these limits.")
            t1, t2, t3 = st.columns(3)
            pari_timeout = int(t1.number_input(
                "PARI rank · seconds", min_value=10,
                value=int(setting_value(db, "pari_rank_timeout", 300)), step=30,
            ))
            mwrank_timeout = int(t2.number_input(
                "mwrank rank · seconds", min_value=10,
                value=int(setting_value(db, "mwrank_rank_timeout", 300)), step=30,
            ))
            deep_timeout = int(t3.number_input(
                "Deep cert · seconds", min_value=10,
                value=int(setting_value(db, "deep_cert_timeout", 900)), step=60,
            ))

        test_col, save_col = st.columns([1, 1], vertical_alignment="bottom")
        with test_col:
            test_clicked = button(
                "Test runtimes", semantic="settings-test-runtimes",
                icon=":material/health_and_safety:", width="stretch",
            )
        with save_col:
            save_clicked = button(
                "Save runtime defaults", semantic="settings-save-runtimes",
                type="primary", icon=":material/save:", width="stretch",
            )
        if test_clicked:
            _render_runtime_probes(probe_runtime_bundle(
                runtime_values["science_python"], runtime_values["ratpoints"],
                runtime_values["ratpoints_gpu"],
            ))

    if save_clicked:
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
    with st.container(key="rh-settings-service-icarm", border=True):
        ready = bool(read_token(ctx.project_root))
        source = token_source(ctx.project_root) if ready else None
        _service_heading("ICARM", source, detail="Submission token")
        st.caption("Used for ICARM submissions. Stored outside rank42.db.")
        if ready:
            st.caption(f"Token: {masked_token(ctx.project_root)}")
            if source == "local file":
                if button("Clear local token", semantic="settings-icarm-clear",
                          icon=":material/delete_outline:"):
                    delete_local_token(ctx.project_root)
                    st.rerun()
            else:
                st.caption("Set in the environment; clear it there to remove access.")
            return
        token = text_input("ICARM token", type="password", value="",
                           semantic="settings-icarm-token")
        if button("Save token", semantic="settings-icarm-save", type="primary",
                  icon=":material/save:", disabled=not token.strip()):
            write_token(ctx.project_root, token)
            st.success("ICARM token saved locally.")
            st.rerun()


def _service_heading(name, source, *, detail=None, selected=False):
    status = "Key set" if source else "Not configured"
    tone = "ready" if source else "missing"
    source_text = "Environment" if source == "environment" else "Local key" if source else "Add a key"
    selected_text = '<span class="rh-settings-service-selected">DEFAULT</span>' if selected else ""
    st.html(
        '<div class="rh-settings-service-heading"><div>'
        f'<span class="rh-settings-eyebrow">{html.escape(detail or "AI SERVICE")}</span>'
        f'<h3>{html.escape(name)} {selected_text}</h3></div>'
        f'<span class="rh-settings-status rh-settings-status--{tone}">'
        f'{status} · {source_text}</span></div>'
    )


def _services_settings(db, ctx):
    st.caption("Manage the credentials used by Agent, AI Hunt, and ICARM submissions.")
    openai_col, gemini_col = st.columns(2, gap="medium")
    with openai_col:
        _openai_settings(db, ctx)
    with gemini_col:
        _gemini_settings(db, ctx)
    _icarm_settings(ctx)


def _gemini_settings(db, ctx):
    with st.container(key="rh-settings-service-gemini", border=True):
        source = gemini_key_source(ctx.project_root)
        _service_heading("Google Gemini", source, selected=selected_service(db) == "Gemini")
        st.caption("Agent chat and AI Hunt planning when Gemini is selected.")
        if source is None:
            candidate = text_input("Gemini API key", type="password", value="",
                                   semantic="settings-gemini-key",
                                   placeholder="Paste your Gemini API key")
            if button("Save key", semantic="settings-gemini-save", type="primary",
                      icon=":material/save:", disabled=not candidate.strip()):
                try:
                    write_gemini_key(ctx.project_root, candidate)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.success("Gemini key saved locally.")
                    st.rerun()
            return
        if source == "local file":
            test_col, clear_col = st.columns(2, gap="small")
            with test_col:
                test_clicked = button("Test connection", semantic="settings-gemini-test",
                                      icon=":material/cable:", width="stretch")
            with clear_col:
                if button("Clear key", semantic="settings-gemini-clear",
                          icon=":material/delete_outline:", width="stretch"):
                    delete_gemini_key(ctx.project_root)
                    st.rerun()
        else:
            st.caption("GEMINI_API_KEY takes precedence; change it in the environment.")
            test_clicked = button("Test connection", semantic="settings-gemini-test-env",
                                  icon=":material/cable:")
        if test_clicked:
            try:
                model_name = check_gemini_connection(
                    ctx.project_root, model=default_model("Gemini", db=db))
            except (GeminiAPIError, ValueError) as exc:
                st.error(str(exc))
            else:
                st.success(f"Gemini connection ready · {model_name}")


def _openai_settings(db, ctx):
    with st.container(key="rh-settings-service-openai", border=True):
        source = openai_key_source(ctx.project_root)
        _service_heading("OpenAI API", source, selected=selected_service(db) == "OpenAI")
        st.caption("Agent chat and AI Hunt planning when OpenAI is selected.")
        if source is None:
            candidate = text_input("OpenAI API key", type="password", value="",
                                   semantic="settings-openai-key",
                                   placeholder="Paste your OpenAI API key")
            if button("Save key", semantic="settings-openai-save", type="primary",
                      icon=":material/save:", disabled=not candidate.strip()):
                try:
                    write_openai_key(ctx.project_root, candidate)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.success("OpenAI key saved locally.")
                    st.rerun()
            return
        if source == "local file":
            test_col, clear_col = st.columns(2, gap="small")
            with test_col:
                test_clicked = button("Test connection", semantic="settings-openai-test",
                                      icon=":material/cable:", width="stretch")
            with clear_col:
                if button("Clear key", semantic="settings-openai-clear",
                          icon=":material/delete_outline:", width="stretch"):
                    delete_openai_key(ctx.project_root)
                    st.rerun()
        else:
            st.caption("OPENAI_API_KEY takes precedence; change it in the environment.")
            test_clicked = button("Test connection", semantic="settings-openai-test-env",
                                  icon=":material/cable:")
        if test_clicked:
            try:
                model_name = check_openai_connection(
                    ctx.project_root, model=default_model("OpenAI", db=db))
            except (OpenAIAPIError, ValueError) as exc:
                st.error(str(exc))
            else:
                st.success(f"OpenAI connection ready · {model_name}")


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


def _general_settings(db, ctx):
    appearance = str(setting_value(db, "ui_appearance", "light") or "light").lower()
    if appearance not in {"light", "dark"}:
        appearance = "light"
    with st.container(key="rh-settings-general-appearance", border=True):
        st.subheader("Appearance")
        selected = st.segmented_control(
            "Color mode", ["Light", "Dark"],
            default=appearance.title(),
            key=f"settings-general-appearance-{appearance}",
        ) or appearance.title()
        if selected.lower() != appearance:
            from rank42.ui import _apply_native_streamlit_appearance
            _apply_native_streamlit_appearance(ctx.theme, selected.lower())
            save_setting_value(db, "ui_appearance", selected.lower())
            st.rerun()

    with st.container(key="rh-settings-general-ai", border=True):
        st.subheader("AI")
        service = selected_service(db)
        service_col, model_col, agent_col = st.columns(
            [0.95, 1.1, 1.45], gap="small", vertical_alignment="bottom"
        )
        with service_col:
            chosen = st.segmented_control(
                "Default AI service", SERVICES, default=service,
                key=f"settings-ai-service-{service}",
                help="The selected service handles Agent chat and the next AI Hunt planning round, including active hunts.",
            ) or service
        if chosen != service:
            save_setting_value(db, "default_ai_service", chosen)
            st.rerun()
        models = models_for_service(service)
        selected_model = default_model(service, db=db)
        with model_col:
            chosen_model = st.selectbox(
                "Default model", models, index=models.index(selected_model),
                key=f"settings-default-model-{service}-{selected_model}",
            )
        if chosen_model != selected_model:
            if service == "Gemini":
                save_setting_value(db, "gemini_default_model", chosen_model)
            else:
                save_setting_value(db, "openai_default_model", chosen_model)
            st.rerun()
        enabled = bool(setting_value(db, "agent_enabled", True))
        with agent_col:
            shown = st.toggle("Show Rank Hunter Agent", value=enabled,
                              key=f"settings-agent-enabled-{int(enabled)}")
        if shown != enabled:
            save_setting_value(db, "agent_enabled", bool(shown))
            st.rerun()
        if not ai_key_source(ctx.project_root, service):
            st.caption(f"Add a {service} API key under Services to use AI features.")


def page(db, ctx):
    title("Settings")
    render_feature_hook(db, ctx, "settings.after_header")

    lifecycle_action = st.session_state.pop("settings_lifecycle_action", None)
    if lifecycle_action == "Restart":
        from rank42.ui import _restart_server_confirmation
        _restart_server_confirmation(ctx)
    elif lifecycle_action == "Power off":
        from rank42.ui import _shutdown_server_confirmation
        _shutdown_server_confirmation()

    notice = st.session_state.pop("settings-runtime-notice", None)
    if notice:
        st.success(str(notice))

    current = str(st.session_state.get("settings_view") or "General")
    if current not in {"General", "Runtimes", "Services", "About"}:
        current = "General"
    choice = tabs(
        ["General", "Runtimes", "Services", "About"],
        value=current,
        key="settings-view-tabs",
        variant="line",
    )
    st.session_state["settings_view"] = choice

    if choice == "General":
        _general_settings(db, ctx)
    elif choice == "Runtimes":
        _runtime_settings(db, ctx)
    elif choice == "Services":
        _services_settings(db, ctx)
    else:
        _about_settings(ctx)
