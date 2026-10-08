import inspect
import json

from rank42.candidates import create_pool, replace_pool_rows
from rank42.db import connect, upsert_curve
from rank42.landscape_state import build_landscape_cohort
from rank42.rank_evidence import record_rank_evidence
from rank42.ui_pages import landscape_page


MODEL = ["0", "0", "0", "-1", "1"]


def _curve(db, parameter="5/7"):
    return upsert_curve(
        db,
        family="landscape-ui-family",
        parameter=parameter,
        a_invariants_json=json.dumps(MODEL),
        family_spec="json:landscape-ui-family",
        family_sha256="family-sha",
        native_family_key="landscape-ui-family:default",
        native_family_spec="json:landscape-ui-family",
        native_parameter=parameter,
        chart_id="identity",
        chart_parameter=parameter,
        chart_map_fingerprint="chart-sha",
        conductor="37",
        discriminant="-37",
        bad_primes_json="[3,37,41]",
        root_number=-1,
    )


def test_landscape_page_uses_r4_r5_contracts_not_dashboard_legacy_renderers():
    source = inspect.getsource(landscape_page)

    assert "build_landscape_cohort" in source
    assert "list_landscape_analyses" in source
    assert "save_landscape_analysis" in source
    assert "resolve_curve_landscape_dimensions" in source
    assert "landscape_dimensions" in source

    assert "dashboard_rank_summary" not in source
    assert "render_landscape_charts" not in source
    assert "render_landscape_comparisons" not in source
    assert "_parameter_pool_rows" not in source


def test_landscape_analysis_rows_combine_frozen_candidate_and_current_curve_authorities(tmp_path):
    db = connect(tmp_path / "landscape-ui.db")
    try:
        curve_id = _curve(db)
        record_rank_evidence(
            db,
            curve_id=curve_id,
            model=MODEL,
            data={
                "engine": "landscape-ui-test",
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
        pool = create_pool(
            db,
            name="landscape-ui-pool",
            plugin_id="fixture",
            family_spec="json:landscape-ui-family",
            family_sha256="family-sha",
        )
        replace_pool_rows(
            db,
            int(pool["id"]),
            [{
                "parameter": "5/7",
                "score": 12.5,
                "ranking_kind": "nagao",
                "ranking_value": 12.5,
                "score_provenance": {
                    "ranking_kind": "nagao",
                    "algorithm": "frozen-nagao",
                },
                "native_family_key": "landscape-ui-family:default",
                "native_family_spec": "json:landscape-ui-family",
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

        cohort = build_landscape_cohort(
            db,
            source_kind="candidate_pool",
            source={"pool_id": int(pool["id"])},
        )
        rows = landscape_page._analysis_rows(
            db,
            cohort,
            [
                "rank.rigorous_lower",
                "candidate.ranking_kind",
                "candidate.ranking_value",
                "parameter.native",
            ],
            cohort_label="Primary",
        )

        assert len(rows) == 1
        row = rows[0]
        assert row["Cohort"] == "Primary"
        assert row["Curve"] == f"#{curve_id}"
        assert row["Rigorous lower"] == 6
        assert row["Candidate ranking kind"] == "nagao"
        assert row["Candidate ranking value"] == 12.5
        assert row["Native parameter"] == "5/7"
        assert row["_provenance"]["candidate.ranking_value"]["score_provenance"]["algorithm"] == "frozen-nagao"
    finally:
        db.close()


def test_landscape_saved_analysis_view_renders_frozen_cohort_contract():
    source = inspect.getsource(landscape_page._saved_analyses)

    assert "get_landscape_analysis" in source
    assert 'saved["primary_cohort"]' in source
    assert 'saved["comparison_cohort"]' in source
    assert 'saved["dimensions"]' in source
    assert "dimension_registry_hash" in source
    assert "exploratory" in source.lower()


def test_landscape_page_exposes_generic_feature_controls_only():
    source = inspect.getsource(landscape_page._feature_controls)

    assert "bad_prime_contains" in source
    assert "bad_prime_superset" in source
    assert "root_number_equals" in source
    assert "denominator_divisible_by" in source
    assert "29,37,41,73" not in source.replace(" ", "")
    assert "3,5,7,13" not in source.replace(" ", "")
