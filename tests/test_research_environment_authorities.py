import json
from pathlib import Path

from rank42.candidate_ranking_state import (
    pipeline_candidate_ranking_state,
    pool_candidate_ranking_state,
)
from rank42.candidates import candidate_rows, create_pool, replace_pool_rows
from rank42.curve_arithmetic_state import get_curve_arithmetic_state
from rank42.curve_research_state import get_curve_research_state
from rank42.db import connect, upsert_curve, update_curve
from rank42.manage_store import (
    campaign_curve_ids_readonly,
    create_campaign,
    ensure_manage_schema,
)
from rank42.pipeline_state import (
    create_pipeline_run,
    ensure_pipeline_schema,
    get_pipeline_run,
    pipeline_payload,
    pipeline_run_payload,
    save_pipeline,
    upsert_pipeline_candidate,
)
from rank42.rank_evidence import record_rank_evidence


ROOT = Path(__file__).resolve().parents[1]
MODEL = ["0", "0", "0", "-1", "1"]


def test_candidate_ranking_projection_preserves_pool_provenance(tmp_path):
    db = connect(tmp_path / "research-authorities.db")
    try:
        pool = create_pool(
            db,
            name="research-r1",
            plugin_id="fixture-family",
            plugin_version="1.2.3",
            family_spec="json:fixture-family",
            family_sha256="family-sha",
            adapter_sha256="adapter-sha",
            plugin_manifest_sha256="manifest-sha",
            generation={"engine": "sieve"},
        )
        replace_pool_rows(
            db,
            int(pool["id"]),
            [{
                "parameter": "7/11",
                "score": 12.5,
                "prime_bound": 523,
                "prime_terms": 99,
                "ranking_kind": "nagao",
                "ranking_value": 12.5,
                "score_provenance": {
                    "ranking_kind": "nagao",
                    "algorithm": "fixture-nagao-v1",
                    "prime_bound": 523,
                },
                "native_family_key": "fixture-family:default",
                "native_family_spec": "json:fixture-family",
                "native_parameter": "7/11",
                "chart_id": "identity",
                "chart_parameter": "7/11",
                "chart_map_fingerprint": "chart-sha",
            }],
        )

        row = candidate_rows(db, int(pool["id"]), limit=None)[0]
        state = pool_candidate_ranking_state(row)

        assert state["source_kind"] == "candidate_pool"
        assert state["ranking_kind"] == "nagao"
        assert state["ranking_label"] == "Nagao"
        assert state["ranking_value"] == 12.5
        assert state["score_provenance"]["algorithm"] == "fixture-nagao-v1"
        assert state["plugin_id"] == "fixture-family"
        assert state["plugin_version"] == "1.2.3"
        assert state["family_sha256"] == "family-sha"
        assert state["adapter_sha256"] == "adapter-sha"
        assert state["plugin_manifest_sha256"] == "manifest-sha"
        assert state["native_family_key"] == "fixture-family:default"
        assert state["native_parameter"] == "7/11"
        assert state["chart_id"] == "identity"
        assert state["chart_map_fingerprint"] == "chart-sha"
    finally:
        db.close()


def test_pipeline_candidate_ranking_projection_preserves_score_provenance(tmp_path):
    db = connect(tmp_path / "pipeline-ranking.db")
    try:
        ensure_pipeline_schema(db)
        run_id = create_pipeline_run(
            db,
            pipeline_name="Research R1",
            target_mode="general",
            target={"pool_mode": "open"},
            stages=[{"id": "nagao_screen", "config": {"prime_bound": 523}}],
        )
        provenance = {
            "ranking_kind": "nagao",
            "algorithm": "short-nagao-v1",
            "prime_bound": 523,
        }
        row = upsert_pipeline_candidate(
            db,
            run_id=run_id,
            provider_key="general",
            parameter="A=1,B=2",
            score=9.75,
            score_provenance=provenance,
            status="queued",
        )

        state = pipeline_candidate_ranking_state(row)

        assert state["source_kind"] == "pipeline_candidate"
        assert state["pipeline_run_id"] == run_id
        assert state["ranking_kind"] == "nagao"
        assert state["ranking_value"] == 9.75
        assert state["score_provenance"] == provenance
    finally:
        db.close()


def test_research_prerequisites_share_rank_arithmetic_pipeline_and_campaign_authorities(tmp_path):
    db = connect(tmp_path / "research-prerequisites.db")
    try:
        ensure_manage_schema(db)
        ensure_pipeline_schema(db)

        curve_id = upsert_curve(
            db,
            family="research-r1",
            parameter="5/7",
            a_invariants_json=json.dumps(MODEL),
        )
        update_curve(
            db,
            curve_id,
            conductor="37",
            discriminant="-37",
            bad_primes_json="[37]",
            root_number=-1,
        )
        record_rank_evidence(
            db,
            curve_id=curve_id,
            model=MODEL,
            data={
                "engine": "research-r1-test",
                "evidence_type": "rank_bounds",
                "status": "completed",
                "rigorous": True,
                "rigorous_lower": 4,
                "rigorous_upper": 6,
                "exact_rank": None,
                "conditional_analytic_upper": None,
                "numerical_rank_signal": None,
                "assumptions": [],
                "points_found": [],
                "options": {},
            },
        )

        campaign_id = create_campaign(
            db,
            name="Research authority campaign",
            objective="Verify shared research prerequisites",
            target_rank=8,
        )
        pipeline_id = save_pipeline(
            db,
            name="Research authority pipeline",
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
            pipeline_name="Research authority pipeline",
            target_mode="curve",
            target={"curve_id": curve_id},
            stages=[{"id": "rank_bounds", "config": {}}],
            run_config={"campaign_id": campaign_id},
        )
        upsert_pipeline_candidate(
            db,
            run_id=run_id,
            provider_key="curve",
            parameter=f"curve:{curve_id}",
            curve_id=curve_id,
            status="completed",
            rigorous_lower=4,
        )

        research = get_curve_research_state(db, curve_id)
        arithmetic = get_curve_arithmetic_state(db, curve_id)
        run = pipeline_run_payload(get_pipeline_run(db, run_id))

        assert research["rigorous_lower"] == 4
        assert research["rigorous_upper"] == 6
        assert research["rank_inconsistent"] is False
        assert arithmetic["conductor"] == 37
        assert arithmetic["root_number"] == -1
        assert run["campaign_id"] == campaign_id
        assert run["manifest"]["source_pipeline_id"] == pipeline_id
        assert run["manifest"]["source_pipeline_revision"] == definition["revision"]
        assert run["manifest"]["source_pipeline_hash"] == definition["content_hash"]
        assert len(run["manifest"]["run_snapshot_hash"]) == 64
        assert campaign_curve_ids_readonly(db, campaign_id) == [curve_id]
    finally:
        db.close()


def test_candidate_consumers_use_shared_ranking_projection():
    pools_source = (ROOT / "rank42" / "ui_pages" / "candidate_pools_page.py").read_text(
        encoding="utf-8"
    )
    runner_source = (ROOT / "rank42" / "pipeline_runner.py").read_text(
        encoding="utf-8"
    )

    assert "pool_candidate_ranking_state(row)" in pools_source
    assert "pool_candidate_ranking_state(row)" in runner_source
    assert 'json.loads(row["metadata_json"] or "{}").get("score_provenance")' not in runner_source
