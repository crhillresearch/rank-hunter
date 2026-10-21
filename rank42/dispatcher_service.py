"""Persistent user-service management for the Rank Hunter dispatcher."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _resolved(value):
    return str(Path(value).expanduser().resolve())


def service_instance(project_root, db_path):
    key = (_resolved(project_root) + "\n" + _resolved(db_path)).encode("utf-8")
    return hashlib.sha256(key).hexdigest()[:12]


def service_name(project_root, db_path):
    return f"rank-hunter-dispatcher-{service_instance(project_root, db_path)}.service"


def user_unit_dir(home=None):
    base = Path(home).expanduser() if home is not None else Path.home()
    return base / ".config" / "systemd" / "user"


def service_path(project_root, db_path, *, home=None):
    return user_unit_dir(home) / service_name(project_root, db_path)


RUNTIME_HEARTBEAT_PROTOCOL = 1
RUNTIME_HEARTBEAT_FRESH_SECONDS = 15.0


def runtime_heartbeat_path(project_root, db_path):
    root = Path(_resolved(project_root))
    return (
        root
        / ".rank42-ui"
        / f"dispatcher-{service_instance(project_root, db_path)}.heartbeat.json"
    )


def write_runtime_heartbeat(
    project_root,
    db_path,
    *,
    pid,
    worker_id,
    status,
    max_workers=None,
    resource_limits=None,
):
    """Atomically publish dispatcher liveness outside the research database."""
    path = runtime_heartbeat_path(project_root, db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "protocol": RUNTIME_HEARTBEAT_PROTOCOL,
        "pid": int(pid),
        "worker_id": str(worker_id),
        "status": str(status),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if max_workers is not None:
        payload["max_workers"] = int(max_workers)
    if resource_limits is not None:
        payload["resource_limits"] = {
            str(key): int(value)
            for key, value in dict(resource_limits).items()
        }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return payload


def runtime_heartbeat_status(
    project_root,
    db_path,
    *,
    fresh_seconds=RUNTIME_HEARTBEAT_FRESH_SECONDS,
):
    """Read the atomic dispatcher liveness sidecar without touching SQLite."""
    path = runtime_heartbeat_path(project_root, db_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "exists": False,
            "valid": False,
            "fresh": False,
            "path": str(path),
        }
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "exists": True,
            "valid": False,
            "fresh": False,
            "path": str(path),
            "error": str(exc),
        }

    if not isinstance(raw, dict):
        return {
            "exists": True,
            "valid": False,
            "fresh": False,
            "path": str(path),
            "error": "runtime heartbeat payload is not an object",
        }

    updated = raw.get("updated_at")
    age_seconds = None
    try:
        dt = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
        age_seconds = max(
            0.0,
            (
                datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
            ).total_seconds(),
        )
    except Exception:
        age_seconds = None

    try:
        protocol = int(raw.get("protocol") or 0)
    except (TypeError, ValueError):
        protocol = 0
    protocol_ok = protocol == RUNTIME_HEARTBEAT_PROTOCOL
    fresh = bool(
        protocol_ok
        and age_seconds is not None
        and age_seconds <= float(fresh_seconds)
    )
    return {
        **raw,
        "exists": True,
        "valid": bool(protocol_ok and age_seconds is not None),
        "fresh": fresh,
        "age_seconds": age_seconds,
        "path": str(path),
        "protocol_ok": protocol_ok,
    }


def _unit_quote(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def service_unit_text(project_root, db_path, *, python_executable=None):
    root = _resolved(project_root)
    database = _resolved(db_path)
    python = _resolved(python_executable or sys.executable)
    return "\n".join(
        [
            "[Unit]",
            "Description=Rank Hunter durable queue dispatcher",
            "After=default.target",
            "",
            "[Service]",
            "Type=simple",
            f"WorkingDirectory={root}",
            (
                "ExecStart="
                f"{_unit_quote(python)} -m rank42.dispatcher "
                f"--db {_unit_quote(database)} "
                f"--project-root {_unit_quote(root)}"
            ),
            f"Environment={_unit_quote('PYTHONPATH=' + root)}",
            'Environment="PYTHONUNBUFFERED=1"',
            "Restart=always",
            "RestartSec=3",
            # Scientific runners are started in separate sessions and must not
            # be killed merely because dispatcher infrastructure is restarted.
            "KillMode=process",
            "TimeoutStopSec=10",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )


def _systemctl():
    return shutil.which("systemctl")


def _run_systemctl(*args, timeout=10, check=False):
    exe = _systemctl()
    if not exe:
        return subprocess.CompletedProcess(
            ["systemctl", "--user", *args],
            returncode=127,
            stdout="",
            stderr="systemctl not found",
        )
    return subprocess.run(
        [exe, "--user", *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=check,
    )


def service_status(project_root, db_path, *, home=None):
    path = service_path(project_root, db_path, home=home)
    name = path.name
    installed = path.is_file()
    active = False
    enabled = False
    main_pid = None
    detail = ""

    if _systemctl():
        if installed:
            active_result = _run_systemctl("is-active", name, timeout=5)
            enabled_result = _run_systemctl("is-enabled", name, timeout=5)
            pid_result = _run_systemctl(
                "show",
                "--property=MainPID",
                "--value",
                name,
                timeout=5,
            )
            active = active_result.returncode == 0 and active_result.stdout.strip() == "active"
            enabled = enabled_result.returncode == 0 and enabled_result.stdout.strip() == "enabled"
            try:
                parsed_pid = int(pid_result.stdout.strip() or "0")
            except ValueError:
                parsed_pid = 0
            main_pid = parsed_pid if parsed_pid > 0 else None
            detail = (
                active_result.stderr.strip()
                or enabled_result.stderr.strip()
                or pid_result.stderr.strip()
                or active_result.stdout.strip()
                or enabled_result.stdout.strip()
            )
    elif installed:
        detail = "systemctl not found"

    return {
        "name": name,
        "path": str(path),
        "installed": bool(installed),
        "active": bool(active),
        "enabled": bool(enabled),
        "main_pid": main_pid,
        "systemctl_available": bool(_systemctl()),
        "detail": detail,
    }


def install_service(
    project_root,
    db_path,
    *,
    python_executable=None,
    home=None,
    start=True,
):
    path = service_path(project_root, db_path, home=home)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = service_unit_text(
        project_root,
        db_path,
        python_executable=python_executable,
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)

    reload_result = _run_systemctl("daemon-reload")
    if reload_result.returncode != 0:
        raise RuntimeError(
            reload_result.stderr.strip()
            or "systemctl --user daemon-reload failed"
        )
    if start:
        result = _run_systemctl("enable", "--now", path.name)
    else:
        result = _run_systemctl("enable", path.name)
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip()
            or f"failed to enable {path.name}"
        )
    return service_status(project_root, db_path, home=home)


def start_service(project_root, db_path, *, home=None):
    status = service_status(project_root, db_path, home=home)
    if not status["installed"]:
        raise ValueError("dispatcher service is not installed")
    result = _run_systemctl("start", status["name"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "failed to start dispatcher service")
    return service_status(project_root, db_path, home=home)


def restart_service(project_root, db_path, *, home=None):
    status = service_status(project_root, db_path, home=home)
    if not status["installed"]:
        raise ValueError("dispatcher service is not installed")
    result = _run_systemctl("restart", status["name"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "failed to restart dispatcher service")
    return service_status(project_root, db_path, home=home)


def uninstall_service(project_root, db_path, *, home=None):
    path = service_path(project_root, db_path, home=home)
    name = path.name
    if _systemctl():
        _run_systemctl("disable", "--now", name)
    path.unlink(missing_ok=True)
    if _systemctl():
        _run_systemctl("daemon-reload")
        _run_systemctl("reset-failed", name)
    return service_status(project_root, db_path, home=home)


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["install", "status", "start", "restart", "uninstall"])
    ap.add_argument("--db", required=True)
    ap.add_argument("--project-root", required=True)
    ap.add_argument("--python", default=sys.executable)
    return ap.parse_args()


def main():
    args = parse_args()
    if args.action == "install":
        result = install_service(
            args.project_root,
            args.db,
            python_executable=args.python,
        )
    elif args.action == "start":
        result = start_service(args.project_root, args.db)
    elif args.action == "restart":
        result = restart_service(args.project_root, args.db)
    elif args.action == "uninstall":
        result = uninstall_service(args.project_root, args.db)
    else:
        result = service_status(args.project_root, args.db)

    for key in (
        "name",
        "path",
        "installed",
        "enabled",
        "active",
        "main_pid",
        "systemctl_available",
        "detail",
    ):
        print(f"{key}={result.get(key)}")


if __name__ == "__main__":
    main()
