"""Local registry and authenticated transport for Rank Hunter compute nodes.

This registry lives beside the controller database, never on a network share.
Workers receive only allowlisted, self-contained tasks, not SQL or shell commands.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import signal
import secrets
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from functools import lru_cache
from pathlib import Path


PROTOCOL = 1
DEFAULT_PORT = 8765
NODE_STALE_SECONDS = 18
TASK_LEASE_SECONDS = 180
MAX_BODY_BYTES = 65536
ALLOWED_TASKS = {"curve_invariants", "curve_point_search", "ratpoints_scan", "ui_job"}


@lru_cache(maxsize=1)
def code_fingerprint():
    digest = hashlib.sha256()
    package = Path(__file__).resolve().parent
    for path in sorted(package.rglob("*.py")):
        digest.update(str(path.relative_to(package)).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def utc_now():
    return datetime.now(timezone.utc)


def registry_path(project_root):
    return Path(project_root).resolve() / ".rank42-ui" / "nodes.sqlite"


def connect_registry(project_root):
    path = registry_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=10000")
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS pair_codes (
        digest TEXT PRIMARY KEY, expires_at TEXT NOT NULL, used_at TEXT
    );
    CREATE TABLE IF NOT EXISTS nodes (
        id TEXT PRIMARY KEY, secret_digest TEXT NOT NULL,
        hostname TEXT NOT NULL, platform TEXT NOT NULL DEFAULT '',
        cpu_cores INTEGER NOT NULL DEFAULT 1, slots INTEGER NOT NULL DEFAULT 1,
        has_gpu INTEGER NOT NULL DEFAULT 0, version TEXT NOT NULL DEFAULT '',
        last_seen TEXT NOT NULL, joined_at TEXT NOT NULL, revoked_at TEXT,
        priority INTEGER NOT NULL DEFAULT 100,
        has_ratpoints_cpu INTEGER NOT NULL DEFAULT 0,
        has_ratpoints_gpu INTEGER NOT NULL DEFAULT 0,
        code_fingerprint TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL, payload_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued', node_id TEXT,
        result_json TEXT, error TEXT,
        created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
        lease_expires_at TEXT,
        FOREIGN KEY(node_id) REFERENCES nodes(id)
    );
    CREATE INDEX IF NOT EXISTS idx_node_tasks_queue ON tasks(status,id);
    CREATE TABLE IF NOT EXISTS node_settings (
        key TEXT PRIMARY KEY, value_json TEXT NOT NULL
    );
    """)
    db.commit()
    columns = {row["name"] for row in db.execute("PRAGMA table_info(nodes)")}
    if "revoked_at" not in columns:
        db.execute("ALTER TABLE nodes ADD COLUMN revoked_at TEXT")
        db.commit()
    if "priority" not in columns:
        db.execute("ALTER TABLE nodes ADD COLUMN priority INTEGER NOT NULL DEFAULT 100")
        db.commit()
    for column in ("has_ratpoints_cpu", "has_ratpoints_gpu"):
        if column not in columns:
            db.execute(f"ALTER TABLE nodes ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")
            db.commit()
    if "code_fingerprint" not in columns:
        db.execute("ALTER TABLE nodes ADD COLUMN code_fingerprint TEXT NOT NULL DEFAULT ''")
        db.commit()
    return db


def _digest(secret):
    return hashlib.sha256(str(secret).encode("utf-8")).hexdigest()


def create_pair_code(db, *, minutes=15):
    code = secrets.token_urlsafe(32)
    expires = (utc_now() + timedelta(minutes=minutes)).isoformat()
    db.execute("INSERT INTO pair_codes(digest,expires_at) VALUES(?,?)", (_digest(code), expires))
    db.commit()
    return code


