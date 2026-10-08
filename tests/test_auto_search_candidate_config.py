import json
import sys
from types import SimpleNamespace

from sage.all import EllipticCurve, QQ

from rank42 import auto_search
from rank42 import search_classical_covering as classical_covering
from rank42 import search_plugin_geometry as plugin_geometry_host
from rank42 import plugin_geometry_result as geometry_result
from rank42.db import connect, get_curve, upsert_curve
from rank42.plugin_api import FamilyAdapterV1


def test_candidate_config_preserves_sampled_engine_settings(monkeypatch):
    plugin = SimpleNamespace()
    variant = SimpleNamespace()

    monkeypatch.setattr(
        auto_search,
        "variant_candidate_defaults",
        lambda plugin, variant: {
            "a_min": -25,
            "a_max": 25,
            "b_min": 1,
            "b_max": 100,
            "engine": "sampled",
            "sample_count": 25000,
            "sample_seed": 1811,
            "stage_bounds": "127,523",
            "stage_keeps": "10000,2000",
            "top": 5000,
        },
    )
    monkeypatch.setattr(
        auto_search,
        "search_presets_for_variant",
        lambda plugin, variant: (),
    )

    config = auto_search._candidate_config(plugin, variant, 64)

    assert config["engine"] == "sampled"
    assert config["sample_count"] == 25000
    assert config["sample_seed"] == 1811
    assert config["top"] == 64


def test_generate_pool_passes_sampled_settings_to_candidate_generator(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        auto_search,
        "get_campaign",
        lambda db, campaign_id: {"pool_id": None},
    )
    monkeypatch.setattr(
        auto_search,
        "_candidate_config",
        lambda plugin, variant, pool_size: {
            "a_min": -25,
            "a_max": 25,
            "b_min": 1,
            "b_max": 100,
            "stage_bounds": "127,523",
            "stage_keeps": "10000,2000",
            "engine": "sampled",
            "sample_count": 25000,
            "sample_seed": 1811,
            "top": 64,
        },
    )

    class FakeRun:
        returncode = 0

    def fake_run(cmd, check=False):
        captured["cmd"] = list(cmd)
        return FakeRun()

    monkeypatch.setattr(auto_search.subprocess, "run", fake_run)
    monkeypatch.setattr(
        auto_search,
        "update_campaign",
        lambda *args, **kwargs: None,
    )

    class FakeDB:
        def execute(self, sql, params):
            class Cursor:
                def fetchone(self):
                    return {
                        "id": 7,
                        "name": "pool",
                        "candidate_count": 64,
                    }
            return Cursor()

    args = SimpleNamespace(
        pool_size=64,
        project_root=".",
        db="rank42.db",
    )
    plugin = SimpleNamespace(id="elkies_x1092_rank17")
    variant = SimpleNamespace(id="qbc11")

    auto_search._generate_pool(
        args,
        FakeDB(),
        3,
        plugin,
        variant,
    )

    cmd = captured["cmd"]
    assert "--sample-count" in cmd
    assert cmd[cmd.index("--sample-count") + 1] == "25000"
    assert "--sample-seed" in cmd
    assert cmd[cmd.index("--sample-seed") + 1] == "1811"



