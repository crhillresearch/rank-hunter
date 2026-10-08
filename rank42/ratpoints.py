"""Safe subprocess interface to Michael Stoll's ``ratpoints`` program.

``ratpoints`` searches rational points on curves y^2=f(x) with integral
polynomial coefficients.  This module accepts rational coefficients too: if
D is the least common multiple of their denominators, it searches

    Y^2 = D^2 f(x),      Y = D y,

which preserves the rational x-coordinates and lets us convert every returned
point exactly back to the original curve.

No shell is used.  All returned points are checked with exact Fraction
arithmetic before they are exposed to the caller.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from fractions import Fraction
from pathlib import Path


class RatpointsError(RuntimeError):
    pass


class RatpointsNotFound(RatpointsError):
    pass


class RatpointsTimeout(RatpointsError):
    pass


class RatpointsFailure(RatpointsError):
    pass


@dataclass(frozen=True)
class NormalizedPolynomial:
    rational_coefficients: tuple[Fraction, ...]
    integer_coefficients: tuple[int, ...]
    y_scale: int
    degree: int


@dataclass(frozen=True)
class Ratpoint:
    x: Fraction
    y: Fraction
    projective_x: int
    projective_y: int
    projective_z: int

    def as_json(self):
        return {
            "x": str(self.x),
            "y": str(self.y),
            "projective": [
                str(self.projective_x),
                str(self.projective_y),
                str(self.projective_z),
            ],
        }


def _fraction(value):
    if isinstance(value, Fraction):
        return value
    return Fraction(str(value))


def normalize_polynomial(coefficients):
    """Convert rational coefficients a_0,...,a_n to an integral model.

    Trailing zero coefficients are removed.  The returned integral equation is
    Y^2 = sum(integer_coefficients[i] * x^i), with Y = y_scale * y.
    """
    coeffs = [_fraction(c) for c in coefficients]
    while len(coeffs) > 1 and coeffs[-1] == 0:
        coeffs.pop()
    if len(coeffs) < 2 or all(c == 0 for c in coeffs[1:]):
        raise ValueError("ratpoints requires a positive-degree polynomial")

    D = 1
    for c in coeffs:
        D = math.lcm(D, c.denominator)
    scale2 = D * D
    ints = []
    for c in coeffs:
        value = c * scale2
        if value.denominator != 1:
            raise AssertionError("internal denominator normalization failure")
        ints.append(int(value.numerator))
    return NormalizedPolynomial(
        rational_coefficients=tuple(coeffs),
        integer_coefficients=tuple(ints),
        y_scale=D,
        degree=len(coeffs) - 1,
    )


def evaluate_polynomial(coefficients, x):
    x = _fraction(x)
    acc = Fraction(0)
    for c in reversed(tuple(_fraction(v) for v in coefficients)):
        acc = acc * x + c
    return acc


def vendored_executable(backend="CPU", project_root=None):
    """Return the canonical in-repository CPU/GPU ratpoints executable path.

    The source trees are Git submodules under ``vendor/``.  This helper does
    not require that the executable has already been built; Settings uses it
    to prepopulate the expected path before installation/build.
    """
    root = Path(project_root).expanduser().resolve() if project_root else Path(__file__).resolve().parents[1]
    backend = str(backend or "CPU").upper()
    if backend == "GPU":
        return str(root / "vendor" / "ratpoints-gpu" / "ratpoints_gpu")
    if backend != "CPU":
        raise ValueError(f"unknown ratpoints backend: {backend}")
    return str(root / "vendor" / "ratpoints" / "ratpoints")


def resolve_executable(explicit=None):
    # Reproducibility order: explicit override, environment override, pinned
    # vendored build, then a system PATH fallback for developer convenience.
    candidate = explicit or os.environ.get("RANK42_RATPOINTS")
    if not candidate:
        vendored = vendored_executable("CPU")
        candidate = vendored if Path(vendored).is_file() else shutil.which("ratpoints")
    if not candidate:
        raise RatpointsNotFound(
            "ratpoints executable not found; build vendor/ratpoints with "
            "scripts/install-ratpoints.sh or set RANK42_RATPOINTS"
        )
    candidate = str(Path(candidate).expanduser()) if os.path.sep in candidate or candidate.startswith("~") else candidate
    path = shutil.which(candidate) if os.path.sep not in candidate else candidate
    if path is None or not Path(path).exists():
        raise RatpointsNotFound(f"ratpoints executable not found: {candidate}")
    return str(Path(path).resolve())


def _process_group_alive(pgid):
    try:
        os.killpg(int(pgid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _terminate_process_group(pgid, *, grace=0.35):
    """Best-effort cleanup for a ratpoints process group.

    GPU wrappers may spawn helper processes.  Every ratpoints invocation gets
    its own session/process group so cleanup can include those helpers without
    touching the Rank Hunter scientific worker.
    """
    pgid = int(pgid)
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + max(0.0, float(grace))
    while time.monotonic() < deadline:
        if not _process_group_alive(pgid):
            return
        time.sleep(0.02)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _managed_run(command, *, timeout):
    """Run one ratpoints-compatible command with hard process-tree cleanup.

    ``subprocess.run(..., timeout=...)`` only guarantees termination of the
    direct child. A GPU wrapper can have worker/helper descendants, so each
    invocation gets its own session. Output is captured in temporary files
    rather than pipes: a stray helper inheriting stdout/stderr therefore cannot
    make ``communicate()`` look hung after the wrapper itself has exited.
    """
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace") as out, \
         tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace") as err:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            start_new_session=True,
        )
        old_sigterm = None
        can_handle_sigterm = threading.current_thread() is threading.main_thread()
        if can_handle_sigterm:
            old_sigterm = signal.getsignal(signal.SIGTERM)

            def _sigterm_handler(signum, frame):
                _terminate_process_group(proc.pid, grace=0.15)
                raise SystemExit(128 + int(signum))

            signal.signal(signal.SIGTERM, _sigterm_handler)
        timed_out = None
        try:
            try:
                proc.wait(timeout=float(timeout))
            except subprocess.TimeoutExpired as exc:
                timed_out = exc
            finally:
                # A well-behaved CPU/GPU binary leaves no group members here.
                # If a wrapper returned while helpers survived, retire them.
                _terminate_process_group(proc.pid, grace=0.15)
                if proc.poll() is None:
                    try:
                        proc.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        try:
                            proc.kill()
                        except ProcessLookupError:
                            pass
                        proc.wait(timeout=1.0)
        finally:
            if can_handle_sigterm:
                signal.signal(signal.SIGTERM, old_sigterm)

        out.flush(); err.flush()
        out.seek(0); err.seek(0)
        stdout = out.read()
        stderr = err.read()
        if timed_out is not None:
            timed_out.stdout = stdout
            timed_out.stderr = stderr
            raise timed_out
        return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)


def probe_version(executable=None, timeout=3):
    exe = resolve_executable(executable)
    try:
        cp = _managed_run([exe], timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RatpointsTimeout("ratpoints version probe timed out") from exc
    text = (cp.stdout or "") + "\n" + (cp.stderr or "")
    match = re.search(r"ratpoints-([0-9]+(?:\.[0-9]+)+)", text)
    return {
        "executable": exe,
        "version": match.group(1) if match else None,
        "returncode": cp.returncode,
        "output": text.strip(),
    }


def _path_within(path, parent):
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
        return True
    except (ValueError, OSError):
        return False


@lru_cache(maxsize=16)
def _vendored_revision(directory):
    try:
        cp = subprocess.run(
            ["git", "-C", str(directory), "rev-parse", "HEAD"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1.0,
            check=False,
        )
    except Exception:
        return None
    value = (cp.stdout or "").strip()
    return value if cp.returncode == 0 and re.fullmatch(r"[0-9a-fA-F]{40}", value) else None


@lru_cache(maxsize=32)
def _ratpoints_engine_identity_cached(executable, backend_hint):
    exe = str(Path(executable).resolve())
    root = Path(__file__).resolve().parents[1]
    cpu_dir = root / "vendor" / "ratpoints"
    gpu_dir = root / "vendor" / "ratpoints-gpu"
    hint = str(backend_hint or "").upper()
    backend = hint if hint in {"CPU", "GPU"} else None
    vendored_revision = None
    source = "external"

    if _path_within(exe, gpu_dir):
        backend = "GPU"
        source = "vendored"
        vendored_revision = _vendored_revision(str(gpu_dir))
    elif _path_within(exe, cpu_dir):
        backend = "CPU"
        source = "vendored"
        vendored_revision = _vendored_revision(str(cpu_dir))
    elif backend is None:
        backend = "GPU" if "gpu" in Path(exe).name.lower() else "CPU"

    version = None
    probe_error = None
    try:
        version = probe_version(exe, timeout=2).get("version")
    except Exception as exc:
        probe_error = f"{exc.__class__.__name__}: {exc}"

    return {
        "backend": backend,
        "executable": exe,
        "version": version,
        "source": source,
        "vendored_revision": vendored_revision,
        "version_probe_error": probe_error,
    }


def ratpoints_engine_identity(executable=None, backend_hint=None):
    """Return compact reproducibility identity for the selected ratpoints engine.

    Resolution/probing is best-effort for manifests: an unavailable selected
    engine is represented explicitly instead of preventing Pipeline creation.
    """
    requested = executable
    hint = str(backend_hint or "").upper()
    if not requested and hint in {"CPU", "GPU"}:
        vendored = vendored_executable(hint)
        if Path(vendored).is_file():
            requested = vendored
        elif hint == "GPU":
            return {
                "backend": "GPU",
                "executable": str(Path(vendored).resolve()),
                "version": None,
                "source": "vendored",
                "vendored_revision": _vendored_revision(
                    str(Path(vendored).resolve().parent)
                ),
                "version_probe_error": "selected GPU executable is not built",
            }
    try:
        exe = resolve_executable(requested)
    except Exception as exc:
        return {
            "backend": hint if hint in {"CPU", "GPU"} else None,
            "executable": None if requested is None else str(requested),
            "version": None,
            "source": "unresolved",
            "vendored_revision": None,
            "version_probe_error": f"{exc.__class__.__name__}: {exc}",
        }
    return dict(_ratpoints_engine_identity_cached(exe, hint))


def _invocation_provenance(engine, command, *, timeout, runtime, status):
    return {
        **dict(engine or {}),
        "argv": [str(x) for x in command],
        "timeout_seconds": float(timeout),
        "runtime_seconds": float(runtime),
        "status": str(status),
    }


def _parse_output(stdout, normalized):
    # We force this format with -f, so no locale-sensitive parsing is needed.
    m = (normalized.degree + 1) // 2
    out = []
    seen = set()
    for line in (stdout or "").splitlines():
        parts = line.strip().split("\t")
        if len(parts) != 3:
            continue
        try:
            a, Y, b = map(int, parts)
        except ValueError:
            continue
        if b <= 0:
            continue
        x = Fraction(a, b)
        y = Fraction(Y, (b ** m) * normalized.y_scale)
        if y * y != evaluate_polynomial(normalized.rational_coefficients, x):
            raise RatpointsFailure(
                f"ratpoints returned a point failing exact verification: {line!r}"
            )
        key = (x, y)
        if key in seen:
            continue
        seen.add(key)
        out.append(Ratpoint(x, y, a, Y, b))
    return out


def run_ratpoints(
    coefficients,
    height,
    *,
    executable=None,
    timeout=300,
    denominator_low=None,
    denominator_high=None,
    one_point=False,
    extra_args=None,
):
    """Search finite rational points on y^2=f(x).

    ``height`` bounds both |numerator(x)| and denominator(x), as in ratpoints.
    The denominator range can optionally be restricted.  ``extra_args`` is an
    argv list, never a shell fragment.
    """
    height = int(height)
    if height <= 0:
        raise ValueError("height must be positive")
    exe = resolve_executable(executable)
    normalized = normalize_polynomial(coefficients)

    cmd = [
        exe,
        " ".join(str(c) for c in normalized.integer_coefficients),
        str(height),
        "-q",
        "-i",
        "-f",
        r"%x\t%y\t%z\n",
    ]
    if denominator_low is not None:
        cmd.extend(["-dl", str(int(denominator_low))])
    if denominator_high is not None:
        cmd.extend(["-du", str(int(denominator_high))])
    if one_point:
        cmd.append("-1")
    if extra_args:
        cmd.extend(str(x) for x in extra_args)

    engine = ratpoints_engine_identity(exe)
    started = time.monotonic()
    try:
        cp = _managed_run(cmd, timeout=float(timeout))
    except subprocess.TimeoutExpired as exc:
        runtime = time.monotonic() - started
        error = RatpointsTimeout(f"ratpoints exceeded {timeout}s")
        error.engine_provenance = _invocation_provenance(
            engine, cmd, timeout=timeout, runtime=runtime, status="timeout"
        )
        raise error from exc
    runtime = time.monotonic() - started

    if cp.returncode != 0:
        tail = "\n".join(((cp.stdout or "") + "\n" + (cp.stderr or "")).splitlines()[-30:])
        error = RatpointsFailure(
            f"ratpoints failed with exit={cp.returncode}; tail:\n{tail}"
        )
        error.engine_provenance = _invocation_provenance(
            engine, cmd, timeout=timeout, runtime=runtime, status="error"
        )
        raise error

    points = _parse_output(cp.stdout, normalized)
    invocation = _invocation_provenance(
        engine, cmd, timeout=timeout, runtime=runtime, status="completed"
    )
    return {
        "normalized": normalized,
        "points": points,
        "runtime": runtime,
        "executable": exe,
        "command": cmd,
        "engine_provenance": invocation,
        "stdout": cp.stdout,
        "stderr": cp.stderr,
    }
