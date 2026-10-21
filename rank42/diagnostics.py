"""Read-only release diagnostics for Rank Hunter.

Diagnostics reports what the running checkout can actually see. It does not
repair state, migrate databases, change settings, or validate mathematical
claims beyond consistency checks on already-persisted rigorous evidence.
"""
from __future__ import annotations

import ast
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from rank42 import __version__
from rank42.corpus import connect_corpus, corpus_path, corpus_stats, corpus_status
from rank42.database_checks import pragma_rows_with_budget
from rank42.database_verification import load_full_database_verification
from rank42.dispatcher_service import service_path as dispatcher_service_path
from rank42.curve_research_state import list_curve_research_states
from rank42.plugins import Plugin, discover_plugins, plugin_roots
from rank42.runtime_validation import probe_runtime_setting
from rank42.schema_manifest import (
    CORE_SCHEMA_MANIFEST,
    MANAGE_SCHEMA_MANIFEST,
    UI_SCHEMA_MANIFEST,
    inspect_schema,
    schema_issue_summary,
)
from rank42.settings_registry import setting_value
from rank42.settings_registry import resolve_setting
from rank42.ui_store import (
    DEFAULT_RESOURCE_LIMITS,
    dispatcher_status,
    process_alive,
    resource_limits,
)


STATUSES = ("pass", "warn", "fail")


def _check(checks, check_id, area, label, status, value="", detail=""):
    status = str(status).lower()
    if status not in STATUSES:
        status = "warn"
    checks.append({
        "id": str(check_id),
        "area": str(area),
        "label": str(label),
        "status": status,
        "value": "" if value is None else str(value),
        "detail": "" if detail is None else str(detail),
    })


def _table_exists(db, name):
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (str(name),),
    ).fetchone() is not None


def _safe_run(command, *, cwd=None, timeout=3):
    try:
        cp = subprocess.run(
            [str(x) for x in command],
            cwd=None if cwd is None else str(cwd),
            check=False,
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout)),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": str(exc)}
    return {
        "ok": cp.returncode == 0,
        "returncode": int(cp.returncode),
        "stdout": (cp.stdout or "").strip(),
        "stderr": (cp.stderr or "").strip(),
    }


def _git_root_hint(path):
    path = Path(path).resolve()
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            return candidate
    return path


def _git_snapshot(path):
    path = Path(path).resolve()
    top = _safe_run(["git", "-C", path, "rev-parse", "--show-toplevel"])
    if not top["ok"]:
        return {"available": False, "error": top["stderr"] or top["stdout"]}
    root = Path(top["stdout"]).resolve()
    branch = _safe_run(["git", "-C", root, "branch", "--show-current"])
    commit = _safe_run(["git", "-C", root, "rev-parse", "HEAD"])
    dirty = _safe_run(["git", "-C", root, "status", "--porcelain"])
    dirty_lines = [line for line in (dirty["stdout"] or "").splitlines() if line.strip()] if dirty["ok"] else []
    return {
        "available": True,
        "root": str(root),
        "branch": branch["stdout"] if branch["ok"] else "",
        "commit": commit["stdout"] if commit["ok"] else "",
        "short_commit": commit["stdout"][:12] if commit["ok"] else "",
        "dirty": bool(dirty_lines) if dirty["ok"] else None,
        "dirty_files": dirty_lines[:100],
    }


def _setting(db, key, default=None):
    if not _table_exists(db, "ui_settings"):
        return default
    return setting_value(db, key, default)


def _runtime_checks(db, project_root, checks, *, deep=False):
    runtime = {
        "python": sys.executable,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
    }
    _check(
        checks, "runtime.python", "Runtime", "UI Python", "pass",
        f"{platform.python_version()} · {sys.executable}",
    )

    science_setting = _setting(db, "science_python", sys.executable)
    science_probe = probe_runtime_setting(
        "science_python",
        science_setting,
        run_probe=bool(deep),
        timeout=15 if deep else 5,
    )
    science = science_probe.resolved
    runtime["science_python_configured"] = str(science_setting or "")
    runtime["science_python"] = science
    runtime["science_python_probe"] = science_probe.as_dict()
    science_value = science or str(science_setting or "not configured")
    if science_probe.version:
        science_value = f"{science_value} · {science_probe.version}"
    _check(
        checks,
        "runtime.science_python",
        "Runtime",
        "Science Python / Sage",
        science_probe.status,
        science_value,
        science_probe.detail,
    )

    for key, label in (
        ("ratpoints", "ratpoints CPU"),
        ("ratpoints_gpu", "ratpoints GPU"),
    ):
        configured = _setting(db, key, "")
        probe = probe_runtime_setting(
            key,
            configured,
            run_probe=bool(deep),
            timeout=5,
        )
        runtime[key] = probe.resolved
        runtime[f"{key}_probe"] = probe.as_dict()
        value = probe.resolved or str(configured or "not configured")
        if probe.version:
            value = f"{value} · {probe.version}"
        status = probe.status
        detail = probe.detail
        if key == "ratpoints" and not str(configured or "").strip() and not probe.healthy:
            # A raw/test database can predate shell bootstrap. Keep historical
            # Diagnostics behavior for "not configured" while still failing a
            # persisted invalid CPU path and preventing such saves in Settings.
            status = "warn"
            detail = (
                "CPU ratpoints is not configured in this database. "
                "Settings requires a successful probe before saving it."
            )
        _check(
            checks,
            f"runtime.{key}",
            "Runtime",
            label,
            status,
            value,
            detail,
        )

    nvidia = shutil.which("nvidia-smi")
    gpu = {"nvidia_smi": nvidia}
    if nvidia:
        probe = _safe_run([
            nvidia,
            "--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total",
            "--format=csv,noheader,nounits",
        ], timeout=3)
        gpu["probe"] = probe
        if probe["ok"] and probe["stdout"]:
            _check(checks, "runtime.gpu", "Runtime", "NVIDIA GPU", "pass", probe["stdout"])
        else:
            _check(
                checks, "runtime.gpu", "Runtime", "NVIDIA GPU", "warn",
                "nvidia-smi present but probe failed", probe["stderr"],
            )
    else:
        status = "warn" if runtime.get("ratpoints_gpu") else "pass"
        _check(
            checks, "runtime.gpu", "Runtime", "NVIDIA GPU",
            status, "not detected",
            "GPU is optional unless ratpoints-gpu is configured." if status == "pass"
            else "ratpoints-gpu is configured but nvidia-smi was not found.",
        )
    runtime["gpu"] = gpu

    if deep:
        runtime["sage_probe"] = science_probe.as_dict()
        _check(
            checks,
            "runtime.sage_import",
            "Runtime",
            "Sage import",
            science_probe.status,
            science_probe.version or science_probe.resolved or "failed",
            science_probe.detail,
        )
    return runtime


