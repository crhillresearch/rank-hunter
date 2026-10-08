import subprocess
from types import SimpleNamespace

from sage.all import EllipticCurve, QQ

from rank42 import isogeny_discovery
from rank42 import pipeline_transforms as transforms
from rank42.db import connect, get_curve, upsert_curve


def _parent_curve(tmp_path):
    db = connect(tmp_path / "rank42.db")
    E = EllipticCurve(QQ, [0, 0, 0, -1, 0])
    curve_id = upsert_curve(
        db,
        family="test",
        parameter="parent",
        a_invariants_json='["0","0","0","-1","0"]',
        status="test",
    )
    return db, E, curve_id


def test_isogeny_discovery_timeout_kills_worker(monkeypatch):
    killed = []

    class Proc:
        pid = 12345
        returncode = None

        def communicate(self, payload, timeout):
            raise subprocess.TimeoutExpired("isogeny", timeout)

    monkeypatch.setattr(
        isogeny_discovery.subprocess,
        "Popen",
        lambda *args, **kwargs: Proc(),
    )
    monkeypatch.setattr(
        isogeny_discovery,
        "_terminate_process_tree",
        lambda proc: killed.append(proc.pid),
    )

    E = EllipticCurve(QQ, [0, 0, 0, -1, 0])
    result = isogeny_discovery.run_isogeny_degree_discovery(
        E.a_invariants(), 2, [], timeout=1
    )
    assert result["status"] == "timeout"
    assert result["degree"] == 2
    assert result["timeout_seconds"] == 1
    assert killed == [12345]


def test_isogeny_discovery_worker_returns_exact_codomain_models():
    E = EllipticCurve(QQ, [0, 0, 0, -1, 0])
    result = isogeny_discovery.run_isogeny_degree_discovery(
        E.a_invariants(), 2, [], timeout=10
    )
    assert result["status"] == "completed"
    assert result["degree"] == 2
    assert result["child_count"] >= 1
    for child in result["children"]:
        assert child["degree"] == 2
        assert len(child["codomain_a_invariants"]) == 5
        EllipticCurve(
            QQ, [QQ(str(x)) for x in child["codomain_a_invariants"]]
        )


def test_isogeny_walk_all_degree_timeouts_are_not_completed(monkeypatch):
    E = EllipticCurve(QQ, [0, 0, 0, -1, 0])
    monkeypatch.setattr(
        transforms,
        "rigorous_witness_basis",
        lambda db, curve_id, curve: ([], 0, True),
    )
    monkeypatch.setattr(
        transforms,
        "run_isogeny_degree_discovery",
        lambda ainvs, degree, points, timeout: {
            "status": "timeout",
            "degree": degree,
            "children": [],
            "runtime_seconds": 1.0,
        },
    )
    result = transforms.isogeny_walk_children(
        object(),
        context={"curve_id": 1, "E": E},
        config={"degrees": [2, 3], "timeout": 1, "transfer_basis": False},
    )
    assert result["status"] == "timeout"
    assert result["hard_isolated"] is True
    assert result["degree_coverage"]["timeout"] == 2
    assert result["degree_coverage"]["completed"] == 0
    assert [x["status"] for x in result["degree_outcomes"]] == [
        "timeout", "timeout"
    ]


def test_isogeny_walk_mixed_completed_timeout_is_partial(monkeypatch):
    E = EllipticCurve(QQ, [0, 0, 0, -1, 0])
    monkeypatch.setattr(
        transforms,
        "rigorous_witness_basis",
        lambda db, curve_id, curve: ([], 0, True),
    )

    def discover(ainvs, degree, points, timeout):
        if degree == 2:
            return {
                "status": "completed",
                "degree": degree,
                "children": [],
                "runtime_seconds": 0.01,
                "worker_exit_code": 0,
            }
        return {
            "status": "timeout",
            "degree": degree,
            "children": [],
            "runtime_seconds": 1.0,
        }

    monkeypatch.setattr(transforms, "run_isogeny_degree_discovery", discover)
    result = transforms.isogeny_walk_children(
        object(),
        context={"curve_id": 1, "E": E},
        config={"degrees": [2, 3], "timeout": 1, "transfer_basis": False},
    )
    assert result["status"] == "partial"
    assert result["degree_coverage"]["completed"] == 1
    assert result["degree_coverage"]["timeout"] == 1


def test_isogeny_walk_materializes_isolated_exact_child(tmp_path, monkeypatch):
    db, E, curve_id = _parent_curve(tmp_path)
    monkeypatch.setattr(
        transforms,
        "rigorous_witness_basis",
        lambda db, curve_id, curve: ([], 0, True),
    )
    result = transforms.isogeny_walk_children(
        db,
        context={"curve_id": curve_id, "E": E},
        config={
            "degrees": [2],
            "timeout": 10,
            "max_children": 2,
            "transfer_basis": False,
        },
    )
    assert result["status"] == "completed"
    assert result["hard_isolated"] is True
    assert result["degree_coverage"]["completed"] == 1
    assert result["children"]
    child = result["children"][0]
    assert child["metadata"]["exact_transform"] is True
    assert child["metadata"]["isogeny_degree"] == 2
    assert child["metadata"]["isogeny_discovery_hard_isolated"] is True
    assert child["metadata"]["basis_transfer_requested"] is False
    assert child["metadata"]["basis_transfer_status"] == "transfer_not_requested"
    assert child["metadata"]["basis_transfer_complete"] is False
    assert child["metadata"]["transfer_certification"]["status"] == (
        "certification_not_requested"
    )
    stored = get_curve(db, child["curve_id"])
    assert stored is not None


def test_plugin_transform_hook_timeout_is_enforced(tmp_path):
    adapter = tmp_path / "slow_transform.py"
    adapter.write_text(
        "import time\n"
        "def derive_pipeline_transform(payload):\n"
        "    time.sleep(10)\n"
        "    return {'status': 'completed', 'children': []}\n",
        encoding="utf-8",
    )
    plugin = SimpleNamespace(id="slow-transform", adapter_path=adapter)
    variant = SimpleNamespace(id="v1", family_spec="json:slow")
    result = transforms.plugin_base_change_children(
        object(),
        context={
            "plugin": plugin,
            "variant": variant,
            "parameter": "1",
            "curve_id": 1,
            "E": SimpleNamespace(a_invariants=lambda: [0, 0, 0, 1, 1]),
            "score": 0.0,
        },
        config={"timeout": 1},
    )
    assert result["status"] == "timeout"
    assert result["children"] == []
    assert result["hard_isolated"] is True
    assert result["timeout_seconds"] == 1
    assert result["reason"] == "derive_pipeline_transform exceeded core timeout"


def test_isogeny_worker_mapped_witnesses_lie_exactly_on_child_models():
    E = EllipticCurve(QQ, [0, 0, 0, -1, 0])
    P = E(QQ(1), QQ(0))
    result = isogeny_discovery.run_isogeny_degree_discovery(
        E.a_invariants(), 2, [P], timeout=10
    )
    assert result["status"] == "completed"
    mapped_total = 0
    for branch in result["children"]:
        child = EllipticCurve(
            QQ, [QQ(str(x)) for x in branch["codomain_a_invariants"]]
        )
        for mapped in branch["mapped_points"]:
            x, y = mapped["point"]
            Q = child(QQ(str(x)), QQ(str(y)))
            assert not Q.is_zero()
            mapped_total += 1
    assert mapped_total >= 1
