import io
import json
import zipfile

from rank42.db import connect, upsert_curve
from rank42.landscape_state import (
    build_landscape_cohort,
    save_landscape_analysis,
)
from rank42.manage_store import (
    add_campaign_note,
    create_campaign,
    ensure_manage_schema,
)
from rank42.pipeline_state import (
    create_pipeline_run,
    ensure_pipeline_schema,
    upsert_pipeline_candidate,
)
from rank42.rank_evidence import record_rank_evidence
from rank42.research_bundle import (
    CAMPAIGN_BUNDLE_MANIFEST_VERSION,
    build_campaign_bundle_archive,
    build_campaign_bundle_manifest,
    campaign_bundle_files,
    render_campaign_bundle_readme,
)
from rank42.ui_store import create_job


MODEL = ["0", "0", "0", "-1", "1"]


def _curve(db, *, parameter="5/7"):
    return upsert_curve(
        db,
        family="campaign-bundle-family",
        parameter=parameter,
        a_invariants_json=json.dumps(MODEL),
        family_spec="json:campaign-bundle-family",
        family_sha256="family-sha",
        plugin_id="campaign-bundle-fixture",
        plugin_version="1.0",
        native_family_key="campaign-bundle-family:default",
        native_family_spec="json:campaign-bundle-family",
        native_parameter=parameter,
        chart_id="identity",
        chart_parameter=parameter,
        chart_map_fingerprint="chart-sha",
    )