def test_classical_covering_upper_uses_shared_interval_promotion(monkeypatch):
    captured = {}

    class Curve:
        def a_invariants(self):
            return [0, 0, 0, -1, 0]

    monkeypatch.setattr(
        classical_covering,
        "rigorous_witness_basis",
        lambda db, curve_id, E: ([], 0, True),
    )
    monkeypatch.setattr(
        classical_covering,
        "run_classical_descent",
        lambda *args, **kwargs: {
            "rigorous_upper": 4,
            "reported_upper": 4,
            "upper_bound_rigorous": True,
            "points": [],
            "runtime_seconds": 0.25,
            "minimal_model_a_invariants": [0, 0, 0, -1, 0],
        },
    )
    monkeypatch.setattr(
        classical_covering,
        "research_rank_state",
        lambda db, curve_id: {
            "rigorous_lower": 2,
            "specialization_rigorous_lower": 2,
            "rigorous_upper": None,
            "exact_rank": None,
            "rank_inconsistent": False,
        },
    )
    monkeypatch.setattr(
        classical_covering,
        "certify_ledger_growth",
        lambda *args, **kwargs: {
            "attempts": 0,
            "basis_complete": True,
        },
    )

    def fake_promote(db, **kwargs):
        captured.update(kwargs)
        return {
            "evidence_id": 123,
            "rigorous_lower": 2,
            "rigorous_upper": 4,
            "exact_rank": None,
            "rank_inconsistent": False,
        }

    monkeypatch.setattr(
        classical_covering,
        "promote_rigorous_rank_interval",
        fake_promote,
    )

    result = auto_search._run_classical_covering_escalation(
        object(),
        curve_id=7,
        E=Curve(),
        policy={
            "classical_covering_enabled": True,
            "classical_covering_engine": "mwrank_coverings",
            "classical_covering_timeout": 90,
            "classical_covering_first_limit": 20,
            "classical_covering_second_limit": 10,
        },
        certificate_timeout=120,
        exact_candidates=64,
        attempt_identity="pipeline:1:candidate:2:stage:3:mwrank",
        attempt_number=1,
        retry_policy="manual",
        retry_timeout=300,
    )

    assert captured["curve_id"] == 7
    assert captured["rigorous_upper"] == 4
    assert captured["engine"] == "eclib_mwrank_full_two_descent"
    assert captured["source"] == "auto_search_classical_covering"
    assert result["interval_evidence_id"] == 123
    assert result["rigorous_upper"] == 4
    assert result["attempt_identity"] == "pipeline:1:candidate:2:stage:3:mwrank"
    assert result["attempt_number"] == 1
    assert result["retry_policy"] == "manual"



def test_mwrank_covering_timeout_creates_no_upper_and_keeps_retry_policy(
    monkeypatch
):
    class Curve:
        def a_invariants(self):
            return [0, 0, 0, -1, 0]

    monkeypatch.setattr(
        classical_covering,
        "rigorous_witness_basis",
        lambda db, curve_id, E: ([], 0, True),
    )
    monkeypatch.setattr(
        classical_covering,
        "research_rank_state",
        lambda db, curve_id: {
            "rigorous_lower": 2,
            "specialization_rigorous_lower": 2,
            "rigorous_upper": None,
            "exact_rank": None,
            "rank_inconsistent": False,
        },
    )
    monkeypatch.setattr(
        classical_covering,
        "run_classical_descent",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            classical_covering.ClassicalDescentTimeout("fixture timeout")
        ),
    )
    monkeypatch.setattr(
        classical_covering,
        "promote_rigorous_rank_interval",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("timeout must not promote an upper")
        ),
    )

    result = auto_search._run_classical_covering_escalation(
        object(),
        curve_id=7,
        E=Curve(),
        policy={
            "classical_covering_enabled": True,
            "classical_covering_engine": "mwrank_coverings",
            "classical_covering_timeout": 11,
            "classical_covering_first_limit": 20,
            "classical_covering_second_limit": 10,
        },
        certificate_timeout=120,
        exact_candidates=64,
        attempt_identity="pipeline:9:candidate:2:stage:4:mwrank",
        attempt_number=2,
        retry_policy="escalated",
        retry_timeout=45,
    )

    assert result["status"] == "timeout"
    assert result.get("rigorous_upper") is None
    assert result["rank_growth"] == 0
    assert result["attempt_identity"] == (
        "pipeline:9:candidate:2:stage:4:mwrank"
    )
    assert result["attempt_number"] == 2
    assert result["attempt_timeout"] == 11
    assert result["retry_policy"] == "escalated"
    assert result["retry_timeout"] == 45
    assert result["mathematical_outcome"] == "timeout"


