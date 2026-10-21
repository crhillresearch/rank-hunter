from sage.all import EllipticCurve, QQ

from rank42 import independence_check
from rank42 import pipeline_transforms as transforms
from rank42.db import connect, upsert_curve
from rank42.points import upsert_point


def _parent_curve(tmp_path):
    db = connect(tmp_path / "rank42.db")
    E = EllipticCurve(QQ, [0, 0, 0, -1, 0])
    curve_id = upsert_curve(
        db,
        family="isogeny-transfer-test",
        parameter="parent",
        a_invariants_json='["0","0","0","-1","0"]',
        status="test",
    )
    return db, E, curve_id


def _mock_child_branch(mapping_complete=True, mapped_points=None):
    child = EllipticCurve(QQ, [0, 0, 0, -1, 1])
    return {
        "edge_index": 1,
        "degree": 2,
        "codomain_a_invariants": [str(x) for x in child.a_invariants()],
        "mapped_points": (
            [{"parent_basis_index": 1, "point": ["0", "1"]}]
            if mapped_points is None
            else list(mapped_points)
        ),
        "mapping_complete": bool(mapping_complete),
        "mapping_failures": 0 if mapping_complete else 1,
        "mapping_failure_samples": (
            []
            if mapping_complete
            else [{
                "parent_basis_index": 2,
                "error_class": "FixtureMapError",
                "error": "fixture mapping failure",
            }]
        ),
    }


def test_isogeny_transfer_timeout_preserves_attempt_provenance(
    tmp_path, monkeypatch
):
    db, E, curve_id = _parent_curve(tmp_path)
    monkeypatch.setattr(
        transforms,
        "rigorous_witness_basis",
        lambda db, curve_id, curve: ([object()], 1, True),
    )
    monkeypatch.setattr(
        transforms,
        "run_isogeny_degree_discovery",
        lambda ainvs, degree, points, timeout: {
            "status": "completed",
            "degree": 2,
            "children": [_mock_child_branch()],
            "runtime_seconds": 0.01,
            "worker_exit_code": 0,
        },
    )
    captured = {}

    def certify(db_arg, **kwargs):
        captured.update(kwargs)
        return {
            "status": "timeout",
            "reason": "all_certificate_attempts_timed_out",
            "exact_attempts": 1,
            "attempt_outcomes": [{
                "point_id": 17,
                "status": "timeout",
                "evidence_id": 91,
                "basis_size_before": 0,
                "basis_size_after": 0,
                "error": "fixture timeout",
            }],
            "evidence_ids": [91],
            "promotion_evidence_id": None,
            "rigorous_lower": 0,
            "certificate_service": "rank42.independence_check",
        }

    monkeypatch.setattr(transforms, "certify_stored_independence", certify)

    result = transforms.isogeny_walk_children(
        db,
        context={"curve_id": curve_id, "E": E},
        config={
            "degrees": [2],
            "timeout": 5,
            "max_children": 2,
            "transfer_basis": True,
            "certificate_timeout": 7,
            "exact_candidates": 4,
        },
    )

    assert result["status"] == "partial"
    assert result["degree_outcomes"][0]["status"] == "partial"
    child = result["children"][0]
    meta = child["metadata"]
    assert meta["basis_transfer_status"] == "transfer_completed"
    assert meta["basis_transfer_complete"] is True
    assert meta["mapped_witness_points"] == 1
    assert len(meta["transferred_point_ids"]) == 1
    assert captured["candidate_point_ids"] == meta["transferred_point_ids"]
    assert captured["certificate_timeout"] == 7
    assert captured["max_candidates"] == 4

    cert = meta["transfer_certification"]
    assert cert["status"] == "certification_timeout"
    assert cert["reason"] == "all_certificate_attempts_timed_out"
    assert cert["evidence_ids"] == [91]
    assert cert["attempt_outcomes"][0]["status"] == "timeout"
    assert cert["certificate_service"] == "rank42.independence_check"

    transfer = result["degree_outcomes"][0]["transfer_outcomes"][0]
    assert transfer["transfer_status"] == "transfer_completed"
    assert transfer["certification_status"] == "certification_timeout"
    assert transfer["evidence_ids"] == [91]


