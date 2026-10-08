"""Shared runtime executable resolution and lightweight health probes.

SYSTEM-14 owns runtime health. Settings and Diagnostics consume this module so
"healthy" has one meaning across the application.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rank42.ratpoints import RatpointsError, probe_version


RUNTIME_KEYS = ("science_python", "ratpoints", "ratpoints_gpu")
_REQUIRED_KEYS = frozenset({"science_python", "ratpoints"})


@dataclass(frozen=True)
class RuntimeProbe:
    key: str
    configured: str
    resolved: str | None
    required: bool
    available: bool
    healthy: bool
    status: str
    version: str | None = None
    detail: str = ""

    def as_dict(self):
        return {
            "key": self.key,
            "configured": self.configured,
            "resolved": self.resolved,
            "required": self.required,
            "available": self.available,
            "healthy": self.healthy,
            "status": self.status,
            "version": self.version,
            "detail": self.detail,
        }


def resolve_configured_executable(value):
    """Resolve one configured command and require executable permission."""
    configured = str(value or "").strip()
    if not configured:
        return None

    expanded = Path(configured).expanduser()
    path_like = (
        os.path.sep in configured
        or (os.path.altsep is not None and os.path.altsep in configured)
        or configured.startswith("~")
    )
    if path_like:
        if not expanded.is_file() or not os.access(expanded, os.X_OK):
            return None
        return str(expanded.resolve())

    found = shutil.which(configured)
    if not found:
        return None
    resolved = Path(found).expanduser()
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        return None
    return str(resolved.resolve())


def _missing_probe(key, configured, *, required):
    label = "required" if required else "optional"
    return RuntimeProbe(
        key=key,
        configured=configured,
        resolved=None,
        required=required,
        available=False,
        healthy=False,
        status="fail" if required else "warn",
        detail=(
            f"Configured {label} runtime does not resolve to an executable "
            "file or PATH command."
        ),
    )


def _science_probe(configured, resolved, *, timeout):
    try:
        cp = subprocess.run(
            [
                resolved,
                "-c",
                "from sage.version import version; print(version)",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=max(1.0, float(timeout)),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return RuntimeProbe(
            "science_python", configured, resolved, True, True, False, "fail",
            detail=f"Sage import probe failed: {exc}",
        )

    stdout = (cp.stdout or "").strip()
    stderr = (cp.stderr or "").strip()
    if cp.returncode != 0:
        return RuntimeProbe(
            "science_python", configured, resolved, True, True, False, "fail",
            detail=stderr or stdout or f"Sage import probe exited {cp.returncode}.",
        )
    version = stdout.splitlines()[-1].strip() if stdout else None
    return RuntimeProbe(
        "science_python",
        configured,
        resolved,
        True,
        True,
        True,
        "pass",
        version=version,
        detail="Sage import probe succeeded.",
    )


def _ratpoints_probe(key, configured, resolved, *, required, timeout):
    try:
        probe = probe_version(resolved, timeout=max(1.0, float(timeout)))
    except (RatpointsError, OSError, subprocess.SubprocessError) as exc:
        return RuntimeProbe(
            key,
            configured,
            resolved,
            required,
            True,
            False,
            "fail" if required else "warn",
            detail=f"ratpoints probe failed: {exc}",
        )

    version = probe.get("version")
    output = str(probe.get("output") or "")
    identified = bool(version) or "ratpoints" in output.lower()
    if not identified:
        return RuntimeProbe(
            key,
            configured,
            resolved,
            required,
            True,
            False,
            "fail" if required else "warn",
            detail="Executable launched but did not identify itself as ratpoints.",
        )

    return RuntimeProbe(
        key,
        configured,
        resolved,
        required,
        True,
        True,
        "pass",
        version=None if version is None else str(version),
        detail="ratpoints probe succeeded.",
    )


def probe_runtime_setting(key, value, *, run_probe=True, timeout=5):
    """Resolve and optionally probe one SYSTEM-14 runtime setting."""
    key = str(key)
    if key not in RUNTIME_KEYS:
        raise KeyError(f"unknown runtime executable setting {key!r}")

    configured = str(value or "").strip()
    required = key in _REQUIRED_KEYS
    resolved = resolve_configured_executable(configured)
    if resolved is None:
        return _missing_probe(key, configured, required=required)

    if not run_probe:
        return RuntimeProbe(
            key,
            configured,
            resolved,
            required,
            True,
            True,
            "pass",
            detail="Executable resolved.",
        )

    if key == "science_python":
        return _science_probe(configured, resolved, timeout=timeout)
    return _ratpoints_probe(
        key,
        configured,
        resolved,
        required=required,
        timeout=timeout,
    )


def probe_runtime_bundle(science_python, ratpoints, ratpoints_gpu, *, timeout=5):
    return {
        key: probe_runtime_setting(key, value, run_probe=True, timeout=timeout)
        for key, value in (
            ("science_python", science_python),
            ("ratpoints", ratpoints),
            ("ratpoints_gpu", ratpoints_gpu),
        )
    }


def required_runtime_failures(probes):
    """Return required runtime probes that are not healthy."""
    return [
        probe
        for probe in probes.values()
        if probe.required and not probe.healthy
    ]
