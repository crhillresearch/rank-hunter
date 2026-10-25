"""System → Nodes: actual local compute and dispatch status."""

from __future__ import annotations

import html
import json
import os
import shlex
from fractions import Fraction
from urllib.parse import urlparse

import streamlit as st

from rank42.node_cluster import (
    code_fingerprint, connect_registry, controller_info, controller_port, create_pair_code,
    ensure_controller_running, enqueue_task, list_nodes, list_tasks,
    revoke_node, set_default_worker, set_private_network,
    full_jobs_enabled, remote_search_enabled, set_full_jobs_enabled,
    set_remote_search_enabled, validate_curve_points,
)
from rank42.node_status import local_node_snapshot
from rank42.points import upsert_point
from rank42.ui_components import tabs
from .common import title


def _escape(value):
    return html.escape(str(value), quote=True)


def _metric(snapshot, key):
    return snapshot["metrics"].get(key, {}).get("value") or "—"


def _task_result_label(task):
    if not task["result_json"]:
        return task["error"] or "—"
    result = json.loads(task["result_json"])
    if task["kind"] == "curve_point_search":
        return f'{len(result.get("points") or [])} exact points'
    if task["kind"] == "ratpoints_scan":
        return f'{len(result.get("points") or [])} ratpoints hits'
    if task["kind"] == "ui_job":
        return f'Job #{result.get("job_id")} · exit {result.get("exit_code")}'
    return "j = " + str(result.get("j_invariant"))


def page(db, ctx):
    title("Nodes")
    section = tabs(
        ["Workers", "Connect", "Remote Tasks"],
        value=st.session_state.get("nodes_view") or "Workers",
        key="nodes-view-tabs",
    )
    st.session_state["nodes_view"] = section
    controller_ready = ensure_controller_running(ctx.project_root)
    registry = connect_registry(ctx.project_root)
    try:
        _render_nodes(db, ctx, registry, controller_ready, section)
    finally:
        registry.close()