# Compatibility alias for existing diagnostics tests/callers. The public
# helper now lives in rank42.database_checks so Diagnostics and Database share
# one bounded implementation.
_pragma_rows_with_budget = pragma_rows_with_budget

def _database_checks(db, db_path, checks, *, deep=False):
    info = {
        "deep_checks_run": bool(deep),
        "integrity": None,
        "foreign_key_violations": None,
        "foreign_key_breakdown": {},
        "foreign_key_samples": [],
    }

    if deep:
        integrity_result = _pragma_rows_with_budget(
            db, "PRAGMA integrity_check", timeout_seconds=8
        )
        if integrity_result["completed"]:
            integrity_rows = integrity_result["rows"]
            integrity = (
                str(integrity_rows[0][0])
                if integrity_rows
                else "no result"
            )
            info["integrity"] = integrity
            _check(
                checks, "db.integrity", "Database", "SQLite integrity",
                "pass" if integrity.lower() == "ok" else "fail", integrity,
            )
        else:
            info["integrity"] = "inconclusive"
            _check(
                checks, "db.integrity", "Database", "SQLite integrity",
                "warn", "inconclusive",
                integrity_result["error"],
            )

        fk_result = _pragma_rows_with_budget(
            db, "PRAGMA foreign_key_check", timeout_seconds=8
        )
        if fk_result["completed"]:
            fk_rows = fk_result["rows"]
            fk_breakdown = {}
            fk_samples = []
            for row in fk_rows:
                # SQLite columns are: table, rowid, parent, fkid.
                table = str(row[0])
                parent = str(row[2])
                key = f"{table} -> {parent}"
                fk_breakdown[key] = fk_breakdown.get(key, 0) + 1
                if len(fk_samples) < 50:
                    fk_samples.append({
                        "table": table,
                        "rowid": row[1],
                        "parent": parent,
                        "fkid": row[3],
                    })
            info["foreign_key_violations"] = len(fk_rows)
            info["foreign_key_breakdown"] = dict(
                sorted(
                    fk_breakdown.items(),
                    key=lambda item: (-item[1], item[0]),
                )
            )
            info["foreign_key_samples"] = fk_samples
            detail = ""
            if fk_breakdown:
                detail = "; ".join(
                    f"{name}: {count}"
                    for name, count in list(
                        info["foreign_key_breakdown"].items()
                    )[:8]
                )
            _check(
                checks,
                "db.foreign_key_check",
                "Database",
                "Foreign-key consistency",
                "pass" if not fk_rows else "fail",
                f"{len(fk_rows)} violation(s)",
                detail
                or (
                    ""
                    if not fk_rows
                    else "PRAGMA foreign_key_check returned inconsistent rows."
                ),
            )
        else:
            _check(
                checks,
                "db.foreign_key_check",
                "Database",
                "Foreign-key consistency",
                "warn",
                "inconclusive",
                fk_result["error"],
            )

    full_verification = load_full_database_verification(db_path)
    info["full_verification"] = full_verification
    if full_verification is not None:
        completed = str(full_verification.get("status") or "") == "completed"
        healthy = bool(full_verification.get("healthy")) if completed else False
        status = "pass" if completed and healthy else (
            "fail" if completed else "warn"
        )
        value = (
            "healthy"
            if completed and healthy
            else ("issues found" if completed else "inconclusive")
        )
        job_id = full_verification.get("job_id")
        finished = full_verification.get("finished_at")
        fk_count = full_verification.get("foreign_key_violations")
        detail_bits = []
        if job_id is not None:
            detail_bits.append(f"job #{int(job_id)}")
        if finished:
            detail_bits.append(str(finished))
        if fk_count is not None:
            detail_bits.append(f"{int(fk_count)} foreign-key violation(s)")
        _check(
            checks,
            "db.full_verification",
            "Database",
            "Latest Full Database Verification",
            status,
            value,
            " · ".join(detail_bits),
        )

    fk_enabled = bool(int(db.execute("PRAGMA foreign_keys").fetchone()[0] or 0))
    info["foreign_keys_enabled"] = fk_enabled
    _check(
        checks, "db.foreign_keys", "Database", "Foreign-key enforcement",
        "pass" if fk_enabled else "warn",
        "ON" if fk_enabled else "OFF",
        (
            ""
            if fk_enabled
            else "This connection is not enforcing new foreign-key writes."
        ),
    )

    journal = str(db.execute("PRAGMA journal_mode").fetchone()[0])
    schema_statuses = {
        manifest.name: inspect_schema(db, manifest)
        for manifest in (
            CORE_SCHEMA_MANIFEST,
            UI_SCHEMA_MANIFEST,
            MANAGE_SCHEMA_MANIFEST,
        )
    }
    core_schema = schema_statuses["core"]
    user_version = int(core_schema["actual_user_version"])
    info.update({
        "journal_mode": journal,
        "user_version": user_version,
        "schema": schema_statuses,
    })
    _check(
        checks, "db.journal", "Database", "Journal mode",
        "pass" if journal.lower() == "wal" else "warn", journal,
        "" if journal.lower() == "wal" else "WAL is usually safer for concurrent readers/workers; current mode is reported, not changed.",
    )

    for name, label in (
        ("core", "Core schema"),
        ("ui", "UI / Jobs schema"),
        ("manage", "Manage schema"),
    ):
        status = schema_statuses[name]
        if status["expected_user_version"] is None:
            value = "ready" if status["ready"] else "incomplete"
        else:
            value = (
                f"user_version={status['actual_user_version']} "
                f"(expected {status['expected_user_version']})"
            )
        _check(
            checks,
            f"db.schema.{name}",
            "Database",
            label,
            "pass" if status["ready"] else "fail",
            value,
            "" if status["ready"] else schema_issue_summary(status),
        )

    # Compatibility check id retained for report/test consumers while its
    # semantics now use the authoritative exact core readiness contract.
    _check(
        checks,
        "db.schema",
        "Database",
        "Schema user_version",
        "pass" if core_schema["ready"] else "fail",
        f"{user_version} / expected {CORE_SCHEMA_MANIFEST.expected_user_version}",
        "" if core_schema["ready"] else schema_issue_summary(core_schema),
    )

    path = Path(db_path)
    size = path.stat().st_size if path.is_file() else 0
    info["path"] = str(path)
    info["size_bytes"] = size
    _check(
        checks, "db.file", "Database", "Database file",
        "pass" if path.is_file() else "fail",
        f"{size / 1024 / 1024:.1f} MB · {path}" if path.is_file() else str(path),
    )
    return info


