"""Authenticated controller endpoint for self-contained remote node tasks."""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import io
import json
import os
import sqlite3
import tarfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from rank42 import __version__
from rank42.node_cluster import (
    MAX_BODY_BYTES, PROTOCOL, authenticated_node, claim_task,
    connect_registry, finish_task, get_task, heartbeat, pair_node,
    renew_task, utc_now,
)
from rank42.node_db_proxy import SessionStore


def _plugin_bundle(root):
    source = Path(root) / "plugins"
    out = io.BytesIO()
    total = 0
    with tarfile.open(fileobj=out, mode="w:gz") as archive:
        if source.is_dir():
            for path in sorted(source.rglob("*")):
                if not path.is_file() or path.is_symlink():
                    continue
                if "__pycache__" in path.parts or path.suffix == ".pyc":
                    continue
                total += path.stat().st_size
                if total > 64 * 1024 * 1024:
                    raise ValueError("plugin tree exceeds the 64 MiB worker limit")
                archive.add(path, arcname=str(path.relative_to(Path(root))))
    raw = out.getvalue()
    if len(raw) > 32 * 1024 * 1024:
        raise ValueError("compressed plugin tree exceeds the 32 MiB worker limit")
    return {"bundle": base64.b64encode(raw).decode("ascii"),
            "sha256": hashlib.sha256(raw).hexdigest()}


def _accept_job_file(root, task_id, data):
    relative = Path(str(data.get("path") or ""))
    if (relative.is_absolute() or not relative.parts or ".." in relative.parts
            or relative.name == "rank42.db"):
        raise ValueError("invalid remote artifact path")
    if relative.parts[0] not in {
        "runs", "artifacts", "exports", "plugins", ".rank42-ui",
    }:
        raise ValueError("remote job attempted to write outside research output folders")
    if (relative.parts[0] == "plugins"
            and (len(relative.parts) < 3 or relative.parts[1] != "families")):
        raise ValueError("remote job may only update family plugins")
    if (relative.parts[0] == ".rank42-ui"
            and (len(relative.parts) < 3 or relative.parts[1] != "candidate-slices")):
        raise ValueError("invalid private system artifact path")
    root_path = Path(root).resolve()
    destination = (root_path / relative).resolve()
    if not destination.is_relative_to(root_path):
        raise ValueError("remote artifact leaves the project root")
    staging = root_path / ".rank42-ui" / "remote-upload" / str(task_id) / relative
    staging.parent.mkdir(parents=True, exist_ok=True)
    offset = int(data.get("offset") or 0)
    if offset < 0 or offset > 1024 * 1024 * 1024:
        raise ValueError("invalid remote artifact offset")
    raw = base64.b64decode(str(data.get("chunk") or ""), validate=True)
    if len(raw) > 32768:
        raise ValueError("remote artifact chunk is too large")
    if offset == 0:
        staging.write_bytes(b"")
    if not staging.exists() or staging.stat().st_size != offset:
        raise ValueError("remote artifact offset does not match")
    with staging.open("ab") as out:
        out.write(raw)
    if data.get("finished"):
        checker = hashlib.sha256()
        with staging.open("rb") as source:
            for piece in iter(lambda: source.read(1024 * 1024), b""):
                checker.update(piece)
        digest = checker.hexdigest()
        if digest != data.get("sha256"):
            raise ValueError("remote artifact checksum mismatch")
        if relative.parts[0] == "plugins":
            base = data.get("base_sha256")
            if destination.exists():
                current = hashlib.sha256(destination.read_bytes()).hexdigest()
                if base != current:
                    raise ValueError("controller plugin changed while remote job ran")
            elif base:
                raise ValueError("controller plugin was removed while remote job ran")
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, destination)
    return {"ok": True}


