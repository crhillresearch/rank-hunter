import json

from rank42 import __version__ as CORE_VERSION
from rank42.candidates import create_pool, replace_pool_rows
from rank42.db import connect, get_curve, upsert_curve
from rank42.landscape_dimensions import LANDSCAPE_DIMENSION_REGISTRY_HASH
from rank42.landscape_state import (
    LANDSCAPE_ANALYSIS_SCHEMA_VERSION,
    LANDSCAPE_COHORT_VERSION,
    build_landscape_cohort,
    get_landscape_analysis,
    save_landscape_analysis,
)
from rank42.manage_store import create_campaign, ensure_manage_schema
from rank42.pipeline_state import (
    create_pipeline_run,
    ensure_pipeline_schema,
    pipeline_payload,
    save_pipeline,
    upsert_pipeline_candidate,
)
from rank42.rank_evidence import record_rank_evidence


MODEL = ["0", "0", "0", "-1", "1"]


def _curve(
    db,
    *,
    family="cohort-family",
    parameter="1/2",
    native_family_key="cohort-family:default",
    native_parameter=None,
    chart_id="identity",
):
    native_parameter = parameter if native_parameter is None else native_parameter
    return upsert_curve(
        db,
        family=family,
        parameter=parameter,
        a_invariants_json=json.dumps(MODEL),
        family_spec=f"json:{family}",
        family_sha256=f"{family}-sha",
        plugin_id="cohort-fixture",
        plugin_version="1.0",
        native_family_key=native_family_key,
        native_family_spec=f"json:{family}",
        native_parameter=native_parameter,
        chart_id=chart_id,
        chart_parameter=parameter,
        chart_map_fingerprint=f"{chart_id}-sha",
    )


def test_family_cohort_freezes_membership_after_unrelated_new_curve(tmp_path):
    db = connect(tmp_path / "family-cohort.db")
    try:
        first = _curve(db, parameter="1/2")
        second = _curve(db, parameter="2/3")

        cohort = build_landscape_cohort(
            db,
            source_kind="family",
            source={"native_family_key": "cohort-family:default"},
        )

        assert cohort["cohort_version"] == LANDSCAPE_COHORT_VERSION
        assert cohort["source_kind"] == "family"
        assert cohort["curve_ids"] == [first, second]
        assert len(cohort["member_hash"]) == 64
        assert cohort["dimension_registry_hash"] == LANDSCAPE_DIMENSION_REGISTRY_HASH

        third = _curve(db, parameter="3/5")
        assert third not in cohort["curve_ids"]
        assert [m["curve_id"] for m in cohort["members"]] == [first, second]

        refreshed = build_landscape_cohort(
            db,
            source_kind="family",
            source={"native_family_key": "cohort-family:default"},
        )
        assert refreshed["curve_ids"] == [first, second, third]
    finally:
        db.close()


def test_explicit_curve_cohort_preserves_native_and_chart_identity(tmp_path):
    db = connect(tmp_path / "explicit-cohort.db")
    try:
        curve_id = _curve(
            db,
            parameter="9/14",
            native_parameter="5/7",
            chart_id="mobius-a",
        )
        cohort = build_landscape_cohort(
            db,
            source_kind="explicit_curves",
            source={"curve_ids": [curve_id]},
        )

        member = cohort["members"][0]
        assert member["kind"] == "curve"
        assert member["curve_id"] == curve_id
        assert member["native_family_key"] == "cohort-family:default"
        assert member["native_parameter"] == "5/7"
        assert member["chart_id"] == "mobius-a"
        assert member["chart_parameter"] == "9/14"
        assert member["chart_map_fingerprint"] == "mobius-a-sha"
    finally:
        db.close()


