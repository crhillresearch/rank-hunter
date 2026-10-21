"""Persistence helpers for Mordell--Weil lattices, half-cosets, coverings,
and extra-point screens.

The database stores *screens* and provenance.  A positive-definite numerical
height matrix is useful evidence for independence, but this module does not
upgrade it to a formal proof automatically.
"""

from __future__ import annotations

import hashlib
import json

from rank42.db import now


def _canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def lattice_key(*, curve_id, source, basis, precision_bits, family_spec=None):
    payload = {
        "curve_id": int(curve_id),
        "source": str(source),
        "family_spec": family_spec,
        "basis": basis,
        "precision_bits": int(precision_bits),
    }
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


def store_lattice(
    db,
    *,
    curve_id,
    source,
    basis,
    gram,
    precision_bits,
    determinant,
    min_eigenvalue,
    positive_definite_screen,
    family_spec=None,
    parameter=None,
    status="screened",
    metadata=None,
):
    key = lattice_key(
        curve_id=curve_id,
        source=source,
        basis=basis,
        precision_bits=precision_bits,
        family_spec=family_spec,
    )
    ts = now()
    db.execute(
        """
        INSERT INTO mw_lattices(
            lattice_key, curve_id, source, family_spec, parameter,
            basis_json, gram_json, precision_bits, basis_count,
            determinant, min_eigenvalue, positive_definite_screen,
            status, metadata_json, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(lattice_key) DO UPDATE SET
            gram_json=excluded.gram_json,
            determinant=excluded.determinant,
            min_eigenvalue=excluded.min_eigenvalue,
            positive_definite_screen=excluded.positive_definite_screen,
            status=excluded.status,
            metadata_json=excluded.metadata_json,
            updated_at=excluded.updated_at
        """,
        (
            key,
            int(curve_id),
            str(source),
            family_spec,
            parameter,
            _canonical_json(basis),
            _canonical_json(gram),
            int(precision_bits),
            len(basis),
            str(determinant) if determinant is not None else None,
            str(min_eigenvalue) if min_eigenvalue is not None else None,
            1 if positive_definite_screen else 0,
            str(status),
            _canonical_json(dict(metadata or {})),
            ts,
            ts,
        ),
    )
    db.commit()
    return db.execute("SELECT * FROM mw_lattices WHERE lattice_key=?", (key,)).fetchone()


def get_lattice(db, lattice_id):
    return db.execute("SELECT * FROM mw_lattices WHERE id=?", (int(lattice_id),)).fetchone()


def list_lattices(db, *, curve_id=None, limit=30):
    sql = "SELECT * FROM mw_lattices"
    vals = []
    if curve_id is not None:
        sql += " WHERE curve_id=?"
        vals.append(int(curve_id))
    sql += " ORDER BY id DESC LIMIT ?"
    vals.append(int(limit))
    return db.execute(sql, vals).fetchall()


def hole_key(bits):
    bits = "".join(str(int(x)) for x in bits)
    return hashlib.sha256(bits.encode()).hexdigest()


