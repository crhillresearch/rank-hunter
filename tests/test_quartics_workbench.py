import json
from pathlib import Path
from types import SimpleNamespace

from rank42.analysis_planner import analysis_plan, planner_immediate_actions
import rank42.covering_search as covering_search_module
from rank42.covering_search import search_stored_covering
from rank42.db import connect, upsert_curve
from rank42.lattice_store import record_covering_search_attempt, store_covering
from rank42.points import record_point_discovery, upsert_point
from rank42.plugin_hooks import ACCEPTED_FEATURE_HOOKS
from rank42.ui_pages import common, quartics_page
from rank42.quartic_store import create_or_get_search, finish_search
from rank42.quartic_workbench import (
    covering_command,
    covering_local_command,
    covering_history,
    covering_research_record,
    covering_matrix,
    covering_matrix_view,
    covering_next_attack_plan,
    covering_point_provenance,
    covering_point_provenance_summary,
    covering_yield_series,
    covering_yield_recap,
    family_quartic_signature_matrix,
    mapped_points_for_covering,
    mapped_points_for_quartic,
    pointed_quartic_command,
    point_height_profile,
    point_height_summary,
    quartic_arithmetic,
    quartic_curve_summary,
    quartic_history,
    quartic_local_evidence,
    quartic_model_comparison,
    quartic_point_inventory,
    quartic_overview_accounting,
    quartic_search_research_record,
    quartic_coverage_chart_data,
    quartic_coverage_density,
    quartic_model_yield_series,
    quartic_model_yield_recap,
    quartic_next_attack_plan,
    quartic_region_overlap,
    quartic_search_coverage,
    raw_quartic_command,
)


ROOT = Path(__file__).resolve().parents[1]


def _fixture(tmp_path):
    db = connect(tmp_path / "quartics-workbench.db")
    curve_id = upsert_curve(
        db,
        family="quartics",
        parameter="1",
        a_invariants_json='["0","1","1","-2","0"]',
    )
    search = create_or_get_search(
        db,
        curve_id=curve_id,
        family="quartics",
        parameter="1",
        hole_label="pointed:abc123",
        coefficients=["1", "0", "-2", "0", "1"],
        integer_coefficients=[1, 0, -2, 0, 1],
        y_scale=1,
        degree=4,
        height_bound=1000,
        metadata={
            "schema": "rank42.pointed_quartic.v1",
            "anchor": ["0", "0"],
            "anchor_key": "abc123",
            "anchor_source": "basis",
            "anchor_vector": [1],
            "reduction": {"transform": {"matrix": ["1", "0", "0", "1"]}},
        },
    )
    finish_search(db, search["id"], status="timeout", runtime=2.5, error="fixture timeout")
    mapped = upsert_point(
        db,
        curve_id=curve_id,
        x="1",
        y="0",
        source="auto_pointed_quartic",
        role="candidate_extra",
        exact_verified=True,
        independence_status="unknown",
        rigorous_independent=False,
        search_ref=f"quartic:{int(search['id'])}",
        metadata={"quartic_search_id": int(search["id"])},
    )
    covering = store_covering(
        db,
        {
            "schema": "rank42.covering.v1",
            "curve_id": curve_id,
            "quartic": {
                "coefficients": ["1", "0", "0", "0", "1"],
                "height": 2000,
            },
            "map": {"x": "u", "y": "v"},
            "metadata": {"source": "fixture"},
        },
    )
    record_covering_search_attempt(
        db,
        covering_id=covering["id"],
        curve_id=curve_id,
        pipeline_run_id=None,
        pipeline_stage_index=None,
        pipeline_stage_id="analysis_quartics",
        height=2000,
        timeout_seconds=30,
        backend="ratpoints",
        one_point=False,
        outcome="timeout",
        runtime_seconds=30.0,
        error="fixture timeout",
    )
    return db, curve_id, search, mapped, covering


def test_quartics_history_is_read_only_and_surfaces_persisted_geometry(tmp_path):
    db, curve_id, search, mapped, covering = _fixture(tmp_path)
    before = db.total_changes

    searches = quartic_history(db, curve_id)
    coverings = covering_history(db, curve_id)

    assert db.total_changes == before
    assert len(searches) == 1
    assert searches[0]["id"] == int(search["id"])
    assert searches[0]["kind"] == "point-centered"
    assert searches[0]["status"] == "timeout"
    assert searches[0]["mapped_points"] == 1
    assert searches[0]["anchor_source"] == "basis"
    assert searches[0]["reduced"] is True
    assert len(coverings) == 1
    assert coverings[0]["id"] == int(covering["id"])
    assert coverings[0]["outcome"] == "timeout"
    assert coverings[0]["height"] == 2000
    assert [int(row["id"]) for row in mapped_points_for_quartic(db, curve_id, search["id"])] == [
        int(mapped["id"])
    ]


def test_covering_point_provenance_keeps_legacy_origin_ambiguity_explicit(tmp_path):
    db, curve_id, _search, preexisting_point, covering = _fixture(tmp_path)
    covering_id = int(covering["id"])

    # The point existed before this covering, then later received current covering attribution.
    upsert_point(
        db,
        curve_id=curve_id,
        x=preexisting_point["x"],
        y=preexisting_point["y"],
        source="legacy_covering_association",
        role="candidate_extra",
        exact_verified=True,
        search_ref=f"covering:{covering_id}",
        metadata={"covering_id": covering_id},
    )

    current_only = upsert_point(
        db,
        curve_id=curve_id,
        x="2",
        y="0",
        source="legacy_current_only",
        role="candidate_extra",
        exact_verified=True,
        search_ref=f"covering:{covering_id}",
        metadata={"covering_id": covering_id},
    )
    discovered_here = upsert_point(
        db,
        curve_id=curve_id,
        x="3",
        y="0",
        source="covering_future",
        role="candidate_extra",
        exact_verified=True,
        search_ref=f"covering:{covering_id}",
        metadata={"covering_id": covering_id},
    )
    record_point_discovery(
        db,
        curve_id=curve_id,
        point_id=discovered_here["id"],
        source="covering_future",
        outcome="mapped",
        search_ref=f"covering:{covering_id}",
        height=1000,
        stored_x=discovered_here["x"],
        stored_y=discovered_here["y"],
        exact_verified=True,
        metadata={"covering_id": covering_id, "preexisting_point": False},
    )

    rediscovered = upsert_point(
        db,
        curve_id=curve_id,
        x="4",
        y="0",
        source="covering_future",
        role="candidate_extra",
        exact_verified=True,
        search_ref=f"covering:{covering_id}",
        metadata={"covering_id": covering_id},
    )
    record_point_discovery(
        db,
        curve_id=curve_id,
        point_id=rediscovered["id"],
        source="covering_future",
        outcome="mapped",
        search_ref=f"covering:{covering_id}",
        height=1000,
        stored_x=rediscovered["x"],
        stored_y=rediscovered["y"],
        exact_verified=True,
        metadata={"covering_id": covering_id, "preexisting_point": True},
    )

    legacy_event = upsert_point(
        db,
        curve_id=curve_id,
        x="5",
        y="0",
        source="legacy_covering_event",
        role="candidate_extra",
        exact_verified=True,
        search_ref=f"covering:{covering_id}",
        metadata={"covering_id": covering_id},
    )
    record_point_discovery(
        db,
        curve_id=curve_id,
        point_id=legacy_event["id"],
        source="legacy_covering_event",
        outcome="mapped",
        search_ref=f"covering:{covering_id}",
        stored_x=legacy_event["x"],
        stored_y=legacy_event["y"],
        exact_verified=True,
        metadata={"covering_id": covering_id},
    )

    rec = next(
        row for row in covering_history(db, curve_id)
        if int(row["id"]) == covering_id
    )
    provenance = covering_point_provenance(db, curve_id, rec)
    origins = {int(row["point_id"]): row["origin"] for row in provenance}

    assert origins[int(preexisting_point["id"])] == "pre-existing"
    assert origins[int(current_only["id"])] == "current attribution only"
    assert origins[int(discovered_here["id"])] == "discovered here"
    assert origins[int(rediscovered["id"])] == "rediscovered here"
    assert origins[int(legacy_event["id"])] == "unknown legacy origin"

    summary = covering_point_provenance_summary(provenance)
    assert summary["discovered_here_points"] == 1
    assert summary["rediscovered_points"] == 1
    assert summary["preexisting_points"] == 1
    assert summary["current_attribution_only_points"] == 1
    assert summary["unknown_origin_points"] == 1
    assert summary["origin_resolved_points"] == 3
    assert summary["origin_unclear_points"] == 2


