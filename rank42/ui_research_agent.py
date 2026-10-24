"""Global Rank Hunter Agent popover, shown for a configured AI service."""

from __future__ import annotations

import time
from contextlib import closing

import streamlit as st

from rank42.db import connect_existing
from rank42.ai_provider import key_source, selected_service
from rank42.gemini_api import GeminiAPIError
from rank42.groq_api import GroqAPIError
from rank42.openai_api import OpenAIAPIError
from rank42.research_agent import calculate_log_n, quick_response, stream_answer
from rank42.research_agent_actions import audit_action, execute_action, plan_action
from rank42.settings_registry import setting_value
from rank42.ui_active_curve import get_active_curve_id


_PAGE_SELECTION_KEYS = {
    "Search": "new_search_view",
    "Tools": "tools_selection",
    "Pipelines": "builder_main_view",
    "Jobs": "jobs_section",
    "Candidates": "candidates_view",
    "Settings": "settings_view",
}


def render(db, ctx, page: str) -> None:
    if not setting_value(db, "agent_enabled", True):
        return
    if not key_source(ctx.project_root, selected_service(db)):
        return
    st.markdown(
        """<style>
        div.st-key-rh-agent-launch {
            position: fixed; right: 1.25rem; bottom: 1.25rem;
            z-index: 9999; width: auto; max-width: calc(100vw - 2rem);
        }
        div.st-key-rh-agent-launch > div {width: auto;}
        div.st-key-rh-agent-launch button[data-testid="stPopoverButton"] {
            border-radius: 50% !important;
            border: 1px solid var(--rh-border-strong);
            background: var(--rh-card); color: var(--rh-text);
            box-shadow: 0 4px 18px var(--rh-shadow, rgba(0,0,0,.16));
            width: 3rem !important; min-width: 3rem !important;
            height: 3rem !important; min-height: 3rem !important;
            padding: 0 !important; position: relative !important;
            display: flex !important; align-items: center !important;
            justify-content: center !important;
        }
        div.st-key-rh-agent-launch button[data-testid="stPopoverButton"] > div,
        div.st-key-rh-agent-launch button[data-testid="stPopoverButton"] > div > div:first-child {
            width: 100% !important; margin: 0 !important;
            display: flex !important; align-items: center !important;
            justify-content: center !important; gap: 0 !important;
        }
        div.st-key-rh-agent-launch button[data-testid="stPopoverButton"] > div > div:last-child {
            display: none !important;
        }
        div.st-key-rh-agent-launch button[data-testid="stPopoverButton"] [data-testid="stIconMaterial"],
        div.st-key-rh-agent-launch button[data-testid="stPopoverButton"] .material-symbols-rounded,
        div.st-key-rh-agent-launch button[data-testid="stPopoverButton"] svg {
            position: absolute !important; left: 50% !important;
            top: 50% !important; transform: translate(-50%, -50%) !important;
            font-size: 1.3rem !important;
            width: 1.3rem !important; height: 1.3rem !important;
            flex: none !important;
        }
        div.st-key-rh-agent-launch button[data-testid="stPopoverButton"] [data-testid="stMarkdownContainer"],
        div.st-key-rh-agent-launch button[data-testid="stPopoverButton"] p {
            position: absolute !important;
            width: 1px !important; height: 1px !important;
            padding: 0 !important; margin: 0 !important;
            overflow: hidden !important;
            clip-path: inset(50%) !important;
            white-space: nowrap !important;
        }
        div.st-key-rh-agent-launch button[data-testid="stPopoverButton"][aria-expanded="true"] {
            background: var(--rh-primary) !important;
            border-color: var(--rh-primary) !important;
            color: var(--rh-text-on-accent) !important;
        }
        div.st-key-rh-agent-launch button[data-testid="stPopoverButton"]:focus-visible {
            outline: 2px solid var(--rh-primary);
            outline-offset: 3px;
        }
        div[data-testid="stPopoverBody"][aria-label="Agent"] {
            width: min(420px, calc(100vw - 1.5rem)) !important;
            min-width: 0 !important;
            height: min(570px, calc(100dvh - 1.5rem)) !important;
            max-height: calc(100dvh - 1.5rem) !important;
            overflow: hidden !important;
            background: var(--rh-card);
        }
        div[data-testid="stPopoverBody"][aria-label="Agent"] div.st-key-rh-agent-header {
            padding-bottom: .35rem;
            border-bottom: 1px solid var(--rh-border);
        }
        div[data-testid="stPopoverBody"][aria-label="Agent"] div.st-key-rh-agent-messages {
            height: min(365px, calc(100dvh - 215px)) !important;
            min-height: 60px;
            overflow-y: auto;
            background: var(--rh-card);
        }
        div[data-testid="stPopoverBody"][aria-label="Agent"] div.st-key-rh-agent-composer {
            position: sticky; bottom: 0; z-index: 2;
            padding-top: .45rem;
            border-top: 1px solid var(--rh-border);
            background: var(--rh-card);
        }
        div[data-testid="stPopoverBody"][aria-label="Agent"] div.st-key-rh-agent-composer [data-testid="stForm"] {
            border: 0; padding: 0; background: transparent;
        }
        div[data-testid="stPopoverBody"][aria-label="Agent"] div.st-key-rh-agent-composer input {
            border-radius: 999px;
            background: var(--rh-card-2);
        }
        div[data-testid="stPopoverBody"][aria-label="Agent"] div.st-key-rh-agent-send button {
            width: 2.55rem !important; min-width: 2.55rem !important;
            height: 2.55rem !important; min-height: 2.55rem !important;
            padding: 0 !important; border-radius: 50% !important;
            display: flex !important; align-items: center !important;
            justify-content: center !important;
        }
        div[data-testid="stPopoverBody"][aria-label="Agent"] div.st-key-rh-agent-send button [data-testid="stMarkdownContainer"],
        div[data-testid="stPopoverBody"][aria-label="Agent"] div.st-key-rh-agent-send button p {
            position: absolute !important;
            width: 1px !important; height: 1px !important;
            padding: 0 !important; margin: 0 !important;
            overflow: hidden !important;
            clip-path: inset(50%) !important;
            white-space: nowrap !important;
        }
        div[data-testid="stPopoverBody"][aria-label="Agent"] div.st-key-rh-agent-send button svg {
            width: 1.3rem !important; height: 1.3rem !important;
        }
        div[data-testid="stPopoverBody"][aria-label="Agent"] div.st-key-rh-agent-send button [data-testid="stIconMaterial"],
        div[data-testid="stPopoverBody"][aria-label="Agent"] div.st-key-rh-agent-send button .material-symbols-rounded {
            font-size: 1.3rem !important;
        }
        @media (max-width: 640px) {
            div.st-key-rh-agent-launch {right: .7rem; bottom: .7rem;}
        }
        </style>""",
        unsafe_allow_html=True,
    )
    _agent_panel(ctx, page)


