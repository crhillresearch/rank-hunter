"""Database channel for a remote job whose authoritative SQLite file stays local.

Each remote job owns one controller-side connection. A worker can run existing
search code against the channel without copying or network-mounting rank42.db.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import sqlite3
import threading
import urllib.error
import urllib.request
from pathlib import Path


def _wire(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__rh_bytes__": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, (tuple, list)):
        return [_wire(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _wire(item) for key, item in value.items()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported database value: {type(value).__name__}")


def _unwire(value):
    if isinstance(value, dict) and set(value) == {"__rh_bytes__"}:
        return base64.b64decode(value["__rh_bytes__"], validate=True)
    if isinstance(value, list):
        return [_unwire(item) for item in value]
    if isinstance(value, dict):
        return {key: _unwire(item) for key, item in value.items()}
    return value


def _map_paths(value, source, target):
    if not source:
        return value
    if isinstance(value, str):
        return value.replace(source, target)
    if isinstance(value, (tuple, list)):
        return [_map_paths(item, source, target) for item in value]
    if isinstance(value, dict):
        return {key: _map_paths(item, source, target) for key, item in value.items()}
    return value


def _path_pair(outgoing):
    local = os.environ.get("RANK_HUNTER_REMOTE_WORKSPACE", "")
    controller = os.environ.get("RANK_HUNTER_CONTROLLER_ROOT", "")
    return (local, controller) if outgoing else (controller, local)


class SessionStore:
    """Controller-side connections, each isolated to one authenticated job."""

    def __init__(self):
        self._sessions = {}
        self._lock = threading.RLock()

    def open(self, owner, db_path):
        token = secrets.token_urlsafe(32)
        connection = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        with self._lock:
            self._sessions[token] = {
                "owner": owner, "connection": connection, "lock": threading.RLock(),
                "cursors": {},
            }
        return token

    def _session(self, owner, token):
        with self._lock:
            session = self._sessions.get(str(token))
        if session is None or session["owner"] != owner:
            raise PermissionError("database session is unavailable")
        return session

    def call(self, owner, token, operation, data):
        session = self._session(owner, token)
        with session["lock"]:
            connection = session["connection"]
            if operation in {"execute", "executemany", "executescript"}:
                statement = str(data.get("sql") or "")
                if operation == "execute":
                    cursor = connection.execute(statement, _unwire(data.get("params") or []))
                elif operation == "executemany":
                    cursor = connection.executemany(
                        statement, _unwire(data.get("params") or [])
                    )
                else:
                    cursor = connection.executescript(statement)
                cursor_id = secrets.token_urlsafe(12)
                if cursor.description:
                    session["cursors"][cursor_id] = cursor
                else:
                    cursor.close()
                return {
                    "cursor": cursor_id, "description": (
                        [column[0] for column in cursor.description]
                        if cursor.description else None
                    ),
                    "lastrowid": cursor.lastrowid, "rowcount": cursor.rowcount,
                }
            if operation == "fetch":
                cursor_id = str(data.get("cursor") or "")
                cursor = session["cursors"].get(cursor_id)
                if cursor is None:
                    raise ValueError("database cursor is unavailable")
                count = max(1, min(256, int(data.get("count") or 1)))
                rows = cursor.fetchmany(count)
                if not rows:
                    session["cursors"].pop(cursor_id, None)
                    cursor.close()
                return {"rows": [_wire(tuple(row)) for row in rows]}
            if operation == "close_cursor":
                cursor = session["cursors"].pop(str(data.get("cursor") or ""), None)
                if cursor is not None:
                    cursor.close()
                return {"ok": True}
            if operation == "commit":
                connection.commit()
                return {"ok": True}
            if operation == "rollback":
                connection.rollback()
                return {"ok": True}
            if operation == "info":
                return {"in_transaction": connection.in_transaction,
                        "total_changes": connection.total_changes}
            raise ValueError("unsupported database operation")

    def close(self, owner, token):
        session = self._session(owner, token)
        with self._lock:
            self._sessions.pop(str(token), None)
        with session["lock"]:
            session["connection"].close()

    def close_owner(self, owner):
        with self._lock:
            tokens = [token for token, session in self._sessions.items()
                      if session["owner"] == owner]
        for token in tokens:
            try:
                self.close(owner, token)
            except PermissionError:
                pass

    def owners(self):
        with self._lock:
            return set(session["owner"] for session in self._sessions.values())


class RemoteRow:
    def __init__(self, names, values):
        self._names = tuple(names or ())
        self._values = tuple(_unwire(values))
        self._index = {}
        for index, name in enumerate(self._names):
            self._index.setdefault(name, index)

    def __getitem__(self, key):
        return self._values[key] if isinstance(key, int) else self._values[self._index[key]]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def keys(self):
        return list(self._names)


class RemoteCursor:
    def __init__(self, connection, info):
        self._connection = connection
        self._id = info["cursor"]
        self._names = info.get("description")
        self.description = (
            [(name, None, None, None, None, None, None) for name in self._names]
            if self._names else None
        )
        self.lastrowid = info.get("lastrowid")
        self.rowcount = info.get("rowcount", -1)
        self._closed = False
        self._exhausted = False

    def _rows(self, count):
        if self._closed:
            raise sqlite3.ProgrammingError("cursor is closed")
        if self._names is None:
            return []
        if self._exhausted:
            return []
        result = self._connection._call("fetch", {"cursor": self._id, "count": count})
        rows = result["rows"]
        if not rows:
            self._exhausted = True
        source, target = _path_pair(False)
        rows = _map_paths(rows, source, target)
        if self._connection.row_factory is sqlite3.Row:
            return [RemoteRow(self._names, values) for values in rows]
        return [tuple(_unwire(values)) for values in rows]

    def fetchone(self):
        rows = self._rows(1)
        return rows[0] if rows else None

    def fetchmany(self, size=1):
        return self._rows(size)

    def fetchall(self):
        rows = []
        while True:
            page = self._rows(256)
            if not page:
                return rows
            rows.extend(page)

    def __iter__(self):
        while (row := self.fetchone()) is not None:
            yield row

    def close(self):
        if not self._closed:
            if self._names is not None:
                self._connection._call("close_cursor", {"cursor": self._id})
            self._closed = True

    def __del__(self):
        if not self._closed and self._names:
            try:
                self.close()
            except Exception:
                pass


class RemoteConnection:
    def __init__(self, call, close):
        self._call = call
        self._close = close
        self.row_factory = None

    def execute(self, sql, parameters=()):
        source, target = _path_pair(True)
        return RemoteCursor(self, self._call("execute", {
            "sql": sql, "params": _wire(_map_paths(parameters, source, target)),
        }))

    def executemany(self, sql, parameters):
        source, target = _path_pair(True)
        rows = iter(parameters)
        total = 0
        while True:
            batch = []
            for _ in range(256):
                try:
                    batch.append(next(rows))
                except StopIteration:
                    break
            if not batch:
                break
            result = self._call("executemany", {
                "sql": sql, "params": _wire(_map_paths(batch, source, target)),
            })
            if int(result.get("rowcount") or 0) > 0:
                total += int(result["rowcount"])
        return RemoteCursor(self, {
            "cursor": "", "description": None, "lastrowid": None,
            "rowcount": total,
        })

    def executescript(self, sql):
        return RemoteCursor(self, self._call("executescript", {"sql": sql}))

    def commit(self):
        self._call("commit", {})

    def rollback(self):
        self._call("rollback", {})

    @property
    def in_transaction(self):
        return bool(self._call("info", {})["in_transaction"])

    @property
    def total_changes(self):
        return int(self._call("info", {})["total_changes"])

    def close(self):
        self._close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, _value, _traceback):
        self.rollback() if exc_type else self.commit()


def local_test_connection(store, owner, path):
    """Exercise the exact same proxy contract without opening a network port."""
    token = store.open(owner, path)
    return RemoteConnection(
        lambda operation, data: store.call(owner, token, operation, data),
        lambda: store.close(owner, token),
    )


def _remote_request(path, payload):
    url = os.environ["RANK_HUNTER_NODE_CONTROLLER"].rstrip("/") + path
    headers = {
        "Content-Type": "application/json",
        "X-RH-Node-ID": os.environ["RANK_HUNTER_NODE_ID"],
        "X-RH-Node-Secret": os.environ["RANK_HUNTER_NODE_SECRET"],
    }
    request = urllib.request.Request(
        url, data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.load(exc)
        except Exception:
            detail = {"error": exc.reason}
        if exc.code == 422:
            error_type = getattr(sqlite3, str(detail.get("sqlite_error")), sqlite3.OperationalError)
            raise error_type(str(detail.get("error"))) from exc
        raise RuntimeError(f"controller database channel: {detail.get('error')}") from exc


def remote_connection():
    task_id = int(os.environ["RANK_HUNTER_REMOTE_JOB_TASK_ID"])
    session = _remote_request("/db/open", {"task_id": task_id})["session"]
    base = {"task_id": task_id, "session": session}
    return RemoteConnection(
        lambda operation, data: _remote_request(
            "/db/call", {**base, "operation": operation, "data": data}
        ),
        lambda: _remote_request("/db/close", base),
    )


def install_remote_sqlite():
    """Route only the assigned rank database to its controller connection."""
    target = os.environ.get("RANK_HUNTER_REMOTE_DB_PATH")
    if not target:
        return
    resolved = Path(target).resolve()
    native_connect = sqlite3.connect

    def connect(database, *args, **kwargs):
        try:
            match = Path(str(database)).resolve() == resolved
        except (TypeError, ValueError):
            match = False
        return remote_connection() if match else native_connect(database, *args, **kwargs)

    sqlite3.connect = connect