def test_covering_search_records_immutable_new_vs_rediscovery_evidence(tmp_path, monkeypatch):
    db, curve_id, _search, _mapped, covering = _fixture(tmp_path)
    covering_id = int(covering["id"])
    row = db.execute("SELECT * FROM coverings WHERE id=?", (covering_id,)).fetchone()

    class FakePoint:
        def is_zero(self):
            return False

        def __getitem__(self, index):
            return ("11", "12")[index]

    monkeypatch.setattr(
        covering_search_module,
        "map_quartic_point",
        lambda _E, _data, _x, _y: FakePoint(),
    )

    def fake_search(*_args, **_kwargs):
        return {
            "points": [SimpleNamespace(x="7", y="8")],
            "runtime": 0.25,
        }

    first = search_stored_covering(
        db,
        row=row,
        E=object(),
        height=5000,
        timeout=30,
        ratpoints=None,
        one_point=False,
        resume_completed=False,
        source="test_covering_provenance",
        run_search=fake_search,
    )
    first_discovery = db.execute(
        """SELECT * FROM point_discoveries
           WHERE search_ref=? ORDER BY id DESC LIMIT 1""",
        (f"covering:{covering_id}",),
    ).fetchone()
    first_metadata = json.loads(first_discovery["metadata_json"])

    assert first_discovery["outcome"] == "mapped"
    assert int(first_discovery["height"]) == 5000
    assert first_metadata["covering_id"] == covering_id
    assert first_metadata["covering_attempt_id"] == int(first["attempt_id"])
    assert first_metadata["preexisting_point"] is False
    assert first_metadata["quartic_x"] == "7"
    assert first_metadata["quartic_y"] == "8"

    second = search_stored_covering(
        db,
        row=row,
        E=object(),
        height=10000,
        timeout=30,
        ratpoints=None,
        one_point=False,
        resume_completed=False,
        source="test_covering_provenance",
        run_search=fake_search,
    )
    second_discovery = db.execute(
        """SELECT * FROM point_discoveries
           WHERE search_ref=? ORDER BY id DESC LIMIT 1""",
        (f"covering:{covering_id}",),
    ).fetchone()
    second_metadata = json.loads(second_discovery["metadata_json"])

    assert second_metadata["covering_attempt_id"] == int(second["attempt_id"])
    assert second_metadata["preexisting_point"] is True


def test_covering_association_includes_immutable_rediscovery_when_current_ref_is_preserved(tmp_path):
    db, curve_id, _search, _mapped, covering = _fixture(tmp_path)
    covering_id = int(covering["id"])
    rigorous = upsert_point(
        db,
        curve_id=curve_id,
        x="9",
        y="0",
        source="manual_basis",
        role="rigorous_witness",
        exact_verified=True,
        independence_status="rigorous_independent",
        rigorous_independent=True,
        search_ref="manual:basis",
        metadata={"source": "manual_basis"},
    )
    record_point_discovery(
        db,
        curve_id=curve_id,
        point_id=rigorous["id"],
        source="covering_rediscovery",
        outcome="mapped",
        search_ref=f"covering:{covering_id}",
        height=2500,
        stored_x=rigorous["x"],
        stored_y=rigorous["y"],
        exact_verified=True,
        metadata={"covering_id": covering_id, "preexisting_point": True},
    )

    ids = {
        int(point["id"])
        for point in mapped_points_for_covering(db, curve_id, covering_id)
    }

    assert int(rigorous["id"]) in ids
    assert db.execute(
        "SELECT search_ref FROM points WHERE id=?",
        (int(rigorous["id"]),),
    ).fetchone()["search_ref"] == "manual:basis"


def _height_point(point_id, x, *, metadata=None, status="unknown", rigorous=False):
    return {
        "id": int(point_id),
        "x": str(x),
        "y": "0",
        "metadata_json": json.dumps(metadata or {}, sort_keys=True),
        "exact_verified": 1,
        "rigorous_independent": 1 if rigorous else 0,
        "independence_status": status,
    }


def test_point_height_profile_uses_exact_projective_x_height_without_calling_it_canonical():
    profile = point_height_profile(_height_point(1, "3/4"))

    assert profile["x_height"] == 4
    assert profile["x_height_text"] == "4"
    assert round(profile["x_height_log10"], 6) == 0.602060
    assert profile["canonical_height"] is None
    assert profile["quality_metric"] == "log10 Hx"

    huge = point_height_profile(_height_point(2, f"{10**100}/3"))
    assert huge["x_height_text"] == str(10**100)
    assert round(huge["x_height_log10"], 6) == 100.0


def test_point_height_summary_reports_distribution_and_prefers_explicit_canonical_metadata():
    points = [
        _height_point(1, "1"),
        _height_point(2, "10", status="numerical_novel"),
        _height_point(
            3,
            "100",
            metadata={"canonical_height": "0.25"},
            status="inconclusive",
        ),
    ]

    summary = point_height_summary(points, top_n=3)

    assert summary["x_height_count"] == 3
    assert summary["canonical_height_count"] == 1
    assert summary["minimum_log10_x_height"] == 0.0
    assert summary["p25_log10_x_height"] == 0.5
    assert summary["median_log10_x_height"] == 1.0
    assert summary["p75_log10_x_height"] == 1.5
    assert summary["maximum_log10_x_height"] == 2.0
    assert int(summary["best_points"][0]["point_id"]) == 3
    assert summary["best_points"][0]["quality_metric"] == "canonical height"
    assert [int(row["point_id"]) for row in summary["best_unresolved_points"]] == [3, 1, 2]


def test_covering_matrix_adds_sortable_point_height_quality(tmp_path):
    db, curve_id, _search, _mapped, covering = _fixture(tmp_path)
    covering_id = int(covering["id"])
    upsert_point(
        db,
        curve_id=curve_id,
        x="10",
        y="0",
        source="height_fixture",
        role="candidate_extra",
        exact_verified=True,
        independence_status="unknown",
        rigorous_independent=False,
        search_ref=f"covering:{covering_id}",
        metadata={"covering_id": covering_id},
    )
    upsert_point(
        db,
        curve_id=curve_id,
        x="100",
        y="0",
        source="height_fixture",
        role="candidate_extra",
        exact_verified=True,
        independence_status="unknown",
        rigorous_independent=False,
        search_ref=f"covering:{covering_id}",
        metadata={"covering_id": covering_id},
    )

    rec = covering_matrix(db, curve_id)[0]

    assert rec["unique_mapped_points"] == 2
    assert rec["best_point_log10_x_height"] == 1.0
    assert rec["median_point_log10_x_height"] == 1.5
    assert rec["point_height_summary"]["p25_log10_x_height"] == 1.25
    assert rec["point_height_summary"]["p75_log10_x_height"] == 1.75


def _covering_view_row(
    covering_id,
    *,
    new=0,
    mapped=0,
    rigorous=0,
    unresolved=0,
    unclear=0,
    searches=0,
    hits=0,
    best=None,
    median=None,
    local_state="not checked / no local record",
    local_recorded=False,
    workbench_status="unsearched",
):
    return {
        "id": int(covering_id),
        "discovered_here_points": int(new),
        "unique_mapped_points": int(mapped),
        "independent_points": int(rigorous),
        "unresolved_points": int(unresolved),
        "origin_unclear_points": int(unclear),
        "search_count": int(searches),
        "total_quartic_hits": int(hits),
        "best_point_log10_x_height": best,
        "median_point_log10_x_height": median,
        "local_state": local_state,
        "local_evidence": {"recorded": bool(local_recorded)},
        "workbench_status": workbench_status,
    }