@st.fragment
def _agent_panel(ctx, page: str) -> None:
    # A fragment reruns after the parent page has closed its database handle.
    with closing(connect_existing(ctx.db_path)) as db:
        _render_panel(db, ctx, page)


def _render_panel(db, ctx, page: str) -> None:
    with st.container(key="rh-agent-launch", border=False):
        with st.popover(
            "Agent", icon=":material/auto_awesome:", key="rh-agent-popover",
            help="Rank Hunter Agent. App changes require confirmation.",
        ):
            with st.container(key="rh-agent-header", border=False):
                heading, clear = st.columns([4, 1], vertical_alignment="center")
                with heading:
                    st.markdown("**Rank Hunter Agent**")
                    active = get_active_curve_id(st.session_state)
                    st.caption(f"{page} · {selected_service(db)}"
                               + (f" · curve #{active}" if active else ""))
                if clear.button("Clear", key="rh-agent-clear", type="tertiary"):
                    st.session_state["rh_agent_messages"] = []
                    st.session_state["rh_agent_pending"] = None
            messages = st.session_state.setdefault("rh_agent_messages", [])
            chat_box = st.container(height=365, border=False, autoscroll=True, key="rh-agent-messages")
            with chat_box:
                if not messages:
                    st.markdown("**What can I help you find?**")
                    st.caption("Ask about your research, or tell me to create a campaign, start an AI Hunt, manage a plugin, or control a job.")
                for entry in messages[-16:]:
                    with st.chat_message(entry["role"]):
                        st.markdown(entry["text"], unsafe_allow_html=False)
                pending_slot = st.empty()
            with st.container(key="rh-agent-composer", border=False):
                with st.form("rh-agent-form", clear_on_submit=True, border=False):
                    prompt_col, send_col = st.columns([8.8, 1.2], gap="small", vertical_alignment="center")
                    with prompt_col:
                        question = st.text_input(
                            "Message", placeholder="Message Agent…", max_chars=1500,
                            label_visibility="collapsed",
                        )
                    with send_col:
                        submitted = st.form_submit_button(
                            "Send", icon=":material/arrow_upward:", type="primary",
                            key="rh-agent-send", help="Send message",
                        )
            if submitted and question.strip():
                recent_requests = [t for t in st.session_state.get("rh_agent_request_times", [])
                                   if time.monotonic() - t < 600]
                if len(recent_requests) >= 12:
                    st.warning("Chat limit reached. Try again in a few minutes.")
                    return
                recent_requests.append(time.monotonic())
                st.session_state["rh_agent_request_times"] = recent_requests
                prior = list(messages[-6:])
                messages.append({"role": "user", "text": question.strip()})
                st.session_state["rh_agent_pending"] = None
                with chat_box:
                    with st.chat_message("user"):
                        st.write(question.strip())
                try:
                    selected_view = (st.session_state.get(_PAGE_SELECTION_KEYS[page])
                                     if page in _PAGE_SELECTION_KEYS else None)
                    action_plan = plan_action(
                        db, ctx.project_root, message=question,
                        active_curve_id=get_active_curve_id(st.session_state),
                        history=prior,
                    )
                    response = ({"reply": "Review the action below before I make any changes.",
                                 "action": "planned", "curve_id": None}
                                if action_plan else quick_response(
                                    db, ctx.project_root, question,
                                    active_curve_id=get_active_curve_id(st.session_state),
                                    history=prior,
                                ))
                    if response is None:
                        chunks = stream_answer(
                            db, ctx.project_root, page=page, message=question,
                            history=prior, active_curve_id=get_active_curve_id(st.session_state),
                            page_selection=selected_view,
                            db_path=ctx.db_path,
                        )
                        reply = ""
                        with chat_box:
                            with st.chat_message("assistant"):
                                output = st.empty()
                                output.caption("Thinking…")
                                try:
                                    for chunk in chunks:
                                        reply += str(chunk)
                                        output.write(reply[:2000])
                                        if len(reply) >= 2000:
                                            break
                                finally:
                                    close = getattr(chunks, "close", None)
                                    if callable(close):
                                        close()
                                if not reply:
                                    reply = "The AI service returned no answer. Try a more specific question."
                                output.write(reply[:2000])
                        response = {"reply": reply[:2000], "action": "none", "curve_id": None}
                    else:
                        with chat_box:
                            with st.chat_message("assistant"):
                                st.write(response["reply"])
                    messages.append({"role": "assistant", "text": response["reply"]})
                    if response["action"] == "calculate_log_n":
                        st.session_state["rh_agent_pending"] = {
                            "kind": "calculate_log_n", "curve_id": response["curve_id"]}
                    elif action_plan:
                        st.session_state["rh_agent_pending"] = action_plan
                except (GeminiAPIError, OpenAIAPIError, GroqAPIError, ValueError) as exc:
                    messages.append({"role": "assistant", "text": str(exc)})
                    with chat_box:
                        with st.chat_message("assistant"):
                            st.write(str(exc))
                del messages[:-24]
            pending = st.session_state.get("rh_agent_pending")
            if pending:
                with pending_slot.container():
                    if pending.get("kind") == "calculate_log_n":
                        curve_id = int(pending["curve_id"])
                        st.info(f"Calculate conductor and log N for stored curve #{curve_id}? Rank Hunter may update this curve's conductor. Worker limit: 20 seconds.")
                    else:
                        st.info(pending["preview"])
                    run, cancel = st.columns(2)
                    if run.button("Calculate" if pending.get("kind") == "calculate_log_n" else "Confirm",
                                  type="primary", key="rh-agent-confirm"):
                        st.session_state["rh_agent_pending"] = None
                        pending_slot.empty()
                        try:
                            with chat_box:
                                with st.spinner("Working…"):
                                    if pending.get("kind") == "calculate_log_n":
                                        result = calculate_log_n(db, ctx.project_root, curve_id)
                                    else:
                                        audit_action(ctx.project_root, pending, "started", "confirmed in chat")
                                        result = execute_action(db, ctx, pending)
                                        audit_action(ctx.project_root, pending, "complete", result)
                        except Exception as exc:
                            if pending.get("kind") != "calculate_log_n":
                                audit_action(ctx.project_root, pending, "failed", type(exc).__name__)
                            result = f"Action failed: {str(exc)[:200]}"
                        messages.append({"role": "assistant", "text": result})
                        with chat_box:
                            with st.chat_message("assistant"):
                                st.write(result)
                    elif cancel.button("Cancel", key="rh-agent-cancel"):
                        st.session_state["rh_agent_pending"] = None
                        pending_slot.empty()
