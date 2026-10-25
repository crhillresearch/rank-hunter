"""Persistent, exact Genus 2 models and their elliptic-source provenance."""
from __future__ import annotations

import json

from rank42.db import get_curve, now
from rank42.genus2_pipeline import audit_model, double_cover_from_elliptic
from rank42.curve_research_state import get_curve_research_state
from rank42.pipeline_state import ensure_pipeline_schema


def list_genus2_models(db):
    ensure_pipeline_schema(db)
    return db.execute(
        "SELECT * FROM genus2_models ORDER BY id DESC"
    ).fetchall()


def get_genus2_model(db, model_id):
    ensure_pipeline_schema(db)
    return db.execute(
        "SELECT * FROM genus2_models WHERE id=?", (int(model_id),)
    ).fetchone()


def active_genus2_model_work(db, model_id):
    """Return active jobs and pipeline runs that target a saved model ID."""
    ensure_pipeline_schema(db)
    wanted = int(model_id)
    active = []
    runs = db.execute(
        """SELECT id,target_json FROM search_pipeline_runs
           WHERE target_mode='genus2_jacobian'
             AND status IN ('queued','starting','running','stopping','paused')"""
    ).fetchall()
    for run in runs:
        try:
            target = json.loads(run["target_json"] or "{}")
            if int(target.get("genus2_model_id")) == wanted:
                active.append(f"Pipeline run #{int(run['id'])}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    has_jobs = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ui_jobs'"
    ).fetchone()
    if has_jobs:
        jobs = db.execute(
            """SELECT id,metadata_json FROM ui_jobs
               WHERE status IN ('queued','starting','running','stopping','paused')"""
        ).fetchall()
        for job in jobs:
            try:
                metadata = json.loads(job["metadata_json"] or "{}")
                if int(metadata.get("model_id")) == wanted:
                    active.append(f"Job #{int(job['id'])}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
    return active


def delete_genus2_model(db, model_id, *, expected_label):
    """Delete one saved model without deleting evidence shared by its equation."""
    row = get_genus2_model(db, model_id)
    if row is None:
        return False
    if str(row["label"]) != str(expected_label):
        raise ValueError("The model name changed. Reload and confirm the current name.")
    active = active_genus2_model_work(db, model_id)
    if active:
        raise ValueError("Finish or stop active work first: " + ", ".join(active))
    db.execute("DELETE FROM genus2_models WHERE id=?", (int(model_id),))
    db.commit()
    return True


def register_genus2_model(db, *, label, coefficients, source_curve_id=None,
                          construction=None):
    ensure_pipeline_schema(db)
    audit = audit_model(coefficients)
    if audit["status"] != "completed":
        raise ValueError(f"invalid genus-two model: {audit['reason']}")
    source_id = None if source_curve_id is None else int(source_curve_id)
    if source_id is not None and get_curve(db, source_id) is None:
        raise ValueError(f"source curve #{source_id} does not exist")
    fingerprint = audit["model_fingerprint_sha256"]
    existing = db.execute(
        """SELECT id FROM genus2_models
           WHERE model_fingerprint_sha256=? AND source_curve_id IS ?
           ORDER BY id LIMIT 1""",
        (fingerprint, source_id),
    ).fetchone()
    if existing is not None:
        return int(existing["id"])
    ts = now()
    cursor = db.execute(
        """INSERT INTO genus2_models(
               label,model_fingerprint_sha256,coefficients_json,source_curve_id,
               construction_json,created_at,updated_at
           ) VALUES(?,?,?,?,?,?,?)""",
        (
            str(label).strip() or f"Genus 2 model {fingerprint[:12]}",
            fingerprint,
            json.dumps(audit["coefficients_ascending"]),
            source_id,
            json.dumps(construction or {}, sort_keys=True),
            ts, ts,
        ),
    )
    db.commit()
    return int(cursor.lastrowid)


def register_double_cover_from_curve(db, *, curve_id, label=None):
    row = get_curve(db, int(curve_id))
    if row is None:
        raise ValueError(f"curve #{int(curve_id)} does not exist")
    try:
        a_invariants = json.loads(row["a_invariants_json"] or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError("source curve has invalid a-invariants") from exc
    if not a_invariants:
        raise ValueError("source curve has no Weierstrass model")
    construction = double_cover_from_elliptic(a_invariants)
    construction["source_curve_id"] = int(curve_id)
    construction["source_family"] = str(row["family"])
    construction["source_parameter"] = str(row["parameter"])
    return register_genus2_model(
        db,
        label=label or f"Double cover of curve #{int(curve_id)}",
        coefficients=construction["coefficients_ascending"],
        source_curve_id=int(curve_id),
        construction=construction,
    )


def elliptic_quotient_rank_certificate(db, *, model_id, coefficients):
    """Transfer a certified elliptic lower bound through a checked degree-two map."""
    model = get_genus2_model(db, model_id)
    if model is None:
        raise ValueError(f"Genus 2 model #{int(model_id)} does not exist")
    supplied = audit_model(coefficients)
    if supplied["status"] != "completed" or supplied["model_fingerprint_sha256"] != model["model_fingerprint_sha256"]:
        raise ValueError("pipeline target differs from its saved Genus 2 model")
    source_id = model["source_curve_id"]
    construction = json.loads(model["construction_json"] or "{}")
    if source_id is None or construction.get("construction") != "quadratic_base_change_of_short_weierstrass_x":
        return {"status": "completed", "proof_status": "no_elliptic_quotient", "rank_lower_bound": None}
    source = get_curve(db, int(source_id))
    if source is None:
        raise ValueError("the source elliptic curve is missing")
    current = double_cover_from_elliptic(json.loads(source["a_invariants_json"] or "[]"))
    if current["model_fingerprint_sha256"] != supplied["model_fingerprint_sha256"]:
        raise ValueError("saved double cover no longer matches its source elliptic curve")
    state = get_curve_research_state(db, int(source_id))
    lower = int(state["rigorous_lower"] or 0)
    if state.get("rank_inconsistent") or lower <= 0:
        return {
            "status": "completed", "proof_status": "source_rank_unavailable",
            "source_curve_id": int(source_id), "rank_lower_bound": None,
        }
    return {
        "status": "completed",
        "proof_status": "rigorous_rank_lower_bound",
        "source_curve_id": int(source_id),
        "source_rank_lower_bound": lower,
        "source_rank_state": {
            "status": str(source["status"]),
            "descent_lower": source["descent_lower"],
            "generic_lower": source["generic_lower"],
            "exact_rank": source["exact_rank"],
            "rigorous_lower": lower,
        },
        "rank_lower_bound": lower,
        "model_fingerprint_sha256": supplied["model_fingerprint_sha256"],
        "short_model": current["short_model"],
        "map_to_elliptic_short_model": {"X": "x^2", "Y": "y"},
        "map_degree": 2,
        "proof_method": (
            "The verified finite degree-two map C→E induces a surjection "
            "J(C)→E, so rank J(C) is at least the certified rank of E."
        ),
        "rank_upper_bound": None,
        "exact_rank": None,
    }
