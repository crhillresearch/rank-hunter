import sqlite3
import subprocess

import pytest
from sage.all import EllipticCurve, QQ

import rank42.torsion as torsion
from rank42.torsion import (
    MAZUR_TORSION_CHOICES,
    TorsionSchemaNotReady,
    TorsionTimeout,
    canonical_torsion_label,
    compute_torsion_data,
    compute_torsion_data_bounded,
    ensure_torsion_schema,
    normalize_torsion_invariants,
    require_torsion_schema,
    retained_research_curve,
    torsion_label,
    validate_mazur_torsion,
)


def _db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE curves (
            id INTEGER PRIMARY KEY,
            status TEXT,
            exact_rank INTEGER,
            descent_lower INTEGER,
            generic_lower INTEGER,
            updated_at TEXT
        );
        CREATE TABLE points (
            curve_id INTEGER,
            rigorous_independent INTEGER DEFAULT 0
        );
        CREATE TABLE rank_evidence (
            curve_id INTEGER,
            rigorous INTEGER,
            status TEXT,
            rigorous_lower INTEGER
        );
        """
    )
    return db


def test_torsion_labels_and_mazur_validation():
    assert normalize_torsion_invariants([]) == ()
    assert torsion_label([]) == "Trivial"
    assert torsion_label([2]) == "C2"
    assert torsion_label([2, 4]) == "C2 × C4"
    assert validate_mazur_torsion([10]) == (10,)
    assert validate_mazur_torsion([2, 8]) == (2, 8)
    assert len(MAZUR_TORSION_CHOICES) == 15
    assert canonical_torsion_label("C2xC6") == "C2 × C6"
    assert canonical_torsion_label("Z/7Z") == "C7"
    assert canonical_torsion_label("trivial") == "Trivial"


def test_invalid_rational_torsion_is_rejected():
    try:
        validate_mazur_torsion([11])
    except ValueError as exc:
        assert "Mazur" in str(exc)
    else:
        raise AssertionError("C11 must be rejected over Q")


def test_retained_boundary_excludes_pruned_and_quick_only_rows():
    db = _db()
    db.executemany(
        "INSERT INTO curves(id,status,exact_rank,descent_lower,generic_lower,updated_at) VALUES(?,?,?,?,?,?)",
        [
            (1, "pruned", None, 8, None, ""),
            (2, "promising", None, None, None, ""),
            (3, "exact", 7, None, None, ""),
            (4, "proven_lower", None, 9, None, ""),
        ],
    )
    assert not retained_research_curve(db, 1)
    assert not retained_research_curve(db, 2)
    assert retained_research_curve(db, 3)
    assert retained_research_curve(db, 4)


def test_rigorous_point_or_evidence_crosses_retained_boundary():
    db = _db()
    db.executemany(
        "INSERT INTO curves(id,status,exact_rank,descent_lower,generic_lower,updated_at) VALUES(?,?,?,?,?,?)",
        [
            (1, "strong_done", None, None, None, ""),
            (2, "strong_done", None, None, None, ""),
        ],
    )
    db.execute("INSERT INTO points(curve_id,rigorous_independent) VALUES(1,1)")
    db.execute("INSERT INTO rank_evidence(curve_id,rigorous,status,rigorous_lower) VALUES(2,1,'completed',6)")
    assert retained_research_curve(db, 1)
    assert retained_research_curve(db, 2)


def test_exact_torsion_known_curve_controls():
    controls = (
        ([0, 0, 1, -1, 0], "Trivial", 1, []),
        ([0, 0, 0, 2, 0], "C2", 2, [2]),
        ([0, -1, 1, -10, -20], "C5", 5, [5]),
        ([0, 0, 0, -1, 0], "C2 × C2", 4, [2, 2]),
        ([0, 5, 0, 4, 0], "C2 × C4", 8, [2, 4]),
    )
    for ainvs, label, order, invariants in controls:
        E = EllipticCurve(QQ, ainvs)
        data = compute_torsion_data(E)
        assert data["torsion_label"] == label
        assert data["torsion_order"] == order
        assert data["torsion_invariants_json"] == __import__("json").dumps(invariants)


def test_bounded_torsion_worker_returns_exact_control():
    E = EllipticCurve(QQ, [0, 0, 0, -1, 0])
    data = compute_torsion_data_bounded(E, timeout=30)
    assert data["torsion_label"] == "C2 × C2"
    assert data["torsion_order"] == 4
    assert data["torsion_invariants_json"] == "[2, 2]"


def test_torsion_runtime_schema_guard_does_not_mutate_unmigrated_schema():
    db = _db()
    before = {row["name"] for row in db.execute("PRAGMA table_info(curves)")}
    with pytest.raises(TorsionSchemaNotReady, match="Migration Manager"):
        require_torsion_schema(db)
    after = {row["name"] for row in db.execute("PRAGMA table_info(curves)")}
    assert after == before
    assert "torsion_label" not in after
    index = db.execute(
        """SELECT 1 FROM sqlite_master
           WHERE type='index' AND name='idx_curves_torsion_label'"""
    ).fetchone()
    assert index is None


def test_bounded_torsion_timeout_is_explicit(monkeypatch):
    E = EllipticCurve(QQ, [0, 0, 0, -1, 0])

    def timeout_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr(torsion.subprocess, "run", timeout_run)
    with pytest.raises(TorsionTimeout, match="exceeded 3s"):
        compute_torsion_data_bounded(E, timeout=3)


def test_torsion_schema_is_idempotent():
    db = _db()
    assert ensure_torsion_schema(db)
    assert not ensure_torsion_schema(db)
    cols = {row["name"] for row in db.execute("PRAGMA table_info(curves)")}
    assert {
        "torsion_order",
        "torsion_invariants_json",
        "torsion_label",
        "torsion_computed_at",
        "torsion_algorithm",
        "torsion_error",
    } <= cols


def test_torsion_schema_second_check_is_read_only():
    db = _db()
    assert ensure_torsion_schema(db)
    traced = []
    db.set_trace_callback(traced.append)
    assert not ensure_torsion_schema(db)
    writes = [
        stmt for stmt in traced
        if stmt.lstrip().upper().startswith(("CREATE ", "ALTER ", "INSERT ", "UPDATE ", "DELETE "))
    ]
    assert writes == []
