"""Deleting a saved model must preserve shared mathematical evidence."""

import json

import pytest

from rank42.db import connect, now, upsert_curve
from rank42.genus2_registry import (
    delete_genus2_model, get_genus2_model, register_genus2_model,
)


def test_delete_model_blocks_active_work_and_preserves_shared_points(tmp_path):
    db = connect(tmp_path / "rank42.db")
    source = upsert_curve(db, family="source", parameter="1")
    coefficients = ["1", "0", "-1", "0", "0", "0", "1"]
    first = register_genus2_model(db, label="First model", coefficients=coefficients)
    second = register_genus2_model(
        db, label="Second model", coefficients=coefficients,
        source_curve_id=source,
    )
    fingerprint = get_genus2_model(db, first)["model_fingerprint_sha256"]
    db.execute(
        """INSERT INTO genus2_rational_points
           (model_fingerprint_sha256,x,y,source,created_at) VALUES(?,?,?,?,?)""",
        (fingerprint, "0", "1", "test", now()),
    )
    run = db.execute(
        """INSERT INTO search_pipeline_runs
           (pipeline_name,target_mode,target_json,stages_json,status,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?)""",
        ("test", "genus2_jacobian", json.dumps({"genus2_model_id": first}),
         "[]", "running", now(), now()),
    )
    db.commit()
    with pytest.raises(ValueError, match="active work"):
        delete_genus2_model(db, first, expected_label="First model")
    assert get_genus2_model(db, first) is not None
    db.execute("UPDATE search_pipeline_runs SET status='completed' WHERE id=?", (run.lastrowid,))
    db.commit()
    assert delete_genus2_model(db, first, expected_label="First model")
    assert get_genus2_model(db, first) is None
    assert get_genus2_model(db, second) is not None
    assert db.execute(
        "SELECT COUNT(*) FROM genus2_rational_points WHERE model_fingerprint_sha256=?",
        (fingerprint,),
    ).fetchone()[0] == 1
    db.close()
