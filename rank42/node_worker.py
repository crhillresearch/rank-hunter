"""Rank Hunter remote worker. Runs only allowlisted, database-free tasks."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import platform
import selectors
import signal
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rank42 import __version__
from rank42.node_cluster import PROTOCOL, code_fingerprint


CURVE_INVARIANTS_SOURCE = r'''
import json,sys
from sage.all import EllipticCurve, QQ
p=json.load(sys.stdin)
a=p.get("a_invariants")
if not isinstance(a,list) or len(a)!=5:
    raise ValueError("five a-invariants required")
E=EllipticCurve(QQ,[QQ(str(x)) for x in a])
if E.discriminant()==0:
    raise ValueError("singular curve")
print(json.dumps({
  "a_invariants":[str(x) for x in E.a_invariants()],
  "discriminant":str(E.discriminant()),
  "j_invariant":str(E.j_invariant()),
  "c4":str(E.c4()),"c6":str(E.c6()),
  "curve_id":p.get("curve_id")
},separators=(",",":")),flush=True)
'''

CURVE_POINT_SEARCH_SOURCE = r'''
import json,sys
from sage.all import EllipticCurve, QQ
p=json.load(sys.stdin)
raw=p.get("a_invariants")
if not isinstance(raw,list) or len(raw)!=5:
    raise ValueError("five a-invariants required")
E=EllipticCurve(QQ,[QQ(str(x)) for x in raw])
if E.discriminant()==0:
    raise ValueError("singular curve")
height=float(p.get("height_limit"))
if not 1<=height<=10:
    raise ValueError("height limit outside allowed range")
points=E.point_search(height,verbose=False)
print(json.dumps({
  "a_invariants":[str(x) for x in E.a_invariants()],
  "curve_id":p.get("curve_id"),
  "height_limit":height,
  "points":[[str(P[0]),str(P[1])] for P in points[:64]]
},separators=(",",":")),flush=True)
'''


def _request(url, path, data, *, node_id=None, secret=None):
    raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if node_id and secret:
        headers.update({"X-RH-Node-ID": node_id, "X-RH-Node-Secret": secret})
    request = urllib.request.Request(
        url.rstrip("/") + path, data=raw, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=60 if path == "/job/plugins" else 12) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.load(exc).get("error", exc.reason)
        except Exception:
            detail = exc.reason
        raise RuntimeError(f"controller HTTP {exc.code}: {detail}") from exc


def _config_path(controller_url):
    digest = hashlib.sha256(controller_url.rstrip("/").encode()).hexdigest()[:16]
    return Path.home() / ".config" / "rank-hunter" / f"node-{digest}.json"


def _save_identity(path, identity):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as out:
        os.fchmod(out.fileno(), 0o600)
        json.dump(identity, out)
    os.replace(tmp, path)


def _gpu_available():
    tool = shutil.which("nvidia-smi")
    if not tool:
        return False
    try:
        proc = subprocess.run([tool, "-L"], capture_output=True, text=True,
                              timeout=3, check=False)
        return proc.returncode == 0 and bool(proc.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        return False


def _ratpoints_available(backend):
    from rank42.ratpoints import vendored_executable
    path = vendored_executable(backend)
    return (os.path.isfile(path) and os.access(path, os.X_OK)) or (
        backend == "CPU" and bool(shutil.which("ratpoints"))
    )


def _details(slots, has_gpu=False, has_ratpoints_cpu=False, has_ratpoints_gpu=False):
    return {
        "hostname": platform.node() or "Worker",
        "platform": platform.platform(),
        "cpu_cores": os.cpu_count() or 1,
        "slots": slots,
        "has_gpu": bool(has_gpu),
        "has_ratpoints_cpu": bool(has_ratpoints_cpu),
        "has_ratpoints_gpu": bool(has_ratpoints_gpu),
        "version": __version__,
        "protocol": PROTOCOL,
        "code_fingerprint": code_fingerprint(),
    }


def _run_task(task):
    kind = task.get("kind")
    if kind == "ui_job":
        return _run_ui_job(task)
    if kind == "ratpoints_scan":
        try:
            from rank42.ratpoints import (
                RatpointsFailure, RatpointsTimeout, run_ratpoints,
                vendored_executable,
            )
            payload = json.loads(task["payload_json"])
            backend = payload["backend"]
            executable = vendored_executable(backend) if backend == "GPU" else None
            result = run_ratpoints(
                payload["coefficients"], payload["height"],
                executable=executable, timeout=payload["timeout"],
                denominator_low=payload.get("denominator_low"),
                denominator_high=payload.get("denominator_high"),
                one_point=payload.get("one_point", False),
                extra_args=payload.get("extra_args") or None,
                _local_only=True,
            )
            if len(result["points"]) > 400:
                return None, "ratpoints returned more than 400 points; result too large for transport"
            response = {
                "coefficients": payload["coefficients"],
                "backend": backend,
                "points": [point.as_json() for point in result["points"]],
                "runtime": result["runtime"],
                "engine_provenance": result.get("engine_provenance"),
            }
            if len(json.dumps(response).encode("utf-8")) > 60000:
                return None, "ratpoints result exceeds the node transport limit"
            return response, None
        except RatpointsTimeout as exc:
            return None, f"timeout: {exc}"
        except (RatpointsFailure, OSError, ValueError, KeyError) as exc:
            return None, f"ratpoints failed: {exc}"
    sources = {"curve_invariants": CURVE_INVARIANTS_SOURCE,
               "curve_point_search": CURVE_POINT_SEARCH_SOURCE}
    if kind not in sources:
        return None, f"unsupported worker task {kind!r}"
    try:
        payload = json.loads(task["payload_json"])
        proc = subprocess.run(
            [sys.executable, "-c", sources[kind]],
            input=json.dumps(payload), text=True, capture_output=True,
            timeout=120, check=False, start_new_session=True,
        )
        if proc.returncode != 0:
            return None, (proc.stderr or proc.stdout or "worker failed")[-1200:]
        result = json.loads(proc.stdout.strip())
        if not isinstance(result, dict):
            raise ValueError("worker result is not an object")
        return result, None
    except subprocess.TimeoutExpired:
        return None, "worker timed out after 120 seconds"
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        return None, str(exc)[:1200]


def _run_ui_job(task):
    """Run one whole Pipeline process; its SQLite calls return to Node 1."""
    payload = json.loads(task["payload_json"])
    root = Path(__file__).resolve().parents[1]
    controller_key = hashlib.sha256(task["_controller_url"].encode("utf-8")).hexdigest()[:12]
    workspace = root / ".rank42-ui" / "remote-jobs" / controller_key / str(task["id"])
    remote_path = workspace / "rank42.db"
    remote_path.parent.mkdir(parents=True, exist_ok=True)
    remote_path.touch(exist_ok=True)
    cache = root / ".rank42-cache" / "remote-worker" / controller_key / code_fingerprint()[:12]
    cache.mkdir(parents=True, exist_ok=True)
    cache_link = workspace / ".rank42-cache"
    if not cache_link.exists() and not cache_link.is_symlink():
        cache_link.symlink_to(cache, target_is_directory=True)
    staged_hashes = {}
    try:
        response = _request(task["_controller_url"], "/job/plugins", {
            "task_id": task["id"],
        }, node_id=task["_node_id"], secret=task["_secret"])
        raw = base64.b64decode(response["bundle"], validate=True)
        if hashlib.sha256(raw).hexdigest() != response["sha256"]:
            raise ValueError("plugin bundle checksum mismatch")
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
            for member in archive.getmembers():
                relative = Path(member.name)
                if (not member.isfile() or not relative.parts
                        or relative.parts[0] != "plugins"
                        or ".." in relative.parts or relative.is_absolute()):
                    raise ValueError("invalid plugin bundle entry")
                source = archive.extractfile(member)
                target = workspace / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                data = source.read()
                target.write_bytes(data)
                staged_hashes[str(relative)] = hashlib.sha256(data).hexdigest()
    except (OSError, RuntimeError, ValueError, KeyError, tarfile.TarError) as exc:
        return None, f"could not stage controller plugins: {exc}"
    command = list(payload["command"])
    command[0] = sys.executable
    for option, value in (("--project-root", str(workspace)), ("--db", str(remote_path))):
        if option not in command:
            return None, f"Pipeline command is missing {option}"
        command[command.index(option) + 1] = value
    env = os.environ.copy()
    env.update({
        "RANK_HUNTER_REMOTE_DB_PATH": str(remote_path),
        "RANK_HUNTER_REMOTE_WORKSPACE": str(workspace),
        "RANK_HUNTER_CONTROLLER_ROOT": str(payload["project_root"]),
        "RANK_HUNTER_REMOTE_JOB_TASK_ID": str(task["id"]),
        "RANK_HUNTER_NODE_CONTROLLER": task["_controller_url"],
        "RANK_HUNTER_NODE_ID": task["_node_id"],
        "RANK_HUNTER_NODE_SECRET": task["_secret"],
        "RANK_HUNTER_PROJECT_ROOT": str(workspace),
        "PYTHONUNBUFFERED": "1",
    })
    env["PYTHONPATH"] = os.pathsep.join(
        [str(workspace), str(root), *(env.get("PYTHONPATH") or "").split(os.pathsep)]
    )
    env.pop("RANK_HUNTER_REMOTE_DB_SESSION", None)
    proc = subprocess.Popen(
        command, cwd=str(workspace), env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
    )
    task["_process"] = proc
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while True:
                if task.get("_cancelled"):
                    os.killpg(proc.pid, signal.SIGTERM)
                    return None, "remote job cancelled"
                ready = selector.select(timeout=0.5)
                if ready:
                    chunk = os.read(proc.stdout.fileno(), 4096)
                    if not chunk:
                        break
                    _request(task["_controller_url"], "/job/log", {
                        "task_id": task["id"],
                        "chunk": chunk.decode("utf-8", errors="replace"),
                    }, node_id=task["_node_id"], secret=task["_secret"])
                elif proc.poll() is not None:
                    break
        code = proc.wait()
        uploaded = _upload_job_outputs(task, workspace, staged_hashes)
        shutil.rmtree(workspace)
        return {"job_id": payload["job_id"], "exit_code": int(code),
                "artifacts_uploaded": uploaded}, None
    except (OSError, RuntimeError) as exc:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
        return None, f"remote job transport failed: {exc}"
    finally:
        task.pop("_process", None)
        remote_path.unlink(missing_ok=True)
        # A failed transfer keeps the workspace for manual recovery.


def _upload_job_outputs(task, workspace, staged_hashes):
    count = 0
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(workspace)
        if relative.name == "rank42.db":
            continue
        if relative.parts[0] == ".rank42-cache":
            continue
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        if relative.parts[0] not in {
            "runs", "artifacts", "exports", "plugins", ".rank42-ui",
        }:
            raise RuntimeError(f"job produced an unsupported project file: {relative}")
        if (relative.parts[0] == "plugins"
                and (len(relative.parts) < 3 or relative.parts[1] != "families")):
            raise RuntimeError(f"job attempted to modify a non-family plugin: {relative}")
        if (relative.parts[0] == ".rank42-ui"
                and (len(relative.parts) < 3 or relative.parts[1] != "candidate-slices")):
            raise RuntimeError(f"job produced a private system file: {relative}")
        if path.stat().st_size > 1024 * 1024 * 1024:
            raise RuntimeError(f"job artifact exceeds 1 GiB: {relative}")
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        if (relative.parts[0] == "plugins"
                and staged_hashes.get(str(relative)) == digest.hexdigest()):
            continue
        offset = 0
        with path.open("rb") as source:
            while True:
                chunk = source.read(32768)
                finished = not chunk or offset + len(chunk) == path.stat().st_size
                _request(task["_controller_url"], "/job/file", {
                    "task_id": task["id"], "path": str(relative),
                    "offset": offset,
                    "chunk": base64.b64encode(chunk).decode("ascii"),
                    "finished": finished, "sha256": digest.hexdigest() if finished else None,
                    "base_sha256": staged_hashes.get(str(relative)) if finished else None,
                }, node_id=task["_node_id"], secret=task["_secret"])
                offset += len(chunk)
                if finished:
                    break
        count += 1
    return count


def run(controller_url, *, slots=1, pair_code=None, poll_seconds=3, forget=False):
    try:
        import sage.all  # noqa: F401
    except ImportError as exc:
        raise SystemExit("Node worker requires Rank Hunter's Sage Python runtime") from exc
    slots = max(1, min(32, int(slots)))
    has_gpu = _gpu_available()
    has_ratpoints_cpu = _ratpoints_available("CPU")
    has_ratpoints_gpu = has_gpu and _ratpoints_available("GPU")
    details = _details(slots, has_gpu, has_ratpoints_cpu, has_ratpoints_gpu)
    config = _config_path(controller_url)
    if forget:
        config.unlink(missing_ok=True)
    if config.exists():
        identity = json.loads(config.read_text(encoding="utf-8"))
    else:
        code = pair_code or os.environ.get("RH_NODE_PAIR_CODE")
        if not code:
            raise SystemExit("Set RH_NODE_PAIR_CODE to the one-time code shown on Nodes")
        identity = _request(controller_url, "/pair", {"pair_code": code, **details})
        _save_identity(config, identity)
    node_id = identity["node_id"]
    secret = identity["secret"]
    print(f"Rank Hunter worker {node_id} connected to {controller_url}", flush=True)
    active = {}
    last_heartbeat = 0.0
    with ThreadPoolExecutor(max_workers=slots) as pool:
        while True:
            try:
                if time.monotonic() - last_heartbeat >= 5:
                    _request(controller_url, "/heartbeat", details,
                             node_id=node_id, secret=secret)
                    for task in active.values():
                        try:
                            _request(controller_url, "/renew", {"task_id": task["id"]},
                                     node_id=node_id, secret=secret)
                        except RuntimeError as exc:
                            if "HTTP 409" in str(exc):
                                task["_cancelled"] = True
                                proc = task.get("_process")
                                if proc is not None and proc.poll() is None:
                                    os.killpg(proc.pid, signal.SIGTERM)
                            else:
                                raise
                    last_heartbeat = time.monotonic()
                for future, task in list(active.items()):
                    if not future.done():
                        continue
                    try:
                        result, error = future.result()
                    except Exception as exc:
                        result, error = None, str(exc)[:1200]
                    try:
                        _request(controller_url, "/result", {
                            "task_id": task["id"], "result": result, "error": error,
                        }, node_id=node_id, secret=secret)
                    except RuntimeError as exc:
                        if "HTTP 409" not in str(exc):
                            raise
                    print(f"Task #{task['id']}: {'failed' if error else 'completed'}", flush=True)
                    del active[future]
                while len(active) < slots:
                    response = _request(controller_url, "/claim", {},
                                        node_id=node_id, secret=secret)
                    task = response.get("task")
                    if task is None:
                        break
                    if task["kind"] == "ui_job":
                        task.update({"_controller_url": controller_url,
                                     "_node_id": node_id, "_secret": secret})
                    active[pool.submit(_run_task, task)] = task
                    print(f"Task #{task['id']}: {task['kind']}", flush=True)
            except (OSError, RuntimeError, ValueError) as exc:
                print(f"Controller unavailable: {exc}", file=sys.stderr, flush=True)
            time.sleep(max(0.5, float(poll_seconds)))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Join a Rank Hunter controller as a compute worker")
    parser.add_argument("--controller", required=True, help="Controller URL shown on Nodes")
    parser.add_argument("--slots", type=int, default=1)
    parser.add_argument("--poll-seconds", type=float, default=3)
    parser.add_argument("--forget", action="store_true", help="discard saved identity and pair again")
    args = parser.parse_args(argv)
    run(args.controller, slots=args.slots, poll_seconds=args.poll_seconds, forget=args.forget)


if __name__ == "__main__":
    main()
