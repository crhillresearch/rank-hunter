import pytest

from rank42.node_cluster import (
    authenticated_node, claim_task, code_fingerprint, connect_registry, create_pair_code,
    enqueue_task, finish_task, list_nodes, list_tasks, pair_node, revoke_node,
    full_jobs_enabled, renew_task, set_default_worker,
    validate_curve_invariants, validate_curve_points,
    validate_ratpoints_scan,
)


def test_pairing_is_single_use_and_revocable(tmp_path):
    db = connect_registry(tmp_path)
    try:
        code = create_pair_code(db)
        identity = pair_node(db, code, {"hostname": "worker-a", "slots": 2})
        assert authenticated_node(db, identity["node_id"], identity["secret"])
        assert len(list_nodes(db)) == 1
        with pytest.raises(PermissionError):
            pair_node(db, code, {"hostname": "second attempt"})
        assert revoke_node(db, identity["node_id"])
        assert authenticated_node(db, identity["node_id"], identity["secret"]) is None
        assert list_nodes(db) == []
    finally:
        db.close()


def test_remote_point_result_requires_exact_curve_membership(tmp_path):
    db = connect_registry(tmp_path)
    try:
        identity = pair_node(db, create_pair_code(db), {"hostname": "worker-a"})
        node_id = identity["node_id"]
        payload = {
            "curve_id": 37, "a_invariants": ["0", "0", "1", "-1", "0"],
            "height_limit": 5.0,
        }
        task_id = enqueue_task(db, "curve_point_search", payload)
        claim_task(db, node_id)
        result = {**payload, "points": [["0", "0"]]}
        validate_curve_points(payload, result)
        assert finish_task(db, node_id, task_id, result=result)
        assert list_tasks(db)[0]["status"] == "succeeded"
        bad_id = enqueue_task(db, "curve_point_search", payload)
        claim_task(db, node_id)
        assert finish_task(db, node_id, bad_id, result={**payload, "points": [["0", "1"]]})
        assert list_tasks(db)[0]["status"] == "failed"
    finally:
        db.close()


def test_remote_result_is_checked_before_success(tmp_path):
    db = connect_registry(tmp_path)
    try:
        identity = pair_node(db, create_pair_code(db), {"hostname": "worker-a"})
        node_id = identity["node_id"]
        payload = {"curve_id": 11, "a_invariants": ["0", "0", "0", "-1", "0"]}
        task_id = enqueue_task(db, "curve_invariants", payload)
        assert claim_task(db, node_id)["id"] == task_id
        result = {
            **payload, "c4": "48", "c6": "0", "discriminant": "64",
            "j_invariant": "1728",
        }
        validate_curve_invariants(payload, result)
        assert finish_task(db, node_id, task_id, result=result)
        assert list_tasks(db)[0]["status"] == "succeeded"

        bad_id = enqueue_task(db, "curve_invariants", payload)
        assert claim_task(db, node_id)["id"] == bad_id
        assert finish_task(db, node_id, bad_id, result={**result, "j_invariant": "0"})
        bad = list_tasks(db)[0]
        assert bad["status"] == "failed"
        assert "incorrect j_invariant" in bad["error"]
        assert bad["result_json"] is None
    finally:
        db.close()


def test_default_worker_claims_first_then_overflows(tmp_path):
    db = connect_registry(tmp_path)
    try:
        first = pair_node(db, create_pair_code(db), {"hostname": "workhorse", "slots": 1})
        second = pair_node(db, create_pair_code(db), {"hostname": "fallback", "slots": 1})
        payload = {"curve_id": 1, "a_invariants": ["0", "0", "0", "-1", "0"]}
        a = enqueue_task(db, "curve_invariants", payload)
        b = enqueue_task(db, "curve_invariants", payload)
        assert claim_task(db, second["node_id"]) is None
        assert claim_task(db, first["node_id"])["id"] == a
        assert renew_task(db, first["node_id"], a)
        assert not renew_task(db, second["node_id"], a)
        assert claim_task(db, second["node_id"])["id"] == b
        assert [node["hostname"] for node in list_nodes(db)] == ["workhorse", "fallback"]
        assert set_default_worker(db, second["node_id"])[0] == second["node_id"]
        assert [node["hostname"] for node in list_nodes(db)] == ["fallback", "workhorse"]
    finally:
        db.close()