def store_holes(db, lattice_id, holes, *, method, metadata=None):
    ts = now()
    for rank_order, hole in enumerate(holes, 1):
        bits = str(hole["bits"])
        key = hole_key(bits)
        rec_meta = dict(metadata or {})
        rec_meta.update(dict(hole.get("metadata") or {}))
        db.execute(
            """
            INSERT INTO lattice_holes(
                lattice_id, hole_key, bits, representative_json, norm2,
                method, rank_order, status, metadata_json, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(lattice_id, hole_key) DO UPDATE SET
                representative_json=excluded.representative_json,
                norm2=excluded.norm2,
                method=excluded.method,
                rank_order=excluded.rank_order,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            (
                int(lattice_id),
                key,
                bits,
                _canonical_json([str(x) for x in hole["representative"]]),
                float(hole["norm2"]),
                str(method),
                rank_order,
                "new",
                _canonical_json(rec_meta),
                ts,
                ts,
            ),
        )
    db.commit()


def list_holes(db, lattice_id, *, limit=50, status=None):
    sql = "SELECT * FROM lattice_holes WHERE lattice_id=?"
    vals = [int(lattice_id)]
    if status is not None:
        sql += " AND status=?"
        vals.append(status)
    sql += " ORDER BY norm2 DESC, id ASC LIMIT ?"
    vals.append(int(limit))
    return db.execute(sql, vals).fetchall()


def get_hole(db, hole_id):
    return db.execute("SELECT * FROM lattice_holes WHERE id=?", (int(hole_id),)).fetchone()


def covering_key(data):
    payload = {
        "curve_id": int(data["curve_id"]),
        "lattice_id": int(data["lattice_id"]) if data.get("lattice_id") is not None else None,
        "hole_id": int(data["hole_id"]) if data.get("hole_id") is not None else None,
        "quartic": data["quartic"],
        "map": data["map"],
    }
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


def store_covering(db, data):
    key = covering_key(data)
    ts = now()
    db.execute(
        """
        INSERT INTO coverings(
            covering_key, curve_id, lattice_id, hole_id, schema_version,
            quartic_json, map_x, map_y, metadata_json, status,
            created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(covering_key) DO UPDATE SET
            metadata_json=excluded.metadata_json,
            updated_at=excluded.updated_at
        """,
        (
            key,
            int(data["curve_id"]),
            int(data["lattice_id"]) if data.get("lattice_id") is not None else None,
            int(data["hole_id"]) if data.get("hole_id") is not None else None,
            str(data["schema"]),
            _canonical_json(data["quartic"]),
            str(data["map"]["x"]),
            str(data["map"]["y"]),
            _canonical_json(dict(data.get("metadata") or {})),
            "ready",
            ts,
            ts,
        ),
    )
    db.commit()
    return db.execute("SELECT * FROM coverings WHERE covering_key=?", (key,)).fetchone()


def get_covering(db, covering_id):
    return db.execute("SELECT * FROM coverings WHERE id=?", (int(covering_id),)).fetchone()


def record_covering_search_attempt(
    db,
    *,
    covering_id,
    curve_id,
    pipeline_run_id,
    pipeline_stage_index,
    pipeline_stage_id,
    height,
    timeout_seconds,
    backend,
    one_point,
    outcome,
    ratpoints_hits=0,
    mapped_points=0,
    runtime_seconds=None,
    error=None,
    metadata=None,
):
    """Append one immutable covering-search attempt record."""
    cur = db.execute(
        """INSERT INTO covering_search_attempts(
               covering_id,curve_id,pipeline_run_id,pipeline_stage_index,
               pipeline_stage_id,height,timeout_seconds,backend,one_point,
               outcome,ratpoints_hits,mapped_points,runtime_seconds,error,
               metadata_json,created_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            int(covering_id),
            int(curve_id),
            None if pipeline_run_id is None else int(pipeline_run_id),
            (
                None
                if pipeline_stage_index is None
                else int(pipeline_stage_index)
            ),
            str(pipeline_stage_id),
            int(height),
            int(timeout_seconds),
            str(backend),
            1 if one_point else 0,
            str(outcome),
            int(ratpoints_hits or 0),
            int(mapped_points or 0),
            (
                None
                if runtime_seconds is None
                else float(runtime_seconds)
            ),
            None if error is None else str(error),
            _canonical_json(dict(metadata or {})),
            now(),
        ),
    )
    db.commit()
    return int(cur.lastrowid)


def list_covering_search_attempts(db, covering_id, *, limit=100):
    return db.execute(
        """SELECT * FROM covering_search_attempts
           WHERE covering_id=?
           ORDER BY id DESC LIMIT ?""",
        (int(covering_id), max(1, int(limit))),
    ).fetchall()


def completed_covering_search_attempt(
    db,
    *,
    covering_id,
    curve_id,
    pipeline_run_id,
    pipeline_stage_index,
    pipeline_stage_id,
    height,
    backend,
    one_point,
):
    """Return the latest matching completed covering branch for run resume.

    A completed search is reusable at the same height/backend/one-point scope
    even if a later retry raises the timeout budget: completion already means
    the bounded search answered before its previous deadline.
    """
    return db.execute(
        """SELECT * FROM covering_search_attempts
           WHERE covering_id=?
             AND curve_id=?
             AND pipeline_run_id=?
             AND pipeline_stage_index=?
             AND pipeline_stage_id=?
             AND height=?
             AND backend=?
             AND one_point=?
             AND outcome='completed'
           ORDER BY id DESC LIMIT 1""",
        (
            int(covering_id),
            int(curve_id),
            int(pipeline_run_id),
            int(pipeline_stage_index),
            str(pipeline_stage_id),
            int(height),
            str(backend),
            1 if one_point else 0,
        ),
    ).fetchone()


def update_covering(db, covering_id, *, status=None, quartic_search_id=None, error=None):
    fields = {"updated_at": now()}
    if status is not None:
        fields["status"] = status
    if quartic_search_id is not None:
        fields["quartic_search_id"] = int(quartic_search_id)
    if error is not None:
        fields["error"] = str(error)
    elif status in {"ready", "searched", "mapped", "reduced", "locally_obstructed"}:
        fields["error"] = None
    cols = ", ".join(f"{k}=?" for k in fields)
    db.execute(
        f"UPDATE coverings SET {cols} WHERE id=?",
        list(fields.values()) + [int(covering_id)],
    )
    db.commit()


def update_covering_metadata(db, covering_id, metadata):
    db.execute(
        "UPDATE coverings SET metadata_json=?,updated_at=? WHERE id=?",
        (_canonical_json(dict(metadata or {})), now(), int(covering_id)),
    )
    db.commit()


def store_extra_point(
    db,
    *,
    covering_id,
    quartic_point_id,
    curve_id,
    x,
    y,
    exact_verified,
    independence_screen,
    basis_count_before,
    basis_count_after,
    determinant=None,
    min_eigenvalue=None,
    metadata=None,
):
    """Store a mapped rational point in the unified v0.8 point ledger.

    Covering-specific provenance is retained in ``search_ref`` and metadata; no
    duplicate fresh-install ``extra_points`` table is maintained.
    """
    from rank42.points import upsert_point

    meta = dict(metadata or {})
    meta.update({
        "covering_id": int(covering_id) if covering_id is not None else None,
        "quartic_point_id": int(quartic_point_id) if quartic_point_id is not None else None,
        "basis_count_before": int(basis_count_before),
        "basis_count_after": int(basis_count_after),
        "determinant": str(determinant) if determinant is not None else None,
        "min_eigenvalue": str(min_eigenvalue) if min_eigenvalue is not None else None,
        "independence_screen": bool(independence_screen),
    })
    exact_status = str(meta.get("exact_status") or "")
    rigorous = exact_status == "certified_independent"
    if rigorous:
        ind_status = "rigorous_independent"
        role = "rigorous_witness"
    elif independence_screen:
        ind_status = "numerical_novel"
        role = "candidate_extra"
    elif exact_status == "dependent":
        ind_status = "dependent"
        role = "candidate_extra"
    else:
        ind_status = "unknown"
        role = "candidate_extra"
    return upsert_point(
        db, curve_id=curve_id, x=x, y=y,
        source=str(meta.get("source") or "mapped_extra"), role=role,
        exact_verified=bool(exact_verified), independence_status=ind_status,
        rigorous_independent=rigorous,
        search_ref=(f"covering:{int(covering_id)}" if covering_id is not None else None),
        plugin_id=meta.get("plugin_id") or meta.get("family_spec"), metadata=meta,
    )