def test_candidate_pool_cohort_freezes_ranking_and_coordinate_provenance(tmp_path):
    db = connect(tmp_path / "pool-cohort.db")
    try:
        pool = create_pool(
            db,
            name="cohort-pool",
            plugin_id="cohort-fixture",
            plugin_version="1.0",
            family_spec="json:cohort-family",
            family_sha256="family-sha",
            adapter_sha256="adapter-sha",
            plugin_manifest_sha256="manifest-sha",
            generation={"engine": "fixture"},
        )
        replace_pool_rows(
            db,
            int(pool["id"]),
            [{
                "parameter": "9/14",
                "score": 12.75,
                "ranking_kind": "nagao",
                "ranking_value": 12.75,
                "score_provenance": {
                    "ranking_kind": "nagao",
                    "algorithm": "fixture-nagao",
                },
                "native_family_key": "cohort-family:default",
                "native_family_spec": "json:cohort-family",
                "native_parameter": "5/7",
                "chart_id": "mobius-a",
                "chart_parameter": "9/14",
                "chart_map_fingerprint": "chart-sha",
            }],
        )

        cohort = build_landscape_cohort(
            db,
            source_kind="candidate_pool",
            source={"pool_id": int(pool["id"])},
        )

        assert cohort["source"]["pool_id"] == int(pool["id"])
        assert cohort["source"]["family_sha256"] == "family-sha"
        assert len(cohort["members"]) == 1
        member = cohort["members"][0]
        assert member["kind"] == "candidate_pool"
        assert member["ranking"]["ranking_kind"] == "nagao"
        assert member["ranking"]["ranking_value"] == 12.75
        assert member["ranking"]["score_provenance"]["algorithm"] == "fixture-nagao"
        assert member["native_parameter"] == "5/7"
        assert member["chart_parameter"] == "9/14"
        assert member["chart_map_fingerprint"] == "chart-sha"
    finally:
        db.close()


def test_pipeline_run_cohort_freezes_run_manifest_and_candidate_members(tmp_path):
    db = connect(tmp_path / "run-cohort.db")
    try:
        ensure_pipeline_schema(db)
        curve_id = _curve(db)
        pipeline_id = save_pipeline(
            db,
            name="Cohort pipeline",
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
            pipeline_name="Cohort pipeline",
            target_mode="curve",
            target={"curve_id": curve_id},
            stages=[{"id": "rank_bounds", "config": {}}],
            run_config={
                "source_pipeline_id": pipeline_id,
                "source_pipeline_revision": definition["revision"],
                "source_pipeline_hash": definition["content_hash"],
                "plugin_id": "cohort-fixture",
                "plugin_variant": "default",
            },
        )
        upsert_pipeline_candidate(
            db,
            run_id=run_id,
            provider_key="curve",
            parameter=f"curve:{curve_id}",
            score=9.25,
            score_provenance={
                "ranking_kind": "nagao",
                "algorithm": "pipeline-nagao",
            },
            curve_id=curve_id,
            status="completed",
            rigorous_lower=4,
        )

        cohort = build_landscape_cohort(
            db,
            source_kind="pipeline_run",
            source={"run_id": run_id},
        )

        assert cohort["source"]["run_id"] == run_id
        assert cohort["source"]["manifest"]["source_pipeline_id"] == pipeline_id
        assert cohort["source"]["manifest"]["source_pipeline_revision"] == definition["revision"]
        assert cohort["source"]["manifest"]["source_pipeline_hash"] == definition["content_hash"]
        assert len(cohort["source"]["manifest"]["run_snapshot_hash"]) == 64
        assert cohort["members"][0]["kind"] == "pipeline_candidate"
        assert cohort["members"][0]["ranking"]["ranking_kind"] == "nagao"
        assert cohort["members"][0]["ranking"]["score_provenance"]["algorithm"] == "pipeline-nagao"
        assert cohort["members"][0]["curve_identity"]["native_family_key"] == "cohort-family:default"
        assert cohort["members"][0]["curve_identity"]["chart_id"] == "identity"
        assert cohort["curve_ids"] == [curve_id]
    finally:
        db.close()


def test_campaign_cohort_uses_readonly_campaign_curve_projection(tmp_path):
    db = connect(tmp_path / "campaign-cohort.db")
    try:
        ensure_manage_schema(db)
        ensure_pipeline_schema(db)
        campaign_id = create_campaign(db, name="Landscape campaign")
        campaign = db.execute(
            "SELECT * FROM research_campaigns WHERE id=?",
            (campaign_id,),
        ).fetchone()
        curve_id = _curve(db)

        run_id = create_pipeline_run(
            db,
            pipeline_name="Campaign cohort run",
            target_mode="curve",
            target={"curve_id": curve_id},
            stages=[{"id": "rank_bounds", "config": {}}],
            run_config={
                "campaign_id": campaign_id,
                "campaign_created_at": campaign["created_at"],
            },
        )
        upsert_pipeline_candidate(
            db,
            run_id=run_id,
            provider_key="curve",
            parameter=f"curve:{curve_id}",
            curve_id=curve_id,
            status="completed",
            rigorous_lower=2,
        )

        traced = []
        db.set_trace_callback(traced.append)
        cohort = build_landscape_cohort(
            db,
            source_kind="campaign",
            source={"campaign_id": campaign_id},
        )
        writes = [
            stmt for stmt in traced
            if stmt.lstrip().upper().startswith(
                ("CREATE ", "ALTER ", "INSERT ", "UPDATE ", "DELETE ", "REPLACE ")
            )
        ]

        assert writes == []
        assert cohort["curve_ids"] == [curve_id]
        assert cohort["source"]["campaign_id"] == campaign_id
        assert cohort["source"]["campaign_created_at"] == campaign["created_at"]
    finally:
        db.close()


