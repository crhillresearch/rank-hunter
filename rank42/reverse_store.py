"""Persistent storage for curve reverse-engineering fingerprints.

This is deliberately additive and isolated from the discovery tables.  A
reverse-engineering report may contain exact invariants, heuristic Nagao
scores, and numerical height screens, but it never changes a curve's rigorous
rank fields or the Rank Hunter incumbent.
"""

from __future__ import annotations

import json

from rank42.db import now


REVERSE_SCHEMA = """
CREATE TABLE IF NOT EXISTS reverse_engineering_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_key TEXT NOT NULL UNIQUE,
    subject_type TEXT NOT NULL,
    source TEXT,
    source_id TEXT,
    curve_id INTEGER,
    label TEXT NOT NULL,
    rank_lower_bound INTEGER,
    a_invariants_json TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    exact_json TEXT NOT NULL,
    heuristic_json TEXT NOT NULL,
    numerical_json TEXT NOT NULL,
    comparison_json TEXT NOT NULL,
    report_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'complete',
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reverse_subject
    ON reverse_engineering_reports(subject_type, source, source_id, curve_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_reverse_rank
    ON reverse_engineering_reports(rank_lower_bound DESC, updated_at DESC);
"""


def ensure_reverse_schema(db):
    db.executescript(REVERSE_SCHEMA)
    db.commit()


def store_report(db, report: dict):
    ensure_reverse_schema(db)
    ts = now()
    subject = report.get("subject") or {}
    fields = (
        str(report["report_key"]),
        str(subject.get("type") or "unknown"),
        subject.get("source"),
        str(subject["source_id"]) if subject.get("source_id") is not None else None,
        int(subject["curve_id"]) if subject.get("curve_id") is not None else None,
        str(subject.get("label") or report["report_key"]),
        int(subject.get("rank_lower_bound") or 0),
        json.dumps(report.get("a_invariants") or [], sort_keys=True),
        json.dumps(report.get("parameters") or {}, sort_keys=True),
        json.dumps(report.get("exact") or {}, sort_keys=True),
        json.dumps(report.get("heuristic") or {}, sort_keys=True),
        json.dumps(report.get("numerical") or {}, sort_keys=True),
        json.dumps(report.get("comparison") or {}, sort_keys=True),
        json.dumps(report, sort_keys=True),
        str(report.get("status") or "complete"),
        report.get("error"),
        ts,
        ts,
    )
    db.execute(
        """
        INSERT INTO reverse_engineering_reports(
            report_key,subject_type,source,source_id,curve_id,label,rank_lower_bound,
            a_invariants_json,parameters_json,exact_json,heuristic_json,numerical_json,
            comparison_json,report_json,status,error,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(report_key) DO UPDATE SET
            label=excluded.label,
            rank_lower_bound=excluded.rank_lower_bound,
            a_invariants_json=excluded.a_invariants_json,
            parameters_json=excluded.parameters_json,
            exact_json=excluded.exact_json,
            heuristic_json=excluded.heuristic_json,
            numerical_json=excluded.numerical_json,
            comparison_json=excluded.comparison_json,
            report_json=excluded.report_json,
            status=excluded.status,
            error=excluded.error,
            updated_at=excluded.updated_at
        """,
        fields,
    )
    db.commit()
    return db.execute(
        "SELECT * FROM reverse_engineering_reports WHERE report_key=?",
        (str(report["report_key"]),),
    ).fetchone()


def list_reports(db, *, limit=200):
    ensure_reverse_schema(db)
    return db.execute(
        """
        SELECT id,report_key,subject_type,source,source_id,curve_id,label,
               rank_lower_bound,status,error,created_at,updated_at,report_json
        FROM reverse_engineering_reports
        ORDER BY updated_at DESC,id DESC LIMIT ?
        """,
        (int(limit),),
    ).fetchall()


def get_report(db, report_id):
    ensure_reverse_schema(db)
    return db.execute(
        "SELECT * FROM reverse_engineering_reports WHERE id=?", (int(report_id),)
    ).fetchone()


def report_payload(row):
    if row is None:
        return None
    return json.loads(row["report_json"] or "{}")