def test_covering_matrix_view_research_sort_presets_are_stable_and_numeric():
    rows = [
        _covering_view_row(1, new=1, mapped=4, rigorous=0, best=5.0, median=7.0),
        _covering_view_row(2, new=3, mapped=3, rigorous=1, best=2.0, median=3.0),
        _covering_view_row(3, new=0, mapped=8, rigorous=2, best=1.0, median=6.0),
        _covering_view_row(4),
    ]

    assert [row["id"] for row in covering_matrix_view(rows, order_by="Most new points")] == [2, 1, 3, 4]
    assert [row["id"] for row in covering_matrix_view(rows, order_by="Most currently mapped")] == [3, 1, 2, 4]
    assert [row["id"] for row in covering_matrix_view(rows, order_by="Most rigorous witnesses")] == [3, 2, 1, 4]
    assert [row["id"] for row in covering_matrix_view(rows, order_by="Lowest best point height")] == [3, 2, 1, 4]
    assert [row["id"] for row in covering_matrix_view(rows, order_by="Lowest median point height")] == [2, 3, 1, 4]


def test_covering_matrix_view_research_filters_answer_common_questions():
    rows = [
        _covering_view_row(
            1,
            new=1,
            mapped=2,
            unresolved=1,
            searches=1,
            hits=1,
            local_recorded=True,
            workbench_status="mapped point",
        ),
        _covering_view_row(
            2,
            mapped=2,
            unclear=2,
            searches=0,
            workbench_status="mapped point",
        ),
        _covering_view_row(
            3,
            searches=1,
            local_state="obstructed at p=7",
            local_recorded=True,
            workbench_status="local obstruction",
        ),
        _covering_view_row(4),
    ]

    assert [row["id"] for row in covering_matrix_view(rows, show="Productive")] == [1, 2]
    assert [row["id"] for row in covering_matrix_view(rows, show="Has unresolved candidates")] == [1]
    assert [row["id"] for row in covering_matrix_view(rows, show="Local obstruction")] == [3]
    assert [row["id"] for row in covering_matrix_view(rows, show="Local evidence recorded")] == [1, 3]
    assert [row["id"] for row in covering_matrix_view(rows, show="Provenance complete")] == [1]
    assert [row["id"] for row in covering_matrix_view(rows, show="Provenance incomplete")] == [2]
    assert [row["id"] for row in covering_matrix_view(rows, show="Search attempted")] == [1, 3]
    assert [row["id"] for row in covering_matrix_view(rows, show="Never searched")] == [2, 4]



def test_quartics_curve_scope_migrates_stale_state_to_all_but_preserves_deliberate_choices():
    state = {"quartics_curve_scope": "Latest"}
    assert common.normalize_curve_selector_scope_state(state, "quartics") == "All"
    assert state["quartics_curve_scope"] == "All"

    state["quartics_curve_scope"] = "Latest"
    assert common.normalize_curve_selector_scope_state(state, "quartics") == "Latest"

    handoff = {
        "quartics_curve_scope": "All",
        "_quartics_curve_scope_version": 1,
        "quartics_curve_scope_pending": "Campaign",
    }
    assert common.normalize_curve_selector_scope_state(handoff, "quartics") == "Campaign"
    assert handoff["quartics_curve_scope"] == "Campaign"
    assert "quartics_curve_scope_pending" not in handoff

def test_quartic_arithmetic_is_exact_and_fingerprint_is_stable():
    first = quartic_arithmetic(["-1", "0", "0", "0", "1"])
    second = quartic_arithmetic(["-1", "0", "0", "0", "1"])

    assert first["fingerprint"] == second["fingerprint"]
    assert first["fingerprint"].startswith("QF:")
    assert first["degree"] == 4
    assert first["I"] == "-12"
    assert first["J"] == "0"
    assert first["invariant_delta"] == "-6912"
    assert first["polynomial_discriminant"] == "-256"
    assert first["nonsingular_binary_quartic"] is True
    assert first["content"] == 1
    assert first["primitive"] is True
    assert first["coefficient_height"] == 1


def test_quartic_arithmetic_tracks_integral_search_model_content():
    arithmetic = quartic_arithmetic(["1/4", "0", "0", "0", "1/4"], reduced=True)

    assert arithmetic["degree"] == 4
    assert arithmetic["integer_coefficients"] == (4, 0, 0, 0, 4)
    assert arithmetic["y_scale"] == 4
    assert arithmetic["content"] == 4
    assert arithmetic["primitive"] is False
    assert arithmetic["reduced"] is True


def test_quartic_local_evidence_uses_only_explicit_recorded_data():
    absent = quartic_local_evidence({}, status="searched")
    assert absent["overall"] == "not checked / no local record"
    assert absent["recorded"] is False
    assert absent["places"] == ()

    recorded = quartic_local_evidence(
        {
            "local_prime_status": {
                "2": "soluble",
                "7": "obstructed",
                "11": "not_checked",
            },
            "first_obstructing_prime": 7,
        },
        status="ready",
    )
    assert recorded["overall"] == "obstructed at p=7"
    assert recorded["recorded"] is True
    assert recorded["first_obstructing_prime"] == "7"
    states = {row["place"]: row["state"] for row in recorded["places"]}
    assert states == {
        "p=11": "not_checked",
        "p=2": "soluble",
        "p=7": "obstructed",
    }


def test_quartic_local_evidence_reads_persisted_covering_local_planner_records():
    soluble = quartic_local_evidence(
        {
            "covering_local_height_plan": {
                "proof_scope": "all_completions_of_Q",
                "local_analysis": {
                    "status": "everywhere_locally_soluble",
                    "all_places_checked": True,
                    "checks": [
                        {
                            "place": "infinity",
                            "locally_soluble": True,
                            "method": "exact_real_sign_and_sturm",
                        },
                        {
                            "place": "2",
                            "locally_soluble": True,
                            "method": "PARI_hyperell_locally_soluble",
                        },
                    ],
                },
            }
        },
        status="ready",
    )
    assert soluble["overall"] == "everywhere locally soluble"
    assert soluble["recorded"] is True
    assert soluble["attempted"] is True
    assert soluble["all_places_checked"] is True
    assert soluble["proof_scope"] == "all_completions_of_Q"
    assert {row["place"]: row["state"] for row in soluble["places"]} == {
        "∞": "soluble",
        "p=2": "soluble",
    }

    obstructed = quartic_local_evidence(
        {
            "covering_local_height_plan": {
                "proof_scope": "all_completions_of_Q",
                "local_analysis": {
                    "status": "locally_insoluble",
                    "obstruction": 7,
                    "all_places_checked": True,
                    "checks": [
                        {"place": "infinity", "locally_soluble": True},
                        {"place": "7", "locally_soluble": False},
                    ],
                },
            }
        },
        status="locally_obstructed",
    )
    assert obstructed["overall"] == "obstructed at p=7"
    assert obstructed["first_obstructing_prime"] == "7"
    assert obstructed["first_obstructing_place"] == "7"
    assert {row["place"]: row["state"] for row in obstructed["places"]}["p=7"] == "obstructed"

    unsupported = quartic_local_evidence(
        {
            "covering_local_height_plan": {
                "local_analysis": {
                    "status": "unsupported",
                    "reason": "full_local_solver_supports_degree_3_or_4",
                }
            }
        },
        status="ready",
    )
    assert unsupported["overall"] == "local solver unsupported"
    assert unsupported["attempted"] is True
    assert unsupported["recorded"] is False
    assert unsupported["unsupported_reason"] == "full_local_solver_supports_degree_3_or_4"


def test_quartic_local_evidence_respects_explicit_obstruction_status_without_inventing_prime():
    local = quartic_local_evidence({}, status="locally_obstructed")

    assert local["overall"] == "obstruction recorded"
    assert local["recorded"] is True
    assert local["first_obstructing_prime"] is None
    assert local["places"] == ()