def _compatibility_rank_projection(curve):
    lower_values = [
        int(curve[key])
        for key in ("exact_rank", "descent_lower", "generic_lower")
        if curve.get(key) is not None
    ]
    lower = max(lower_values) if lower_values else 0
    exact = int(curve["exact_rank"]) if curve.get("exact_rank") is not None else None
    upper = exact
    if upper is None and curve.get("descent_upper") is not None:
        upper = int(curve["descent_upper"])
    return {
        "rigorous_lower": lower,
        "rigorous_upper": upper,
        "exact_rank": exact,
    }


def _evidence_checks(db, checks):
    result = {}

    states = list_curve_research_states(db)
    reduced_conflicts = sum(bool(state["rank_inconsistent"]) for state in states)
    result["reduced_rank_conflicts"] = int(reduced_conflicts)
    # Preserve legacy result keys for reports/consumers while deriving their
    # values from the same authoritative reduced conflict set.
    result["curve_exact_lower_conflicts"] = int(reduced_conflicts)
    result["curve_exact_upper_conflicts"] = 0
    _check(
        checks, "evidence.curve_bounds", "Evidence", "Curve rank-bound consistency",
        "pass" if reduced_conflicts == 0 else "fail",
        f"{reduced_conflicts} conflict(s)",
        "Reduced rigorous lower/upper evidence must define a consistent rank interval.",
    )

    projection_mismatches = 0
    for state in states:
        legacy = _compatibility_rank_projection(state["curve"])
        if (
            int(legacy["rigorous_lower"]) != int(state["rigorous_lower"])
            or legacy["rigorous_upper"] != state["rigorous_upper"]
            or legacy["exact_rank"] != state["exact_rank"]
        ):
            projection_mismatches += 1
    result["rank_projection_mismatches"] = int(projection_mismatches)
    _check(
        checks, "evidence.rank_projection", "Evidence", "Compatibility rank projection",
        "pass" if projection_mismatches == 0 else "warn",
        f"{projection_mismatches} mismatch(es)",
        (
            ""
            if projection_mismatches == 0
            else "Structured/reduced rank evidence is authoritative; compatibility curve columns lag on these rows."
        ),
    )

    witness_counts = {
        int(row["curve_id"]): int(row["n"] or 0)
        for row in db.execute(
            """SELECT curve_id, COUNT(*) n
               FROM points
               WHERE exact_verified=1
                 AND rigorous_independent<>0
                 AND independence_status='rigorous_independent'
               GROUP BY curve_id"""
        ).fetchall()
    }
    witness_shortages = []
    missing_witnesses = 0
    for state in states:
        required = int(state["rigorous_lower"] or 0)
        available = int(witness_counts.get(int(state["curve_id"]), 0))
        if available < required:
            witness_shortages.append(int(state["curve_id"]))
            missing_witnesses += required - available
    result["witness_basis_shortages"] = len(witness_shortages)
    result["witness_basis_missing_points"] = int(missing_witnesses)
    _check(
        checks, "evidence.witness_basis", "Evidence", "Rigorous witness-basis sufficiency",
        "pass" if not witness_shortages else "fail",
        (
            "complete"
            if not witness_shortages
            else f"{len(witness_shortages)} curve(s) short · {missing_witnesses} witness(es) missing"
        ),
        (
            ""
            if not witness_shortages
            else "Durable exact rigorous-independent witnesses must be sufficient to replay each authoritative reduced lower bound."
        ),
    )

    bad_witnesses = int(db.execute(
        """SELECT COUNT(*) n
           FROM points
           WHERE (
               rigorous_independent<>0
               AND (
                   exact_verified=0
                   OR independence_status<>'rigorous_independent'
               )
           )
           OR (
               role='rigorous_witness'
               AND (
                   exact_verified=0
                   OR rigorous_independent=0
                   OR independence_status<>'rigorous_independent'
               )
           )"""
    ).fetchone()["n"] or 0)
    result["invalid_rigorous_points"] = bad_witnesses
    _check(
        checks, "evidence.points", "Evidence", "Rigorous point ledger",
        "pass" if bad_witnesses == 0 else "fail",
        f"{bad_witnesses} invalid rigorous witness row(s)",
    )

    if _table_exists(db, "rank_evidence"):
        inverted = int(db.execute(
            "SELECT COUNT(*) n FROM rank_evidence WHERE rigorous<>0 "
            "AND rigorous_lower IS NOT NULL AND rigorous_upper IS NOT NULL "
            "AND rigorous_lower > rigorous_upper"
        ).fetchone()["n"] or 0)
        exact_mismatch = int(db.execute(
            "SELECT COUNT(*) n FROM rank_evidence WHERE exact_rank IS NOT NULL AND "
            "((rigorous_lower IS NOT NULL AND rigorous_lower<>exact_rank) OR "
            "(rigorous_upper IS NOT NULL AND rigorous_upper<>exact_rank))"
        ).fetchone()["n"] or 0)
        result["rank_evidence_inverted_intervals"] = inverted
        result["rank_evidence_exact_mismatches"] = exact_mismatch
        _check(
            checks, "evidence.ledger", "Evidence", "Rank-evidence ledger",
            "pass" if inverted + exact_mismatch == 0 else "fail",
            f"{inverted + exact_mismatch} conflict(s)",
        )
    return result


