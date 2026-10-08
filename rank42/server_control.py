"""Core-owned lifecycle controls for the Rank Hunter Streamlit server."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _launcher_script(project_root: str | Path) -> Path:
    root = Path(project_root).resolve()
    script = root / "scripts" / "run-ui.sh"
    if not script.is_file():
        raise FileNotFoundError(f"Rank Hunter UI launcher not found: {script}")
    return script


def restart_server(
    project_root: str | Path,
    db_path: str | Path,
    *,
    runtime_python: str | Path | None = None,
    delay_seconds: float = 0.1,
    shutdown_timeout_seconds: float = 10.0,
) -> int:
    """Terminate this server, wait for it to exit, then launch one replacement.

    The detached helper owns the handoff.  It signals the current Streamlit
    process, waits until that exact PID is gone, and only then execs the
    canonical launcher.  This prevents Streamlit from selecting a second port
    while the old listener is still alive.
    """
    root = Path(project_root).resolve()
    script = _launcher_script(root)
    database = Path(db_path).resolve()
    runtime = Path(runtime_python or sys.executable).resolve()
    current_pid = os.getpid()

    env = os.environ.copy()
    env["RANK_HUNTER_DB"] = str(database)
    env["RANK_HUNTER_RUNTIME_PYTHON"] = str(runtime)

    poll = max(0.05, float(delay_seconds))
    timeout = max(poll, float(shutdown_timeout_seconds))
    max_checks = max(1, int(timeout / poll))

    helper = (
        'old_pid="$1"; poll="$2"; max_checks="$3"; launcher="$4"; '
        'kill -TERM "$old_pid" 2>/dev/null || true; '
        'checks=0; '
        'while kill -0 "$old_pid" 2>/dev/null && (( checks < max_checks )); do '
        'sleep "$poll"; checks=$((checks + 1)); '
        'done; '
        'if kill -0 "$old_pid" 2>/dev/null; then '
        'kill -KILL "$old_pid" 2>/dev/null || true; '
        'fi; '
        'while kill -0 "$old_pid" 2>/dev/null; do sleep "$poll"; done; '
        'exec /bin/bash "$launcher"'
    )

    child = subprocess.Popen(
        [
            "/bin/bash",
            "-c",
            helper,
            "rank-hunter-restart",
            str(current_pid),
            f"{poll:g}",
            str(max_checks),
            str(script),
        ],
        cwd=str(root),
        env=env,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    return int(child.pid)


def shutdown_server(
    *,
    delay_seconds: float = 0.1,
    shutdown_timeout_seconds: float = 2.0,
) -> int:
    """Terminate this server and guarantee bounded process completion.

    Streamlit handles SIGTERM gracefully, but a Sage-backed process can remain
    alive after the listener stops.  A detached watchdog requests graceful
    shutdown first, waits for this exact PID, and escalates to SIGKILL only
    when the bounded timeout expires.  It never launches a replacement.
    """
    current_pid = os.getpid()
    poll = max(0.05, float(delay_seconds))
    timeout = max(poll, float(shutdown_timeout_seconds))
    max_checks = max(1, int(timeout / poll))

    helper = (
        'old_pid="$1"; poll="$2"; max_checks="$3"; '
        'kill -TERM "$old_pid" 2>/dev/null || exit 0; '
        'checks=0; '
        'while kill -0 "$old_pid" 2>/dev/null && (( checks < max_checks )); do '
        'sleep "$poll"; checks=$((checks + 1)); '
        'done; '
        'if kill -0 "$old_pid" 2>/dev/null; then '
        'kill -KILL "$old_pid" 2>/dev/null || true; '
        'fi; '
        'while kill -0 "$old_pid" 2>/dev/null; do sleep "$poll"; done'
    )

    child = subprocess.Popen(
        [
            "/bin/bash",
            "-c",
            helper,
            "rank-hunter-shutdown",
            str(current_pid),
            f"{poll:g}",
            str(max_checks),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    return int(child.pid)