def _completed_search(
    db,
    *,
    curve_id,
    height,
    denominator_low,
    denominator_high,
):
    row = create_or_get_search(
        db,
        curve_id=curve_id,
        family="quartics",
        parameter="1",
        hole_label="coverage:test",
        coefficients=["1", "0", "-2", "0", "1"],
        integer_coefficients=[1, 0, -2, 0, 1],
        y_scale=1,
        degree=4,
        height_bound=height,
        denominator_low=denominator_low,
        denominator_high=denominator_high,
        metadata={"schema": "rank42.coverage.test.v1"},
    )
    finish_search(db, row["id"], status="done", runtime=1.0)
    return row


def test_quartic_search_coverage_counts_only_completed_regions(tmp_path):
    db, curve_id, _search, _mapped, _covering = _fixture(tmp_path)
    _completed_search(
        db,
        curve_id=curve_id,
        height=100,
        denominator_low=1,
        denominator_high=50,
    )
    _completed_search(
        db,
        curve_id=curve_id,
        height=200,
        denominator_low=26,
        denominator_high=100,
    )

    coverage = quartic_search_coverage(db, curve_id)
    model = coverage["models"][0]

    assert model["jobs"] == 3
    assert model["completed_regions"] == 2
    assert model["incomplete_attempts"] == 1
    assert model["max_height"] == 200
    assert model["denominator_intervals"] == ((1, 100),)
    assert round(model["overlap_fraction"], 3) == 0.125
    assert model["state"] == "lightly searched · no hits yet"

    covering = coverage["coverings"][0]
    assert covering["attempts"] == 1
    assert covering["completed_regions"] == 0
    assert covering["incomplete_attempts"] == 1
    assert covering["state"] == "coverage incomplete"


def test_quartic_region_overlap_is_same_model_and_completed_only(tmp_path):
    db, curve_id, original, _mapped, _covering = _fixture(tmp_path)
    _completed_search(
        db,
        curve_id=curve_id,
        height=100,
        denominator_low=1,
        denominator_high=50,
    )
    _completed_search(
        db,
        curve_id=curve_id,
        height=200,
        denominator_low=26,
        denominator_high=100,
    )
    searches = quartic_history(db, curve_id)

    target = next(rec for rec in searches if int(rec["id"]) == int(original["id"]))
    covered = quartic_region_overlap(
        searches,
        target,
        height=300,
        denominator_low=40,
        denominator_high=60,
    )
    fresh = quartic_region_overlap(
        searches,
        target,
        height=200,
        denominator_low=101,
        denominator_high=150,
    )

    assert covered["valid"] is True
    assert round(covered["overlap_fraction"], 3) == 0.667
    assert fresh["valid"] is True
    assert fresh["overlap_fraction"] == 0.0


def test_covering_one_point_attempt_is_yield_not_completed_coverage(tmp_path):
    db, curve_id, _search, _mapped, covering = _fixture(tmp_path)
    record_covering_search_attempt(
        db,
        covering_id=covering["id"],
        curve_id=curve_id,
        pipeline_run_id=None,
        pipeline_stage_index=None,
        pipeline_stage_id="analysis_quartics",
        height=5000,
        timeout_seconds=30,
        backend="ratpoints",
        one_point=True,
        outcome="completed",
        ratpoints_hits=1,
        mapped_points=1,
        runtime_seconds=2.0,
    )

    coverage = quartic_search_coverage(db, curve_id)
    rec = coverage["coverings"][0]

    assert rec["attempts"] == 2
    assert rec["early_stop_attempts"] == 1
    assert rec["completed_regions"] == 0
    assert rec["quartic_hits"] == 1


def test_quartic_next_attack_prioritizes_unresolved_mapped_points(tmp_path):
    db, curve_id, _search, _mapped, _covering = _fixture(tmp_path)
    searches = quartic_history(db, curve_id)
    coverage = quartic_search_coverage(db, curve_id, searches=searches)
    rec = searches[0]

    plan = quartic_next_attack_plan(
        searches,
        rec,
        model_coverage=coverage["models"][0],
    )

    assert plan["action"] == "resolve_independence"
    assert plan["launch_recommended"] is False
    assert "mapped elliptic point" in plan["headline"]


def test_quartic_next_attack_prefers_fresh_neighbor_band_after_stale_raw_coverage(tmp_path):
    db, curve_id, _search, _mapped, _covering = _fixture(tmp_path)
    db.execute("DELETE FROM points WHERE curve_id=?", (curve_id,))
    db.commit()
    raw = []
    for low, high in ((1, 20), (21, 40), (41, 60)):
        raw.append(
            _completed_search(
                db,
                curve_id=curve_id,
                height=200,
                denominator_low=low,
                denominator_high=high,
            )
        )
    searches = quartic_history(db, curve_id)
    coverage = quartic_search_coverage(db, curve_id, searches=searches)
    rec = next(item for item in searches if int(item["id"]) == int(raw[-1]["id"]))

    plan = quartic_next_attack_plan(
        searches,
        rec,
        model_coverage=coverage["models"][0],
    )

    assert plan["action"] == "neighbor_band"
    assert plan["launch_recommended"] is True
    assert plan["suggested"]["neighbor_low"] == 61
    assert plan["suggested"]["neighbor_high"] == 80
    assert plan["suggested"]["neighbor_overlap"] == 0.0


def test_quartic_next_attack_stops_on_recorded_local_obstruction(tmp_path):
    db, curve_id, search, _mapped, _covering = _fixture(tmp_path)
    db.execute(
        "UPDATE quartic_searches SET status='locally_obstructed' WHERE id=?",
        (int(search["id"]),),
    )
    db.commit()
    searches = quartic_history(db, curve_id)
    rec = searches[0]

    plan = quartic_next_attack_plan(searches, rec)

    assert plan["action"] == "hold_local_obstruction"
    assert plan["launch_recommended"] is False


def test_covering_next_attack_retries_incomplete_before_widening(tmp_path):
    db, curve_id, _search, _mapped, _covering = _fixture(tmp_path)
    coverings = covering_history(db, curve_id)
    matrix = covering_matrix(db, curve_id, coverings=coverings)
    coverage = quartic_search_coverage(db, curve_id, coverings=coverings)

    plan = covering_next_attack_plan(
        matrix[0],
        coverage=coverage["coverings"][0],
    )

    assert plan["action"] == "retry_incomplete"
    assert plan["launch_recommended"] is True
    assert plan["suggested"]["timeout"] == 60
    assert plan["suggested"]["one_point"] is False


def test_quartic_model_comparison_groups_exact_coefficients_only(tmp_path):
    db, curve_id, search, mapped, covering = _fixture(tmp_path)
    duplicate = create_or_get_search(
        db,
        curve_id=curve_id,
        family="quartics",
        parameter="1",
        hole_label="same-model-different-band",
        coefficients=["1", "0", "-2", "0", "1"],
        integer_coefficients=[1, 0, -2, 0, 1],
        y_scale=1,
        degree=4,
        height_bound=5000,
        denominator_low=10,
        denominator_high=20,
        metadata={"schema": "rank42.coverage.test.v1"},
    )
    finish_search(db, duplicate["id"], status="done", runtime=1.0)
    searches = quartic_history(db, curve_id)
    coverings = covering_history(db, curve_id)
    coverage = quartic_search_coverage(
        db,
        curve_id,
        searches=searches,
        coverings=coverings,
    )
    matrix = covering_matrix(db, curve_id, coverings=coverings)

    comparison = quartic_model_comparison(
        db,
        curve_id,
        searches=searches,
        coverings=coverings,
        coverage=coverage,
        matrix=matrix,
    )

    search_group = next(
        rec
        for rec in comparison
        if int(search["id"]) in rec["search_ids"]
    )
    assert set(search_group["search_ids"]) == {int(search["id"]), int(duplicate["id"])}
    assert search_group["covering_ids"] == ()
    assert search_group["mapped_points"] == 1
    assert search_group["rigorous_points"] == 0

    covering_group = next(
        rec
        for rec in comparison
        if int(covering["id"]) in rec["covering_ids"]
    )
    assert covering_group["search_ids"] == ()
    assert covering_group["covering_ids"] == (int(covering["id"]),)