def handler_for(project_root):
    root = str(Path(project_root).resolve())
    instance = hashlib.sha256(root.encode("utf-8")).hexdigest()[:16]
    sessions = SessionStore()

    class NodeHandler(BaseHTTPRequestHandler):
        server_version = "RankHunterNodes/1"

        def _reply(self, code, payload):
            raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == "/health":
                self._reply(200, {"protocol": PROTOCOL, "instance": instance,
                                  "version": __version__,
                                  "pid": os.getpid(),
                                  "bind": self.server.server_address[0],
                                  "port": self.server.server_address[1]})
            else:
                self._reply(404, {"error": "unknown endpoint"})

        def do_POST(self):
            if self.path not in {"/pair", "/heartbeat", "/claim", "/renew", "/result",
                                 "/db/open", "/db/call", "/db/close", "/job/log",
                                 "/job/plugins", "/job/file"}:
                self._reply(404, {"error": "unknown endpoint"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                limit = 8 * 1024 * 1024 if self.path.startswith("/db/") else MAX_BODY_BYTES
                if not 0 < size <= limit:
                    self._reply(413, {"error": "invalid request size"})
                    return
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError("request must be a JSON object")
                db = connect_registry(root)
                try:
                    for owner in sessions.owners():
                        lease = get_task(db, owner[1])
                        if (lease is None or lease["status"] != "running"
                                or lease["node_id"] != owner[0]
                                or str(lease["lease_expires_at"] or "") < utc_now().isoformat()):
                            sessions.close_owner(owner)
                    if self.path == "/pair":
                        if str(data.get("version") or "") != __version__:
                            self._reply(409, {"error": f"Rank Hunter versions must match ({__version__})"})
                            return
                        result = pair_node(db, data.get("pair_code"), data)
                        self._reply(200, result)
                        return
                    node_id = self.headers.get("X-RH-Node-ID", "")
                    secret = self.headers.get("X-RH-Node-Secret", "")
                    if authenticated_node(db, node_id, secret) is None:
                        self._reply(403, {"error": "node authentication failed"})
                        return
                    remote_task = None
                    if self.path.startswith("/db/") or self.path.startswith("/job/"):
                        remote_task = get_task(db, data["task_id"])
                        if (remote_task is None or remote_task["kind"] != "ui_job"
                                or remote_task["status"] != "running"
                                or remote_task["node_id"] != node_id
                                or str(remote_task["lease_expires_at"] or "") < utc_now().isoformat()):
                            self._reply(403, {"error": "remote job lease is unavailable"})
                            return
                    if self.path == "/db/open":
                        payload = json.loads(remote_task["payload_json"])
                        token = sessions.open((node_id, int(remote_task["id"])), payload["db_path"])
                        self._reply(200, {"session": token})
                    elif self.path == "/db/call":
                        result = sessions.call(
                            (node_id, int(data["task_id"])), data["session"],
                            data["operation"], data.get("data") or {},
                        )
                        self._reply(200, result)
                    elif self.path == "/db/close":
                        sessions.close((node_id, int(data["task_id"])), data["session"])
                        self._reply(200, {"ok": True})
                    elif self.path == "/job/log":
                        chunk = data.get("chunk")
                        if not isinstance(chunk, str) or len(chunk) > 12000:
                            raise ValueError("invalid log chunk")
                        payload = json.loads(remote_task["payload_json"])
                        path = Path(payload["log_path"])
                        path.parent.mkdir(parents=True, exist_ok=True)
                        with path.open("a", encoding="utf-8") as out:
                            out.write(chunk)
                        self._reply(200, {"ok": True})
                    elif self.path == "/job/plugins":
                        self._reply(200, _plugin_bundle(root))
                    elif self.path == "/job/file":
                        self._reply(200, _accept_job_file(root, remote_task["id"], data))
                    elif self.path == "/heartbeat":
                        heartbeat(db, node_id, data)
                        self._reply(200, {"ok": True})
                    elif self.path == "/claim":
                        self._reply(200, {"task": claim_task(db, node_id)})
                    elif self.path == "/renew":
                        ok = renew_task(db, node_id, data["task_id"])
                        if not ok:
                            sessions.close_owner((node_id, int(data["task_id"])))
                        self._reply(200 if ok else 409, {"ok": ok})
                    else:
                        ok = finish_task(
                            db, node_id, data["task_id"],
                            result=data.get("result"), error=data.get("error"),
                        )
                        if ok:
                            sessions.close_owner((node_id, int(data["task_id"])))
                        self._reply(200 if ok else 409, {"ok": ok})
                finally:
                    db.close()
            except PermissionError:
                self._reply(403, {"error": "pair code is invalid or expired"})
            except sqlite3.Error as exc:
                self._reply(422, {"error": str(exc)[:500], "sqlite_error": type(exc).__name__})
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                self._reply(400, {"error": str(exc)[:300]})

        def log_message(self, _format, *_args):
            # Pairing credentials never belong in access logs.
            return

    return NodeHandler


def main(argv=None):
    parser = argparse.ArgumentParser(description="Rank Hunter node controller")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    root = Path(args.project_root).resolve()
    lock_path = root / ".rank42-ui" / "node-controller.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        db = connect_registry(root)
        db.close()
        server = ThreadingHTTPServer((args.bind, args.port), handler_for(root))
        print(f"Node controller listening on {args.bind}:{args.port}", flush=True)
        try:
            server.serve_forever(poll_interval=0.5)
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
