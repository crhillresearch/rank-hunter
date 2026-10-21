"""Core-owned result/proof boundary for family Plugin Geometry commands."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

from sage.all import QQ

from rank42.auto_point_search import certify_ledger_growth
from rank42.curve_research_state import get_curve_research_state
from rank42.plugin_rank_certificate import verify_plugin_rigorous_upper_certificate
from rank42.point_promotion import promote_rigorous_rank_interval
from rank42.points import rigorous_witness_basis, upsert_point

SCHEMA = "rank42.plugin_geometry_result.v1"
RESULT_ENV = "RANK42_PLUGIN_GEOMETRY_RESULT_PATH"
STATUSES = frozenset({"completed", "partial", "inconclusive"})


def _required_text(result, key):
    value = result.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Plugin Geometry result requires non-empty {key}")
    return value.strip()


def validate_plugin_geometry_result(result):
    """Validate one plugin-produced geometry artifact before live persistence."""
    if not isinstance(result, dict):
        raise ValueError("Plugin Geometry result must be an object")
    if str(result.get("schema") or "") != SCHEMA:
        raise ValueError(
            f"unsupported Plugin Geometry result schema: {result.get('schema')!r}"
        )
    status = str(result.get("status") or "")
    if status not in STATUSES:
        raise ValueError(
            "Plugin Geometry result status must be completed, partial, or inconclusive"
        )
    engine = _required_text(result, "engine")
    engine_version = _required_text(result, "engine_version")
    algorithm = _required_text(result, "algorithm")

    points = result.get("points")
    if points is None:
        points = []
    if not isinstance(points, list):
        raise ValueError("Plugin Geometry result points must be a list")
    normalized_points = []
    for raw in points:
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            raise ValueError("Plugin Geometry point must be [x,y]")
        normalized_points.append([str(raw[0]), str(raw[1])])

    upper_certificate = result.get("rigorous_upper_certificate")
    if upper_certificate is not None and not isinstance(upper_certificate, dict):
        raise ValueError(
            "Plugin Geometry rigorous_upper_certificate must be an object"
        )
    artifacts = result.get("artifacts")
    if artifacts is None:
        artifacts = []
    if not isinstance(artifacts, list) or not all(
        isinstance(item, dict) for item in artifacts
    ):
        raise ValueError("Plugin Geometry artifacts must be a list of objects")
    metadata = result.get("metadata")
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError("Plugin Geometry metadata must be an object")

    normalized = dict(result)
    normalized.update({
        "schema": SCHEMA,
        "status": status,
        "engine": engine,
        "engine_version": engine_version,
        "algorithm": algorithm,
        "points": normalized_points,
        "rigorous_upper_certificate": (
            None if upper_certificate is None else dict(upper_certificate)
        ),
        "artifacts": [dict(item) for item in artifacts],
        "metadata": dict(metadata),
    })
    return normalized


def load_plugin_geometry_result(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        return None
    return validate_plugin_geometry_result(
        json.loads(path.read_text(encoding="utf-8"))
    )


@contextmanager
def plugin_geometry_sandbox(db):
    """Yield a temporary SQLite backup that legacy plugins may mutate freely."""
    with tempfile.TemporaryDirectory(prefix="rank42-plugin-geometry-") as root:
        sandbox_path = Path(root) / "rank42-plugin-geometry.db"
        target = sqlite3.connect(str(sandbox_path))
        try:
            db.backup(target)
            target.commit()
        finally:
            target.close()
        yield sandbox_path


def plugin_geometry_environment(result_path):
    env = dict(os.environ)
    env[RESULT_ENV] = str(Path(result_path).resolve())
    return env


def force_command_database(command, sandbox_path):
    """Force a target adapter command to use the sandbox DB after feature edits."""
    command = [str(value) for value in command]
    sandbox = str(Path(sandbox_path).resolve())
    found = False
    index = 0
    while index < len(command):
        token = command[index]
        if token == "--db":
            if index + 1 >= len(command):
                raise ValueError("Plugin Geometry command has --db without a value")
            command[index + 1] = sandbox
            found = True
            index += 2
            continue
        if token.startswith("--db="):
            command[index] = f"--db={sandbox}"
            found = True
        index += 1
    if not found:
        raise ValueError(
            "legacy Plugin Geometry target command must expose a --db argument "
            "so core can enforce sandboxed scientific writes"
        )
    return command


def snapshot_plugin_geometry_subject(db, curve_id):
    """Capture live curve-scoped state needed to interpret a legacy sandbox delta."""
    curve_id = int(curve_id)
    points = {
        int(row["id"]): {
            "x": str(row["x"]),
            "y": str(row["y"]),
            "exact_verified": int(row["exact_verified"] or 0),
            "rigorous_independent": int(row["rigorous_independent"] or 0),
            "independence_status": str(row["independence_status"] or ""),
            "role": str(row["role"] or ""),
        }
        for row in db.execute(
            """SELECT id,x,y,exact_verified,rigorous_independent,
                      independence_status,role
               FROM points WHERE curve_id=? ORDER BY id""",
            (curve_id,),
        ).fetchall()
    }
    evidence = {
        int(row["id"]): tuple(
            row[key]
            for key in (
                "engine", "evidence_type", "status", "rigorous",
                "rigorous_lower", "rigorous_upper", "exact_rank",
            )
        )
        for row in db.execute(
            """SELECT id,engine,evidence_type,status,rigorous,
                      rigorous_lower,rigorous_upper,exact_rank
               FROM rank_evidence WHERE curve_id=? ORDER BY id""",
            (curve_id,),
        ).fetchall()
    }
    curve = db.execute(
        """SELECT generic_lower,descent_lower,descent_upper,exact_rank,
                  generators_json,status
           FROM curves WHERE id=?""",
        (curve_id,),
    ).fetchone()
    return {
        "curve_id": curve_id,
        "points": points,
        "evidence": evidence,
        "curve_projection": None if curve is None else dict(curve),
        "research_state": get_curve_research_state(db, curve_id),
    }


def legacy_sandbox_geometry_result(
    sandbox_path,
    *,
    baseline,
    plugin_id,
    plugin_version,
    variant_id,
):
    """Convert legacy sandbox DB mutations into a non-proof typed point artifact."""
    curve_id = int(baseline["curve_id"])
    con = sqlite3.connect(str(sandbox_path))
    con.row_factory = sqlite3.Row
    try:
        point_rows = con.execute(
            """SELECT id,x,y,exact_verified,rigorous_independent,
                      independence_status,role
               FROM points WHERE curve_id=? ORDER BY id""",
            (curve_id,),
        ).fetchall()
        evidence_rows = con.execute(
            """SELECT id,engine,evidence_type,status,rigorous,
                      rigorous_lower,rigorous_upper,exact_rank
               FROM rank_evidence WHERE curve_id=? ORDER BY id""",
            (curve_id,),
        ).fetchall()
        curve = con.execute(
            """SELECT generic_lower,descent_lower,descent_upper,exact_rank,
                      generators_json,status
               FROM curves WHERE id=?""",
            (curve_id,),
        ).fetchone()
    finally:
        con.close()

    baseline_points = dict(baseline.get("points") or {})
    candidates = []
    changed_point_rows = 0
    deleted_point_rows = max(0, len(baseline_points) - len(point_rows))
    for row in point_rows:
        current = {
            "x": str(row["x"]),
            "y": str(row["y"]),
            "exact_verified": int(row["exact_verified"] or 0),
            "rigorous_independent": int(row["rigorous_independent"] or 0),
            "independence_status": str(row["independence_status"] or ""),
            "role": str(row["role"] or ""),
        }
        before = baseline_points.get(int(row["id"]))
        if before == current:
            continue
        changed_point_rows += 1
        pair = [current["x"], current["y"]]
        if pair not in candidates:
            candidates.append(pair)

    baseline_curve = baseline.get("curve_projection") or {}
    sandbox_curve = None if curve is None else dict(curve)
    sandbox_generators = []
    if sandbox_curve is not None:
        before_generators = baseline_curve.get("generators_json")
        after_generators = sandbox_curve.get("generators_json")
        if after_generators and after_generators != before_generators:
            try:
                decoded_generators = json.loads(after_generators)
            except Exception:
                decoded_generators = []
            if isinstance(decoded_generators, list):
                for raw in decoded_generators:
                    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
                        pair = [str(raw[0]), str(raw[1])]
                        if pair not in candidates:
                            candidates.append(pair)
                            sandbox_generators.append(pair)

    baseline_evidence = dict(baseline.get("evidence") or {})
    changed_evidence = 0
    for row in evidence_rows:
        current = tuple(
            row[key]
            for key in (
                "engine", "evidence_type", "status", "rigorous",
                "rigorous_lower", "rigorous_upper", "exact_rank",
            )
        )
        if baseline_evidence.get(int(row["id"])) != current:
            changed_evidence += 1
    deleted_evidence = max(0, len(baseline_evidence) - len(evidence_rows))

    curve_projection_changed = (
        sandbox_curve != baseline.get("curve_projection")
    )

    return validate_plugin_geometry_result({
        "schema": SCHEMA,
        "status": "completed",
        "engine": "legacy_target_adapter",
        "engine_version": str(plugin_version or "unknown"),
        "algorithm": "sandbox_db_delta",
        "points": candidates,
        "artifacts": [],
        "metadata": {
            "result_source": "legacy_sandbox_delta",
            "compatibility_mode": "legacy_trusted_local_sandbox",
            "plugin_id": str(plugin_id),
            "variant_id": str(variant_id),
            "sandbox_changed_point_rows": changed_point_rows,
            "sandbox_deleted_point_rows": deleted_point_rows,
            "sandbox_rank_evidence_writes_discarded": changed_evidence,
            "sandbox_rank_evidence_deletes_discarded": deleted_evidence,
            "sandbox_curve_projection_write_discarded": bool(
                curve_projection_changed
            ),
            "sandbox_generator_candidates": len(sandbox_generators),
            "scientific_write_policy": "core_validated_artifacts_only",
        },
    })


def apply_plugin_geometry_result(
    db,
    *,
    curve_id,
    E,
    plugin_id,
    result,
    search_ref,
    certificate_timeout,
    exact_candidates,
):
    """Apply typed Plugin Geometry output through authoritative live services."""
    typed = validate_plugin_geometry_result(result)
    accepted = 0
    rejected = 0
    rejection_samples = []
    for index, raw in enumerate(typed["points"], 1):
        try:
            P = E(QQ(str(raw[0])), QQ(str(raw[1])))
            if P.is_zero():
                raise ValueError("identity point is not a search witness")
        except Exception as exc:
            rejected += 1
            if len(rejection_samples) < 5:
                rejection_samples.append({
                    "index": index,
                    "point": list(raw),
                    "error": repr(exc),
                })
            continue
        upsert_point(
            db,
            curve_id=int(curve_id),
            x=P[0],
            y=P[1],
            source="plugin_geometry",
            role="candidate_extra",
            exact_verified=True,
            independence_status="unknown",
            rigorous_independent=False,
            search_ref=str(search_ref),
            plugin_id=str(plugin_id),
            metadata={
                "result_schema": typed["schema"],
                "engine": typed["engine"],
                "engine_version": typed["engine_version"],
                "algorithm": typed["algorithm"],
                "result_source": typed["metadata"].get("result_source"),
                "plugin_geometry_metadata": typed["metadata"],
                "artifact_count": len(typed["artifacts"]),
                "plugin_point_index": index,
            },
        )
        accepted += 1

    if accepted:
        cert = certify_ledger_growth(
            db,
            curve_id=int(curve_id),
            E=E,
            source="plugin_geometry",
            search_ref=f"{search_ref}:exact",
            certificate_timeout=max(1, int(certificate_timeout)),
            max_candidates=max(1, int(exact_candidates)),
        )
    else:
        cert = {
            "attempts": 0,
            "growth": 0,
            "rigorous_lower": get_curve_research_state(
                db, int(curve_id)
            )["rigorous_lower"],
        }

    upper = None
    upper_verification = None
    certificate = typed.get("rigorous_upper_certificate")
    if certificate is not None:
        basis, _required, complete = rigorous_witness_basis(db, int(curve_id), E)
        upper_verification = verify_plugin_rigorous_upper_certificate(
            E,
            certificate,
            known_points=basis if complete else [],
            timeout=max(1, int(certificate_timeout)),
        )
        if upper_verification.get("verified") is True:
            promoted = promote_rigorous_rank_interval(
                db,
                curve_id=int(curve_id),
                model=E.a_invariants(),
                rigorous_lower=None,
                rigorous_upper=int(upper_verification["rigorous_upper"]),
                certificate=upper_verification["certificate"],
                engine=str(
                    upper_verification.get("engine")
                    or "plugin_geometry_certificate_verifier"
                ),
                evidence_type="descent",
                source="plugin_geometry",
                engine_version=upper_verification.get("engine_version"),
                points_found=[],
                options={
                    "plugin_id": str(plugin_id),
                    "result_schema": typed["schema"],
                    "plugin_engine": typed["engine"],
                    "plugin_engine_version": typed["engine_version"],
                    "plugin_algorithm": typed["algorithm"],
                },
                metadata={"plugin_geometry_result": {
                    k: v for k, v in typed.items()
                    if k not in {"points", "rigorous_upper_certificate"}
                }},
                elapsed_seconds=upper_verification.get("elapsed_seconds"),
            )
            if not promoted.get("rank_inconsistent"):
                upper = promoted.get("rigorous_upper")

    state = get_curve_research_state(db, int(curve_id))
    return {
        "result_schema": typed["schema"],
        "result_source": typed["metadata"].get("result_source", "typed_artifact"),
        "engine": typed["engine"],
        "engine_version": typed["engine_version"],
        "algorithm": typed["algorithm"],
        "exact_points_accepted": accepted,
        "points_rejected": rejected,
        "point_rejection_samples": rejection_samples,
        "certificate_attempts": cert.get("attempts", 0),
        "rank_growth": cert.get("growth", 0),
        "rigorous_lower": int(state["rigorous_lower"]),
        "rigorous_upper": state.get("rigorous_upper"),
        "verified_plugin_upper": upper,
        "rigorous_upper_verification": upper_verification,
        "artifact_count": len(typed["artifacts"]),
        "result_metadata": typed["metadata"],
    }