def test_cohort_filters_use_authoritative_rank_state(tmp_path):
    db = connect(tmp_path / "cohort-filter.db")
    try:
        low = _curve(db, parameter="1/3")
        high = _curve(db, parameter="2/5")
        record_rank_evidence(
            db,
            curve_id=high,
            model=MODEL,
            data={
                "engine": "cohort-filter",
                "evidence_type": "rank_bounds",
                "status": "completed",
                "rigorous": True,
                "rigorous_lower": 6,
                "rigorous_upper": None,
                "exact_rank": None,
                "conditional_analytic_upper": None,
                "conditional_mw_upper": None,
                "numerical_rank_signal": None,
                "assumptions": [],
                "points_found": [],
                "options": {},
            },
        )

        cohort = build_landscape_cohort(
            db,
            source_kind="family",
            source={"native_family_key": "cohort-family:default"},
            filters={"minimum_rigorous_lower": 5},
        )

        assert low not in cohort["curve_ids"]
        assert cohort["curve_ids"] == [high]
        assert cohort["filters"] == {"minimum_rigorous_lower": 5}
    finally:
        db.close()


def test_saved_landscape_analysis_records_frozen_cohorts_and_registry_provenance(tmp_path):
    db = connect(tmp_path / "saved-analysis.db")
    try:
        first = _curve(db, parameter="1/7")
        second = _curve(db, parameter="2/7")
        primary = build_landscape_cohort(
            db,
            source_kind="explicit_curves",
            source={"curve_ids": [first]},
        )
        comparison = build_landscape_cohort(
            db,
            source_kind="explicit_curves",
            source={"curve_ids": [second]},
        )

        analysis_id = save_landscape_analysis(
            db,
            name="Rank vs conductor",
            primary_cohort=primary,
            comparison_cohort=comparison,
            dimension_ids=["rank.rigorous_lower", "arithmetic.log_conductor"],
            feature_definitions=[
                {"kind": "bad_prime_contains", "prime": 41},
            ],
        )
        saved = get_landscape_analysis(db, analysis_id)

        assert saved["analysis_schema_version"] == LANDSCAPE_ANALYSIS_SCHEMA_VERSION
        assert saved["name"] == "Rank vs conductor"
        assert saved["primary_cohort"]["member_hash"] == primary["member_hash"]
        assert saved["comparison_cohort"]["member_hash"] == comparison["member_hash"]
        assert [rec["id"] for rec in saved["dimensions"]] == [
            "rank.rigorous_lower",
            "arithmetic.log_conductor",
        ]
        assert all(rec["version"] == 1 for rec in saved["dimensions"])
        assert saved["dimension_registry_hash"] == LANDSCAPE_DIMENSION_REGISTRY_HASH
        assert saved["core_version"] == CORE_VERSION
        assert saved["feature_definitions"][0]["kind"] == "bad_prime_contains"
        assert saved["feature_definitions"][0]["prime"] == 41
        assert saved["created_at"]

        _curve(db, parameter="3/7")
        reloaded = get_landscape_analysis(db, analysis_id)
        assert reloaded["primary_cohort"]["curve_ids"] == [first]
        assert reloaded["comparison_cohort"]["curve_ids"] == [second]
    finally:
        db.close()


def test_saved_analysis_persistence_does_not_mutate_scientific_state(tmp_path):
    db = connect(tmp_path / "analysis-boundary.db")
    try:
        curve_id = _curve(db)
        cohort = build_landscape_cohort(
            db,
            source_kind="explicit_curves",
            source={"curve_ids": [curve_id]},
        )
        before_curve = dict(get_curve(db, curve_id))
        before_evidence = db.execute(
            "SELECT COUNT(*) FROM rank_evidence WHERE curve_id=?",
            (curve_id,),
        ).fetchone()[0]

        save_landscape_analysis(
            db,
            name="Boundary test",
            primary_cohort=cohort,
            dimension_ids=["rank.rigorous_lower"],
        )

        after_curve = dict(get_curve(db, curve_id))
        after_evidence = db.execute(
            "SELECT COUNT(*) FROM rank_evidence WHERE curve_id=?",
            (curve_id,),
        ).fetchone()[0]
        assert after_curve == before_curve
        assert after_evidence == before_evidence
    finally:
        db.close()