def _rank(db, curve_id, lower):
    record_rank_evidence(
        db,
        curve_id=curve_id,
        model=MODEL,
        data={
            "engine": "campaign-bundle-test",
            "evidence_type": "rank_bounds",
            "status": "completed",
            "rigorous": True,
            "rigorous_lower": int(lower),
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


def test_campaign_bundle_preserves_campaign_run_job_curve_notebook_and_landscape_links(tmp_path):
    db = connect(tmp_path / "campaign-bundle.db")
    try:
        ensure_manage_schema(db)
        ensure_pipeline_schema(db)
        campaign_id = create_campaign(
            db,
            name="Campaign Bundle Fixture",
            objective="Preserve the research chain.",
            target_rank=9,
        )
        campaign = db.execute(
            "SELECT * FROM research_campaigns WHERE id=?",
            (campaign_id,),
        ).fetchone()

        curve_a = _curve(db, parameter="5/7")
        curve_b = _curve(db, parameter="8/11")
        _rank(db, curve_a, 6)
        _rank(db, curve_b, 7)

        run_id = create_pipeline_run(
            db,
            pipeline_name="Campaign Bundle Run",
            target_mode="curve",
            target={"curve_id": curve_a},
            stages=[{"id": "rank_bounds", "config": {}}],
            run_config={
                "campaign_id": campaign_id,
                "campaign_name": campaign["name"],
                "campaign_created_at": campaign["created_at"],
                "plugin_id": "campaign-bundle-fixture",
            },
        )
        upsert_pipeline_candidate(
            db,
            run_id=run_id,
            provider_key="curve",
            parameter=f"curve:{curve_a}",
            curve_id=curve_a,
            status="completed",
            rigorous_lower=6,
        )
        upsert_pipeline_candidate(
            db,
            run_id=run_id,
            provider_key="curve",
            parameter=f"curve:{curve_b}",
            curve_id=curve_b,
            status="completed",
            rigorous_lower=7,
        )

        job_id = create_job(
            db,
            kind="pipeline_search",
            label="Campaign Bundle Job",
            command=["python", "-m", "rank42.pipeline_runner", "--run-id", str(run_id)],
            cwd="/tmp",
            log_path="/tmp/campaign-bundle.log",
            metadata={
                "campaign_id": campaign_id,
                "campaign_name": campaign["name"],
                "campaign_created_at": campaign["created_at"],
                "pipeline_run_id": run_id,
                "curve_ids": [curve_a, curve_b],
            },
        )

        cohort = build_landscape_cohort(
            db,
            source_kind="campaign",
            source={"campaign_id": campaign_id},
        )
        analysis_id = save_landscape_analysis(
            db,
            name="Campaign rank comparison",
            primary_cohort=cohort,
            dimension_ids=["rank.rigorous_lower"],
        )

        include_note = add_campaign_note(
            db,
            campaign_id,
            "The 8/11 fiber currently leads this campaign.",
            entry_type="observation",
            handoff_state="include",
            links=[
                {"kind": "curve", "id": curve_b},
                {"kind": "pipeline_run", "id": run_id},
                {"kind": "job", "id": job_id},
                {"kind": "landscape_analysis", "id": analysis_id},
            ],
        )
        private_note = add_campaign_note(
            db,
            campaign_id,
            "Scratch thought that should remain private.",
            entry_type="note",
            handoff_state="private",
        )

        manifest = build_campaign_bundle_manifest(db, campaign_id)

        assert manifest["manifest_version"] == CAMPAIGN_BUNDLE_MANIFEST_VERSION
        assert manifest["scope"] == "campaign"
        assert manifest["campaign"]["id"] == campaign_id
        assert manifest["campaign"]["name"] == "Campaign Bundle Fixture"

        assert manifest["links"]["curve_ids"] == [curve_a, curve_b]
        assert manifest["links"]["pipeline_run_ids"] == [run_id]
        assert manifest["links"]["job_ids"] == [job_id]
        assert manifest["links"]["landscape_analysis_ids"] == [analysis_id]
        assert manifest["links"]["notebook_entry_ids"] == [include_note]

        assert [rec["curve"]["id"] for rec in manifest["curves"]] == [curve_a, curve_b]
        assert manifest["curves"][0]["rank_state"]["rigorous_lower"] == 6
        assert manifest["curves"][1]["rank_state"]["rigorous_lower"] == 7

        assert manifest["pipeline_runs"][0]["run"]["id"] == run_id
        assert manifest["jobs"][0]["id"] == job_id
        assert manifest["landscape_analyses"][0]["id"] == analysis_id

        notes = {rec["id"]: rec for rec in manifest["notebook"]}
        assert notes[include_note]["handoff_state"] == "include"
        assert notes[include_note]["links"] == [
            {"kind": "curve", "id": curve_b},
            {"kind": "pipeline_run", "id": run_id},
            {"kind": "job", "id": job_id},
            {"kind": "landscape_analysis", "id": analysis_id},
        ]
        assert private_note not in notes
        assert manifest["availability"]["notebook_private_or_resolved_omitted"]["count"] == 1

        assert manifest["link_graph"]["notebook_to_objects"][str(include_note)] == [
            {"kind": "curve", "id": curve_b},
            {"kind": "pipeline_run", "id": run_id},
            {"kind": "job", "id": job_id},
            {"kind": "landscape_analysis", "id": analysis_id},
        ]
    finally:
        db.close()


def test_campaign_bundle_discovers_landscape_analysis_from_notebook_link_even_when_cohort_is_not_campaign(tmp_path):
    db = connect(tmp_path / "campaign-bundle-landscape-link.db")
    try:
        ensure_manage_schema(db)
        campaign_id = create_campaign(db, name="Linked Landscape")
        curve_id = _curve(db)

        cohort = build_landscape_cohort(
            db,
            source_kind="explicit_curves",
            source={"curve_ids": [curve_id]},
        )
        analysis_id = save_landscape_analysis(
            db,
            name="Explicit curve analysis",
            primary_cohort=cohort,
            dimension_ids=["rank.rigorous_lower"],
        )
        add_campaign_note(
            db,
            campaign_id,
            "Keep this analysis with the campaign.",
            entry_type="reference",
            handoff_state="include",
            links=[{"kind": "landscape_analysis", "id": analysis_id}],
        )

        manifest = build_campaign_bundle_manifest(db, campaign_id)

        assert manifest["links"]["landscape_analysis_ids"] == [analysis_id]
        assert manifest["landscape_analyses"][0]["id"] == analysis_id
    finally:
        db.close()


def test_campaign_bundle_readme_reports_same_curve_identity_and_rank_facts_as_manifest(tmp_path):
    db = connect(tmp_path / "campaign-bundle-readme.db")
    try:
        ensure_manage_schema(db)
        campaign_id = create_campaign(
            db,
            name="README Fidelity",
            objective="README must not invent or strengthen rank.",
        )
        curve_id = _curve(db, parameter="13/17")
        _rank(db, curve_id, 8)

        # Explicit pin keeps this retained curve in the Campaign without adding
        # unrelated Job/Run state.
        from rank42.manage_store import pin_campaign_curve
        pin_campaign_curve(db, campaign_id, curve_id)

        manifest = build_campaign_bundle_manifest(db, campaign_id)
        readme = render_campaign_bundle_readme(manifest)

        curve = manifest["curves"][0]["curve"]
        rank = manifest["curves"][0]["rank_state"]

        assert f'# Campaign Research Bundle — {manifest["campaign"]["name"]}' in readme
        assert f'Curve #{curve["id"]}' in readme
        assert str(curve["a_invariants"]) in readme
        assert f'parameter {curve["parameter"]}' in readme
        assert f'rigorous rank ≥ {rank["rigorous_lower"]}' in readme
        assert "exact rank" not in readme.lower()

        # README renderer is manifest-only: it cannot independently query or
        # recertify science.
        import inspect
        source = inspect.getsource(render_campaign_bundle_readme)
        assert "db" not in source.split("):", 1)[0]
        assert "get_curve_research_state" not in source
        assert "record_rank_evidence" not in source
    finally:
        db.close()


def test_campaign_bundle_generation_is_read_only(tmp_path):
    db = connect(tmp_path / "campaign-bundle-readonly.db")
    try:
        ensure_manage_schema(db)
        campaign_id = create_campaign(db, name="Read-only Campaign Bundle")
        curve_id = _curve(db)
        from rank42.manage_store import pin_campaign_curve
        pin_campaign_curve(db, campaign_id, curve_id)

        traced = []
        db.set_trace_callback(traced.append)
        manifest = build_campaign_bundle_manifest(db, campaign_id)

        writes = [
            stmt for stmt in traced
            if stmt.lstrip().upper().startswith(
                ("CREATE ", "ALTER ", "INSERT ", "UPDATE ", "DELETE ", "REPLACE ")
            )
        ]
        assert writes == []
        assert manifest["policy"]["read_only_snapshot"] is True
        assert manifest["policy"]["scientific_state_mutation"] is False
    finally:
        db.close()


def test_campaign_bundle_package_uses_manifest_derived_files(tmp_path):
    db = connect(tmp_path / "campaign-bundle-package.db")
    try:
        ensure_manage_schema(db)
        campaign_id = create_campaign(db, name="Package Fixture")
        curve_id = _curve(db, parameter="19/23")
        _rank(db, curve_id, 5)
        from rank42.manage_store import pin_campaign_curve
        pin_campaign_curve(db, campaign_id, curve_id)
        add_campaign_note(
            db,
            campaign_id,
            "Include this reproducibility note.",
            entry_type="reference",
            handoff_state="include",
        )

        manifest = build_campaign_bundle_manifest(db, campaign_id)
        files = campaign_bundle_files(manifest)

        assert "README.md" in files
        assert "manifest.json" in files
        assert "campaign.json" in files
        assert "pipeline-runs.json" in files
        assert "jobs.jsonl" in files
        assert "landscape-analyses.json" in files
        assert "notebook.md" in files
        assert f"curves/curve-{curve_id}/curve.json" in files
        assert f"curves/curve-{curve_id}/points.jsonl" in files
        assert f"curves/curve-{curve_id}/rank-evidence.jsonl" in files
        assert json.loads(files["manifest.json"])["campaign"]["id"] == campaign_id

        archive_bytes = build_campaign_bundle_archive(manifest)
        with zipfile.ZipFile(io.BytesIO(archive_bytes), "r") as archive:
            names = set(archive.namelist())
            assert set(files).issubset(names)
            readme = archive.read("README.md").decode("utf-8")
            manifest_from_zip = json.loads(
                archive.read("manifest.json").decode("utf-8")
            )
        assert readme == render_campaign_bundle_readme(manifest)
        assert manifest_from_zip["links"] == manifest["links"]
    finally:
        db.close()
