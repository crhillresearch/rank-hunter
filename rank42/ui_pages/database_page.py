from __future__ import annotations

from rank42.feature_hooks import render_feature_hook
from rank42.database_checks import pragma_rows_with_budget, sql_is_readonly
from rank42.database_verification import (
    enqueue_full_database_verification,
    load_full_database_verification,
)
import streamlit as st

from rank42.db import repair_orphan_quartic_curve_refs
from rank42.db_backup import (
    backup_metadata,
    create_backup,
    import_backup_bytes,
    list_backups,
    restore_backup,
)
from rank42.maintenance import (
    MaintenanceBusyError,
    acquire_maintenance_lock,
    maintenance_status,
)
from rank42.ui_components import tabs
from rank42.ui_store import list_jobs
from .common import title


_TABLES = (
    "curves",
    "candidate_pools",
    "candidates",
    "points",
    "mw_lattices",
    "ui_jobs",
)


_MAX_BROWSER_BACKUP_DOWNLOAD_BYTES = 128 * 1024 * 1024


def _backup_browser_download_allowed(size_bytes: int) -> bool:
    return 0 <= int(size_bytes) <= _MAX_BROWSER_BACKUP_DOWNLOAD_BYTES


def _deferred_backup_bytes(path):
    """Read a bounded-size backup only when the researcher clicks Download."""
    def _load():
        with path.open("rb") as handle:
            return handle.read()

    return _load


def _sql_is_readonly(sql: str) -> bool:
    """Compatibility seam for the shared conservative SQL classifier."""
    return sql_is_readonly(sql)


def _active(db):
    return [
        row
        for row in list_jobs(db, limit=100)
        if row["status"] in {"queued", "running", "stopping"}
    ]


def _restore_confirmed(expected_name: str, typed_name: str) -> bool:
    return bool(expected_name) and str(typed_name or "").strip() == str(expected_name)


def _maintenance_label(status: dict[str, object]) -> str:
    metadata = dict(status.get("metadata") or {})
    reason = str(metadata.get("reason") or "database maintenance")
    owner = metadata.get("pid")
    created = metadata.get("created_at")
    parts = [reason]
    if owner:
        parts.append(f"PID {owner}")
    if created:
        parts.append(str(created))
    return " · ".join(parts)


def _warn_external_processes(external) -> None:
    rows = list(external or [])
    if not rows:
        return
    rendered = ", ".join(
        f"{row.get('label') or row.get('module') or 'Rank Hunter'} "
        f"(PID {row.get('pid')})"
        for row in rows[:6]
    )
    suffix = "" if len(rows) <= 6 else f" +{len(rows) - 6} more"
    st.warning(
        "Observed terminal-launched Rank Hunter work while entering maintenance: "
        f"{rendered}{suffix}. These processes are not killed automatically."
    )


def _render_maintenance_state(ctx) -> None:
    status = maintenance_status(ctx.db_path)
    if status["active"]:
        st.warning(
            "Database maintenance mode is active in another Rank Hunter process/session: "
            + _maintenance_label(status)
        )
    elif status["stale_state"]:
        st.caption(
            "A stale maintenance metadata file is present, but no exclusive "
            "maintenance lock is currently held."
        )