def test_ratpoints_result_requires_exact_polynomial_and_projective_data():
    payload = {
        "coefficients": ["0", "-1", "0", "1"],
        "backend": "CPU",
    }
    result = {
        **payload,
        "points": [{"x": "-1", "y": "0", "projective": ["-1", "0", "1"]}],
    }
    validate_ratpoints_scan(payload, result)
    with pytest.raises(ValueError, match="outside the polynomial"):
        validate_ratpoints_scan(payload, {
            **result, "points": [{"x": "-1", "y": "1", "projective": ["-1", "1", "1"]}],
        })
    with pytest.raises(ValueError, match="inconsistent ratpoints"):
        validate_ratpoints_scan(payload, {
            **result, "points": [{"x": "-1", "y": "0", "projective": ["0", "0", "1"]}],
        })


def test_ratpoints_workhorse_falls_through_to_next_capable_worker(tmp_path):
    db = connect_registry(tmp_path)
    try:
        first = pair_node(db, create_pair_code(db), {
            "hostname": "first", "slots": 1, "has_ratpoints_cpu": True,
        })
        second = pair_node(db, create_pair_code(db), {
            "hostname": "second", "slots": 1, "has_ratpoints_cpu": True,
        })
        payload = {
            "coefficients": ["0", "-1", "0", "1"], "height": 10,
            "timeout": 10, "backend": "CPU",
        }
        a = enqueue_task(db, "ratpoints_scan", payload)
        b = enqueue_task(db, "ratpoints_scan", payload)
        assert claim_task(db, second["node_id"]) is None
        assert claim_task(db, first["node_id"])["id"] == a
        assert claim_task(db, second["node_id"])["id"] == b
    finally:
        db.close()


def test_full_pipeline_fills_preferred_worker_then_fallback(tmp_path):
    db = connect_registry(tmp_path)
    try:
        details = {"slots": 1, "code_fingerprint": code_fingerprint(),
                   "has_ratpoints_cpu": True}
        first = pair_node(db, create_pair_code(db), {**details, "hostname": "preferred"})
        assert full_jobs_enabled(db)
        second = pair_node(db, create_pair_code(db), {**details, "hostname": "overflow"})
        payload = {
            "job_id": 1, "db_path": str(tmp_path / "rank42.db"),
            "project_root": str(tmp_path), "log_path": str(tmp_path / "job.log"),
            "command": ["python", "-m", "rank42.pipeline_runner", "--run-id", "1"],
        }
        a = enqueue_task(db, "ui_job", payload)
        b = enqueue_task(db, "ui_job", {**payload, "job_id": 2})
        c = enqueue_task(db, "ui_job", {
            **payload, "job_id": 3,
            "command": ["python", "-u", "-m", "rank42.pipeline_runner", "--run-id", "3"],
        })
        assert claim_task(db, second["node_id"]) is None
        assert claim_task(db, first["node_id"])["id"] == a
        assert claim_task(db, second["node_id"])["id"] == b
        assert c > b
    finally:
        db.close()


def test_gpu_pipeline_skips_cpu_only_default_worker(tmp_path):
    db = connect_registry(tmp_path)
    try:
        details = {"slots": 1, "code_fingerprint": code_fingerprint(),
                   "has_ratpoints_cpu": True}
        cpu = pair_node(db, create_pair_code(db), {**details, "hostname": "cpu"})
        gpu = pair_node(db, create_pair_code(db), {
            **details, "hostname": "gpu", "has_gpu": True,
            "has_ratpoints_gpu": True,
        })
        task_id = enqueue_task(db, "ui_job", {
            "job_id": 5, "db_path": str(tmp_path / "rank42.db"),
            "project_root": str(tmp_path), "log_path": str(tmp_path / "job.log"),
            "command": ["python", "-m", "rank42.pipeline_runner", "--run-id", "5"],
            "backend": "GPU",
        })
        assert claim_task(db, cpu["node_id"]) is None
        assert claim_task(db, gpu["node_id"])["id"] == task_id
    finally:
        db.close()
