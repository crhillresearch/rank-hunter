"""Core pencil registry shared by family plugins and read-only dashboard stats.

A pencil is an elliptic fibration, not a chart or specialization. Plugins own
its stable key and explicitly register aliases under the same key. The registry
stores provenance/definitions; rank certificates remain in their existing owners.
Registration participates in the caller's transaction and never commits it.
"""
from __future__ import annotations

import json
from rank42.db import now


def register_pencil(db, *, plugin_id, pencil_key, label, status="registered",
                    definition=None, curve_ids=()):
    if not plugin_id or not pencil_key or not label:
        raise ValueError("Pencil owner, stable key and label are required")
    if status not in {"registered", "constructed", "explored"}:
        raise ValueError("Invalid pencil status")
    timestamp = now()
    db.execute("""INSERT INTO pencils
        (plugin_id,pencil_key,label,status,definition_json,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?) ON CONFLICT(plugin_id,pencil_key) DO UPDATE SET
        label=excluded.label,
        status=CASE WHEN pencils.status='explored' OR excluded.status='explored'
                    THEN 'explored' WHEN pencils.status='constructed' OR excluded.status='constructed'
                    THEN 'constructed' ELSE 'registered' END,
        definition_json=excluded.definition_json, updated_at=excluded.updated_at""",
        (str(plugin_id), str(pencil_key), str(label), status,
         json.dumps(definition or {}, sort_keys=True), timestamp, timestamp))
    pencil_id = int(db.execute("SELECT id FROM pencils WHERE plugin_id=? AND pencil_key=?",
                              (str(plugin_id), str(pencil_key))).fetchone()[0])
    db.executemany("INSERT OR IGNORE INTO pencil_curves(pencil_id,curve_id) VALUES(?,?)",
                   ((pencil_id, int(cid)) for cid in curve_ids))
    return pencil_id


def pencil_summary(db):
    # Old read-only databases remain viewable until explicitly migrated.
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pencils'").fetchone():
        return {"registered": 0, "explored": 0, "available": False}
    row = db.execute("SELECT COUNT(*), COALESCE(SUM(status='explored'),0) FROM pencils").fetchone()
    return {"registered": int(row[0]), "explored": int(row[1]), "available": True}


def biggest_rank_jump(db, states):
    """Largest improvement of certified specialization bound over linked baseline.

    Both inputs are certified lower bounds; their difference is a research
    milestone, not a proof of the exact generic rank or actual rank increase.
    """
    baselines = {}
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pencils'").fetchone():
        for row in db.execute("""SELECT pc.curve_id,p.definition_json,p.label
                                 FROM pencil_curves pc JOIN pencils p ON p.id=pc.pencil_id"""):
            try:
                definition = json.loads(row['definition_json'])
                ranks = [int(evidence['rank_lower']) for evidence in definition.get('generic_rank_evidence', [])
                         if evidence.get('rigorous') is True and evidence.get('status') == 'completed'
                         and isinstance(evidence.get('certificate'), dict) and evidence['certificate']
                         and int(evidence.get('rank_lower', 0)) > 0]
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
            if ranks:
                rank = max(ranks)
                cid = int(row['curve_id'])
                if cid not in baselines or rank > baselines[cid][0]:
                    baselines[cid] = (rank, row['label'])
    candidates = []
    for state in states:
        cid = int(state['curve_id'])
        if state['rank_inconsistent'] or cid not in baselines:
            continue
        generic, label = baselines[cid]
        specialized = int(state['specialization_rigorous_lower'] or 0)
        if specialized > generic:
            candidates.append({'gain': specialized-generic, 'generic_lower': generic,
                               'specialization_lower': specialized, 'curve_id': cid,
                               'pencil_label': label})
    return max(candidates, key=lambda row: (row['gain'], row['specialization_lower'], -row['curve_id']), default=None)