def test_quartic_search_research_record_preserves_exact_recipe_and_provenance(tmp_path):
    db, curve_id, search, mapped, _covering = _fixture(tmp_path)
    rec = quartic_history(db, curve_id)[0]

    record = quartic_search_research_record(db, curve_id, rec)

    assert record["recipe"]["schema"] == "rank42.quartic_reproduction.v1"
    assert record["recipe"]["object"] == "quartic_search"
    assert record["recipe"]["search"]["id"] == int(search["id"])
    assert record["recipe"]["quartic"]["coefficients"] == ["1", "0", "-2", "0", "1"]
    assert record["recipe"]["results"]["mapped_point_ids"] == [int(mapped["id"])]
    assert record["recipe"]["search"]["timeout_seconds"] is None
    assert record["recipe"]["replay"]["module"] == "rank42.quartic_search"
    argv = record["recipe"]["replay"]["argv_template"]
    assert argv[:3] == ["<science-python>", "-m", "rank42.quartic_search"]
    assert argv[argv.index("--timeout") + 1] == "<timeout>"
    assert "<ratpoints>" in argv
    assert [step["stage"] for step in record["provenance"]] == [
        "exact quartic model",
        "persisted search",
        "exact quartic hits",
        "mapped E(Q)",
        "rigorous witnesses",
    ]


def test_covering_research_record_preserves_exact_map_and_attempt_history(tmp_path):
    db, curve_id, _search, _mapped, covering = _fixture(tmp_path)
    rec = covering_history(db, curve_id)[0]

    record = covering_research_record(db, curve_id, rec)

    assert record["recipe"]["object"] == "exact_covering"
    assert record["recipe"]["covering"]["id"] == int(covering["id"])
    assert record["recipe"]["covering"]["map"] == {"x": "u", "y": "v"}
    assert record["recipe"]["latest_attempt"]["height"] == 2000
    assert record["recipe"]["latest_attempt"]["timeout_seconds"] == 30
    assert record["recipe"]["results"]["attempt_ids"]
    assert record["recipe"]["replay"]["mode"] == "covering"
    argv = record["recipe"]["replay"]["argv_template"]
    assert argv[:3] == ["<science-python>", "-m", "rank42.quartic_workbench_runner"]
    assert argv[argv.index("--height") + 1] == "2000"
    assert argv[argv.index("--timeout") + 1] == "30"
    assert "<ratpoints>" in argv


def test_quartic_coverage_chart_data_separates_completed_and_incomplete(tmp_path):
    db, curve_id, search, _mapped, _covering = _fixture(tmp_path)
    done = _completed_search(
        db,
        curve_id=curve_id,
        height=100,
        denominator_low=5,
        denominator_high=25,
    )
    searches = quartic_history(db, curve_id)

    rows = quartic_coverage_chart_data(db, curve_id, searches=searches)
    by_id = {row["search_id"]: row for row in rows}

    assert by_id[int(search["id"])]["coverage_state"] == "incomplete"
    assert by_id[int(done["id"])]["coverage_state"] == "completed"
    assert by_id[int(done["id"])]["denominator_low"] == 5
    assert by_id[int(done["id"])]["denominator_high"] == 25
    assert by_id[int(done["id"])]["height_high"] == 100


def test_quartic_coverage_density_uses_log_cells_and_separates_incomplete(tmp_path):
    db, curve_id, _search, _mapped, _covering = _fixture(tmp_path)
    _completed_search(
        db,
        curve_id=curve_id,
        height=100,
        denominator_low=1,
        denominator_high=50,
    )
    _completed_search(
        db,
        curve_id=curve_id,
        height=10000,
        denominator_low=100,
        denominator_high=999,
    )
    rows = quartic_coverage_chart_data(
        db,
        curve_id,
        searches=quartic_history(db, curve_id),
    )

    density = quartic_coverage_density(rows)

    assert density["summary"]["completed_searches"] == 2
    assert density["summary"]["incomplete_searches"] == 1
    assert density["summary"]["max_height"] == 10000
    assert density["summary"]["max_denominator"] == 1000
    assert density["summary"]["total_cells"] == 20
    assert density["summary"]["blank_cells"] > 0
    cells = {
        (row["height_label"], row["denominator_label"]): row
        for row in density["cells"]
    }
    assert cells[("1–9", "1–9")]["completed_searches"] == 1
    assert cells[("1000–9999", "100–999")]["completed_searches"] == 1
    assert any(int(row["incomplete_requests"]) > 0 for row in density["cells"])


def test_quartic_coverage_density_filters_exact_model_and_kind(tmp_path):
    db, curve_id, _search, _mapped, _covering = _fixture(tmp_path)
    raw = _completed_search(
        db,
        curve_id=curve_id,
        height=100,
        denominator_low=1,
        denominator_high=25,
    )
    rows = quartic_coverage_chart_data(
        db,
        curve_id,
        searches=quartic_history(db, curve_id),
    )
    raw_row = next(row for row in rows if int(row["search_id"]) == int(raw["id"]))

    density = quartic_coverage_density(
        rows,
        fingerprint=raw_row["fingerprint"],
        kind=raw_row["kind"],
    )

    assert density["summary"]["completed_searches"] == 1
    assert density["summary"]["incomplete_searches"] == 0
    assert density["fingerprints"]
    assert density["kinds"]


def test_quartic_model_yield_series_is_cumulative_by_height(tmp_path):
    db, curve_id, _search, _mapped, _covering = _fixture(tmp_path)
    db.execute("DELETE FROM points WHERE curve_id=?", (curve_id,))
    db.commit()
    _completed_search(
        db,
        curve_id=curve_id,
        height=100,
        denominator_low=1,
        denominator_high=50,
    )
    second = _completed_search(
        db,
        curve_id=curve_id,
        height=200,
        denominator_low=51,
        denominator_high=100,
    )
    searches = quartic_history(db, curve_id)
    rec = next(item for item in searches if int(item["id"]) == int(second["id"]))

    series = quartic_model_yield_series(db, curve_id, rec, searches=searches)

    assert [row["height"] for row in series] == [100, 200]
    assert [row["attempts"] for row in series] == [1, 2]
    assert [row["cumulative_hits"] for row in series] == [0, 0]


def test_covering_yield_series_uses_persisted_attempt_counts(tmp_path):
    db, curve_id, _search, _mapped, covering = _fixture(tmp_path)
    record_covering_search_attempt(
        db,
        covering_id=covering["id"],
        curve_id=curve_id,
        pipeline_run_id=None,
        pipeline_stage_index=None,
        pipeline_stage_id="chart",
        height=5000,
        timeout_seconds=30,
        backend="ratpoints",
        one_point=False,
        outcome="completed",
        ratpoints_hits=3,
        mapped_points=2,
        runtime_seconds=1.5,
    )

    series = covering_yield_series(db, covering["id"])

    assert [row["height"] for row in series] == [2000, 5000]
    assert series[-1]["cumulative_hits"] == 3
    assert series[-1]["cumulative_mapped_reports"] == 2


def test_quartic_model_yield_recap_handles_shallow_and_stale_history():
    single = quartic_model_yield_recap(
        [
            {
                "height": 100,
                "cumulative_hits": 2,
                "cumulative_mapped": 1,
                "cumulative_rigorous": 0,
                "cumulative_runtime_seconds": 3.5,
                "attempts": 1,
            }
        ]
    )
    assert single["trend_ready"] is False
    assert single["state"] == "single completed height — not enough history for a trend"
    assert single["recent_hit_gain"] == 2
    assert single["recent_mapped_gain"] == 1

    stale = quartic_model_yield_recap(
        [
            {
                "height": 100,
                "cumulative_hits": 2,
                "cumulative_mapped": 1,
                "cumulative_rigorous": 0,
                "cumulative_runtime_seconds": 3.5,
                "attempts": 1,
            },
            {
                "height": 1000,
                "cumulative_hits": 2,
                "cumulative_mapped": 1,
                "cumulative_rigorous": 0,
                "cumulative_runtime_seconds": 8.0,
                "attempts": 2,
            },
        ]
    )
    assert stale["trend_ready"] is True
    assert stale["last_yield_height"] == 100
    assert stale["state"] == "no new yield above H=100"
    assert stale["recent_hit_gain"] == 0
    assert stale["recent_mapped_gain"] == 0


