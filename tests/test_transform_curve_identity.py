import json
from types import SimpleNamespace

from sage.all import EllipticCurve, QQ

from rank42 import pipeline_runner as runner
from rank42 import pipeline_transforms as transforms
from rank42.curve_identity import (
    CANONICAL_FAMILY,
    get_or_create_canonical_curve,
)
from rank42.db import connect, get_curve, upsert_curve
from rank42.pipeline_state import (
    create_pipeline_run,
    get_pipeline_candidate,
    pipeline_derivations,
    record_pipeline_derivation,
    upsert_pipeline_candidate,
)


def _curve():
    return EllipticCurve(QQ, [0, 0, 0, -1, 0])


def test_existing_family_curve_is_reused_after_exact_q_isomorphism(tmp_path):
    db = connect(tmp_path / "existing.db")
    E = _curve()
    family_id = upsert_curve(
        db,
        family="demo-family",
        parameter="5/7",
        a_invariants_json=json.dumps(
            [str(x) for x in E.a_invariants()]
        ),
        status="family_specialization",
    )

    scaled = EllipticCurve(
        QQ, [0, 0, 0, QQ(-1) / QQ(16), 0]
    )
    assert scaled.is_isomorphic(E)
    result = get_or_create_canonical_curve(db, scaled)

    assert result["curve_id"] == family_id
    assert result["created"] is False
    assert result["family"] == "demo-family"
    assert result["parameter"] == "5/7"
    identity = result["identity"]
    assert identity["exact_q_isomorphism_verified"] is True
    assert identity["matched_by"] == "existing_curve_exact_q_isomorphism"
    assert identity["method"] == (
        "global-minimal-invariants+sage-is_isomorphic-v1"
    )
    assert db.execute(
        "SELECT COUNT(*) AS n FROM curves WHERE family=?",
        (CANONICAL_FAMILY,),
    ).fetchone()["n"] == 0


def test_same_j_nonisomorphic_twists_are_not_collapsed(tmp_path):
    db = connect(tmp_path / "same-j.db")
    E = _curve()
    T = E.quadratic_twist(2)
    assert E.j_invariant() == T.j_invariant()
    assert not E.is_isomorphic(T)

    first = get_or_create_canonical_curve(db, E)
    second = get_or_create_canonical_curve(db, T)

    assert first["curve_id"] != second["curve_id"]
    assert first["identity"]["signature_sha256"] != (
        second["identity"]["signature_sha256"]
    )
    assert db.execute(
        "SELECT COUNT(*) AS n FROM curves WHERE family=?",
        (CANONICAL_FAMILY,),
    ).fetchone()["n"] == 2


def test_two_transform_paths_share_curve_id_but_keep_path_identity(tmp_path):
    db = connect(tmp_path / "paths.db")
    E = _curve()

    first = transforms._store_direct_child(
        db,
        parent_curve_id=101,
        kind="quadratic_twist",
        token="d=2",
        E=E,
        score=None,
    )
    second = transforms._store_direct_child(
        db,
        parent_curve_id=202,
        kind="isogeny_walk",
        token="degree=2:edge=1",
        E=E,
        score=None,
    )

    assert first["curve_id"] == second["curve_id"]
    assert first["parameter"] != second["parameter"]
    assert first["created_by_pipeline"] is True
    assert second["created_by_pipeline"] is False
    for child in (first, second):
        meta = child["metadata"]
        assert meta["canonical_curve_id"] == first["curve_id"]
        assert meta["exact_q_isomorphism_verified"] is True
        assert meta["derivation_identity_separate"] is True
        assert meta["curve_fingerprint_is_identity_authority"] is False
        assert meta["canonical_identity_method"] == (
            "global-minimal-invariants+sage-is_isomorphic-v1"
        )


