import inspect
import json

from rank42.candidate_ranking_state import pool_candidate_ranking_state
from rank42.candidates import create_pool, replace_pool_rows
from rank42.catalog import import_icarm_payload
from rank42.db import connect, now, upsert_curve, update_curve
from rank42.pipeline_state import (
    create_pipeline_run,
    ensure_pipeline_schema,
    get_pipeline_run,
    pipeline_payload,
    save_pipeline,
    upsert_pipeline_candidate,
)
from rank42.points import upsert_point
from rank42.rank_evidence import record_rank_evidence
from rank42.research_bundle import build_curve_bundle_manifest


MODEL = ["0", "0", "0", "-1", "1"]


def _curve(db, *, parameter="5/7"):
    return upsert_curve(
        db,
        family="bundle-family",
        parameter=parameter,
        a_invariants_json=json.dumps(MODEL),
        descent_lower=1,
        conductor="37",
        discriminant="-37",
        bad_primes_json="[37]",
        root_number=-1,
        plugin_id="fixture-family",
        plugin_version="1.2.3",
        family_spec="json:fixture-family",
        family_sha256="family-sha",
        adapter_sha256="adapter-sha",
        plugin_manifest_sha256="manifest-sha",
        native_family_key="fixture-family:default",
        native_family_spec="json:fixture-family",
        native_parameter=parameter,
        chart_id="identity",
        chart_parameter=parameter,
        chart_map_fingerprint="chart-sha",
    )


def test_curve_bundle_manifest_uses_authoritative_rank_and_arithmetic(tmp_path):
    db = connect(tmp_path / "bundle-authorities.db")
    try:
        curve_id = _curve(db)
        record_rank_evidence(
            db,
            curve_id=curve_id,
            model=MODEL,
            data={
                "engine": "bundle-test",
                "evidence_type": "rank_bounds",
                "status": "completed",
                "rigorous": True,
                "rigorous_lower": 5,
                "rigorous_upper": 7,
                "exact_rank": None,
                "conditional_analytic_upper": None,
                "conditional_mw_upper": None,
                "numerical_rank_signal": None,
                "assumptions": [],
                "points_found": [],
                "options": {"fixture": True},
            },
        )

        manifest = build_curve_bundle_manifest(db, curve_id)

        assert manifest["manifest_version"] == 1
        assert manifest["scope"] == "curve"
        assert manifest["curve"]["id"] == curve_id
        assert manifest["curve"]["a_invariants"] == MODEL
        assert manifest["curve"]["native_identity"]["native_parameter"] == "5/7"
        assert manifest["rank_state"]["rigorous_lower"] == 5
        assert manifest["rank_state"]["rigorous_upper"] == 7
        assert manifest["rank_state"]["exact_rank"] is None
        assert manifest["rank_state"]["rank_inconsistent"] is False
        assert manifest["arithmetic"]["local"]["conductor"] == 37
        assert manifest["arithmetic"]["display"]["conductor"] == 37
        assert all(
            refs == []
            for refs in manifest["arithmetic"]["references"].values()
        )
        assert manifest["reproduction"]["curve_fingerprints"] == {
            "plugin_id": "fixture-family",
            "plugin_version": "1.2.3",
            "family_sha256": "family-sha",
            "adapter_sha256": "adapter-sha",
            "plugin_manifest_sha256": "manifest-sha",
            "chart_map_fingerprint": "chart-sha",
        }
        json.dumps(manifest, sort_keys=True)
    finally:
        db.close()