def pair_node(db, code, details):
    if not isinstance(code, str) or len(code) > 128:
        raise ValueError("invalid pair code")
    now = utc_now().isoformat()
    # BEGIN IMMEDIATE makes a one-time code single-use across simultaneous requests.
    db.execute("BEGIN IMMEDIATE")
    try:
        row = db.execute(
            "SELECT * FROM pair_codes WHERE digest=?", (_digest(code),)
        ).fetchone()
        if row is None or row["used_at"] or row["expires_at"] <= now:
            raise PermissionError("pair code is invalid or expired")
        node_id = "node-" + secrets.token_hex(8)
        secret = secrets.token_urlsafe(48)
        hostname = str(details.get("hostname") or "Worker")[:120]
        priority = int(db.execute(
            "SELECT COALESCE(MAX(priority),0)+100 FROM nodes WHERE revoked_at IS NULL"
        ).fetchone()[0])
        db.execute("UPDATE pair_codes SET used_at=? WHERE digest=?", (now, _digest(code)))
        db.execute(
            """INSERT INTO nodes(id,secret_digest,hostname,platform,cpu_cores,slots,
               has_gpu,version,last_seen,joined_at,priority,
               has_ratpoints_cpu,has_ratpoints_gpu,code_fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (node_id, _digest(secret), hostname, str(details.get("platform") or "")[:120],
             max(1, min(512, int(details.get("cpu_cores") or 1))),
             max(1, min(32, int(details.get("slots") or 1))),
             int(bool(details.get("has_gpu"))), str(details.get("version") or "")[:40],
             now, now, priority,
             int(bool(details.get("has_ratpoints_cpu"))),
             int(bool(details.get("has_ratpoints_gpu"))),
             str(details.get("code_fingerprint") or "")[:64]),
        )
        db.commit()
    except BaseException:
        db.rollback()
        raise
    if (str(details.get("code_fingerprint") or "") == code_fingerprint()
            and bool(details.get("has_ratpoints_cpu"))
            and db.execute(
                "SELECT 1 FROM node_settings WHERE key='full_jobs_enabled'"
            ).fetchone() is None):
        set_full_jobs_enabled(db, True)
    return {"node_id": node_id, "secret": secret, "protocol": PROTOCOL}


def authenticated_node(db, node_id, secret):
    if not isinstance(node_id, str) or not isinstance(secret, str):
        return None
    row = db.execute("SELECT * FROM nodes WHERE id=? AND revoked_at IS NULL", (node_id,)).fetchone()
    if row is None or not hmac.compare_digest(row["secret_digest"], _digest(secret)):
        return None
    return row


def heartbeat(db, node_id, details):
    now = utc_now().isoformat()
    db.execute(
        """UPDATE nodes SET last_seen=?,hostname=?,platform=?,cpu_cores=?,
           slots=?,has_gpu=?,version=?,has_ratpoints_cpu=?,has_ratpoints_gpu=?,
           code_fingerprint=? WHERE id=?""",
        (now, str(details.get("hostname") or "Worker")[:120],
         str(details.get("platform") or "")[:120],
         max(1, min(512, int(details.get("cpu_cores") or 1))),
         max(1, min(32, int(details.get("slots") or 1))),
         int(bool(details.get("has_gpu"))), str(details.get("version") or "")[:40],
         int(bool(details.get("has_ratpoints_cpu"))),
         int(bool(details.get("has_ratpoints_gpu"))),
         str(details.get("code_fingerprint") or "")[:64],
         node_id),
    )
    db.commit()


def list_nodes(db):
    now = utc_now()
    rows = []
    for row in db.execute("SELECT * FROM nodes WHERE revoked_at IS NULL ORDER BY priority,joined_at"):
        rec = dict(row)
        try:
            age = (now - datetime.fromisoformat(rec["last_seen"])).total_seconds()
        except (ValueError, TypeError):
            age = float("inf")
        rec["online"] = age <= NODE_STALE_SECONDS
        rec["age_seconds"] = max(0, age)
        rec["active_tasks"] = int(db.execute(
            "SELECT COUNT(*) FROM tasks WHERE node_id=? AND status='running'", (rec["id"],)
        ).fetchone()[0])
        rec.pop("secret_digest", None)
        rows.append(rec)
    return rows


def set_default_worker(db, node_id):
    """Put one worker first while preserving the remaining fallback order."""
    rows = db.execute(
        "SELECT id FROM nodes WHERE revoked_at IS NULL ORDER BY priority,joined_at"
    ).fetchall()
    ids = [str(row["id"]) for row in rows]
    if node_id not in ids:
        raise ValueError("worker is not paired")
    ordered = [node_id, *(item for item in ids if item != node_id)]
    db.execute("BEGIN IMMEDIATE")
    try:
        for index, item in enumerate(ordered):
            db.execute("UPDATE nodes SET priority=? WHERE id=?", (index * 100, item))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return ordered


def remote_search_enabled(db):
    row = db.execute(
        "SELECT value_json FROM node_settings WHERE key='remote_search_enabled'"
    ).fetchone()
    return bool(json.loads(row[0])) if row else False


def set_remote_search_enabled(db, enabled):
    db.execute(
        """INSERT INTO node_settings(key,value_json)
           VALUES('remote_search_enabled',?)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json""",
        (json.dumps(bool(enabled)),),
    )
    db.commit()


def full_jobs_enabled(db):
    row = db.execute(
        "SELECT value_json FROM node_settings WHERE key='full_jobs_enabled'"
    ).fetchone()
    return bool(json.loads(row[0])) if row else False


def set_full_jobs_enabled(db, enabled):
    db.execute(
        """INSERT INTO node_settings(key,value_json) VALUES('full_jobs_enabled',?)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json""",
        (json.dumps(bool(enabled)),),
    )
    db.commit()


def revoke_node(db, node_id):
    now = utc_now().isoformat()
    db.execute("BEGIN IMMEDIATE")
    try:
        cur = db.execute(
            "UPDATE nodes SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
            (now, str(node_id)),
        )
        db.execute(
            """UPDATE tasks SET status='queued',node_id=NULL,started_at=NULL,
               lease_expires_at=NULL WHERE node_id=? AND status='running'""",
            (str(node_id),),
        )
        db.commit()
    except BaseException:
        db.rollback()
        raise
    if not db.execute(
        "SELECT 1 FROM nodes WHERE revoked_at IS NULL LIMIT 1"
    ).fetchone():
        set_remote_search_enabled(db, False)
        if full_jobs_enabled(db):
            db.execute("DELETE FROM node_settings WHERE key='full_jobs_enabled'")
            db.commit()
    return cur.rowcount == 1


def enqueue_task(db, kind, payload):
    if kind not in ALLOWED_TASKS:
        raise ValueError(f"unsupported remote task: {kind}")
    if kind in {"curve_invariants", "curve_point_search"}:
        ainvs = payload.get("a_invariants")
        if not isinstance(ainvs, list) or len(ainvs) != 5:
            raise ValueError("curve requires five a-invariants")
        requested_height = payload.get("height_limit")
        payload = {"a_invariants": [str(x) for x in ainvs],
                   "curve_id": int(payload["curve_id"]) if payload.get("curve_id") else None}
        if kind == "curve_point_search":
            height = float(requested_height or 0)
            if not 1 <= height <= 10:
                raise ValueError("point-search height limit must be between 1 and 10")
            payload["height_limit"] = height
    elif kind == "ratpoints_scan":
        coefficients = payload.get("coefficients")
        if not isinstance(coefficients, list) or not 2 <= len(coefficients) <= 8:
            raise ValueError("ratpoints task requires 2–8 polynomial coefficients")
        from fractions import Fraction
        coefficients = [str(Fraction(str(value))) for value in coefficients]
        height = int(payload.get("height") or 0)
        timeout = int(payload.get("timeout") or 0)
        if not 1 <= height <= 10**15 or not 1 <= timeout <= 86400:
            raise ValueError("ratpoints task has an invalid height or timeout")
        low = payload.get("denominator_low")
        high = payload.get("denominator_high")
        low = int(low) if low is not None else None
        high = int(high) if high is not None else None
        if (low is not None and low < 1) or (high is not None and high < 1):
            raise ValueError("denominator bounds must be positive")
        if low is not None and high is not None and low > high:
            raise ValueError("denominator bounds are reversed")
        backend = str(payload.get("backend") or "CPU").upper()
        if backend not in {"CPU", "GPU"}:
            raise ValueError("ratpoints backend must be CPU or GPU")
        extra_args = payload.get("extra_args") or []
        if not isinstance(extra_args, list) or len(extra_args) > 24:
            raise ValueError("invalid ratpoints extra arguments")
        extra_args = [str(value) for value in extra_args]
        if any(len(value) > 120 for value in extra_args):
            raise ValueError("ratpoints argument is too long")
        payload = {
            "coefficients": coefficients, "height": height, "timeout": timeout,
            "denominator_low": low, "denominator_high": high,
            "one_point": bool(payload.get("one_point")), "backend": backend,
            "extra_args": extra_args,
        }
    elif kind == "ui_job":
        if not isinstance(payload.get("job_id"), int) or payload["job_id"] < 1:
            raise ValueError("remote UI job requires a job ID")
        if not isinstance(payload.get("db_path"), str):
            raise ValueError("remote UI job requires a database path")
        command = payload.get("command")
        if (not isinstance(command, list) or len(command) > 64
                or any(not isinstance(part, str) or len(part) > 4096 for part in command)):
            raise ValueError("remote UI job requires a bounded command")
        module_args = command[2:4] if command[1:2] == ["-u"] else command[1:3]
        if module_args != ["-m", "rank42.pipeline_runner"]:
            raise ValueError("only Pipeline search jobs can run remotely")
        payload = {
            "job_id": payload["job_id"], "db_path": payload["db_path"],
            "project_root": str(payload["project_root"]),
            "log_path": str(payload["log_path"]),
            "command": command,
            "code_fingerprint": code_fingerprint(),
            "backend": str(payload.get("backend") or "CPU").upper(),
        }
        if payload["backend"] not in {"CPU", "GPU"}:
            raise ValueError("remote Pipeline backend must be CPU or GPU")
    now = utc_now().isoformat()
    cur = db.execute(
        "INSERT INTO tasks(kind,payload_json,status,created_at) VALUES(?,?,'queued',?)",
        (kind, json.dumps(payload, separators=(",", ":")), now),
    )
    db.commit()
    return int(cur.lastrowid)


def claim_task(db, node_id):
    now = utc_now().isoformat()
    lease = (utc_now() + timedelta(seconds=TASK_LEASE_SECONDS)).isoformat()
    db.execute("BEGIN IMMEDIATE")
    try:
        # A dead worker's task may be retried. Results from its old lease are rejected.
        db.execute(
            """UPDATE tasks SET status='queued',node_id=NULL,started_at=NULL,
               lease_expires_at=NULL WHERE status='running' AND lease_expires_at<?""",
            (now,),
        )
        # Pull requests are ordered by controller policy, not arrival timing:
        # the preferred workhorse gets a free slot first, then the next worker.
        cutoff = (utc_now() - timedelta(seconds=NODE_STALE_SECONDS)).isoformat()
        pending_rows = db.execute(
            "SELECT id,kind,payload_json FROM tasks WHERE status='queued' ORDER BY id"
        ).fetchall()
        if not pending_rows:
            db.commit()
            return None
        eligible = db.execute(
            """SELECT n.id,n.slots,n.has_gpu,n.has_ratpoints_cpu,
               n.has_ratpoints_gpu,n.code_fingerprint,COUNT(t.id) AS active
               FROM nodes n LEFT JOIN tasks t ON t.node_id=n.id AND t.status='running'
               WHERE n.revoked_at IS NULL AND n.last_seen>=?
               GROUP BY n.id ORDER BY n.priority,n.joined_at""",
            (cutoff,),
        ).fetchall()
        row = None
        for pending in pending_rows:
            requires_gpu = (
                pending["kind"] in {"ratpoints_scan", "ui_job"}
                and json.loads(pending["payload_json"]).get("backend") == "GPU"
            )
            requires_cpu_ratpoints = (
                pending["kind"] == "ratpoints_scan" and not requires_gpu
            )
            requires_code = (
                json.loads(pending["payload_json"]).get("code_fingerprint")
                if pending["kind"] == "ui_job" else None
            )
            requires_full_job = pending["kind"] == "ui_job"
            first_free = next(
                (str(item["id"]) for item in eligible
                 if int(item["active"]) < int(item["slots"])
                 and (not requires_gpu or bool(item["has_gpu"] and item["has_ratpoints_gpu"]))
                 and (not requires_cpu_ratpoints or bool(item["has_ratpoints_cpu"]))
                 and (not requires_full_job or bool(item["has_ratpoints_cpu"]))
                 and (not requires_code or item["code_fingerprint"] == requires_code)),
                None,
            )
            if first_free is None:
                continue
            if first_free == node_id:
                row = pending
                break
            caller = next((item for item in eligible if item["id"] == node_id), None)
            caller_incapable = bool(
                caller is not None and (
                    (requires_gpu and not bool(caller["has_gpu"] and caller["has_ratpoints_gpu"]))
                    or (requires_cpu_ratpoints and not bool(caller["has_ratpoints_cpu"]))
                    or (requires_full_job and not bool(caller["has_ratpoints_cpu"]))
                    or (requires_code and caller["code_fingerprint"] != requires_code)
                )
            )
            if not caller_incapable:
                break
        if row is None:
            db.commit()
            return None
        if row is not None:
            db.execute(
                """UPDATE tasks SET status='running',node_id=?,started_at=?,
                   lease_expires_at=? WHERE id=?""",
                (node_id, now, lease, row["id"]),
            )
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return dict(row) if row else None


def renew_task(db, node_id, task_id):
    """Extend ownership only for the currently assigned running worker."""
    lease = (utc_now() + timedelta(seconds=TASK_LEASE_SECONDS)).isoformat()
    cur = db.execute(
        """UPDATE tasks SET lease_expires_at=? WHERE id=? AND node_id=?
           AND status='running' AND lease_expires_at>=?""",
        (lease, int(task_id), str(node_id), utc_now().isoformat()),
    )
    db.commit()
    return cur.rowcount == 1


def validate_curve_invariants(payload, result):
    """Independently check the worker's exact invariant identities."""
    a = [Fraction(str(x)) for x in payload["a_invariants"]]
    if len(a) != 5:
        raise ValueError("five a-invariants required")
    a1, a2, a3, a4, a6 = a
    b2 = a1 * a1 + 4 * a2
    b4 = a1 * a3 + 2 * a4
    b6 = a3 * a3 + 4 * a6
    b8 = a1 * a1 * a6 + 4 * a2 * a6 - a1 * a3 * a4 + a2 * a3 * a3 - a4 * a4
    c4 = b2 * b2 - 24 * b4
    c6 = -b2**3 + 36 * b2 * b4 - 216 * b6
    discriminant = -b2 * b2 * b8 - 8 * b4**3 - 27 * b6**2 + 9 * b2 * b4 * b6
    if discriminant == 0:
        raise ValueError("singular curve")
    expected = {
        "c4": c4, "c6": c6,
        "discriminant": discriminant, "j_invariant": c4**3 / discriminant,
    }
    if [Fraction(str(x)) for x in result.get("a_invariants", [])] != a:
        raise ValueError("worker returned a different curve")
    if result.get("curve_id") != payload.get("curve_id"):
        raise ValueError("worker returned a different curve ID")
    for key, value in expected.items():
        if Fraction(str(result.get(key))) != value:
            raise ValueError(f"worker returned an incorrect {key}")


def validate_curve_points(payload, result):
    if result.get("curve_id") != payload.get("curve_id"):
        raise ValueError("worker returned a different curve ID")
    if float(result.get("height_limit")) != float(payload.get("height_limit")):
        raise ValueError("worker returned a different height limit")
    a = [Fraction(str(x)) for x in payload["a_invariants"]]
    if [Fraction(str(x)) for x in result.get("a_invariants", [])] != a:
        raise ValueError("worker returned a different curve")
    points = result.get("points")
    if not isinstance(points, list) or len(points) > 64:
        raise ValueError("invalid point list")
    seen = set()
    a1, a2, a3, a4, a6 = a
    for pair in points:
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError("invalid point coordinates")
        x, y = Fraction(str(pair[0])), Fraction(str(pair[1]))
        if y*y + a1*x*y + a3*y != x**3 + a2*x*x + a4*x + a6:
            raise ValueError("worker returned a point outside the curve")
        if (x, y) in seen:
            raise ValueError("worker returned duplicate points")
        seen.add((x, y))


def validate_ratpoints_scan(payload, result):
    """Check every worker hit against the exact polynomial and projective data."""
    from rank42.ratpoints import evaluate_polynomial, normalize_polynomial
    coefficients = [Fraction(str(x)) for x in payload["coefficients"]]
    if [Fraction(str(x)) for x in result.get("coefficients", [])] != coefficients:
        raise ValueError("worker returned a different polynomial")
    if result.get("backend") != payload["backend"]:
        raise ValueError("worker returned a different backend")
    points = result.get("points")
    if not isinstance(points, list) or len(points) > 400:
        raise ValueError("invalid ratpoints result size")
    normalized = normalize_polynomial(coefficients)
    m = (normalized.degree + 1) // 2
    seen = set()
    for point in points:
        if not isinstance(point, dict):
            raise ValueError("invalid ratpoints result")
        x, y = Fraction(str(point.get("x"))), Fraction(str(point.get("y")))
        if y * y != evaluate_polynomial(coefficients, x):
            raise ValueError("worker returned a point outside the polynomial")
        triplet = point.get("projective")
        if not isinstance(triplet, list) or len(triplet) != 3:
            raise ValueError("missing ratpoints projective coordinates")
        a, Y, b = [int(value) for value in triplet]
        if b <= 0 or Fraction(a, b) != x or Fraction(Y, b**m * normalized.y_scale) != y:
            raise ValueError("inconsistent ratpoints projective coordinates")
        if (x, y) in seen:
            raise ValueError("duplicate ratpoints result")
        seen.add((x, y))


def finish_task(db, node_id, task_id, *, result=None, error=None):
    if result is not None and error is not None:
        raise ValueError("task cannot have both result and error")
    if result is None and not error:
        raise ValueError("task requires a result or an error")
    if result is not None and not isinstance(result, dict):
        raise ValueError("result must be an object")
    row = db.execute(
        "SELECT kind,payload_json FROM tasks WHERE id=? AND node_id=? AND status='running'",
        (int(task_id), node_id),
    ).fetchone()
    if row is None:
        return False
    if result is not None and row["kind"] in ALLOWED_TASKS:
        try:
            payload = json.loads(row["payload_json"])
            if row["kind"] == "ui_job":
                if result.get("job_id") != payload["job_id"]:
                    raise ValueError("worker returned a different job ID")
                if not isinstance(result.get("exit_code"), int):
                    raise ValueError("worker did not return a process exit code")
            elif row["kind"] == "curve_invariants":
                validate_curve_invariants(payload, result)
            elif row["kind"] == "curve_point_search":
                validate_curve_points(payload, result)
            else:
                validate_ratpoints_scan(payload, result)
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
            result, error = None, f"remote result failed exact verification: {exc}"
    status = "failed" if error else "succeeded"
    cur = db.execute(
        """UPDATE tasks SET status=?,result_json=?,error=?,finished_at=?,
           lease_expires_at=NULL WHERE id=? AND node_id=? AND status='running'
           AND lease_expires_at>=?""",
        (status, json.dumps(result) if result is not None else None,
         str(error)[:1200] if error else None, utc_now().isoformat(),
         int(task_id), node_id, utc_now().isoformat()),
    )
    db.commit()
    return cur.rowcount == 1


def list_tasks(db, *, limit=30):
    return [dict(row) for row in db.execute(
        """SELECT t.id,t.kind,t.payload_json,t.status,t.node_id,t.result_json,
           t.error,t.created_at,t.finished_at,n.hostname AS node_name
           FROM tasks t LEFT JOIN nodes n ON n.id=t.node_id
           ORDER BY t.id DESC LIMIT ?""", (int(limit),)
    )]


def get_task(db, task_id):
    row = db.execute("SELECT * FROM tasks WHERE id=?", (int(task_id),)).fetchone()
    return dict(row) if row is not None else None


def cancel_task(db, task_id):
    cur = db.execute(
        """UPDATE tasks SET status='cancelled',finished_at=?,lease_expires_at=NULL
           WHERE id=? AND status IN ('queued','running')""",
        (utc_now().isoformat(), int(task_id)),
    )
    db.commit()
    return cur.rowcount == 1


def cancel_ui_job_tasks(project_root, job_id):
    path = registry_path(project_root)
    if not path.exists():
        return 0
    db = connect_registry(project_root)
    cancelled = 0
    try:
        rows = db.execute(
            "SELECT id,payload_json FROM tasks WHERE kind='ui_job' "
            "AND status IN ('queued','running')"
        ).fetchall()
        for row in rows:
            try:
                matching = int(json.loads(row["payload_json"])["job_id"]) == int(job_id)
            except (KeyError, ValueError, TypeError):
                matching = False
            if matching:
                cancelled += int(cancel_task(db, row["id"]))
    finally:
        db.close()
    return cancelled


def controller_port():
    try:
        port = int(os.environ.get("RANK_HUNTER_NODE_PORT", DEFAULT_PORT))
    except ValueError:
        port = DEFAULT_PORT
    if not 1 <= port <= 65535:
        raise ValueError("RANK_HUNTER_NODE_PORT must be a TCP port")
    return port


def controller_bind(project_root=None):
    override = os.environ.get("RANK_HUNTER_NODE_BIND")
    if override:
        return override
    if project_root is not None:
        path = Path(project_root).resolve() / ".rank42-ui" / "node-controller.json"
        try:
            setting = json.loads(path.read_text(encoding="utf-8"))
            if setting.get("private_network_enabled") is True:
                return "0.0.0.0"
        except (OSError, ValueError, TypeError):
            pass
    return "127.0.0.1"


def controller_info(project_root, port=None):
    port = port or controller_port()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5) as res:
            data = json.load(res)
        instance = hashlib.sha256(str(Path(project_root).resolve()).encode()).hexdigest()[:16]
        return data if data.get("protocol") == PROTOCOL and data.get("instance") == instance else None
    except (OSError, ValueError, urllib.error.URLError):
        return None


