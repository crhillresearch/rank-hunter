"""Read-only reproducibility manifests and package composition.

RESEARCH-R3 defines the authoritative Curve Research Bundle manifest.
RESEARCH-R7 composes Campaign scope, human README, package files, and in-memory
archive output from the already accepted object authorities.
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone

from rank42 import __version__ as CORE_VERSION
from rank42.candidate_ranking_state import (
    pipeline_candidate_ranking_state,
    pool_candidate_ranking_state,
)
from rank42.curve_arithmetic_state import get_curve_arithmetic_state
from rank42.curve_research_state import get_curve_research_state
from rank42.landscape_state import get_landscape_analysis
from rank42.manage_store import (
    campaign_curve_ids_readonly,
    campaign_jobs,
    campaign_notebook_entries,
    campaign_pipeline_runs,
    get_campaign,
)
from rank42.pipeline_state import pipeline_run_payload
from rank42.rank_evidence import list_rank_evidence


CURVE_BUNDLE_MANIFEST_VERSION = 1
CAMPAIGN_BUNDLE_MANIFEST_VERSION = 1


def _table_exists(db, name):
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (str(name),),
    ).fetchone() is not None


def _json(value, default):
    if value in (None, ""):
        return default
    try:
        out = json.loads(value)
    except Exception:
        return default
    return out


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _rank_evidence_payload(row):
    rec = dict(row)
    for field, default in (
        ("assumptions_json", []),
        ("model_a_invariants_json", []),
        ("minimal_model_a_invariants_json", None),
        ("options_json", {}),
        ("points_json", []),
    ):
        rec[field[:-5]] = _json(rec.pop(field, None), default)
    rec["rigorous"] = bool(int(rec.get("rigorous") or 0))
    rec["timed_out"] = bool(int(rec.get("timed_out") or 0))
    rec["partial"] = bool(int(rec.get("partial") or 0))
    return _jsonable(rec)


def _point_payload(row):
    rec = dict(row)
    rec["metadata"] = _json(rec.pop("metadata_json", None), {})
    for key in ("exact_verified", "rigorous_independent", "hard_flag"):
        if key in rec:
            rec[key] = bool(int(rec[key] or 0))
    return _jsonable(rec)


def _lattice_payload(row):
    rec = dict(row)
    for field, default in (
        ("basis_json", []),
        ("gram_json", []),
        ("metadata_json", {}),
    ):
        if field in rec:
            rec[field[:-5]] = _json(rec.pop(field), default)
    if "positive_definite_screen" in rec:
        rec["positive_definite_screen"] = bool(
            int(rec["positive_definite_screen"] or 0)
        )
    return _jsonable(rec)


def _quartic_payload(row):
    rec = dict(row)
    for field, default in (
        ("polynomial_json", []),
        ("integer_polynomial_json", []),
        ("options_json", []),
        ("metadata_json", {}),
    ):
        if field in rec:
            rec[field[:-5]] = _json(rec.pop(field), default)
    return _jsonable(rec)


def _availability(records, *, reason):
    return {
        "available": bool(records),
        "count": len(records),
        "reason": None if records else str(reason),
    }


def _candidate_pool_matches(db, curve_id):
    if not (_table_exists(db, "candidates") and _table_exists(db, "candidate_pools")):
        return []
    rows = db.execute(
        """
        SELECT c.*, p.name AS pool_name, p.plugin_id, p.plugin_version,
               p.family_spec, p.family_sha256, p.adapter_sha256,
               p.plugin_manifest_sha256, p.generation_json
        FROM candidates c
        JOIN candidate_pools p ON p.id=c.pool_id
        WHERE c.curve_id=?
        ORDER BY c.id
        """,
        (int(curve_id),),
    ).fetchall()
    out = []
    for row in rows:
        raw = dict(row)
        out.append(
            {
                "candidate_id": int(raw["id"]),
                "pool_id": int(raw["pool_id"]),
                "pool_name": str(raw["pool_name"]),
                "parameter": str(raw["parameter"]),
                "status": str(raw["status"]),
                "generation": _json(raw.get("generation_json"), {}),
                "metadata": _json(raw.get("metadata_json"), {}),
                "ranking": _jsonable(pool_candidate_ranking_state(raw)),
            }
        )
    return out


def _stored_pipeline_run_payload(row):
    rec = dict(row)
    for field, default in (
        ("target_json", {}),
        ("stages_json", []),
        ("run_config_json", {}),
        ("manifest_json", {}),
        ("result_json", None),
    ):
        if field in rec:
            rec[field[:-5]] = _json(rec.pop(field), default)
    return _jsonable(rec)


def _pipeline_candidate_payload(row):
    raw = dict(row)
    candidate = dict(raw)
    candidate["score_provenance"] = _json(
        candidate.pop("score_provenance_json", None),
        {},
    )
    candidate["stage_results"] = _json(
        candidate.pop("stage_results_json", None),
        {},
    )
    return {
        "candidate": _jsonable(candidate),
        "ranking": _jsonable(pipeline_candidate_ranking_state(raw)),
    }


def _pipeline_runs(db, curve_id):
    if not (
        _table_exists(db, "search_pipeline_candidates")
        and _table_exists(db, "search_pipeline_runs")
    ):
        return []
    candidates = db.execute(
        """SELECT * FROM search_pipeline_candidates
           WHERE curve_id=? ORDER BY run_id,id""",
        (int(curve_id),),
    ).fetchall()
    grouped = {}
    for row in candidates:
        grouped.setdefault(int(row["run_id"]), []).append(row)
    out = []
    for run_id in sorted(grouped):
        run_row = db.execute(
            "SELECT * FROM search_pipeline_runs WHERE id=?",
            (int(run_id),),
        ).fetchone()
        if run_row is None:
            continue
        out.append(
            {
                "run": _jsonable(pipeline_run_payload(run_row)),
                "stored_run": _stored_pipeline_run_payload(run_row),
                "candidates": [
                    _pipeline_candidate_payload(row)
                    for row in grouped[run_id]
                ],
            }
        )
    return out


def _linked_jobs(db, curve_id, run_ids):
    if not _table_exists(db, "ui_jobs"):
        return []
    try:
        rows = db.execute(
            """
            SELECT * FROM ui_jobs
            WHERE CAST(json_extract(metadata_json,'$.curve_id') AS INTEGER)=?
               OR CAST(json_extract(metadata_json,'$.pipeline_run_id') AS INTEGER)
                  IN (
                    SELECT run_id FROM search_pipeline_candidates WHERE curve_id=?
                  )
            ORDER BY id
            """,
            (int(curve_id), int(curve_id)),
        ).fetchall()
    except Exception:
        rows = []
    out = []
    wanted_runs = {int(x) for x in run_ids}
    for row in rows:
        rec = dict(row)
        metadata = _json(rec.pop("metadata_json", None), {})
        direct_curve = metadata.get("curve_id")
        pipeline_run = metadata.get("pipeline_run_id")
        try:
            direct_match = int(direct_curve) == int(curve_id)
        except (TypeError, ValueError):
            direct_match = False
        try:
            run_match = int(pipeline_run) in wanted_runs
        except (TypeError, ValueError):
            run_match = False
        if direct_match or run_match:
            rec["metadata"] = metadata
            rec["command"] = _json(rec.pop("command_json", None), [])
            out.append(_jsonable(rec))
    return out


def _campaigns(db, run_records, jobs):
    if not _table_exists(db, "research_campaigns"):
        return []
    ids = set()
    for rec in run_records:
        value = rec["run"].get("campaign_id")
        if value is not None:
            ids.add(int(value))
    for job in jobs:
        value = job.get("campaign_id")
        if value is not None:
            ids.add(int(value))
    if not ids:
        return []
    marks = ",".join("?" for _ in sorted(ids))
    rows = db.execute(
        f"SELECT * FROM research_campaigns WHERE id IN ({marks}) ORDER BY id",
        sorted(ids),
    ).fetchall()
    return [_jsonable(dict(row)) for row in rows]


def _catalog_checks(db, curve_id):
    if not _table_exists(db, "curve_catalog_checks"):
        return []
    rows = db.execute(
        "SELECT * FROM curve_catalog_checks WHERE curve_id=? ORDER BY source",
        (int(curve_id),),
    ).fetchall()
    return [_jsonable(dict(row)) for row in rows]


def build_curve_bundle_manifest(db, curve_id):
    state = get_curve_research_state(db, int(curve_id))
    curve = dict(state["curve"])
    arithmetic = get_curve_arithmetic_state(db, int(curve_id))
    evidence = [
        _rank_evidence_payload(row)
        for row in list_rank_evidence(db, int(curve_id), limit=100000)
    ]
    points = (
        [
            _point_payload(row)
            for row in db.execute(
                "SELECT * FROM points WHERE curve_id=? ORDER BY id",
                (int(curve_id),),
            ).fetchall()
        ]
        if _table_exists(db, "points")
        else []
    )
    witness_ids = [
        int(row["id"])
        for row in points
        if row.get("exact_verified") and row.get("rigorous_independent")
    ]

    pool_matches = _candidate_pool_matches(db, int(curve_id))
    pipeline_runs = _pipeline_runs(db, int(curve_id))
    run_ids = [int(rec["run"]["id"]) for rec in pipeline_runs]
    jobs = _linked_jobs(db, int(curve_id), run_ids)
    campaigns = _campaigns(db, pipeline_runs, jobs)

    lattices = (
        [
            _lattice_payload(row)
            for row in db.execute(
                "SELECT * FROM mw_lattices WHERE curve_id=? ORDER BY id",
                (int(curve_id),),
            ).fetchall()
        ]
        if _table_exists(db, "mw_lattices")
        else []
    )
    quartics = (
        [
            _quartic_payload(row)
            for row in db.execute(
                "SELECT * FROM quartic_searches WHERE curve_id=? ORDER BY id",
                (int(curve_id),),
            ).fetchall()
        ]
        if _table_exists(db, "quartic_searches")
        else []
    )
    catalog_checks = _catalog_checks(db, int(curve_id))

    minimal_model = next(
        (
            rec.get("minimal_model_a_invariants")
            for rec in evidence
            if rec.get("minimal_model_a_invariants")
        ),
        None,
    )
    a_invariants = _json(curve.get("a_invariants_json"), [])

    rank_state = {
        key: state[key]
        for key in (
            "rigorous_lower",
            "specialization_rigorous_lower",
            "rigorous_upper",
            "exact_rank",
            "conditional_analytic_upper",
            "conditional_mw_upper",
            "numerical_rank_signal",
            "rank_inconsistent",
            "rank_evidence_count",
            "rigorous_evidence_count",
            "completed_rigorous_evidence_count",
            "latest_rank_evidence_id",
        )
    }
    curve_fingerprints = {
        "plugin_id": curve.get("plugin_id"),
        "plugin_version": curve.get("plugin_version"),
        "family_sha256": curve.get("family_sha256"),
        "adapter_sha256": curve.get("adapter_sha256"),
        "plugin_manifest_sha256": curve.get("plugin_manifest_sha256"),
        "chart_map_fingerprint": curve.get("chart_map_fingerprint"),
    }

    arithmetic_payload = {
        "local": _jsonable(dict(arithmetic.get("local") or {})),
        "local_provenance": _jsonable(dict(arithmetic.get("local_provenance") or {})),
        "display": _jsonable(dict(arithmetic.get("display") or {
            key: arithmetic.get(key)
            for key in (
                "conductor",
                "log_conductor",
                "discriminant",
                "bad_primes",
                "root_number",
                "torsion",
                "naive_height",
                "faltings_height",
            )
        })),
        "provenance": _jsonable(dict(arithmetic.get("provenance") or {})),
        "references": _jsonable(dict(arithmetic.get("references") or {})),
        "conflicts": _jsonable(list(arithmetic.get("conflicts") or [])),
        "issues": _jsonable(list(arithmetic.get("issues") or [])),
    }

    availability = {
        "minimal_model": {
            "available": minimal_model is not None,
            "reason": None if minimal_model is not None else "no persisted minimal model found",
        },
        "candidate_pool_matches": _availability(
            pool_matches,
            reason="no Candidate Pool row is linked to this curve",
        ),
        "pipeline_runs": _availability(
            pipeline_runs,
            reason="no Pipeline candidate/run is linked to this curve",
        ),
        "jobs": _availability(
            jobs,
            reason="no normalized Job metadata link to this curve or its Pipeline Runs",
        ),
        "campaigns": _availability(
            campaigns,
            reason="no linked Pipeline Run or Job carries Campaign ownership",
        ),
        "lattices": _availability(
            lattices,
            reason="no persisted MW lattice analysis for this curve",
        ),
        "quartics": _availability(
            quartics,
            reason="no persisted quartic search for this curve",
        ),
        "catalog_checks": _availability(
            catalog_checks,
            reason="no persisted external Catalog check for this curve",
        ),
        "saturation_state": {
            "available": False,
            "reason": "no standalone saturation-state authority is defined for Curve Bundles; retained evidence remains in point/rank records",
        },
        "selected_notebook_entries": {
            "available": False,
            "reason": "Curve Bundle R3 has no explicit notebook-selection input; Campaign Bundle composition is RESEARCH-R7",
        },
    }

    return _jsonable(
        {
            "manifest_version": CURVE_BUNDLE_MANIFEST_VERSION,
            "scope": "curve",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "curve": {
                "id": int(curve["id"]),
                "family": str(curve["family"]),
                "parameter": str(curve["parameter"]),
                "a_invariants": a_invariants,
                "minimal_model_a_invariants": minimal_model,
                "status": curve.get("status"),
                "plugin_id": curve.get("plugin_id"),
                "plugin_version": curve.get("plugin_version"),
                "family_spec": curve.get("family_spec"),
                "native_identity": {
                    "native_fiber_id": curve.get("native_fiber_id"),
                    "native_family_key": curve.get("native_family_key"),
                    "native_family_spec": curve.get("native_family_spec"),
                    "native_parameter": curve.get("native_parameter"),
                    "chart_id": curve.get("chart_id"),
                    "chart_parameter": curve.get("chart_parameter"),
                    "chart_map_fingerprint": curve.get("chart_map_fingerprint"),
                },
                "created_at": curve.get("created_at"),
                "updated_at": curve.get("updated_at"),
            },
            "rank_state": rank_state,
            "rank_evidence": evidence,
            "points": points,
            "witness_basis": {
                "policy": "persisted_rigorous_independent_points",
                "point_ids": witness_ids,
                "count": len(witness_ids),
            },
            "arithmetic": arithmetic_payload,
            "external_references": {
                "catalog_checks": catalog_checks,
                "arithmetic_references": arithmetic_payload["references"],
            },
            "search_provenance": {
                "candidate_pool_matches": pool_matches,
                "pipeline_runs": pipeline_runs,
                "jobs": jobs,
                "campaigns": campaigns,
            },
            "analysis_artifacts": {
                "lattices": lattices,
                "quartics": quartics,
            },
            "reproduction": {
                "core_version": str(CORE_VERSION),
                "core_commit": {
                    "available": False,
                    "value": None,
                    "reason": "core Git commit is not persisted in current curve/run state",
                },
                "curve_fingerprints": curve_fingerprints,
                "pipeline_run_manifests": [
                    rec["run"].get("manifest") or {}
                    for rec in pipeline_runs
                ],
                "feature_plugin_hashes": {
                    "available": False,
                    "value": None,
                    "reason": "Feature plugin ids may be frozen in Run manifests but content hashes are not persisted there",
                },
            },
            "availability": availability,
            "policy": {
                "read_only_snapshot": True,
                "scientific_state_mutation": False,
                "rank_source": "Curve Research State",
                "arithmetic_source": "Curve Arithmetic",
                "missing_optional_artifacts": "reported_unavailable",
            },
        }
    )


def _campaign_payload(row):
    rec = dict(row)
    rec["completion_snapshot"] = _json(
        rec.pop("completion_snapshot_json", None),
        None,
    )
    return _jsonable(rec)


def _campaign_job_payload(row):
    rec = dict(row)
    rec["command"] = _json(rec.pop("command_json", None), [])
    rec["metadata"] = _json(rec.pop("metadata_json", None), {})
    return _jsonable(rec)


def _campaign_run_records(db, campaign_id):
    out = []
    for run_row in campaign_pipeline_runs(db, int(campaign_id)):
        run_id = int(run_row["id"])
        candidates = (
            db.execute(
                """SELECT * FROM search_pipeline_candidates
                   WHERE run_id=? ORDER BY id""",
                (run_id,),
            ).fetchall()
            if _table_exists(db, "search_pipeline_candidates")
            else []
        )
        out.append(
            {
                "run": _jsonable(pipeline_run_payload(run_row)),
                "stored_run": _stored_pipeline_run_payload(run_row),
                "candidates": [
                    _pipeline_candidate_payload(row)
                    for row in candidates
                ],
            }
        )
    return out


def _campaign_job_records(db, campaign_id):
    return [
        _campaign_job_payload(row)
        for row in reversed(campaign_jobs(db, int(campaign_id), limit=100000))
    ]


def _cohort_campaign_id(cohort):
    if not isinstance(cohort, dict):
        return None
    if str(cohort.get("source_kind") or "") != "campaign":
        return None
    source = cohort.get("source")
    if not isinstance(source, dict):
        return None
    try:
        value = source.get("campaign_id")
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _campaign_landscape_analysis_ids(db, campaign_id, notebook_entries):
    ids = {
        int(link["id"])
        for entry in notebook_entries
        for link in entry.get("links", [])
        if str(link.get("kind") or "") == "landscape_analysis"
    }
    if not _table_exists(db, "research_landscape_analyses"):
        return sorted(ids)

    rows = db.execute(
        """SELECT id,primary_cohort_json,comparison_cohort_json
           FROM research_landscape_analyses
           ORDER BY id"""
    ).fetchall()
    for row in rows:
        primary = _json(row["primary_cohort_json"], {})
        comparison = _json(row["comparison_cohort_json"], None)
        if (
            _cohort_campaign_id(primary) == int(campaign_id)
            or _cohort_campaign_id(comparison) == int(campaign_id)
        ):
            ids.add(int(row["id"]))
    return sorted(ids)


def _campaign_landscape_analyses(db, analysis_ids):
    out = []
    for analysis_id in sorted({int(value) for value in analysis_ids}):
        analysis = get_landscape_analysis(db, analysis_id)
        if analysis is not None:
            out.append(_jsonable(analysis))
    return out


def _job_object_links(job):
    metadata = dict(job.get("metadata") or {})
    links = []
    for key, kind in (
        ("curve_id", "curve"),
        ("best_curve_id", "curve"),
        ("pipeline_run_id", "pipeline_run"),
        ("pool_id", "candidate_pool"),
    ):
        value = metadata.get(key)
        if value is None:
            continue
        try:
            links.append({"kind": kind, "id": int(value)})
        except (TypeError, ValueError):
            pass
    for value in metadata.get("curve_ids") or []:
        try:
            links.append({"kind": "curve", "id": int(value)})
        except (TypeError, ValueError):
            pass
    seen = set()
    out = []
    for link in links:
        key = (link["kind"], int(link["id"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(link)
    return out


def _campaign_link_graph(
    *,
    curve_ids,
    run_records,
    jobs,
    notebook,
    landscape_analyses,
):
    run_to_curves = {}
    for record in run_records:
        run_id = int(record["run"]["id"])
        run_to_curves[str(run_id)] = sorted(
            {
                int(candidate["candidate"]["curve_id"])
                for candidate in record["candidates"]
                if candidate["candidate"].get("curve_id") is not None
            }
        )

    landscape_to_curves = {}
    for analysis in landscape_analyses:
        ids = set()
        for key in ("primary_cohort", "comparison_cohort"):
            cohort = analysis.get(key)
            if not isinstance(cohort, dict):
                continue
            for value in cohort.get("curve_ids") or []:
                try:
                    ids.add(int(value))
                except (TypeError, ValueError):
                    pass
        landscape_to_curves[str(int(analysis["id"]))] = sorted(ids)

    return {
        "campaign_to_curves": [int(value) for value in curve_ids],
        "campaign_to_pipeline_runs": [
            int(record["run"]["id"])
            for record in run_records
        ],
        "campaign_to_jobs": [int(job["id"]) for job in jobs],
        "campaign_to_notebook_entries": [
            int(entry["id"]) for entry in notebook
        ],
        "campaign_to_landscape_analyses": [
            int(analysis["id"]) for analysis in landscape_analyses
        ],
        "pipeline_run_to_curves": run_to_curves,
        "job_to_objects": {
            str(int(job["id"])): _job_object_links(job)
            for job in jobs
        },
        "notebook_to_objects": {
            str(int(entry["id"])): list(entry.get("links") or [])
            for entry in notebook
        },
        "landscape_analysis_to_curves": landscape_to_curves,
    }


def build_campaign_bundle_manifest(db, campaign_id):
    """Compose one read-only Campaign Research Bundle manifest.

    Campaign Bundle composition reuses accepted object authorities. It does not
    repair lineage, create schema, recompute rank/arithmetic, or recertify points.
    """
    campaign_id = int(campaign_id)
    campaign_row = get_campaign(db, campaign_id)
    if campaign_row is None:
        raise ValueError(f"campaign #{campaign_id} not found")

    curve_ids = campaign_curve_ids_readonly(db, campaign_id)
    curves = [
        build_curve_bundle_manifest(db, curve_id)
        for curve_id in curve_ids
    ]
    run_records = _campaign_run_records(db, campaign_id)
    jobs = _campaign_job_records(db, campaign_id)

    all_notebook = campaign_notebook_entries(
        db,
        campaign_id,
        limit=100000,
    )
    notebook = sorted(
        (
            _jsonable(entry)
            for entry in all_notebook
            if str(entry.get("handoff_state") or "include") == "include"
        ),
        key=lambda entry: int(entry["id"]),
    )
    omitted_notebook = [
        entry
        for entry in all_notebook
        if str(entry.get("handoff_state") or "include") != "include"
    ]
    legacy_note = str(campaign_row["notes"] or "").strip()

    landscape_ids = _campaign_landscape_analysis_ids(
        db,
        campaign_id,
        notebook,
    )
    landscape_analyses = _campaign_landscape_analyses(db, landscape_ids)
    linked_landscape_ids = [
        int(analysis["id"]) for analysis in landscape_analyses
    ]

    run_ids = [int(record["run"]["id"]) for record in run_records]
    job_ids = [int(job["id"]) for job in jobs]
    notebook_ids = [int(entry["id"]) for entry in notebook]

    link_graph = _campaign_link_graph(
        curve_ids=curve_ids,
        run_records=run_records,
        jobs=jobs,
        notebook=notebook,
        landscape_analyses=landscape_analyses,
    )

    return _jsonable(
        {
            "manifest_version": CAMPAIGN_BUNDLE_MANIFEST_VERSION,
            "scope": "campaign",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "campaign": _campaign_payload(campaign_row),
            "links": {
                "curve_ids": [int(value) for value in curve_ids],
                "pipeline_run_ids": run_ids,
                "job_ids": job_ids,
                "notebook_entry_ids": notebook_ids,
                "landscape_analysis_ids": linked_landscape_ids,
            },
            "link_graph": link_graph,
            "curves": curves,
            "pipeline_runs": run_records,
            "jobs": jobs,
            "notebook": notebook,
            "landscape_analyses": landscape_analyses,
            "availability": {
                "curves": _availability(
                    curves,
                    reason="Campaign has no traceable retained curves",
                ),
                "pipeline_runs": _availability(
                    run_records,
                    reason="Campaign has no owned Pipeline Runs",
                ),
                "jobs": _availability(
                    jobs,
                    reason="Campaign has no owned Jobs",
                ),
                "notebook": {
                    "available": bool(notebook or legacy_note),
                    "count": len(notebook) + (1 if legacy_note else 0),
                    "reason": (
                        None
                        if notebook or legacy_note
                        else "Campaign has no notebook entries marked Include"
                    ),
                },
                "notebook_private_or_resolved_omitted": {
                    "available": bool(omitted_notebook),
                    "count": len(omitted_notebook),
                    "reason": (
                        "private/resolved notebook entries are intentionally excluded"
                        if omitted_notebook
                        else None
                    ),
                },
                "landscape_analyses": _availability(
                    landscape_analyses,
                    reason=(
                        "no saved Landscape analysis references this Campaign "
                        "or is linked from an included notebook entry"
                    ),
                ),
            },
            "reproduction": {
                "core_version": str(CORE_VERSION),
                "curve_manifest_version": CURVE_BUNDLE_MANIFEST_VERSION,
                "campaign_manifest_version": CAMPAIGN_BUNDLE_MANIFEST_VERSION,
                "pipeline_run_manifests": [
                    record["run"].get("manifest") or {}
                    for record in run_records
                ],
            },
            "policy": {
                "read_only_snapshot": True,
                "scientific_state_mutation": False,
                "curve_truth": "Curve Research Bundle manifest",
                "notebook_export_state": "include only",
                "landscape_truth": "frozen saved analysis configuration",
                "missing_optional_artifacts": "reported_unavailable",
            },
        }
    )


def _campaign_readme_rank_text(rank_state):
    exact = rank_state.get("exact_rank")
    if exact is not None:
        return f"exact rank = {int(exact)}"
    lower = int(rank_state.get("rigorous_lower") or 0)
    upper = rank_state.get("rigorous_upper")
    if rank_state.get("rank_inconsistent"):
        if upper is None:
            return f"inconsistent rigorous evidence; lower ≥ {lower}"
        return (
            "inconsistent rigorous evidence; "
            f"lower ≥ {lower}, upper ≤ {int(upper)}"
        )
    if upper is not None:
        return f"rigorous rank ≥ {lower}; rigorous upper ≤ {int(upper)}"
    return f"rigorous rank ≥ {lower}"


def render_campaign_bundle_readme(manifest):
    """Render the human Campaign Bundle summary only from manifest facts."""
    if str(manifest.get("scope") or "") != "campaign":
        raise ValueError("Campaign Bundle README requires a campaign manifest")

    campaign = dict(manifest.get("campaign") or {})
    lines = [
        f'# Campaign Research Bundle — {campaign.get("name") or "Untitled Campaign"}',
        "",
        f'- Campaign #{campaign.get("id")}',
        f'- Status: {campaign.get("status") or "unknown"}',
    ]
    if campaign.get("objective"):
        lines.append(f'- Objective: {campaign["objective"]}')
    if campaign.get("target_rank") is not None:
        lines.append(f'- Target rank: {int(campaign["target_rank"])}')
    lines.extend(
        [
            f'- Manifest version: {int(manifest.get("manifest_version") or 0)}',
            f'- Core version: {manifest.get("reproduction", {}).get("core_version") or "unknown"}',
            "",
            "## Curves",
            "",
        ]
    )

    curves = list(manifest.get("curves") or [])
    if not curves:
        lines.extend(["No traceable retained curves.", ""])
    for bundle in curves:
        curve = dict(bundle.get("curve") or {})
        rank_state = dict(bundle.get("rank_state") or {})
        lines.extend(
            [
                f'### Curve #{curve.get("id")}',
                "",
                f'- Family: {curve.get("family")}',
                f'- parameter {curve.get("parameter")}',
                f'- a-invariants: {curve.get("a_invariants")}',
                f'- Rank: {_campaign_readme_rank_text(rank_state)}',
            ]
        )
        native = dict(curve.get("native_identity") or {})
        if native.get("native_family_key"):
            lines.append(f'- Native family: {native["native_family_key"]}')
        if native.get("native_parameter"):
            lines.append(f'- Native parameter: {native["native_parameter"]}')
        if native.get("chart_id"):
            lines.append(f'- Chart: {native["chart_id"]}')
        lines.append("")

    lines.extend(["## Pipeline Runs", ""])
    runs = list(manifest.get("pipeline_runs") or [])
    if not runs:
        lines.extend(["No Campaign-owned Pipeline Runs.", ""])
    else:
        for record in runs:
            run = dict(record.get("run") or {})
            manifest_state = dict(run.get("manifest") or {})
            lines.append(
                f'- Run #{run.get("id")} · {run.get("pipeline_name")} · '
                f'{run.get("status")} · snapshot '
                f'{str(manifest_state.get("run_snapshot_hash") or "unavailable")}'
            )
        lines.append("")

    lines.extend(["## Jobs", ""])
    jobs = list(manifest.get("jobs") or [])
    if not jobs:
        lines.extend(["No Campaign-owned Jobs.", ""])
    else:
        for job in jobs:
            lines.append(
                f'- Job #{job.get("id")} · {job.get("label")} · {job.get("status")}'
            )
        lines.append("")

    lines.extend(["## Research Notebook", ""])
    notebook = list(manifest.get("notebook") or [])
    legacy_note = str(campaign.get("notes") or "").strip()
    if legacy_note:
        lines.extend(
            [
                "### Initial campaign note",
                "",
                legacy_note,
                "",
            ]
        )
    if not notebook and not legacy_note:
        lines.extend(["No notebook entries marked Include.", ""])
    else:
        for entry in notebook:
            entry_type = str(entry.get("entry_type") or "note").replace("_", " ").title()
            lines.extend(
                [
                    f'### {entry_type} · entry #{entry.get("id")}',
                    "",
                    str(entry.get("body") or ""),
                    "",
                ]
            )

    lines.extend(["## Landscape Analyses", ""])
    analyses = list(manifest.get("landscape_analyses") or [])
    if not analyses:
        lines.extend(["No linked saved Landscape analyses.", ""])
    else:
        for analysis in analyses:
            lines.append(
                f'- Landscape analysis #{analysis.get("id")} · '
                f'{analysis.get("name")} · registry '
                f'{str(analysis.get("dimension_registry_hash") or "")}'
            )
        lines.append("")

    lines.extend(
        [
            "## Reproducibility policy",
            "",
            "This README is rendered from the machine-readable Campaign Bundle manifest.",
            "It does not recompute rank, arithmetic, point independence, or cohort membership.",
            "",
        ]
    )
    return "\n".join(lines)


def _json_text(value):
    return json.dumps(
        _jsonable(value),
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
    ) + "\n"


def _jsonl_text(records):
    return "".join(
        json.dumps(
            _jsonable(record),
            sort_keys=True,
            ensure_ascii=True,
        ) + "\n"
        for record in records
    )


def _campaign_notebook_markdown(manifest):
    lines = ["# Research Notebook", ""]
    campaign = dict(manifest.get("campaign") or {})
    legacy_note = str(campaign.get("notes") or "").strip()
    entries = list(manifest.get("notebook") or [])
    if legacy_note:
        lines.extend(
            [
                "## Initial campaign note",
                "",
                legacy_note,
                "",
            ]
        )
    if not entries and not legacy_note:
        lines.extend(["No notebook entries marked Include.", ""])
        return "\n".join(lines)
    for entry in entries:
        label = str(entry.get("entry_type") or "note").replace("_", " ").title()
        lines.extend(
            [
                f'## {label} · entry #{entry.get("id")}',
                "",
                str(entry.get("body") or ""),
            ]
        )
        links = list(entry.get("links") or [])
        if links:
            lines.extend(
                [
                    "",
                    "Links: "
                    + ", ".join(
                        f'{link.get("kind")} #{link.get("id")}'
                        for link in links
                    ),
                ]
            )
        lines.append("")
    return "\n".join(lines)


def campaign_bundle_files(manifest):
    """Return the recommended Campaign Research Bundle file map.

    The file map is derived only from the accepted Campaign manifest. It is
    suitable for in-memory ZIP creation or another caller-owned export target.
    """
    if str(manifest.get("scope") or "") != "campaign":
        raise ValueError("Campaign Bundle files require a campaign manifest")

    files = {
        "README.md": render_campaign_bundle_readme(manifest),
        "manifest.json": _json_text(manifest),
        "campaign.json": _json_text(manifest.get("campaign") or {}),
        "pipeline-runs.json": _json_text(manifest.get("pipeline_runs") or []),
        "jobs.jsonl": _jsonl_text(manifest.get("jobs") or []),
        "landscape-analyses.json": _json_text(
            manifest.get("landscape_analyses") or []
        ),
    }
    if manifest.get("notebook") or str(
        (manifest.get("campaign") or {}).get("notes") or ""
    ).strip():
        files["notebook.md"] = _campaign_notebook_markdown(manifest)

    for curve_bundle in manifest.get("curves") or []:
        curve = dict(curve_bundle.get("curve") or {})
        curve_id = int(curve["id"])
        prefix = f"curves/curve-{curve_id}"
        files[f"{prefix}/manifest.json"] = _json_text(curve_bundle)
        files[f"{prefix}/curve.json"] = _json_text(curve)
        files[f"{prefix}/points.jsonl"] = _jsonl_text(
            curve_bundle.get("points") or []
        )
        files[f"{prefix}/rank-evidence.jsonl"] = _jsonl_text(
            curve_bundle.get("rank_evidence") or []
        )
        files[f"{prefix}/quartics.jsonl"] = _jsonl_text(
            (curve_bundle.get("analysis_artifacts") or {}).get("quartics") or []
        )
        files[f"{prefix}/lattices.jsonl"] = _jsonl_text(
            (curve_bundle.get("analysis_artifacts") or {}).get("lattices") or []
        )
        files[f"{prefix}/catalog-references.json"] = _json_text(
            curve_bundle.get("external_references") or {}
        )
    return files


def build_campaign_bundle_archive(manifest):
    """Build an in-memory ZIP from one Campaign manifest."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(
        buffer,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        for path, content in sorted(campaign_bundle_files(manifest).items()):
            archive.writestr(str(path), content)
    return buffer.getvalue()