def _plugin_and_corpus_checks(project_root, checks):
    roots = [Path(x) for x in plugin_roots(project_root)]
    plugin_rows = []
    corpus_rows = []
    raw = discover_plugins(project_root, include_superseded=True)
    valid = [x for x in raw if isinstance(x, Plugin)]
    invalid = [x for x in raw if not isinstance(x, Plugin)]

    for root in roots:
        _check(
            checks, f"plugins.root.{root}", "Plugins", "Runtime plugin root",
            "pass" if root.is_dir() else "warn", root,
            "" if root.is_dir() else "Plugin discovery root does not exist.",
        )

    _check(
        checks, "plugins.registry", "Plugins", "Plugin registry",
        "pass" if not invalid else "fail",
        f"{len(valid)} valid · {len(invalid)} invalid",
        "; ".join(str(x.get("error") or x) for x in invalid[:5]) if invalid else "",
    )

    git_cache = {}
    for plugin in valid:
        root = Path(plugin.root)
        resolved = root.resolve()
        git_hint = _git_root_hint(resolved)
        git_key = str(git_hint)
        if git_key not in git_cache:
            git_cache[git_key] = _git_snapshot(git_hint)
        snap = git_cache[git_key]
        research_hooks = {}
        adapter_error = None
        if plugin.plugin_type == "family" and plugin.adapter_path is not None:
            try:
                tree = ast.parse(
                    Path(plugin.adapter_path).read_text(encoding="utf-8"),
                    filename=str(plugin.adapter_path),
                )
                declared = {
                    node.name
                    for node in tree.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
                research_hooks = {
                    name: name in declared
                    for name in (
                        "derive_pipeline_coverings",
                        "run_pipeline_higher_descent",
                        "run_pipeline_padic_covering_search",
                    )
                }
            except Exception as exc:
                adapter_error = repr(exc)
                _check(
                    checks,
                    f"plugins.adapter.{plugin.id}",
                    "Plugins",
                    f"{plugin.name} adapter source",
                    "fail",
                    "adapter source invalid",
                    adapter_error,
                )
        plugin_rows.append({
            "id": plugin.id,
            "name": plugin.name,
            "version": plugin.version,
            "type": plugin.plugin_type,
            "root": str(root),
            "resolved_root": str(resolved),
            "symlinked": str(root) != str(resolved),
            "git_branch": snap.get("branch", ""),
            "git_commit": snap.get("short_commit", ""),
            "git_dirty": snap.get("dirty"),
            "research_hooks": research_hooks,
            "adapter_error": adapter_error,
        })
        for corpus in plugin.corpora:
            status = corpus_status(project_root, plugin, corpus)
            rec = dict(status)
            path = Path(status["path"])
            rec["size_bytes"] = path.stat().st_size if path.is_file() else 0
            rec["curves"] = None
            rec["observations"] = None
            if status["ready"]:
                try:
                    cdb = connect_corpus(project_root, plugin, corpus)
                    try:
                        stats = corpus_stats(cdb)
                    finally:
                        cdb.close()
                    rec.update({
                        "curves": stats["curves"],
                        "observations": stats["observations"],
                        "max_exact_rank": stats["max_exact_rank"],
                    })
                except Exception as exc:
                    rec["ready"] = False
                    rec["error"] = str(exc)
            corpus_rows.append(rec)

    if corpus_rows:
        ready = sum(1 for x in corpus_rows if x["ready"])
        invalid_corpora = sum(1 for x in corpus_rows if x["exists"] and not x["ready"])
        missing = sum(1 for x in corpus_rows if not x["exists"])
        status = "fail" if invalid_corpora else ("warn" if missing else "pass")
        _check(
            checks, "corpora.registry", "Corpora", "Declared corpora",
            status,
            f"{ready} ready · {missing} missing · {invalid_corpora} invalid",
            "Missing corpora can be built from the Corpora page when a builder is declared." if missing else "",
        )
    else:
        _check(
            checks, "corpora.registry", "Corpora", "Declared corpora",
            "warn", "none declared",
            "No installed runtime plugin currently declares a corpus.",
        )
    return plugin_rows, corpus_rows


def _state_checks(db, checks):
    state = {}
    if _table_exists(db, "ui_jobs"):
        rows = db.execute(
            "SELECT id,kind,label,pid,status,metadata_json,updated_at FROM ui_jobs "
            "WHERE status IN ('queued','running','stopping') ORDER BY id DESC"
        ).fetchall()
        stale = []
        active = []
        auto_campaign_ids = set()
        for row in rows:
            rec = dict(row)
            active.append(rec)
            if row["status"] in {"running", "stopping"} and (not row["pid"] or not process_alive(row["pid"])):
                stale.append(rec)
            if row["kind"] == "auto_search":
                try:
                    meta = json.loads(row["metadata_json"] or "{}")
                except Exception:
                    meta = {}
                if meta.get("campaign_id") is not None and row["pid"] and process_alive(row["pid"]):
                    auto_campaign_ids.add(int(meta["campaign_id"]))
        state["active_jobs"] = active
        state["stale_jobs"] = stale
        _check(
            checks, "state.jobs", "State", "Background jobs",
            "pass" if not stale else "warn",
            f"{len(active)} active · {len(stale)} stale",
            "A stale job is stored as running/stopping but its recorded process is gone." if stale else "",
        )
    else:
        auto_campaign_ids = set()

    if _table_exists(db, "auto_search_campaigns"):
        running = [
            dict(row) for row in db.execute(
                "SELECT id,plugin_id,variant_id,status,updated_at FROM auto_search_campaigns "
                "WHERE status='running' ORDER BY id DESC"
            ).fetchall()
        ]
        stale_campaigns = [x for x in running if int(x["id"]) not in auto_campaign_ids]
        state["running_campaigns"] = running
        state["stale_campaigns"] = stale_campaigns
        _check(
            checks, "state.campaigns", "State", "Auto Search campaigns",
            "pass" if not stale_campaigns else "warn",
            f"{len(running)} running · {len(stale_campaigns)} without live worker",
            "Diagnostics does not mutate campaign state; Resume/UI reconciliation can repair stale lifecycle rows." if stale_campaigns else "",
        )
    return state


def _operational_checks(db, project_root, db_path, checks):
    """Read-only Queue / Dispatcher / Scheduler release-health snapshot."""
    project_root = Path(project_root).resolve()
    db_path = Path(db_path).resolve()
    current = datetime.now(timezone.utc)
    result = {
        "dispatcher": {},
        "queue": {},
        "scheduler": {},
        "resource_limits": {},
    }

    ui_schema = inspect_schema(db, UI_SCHEMA_MANIFEST)
    manage_schema = inspect_schema(db, MANAGE_SCHEMA_MANIFEST)

    heartbeat = (
        dispatcher_status(db)
        if ui_schema["ready"]
        else {"healthy": False, "status": "UI schema incomplete"}
    )
    unit_path = dispatcher_service_path(project_root, db_path)
    service = {
        "installed": unit_path.is_file(),
        "path": str(unit_path),
        "systemctl_available": bool(shutil.which("systemctl")),
    }
    result["dispatcher"] = {
        "heartbeat": heartbeat,
        "service": service,
    }

    if service["installed"] and service["systemctl_available"]:
        service_status = "pass"
        service_value = "persistent service available"
        service_detail = str(unit_path)
    elif service["installed"]:
        service_status = "warn"
        service_value = "service installed; systemctl unavailable"
        service_detail = str(unit_path)
    elif heartbeat.get("healthy"):
        service_status = "warn"
        service_value = "detached dispatcher fallback"
        service_detail = (
            "Persistent dispatcher service is not installed, but a healthy "
            "dispatcher heartbeat is present."
        )
    else:
        service_status = "warn"
        service_value = "persistent service not installed"
        service_detail = (
            "No persistent dispatcher service is installed. Rank Hunter may "
            "still start a detached dispatcher when work is queued."
        )
    _check(
        checks,
        "ops.dispatcher_service",
        "Operations",
        "Dispatcher service availability",
        service_status,
        service_value,
        service_detail,
    )

    queue_counts = {}
    expired_claims = []
    missing_leases = []
    if ui_schema["ready"]:
        for row in db.execute(
            "SELECT state,COUNT(*) AS n FROM ui_job_queue GROUP BY state"
        ).fetchall():
            queue_counts[str(row["state"])] = int(row["n"] or 0)

        active_claims = db.execute(
            """SELECT job_id,state,lease_owner,lease_expires_at
               FROM ui_job_queue
               WHERE state IN ('claimed','running')
               ORDER BY job_id"""
        ).fetchall()
        for row in active_claims:
            lease = str(row["lease_expires_at"] or "").strip()
            rec = {
                "job_id": int(row["job_id"]),
                "state": str(row["state"]),
                "lease_owner": str(row["lease_owner"] or ""),
                "lease_expires_at": lease,
            }
            if not lease:
                missing_leases.append(rec)
                continue
            try:
                expiry = datetime.fromisoformat(
                    lease.replace("Z", "+00:00")
                )
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                expiry = expiry.astimezone(timezone.utc)
            except Exception:
                missing_leases.append(rec)
                continue
            if expiry <= current:
                expired_claims.append(rec)

        claim_status = (
            "pass"
            if not expired_claims and not missing_leases
            else "fail"
        )
        _check(
            checks,
            "ops.queue_claims",
            "Operations",
            "Queue claim leases",
            claim_status,
            (
                "healthy"
                if claim_status == "pass"
                else (
                    f"{len(expired_claims)} expired · "
                    f"{len(missing_leases)} missing/invalid"
                )
            ),
            (
                ""
                if claim_status == "pass"
                else "Claimed/running queue work must have a live unexpired lease."
            ),
        )

        queued = int(queue_counts.get("queued", 0))
        dispatcher_healthy = bool(heartbeat.get("healthy"))
        if dispatcher_healthy:
            dispatcher_check_status = "pass"
            dispatcher_value = (
                f"healthy · {queued} queued"
            )
        elif queued:
            dispatcher_check_status = "fail"
            dispatcher_value = f"offline/stale · {queued} queued"
        else:
            dispatcher_check_status = "warn"
            dispatcher_value = "offline/stale · no queued work"
        _check(
            checks,
            "ops.dispatcher",
            "Operations",
            "Dispatcher heartbeat",
            dispatcher_check_status,
            dispatcher_value,
            (
                f"PID {heartbeat.get('pid') or '—'} · "
                f"status {heartbeat.get('status') or 'unknown'} · "
                f"updated {heartbeat.get('updated_at') or 'never'}"
            ),
        )
    else:
        _check(
            checks,
            "ops.queue_claims",
            "Operations",
            "Queue claim leases",
            "fail",
            "UI / Jobs schema incomplete",
            schema_issue_summary(ui_schema),
        )
        _check(
            checks,
            "ops.dispatcher",
            "Operations",
            "Dispatcher heartbeat",
            "fail",
            "UI / Jobs schema incomplete",
            schema_issue_summary(ui_schema),
        )

    result["queue"] = {
        "counts": queue_counts,
        "expired_claims": expired_claims,
        "missing_or_invalid_leases": missing_leases,
    }

    _check(
        checks,
        "ops.scheduler_schema",
        "Operations",
        "Scheduler / Manage schema",
        "pass" if manage_schema["ready"] else "fail",
        "ready" if manage_schema["ready"] else "incomplete",
        "" if manage_schema["ready"] else schema_issue_summary(manage_schema),
    )

    scheduler = {
        "enabled_schedules": 0,
        "schedule_errors": 0,
        "failed_occurrences": 0,
        "stale_pending_occurrences": 0,
        "occurrence_errors": 0,
        "broken_active_links": 0,
    }
    if manage_schema["ready"]:
        scheduler["enabled_schedules"] = int(
            db.execute(
                "SELECT COUNT(*) FROM ui_job_schedules WHERE enabled=1"
            ).fetchone()[0]
            or 0
        )
        scheduler["schedule_errors"] = int(
            db.execute(
                """SELECT COUNT(*) FROM ui_job_schedules
                   WHERE last_error IS NOT NULL
                     AND TRIM(last_error)<>''"""
            ).fetchone()[0]
            or 0
        )
        scheduler["failed_occurrences"] = int(
            db.execute(
                """SELECT COUNT(*) FROM ui_job_schedule_occurrences
                   WHERE state IN ('failed','interrupted','killed')"""
            ).fetchone()[0]
            or 0
        )
        scheduler["occurrence_errors"] = int(
            db.execute(
                """SELECT COUNT(*) FROM ui_job_schedule_occurrences
                   WHERE last_error IS NOT NULL
                     AND TRIM(last_error)<>''"""
            ).fetchone()[0]
            or 0
        )
        stale_cutoff = (
            current.timestamp() - 300.0
        )
        stale_pending = 0
        for row in db.execute(
            """SELECT id,scheduled_for
               FROM ui_job_schedule_occurrences
               WHERE state='pending'"""
        ).fetchall():
            try:
                scheduled = datetime.fromisoformat(
                    str(row["scheduled_for"]).replace("Z", "+00:00")
                )
                if scheduled.tzinfo is None:
                    scheduled = scheduled.replace(tzinfo=timezone.utc)
                scheduled = scheduled.astimezone(timezone.utc)
            except Exception:
                stale_pending += 1
                continue
            if scheduled.timestamp() <= stale_cutoff:
                stale_pending += 1
        scheduler["stale_pending_occurrences"] = int(stale_pending)

        if _table_exists(db, "ui_jobs"):
            scheduler["broken_active_links"] = int(
                db.execute(
                    """SELECT COUNT(*)
                       FROM ui_job_schedule_occurrences o
                       LEFT JOIN ui_jobs j ON j.id=o.job_id
                       WHERE o.state IN ('queued','running')
                         AND (
                           o.job_id IS NULL
                           OR j.id IS NULL
                           OR (o.state='queued' AND j.status<>'queued')
                           OR (
                             o.state='running'
                             AND j.status NOT IN ('running','stopping')
                           )
                         )"""
                ).fetchone()[0]
                or 0
            )
        else:
            scheduler["broken_active_links"] = int(
                db.execute(
                    """SELECT COUNT(*)
                       FROM ui_job_schedule_occurrences
                       WHERE state IN ('queued','running')"""
                ).fetchone()[0]
                or 0
            )

        scheduler_issues = sum(
            int(scheduler[key])
            for key in (
                "schedule_errors",
                "failed_occurrences",
                "stale_pending_occurrences",
                "occurrence_errors",
                "broken_active_links",
            )
        )
        _check(
            checks,
            "ops.scheduler_occurrences",
            "Operations",
            "Scheduled Job occurrences",
            "pass" if scheduler_issues == 0 else "fail",
            (
                f"{scheduler['enabled_schedules']} enabled · "
                f"{scheduler_issues} issue(s)"
            ),
            (
                ""
                if scheduler_issues == 0
                else (
                    f"schedule errors={scheduler['schedule_errors']} · "
                    f"failed occurrences={scheduler['failed_occurrences']} · "
                    f"stale pending={scheduler['stale_pending_occurrences']} · "
                    f"occurrence errors={scheduler['occurrence_errors']} · "
                    f"broken active links={scheduler['broken_active_links']}"
                )
            ),
        )
    result["scheduler"] = scheduler

    if _table_exists(db, "ui_settings"):
        max_workers = resolve_setting(db, "queue_max_workers")
        resource_setting = resolve_setting(db, "queue_resource_limits")
        effective_limits = resource_limits(db)
        resource_shape_ok = (
            isinstance(effective_limits, dict)
            and all(
                isinstance(key, str)
                and bool(key)
                and isinstance(value, int)
                and not isinstance(value, bool)
                and int(value) >= 1
                for key, value in effective_limits.items()
            )
            and all(
                key in effective_limits
                for key in DEFAULT_RESOURCE_LIMITS
            )
        )
        settings_ok = bool(
            max_workers.valid
            and resource_setting.valid
            and resource_shape_ok
        )
        errors = [
            str(error)
            for error in (max_workers.error, resource_setting.error)
            if error
        ]
        value = (
            f"max workers={int(max_workers.value)} · "
            + ", ".join(
                f"{key}={int(limit)}"
                for key, limit in sorted(effective_limits.items())
            )
        )
        result["resource_limits"] = {
            "max_workers": max_workers.value,
            "max_workers_valid": bool(max_workers.valid),
            "resource_limits": effective_limits,
            "resource_limits_valid": bool(resource_setting.valid),
            "errors": errors,
        }
    else:
        settings_ok = False
        errors = ["UI settings table is missing"]
        value = "UI settings schema unavailable"
        result["resource_limits"] = {
            "max_workers": None,
            "max_workers_valid": False,
            "resource_limits": {},
            "resource_limits_valid": False,
            "errors": errors,
        }
    _check(
        checks,
        "ops.queue_resources",
        "Operations",
        "Queue resource limits",
        "pass" if settings_ok else "fail",
        value,
        "; ".join(errors),
    )
    return result


def _storage_checks(project_root, db_path, checks):
    root = Path(project_root).resolve()
    usage = shutil.disk_usage(root)
    free_gib = usage.free / (1024 ** 3)
    status = "fail" if free_gib < 5 else ("warn" if free_gib < 15 else "pass")
    _check(
        checks, "storage.free", "Storage", "Free disk space",
        status, f"{free_gib:.1f} GiB free",
        "Long searches, corpora, logs and backups can grow quickly." if status != "pass" else "",
    )

    db_parent = Path(db_path).resolve().parent
    writable = os.access(db_parent, os.W_OK)
    _check(
        checks, "storage.db_writable", "Storage", "Database directory writable",
        "pass" if writable else "fail", db_parent,
    )

    backup_dir = root / "backups"
    backups = sorted(
        (p for p in backup_dir.glob("*.db") if p.is_file()),
        key=lambda p: p.stat().st_mtime_ns,
        reverse=True,
    ) if backup_dir.is_dir() else []
    _check(
        checks, "storage.backups", "Storage", "Managed backups",
        "pass" if backups else "warn",
        f"{len(backups)} backup(s)" + (f" · latest {backups[0].name}" if backups else ""),
        "" if backups else "Create a database backup before release-critical migrations or destructive work.",
    )
    return {
        "total_bytes": usage.total,
        "free_bytes": usage.free,
        "backups": [str(x) for x in backups[:20]],
    }


def _diagnostic_release_status(*, deep, counts, database):
    """Classify release readiness without overstating Fast or bounded Deep work."""
    if int(counts.get("fail") or 0) > 0:
        return {
            "overall": "ATTENTION REQUIRED",
            "verification_mode": "deep" if deep else "fast",
            "deep_checks_complete": False if not deep else bool(
                database.get("integrity") not in {None, "inconclusive"}
                and database.get("foreign_key_violations") is not None
            ),
        }

    if not deep:
        return {
            "overall": "FAST CHECKS READY",
            "verification_mode": "fast",
            "deep_checks_complete": False,
        }

    deep_complete = bool(
        database.get("integrity") not in {None, "inconclusive"}
        and database.get("foreign_key_violations") is not None
    )
    return {
        "overall": (
            "DEEP CHECKS READY"
            if deep_complete
            else "DEEP CHECKS INCONCLUSIVE"
        ),
        "verification_mode": "deep",
        "deep_checks_complete": deep_complete,
    }


def collect_diagnostics(project_root, db_path, db, *, deep=False):
    project_root = Path(project_root).resolve()
    db_path = Path(db_path).resolve()
    checks = []

    git = _git_snapshot(project_root)
    if git.get("available"):
        git_status = "warn" if git.get("dirty") else "pass"
        value = f"{git.get('branch') or '(detached)'} · {git.get('short_commit') or 'unknown'}"
        _check(
            checks, "core.git", "Core", "Core checkout", git_status, value,
            f"{git.get('root')}" + (" · uncommitted changes present" if git.get("dirty") else ""),
        )
    else:
        _check(checks, "core.git", "Core", "Core checkout", "warn", "Git metadata unavailable", git.get("error", ""))

    _check(checks, "core.version", "Core", "Rank Hunter version", "pass", __version__)

    database = _database_checks(db, db_path, checks, deep=deep)
    evidence = _evidence_checks(db, checks)
    runtime = _runtime_checks(db, project_root, checks, deep=deep)
    plugins, corpora = _plugin_and_corpus_checks(project_root, checks)
    state = _state_checks(db, checks)
    operations = _operational_checks(
        db,
        project_root,
        db_path,
        checks,
    )
    storage = _storage_checks(project_root, db_path, checks)

    counts = {
        status: sum(1 for x in checks if x["status"] == status)
        for status in STATUSES
    }
    release_status = _diagnostic_release_status(
        deep=bool(deep),
        counts=counts,
        database=database,
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "deep": bool(deep),
        "overall": release_status["overall"],
        "verification_mode": release_status["verification_mode"],
        "deep_checks_complete": release_status["deep_checks_complete"],
        "counts": counts,
        "checks": checks,
        "core": {"version": __version__, "git": git, "project_root": str(project_root)},
        "database": database,
        "evidence": evidence,
        "runtime": runtime,
        "plugins": plugins,
        "corpora": corpora,
        "state": state,
        "operations": operations,
        "storage": storage,
    }


def format_diagnostic_report(result, *, redact_home=True):
    home = str(Path.home())

    def clean(value):
        text = str(value)
        return text.replace(home, "~") if redact_home and home else text

    lines = [
        "RANK HUNTER DIAGNOSTICS",
        f"generated={result.get('generated_at')}",
        f"overall={result.get('overall')}",
        f"verification_mode={result.get('verification_mode', 'deep' if result.get('deep') else 'fast')}",
        f"deep_checks_complete={bool(result.get('deep_checks_complete'))}",
        f"version={result.get('core', {}).get('version', '')}",
        f"deep_runtime_probes={bool(result.get('deep'))}",
        "",
    ]
    git = result.get("core", {}).get("git") or {}
    if git.get("available"):
        lines.extend([
            f"core_branch={git.get('branch')}",
            f"core_commit={git.get('commit')}",
            f"core_dirty={git.get('dirty')}",
            f"project_root={clean(result.get('core', {}).get('project_root', ''))}",
        ])
        for line in git.get("dirty_files") or []:
            lines.append(f"core_dirty_file={clean(line)}")
        lines.append("")

    for rec in result.get("checks") or []:
        suffix = f" — {clean(rec.get('detail'))}" if rec.get("detail") else ""
        lines.append(
            f"[{str(rec.get('status', '')).upper()}] "
            f"{rec.get('area')}: {rec.get('label')} = {clean(rec.get('value', ''))}{suffix}"
        )

    if result.get("plugins"):
        lines.extend(["", "PLUGINS"])
        for rec in result["plugins"]:
            hooks = rec.get("research_hooks") or {}
            covering_hook = (
                "yes" if hooks.get("derive_pipeline_coverings") else "no"
            )
            lines.append(
                f"{rec['id']} v{rec['version']} [{rec['type']}] "
                f"{clean(rec['root'])} -> {clean(rec['resolved_root'])} "
                f"git={rec.get('git_branch') or '?'}@{rec.get('git_commit') or '?'} "
                f"covering_hook={covering_hook}"
            )

    if result.get("corpora"):
        lines.extend(["", "CORPORA"])
        for rec in result["corpora"]:
            lines.append(
                f"{rec['plugin_id']}/{rec['corpus_id']}: "
                f"{'ready' if rec.get('ready') else ('invalid' if rec.get('exists') else 'missing')} "
                f"path={clean(rec.get('path', ''))} curves={rec.get('curves')}"
            )
    return "\n".join(lines) + "\n"