def test_plugin_geometry_legacy_db_writes_are_sandboxed_and_revalidated(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "rank42.db"
    db = connect(db_path)
    E = EllipticCurve(QQ, [0, 0, 0, -1, 1])
    curve_id = upsert_curve(
        db,
        family="sandbox-test",
        parameter="1",
        a_invariants_json=json.dumps(
            [str(value) for value in E.a_invariants()]
        ),
        status="test",
    )

    worker = tmp_path / "legacy_geometry_worker.py"
    worker.write_text(
        "import argparse, json, sqlite3, time\n"
        "ap=argparse.ArgumentParser()\n"
        "ap.add_argument('--db', required=True)\n"
        "ap.add_argument('--curve-id', type=int, required=True)\n"
        "args=ap.parse_args()\n"
        "con=sqlite3.connect(args.db)\n"
        "ts=str(time.time())\n"
        "model=con.execute("
        "\"SELECT a_invariants_json FROM curves WHERE id=?\","
        "(args.curve_id,)).fetchone()[0]\n"
        "con.execute("
        "\"UPDATE curves SET descent_lower=99,descent_upper=99,"
        "exact_rank=99,status='proven_exact' WHERE id=?\","
        "(args.curve_id,))\n"
        "con.execute("
        "\"INSERT INTO rank_evidence("
        "curve_id,evidence_key,engine,evidence_type,rigorous,"
        "rigorous_lower,rigorous_upper,exact_rank,assumptions_json,status,"
        "timed_out,partial,model_a_invariants_json,options_json,points_json,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)\","
        "(args.curve_id,'plugin-bypass','evil-plugin','rank_bounds',1,"
        "99,99,99,'[]','completed',0,0,model,'{}','[]',ts,ts))\n"
        "con.execute("
        "\"INSERT INTO points("
        "curve_id,x,y,source,role,exact_verified,independence_status,"
        "rigorous_independent,search_ref,plugin_id,metadata_json,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)\","
        "(args.curve_id,'0','1','evil-plugin','rigorous_witness',1,"
        "'rigorous_independent',1,'evil','evil','{}',ts,ts))\n"
        "con.commit(); con.close()\n",
        encoding="utf-8",
    )

    def build_target_search_command(*, python, db, curve_id, options):
        return [
            str(python),
            str(worker),
            "--db",
            str(db),
            "--curve-id",
            str(int(curve_id)),
        ]

    class Adapter(FamilyAdapterV1):
        def build_target_search_command(self, **kwargs):
            return build_target_search_command(**kwargs)

    adapter = Adapter()
    variant = SimpleNamespace(id="native", family_spec="fixture", manifest={})
    plugin = SimpleNamespace(
        id="evil-demo",
        version="1.0",
        capabilities={"target_search"},
        manifest={},
        variants=[variant],
    )
    monkeypatch.setattr(plugin_geometry_host, "load_adapter", lambda plugin: adapter)
    monkeypatch.setattr(
        plugin_geometry_host,
        "apply_search_command_features",
        lambda *args, **kwargs: (list(args[2]), []),
    )
    monkeypatch.setattr(
        geometry_result,
        "certify_ledger_growth",
        lambda *args, **kwargs: {
            "attempts": 0,
            "growth": 0,
            "rigorous_lower": 0,
            "basis_complete": True,
        },
    )

    result = auto_search._run_plugin_geometry_target(
        SimpleNamespace(
            geometry_first=True,
            geometry_keep=10,
            target_rank=10,
            ratpoints=None,
            certificate_timeout=5,
            exact_candidates=8,
            geometry_timeout=5,
            project_root=str(tmp_path),
            feature_plugin_ids=None,
        ),
        db,
        curve_id=curve_id,
        plugin=plugin,
        variant=variant,
        rank_order=1,
        E=E,
    )

    assert result["status"] == "completed"
    assert result["result_source"] == "legacy_sandbox_delta"
    assert result["scientific_write_policy"] == (
        "sandbox_core_validated_artifacts_only"
    )
    assert result["legacy_raw_db_scope"] == "temporary_sandbox_only"
    assert result["exact_points_accepted"] == 1
    assert (
        result["sandbox_scientific_writes_discarded"][
            "sandbox_rank_evidence_writes_discarded"
        ]
        == 1
    )
    assert (
        result["sandbox_scientific_writes_discarded"][
            "sandbox_curve_projection_write_discarded"
        ]
        is True
    )

    live_curve = get_curve(db, curve_id)
    assert live_curve["exact_rank"] is None
    assert int(live_curve["descent_lower"] or 0) == 0
    assert int(live_curve["descent_upper"] or 0) == 0
    assert db.execute(
        "SELECT COUNT(*) AS n FROM rank_evidence WHERE curve_id=?",
        (curve_id,),
    ).fetchone()["n"] == 0

    point = db.execute(
        "SELECT * FROM points WHERE curve_id=? AND x='0' AND y='1'",
        (curve_id,),
    ).fetchone()
    assert point is not None
    assert int(point["exact_verified"]) == 1
    assert int(point["rigorous_independent"]) == 0
    assert point["independence_status"] == "unknown"
    assert point["source"] == "plugin_geometry"


def test_plugin_geometry_invalid_typed_artifact_does_not_fallback_to_db_delta(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "rank42.db"
    db = connect(db_path)
    E = EllipticCurve(QQ, [0, 0, 0, -1, 1])
    curve_id = upsert_curve(
        db,
        family="typed-test",
        parameter="1",
        a_invariants_json=json.dumps(
            [str(value) for value in E.a_invariants()]
        ),
        status="test",
    )

    worker = tmp_path / "invalid_typed_geometry_worker.py"
    worker.write_text(
        "import argparse, json, os, sqlite3, time\n"
        "ap=argparse.ArgumentParser()\n"
        "ap.add_argument('--db', required=True)\n"
        "ap.add_argument('--curve-id', type=int, required=True)\n"
        "args=ap.parse_args()\n"
        "con=sqlite3.connect(args.db); ts=str(time.time())\n"
        "con.execute("
        "\"INSERT INTO points("
        "curve_id,x,y,source,role,exact_verified,independence_status,"
        "rigorous_independent,search_ref,plugin_id,metadata_json,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)\","
        "(args.curve_id,'0','1','legacy','candidate',1,'unknown',0,"
        "'legacy','demo','{}',ts,ts))\n"
        "con.commit(); con.close()\n"
        "path=os.environ['RANK42_PLUGIN_GEOMETRY_RESULT_PATH']\n"
        "open(path,'w',encoding='utf-8').write(json.dumps({"
        "'status':'completed','engine':'missing-schema','points':[['0','1']]"
        "}))\n",
        encoding="utf-8",
    )

    class Adapter(FamilyAdapterV1):
        def build_target_search_command(self, **kwargs):
            return [
                str(kwargs["python"]),
                str(worker),
                "--db",
                str(kwargs["db"]),
                "--curve-id",
                str(kwargs["curve_id"]),
            ]

    adapter = Adapter()
    monkeypatch.setattr(plugin_geometry_host, "load_adapter", lambda plugin: adapter)
    monkeypatch.setattr(
        plugin_geometry_host,
        "apply_search_command_features",
        lambda *args, **kwargs: (list(args[2]), []),
    )
    variant = SimpleNamespace(id="native", family_spec="fixture", manifest={})
    plugin = SimpleNamespace(
        id="typed-demo",
        version="1.0",
        capabilities={"target_search"},
        manifest={},
        variants=[variant],
    )

    result = auto_search._run_plugin_geometry_target(
        SimpleNamespace(
            geometry_first=True,
            geometry_keep=10,
            target_rank=10,
            ratpoints=None,
            certificate_timeout=5,
            exact_candidates=8,
            geometry_timeout=5,
            project_root=str(tmp_path),
            feature_plugin_ids=None,
        ),
        db,
        curve_id=curve_id,
        plugin=plugin,
        variant=variant,
        rank_order=1,
        E=E,
    )
    assert result["status"] == "error"
    assert result["artifact_error"] is not None
    assert result["exact_points_accepted"] == 0
    assert db.execute(
        "SELECT COUNT(*) AS n FROM points WHERE curve_id=?",
        (curve_id,),
    ).fetchone()["n"] == 0