def test_covering_yield_recap_tracks_latest_attempt_gain():
    recap = covering_yield_recap(
        [
            {
                "height": 1000,
                "cumulative_hits": 0,
                "cumulative_mapped_reports": 0,
                "cumulative_runtime_seconds": 2.0,
                "attempts_at_or_below_height": 1,
            },
            {
                "height": 10000,
                "cumulative_hits": 3,
                "cumulative_mapped_reports": 2,
                "cumulative_runtime_seconds": 5.0,
                "attempts_at_or_below_height": 2,
            },
        ]
    )
    assert recap["max_height"] == 10000
    assert recap["hits"] == 3
    assert recap["mapped"] == 2
    assert recap["recent_hit_gain"] == 3
    assert recap["recent_mapped_gain"] == 2
    assert recap["state"] == "new yield recorded at current max H=10000"


def test_family_quartic_signature_matrix_uses_authoritative_rank_lower(tmp_path):
    db, curve_id, _search, _mapped, _covering = _fixture(tmp_path)

    signature = family_quartic_signature_matrix(db, "quartics")

    assert signature["family"] == "quartics"
    assert signature["models"] >= 1
    assert signature["curves"] >= 1
    assert signature["rows"]
    rank_labels = {row["rank_label"] for row in signature["rows"]}
    assert "≥0" in rank_labels
    features = {row["feature"] for row in signature["rows"]}
    assert {"I > 0", "I < 0", "J > 0", "J < 0", "Δ > 0", "Δ < 0", "Primitive", "Reduced"} <= features


def test_quartic_curve_summary_keeps_pipeline_stages_and_proof_status_separate(tmp_path):
    db, curve_id, _search, mapped, _covering = _fixture(tmp_path)

    summary = quartic_curve_summary(db, curve_id)

    assert summary["coverings"] == 1
    assert summary["persisted_searches"] == 1
    assert summary["covering_attempts"] == 1
    assert summary["search_jobs"] == 2
    assert summary["quartic_hits"] == 0
    assert summary["mapped_points"] == 1
    assert summary["mapped_point_ids"] == (int(mapped["id"]),)
    assert summary["rigorous_independent_points"] == 0


def test_covering_matrix_is_descriptive_and_does_not_claim_exhaustion(tmp_path):
    db, curve_id, _search, _mapped, covering = _fixture(tmp_path)

    matrix = covering_matrix(db, curve_id)

    assert len(matrix) == 1
    assert matrix[0]["id"] == int(covering["id"])
    assert matrix[0]["search_count"] == 1
    assert matrix[0]["max_height"] == 2000
    assert matrix[0]["total_quartic_hits"] == 0
    assert matrix[0]["unique_mapped_points"] == 0
    assert matrix[0]["independent_points"] == 0
    assert matrix[0]["workbench_status"] == "incomplete"
    assert matrix[0]["local_state"] == "not checked / no local record"


def test_quartic_point_inventory_treats_mapped_points_as_objects_not_rank_claims(tmp_path):
    db, curve_id, _search, mapped, _covering = _fixture(tmp_path)

    inventory = quartic_point_inventory(db, curve_id)

    assert len(inventory) == 1
    assert inventory[0]["id"] == int(mapped["id"])
    assert inventory[0]["exact_verified"] is True
    assert inventory[0]["rigorous_independent"] is False
    assert inventory[0]["significance"] == "exact — independence untested"
    assert inventory[0]["rank_meaning"] == "No rank increase proven"
    assert inventory[0]["provenance"] == (f"search #{int(_search['id'])}",)


def test_quartic_point_inventory_surfaces_existing_rigorous_witness_without_inference(tmp_path):
    db, curve_id, _search, mapped, _covering = _fixture(tmp_path)
    db.execute(
        """UPDATE points
           SET rigorous_independent=1,
               independence_status='rigorous_independent',
               role='rigorous_witness'
           WHERE id=?""",
        (int(mapped["id"]),),
    )
    db.commit()

    inventory = quartic_point_inventory(db, curve_id)

    assert inventory[0]["rigorous_independent"] is True
    assert inventory[0]["significance"] == "★ rigorous independent"
    assert inventory[0]["rank_meaning"] == "Stored rigorous witness"



def test_quartic_overview_accounting_is_proof_aware_and_conservative(tmp_path):
    db, curve_id, search, mapped, covering = _fixture(tmp_path)
    covering_id = int(covering["id"])

    proven = upsert_point(
        db,
        curve_id=curve_id,
        x="7",
        y="0",
        source="covering_accounting",
        role="candidate_extra",
        exact_verified=True,
        independence_status="unknown",
        rigorous_independent=False,
        search_ref=f"covering:{covering_id}",
        metadata={"covering_id": covering_id},
    )
    record_point_discovery(
        db,
        curve_id=curve_id,
        point_id=proven["id"],
        source="covering_accounting",
        outcome="mapped",
        search_ref=f"covering:{covering_id}",
        stored_x=proven["x"],
        stored_y=proven["y"],
        exact_verified=True,
        metadata={"covering_id": covering_id, "preexisting_point": False},
    )
    db.execute(
        """UPDATE points
           SET rigorous_independent=1,
               independence_status='rigorous_independent',
               role='rigorous_witness'
           WHERE id=?""",
        (int(proven["id"]),),
    )

    dependent = upsert_point(
        db,
        curve_id=curve_id,
        x="9",
        y="0",
        source="covering_accounting",
        role="candidate_extra",
        exact_verified=True,
        independence_status="dependent",
        rigorous_independent=False,
        search_ref=f"covering:{covering_id}",
        metadata={"covering_id": covering_id},
    )
    cached = upsert_point(
        db,
        curve_id=curve_id,
        x="11",
        y="0",
        source="auto_pointed_quartic",
        role="rigorous_witness",
        exact_verified=True,
        independence_status="rigorous_independent",
        rigorous_independent=True,
        search_ref=f"quartic:{int(search['id'])}",
        metadata={"quartic_search_id": int(search["id"])},
    )
    record_point_discovery(
        db,
        curve_id=curve_id,
        point_id=cached["id"],
        source="auto_pointed_quartic",
        outcome="cached_mapping",
        search_ref=f"quartic:{int(search['id'])}",
        stored_x=cached["x"],
        stored_y=cached["y"],
        exact_verified=True,
        metadata={
            "quartic_search_id": int(search["id"]),
            "preexisting_point": False,
            "cached_replay": True,
            "historical_origin_unresolved": True,
        },
    )
    db.commit()

    searches = quartic_history(db, curve_id)
    coverings = covering_history(db, curve_id)
    inventory = quartic_point_inventory(
        db,
        curve_id,
        searches=searches,
        coverings=coverings,
    )
    summary = quartic_curve_summary(
        db,
        curve_id,
        searches=searches,
        coverings=coverings,
    )
    accounting = quartic_overview_accounting(
        db,
        curve_id,
        inventory=inventory,
        summary=summary,
        searches=searches,
        coverings=coverings,
    )

    assert accounting["associated_points"] == 4
    assert accounting["rigorous_witnesses"] == 2
    assert accounting["proven_quartics_origin_points"] == 1
    assert accounting["proven_quartics_origin_rigorous_witnesses"] == 1
    assert accounting["proven_origin_point_ids"] == (int(proven["id"]),)
    assert accounting["unresolved_points"] == 1
    assert accounting["dependent_points"] == 1
    assert accounting["independence_tested_points"] == 3
    assert accounting["numerical_novel_points"] == 0
    assert accounting["inconclusive_points"] == 0
    assert accounting["untested_points"] == 1
    assert accounting["latest_independence_updated_at"] is not None
    assert set(accounting["height_threshold_counts"]) == {16.0, 18.0, 20.0}
    assert accounting["funnel"]["mapped_points"] == 4
    assert accounting["funnel"]["independence_tested"] == 3
    assert accounting["funnel"]["rigorous_witnesses"] == 2
    assert accounting["point_ledger_before_first_activity"] == 0
    assert accounting["point_ledger_now"] == 4
    assert accounting["recorded_rank_lower_before_first_activity"] is None
    assert int(mapped["id"]) not in accounting["proven_origin_point_ids"]
    assert int(dependent["id"]) not in accounting["proven_origin_point_ids"]
    assert int(cached["id"]) not in accounting["proven_origin_point_ids"]
    assert "Independence" in accounting["diagnosis"]


