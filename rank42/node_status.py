"""Read-only status for the machine currently running Rank Hunter.

The local dispatcher and its jobs use the controller's SQLite database.  A
remote worker must have a separate, authenticated job/result protocol before
it can be registered as an execution node.
"""

from __future__ import annotations

import os
import platform
import socket

from rank42.dispatcher_service import runtime_heartbeat_status
from rank42.settings_registry import setting_value
from rank42.system_metrics import collect_system_metrics


def local_node_snapshot(db, project_root, db_path):
    """Describe the real controller/worker without making network calls."""
    heartbeat = runtime_heartbeat_status(project_root, db_path)
    metrics = {row["key"]: row for row in collect_system_metrics()}
    counts = {
        str(row["status"]): int(row["n"])
        for row in db.execute(
            "SELECT status, COUNT(*) AS n FROM ui_jobs "
            "WHERE status IN ('queued', 'running', 'stopping') GROUP BY status"
        ).fetchall()
    }
    active_jobs = [
        {"id": int(row["id"]), "label": str(row["label"] or row["kind"] or "Job"),
         "status": str(row["status"]), "started_at": row["started_at"]}
        for row in db.execute(
            "SELECT id, label, kind, status, started_at FROM ui_jobs "
            "WHERE status IN ('running', 'stopping') ORDER BY started_at DESC LIMIT 12"
        ).fetchall()
    ]
    try:
        configured_slots = max(1, int(setting_value(db, "queue_max_workers", 2)))
    except (TypeError, ValueError):
        configured_slots = 2
    slots = heartbeat.get("max_workers") if heartbeat.get("fresh") else configured_slots
    try:
        slots = max(1, int(slots))
    except (TypeError, ValueError):
        slots = configured_slots
    return {
        "id": "local",
        "hostname": socket.gethostname() or platform.node() or "This computer",
        "role": "Controller + worker",
        "platform": "WSL 2 · " + str(os.environ["WSL_DISTRO_NAME"])
        if os.environ.get("WSL_DISTRO_NAME") else platform.system(),
        "cpu_cores": os.cpu_count(),
        "dispatcher_online": bool(heartbeat.get("fresh")),
        "dispatcher_age_seconds": heartbeat.get("age_seconds"),
        "worker_slots": slots,
        "running": counts.get("running", 0) + counts.get("stopping", 0),
        "queued": counts.get("queued", 0),
        "metrics": metrics,
        "active_jobs": active_jobs,
        "database": str(db_path),
    }