def _database_overview(db, ctx):
    size = ctx.db_path.stat().st_size if ctx.db_path.exists() else 0
    integrity = st.session_state.get("database_integrity_result")

    a, b, c = st.columns(3)
    a.metric(
        "Database",
        (
            "Healthy"
            if integrity == "ok"
            else (str(integrity) if integrity else "Connected")
        ),
    )
    b.metric("File size", f"{size / 1024 / 1024:.1f} MB")
    c.metric("Journal", str(db.execute("PRAGMA journal_mode").fetchone()[0]))

    st.code(
        f"Path: {ctx.db_path}\n"
        f"User version: {db.execute('PRAGMA user_version').fetchone()[0]}"
    )

    left, right = st.columns(2)
    if left.button(
        "Run integrity check",
        width="stretch",
        help=(
            "Runs a bounded SQLite PRAGMA integrity_check. If the time budget "
            "expires, the result is inconclusive rather than a failure."
        ),
    ):
        with st.spinner("Running bounded SQLite integrity check…"):
            scan = pragma_rows_with_budget(
                db, "PRAGMA integrity_check", timeout_seconds=8
            )
        if scan["completed"]:
            rows = scan["rows"]
            result = str(rows[0][0]) if rows else "no result"
            st.session_state["database_integrity_result"] = result
            if result.lower() == "ok":
                st.success("SQLite integrity check passed.")
            else:
                st.error(result)
        else:
            st.session_state["database_integrity_result"] = "inconclusive"
            st.warning(f"SQLite integrity check inconclusive: {scan['error']}")

    if right.button(
        "Load table counts",
        width="stretch",
        help="Counts can scan large tables, so Rank Hunter only runs them on request.",
    ):
        with st.spinner("Counting database rows…"):
            st.session_state["database_table_counts"] = {
                name: int(
                    db.execute(f"SELECT COUNT(*) n FROM {name}").fetchone()["n"]
                )
                for name in _TABLES
            }

    counts = st.session_state.get("database_table_counts")
    if counts:
        a, b, c, d = st.columns(4)
        a.metric("Curves", f"{int(counts['curves']):,}")
        b.metric("Points", f"{int(counts['points']):,}")
        c.metric("Candidate pools", f"{int(counts['candidate_pools']):,}")
        d.metric("Candidates", f"{int(counts['candidates']):,}")
        e, f = st.columns(2)
        e.metric("Lattices", f"{int(counts['mw_lattices']):,}")
        f.metric("Jobs", f"{int(counts['ui_jobs']):,}")
    else:
        st.caption("Table counts are not loaded automatically.")

    st.divider()
    st.subheader("Full Database Verification")
    st.caption(
        "Queue an unbounded read-only integrity + foreign-key verification as "
        "a globally exclusive database-maintenance Job. The Streamlit page "
        "stays responsive; Diagnostics only reads the persisted result."
    )
    latest = load_full_database_verification(ctx.db_path)
    if latest is not None:
        if bool(latest.get("healthy")):
            st.success(
                "Latest full verification passed"
                + (
                    f" · {latest.get('finished_at')}"
                    if latest.get("finished_at")
                    else ""
                )
            )
        else:
            st.error(
                "Latest full verification found database issues"
                + (
                    f" · {latest.get('finished_at')}"
                    if latest.get("finished_at")
                    else ""
                )
            )
        st.caption(
            f"Integrity: {'ok' if latest.get('integrity_ok') else 'issues'} · "
            f"foreign-key violations: "
            f"{int(latest.get('foreign_key_violations') or 0)}"
        )
    else:
        st.caption("No completed Full Database Verification result is stored yet.")

    if st.button(
        "Queue Full Database Verification",
        width="stretch",
        help=(
            "Runs as a durable database-maintenance Job after other owned "
            "Rank Hunter work drains. Terminal-launched Rank Hunter work must "
            "also be stopped before the verification can proceed."
        ),
    ):
        try:
            job_id = enqueue_full_database_verification(
                ctx.db_path,
                ctx.project_root,
            )
        except Exception as exc:
            st.error(f"Could not queue Full Database Verification: {exc}")
        else:
            st.session_state["manage_job_id"] = int(job_id)
            st.success(
                f"Queued Full Database Verification as Job #{int(job_id)}."
            )

    st.divider()
    with st.expander("Legacy repair", expanded=False):
        st.caption(
            "Explicitly detach quartic-search references to curves that no longer "
            "exist. Normal database opening never performs this repair."
        )
        if st.button(
            "Repair orphan quartic references",
            width="stretch",
            help=(
                "Runs only the legacy orphan-reference repair. Quartic searches "
                "are preserved; only invalid curve_id references are cleared."
            ),
        ):
            try:
                with acquire_maintenance_lock(
                    ctx.db_path,
                    db=db,
                    project_root=ctx.project_root,
                    reason="repair orphan quartic references",
                ) as lease:
                    _warn_external_processes(lease.external_processes)
                    repaired = repair_orphan_quartic_curve_refs(db)
            except MaintenanceBusyError as exc:
                st.error(f"Could not enter maintenance mode: {exc}")
            except Exception as exc:
                db.rollback()
                st.error(f"Legacy repair failed: {exc}")
            else:
                if repaired:
                    st.success(
                        f"Detached {int(repaired)} orphan quartic curve reference(s)."
                    )
                else:
                    st.info("No orphan quartic curve references found.")


