from pathlib import Path
import json

import pytest
from sage.all import EllipticCurve, QQ

from rank42.analysis_planner import analysis_plan
from rank42.db import connect, upsert_curve
from rank42.lattice import _point_ledger_points
from rank42.lattice_analysis import point_ledger_candidate_residuals
from rank42.points import upsert_point
from rank42.rank_evidence import record_rank_evidence
from rank42.ui_pages.lattices_page import _target_geometry_hint
from rank42.ui_pages.target_curve import _target_geometry_hint_for_curve


ROOT = Path(__file__).resolve().parents[1]


def test_point_ledger_lattice_source_uses_authoritative_rigorous_basis(tmp_path):
    db = connect(tmp_path / "mw-geometry.db")
    model = ["0", "1", "1", "-2", "0"]
    curve_id = upsert_curve(
        db,
        family="mw-geometry",
        parameter="1",
        a_invariants_json=json.dumps(model),
    )
    E = EllipticCurve(QQ, [QQ(value) for value in model])
    P = E(0, 0)
    Q = E(1, 0)

    record_rank_evidence(
        db,
        curve_id=curve_id,
        model=model,
        data={
            "engine": "fixture",
            "evidence_type": "certified_subgroup",
            "status": "completed",
            "rigorous": True,
            "rigorous_lower": 1,
            "rigorous_upper": None,
            "exact_rank": None,
            "assumptions": [],
            "points_found": [[str(P[0]), str(P[1])]],
            "options": {},
        },
    )
    upsert_point(
        db,
        curve_id=curve_id,
        x=P[0],
        y=P[1],
        source="fixture",
        role="rigorous_witness",
        exact_verified=True,
        independence_status="rigorous_independent",
        rigorous_independent=True,
    )
    candidate = upsert_point(
        db,
        curve_id=curve_id,
        x=Q[0],
        y=Q[1],
        source="fixture",
        role="candidate_extra",
        exact_verified=True,
        independence_status="unknown",
        rigorous_independent=False,
    )
    row = db.execute("SELECT * FROM curves WHERE id=?", (curve_id,)).fetchone()

    points, meta = _point_ledger_points(db, row, E, [int(candidate["id"])])

    assert points == [P, Q]
    assert meta["rigorous_basis_count"] == 1
    assert meta["rigorous_basis_required"] == 1
    assert meta["rigorous_basis_complete"] is True
    assert meta["selected_point_ids"] == [int(candidate["id"])]
    assert meta["input_labels"] == [
        "rigorous_basis:1",
        f"point:{int(candidate['id'])}",
    ]


def test_point_ledger_lattice_source_rejects_nonexact_candidate(tmp_path):
    db = connect(tmp_path / "mw-geometry-nonexact.db")
    model = ["0", "1", "1", "-2", "0"]
    curve_id = upsert_curve(
        db,
        family="mw-geometry",
        parameter="2",
        a_invariants_json=json.dumps(model),
    )
    E = EllipticCurve(QQ, [QQ(value) for value in model])
    P = E(0, 0)
    Q = E(1, 0)
    record_rank_evidence(
        db,
        curve_id=curve_id,
        model=model,
        data={
            "engine": "fixture",
            "evidence_type": "certified_subgroup",
            "status": "completed",
            "rigorous": True,
            "rigorous_lower": 1,
            "rigorous_upper": None,
            "exact_rank": None,
            "assumptions": [],
            "points_found": [[str(P[0]), str(P[1])]],
            "options": {},
        },
    )
    upsert_point(
        db,
        curve_id=curve_id,
        x=P[0],
        y=P[1],
        source="fixture",
        role="rigorous_witness",
        exact_verified=True,
        independence_status="rigorous_independent",
        rigorous_independent=True,
    )
    candidate = upsert_point(
        db,
        curve_id=curve_id,
        x=Q[0],
        y=Q[1],
        source="fixture",
        exact_verified=False,
        independence_status="unknown",
        rigorous_independent=False,
    )
    row = db.execute("SELECT * FROM curves WHERE id=?", (curve_id,)).fetchone()

    with pytest.raises(SystemExit, match="not exact-verified"):
        _point_ledger_points(db, row, E, [int(candidate["id"])])