def test_two_derivation_edges_resume_to_one_scientific_curve(tmp_path):
    db = connect(tmp_path / "lineage.db")
    E = _curve()
    canonical = get_or_create_canonical_curve(db, E)
    curve_id = canonical["curve_id"]

    run_id = create_pipeline_run(
        db,
        pipeline_name="Canonical identity",
        target_mode="general",
        target={"pool_mode": "open"},
        stages=[{"id": "quadratic_twist_sweep", "config": {}}],
        run_config={"project_root": "."},
    )
    parent1 = upsert_pipeline_candidate(
        db,
        run_id=run_id,
        provider_key="general",
        parameter="parent-1",
        status="running",
    )
    parent2 = upsert_pipeline_candidate(
        db,
        run_id=run_id,
        provider_key="general",
        parameter="parent-2",
        status="running",
    )
    child1 = upsert_pipeline_candidate(
        db,
        run_id=run_id,
        provider_key="general|quadratic_twist_sweep",
        parameter="path-one",
        curve_id=curve_id,
        status="running",
    )
    child2 = upsert_pipeline_candidate(
        db,
        run_id=run_id,
        provider_key="general|quadratic_twist_sweep",
        parameter="path-two",
        curve_id=curve_id,
        status="running",
    )

    metadata1 = {
        "child_kind": "direct_curve",
        "created_by_pipeline": True,
        "canonical_curve_id": curve_id,
    }
    metadata2 = {
        "child_kind": "direct_curve",
        "created_by_pipeline": False,
        "canonical_curve_id": curve_id,
    }
    record_pipeline_derivation(
        db,
        run_id=run_id,
        stage_index=1,
        stage_id="quadratic_twist_sweep",
        parent_candidate_id=int(parent1["id"]),
        child_candidate_id=int(child1["id"]),
        transform_kind="quadratic_twist",
        metadata=metadata1,
    )
    record_pipeline_derivation(
        db,
        run_id=run_id,
        stage_index=1,
        stage_id="quadratic_twist_sweep",
        parent_candidate_id=int(parent2["id"]),
        child_candidate_id=int(child2["id"]),
        transform_kind="quadratic_twist",
        metadata=metadata2,
    )

    rows = pipeline_derivations(db, run_id, stage_index=1)
    assert len(rows) == 2
    assert {int(row["child_curve_id"]) for row in rows} == {curve_id}
    assert {str(row["child_parameter"]) for row in rows} == {
        "path-one", "path-two"
    }

    restored = [
        runner._derived_item_from_lineage(
            db,
            run_config={"project_root": "."},
            row=row,
        )
        for row in rows
    ]
    assert {item["candidate"]["parameter"] for item in restored} == {
        "path-one", "path-two"
    }
    assert {item["candidate"]["curve_id"] for item in restored} == {
        curve_id
    }


def test_shared_canonical_curve_cleanup_clears_all_candidate_refs(
    tmp_path, monkeypatch
):
    db = connect(tmp_path / "cleanup.db")
    E = _curve()
    canonical = get_or_create_canonical_curve(db, E)
    curve_id = canonical["curve_id"]
    run_id = create_pipeline_run(
        db,
        pipeline_name="Canonical cleanup",
        target_mode="general",
        target={"pool_mode": "open"},
        stages=[{"id": "quadratic_twist_sweep", "config": {}}],
        run_config={},
    )
    upsert_pipeline_candidate(
        db,
        run_id=run_id,
        provider_key="derived",
        parameter="path-a",
        curve_id=curve_id,
        status="running",
    )
    upsert_pipeline_candidate(
        db,
        run_id=run_id,
        provider_key="derived",
        parameter="path-b",
        curve_id=curve_id,
        status="running",
    )
    calls = []

    def discard_once(db_arg, *, curve_id, created, run_config):
        calls.append((curve_id, created))
        return True

    monkeypatch.setattr(
        runner, "_apply_builder_retention", discard_once
    )
    cache = {
        ("derived", "path-a"): {
            "context": {"curve_id": curve_id},
            "created": True,
        },
        ("derived", "path-b"): {
            "context": {"curve_id": curve_id},
            "created": False,
        },
    }
    runner._cleanup_population_contexts(
        db,
        run={"id": run_id},
        run_config={},
        cache=cache,
    )

    assert calls == [(curve_id, True)]
    assert get_pipeline_candidate(
        db, run_id, "derived", "path-a"
    )["curve_id"] is None
    assert get_pipeline_candidate(
        db, run_id, "derived", "path-b"
    )["curve_id"] is None


