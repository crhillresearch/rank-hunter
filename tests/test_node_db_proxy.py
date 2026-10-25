"""Remote job database semantics needed by Search and Pipelines."""

import sqlite3
import base64
import hashlib

import pytest

from rank42.node_db_proxy import SessionStore, local_test_connection
from rank42.node_controller import _accept_job_file
from rank42.node_cluster import cancel_ui_job_tasks, connect_registry, enqueue_task
from rank42.node_worker import _upload_job_outputs


def test_remote_connection_preserves_rows_transactions_and_insert_ids(tmp_path):
    path = tmp_path / "job.db"
    store = SessionStore()
    db = local_test_connection(store, "worker-a", path)
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT, raw BLOB)")
    inserted = db.execute("INSERT INTO items(name,raw) VALUES(?,?)", ("one", b"a\x00b"))
    assert inserted.lastrowid == 1
    assert db.in_transaction
    db.commit()
    assert not db.in_transaction
    assert db.total_changes == 1
    db.executemany("INSERT INTO items(name) VALUES(?)", [("two",), ("three",)])
    db.rollback()
    rows = db.execute("SELECT id,name,raw FROM items ORDER BY id").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == rows[0]["id"] == 1
    assert tuple(rows[0]) == (1, "one", b"a\x00b")
    assert dict(rows[0]) == {"id": 1, "name": "one", "raw": b"a\x00b"}
    inserted_many = db.executemany(
        "INSERT INTO items(name) VALUES(?)", [(f"row-{i}",) for i in range(600)]
    )
    assert inserted_many.rowcount == 600
    db.rollback()
    db.close()


def test_database_session_rejects_other_worker(tmp_path):
    store = SessionStore()
    token = store.open("worker-a", tmp_path / "job.db")
    with pytest.raises(PermissionError):
        store.call("worker-b", token, "info", {})
    store.close("worker-a", token)


def test_job_paths_are_stored_on_controller_and_seen_locally_on_worker(tmp_path, monkeypatch):
    controller = str(tmp_path / "controller")
    worker = str(tmp_path / "worker")
    monkeypatch.setenv("RANK_HUNTER_REMOTE_WORKSPACE", worker)
    monkeypatch.setenv("RANK_HUNTER_CONTROLLER_ROOT", controller)
    path = tmp_path / "paths.db"
    store = SessionStore()
    db = local_test_connection(store, "worker-a", path)
    db.execute("CREATE TABLE files(path TEXT)")
    db.execute("INSERT INTO files(path) VALUES(?)", (worker + "/runs/result.json",))
    db.commit()
    assert db.execute("SELECT path FROM files").fetchone()[0] == worker + "/runs/result.json"
    db.close()
    native = sqlite3.connect(path)
    assert native.execute("SELECT path FROM files").fetchone()[0] == controller + "/runs/result.json"
    native.close()


def test_stopping_parent_job_cancels_remote_task(tmp_path):
    registry = connect_registry(tmp_path)
    task_id = enqueue_task(registry, "ui_job", {
        "job_id": 42, "db_path": str(tmp_path / "rank42.db"),
        "project_root": str(tmp_path), "log_path": str(tmp_path / "job.log"),
        "command": ["python", "-m", "rank42.pipeline_runner", "--run-id", "1"],
    })
    registry.close()
    assert cancel_ui_job_tasks(tmp_path, 42) == 1
    registry = connect_registry(tmp_path)
    assert registry.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()[0] == "cancelled"
    registry.close()


def test_remote_output_is_verified_and_promoted_to_controller(tmp_path):
    raw = b"exact construction\n"
    assert _accept_job_file(tmp_path, 7, {
        "path": "runs/node-test/construction.txt", "offset": 0,
        "chunk": base64.b64encode(raw).decode(), "finished": True,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }) == {"ok": True}
    assert (tmp_path / "runs/node-test/construction.txt").read_bytes() == raw
    with pytest.raises(ValueError, match="invalid private system artifact path"):
        _accept_job_file(tmp_path, 7, {
            "path": ".rank42-ui/openai-api-key", "offset": 0,
            "chunk": "", "finished": True,
            "sha256": hashlib.sha256(b"").hexdigest(),
        })


def test_worker_returns_new_files_and_modified_family_plugin(tmp_path, monkeypatch):
    controller = tmp_path / "controller"
    workspace = tmp_path / "worker"
    original = controller / "plugins/families/test/family.json"
    original.parent.mkdir(parents=True)
    original.write_text("old")
    modified = workspace / "plugins/families/test/family.json"
    modified.parent.mkdir(parents=True)
    modified.write_text("new")
    result = workspace / "runs/result.txt"
    result.parent.mkdir(parents=True)
    result.write_text("rank growth")
    (workspace / "rank42.db").touch()
    from rank42 import node_worker
    monkeypatch.setattr(node_worker, "_request", lambda _url, path, data, **_kwargs:
                        _accept_job_file(controller, 7, data) if path == "/job/file" else None)
    count = _upload_job_outputs(
        {"id": 7, "_controller_url": "local", "_node_id": "worker", "_secret": "secret"},
        workspace,
        {"plugins/families/test/family.json": hashlib.sha256(b"old").hexdigest()},
    )
    assert count == 2
    assert original.read_text() == "new"
    assert (controller / "runs/result.txt").read_text() == "rank growth"
