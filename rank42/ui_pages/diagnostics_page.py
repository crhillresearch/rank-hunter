from __future__ import annotations

import json
from datetime import datetime, timezone

import streamlit as st

from rank42.diagnostics import collect_diagnostics, format_diagnostic_report
from rank42.auto_search_state import campaign_trials, list_campaigns
from rank42.system_metrics import collect_system_metrics
from rank42.ui_components import tabs
from .common import section_title, title


_STATUS_ICON = {
    "pass": "✅",
    "warn": "⚠️",
    "fail": "❌",
}


def _check_rows(result, area=None):
    rows = result.get("checks") or []
    if area is not None:
        rows = [row for row in rows if row["area"] == area]
    return [
        {
            "status": f"{_STATUS_ICON.get(row['status'], '•')} {row['status'].upper()}",
            "check": row["label"],
            "value": row["value"],
            "detail": row["detail"],
        }
        for row in rows
    ]


def _render_checks(result, area=None):
    rows = _check_rows(result, area)
    if rows:
        st.dataframe(
            rows,
            width="stretch",
            hide_index=True,
            column_config={
                "status": st.column_config.TextColumn("Status", width="small"),
                "check": st.column_config.TextColumn("Check", width="medium"),
                "value": st.column_config.TextColumn("Value", width="large"),
                "detail": st.column_config.TextColumn("Detail", width="large"),
            },
        )


def _runtime_table(result):
    runtime = result.get("runtime") or {}
    rows = [
        {"item": "UI Python", "value": runtime.get("python"), "detail": runtime.get("python_version")},
        {"item": "Science Python / Sage", "value": runtime.get("science_python"), "detail": runtime.get("science_python_configured")},
        {"item": "ratpoints CPU", "value": runtime.get("ratpoints"), "detail": ""},
        {"item": "ratpoints GPU", "value": runtime.get("ratpoints_gpu"), "detail": ""},
    ]
    gpu = (runtime.get("gpu") or {}).get("probe") or {}
    if gpu.get("stdout"):
        rows.append({"item": "NVIDIA GPU", "value": gpu.get("stdout"), "detail": ""})
    sage = runtime.get("sage_probe") or {}
    if sage:
        rows.append({
            "item": "Deep Sage import",
            "value": sage.get("stdout") or "failed",
            "detail": sage.get("stderr") or "",
        })
    return rows


def _plugin_table(result):
    rows = []
    for rec in result.get("plugins") or []:
        rows.append({
            "id": rec["id"],
            "version": rec["version"],
            "type": rec["type"],
            "runtime path": rec["root"],
            "resolved path": rec["resolved_root"],
            "git": (
                f"{rec.get('git_branch') or '?'}@{rec.get('git_commit') or '?'}"
                + (" · dirty" if rec.get("git_dirty") else "")
            ),
        })
    return rows


def _corpus_table(result):
    rows = []
    for rec in result.get("corpora") or []:
        if rec.get("ready"):
            state = "✅ ready"
        elif rec.get("exists"):
            state = "❌ invalid"
        else:
            state = "⚠️ missing"
        rows.append({
            "plugin": rec["plugin_id"],
            "corpus": rec["corpus_id"],
            "state": state,
            "curves": rec.get("curves"),
            "observations": rec.get("observations"),
            "max exact": rec.get("max_exact_rank"),
            "size MB": round(float(rec.get("size_bytes") or 0) / 1024 / 1024, 1),
            "path": rec["path"],
            "error": rec.get("error") or "",
        })
    return rows


def _resource_table():
    return [
        {
            "resource": row.get("label"),
            "value": row.get("value"),
            "detail": row.get("detail"),
            "level": row.get("level"),
        }
        for row in collect_system_metrics()
    ]


def _render_legacy_auto_archive(db):
    """Read-only compatibility view for pre-Pipeline Auto campaigns."""
    campaigns = list_campaigns(db, limit=100, top_level_only=True)
    st.caption(
        "Read-only pre-Pipeline Auto history. Resume an existing stopped attempt "
        "from Jobs; start all new work from Auto/Pipelines."
    )
    if not campaigns:
        st.info("No legacy Auto campaigns are stored.")
        return
    st.dataframe(
        [
            {
                "id": int(row["id"]),
                "status": row["status"],
                "mode": row["search_mode"],
                "target": row["torsion_group"] or row["family_name"] or row["plugin_id"],
                "rank goal": int(row["target_rank"]),
                "done": f"{int(row['candidates_done'])}/{int(row['candidates_total'])}",
                "best rank ≥": int(row["best_lower"] or 0),
                "updated": row["updated_at"],
            }
            for row in campaigns
        ],
        width="stretch",
        hide_index=True,
    )
    selected_id = st.selectbox(
        "Inspect legacy campaign",
        [int(row["id"]) for row in campaigns],
        format_func=lambda value: f"Campaign #{value}",
        key="diagnostics-legacy-auto-campaign",
    )
    trials = campaign_trials(db, selected_id, limit=250)
    if trials:
        st.dataframe(
            [
                {
                    "curve": row["curve_id"],
                    "parameter": row["parameter"],
                    "status": row["status"],
                    "tier": row["tier"],
                    "rank ≥": int(row["rigorous_lower"] or 0),
                    "upper": row["rigorous_upper"],
                    "exact": row["exact_rank"],
                }
                for row in trials
            ],
            width="stretch",
            hide_index=True,
        )