def test_curve_bundle_snapshots_points_and_persisted_witness_provenance(tmp_path):
    db = connect(tmp_path / "bundle-points.db")
    try:
        curve_id = _curve(db)
        witness = upsert_point(
            db,
            curve_id=curve_id,
            x="0",
            y="1",
            source="exact_fixture",
            role="rigorous_witness",
            exact_verified=True,
            independence_status="rigorous_independent",
            rigorous_independent=True,
            search_ref="job:12",
            plugin_id="fixture-family",
            metadata={"certificate": "stored-only"},
        )
        dependent = upsert_point(
            db,
            curve_id=curve_id,
            x="1",
            y="1",
            source="exact_fixture",
            role="candidate",
            exact_verified=True,
            independence_status="dependent",
            rigorous_independent=False,
            metadata={"reason": "known relation"},
        )

        manifest = build_curve_bundle_manifest(db, curve_id)
        point_ids = [row["id"] for row in manifest["points"]]

        assert int(witness["id"]) in point_ids
        assert int(dependent["id"]) in point_ids
        assert manifest["witness_basis"]["policy"] == "persisted_rigorous_independent_points"
        assert manifest["witness_basis"]["point_ids"] == [int(witness["id"])]
        stored = next(row for row in manifest["points"] if row["id"] == int(witness["id"]))
        assert stored["metadata"]["certificate"] == "stored-only"
        assert stored["search_ref"] == "job:12"

        source = inspect.getsource(build_curve_bundle_manifest)
        assert "rigorous_witness_basis(" not in source
        assert "certif" not in source.lower()
    finally:
        db.close()


def test_curve_bundle_preserves_candidate_and_pipeline_run_provenance(tmp_path):
    db = connect(tmp_path / "bundle-pipeline.db")
    try:
        ensure_pipeline_schema(db)
        curve_id = _curve(db)

        pool = create_pool(
            db,
            name="bundle-pool",
            plugin_id="fixture-family",
            plugin_version="1.2.3",
            family_spec="json:fixture-family",
            family_sha256="family-sha",
            adapter_sha256="adapter-sha",
            plugin_manifest_sha256="manifest-sha",
            generation={"engine": "fixture"},
        )
        replace_pool_rows(
            db,
            int(pool["id"]),
            [{
                "parameter": "5/7",
                "score": 12.25,
                "ranking_kind": "nagao",
                "ranking_value": 12.25,
                "score_provenance": {
                    "ranking_kind": "nagao",
                    "algorithm": "fixture-nagao",
                },
                "native_family_key": "fixture-family:default",
                "native_family_spec": "json:fixture-family",
                "native_parameter": "5/7",
                "chart_id": "identity",
                "chart_parameter": "5/7",
                "chart_map_fingerprint": "chart-sha",
            }],
        )
        db.execute(
            "UPDATE candidates SET curve_id=?,status='retained' WHERE pool_id=?",
            (curve_id, int(pool["id"])),
        )
        db.commit()

        pipeline_id = save_pipeline(
            db,
            name="Bundle Pipeline",
            target_mode="curve",
            stages=[{"id": "rank_bounds", "config": {}}],
            config={"target_rank": 8},
        )
        definition = pipeline_payload(
            db.execute(
                "SELECT * FROM search_pipeline_definitions WHERE id=?",
                (pipeline_id,),
            ).fetchone()
        )
        run_id = create_pipeline_run(
            db,
            pipeline_id=pipeline_id,
            pipeline_name="Bundle Pipeline",
            target_mode="curve",
            target={"curve_id": curve_id},
            stages=[{"id": "rank_bounds", "config": {}}],
            run_config={
                "source_pipeline_id": pipeline_id,
                "source_pipeline_revision": definition["revision"],
                "source_pipeline_hash": definition["content_hash"],
                "plugin_id": "fixture-family",
                "plugin_variant": "default",
            },
        )
        upsert_pipeline_candidate(
            db,
            run_id=run_id,
            provider_key="curve",
            parameter=f"curve:{curve_id}",
            score=9.5,
            score_provenance={
                "ranking_kind": "nagao",
                "algorithm": "pipeline-fixture",
            },
            curve_id=curve_id,
            status="completed",
            rigorous_lower=5,
        )

        manifest = build_curve_bundle_manifest(db, curve_id)

        assert len(manifest["search_provenance"]["candidate_pool_matches"]) == 1
        pool_match = manifest["search_provenance"]["candidate_pool_matches"][0]
        assert pool_match["pool_id"] == int(pool["id"])
        assert pool_match["ranking"]["ranking_kind"] == "nagao"
        assert pool_match["ranking"]["score_provenance"]["algorithm"] == "fixture-nagao"
        assert pool_match["metadata"]["ranking_kind"] == "nagao"

        assert len(manifest["search_provenance"]["pipeline_runs"]) == 1
        run = manifest["search_provenance"]["pipeline_runs"][0]
        assert run["run"]["id"] == run_id
        assert run["run"]["manifest"]["source_pipeline_id"] == pipeline_id
        assert run["run"]["manifest"]["source_pipeline_revision"] == definition["revision"]
        assert run["run"]["manifest"]["source_pipeline_hash"] == definition["content_hash"]
        assert len(run["run"]["manifest"]["run_snapshot_hash"]) == 64
        assert run["run"]["manifest"]["core_version"]
        assert run["run"]["manifest"]["pipeline_schema_version"] >= 1
        assert run["run"]["manifest"]["pipeline_catalog_version"] >= 1
        assert len(run["run"]["manifest"]["pipeline_catalog_hash"]) == 64
        assert run["run"]["manifest"]["plugin_id"] == "fixture-family"
        assert run["run"]["manifest"]["plugin_variant"] == "default"
        assert run["stored_run"]["target"] == {"curve_id": curve_id}
        assert run["stored_run"]["stages"] == [{"config": {}, "id": "rank_bounds"}]
        assert run["stored_run"]["manifest"]["run_snapshot_hash"] == run["run"]["manifest"]["run_snapshot_hash"]
        assert run["candidates"][0]["ranking"]["score_provenance"]["algorithm"] == "pipeline-fixture"
    finally:
        db.close()