def test_point_ledger_candidate_residual_uses_pre_lll_gram():
    row = {
        "metadata_json": json.dumps(
            {
                "input_gram": [
                    [2.0, 0.0, 1.0],
                    [0.0, 3.0, 1.0],
                    [1.0, 1.0, 4.0],
                ],
                "point_ledger": {
                    "rigorous_basis_count": 2,
                    "input_labels": [
                        "rigorous_basis:1",
                        "rigorous_basis:2",
                        "point:17",
                    ],
                },
            }
        )
    }

    residuals = point_ledger_candidate_residuals(row)

    assert len(residuals) == 1
    rec = residuals[0]
    assert rec["point_id"] == 17
    assert rec["basis_count"] == 2
    assert rec["height"] == pytest.approx(4.0)
    assert rec["orthogonal_height_residual"] == pytest.approx(19.0 / 6.0)
    assert rec["relative_residual"] == pytest.approx(19.0 / 24.0)


def test_mw_geometry_target_hint_is_numerical_context_only():
    chosen = {
        "curve_id": 17,
        "id": 44,
        "source": "point-ledger",
    }
    analysis = {
        "basis_count": 3,
        "condition": 1250.0,
        "weakest_ratio": 0.0008,
        "min_eigenvalue": 0.25,
        "strongest_pair": {
            "left": 0,
            "right": 2,
            "correlation": -0.91,
            "absolute_correlation": 0.91,
        },
    }

    hint = _target_geometry_hint(chosen, analysis)

    assert hint == {
        "curve_id": 17,
        "lattice_id": 44,
        "source": "point-ledger",
        "basis_count": 3,
        "condition": 1250.0,
        "weakest_ratio": 0.0008,
        "min_eigenvalue": 0.25,
        "strongest_pair": {
            "left": 1,
            "right": 3,
            "correlation": -0.91,
            "absolute_correlation": 0.91,
        },
        "claim": "numerical_scheduling_evidence_only",
    }


def test_target_geometry_hint_is_curve_scoped():
    state = {
        "target_geometry_hint": {
            "curve_id": 17,
            "lattice_id": 44,
            "claim": "numerical_scheduling_evidence_only",
        }
    }

    assert _target_geometry_hint_for_curve(state, 17)["lattice_id"] == 44
    assert _target_geometry_hint_for_curve(state, 18) is None


def test_mw_geometry_page_is_actionable_but_not_a_proof_engine():
    source = (ROOT / "rank42" / "ui_pages" / "lattices_page.py").read_text(encoding="utf-8")

    assert 'title(\n        "MW Geometry"' in source
    assert '"Rigorous witness basis"' in source
    assert '"Rigorous basis + selected candidates"' in source
    assert '"--source",' in source
    assert '"--point-id"' in source
    assert "Open selected in Independence" in source
    assert "Open Target with MW Geometry hint" in source
    assert 'st.session_state["target_geometry_hint"] = hint' in source
    assert 'st.session_state["rh_page"] = "Target"' in source
    assert 'st.session_state["rh_page"] = "Independence"' in source
    assert "point_ledger_candidate_residuals(chosen)" in source
    assert "scheduling evidence only" in source
    assert "run_lattice_build(" not in source
    assert "build_lattice(" not in source
    target_source = (ROOT / "rank42" / "ui_pages" / "target_curve.py").read_text(encoding="utf-8")
    assert "MW Geometry scheduling hint" in target_source
    assert "will not automatically rewrite" not in target_source
    assert "Target controls remain unchanged" in target_source


def test_shell_uses_mw_geometry_canonical_route_with_legacy_alias():
    source = (ROOT / "rank42" / "ui.py").read_text(encoding="utf-8")

    assert '("MW Geometry", "MW Geometry")' in source
    assert '"MW Geometry": lattices_page.page' in source
    assert '"Lattices & Heights": "MW Geometry"' in source


def test_planner_routes_lattice_work_to_mw_geometry():
    snapshot = {
        "curve": {"id": 7},
        "state": {
            "rigorous_lower": 2,
            "rigorous_upper": None,
            "exact_rank": None,
            "inconsistent": False,
        },
        "actionable_points": [],
        "numerical_novel_points": [],
        "witnesses": [object(), object()],
        "witness_gap": 0,
        "lattices": [],
        "evidence": [],
        "evidence_timeouts": 0,
        "quartic_searches": [],
        "pipeline_candidates": [],
        "family_section_lower": None,
        "extra_directions_lower": None,
        "method_history": {"upper_bound": {}},
    }

    plan = analysis_plan(snapshot)

    lattice_actions = [action for action in plan["actions"] if action["kind"] == "lattice"]
    assert lattice_actions
    assert all(action["page"] == "MW Geometry" for action in lattice_actions)