def _database_import_export(db, ctx):
    if st.button(
        "Create database backup",
        type="primary",
        width="stretch",
    ):
        p = create_backup(db, project_root=ctx.project_root, label="manual")
        st.success(f"Created {p.name}")

    backups = list_backups(ctx.project_root)
    if backups:
        p = st.selectbox(
            "Backup",
            backups,
            format_func=lambda q: f"{q.name} · {q.stat().st_size / 1024 / 1024:.1f} MB",
        )
        size_bytes = int(p.stat().st_size)
        st.code(f"Local backup path: {p.resolve()}")

        if _backup_browser_download_allowed(size_bytes):
            st.download_button(
                "Download selected backup",
                data=_deferred_backup_bytes(p),
                file_name=p.name,
                mime="application/octet-stream",
                width="stretch",
                on_click="ignore",
                help=(
                    "Loaded only when clicked. Browser download is limited to "
                    "128 MB so large research databases are not buffered in "
                    "the Streamlit process."
                ),
            )
        else:
            st.warning(
                "Browser download disabled for this large backup "
                f"({size_bytes / 1024 / 1024:.1f} MB). "
                "Use the local backup path shown above to copy or archive it "
                "without buffering the database in Streamlit memory."
            )

        try:
            meta = backup_metadata(p)
        except Exception as exc:
            meta = None
            st.warning(f"Could not read backup metadata: {exc}")

        if meta is not None:
            st.caption(
                "Restore source · "
                f"{meta['modified_at']} · "
                f"{int(meta['size_bytes']) / 1024 / 1024:.1f} MB · "
                f"schema user_version={meta['user_version']}"
            )

        active = _active(db)
        if active:
            st.warning(
                "Restore is disabled while Rank Hunter has queued/running/stopping jobs."
            )

        confirm = st.text_input(
            "Type the backup filename to confirm restore",
            value="",
            key=f"database-restore-confirm-{p.name}",
            placeholder=p.name,
            help=(
                "Restoring replaces the live Rank Hunter database. "
                "Rank Hunter will create a pre-restore safety backup first."
            ),
        )
        confirmed = _restore_confirmed(p.name, confirm)

        if st.button(
            "Restore selected backup",
            width="stretch",
            disabled=bool(active) or not confirmed,
            help=(
                "Type the exact selected backup filename above to enable restore."
                if not confirmed
                else None
            ),
        ):
            try:
                with acquire_maintenance_lock(
                    ctx.db_path,
                    db=db,
                    project_root=ctx.project_root,
                    reason=f"restore {p.name}",
                ) as lease:
                    _warn_external_processes(lease.external_processes)
                    pre = restore_backup(
                        db,
                        p,
                        project_root=ctx.project_root,
                    )
            except MaintenanceBusyError as exc:
                st.error(f"Could not enter maintenance mode: {exc}")
            except Exception as exc:
                st.error(f"Restore failed: {exc}")
            else:
                st.success(
                    f"Restored {p.name}; safety backup {pre.name}"
                )
                st.rerun()
    else:
        st.info("No database backups yet.")

    upload = st.file_uploader(
        "Import SQLite backup",
        type=["db", "sqlite", "sqlite3"],
    )
    if upload and st.button("Stage imported backup", width="stretch"):
        p = import_backup_bytes(
            upload.getvalue(),
            upload.name,
            project_root=ctx.project_root,
        )
        st.success(f"Staged {p.name}")


def _database_advanced_sql(db, ctx):
    write = st.checkbox(
        "Enable write SQL",
        value=False,
        help="Write mode creates a safety backup before the first statement.",
    )
    sql = st.text_area(
        "SQL",
        value=(
            "SELECT id, family, parameter, exact_rank, descent_lower "
            "FROM curves ORDER BY id DESC LIMIT 20;"
        ),
        height=180,
    )
    if not st.button("Execute SQL", type="primary", width="stretch"):
        return

    readonly = _sql_is_readonly(sql)
    if not readonly and not write:
        st.error("Write SQL is disabled.")
        return

    if readonly:
        try:
            cur = db.execute(sql)
            if cur.description:
                cols = [desc[0] for desc in cur.description]
                st.dataframe(
                    [dict(zip(cols, row)) for row in cur.fetchall()],
                    width="stretch",
                )
            else:
                db.commit()
                st.success(f"Executed. Rows affected: {cur.rowcount}")
        except Exception as exc:
            db.rollback()
            st.error(str(exc))
        return

    try:
        with acquire_maintenance_lock(
            ctx.db_path,
            db=db,
            project_root=ctx.project_root,
            reason="Advanced SQL write",
        ) as lease:
            _warn_external_processes(lease.external_processes)
            safety = create_backup(
                db,
                project_root=ctx.project_root,
                label="pre-sql",
            )
            st.info(f"Safety backup: {safety.name}")
            cur = db.execute(sql)
            if cur.description:
                cols = [desc[0] for desc in cur.description]
                st.dataframe(
                    [dict(zip(cols, row)) for row in cur.fetchall()],
                    width="stretch",
                )
            else:
                db.commit()
                st.success(f"Executed. Rows affected: {cur.rowcount}")
    except MaintenanceBusyError as exc:
        db.rollback()
        st.error(f"Could not enter maintenance mode: {exc}")
    except Exception as exc:
        db.rollback()
        st.error(str(exc))


def page(db, ctx):
    title(
        "Database",
        "Health, backups, import/export, and deliberate low-level access to Rank Hunter state.",
        "System",
    )
    render_feature_hook(db, ctx, "database.after_header")
    _render_maintenance_state(ctx)

    current = str(st.session_state.get("database_tab") or "Overview")
    choice = tabs(
        ["Overview", "Import / Export", "Advanced SQL"],
        value=(
            current
            if current in {"Overview", "Import / Export", "Advanced SQL"}
            else "Overview"
        ),
        key="database-main-tabs",
    )
    st.session_state["database_tab"] = choice

    if choice == "Import / Export":
        _database_import_export(db, ctx)
    elif choice == "Advanced SQL":
        _database_advanced_sql(db, ctx)
    else:
        _database_overview(db, ctx)