def _render_nodes(db, ctx, registry, controller_ready, section):
    node = local_node_snapshot(db, ctx.project_root, ctx.db_path)
    workers = list_nodes(registry)
    active_remote_jobs = {}
    for task in registry.execute(
        """SELECT t.payload_json,n.hostname FROM tasks t
           LEFT JOIN nodes n ON n.id=t.node_id
           WHERE t.kind='ui_job' AND t.status='running'"""
    ):
        try:
            active_remote_jobs[int(json.loads(task["payload_json"])["job_id"])] = (
                task["hostname"] or "Workhorse"
            )
        except (ValueError, KeyError, TypeError):
            continue
    connected = sum(bool(worker["online"]) for worker in workers)
    state = "Online" if node["dispatcher_online"] else "Dispatcher offline"
    state_class = "online" if node["dispatcher_online"] else "offline"
    gpu = node["metrics"].get("gpu") or {}
    gpu_label = "GPU detected" if gpu.get("percent") is not None else "No GPU detected"
    st.html("""
    <style>
    .rh-nodes-hero,.rh-nodes-machine,.rh-nodes-info {
      background:var(--rh-card); border:1px solid var(--rh-border);
      border-radius:var(--rh-radius-lg,16px);
    }
    .rh-nodes-hero {padding:20px 24px; margin:8px 0 16px;}
    .rh-nodes-eyebrow {font-size:.72rem;letter-spacing:.1em;text-transform:uppercase;
      font-weight:700;color:var(--rh-muted);}
    .rh-nodes-hero h2 {margin:5px 0 4px;color:var(--rh-text);font-size:1.35rem;}
    .rh-nodes-hero p,.rh-nodes-info p {margin:0;color:var(--rh-text-secondary);}
    .rh-nodes-machine {padding:20px 24px;}
    .rh-nodes-machine-head {display:flex;align-items:flex-start;justify-content:space-between;
      gap:16px;flex-wrap:wrap;}
    .rh-nodes-machine h3 {margin:0 0 4px;font-size:1.18rem;color:var(--rh-text);}
    .rh-nodes-machine-sub {color:var(--rh-muted);font-size:.88rem;}
    .rh-nodes-state {padding:5px 10px;border-radius:99px;font-size:.79rem;font-weight:700;}
    .rh-nodes-state.online {color:var(--rh-success);background:color-mix(in srgb,var(--rh-success) 12%,var(--rh-card));}
    .rh-nodes-state.offline {color:var(--rh-danger,var(--rh-error));background:color-mix(in srgb,var(--rh-danger,var(--rh-error)) 12%,var(--rh-card));}
    .rh-nodes-stats {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-top:18px;}
    .rh-nodes-stat {padding:12px 14px;background:var(--rh-card-2);border:1px solid var(--rh-border);
      border-radius:var(--rh-radius-md,10px);min-width:0;}
    .rh-nodes-stat span {display:block;color:var(--rh-muted);font-size:.76rem;}
    .rh-nodes-stat strong {display:block;color:var(--rh-text);font-size:1.1rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
    .rh-nodes-info {padding:18px 22px;margin-top:16px;}
    .rh-nodes-info h3 {margin:0 0 5px;color:var(--rh-text);font-size:1rem;}
    @media(max-width:650px){.rh-nodes-stats{grid-template-columns:repeat(2,minmax(0,1fr));}}
    </style>
    """)
    if section == 'Workers':
        st.button("Refresh workers", icon=":material/refresh:", key="nodes-refresh-workers")
        st.html(
            '<section class="rh-nodes-hero"><span class="rh-nodes-eyebrow">COMPUTE NODES</span>'
            f'<h2>{1 + connected} node{"s" if connected else ""} online</h2>'
            '<p>This computer controls the database and job queue. Paired workhorses can run complete Pipeline jobs.</p></section>'
        )
        st.html(
            '<section class="rh-nodes-machine" aria-label="Local compute node">'
            '<div class="rh-nodes-machine-head"><div>'
            f'<h3>{_escape(node["hostname"])}</h3>'
            f'<div class="rh-nodes-machine-sub">{_escape(node["role"])} · {_escape(node["platform"])}'
            f' · {node["cpu_cores"] or "—"} CPU cores · {_escape(gpu_label)}</div></div>'
            f'<span class="rh-nodes-state {state_class}">{_escape(state)}</span></div>'
            '<div class="rh-nodes-stats">'
            f'<div class="rh-nodes-stat"><span>Active jobs</span><strong>{node["running"]}</strong></div>'
            f'<div class="rh-nodes-stat"><span>Queued jobs</span><strong>{node["queued"]}</strong></div>'
            f'<div class="rh-nodes-stat"><span>Worker slots</span><strong>{node["worker_slots"]}</strong></div>'
            f'<div class="rh-nodes-stat"><span>CPU</span><strong>{_escape(_metric(node,"cpu"))}</strong></div>'
            f'<div class="rh-nodes-stat"><span>GPU</span><strong>{_escape(_metric(node,"gpu"))}</strong></div>'
            f'<div class="rh-nodes-stat"><span>RAM</span><strong>{_escape(_metric(node,"ram"))}</strong></div>'
            '</div></section>'
        )
        if node["active_jobs"]:
            st.subheader("Active jobs")
            st.dataframe(
                [{"Job": f'#{job["id"]}', "Name": job["label"],
                  "Status": job["status"],
                  "Compute": active_remote_jobs.get(int(job["id"]), "This node"),
                  "Started": job["started_at"] or "—"}
                 for job in node["active_jobs"]],
                hide_index=True, width="stretch",
            )
        else:
            st.caption("No jobs are running on this node.")
        if workers:
            st.subheader("Paired workers")
            st.caption("Tasks go to the default workhorse first. When its slots are full or it goes offline, the next available worker takes them.")
            routed = remote_search_enabled(registry)
            capable = any(
                worker["online"] and (worker["has_ratpoints_cpu"] or worker["has_ratpoints_gpu"])
                for worker in workers
            )
            wanted = st.toggle(
                "Run ratpoints search scans on workers", value=routed,
                disabled=not capable and not routed,
                key="nodes-route-ratpoints",
            )
            if wanted != routed:
                set_remote_search_enabled(registry, wanted)
                st.rerun()
            st.caption("Point scans from local jobs can run on workers. Full remote jobs use the setting below.")
            full_routed = full_jobs_enabled(registry)
            compatible = any(
                worker["online"] and worker.get("code_fingerprint") == code_fingerprint()
                and worker["has_ratpoints_cpu"]
                for worker in workers
            )
            full_wanted = st.toggle(
                "Run complete Pipeline searches on workhorses", value=full_routed,
                disabled=not compatible and not full_routed,
                key="nodes-route-full-jobs",
            )
            if full_wanted != full_routed:
                set_full_jobs_enabled(registry, full_wanted)
                st.rerun()
            st.caption("The workhorse runs the Pipeline process. This computer keeps rank42.db, the queue, and the live log. Workers must have matching Rank Hunter code; family plugins are staged with each job.")
            ids = [worker["id"] for worker in workers]
            names = {worker["id"]: worker["hostname"] for worker in workers}
            selected = st.selectbox(
                "Default workhorse", ids,
                format_func=lambda value: names.get(value, value),
                key="nodes-default-worker",
            )
            if st.button("Set default", key="nodes-set-default", icon=":material/priority_high:"):
                set_default_worker(registry, selected)
                st.rerun()
            for index, worker in enumerate(workers):
                worker_state = "Online" if worker["online"] else "Offline"
                worker_class = "online" if worker["online"] else "offline"
                position = "Default workhorse" if index == 0 else f"Fallback #{index + 1}"
                st.html(
                    '<section class="rh-nodes-machine" aria-label="Remote worker" style="margin-top:10px">'
                    '<div class="rh-nodes-machine-head"><div>'
                    f'<h3>{_escape(worker["hostname"])}</h3>'
                    f'<div class="rh-nodes-machine-sub">{_escape(position)} · {_escape(worker["platform"])}'
                    f' · {worker["cpu_cores"]} CPU cores · {worker["slots"]} slots'
                    f' · {"GPU" if worker["has_gpu"] else "CPU"}'
                    f' · ratpoints {"ready" if worker["has_ratpoints_cpu"] else "missing"}'
                    f' · code {"matching" if worker.get("code_fingerprint") == code_fingerprint() else "different"}'
                    f' · {worker["active_tasks"]} active tasks</div></div>'
                    f'<span class="rh-nodes-state {worker_class}">{worker_state}</span></div></section>'
                )
                if st.button(f'Remove {worker["hostname"]}', key=f'nodes-remove-{worker["id"]}',
                             icon=":material/link_off:"):
                    revoke_node(registry, worker["id"])
                    st.session_state.pop("nodes-default-worker", None)
                    st.rerun()
    if section == 'Connect':
        st.subheader("Connect a worker")
        listener = controller_info(ctx.project_root)
        bind = listener.get("bind") if listener else None
        if not controller_ready:
            st.error("Node controller is not running. Check .rank42-ui/node-controller.log or port 8765.")
        elif bind in {"127.0.0.1", "localhost", "::1"}:
            st.info(
                "The controller currently listens only on this computer. Enable access on "
                "a trusted private network or VPN before pairing another computer."
            )
        if controller_ready and not os.environ.get("RANK_HUNTER_NODE_BIND"):
            exposed = bind in {"0.0.0.0", "::"}
            label = "Listen on this computer only" if exposed else "Enable private network workers"
            if st.button(label, key="nodes-network-toggle", icon=":material/lan:"):
                if set_private_network(ctx.project_root, not exposed):
                    st.rerun()
                st.error("Could not restart the node controller; inspect .rank42-ui/node-controller.log")
        default_url = f"http://127.0.0.1:{controller_port()}"
        url = st.text_input("Controller address reachable by the worker", value=default_url,
                            key="nodes-controller-url")
        parsed = urlparse(url.strip())
        url_valid = (
            parsed.scheme in {"http", "https"} and bool(parsed.hostname)
            and not parsed.username and parsed.path in {"", "/"}
            and not parsed.query and not parsed.fragment
        )
        if parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
            st.caption("For another computer, replace localhost with this laptop's reachable IP address before copying the worker command.")
        if st.button("Create one-time pairing code", disabled=not (controller_ready and url_valid),
                     icon=":material/add_link:", key="nodes-pair-code"):
            st.session_state["nodes-pair-code-value"] = create_pair_code(registry)
        code = st.session_state.get("nodes-pair-code-value")
        if code:
            st.caption("This code expires in 15 minutes and works once. Run these commands on the worker computer using the same Rank Hunter version and Sage Python:")
            st.code(
                f"read -rsp 'Pairing code: ' RH_NODE_PAIR_CODE; echo; export RH_NODE_PAIR_CODE\n"
                f"python -m rank42.node_worker --controller {shlex.quote(url.strip())} --slots 1",
                language="bash",
            )
            st.code(code, language=None)
            st.caption("The worker stores its long-lived credential in ~/.config/rank-hunter with file permissions 0600. Keep the controller on a trusted network or VPN.")
    if section == 'Remote Tasks':
        st.subheader("Remote calculations")
        st.caption("Send a bounded calculation to a paired worker. The controller checks exact results before accepting them; rank and independence are never inferred from a point search alone.")
        if not any(worker["online"] for worker in workers):
            st.info("No remote worker is online. Pair and start one from Connect to send calculations.")
        calculation = st.selectbox(
            "Calculation", ["Rational point search", "Curve invariants"],
            key="nodes-task-kind",
        )
        latest = db.execute(
            "SELECT id FROM curves WHERE a_invariants_json IS NOT NULL "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        curve_id = st.number_input("Curve ID", min_value=1, step=1,
                                   value=int(latest["id"]) if latest else 1,
                                   key="nodes-curve-id")
        height = None
        if calculation == "Rational point search":
            height = st.number_input(
                "Naive log height limit", min_value=1.0, max_value=10.0,
                value=5.0, step=0.5, key="nodes-point-height",
                help="A bounded Sage point search. Higher limits can become much slower.",
            )
        if st.button("Send to worker", disabled=not any(w["online"] for w in workers),
                     icon=":material/send:", key="nodes-send-test"):
            row = db.execute("SELECT a_invariants_json FROM curves WHERE id=?", (int(curve_id),)).fetchone()
            try:
                ainvs = json.loads(row["a_invariants_json"]) if row else None
                kind = "curve_point_search" if height is not None else "curve_invariants"
                payload = {
                    "curve_id": int(curve_id), "a_invariants": ainvs,
                }
                if height is not None:
                    payload["height_limit"] = float(height)
                task_id = enqueue_task(registry, kind, payload)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                st.error(f"Curve #{curve_id} has no valid five-term model: {exc}")
            else:
                st.success(f"Task #{task_id} queued for a remote worker.")
        st.button("Refresh tasks", icon=":material/refresh:", key="nodes-refresh-tasks")
        tasks = list_tasks(registry, limit=12)
        if tasks:
            st.dataframe(
                [{"Task": f'#{task["id"]}', "Curve": (
                    json.loads(task["payload_json"]).get("curve_id") or "—"),
                  "Calculation": (
                      "Pipeline job" if task["kind"] == "ui_job" else
                      "Point search" if task["kind"] == "curve_point_search" else
                      "Ratpoints scan" if task["kind"] == "ratpoints_scan" else "Invariants"
                  ),
                  "Status": task["status"], "Worker": task["node_name"] or "—",
                  "Result": _task_result_label(task)} for task in tasks],
                hide_index=True, width="stretch",
            )
            for task in tasks:
                if task["status"] != "succeeded" or not task["result_json"]:
                    continue
                result = json.loads(task["result_json"])
                if task["kind"] in {"ui_job", "ratpoints_scan"}:
                    continue
                with st.expander(f'Task #{task["id"]} · curve #{result.get("curve_id")}', expanded=False):
                    if task["kind"] == "curve_point_search":
                        points = result.get("points") or []
                        st.write(f"{len(points)} exact rational point(s) found. Independence is untested.")
                        if points:
                            st.dataframe([{"x": x, "y": y} for x, y in points],
                                         hide_index=True, width="stretch")
                            if st.button("Save exact points to Points", key=f'nodes-save-{task["id"]}'):
                                payload = json.loads(task["payload_json"])
                                current = db.execute(
                                    "SELECT a_invariants_json FROM curves WHERE id=?",
                                    (int(result["curve_id"]),),
                                ).fetchone()
                                try:
                                    validate_curve_points(payload, result)
                                    if current is None or [Fraction(str(x)) for x in json.loads(current["a_invariants_json"])] != [Fraction(str(x)) for x in payload["a_invariants"]]:
                                        raise ValueError("stored curve model changed since this task was queued")
                                    for x, y in points:
                                        upsert_point(
                                            db, curve_id=int(result["curve_id"]), x=x, y=y,
                                            source="remote_node_point_search", role="candidate",
                                            exact_verified=True, independence_status="unknown",
                                            rigorous_independent=False, search_ref=f'node-task:{task["id"]}',
                                            metadata={"height_limit": result["height_limit"],
                                                      "node_id": task["node_id"]},
                                        )
                                except (ValueError, TypeError, KeyError) as exc:
                                    st.error(f"Cannot save points: {exc}")
                                else:
                                    st.success(f"Saved {len(points)} exact point(s) for independence testing.")
                    else:
                        st.json(result)
        st.html(
            '<section class="rh-nodes-info"><h3>Job eligibility</h3>'
            '<p>Remote workers currently accept bounded rational-point searches and exact '
            'curve-invariant tasks. When enabled on Workers, Search and Pipeline ratpoints '
            'scans also run on the workhorse pool. Their coordinators, database writes, '
            'and other stages remain on the controller.</p></section>'
        )
