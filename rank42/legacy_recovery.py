"""Safe recovery importer for pre-ledger Rank42 databases.

The live database is never used as the staging target.  Legacy rank fields are
preserved as non-rigorous provenance; current exact-lower-bound certification
is required before they can affect rigorous Rank Hunter state.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from rank42.db import connect, now
from rank42.exact_lb import ExactCertificateFailure, ExactCertificateTimeout, run_exact_certificate
from rank42.rank_evidence import apply_reduced_rank_state, record_rank_evidence

MAP_SCHEMA = """
CREATE TABLE IF NOT EXISTS legacy_recovery_map (
 old_db TEXT NOT NULL, old_curve_id INTEGER NOT NULL, curve_id INTEGER NOT NULL,
 family TEXT NOT NULL, parameter TEXT NOT NULL, imported_curve INTEGER NOT NULL,
 model_valid INTEGER NOT NULL, legacy_lower INTEGER, legacy_upper INTEGER,
 generator_count INTEGER NOT NULL, point_count INTEGER NOT NULL,
 validation_status TEXT NOT NULL DEFAULT 'staged', validation_json TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(old_db, old_curve_id));
CREATE INDEX IF NOT EXISTS idx_legacy_recovery_curve ON legacy_recovery_map(curve_id);
"""

BASE_CURVE_COLS = (
    "family", "parameter", "score", "a_invariants_json", "conductor",
    "discriminant", "bad_primes_json", "root_number", "regulator",
    "created_at", "updated_at",
)


def _json(value, default):
    try:
        return json.loads(value) if value else default
    except Exception:
        return default


def _get(row, key, default=None):
    return row[key] if key in row.keys() else default


def _lower(row):
    vals = [_get(row, k) for k in ("exact_rank", "descent_lower", "generic_lower")]
    vals = [int(v) for v in vals if v is not None]
    return max(vals) if vals else None


def _upper(row):
    vals = [_get(row, k) for k in ("exact_rank", "descent_upper", "quick_upper")]
    vals = [int(v) for v in vals if v is not None]
    return min(vals) if vals else None


def backup_db(src: Path, dst: Path):
    if src.resolve() == dst.resolve():
        raise ValueError("source and destination databases must differ")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    a = sqlite3.connect(str(src)); b = sqlite3.connect(str(dst))
    try:
        a.backup(b)
    finally:
        b.close(); a.close()


def ensure_map(db):
    db.executescript(MAP_SCHEMA); db.commit()


def legacy_claim(db, curve_id, old_db, old, generators):
    model = _json(_get(old, "a_invariants_json"), [])
    if len(model) != 5:
        return
    record_rank_evidence(db, curve_id=curve_id, model=model, data={
        "engine": "legacy_rank42_import", "evidence_type": "legacy_claim",
        "status": "imported", "rigorous": False,
        "rigorous_lower": None, "rigorous_upper": None, "exact_rank": None,
        "numerical_rank_signal": _lower(old),
        "assumptions": ["Legacy rank fields are provenance only; current certification required."],
        "points_found": generators,
        "options": {
            "old_db": old_db, "old_curve_id": int(old["id"]),
            "legacy_generic_lower": _get(old, "generic_lower"),
            "legacy_quick_upper": _get(old, "quick_upper"),
            "legacy_descent_lower": _get(old, "descent_lower"),
            "legacy_descent_upper": _get(old, "descent_upper"),
            "legacy_exact_rank": _get(old, "exact_rank"),
            "legacy_certain": _get(old, "certain"),
        },
    })


def stage(live: Path, legacy: Path, work: Path):
    backup_db(live, work)
    db = connect(str(work)); db.row_factory = sqlite3.Row; ensure_map(db)
    db.execute("ATTACH DATABASE ? AS legacy", (str(legacy),))
    old_db = str(legacy.resolve())
    stats = {"legacy_curves": 0, "imported_curves": 0, "collisions": 0,
             "invalid_models": 0, "generator_points_added": 0, "legacy_points_added": 0}
    try:
        has_points = db.execute("SELECT 1 FROM legacy.sqlite_master WHERE type='table' AND name='points'").fetchone() is not None
        for old in db.execute("SELECT * FROM legacy.curves ORDER BY id").fetchall():
            stats["legacy_curves"] += 1
            model = _json(_get(old, "a_invariants_json"), [])
            valid = int(isinstance(model, list) and len(model) == 5)
            stats["invalid_models"] += 0 if valid else 1
            cur = db.execute("SELECT id FROM curves WHERE family=? AND parameter=?", (old["family"], old["parameter"])).fetchone()
            imported = 0
            if cur is None:
                vals = [_get(old, c) for c in BASE_CURVE_COLS]
                db.execute(
                    f"INSERT INTO curves({','.join(BASE_CURVE_COLS)},generic_lower,quick_upper,descent_lower,descent_upper,exact_rank,certain,generators_json,status,error) "
                    f"VALUES({','.join('?' for _ in BASE_CURVE_COLS)},NULL,NULL,NULL,NULL,NULL,0,?,'legacy_recovery',NULL)",
                    (*vals, _get(old, "generators_json")),
                )
                curve_id = int(db.execute("SELECT last_insert_rowid()").fetchone()[0]); imported = 1
                stats["imported_curves"] += 1
            else:
                curve_id = int(cur["id"]); stats["collisions"] += 1

            generators = [p for p in _json(_get(old, "generators_json"), []) if isinstance(p, list) and len(p) >= 2]
            ts = now()
            for i, p in enumerate(generators):
                meta = json.dumps({"legacy_recovery": True, "old_db": old_db, "old_curve_id": int(old["id"]), "legacy_generator_index": i}, sort_keys=True)
                before = db.total_changes
                db.execute("INSERT OR IGNORE INTO points(curve_id,x,y,source,role,exact_verified,independence_status,rigorous_independent,metadata_json,created_at,updated_at) VALUES(?,?,?,'legacy_generators','generator',0,'unknown',0,?,?,?)", (curve_id,str(p[0]),str(p[1]),meta,ts,ts))
                stats["generator_points_added"] += db.total_changes - before

            old_points = db.execute("SELECT * FROM legacy.points WHERE curve_id=? ORDER BY id", (old["id"],)).fetchall() if has_points else []
            for p in old_points:
                meta = _json(_get(p, "metadata_json"), {})
                meta.update({"legacy_recovery": True, "old_db": old_db, "old_curve_id": int(old["id"]), "old_point_id": int(p["id"]), "legacy_exact_verified": int(_get(p,"exact_verified",0) or 0), "legacy_independence_status": _get(p,"independence_status"), "legacy_rigorous_independent": int(_get(p,"rigorous_independent",0) or 0)})
                before = db.total_changes
                db.execute("INSERT OR IGNORE INTO points(curve_id,x,y,source,role,exact_verified,independence_status,rigorous_independent,search_ref,plugin_id,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,'unknown',0,?,?,?,?,?)", (curve_id,p["x"],p["y"],"legacy_recovery:"+str(_get(p,"source","unknown")),str(_get(p,"role","candidate")),int(_get(p,"exact_verified",0) or 0),_get(p,"search_ref"),_get(p,"plugin_id"),json.dumps(meta,sort_keys=True),_get(p,"created_at",ts),_get(p,"updated_at",ts)))
                stats["legacy_points_added"] += db.total_changes - before

            legacy_claim(db, curve_id, old_db, old, generators)
            db.execute("INSERT OR REPLACE INTO legacy_recovery_map VALUES(?,?,?,?,?,?,?,?,?,?,?,'staged','{}',?,?)", (old_db,int(old["id"]),curve_id,old["family"],old["parameter"],imported,valid,_lower(old),_upper(old),len(generators),len(old_points),ts,ts))
        db.commit()
    finally:
        try: db.execute("DETACH DATABASE legacy")
        except Exception: pass
        db.close()
    stats["work_db"] = str(work)
    return stats


def validate(work: Path, min_lower: int, timeout: int, max_halvings: int, limit: int | None):
    db = connect(str(work)); db.row_factory = sqlite3.Row; ensure_map(db)
    rows = db.execute("SELECT m.*,c.a_invariants_json,c.generators_json FROM legacy_recovery_map m JOIN curves c ON c.id=m.curve_id WHERE m.model_valid=1 AND m.generator_count>0 AND COALESCE(m.legacy_lower,0)>=? ORDER BY COALESCE(m.legacy_lower,0) DESC,m.old_curve_id", (min_lower,)).fetchall()
    if limit is not None: rows = rows[:limit]
    out = {"attempted":0,"certified":0,"timeout":0,"failed":0,"inconclusive":0,"results":[]}
    for row in rows:
        out["attempted"] += 1; status = "inconclusive"; payload = {}
        model = _json(row["a_invariants_json"], []); points = [p for p in _json(row["generators_json"], []) if isinstance(p,list) and len(p)>=2]
        try:
            result = run_exact_certificate(model, points, timeout=timeout, max_halvings=max_halvings)
            if result.get("independent"):
                lower = int(result["rank_lower_bound"])
                record_rank_evidence(db, curve_id=int(row["curve_id"]), model=model, data={"engine":"exact_quadratic_character","evidence_type":"certified_subgroup","status":"completed","rigorous":True,"rigorous_lower":lower,"rigorous_upper":None,"points_found":points,"options":{"legacy_recovery":True,"old_curve_id":int(row["old_curve_id"]),"max_halvings":max_halvings},"elapsed_seconds":result.get("runtime_seconds")})
                payload = {"certificate":result,"state":apply_reduced_rank_state(db,int(row["curve_id"]))}; status="certified"; out["certified"] += 1
            else:
                payload={"certificate":result}; out["inconclusive"] += 1
        except ExactCertificateTimeout as exc:
            status="timeout"; payload={"error":str(exc)}; out["timeout"] += 1
        except Exception as exc:
            status="failed"; payload={"error":str(exc)}; out["failed"] += 1
        db.execute("UPDATE legacy_recovery_map SET validation_status=?,validation_json=?,updated_at=? WHERE old_db=? AND old_curve_id=?", (status,json.dumps(payload,sort_keys=True,default=str),now(),row["old_db"],row["old_curve_id"])); db.commit()
        out["results"].append({"curve_id":int(row["curve_id"]),"old_curve_id":int(row["old_curve_id"]),"legacy_lower":row["legacy_lower"],"status":status})
    db.close(); return out


def report(work: Path):
    db=connect(str(work)); db.row_factory=sqlite3.Row; ensure_map(db)
    out=dict(db.execute("SELECT COUNT(*) total,SUM(imported_curve) imported,SUM(model_valid) model_valid,SUM(generator_count) generators,SUM(point_count) legacy_points FROM legacy_recovery_map").fetchone())
    out["validation"]={r["validation_status"]:r["n"] for r in db.execute("SELECT validation_status,COUNT(*) n FROM legacy_recovery_map GROUP BY validation_status")}
    out["top"]=[dict(r) for r in db.execute("SELECT old_curve_id,curve_id,family,parameter,legacy_lower,legacy_upper,generator_count,point_count,validation_status FROM legacy_recovery_map ORDER BY COALESCE(legacy_lower,0) DESC LIMIT 25")]
    db.close(); return out


def merge(live_path: Path, work_path: Path, backup: Path, certified_only: bool):
    backup_db(live_path, backup)
    live=connect(str(live_path)); work=connect(str(work_path)); live.row_factory=work.row_factory=sqlite3.Row; ensure_map(work)
    q="SELECT m.old_curve_id,m.curve_id AS work_id,m.validation_status,c.* FROM legacy_recovery_map m JOIN curves c ON c.id=m.curve_id"
    if certified_only: q += " WHERE m.validation_status='certified'"
    rows=work.execute(q).fetchall(); curves_added=points_added=evidence_added=0
    try:
        for row in rows:
            cur=live.execute("SELECT id FROM curves WHERE family=? AND parameter=?",(row["family"],row["parameter"])).fetchone()
            if cur is None:
                cols=[c for c in BASE_CURVE_COLS if c not in ("created_at","updated_at")]
                live.execute(f"INSERT INTO curves({','.join(cols)},generators_json,status,error,created_at,updated_at) VALUES({','.join('?' for _ in cols)},?,'legacy_recovery',NULL,?,?)",(*[row[c] for c in cols],row["generators_json"],row["created_at"],row["updated_at"])); live_id=int(live.execute("SELECT last_insert_rowid()").fetchone()[0]); curves_added += 1
            else: live_id=int(cur["id"])
            for p in work.execute("SELECT * FROM points WHERE curve_id=? AND (source='legacy_generators' OR source LIKE 'legacy_recovery:%')",(row["work_id"],)).fetchall():
                before=live.total_changes; live.execute("INSERT OR IGNORE INTO points(curve_id,x,y,source,role,exact_verified,independence_status,rigorous_independent,search_ref,plugin_id,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(live_id,p["x"],p["y"],p["source"],p["role"],p["exact_verified"],p["independence_status"],p["rigorous_independent"],p["search_ref"],p["plugin_id"],p["metadata_json"],p["created_at"],p["updated_at"])); points_added += live.total_changes-before
            model=_json(row["a_invariants_json"],[])
            for ev in work.execute("SELECT * FROM rank_evidence WHERE curve_id=? AND (engine='legacy_rank42_import' OR (rigorous=1 AND evidence_type='certified_subgroup'))",(row["work_id"],)).fetchall():
                before=live.execute("SELECT COUNT(*) n FROM rank_evidence WHERE curve_id=?",(live_id,)).fetchone()[0]
                record_rank_evidence(live,curve_id=live_id,model=model,data={"engine":ev["engine"],"engine_version":ev["engine_version"],"sage_version":ev["sage_version"],"evidence_type":ev["evidence_type"],"rigorous":bool(ev["rigorous"]),"rigorous_lower":ev["rigorous_lower"],"rigorous_upper":ev["rigorous_upper"],"exact_rank":ev["exact_rank"],"conditional_analytic_upper":ev["conditional_analytic_upper"],"numerical_rank_signal":ev["numerical_rank_signal"],"assumptions":_json(ev["assumptions_json"],[]),"status":ev["status"],"timed_out":bool(ev["timed_out"]),"partial":bool(ev["partial"]),"options":_json(ev["options_json"],{}),"points_found":_json(ev["points_json"],[]),"elapsed_seconds":ev["elapsed_seconds"]})
                after=live.execute("SELECT COUNT(*) n FROM rank_evidence WHERE curve_id=?",(live_id,)).fetchone()[0]; evidence_added += max(0,after-before)
            apply_reduced_rank_state(live,live_id)
        live.commit()
    except Exception:
        live.rollback(); raise
    finally:
        work.close(); live.close()
    return {"backup":str(backup),"mapped_curves":len(rows),"curves_added":curves_added,"points_added":points_added,"evidence_added":evidence_added}


def main():
    ap=argparse.ArgumentParser(description="Recover an old Rank42 DB through a disposable staging DB")
    sub=ap.add_subparsers(dest="cmd",required=True)
    s=sub.add_parser("stage"); s.add_argument("--live",required=True); s.add_argument("--legacy",required=True); s.add_argument("--work",required=True)
    v=sub.add_parser("validate"); v.add_argument("--work",required=True); v.add_argument("--min-legacy-lower",type=int,default=12); v.add_argument("--timeout",type=int,default=120); v.add_argument("--max-halvings",type=int,default=40); v.add_argument("--limit",type=int)
    r=sub.add_parser("report"); r.add_argument("--work",required=True)
    m=sub.add_parser("merge"); m.add_argument("--live",required=True); m.add_argument("--work",required=True); m.add_argument("--backup",required=True); m.add_argument("--certified-only",action="store_true")
    a=ap.parse_args()
    if a.cmd=="stage": out=stage(Path(a.live),Path(a.legacy),Path(a.work))
    elif a.cmd=="validate": out=validate(Path(a.work),a.min_legacy_lower,a.timeout,a.max_halvings,a.limit)
    elif a.cmd=="report": out=report(Path(a.work))
    else: out=merge(Path(a.live),Path(a.work),Path(a.backup),a.certified_only)
    print(json.dumps(out,indent=2,sort_keys=True,default=str))

if __name__ == "__main__": main()