def test_repeated_canonical_lookup_reuses_created_row(tmp_path):
    db = connect(tmp_path / "repeat.db")
    first = get_or_create_canonical_curve(db, _curve())
    second = get_or_create_canonical_curve(db, _curve())

    assert first["curve_id"] == second["curve_id"]
    assert first["created"] is True
    assert second["created"] is False
    row = get_curve(db, first["curve_id"])
    assert row["family"] == CANONICAL_FAMILY
    assert str(row["parameter"]).startswith("qiso:")


def test_resume_reconstructs_direct_and_family_reference_children(
    tmp_path, monkeypatch
):
    db = connect(tmp_path / "multi-kind-resume.db")
    E = _curve()
    canonical = get_or_create_canonical_curve(db, E)
    curve_id = canonical["curve_id"]

    run_id = create_pipeline_run(
        db,
        pipeline_name="Multi-kind resume",
        target_mode="family",
        target={},
        stages=[{"id": "rank_jump_base_change", "config": {}}],
        run_config={"project_root": "."},
    )
    parent_direct = upsert_pipeline_candidate(
        db,
        run_id=run_id,
        provider_key="parent:v1",
        parameter="p-direct",
        status="running",
    )
    parent_family = upsert_pipeline_candidate(
        db,
        run_id=run_id,
        provider_key="parent:v1",
        parameter="p-family",
        status="running",
    )
    child_direct = upsert_pipeline_candidate(
        db,
        run_id=run_id,
        provider_key="parent:v1|rank_jump_base_change",
        parameter="direct-path",
        curve_id=curve_id,
        status="running",
    )
    child_family = upsert_pipeline_candidate(
        db,
        run_id=run_id,
        provider_key="child:v2|rank_jump_base_change",
        parameter="17/19",
        status="running",
    )
    record_pipeline_derivation(
        db,
        run_id=run_id,
        stage_index=1,
        stage_id="rank_jump_base_change",
        parent_candidate_id=int(parent_direct["id"]),
        child_candidate_id=int(child_direct["id"]),
        transform_kind="rank_jump_base_change",
        metadata={
            "child_kind": "direct_curve",
            "canonical_curve_id": curve_id,
        },
    )
    record_pipeline_derivation(
        db,
        run_id=run_id,
        stage_index=1,
        stage_id="rank_jump_base_change",
        parent_candidate_id=int(parent_family["id"]),
        child_candidate_id=int(child_family["id"]),
        transform_kind="rank_jump_base_change",
        metadata={
            "child_kind": "family_reference",
            "plugin_id": "child",
            "variant_id": "v2",
        },
    )

    plugin = SimpleNamespace(id="child")
    variant = SimpleNamespace(id="v2", family_spec="fixture-family")
    family = object()
    monkeypatch.setattr(
        runner, "get_plugin", lambda project_root, plugin_id: plugin
    )
    monkeypatch.setattr(
        runner, "get_variant", lambda plugin_arg, variant_id: variant
    )
    monkeypatch.setattr(
        runner, "load_family", lambda family_spec: family
    )
    monkeypatch.setattr(
        runner, "plugin_fingerprints",
        lambda plugin_arg, variant_arg: {"fixture": "yes"},
    )

    rows = pipeline_derivations(db, run_id, stage_index=1)
    restored = [
        runner._derived_item_from_lineage(
            db,
            run_config={"project_root": "."},
            row=row,
        )
        for row in rows
    ]
    direct = next(
        item for item in restored
        if item["candidate"]["parameter"] == "direct-path"
    )
    family_ref = next(
        item for item in restored
        if item["candidate"]["parameter"] == "17/19"
    )

    assert direct["candidate"]["curve_id"] == curve_id
    assert direct["family"] is None
    assert family_ref["plugin"] is plugin
    assert family_ref["variant"] is variant
    assert family_ref["family"] is family
    assert family_ref["fingerprints"] == {"fixture": "yes"}