def test_quartic_launch_plans_reuse_existing_core_services(tmp_path):
    db, _curve_id, search, _mapped, covering = _fixture(tmp_path)

    raw = raw_quartic_command(
        search,
        python="/sage/python",
        db_path="/tmp/rank42.db",
        ratpoints="/tmp/ratpoints",
        timeout=90,
        height=5000,
        denominator_low=11,
        denominator_high=20,
    )
    assert raw[:3] == ["/sage/python", "-m", "rank42.quartic_search"]
    assert "--source-search-id" in raw
    assert str(int(search["id"])) in raw
    assert raw[raw.index("--denominator-low") + 1] == "11"
    assert raw[raw.index("--denominator-high") + 1] == "20"

    pointed = pointed_quartic_command(
        search,
        python="/sage/python",
        db_path="/tmp/rank42.db",
        ratpoints="/tmp/ratpoints",
        timeout=60,
        height=10000,
        anchors=8,
        alternate_anchors=True,
    )
    assert pointed[:3] == ["/sage/python", "-m", "rank42.quartic_workbench_runner"]
    assert pointed[pointed.index("--mode") + 1] == "pointed"
    assert "--alternate-anchors" in pointed

    covering_cmd = covering_command(
        covering,
        python="/sage/python",
        db_path="/tmp/rank42.db",
        ratpoints="/tmp/ratpoints",
        timeout=120,
        height=100000,
    )
    assert covering_cmd[:3] == ["/sage/python", "-m", "rank42.quartic_workbench_runner"]
    assert covering_cmd[covering_cmd.index("--mode") + 1] == "covering"

    local_cmd = covering_local_command(
        python="/sage/python",
        db_path="/tmp/rank42.db",
        curve_id=7,
        timeout=30,
        max_coverings=44,
        base_height=10000,
    )
    assert local_cmd[:3] == ["/sage/python", "-m", "rank42.quartic_workbench_runner"]
    assert local_cmd[local_cmd.index("--mode") + 1] == "covering-local"
    assert local_cmd[local_cmd.index("--curve-id") + 1] == "7"
    assert local_cmd[local_cmd.index("--max-coverings") + 1] == "44"



def test_pointed_quartic_records_immutable_new_rediscovery_and_map_failure_observations():
    source = (
        ROOT / "rank42" / "pointed_quartic.py"
    ).read_text(encoding="utf-8")

    assert "record_point_discovery" in source
    assert '"map_failure"' in source
    assert '"cached_map_failure"' in source
    assert '"rediscovered"' in source
    assert '"new_point"' in source
    assert '"preexisting_point": bool(preexisting_point)' in source
    assert '"quartic_search_id": int(qsearch["id"])' in source
    assert '"quartic_attempt_id": quartic_attempt_id' in source
    assert "campaign_id=campaign_id" in source
    assert "parameter=parameter" in source
    assert "requested_denominator_low=qsearch[" in source
    assert "requested_denominator_high=qsearch[" in source
    assert "stored_x=P[0]" in source
    assert "stored_y=P[1]" in source


def test_pointed_quartic_attempt_tokens_do_not_turn_cache_replay_into_fresh_discovery():
    source = (
        ROOT / "rank42" / "pointed_quartic.py"
    ).read_text(encoding="utf-8")
    overview = (
        ROOT / "rank42" / "quartic_workbench.py"
    ).read_text(encoding="utf-8")

    assert "import uuid" in source
    assert "quartic_attempt_id = None" in source
    assert "uuid.uuid4().hex" in source
    assert '"quartic_attempt_id": quartic_attempt_id' in source
    assert '"cached_replay": cached_replay' in source
    assert '"historical_origin_unresolved": cached_replay' in source
    assert '"cached_mapping"' in source
    assert "if not (cached_replay and preexisting_point):" in source
    assert 'outcome in {"new_point", "mapped"}' in overview
    assert 'not bool(metadata.get("cached_replay"))' in overview


def test_pipeline_pointed_quartic_threads_run_candidate_and_stage_identity():
    pointed = (
        ROOT / "rank42" / "pointed_quartic.py"
    ).read_text(encoding="utf-8")
    pipeline = (
        ROOT / "rank42" / "pipeline_runner.py"
    ).read_text(encoding="utf-8")

    assert "pipeline_run_id=None" in pointed
    assert "pipeline_candidate_id=None" in pointed
    assert "pipeline_stage_index=None" in pointed
    assert "pipeline_stage_id=None" in pointed
    assert "pipeline_run_id=pipeline_run_id" in pointed
    assert "pipeline_candidate_id=pipeline_candidate_id" in pointed
    assert "pipeline_stage_index=pipeline_stage_index" in pointed
    assert "pipeline_stage_id=pipeline_stage_id" in pointed
    assert 'pipeline_run_id=int(run["id"])' in pipeline
    assert 'pipeline_candidate_id=context.get("pipeline_candidate_id")' in pipeline
    assert "pipeline_stage_index=int(stage_index)" in pipeline
    assert "pipeline_stage_id=str(stage_id)" in pipeline
    assert "parameter=parameter" in pipeline


def test_pipeline_and_quartics_runner_share_covering_service():
    pipeline = (ROOT / "rank42" / "research_modules.py").read_text(encoding="utf-8")
    runner = (ROOT / "rank42" / "quartic_workbench_runner.py").read_text(encoding="utf-8")

    assert "search_stored_covering(" in pipeline
    assert "search_stored_covering(" in runner
    assert "run_pointed_quartic_escalation(" in runner


def test_quartics_page_feature_hook_is_registered():
    source = (ROOT / "rank42" / "ui_pages" / "quartics_page.py").read_text(encoding="utf-8")
    hook_id = "analysis.quartics.after_header"

    assert hook_id in source
    assert hook_id in ACCEPTED_FEATURE_HOOKS