def controller_healthy(project_root, port=None):
    return controller_info(project_root, port) is not None


def set_private_network(project_root, enabled):
    """Change network exposure and restart only this controller instance."""
    if os.environ.get("RANK_HUNTER_NODE_BIND"):
        raise ValueError("RANK_HUNTER_NODE_BIND is controlled by the launcher")
    root = Path(project_root).resolve()
    path = root / ".rank42-ui" / "node-controller.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"private_network_enabled": bool(enabled)}), encoding="utf-8")
    os.replace(tmp, path)
    info = controller_info(root)
    desired = "0.0.0.0" if enabled else "127.0.0.1"
    if info and info.get("bind") != desired:
        os.kill(int(info["pid"]), signal.SIGTERM)
        for _ in range(30):
            if controller_info(root) is None:
                break
            time.sleep(0.1)
    return ensure_controller_running(root)


def ensure_controller_running(project_root):
    if controller_healthy(project_root):
        return True
    root = Path(project_root).resolve()
    log = root / ".rank42-ui" / "node-controller.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as out:
        subprocess.Popen(
            [sys.executable, "-m", "rank42.node_controller", "--project-root", str(root),
             "--bind", controller_bind(root), "--port", str(controller_port())],
            cwd=root, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    for _ in range(15):
        if controller_healthy(root):
            return True
        time.sleep(0.1)
    return False