def test_curve_bundle_separates_local_arithmetic_from_catalog_reference(tmp_path):
    db = connect(tmp_path / "bundle-arithmetic-reference.db")
    try:
        curve_id = _curve(db, parameter="17/19")
        update_curve(
            db,
            curve_id,
            conductor="37",
            discriminant="-11",
            bad_primes_json="[11]",
        )
        import_icarm_payload(
            db,
            {
                "count": 1,
                "curves": [{
                    "id": 9901,
                    "ainvs": MODEL,
                    "rank_lower_bound": 1,
                    "points": [],
                    "conductor": "41",
                    "discriminant": "-13",
                    "bad_primes": [13],
                }],
            },
        )
        ts = now()
        db.execute(
            """INSERT INTO curve_catalog_checks(
                   curve_id,source,status,source_label,source_url,
                   source_rank,metadata_json,checked_at
               ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                curve_id,
                "icarm",
                "known",
                "9901",
                "https://elliptic-rank.icarm.cloud/curve/9901",
                1,
                "{}",
                ts,
            ),
        )
        db.commit()

        manifest = build_curve_bundle_manifest(db, curve_id)

        assert manifest["arithmetic"]["local"]["conductor"] == 37
        assert manifest["arithmetic"]["display"]["conductor"] == 37
        assert manifest["arithmetic"]["references"]["conductor"][0]["value"] == 41
        assert manifest["arithmetic"]["provenance"]["conductor"]["kind"] == "local_persisted"
        assert "conductor" in manifest["arithmetic"]["conflicts"]
        assert manifest["external_references"]["arithmetic_references"]["conductor"][0]["value"] == 41
    finally:
        db.close()

def test_curve_bundle_marks_missing_optional_artifacts_and_is_read_only(tmp_path):
    db = connect(tmp_path / "bundle-readonly.db")
    try:
        curve_id = _curve(db, parameter="11/13")
        traced = []
        db.set_trace_callback(traced.append)

        manifest = build_curve_bundle_manifest(db, curve_id)

        writes = [
            stmt for stmt in traced
            if stmt.lstrip().upper().startswith(
                ("CREATE ", "ALTER ", "INSERT ", "UPDATE ", "DELETE ", "REPLACE ")
            )
        ]
        assert writes == []
        assert manifest["availability"]["pipeline_runs"]["available"] is False
        assert manifest["availability"]["candidate_pool_matches"]["available"] is False
        assert manifest["availability"]["lattices"]["available"] is False
        assert manifest["availability"]["quartics"]["available"] is False
        assert manifest["reproduction"]["core_commit"]["available"] is False
        assert "not persisted" in manifest["reproduction"]["core_commit"]["reason"]
    finally:
        db.close()