def test_isogeny_partial_transfer_is_not_reported_complete(
    tmp_path, monkeypatch
):
    db, E, curve_id = _parent_curve(tmp_path)
    monkeypatch.setattr(
        transforms,
        "rigorous_witness_basis",
        lambda db, curve_id, curve: ([object(), object()], 2, True),
    )
    monkeypatch.setattr(
        transforms,
        "run_isogeny_degree_discovery",
        lambda ainvs, degree, points, timeout: {
            "status": "completed",
            "degree": 2,
            "children": [_mock_child_branch(mapping_complete=False)],
            "runtime_seconds": 0.01,
            "worker_exit_code": 0,
        },
    )
    monkeypatch.setattr(
        transforms,
        "certify_stored_independence",
        lambda *args, **kwargs: {
            "status": "completed",
            "reason": None,
            "exact_attempts": 1,
            "attempt_outcomes": [],
            "evidence_ids": [12],
            "promotion_evidence_id": 13,
            "rigorous_lower": 1,
            "certificate_service": "rank42.independence_check",
        },
    )

    result = transforms.isogeny_walk_children(
        db,
        context={"curve_id": curve_id, "E": E},
        config={
            "degrees": [2],
            "transfer_basis": True,
            "max_children": 2,
        },
    )

    assert result["status"] == "partial"
    meta = result["children"][0]["metadata"]
    assert meta["basis_transfer_status"] == "transfer_partial"
    assert meta["basis_transfer_complete"] is False
    assert meta["expected_witness_points"] == 2
    assert meta["mapped_witness_points"] == 1
    assert meta["worker_mapping_failures"] == 1
    assert meta["transfer_failure_samples"][0]["kind"] == "worker_mapping"
    assert meta["transfer_failure_samples"][0]["parent_basis_index"] == 2
    assert meta["transfer_certification"]["status"] == (
        "certification_completed"
    )


def test_isogeny_incomplete_parent_basis_reports_transfer_failed(
    tmp_path, monkeypatch
):
    db, E, curve_id = _parent_curve(tmp_path)
    monkeypatch.setattr(
        transforms,
        "rigorous_witness_basis",
        lambda db, curve_id, curve: ([], 3, False),
    )
    monkeypatch.setattr(
        transforms,
        "run_isogeny_degree_discovery",
        lambda ainvs, degree, points, timeout: {
            "status": "completed",
            "degree": 2,
            "children": [_mock_child_branch(mapped_points=[])],
            "runtime_seconds": 0.01,
            "worker_exit_code": 0,
        },
    )

    def should_not_certify(*args, **kwargs):
        raise AssertionError("failed transfer must not invoke certification")

    monkeypatch.setattr(
        transforms, "certify_stored_independence", should_not_certify
    )

    result = transforms.isogeny_walk_children(
        db,
        context={"curve_id": curve_id, "E": E},
        config={"degrees": [2], "transfer_basis": True},
    )

    assert result["status"] == "partial"
    meta = result["children"][0]["metadata"]
    assert meta["basis_transfer_status"] == "transfer_failed"
    assert meta["basis_transfer_reason"] == "parent_rigorous_basis_incomplete"
    assert meta["transfer_certification"]["status"] == (
        "certification_not_attempted"
    )


def test_scoped_independence_service_only_selects_requested_point_ids(
    tmp_path, monkeypatch
):
    db = connect(tmp_path / "scope.db")
    E = EllipticCurve(QQ, [0, 0, 0, -1, 1])
    curve_id = upsert_curve(
        db,
        family="scope-test",
        parameter="1",
        a_invariants_json='["0","0","0","-1","1"]',
        status="test",
    )
    first = upsert_point(
        db,
        curve_id=curve_id,
        x=QQ(0),
        y=QQ(1),
        source="scope",
        role="candidate_witness",
        exact_verified=True,
        independence_status="unknown",
        rigorous_independent=False,
        search_ref="scope:first",
        metadata={},
    )
    second = upsert_point(
        db,
        curve_id=curve_id,
        x=QQ(1),
        y=QQ(1),
        source="scope",
        role="candidate_witness",
        exact_verified=True,
        independence_status="unknown",
        rigorous_independent=False,
        search_ref="scope:second",
        metadata={},
    )
    monkeypatch.setattr(
        independence_check,
        "rigorous_witness_basis",
        lambda db, curve_id, curve: ([], 0, True),
    )
    selected = []

    def run_maximal(basis, candidates, certify):
        selected.extend(int(item["record"]["id"]) for item in candidates)
        return list(basis), []

    monkeypatch.setattr(
        independence_check, "run_maximal_independence", run_maximal
    )
    monkeypatch.setattr(
        independence_check,
        "_promote_working_basis",
        lambda *args, **kwargs: {
            "rank_inconsistent": False,
            "evidence_id": None,
        },
    )

    result = independence_check.certify_stored_independence(
        db,
        curve_id=curve_id,
        E=E,
        candidate_point_ids=[int(second["id"])],
        max_candidates=10,
    )

    assert selected == [int(second["id"])]
    assert int(first["id"]) not in selected
    assert result["candidate_point_ids"] == [int(second["id"])]