_SNAPSHOT_STALE_AFTER_SECONDS = 300


def _snapshot_freshness(result, *, now=None):
    generated = str((result or {}).get("generated_at") or "").strip()
    mode = str((result or {}).get("verification_mode") or "").strip().lower()
    if not mode:
        mode = "deep" if bool((result or {}).get("deep")) else "fast"

    try:
        created = datetime.fromisoformat(generated.replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        created = created.astimezone(timezone.utc)
    except Exception:
        return {
            "mode": mode,
            "generated_at": generated or "unknown",
            "age_seconds": None,
            "age_label": "unknown age",
            "freshness": "unknown",
            "stale": True,
        }

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    age = max(0.0, (current - created).total_seconds())
    if age < 60:
        age_label = f"{int(age)}s old"
    elif age < 3600:
        age_label = f"{int(age // 60)}m old"
    else:
        age_label = f"{int(age // 3600)}h {int((age % 3600) // 60)}m old"
    stale = age > _SNAPSHOT_STALE_AFTER_SECONDS
    return {
        "mode": mode,
        "generated_at": created.isoformat(),
        "age_seconds": age,
        "age_label": age_label,
        "freshness": "stale" if stale else "fresh",
        "stale": stale,
    }


def _render_snapshot_banner(result):
    snapshot = _snapshot_freshness(result)
    mode_label = "Deep" if snapshot["mode"] == "deep" else "Fast"
    text = (
        f"**{mode_label} snapshot** · generated "
        f"{snapshot['generated_at']} · {snapshot['age_label']}"
    )
    if snapshot["stale"]:
        st.warning(
            text
            + " · **STALE** — refresh before using this snapshot as current "
            "release/runtime state."
        )
    else:
        st.info(text + " · **Fresh**")


def _diagnostic_result(db, ctx, *, deep=False, force=False):
    key = "diagnostics_result_cache"
    if force or key not in st.session_state:
        with st.spinner("Running diagnostics…"):
            # Collect first, then replace the cached snapshot in one assignment.
            # If collection raises, the prior snapshot remains intact.
            fresh = collect_diagnostics(
                ctx.project_root,
                ctx.db_path,
                db,
                deep=bool(deep),
            )
        st.session_state[key] = fresh
    return st.session_state[key]


def page(db, ctx):
    title(
        "Diagnostics",
        "Read-only release and runtime checks: core, evidence, database, jobs, plugins, research libraries, backends, GPU, and storage.",
        "System",
        icon="health_and_safety",
    )

    controls = st.columns([1, 1, 3])
    deep = controls[0].button(
        "Run deep diagnostics",
        width="stretch",
        help=(
            "Also runs bounded full SQLite integrity/foreign-key scans and "
            "starts the configured science runtime to verify that Sage imports."
        ),
    )
    refresh = controls[1].button("Refresh", width="stretch")
    controls[2].caption(
        "Fast diagnostics load by default. Deep diagnostics adds bounded full "
        "database scans and the Sage import probe. Diagnostics never repairs, "
        "migrates, kills, resumes, or rewrites state."
    )

    result = _diagnostic_result(
        db,
        ctx,
        deep=bool(deep),
        force=bool(deep or refresh),
    )

    _render_snapshot_banner(result)

    counts = result["counts"]
    a, b, c, d = st.columns(4)
    a.metric("Release state", result["overall"])
    b.metric("Passed", counts["pass"])
    c.metric("Warnings", counts["warn"])
    d.metric("Failures", counts["fail"])

    overall = str(result.get("overall") or "")
    if overall == "ATTENTION REQUIRED":
        st.error(
            "Diagnostics found release-blocking inconsistencies. "
            "No mathematical evidence was changed."
        )
    elif overall == "DEEP CHECKS INCONCLUSIVE":
        st.warning(
            "Deep diagnostics did not finish every bounded database verification "
            "check. This is inconclusive, not a failed integrity result."
        )
    elif counts["warn"]:
        st.warning(
            f"{overall}. Completed checks contain warnings worth reviewing."
        )
    elif overall == "FAST CHECKS READY":
        st.success(
            "Fast checks passed. Run deep diagnostics for bounded full database "
            "integrity and foreign-key verification."
        )
    else:
        st.success("Deep checks completed successfully.")

    current = str(st.session_state.get("diagnostics_tab") or "Overview")
    choice = tabs(
        ["Overview", "Runtime", "Plugins & Libraries", "State", "Legacy Auto", "Report"],
        value=(
            current
            if current
            in {"Overview", "Runtime", "Plugins & Libraries", "State", "Legacy Auto", "Report"}
            else "Overview"
        ),
        key="diagnostics-main-tabs",
    )
    st.session_state["diagnostics_tab"] = choice

    if choice == "Overview":
        section_title("Release gate")
        _render_checks(result)
        section_title("Live resources")
        st.dataframe(_resource_table(), width="stretch", hide_index=True)
        return

    if choice == "Runtime":
        section_title(
            "Runtime",
            "Fast checks resolve configured executables. Deep probes additionally start the science runtime and import Sage.",
        )
        _render_checks(result, "Runtime")
        st.dataframe(_runtime_table(result), width="stretch", hide_index=True)

        core = result.get("core") or {}
        git = core.get("git") or {}
        st.code(
            "\n".join([
                f"Rank Hunter: {core.get('version', '')}",
                f"Project root: {core.get('project_root', '')}",
                f"Git branch: {git.get('branch', 'unavailable')}",
                f"Git commit: {git.get('commit', 'unavailable')}",
                f"Git dirty: {git.get('dirty', 'unknown')}",
            ]),
            language="text",
        )
        return

    if choice == "Plugins & Libraries":
        section_title(
            "Runtime plugins",
            "The resolved path exposes symlink/source-checkout mismatches that are otherwise easy to miss.",
        )
        plugin_rows = _plugin_table(result)
        if plugin_rows:
            st.dataframe(plugin_rows, width="stretch", hide_index=True)
        else:
            st.info("No valid runtime plugins were discovered.")

        section_title(
            "Libraries",
            "A declared Research Library is checked at the exact cache path core will use.",
        )
        corpus_rows = _corpus_table(result)
        if corpus_rows:
            st.dataframe(corpus_rows, width="stretch", hide_index=True)
        else:
            st.info("No runtime plugin declares a research library.")
        return

    if choice == "State":
        section_title("Database & evidence")
        if not bool(result.get("deep")):
            st.caption(
                "Fast snapshot: full SQLite integrity and foreign-key scans were "
                "not run. Use **Run deep diagnostics** when you want the release-grade "
                "database scan."
            )
        for area in ("Database", "Evidence", "Storage"):
            _render_checks(result, area)

        dbinfo = result.get("database") or {}
        fk_breakdown = dbinfo.get("foreign_key_breakdown") or {}
        if fk_breakdown:
            st.caption("Foreign-key violation breakdown")
            st.dataframe(
                [
                    {"relation": name, "violations": count}
                    for name, count in fk_breakdown.items()
                ],
                width="stretch",
                hide_index=True,
            )
            with st.expander("Foreign-key violation samples"):
                st.dataframe(
                    dbinfo.get("foreign_key_samples") or [],
                    width="stretch",
                    hide_index=True,
                )

        state = result.get("state") or {}
        section_title("Jobs & campaigns")
        _render_checks(result, "State")

        active = state.get("active_jobs") or []
        if active:
            st.caption("Stored active jobs")
            st.dataframe(
                [
                    {
                        "id": row["id"],
                        "kind": row["kind"],
                        "status": row["status"],
                        "pid": row["pid"],
                        "label": row["label"],
                        "updated": row["updated_at"],
                    }
                    for row in active
                ],
                width="stretch",
                hide_index=True,
            )
        stale_campaigns = state.get("stale_campaigns") or []
        if stale_campaigns:
            st.caption("Running campaigns without a live Auto Search worker")
            st.dataframe(stale_campaigns, width="stretch", hide_index=True)
        return

    if choice == "Legacy Auto":
        section_title("Legacy Auto compatibility archive")
        _render_legacy_auto_archive(db)
        return

    section_title(
        "Copyable report",
        "Home-directory paths are redacted to ~ in the exported report.",
    )
    report = format_diagnostic_report(result, redact_home=True)
    st.code(report, language="text")
    st.download_button(
        "Download diagnostics.txt",
        data=report.encode("utf-8"),
        file_name="rank-hunter-diagnostics.txt",
        mime="text/plain",
        width="stretch",
    )

    with st.expander("Raw diagnostic JSON"):
        st.code(
            json.dumps(result, indent=2, sort_keys=True, default=str),
            language="json",
        )