def test_quartics_page_is_workbench_first_and_not_a_second_math_engine():
    source = (ROOT / "rank42" / "ui_pages" / "quartics_page.py").read_text(encoding="utf-8")
    page_source = source[source.index("def page(db, ctx):"):]

    assert 'title("Quartics")' in source
    assert "_render_curve_browser(db, rows, wanted)" in source
    assert "quartic_overview_accounting(" in source
    assert "Coverings → rank funnel" in source
    assert "Proven Quartics-origin" in source
    assert "Before vs now" in source
    assert "Top unresolved candidates" in source
    assert '"Coverings",' in source
    assert '"Soluble",' in source
    assert '"Quartic Hits",' in source
    assert '"Elliptic Points",' in source
    assert '"Independent",' in source
    assert 'st.markdown("#### Search efficacy")' in source
    assert '"Search jobs"' in source
    assert '"Searches with hits"' in source
    assert '"Hit → Elliptic"' in source
    assert '"Elliptic → Independent"' in source
    assert '"Timeout / error"' in source
    assert "Main loss: independence stage" in source
    assert "Numerically novel" in source
    assert "Send top" in source
    assert "Send all" in source
    assert 'active_curve_selector(' in source
    assert 'state_prefix="quartics"' in source
    assert 'widget_prefix="quartics-curve"' in source
    assert 'compatibility_keys=("quartics_curve_id", "analysis_curve_id")' in source
    assert 'st.markdown("**Curve**")' not in source
    assert "streamlit.components.v1" not in source
    assert "declare_component(" not in source
    assert 'variant="page"' in source
    assert 'variant="line"' in source
    assert 'label="Quartics workspace"' not in source
    assert page_source.index("_render_curve_browser(db, rows, wanted)") < page_source.index("workspace = tabs(")
    assert "height:.45rem" in page_source
    assert "quartic_history(db, curve_id)" in source
    assert "covering_history(db, curve_id)" in source
    assert "quartic_curve_summary(" in source
    assert "covering_matrix(" in source
    assert "Quartic intelligence" not in source
    assert "Rank impact & point discoveries" in source
    assert 'with region("quartics-intelligence-board", border=True)' not in source
    assert 'with region("quartics-rank-impact", border=True)' not in source
    assert "Visual recap" not in source
    assert '["Overview", "Coverings", "Search", "Research", "Audit"]' in source
    assert 'if workspace == "Overview":' in source
    assert 'elif workspace == "Coverings":' in source
    assert 'elif workspace == "Search":' in source
    assert 'elif workspace == "Research":' in source
    assert '["Summary", "Arithmetic", "Provenance", "Next Attack"]' in source
    assert "Curve-level quartic workbench: interpret results first" not in source
    assert "only the selected workspace is rendered" not in source
    assert "A quartic hit ≠ a mapped elliptic point" not in source
    assert "This panel reports persisted proof state and provenance" not in source
    assert "Quartic fingerprint" in source
    assert "Exact covering fingerprint" in source
    assert "Local information" in source
    assert "everywhere locally soluble" in source
    assert "Run local checks" in source
    assert "covering_local_command(" in source
    assert "not checked / no local record" in source
    assert "A zero-hit search is not treated as a local obstruction." in source
    assert "Search coverage & empirical yield" in source
    assert "Search coverage density" in source
    assert "Blank log cells" in source
    assert "Exact model" in source
    assert "Model type" in source
    assert "quartic_coverage_density(" in source
    assert "Yield profile" in source
    assert "Useful elliptic points" in source
    assert "Covering yield" in source
    assert "Only one completed height level is available" in source
    assert "Only one configured height is present" in source
    assert "point rather than implying a trend" in source
    assert "Mapped reports" in source
    assert "Family quartic signatures" in source
    assert '["Coverage Map", "Yield & Search", "Coverage Table"]' in source
    assert '["Family signatures", "Exact-model comparison"]' in source
    assert "st.vega_lite_chart(" in source
    assert "Quartic-model coverage" in source
    assert "Exact-covering search depth" in source
    assert "Prior overlap" in source
    assert "never implies exhaustion" in source
    assert "Evidence-backed compute guidance" in source
    assert "Apply suggested setup" in source
    assert "Apply suggested covering setup" in source
    assert "not a probability" in source
    assert "Exact-model research comparison" in source
    assert "exact-model identity, not" in source
    assert "Provenance & reproduction" in source
    assert "Covering provenance & reproduction" in source
    assert "Copyable reproduction recipe" in source
    assert "machine-specific" in source
    assert "Show exact coordinates" in source
    assert "Selected covering C" in source
    assert "Associated points" in source
    assert "Covering matrix" in source
    assert '"Show"' in source
    assert '"Order by"' in source
    assert "Most new points" in source
    assert "Most currently mapped" in source
    assert "Most rigorous witnesses" in source
    assert "Lowest best point height" in source
    assert "Lowest median point height" in source
    assert "Has unresolved candidates" in source
    assert "Provenance incomplete" in source
    assert "Never searched" in source
    assert "covering_matrix_view(" in source
    assert "Search max H" in source
    assert "Best log₁₀ Hx" in source
    assert "Median log₁₀ Hx" in source
    assert "Point height quality" in source
    assert "Hx=max(|numerator(x)|, denominator(x))" in source
    assert "This is not canonical/Néron–Tate height." in source
    assert "Lowest-height unresolved candidate" in source
    assert "New here" in source
    assert "Rediscovered" in source
    assert "Pre-existing" in source
    assert "Origin unclear" in source
    assert "Point origin accounting" in source
    assert "no persisted covering-search attempt supports" in source
    assert "Current Point Ledger attribution alone is not treated as proof" in source
    assert "selectable_dataframe(" in source
    assert "localized_integer_columns=" in source
    assert "_fmt_int(" in source
    assert "selectable_dataframe_rows(" not in source
    assert "checkboxes=True" not in source
    assert "Inspect selected covering" not in source
    assert "Whole-row clicks and checkboxes" not in source
    assert "Raw data & audit trail" in source
    assert "Open unresolved mapped points in Independence" in source
    assert "Open unresolved covering points in Independence" in source
    assert "Retry incomplete geometry" in source
    assert "Widen height" in source
    assert "Search neighboring band" in source
    assert "Try alternate anchors" in source
    assert "Retry / widen this covering" in source
    assert "run_ratpoints(" not in source
    assert "map_pointed_quartic_point(" not in source
    assert "map_quartic_point(" not in source


def test_shell_exposes_quartics_as_analysis_specialist():
    source = (ROOT / "rank42" / "ui.py").read_text(encoding="utf-8")

    assert '("Quartics", "Quartics")' in source
    assert '"Quartics": quartics_page.page' in source
    assert '"Quartic Searches": "Quartics"' in source
    assert '"Quartic Search": "Quartics"' in source


def test_planner_revisits_persisted_quartics_before_fresh_target_search():
    snapshot = {
        "curve": {"id": 7},
        "state": {
            "rigorous_lower": 4,
            "rigorous_upper": None,
            "exact_rank": None,
            "inconsistent": False,
        },
        "actionable_points": [],
        "numerical_novel_points": [],
        "witnesses": [object() for _ in range(4)],
        "witness_gap": 0,
        "lattices": [object()],
        "evidence": [],
        "evidence_timeouts": 0,
        "quartic_searches": [object(), object()],
        "pipeline_candidates": [],
        "family_section_lower": None,
        "extra_directions_lower": None,
        "method_history": {"upper_bound": {}},
    }

    immediate = planner_immediate_actions(analysis_plan(snapshot))

    quartics = next(action for action in immediate if action["kind"] == "quartics")
    target = next(action for action in immediate if action["kind"] == "search")
    assert quartics["page"] == "Quartics"
    assert quartics["priority"] > target["priority"]


def test_quartics_uses_arrows_to_dot_in_title_and_shell_navigation():
    common = (ROOT / "rank42" / "ui_pages" / "common.py").read_text(encoding="utf-8")
    page = (ROOT / "rank42" / "ui_pages" / "quartics_page.py").read_text(encoding="utf-8")
    shell = (ROOT / "rank42" / "ui.py").read_text(encoding="utf-8")

    assert '"arrows-to-dot": {' in common
    assert "def fa_css_mask_uri" in common
    assert "def _breadcrumb_markup" not in common
    assert "rh-breadcrumbs" not in common
    assert 'title("Quartics")' in page
    assert 'QUARTICS_ICON = "fa:arrows-to-dot"' in common
    assert '"Quartics": QUARTICS_ICON' in common
    assert '"Quartics": QUARTICS_ICON' in shell
    assert "h1::before" in common
    assert 'resolved_icon.startswith("fa:")' in shell
    assert "button::before" in shell


def test_single_select_native_fallback_is_radio_free_and_numeric_formatting_stays_numeric():
    source = (ROOT / "rank42" / "ui_components.py").read_text(encoding="utf-8")

    assert 'selection_mode"] = "single-cell"' in source
    assert "single-row-required" not in source
    assert 'NumberColumn(format="localized")' in source
    assert "localized_integer_columns" in source
    assert "toLocaleString()" in source


def test_covering_search_source_records_immutable_point_discoveries():
    source = (ROOT / "rank42" / "covering_search.py").read_text(encoding="utf-8")

    assert "record_point_discovery(" in source
    assert '"preexisting_point"' in source
    assert '"covering_attempt_id"' in source
    assert '"covering_id"' in source


def test_quartics_runner_local_mode_reuses_covering_local_planner():
    source = (ROOT / "rank42" / "quartic_workbench_runner.py").read_text(encoding="utf-8")

    assert '"covering-local"' in source
    assert "plan_covering_searches(" in source
    assert '"--curve-id"' in source
    assert '"--max-coverings"' in source
