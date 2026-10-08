import json
import sqlite3

import pytest
from pathlib import Path
from types import SimpleNamespace

from rank42.candidates import candidate_rows, create_pool, replace_pool_rows
from rank42.db import connect
from rank42.corpus import (
    apply_candidate_corpus_policy,
    candidate_corpus_identity,
    corpus_match_summary,
    corpus_seed_candidate,
    connect_corpus,
    corpus_path,
    corpus_stats,
    count_corpus_curves,
    corpus_status,
    list_corpus_curves,
    lookup_candidate_corpora,
    lookup_plugin_corpora,
)
from rank42.family_charts import FamilyChart
from rank42.plugins import CorpusSpec


def _write_corpus(path):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE corpus_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE artifacts(
            id INTEGER PRIMARY KEY, artifact_id INTEGER, run_id INTEGER,
            name TEXT, created_at TEXT
        );
        CREATE TABLE files(
            id INTEGER PRIMARY KEY, artifact_id INTEGER, path TEXT
        );
        CREATE TABLE curves(
            id INTEGER PRIMARY KEY, canonical_key TEXT UNIQUE, parameter TEXT,
            family_key TEXT, rank_lower INTEGER, rank_upper INTEGER,
            exact_rank INTEGER, torsion TEXT, root_number INTEGER,
            observation_count INTEGER DEFAULT 0, conflict INTEGER DEFAULT 0,
            metadata_json TEXT
        );
        CREATE TABLE aliases(
            curve_id INTEGER, family_key TEXT, parameter TEXT,
            UNIQUE(family_key,parameter)
        );
        CREATE TABLE observations(
            id INTEGER PRIMARY KEY, curve_id INTEGER, artifact_id INTEGER,
            file_id INTEGER, raw_parameter TEXT, raw_family TEXT,
            rank_lower INTEGER, rank_upper INTEGER, exact_rank INTEGER,
            torsion TEXT, root_number INTEGER, status TEXT, metadata_json TEXT
        );
        """
    )
    db.execute("INSERT INTO corpus_meta VALUES('schema_version','1')")
    db.execute(
        """INSERT INTO curves(
            id,canonical_key,parameter,family_key,rank_lower,rank_upper,exact_rank,
            observation_count,conflict,metadata_json
        ) VALUES(1,'cef|-61/16','-61/16','cef',9,9,9,2,0,'{}')"""
    )
    db.execute("INSERT INTO aliases VALUES(1,'cef','-61/16')")
    db.execute("INSERT INTO aliases VALUES(1,'bef','61/16')")
    db.commit()
    db.close()


def test_corpus_lookup_uses_aliases_and_stays_external(tmp_path):
    corpus=CorpusSpec(
        id="curves2",name="Curves2",cache_file="curves2.db",
        variant_family_keys=(("cef","cef"),),
    )
    plugin=SimpleNamespace(id="dmt",plugin_type="family",corpora=(corpus,))
    variant=SimpleNamespace(id="cef")
    path=corpus_path(tmp_path,plugin,corpus)
    _write_corpus(path)

    status=corpus_status(tmp_path,plugin,corpus)
    assert status["ready"] is True
    assert status["meta"]["schema_version"]=="1"

    matches=lookup_plugin_corpora(tmp_path,plugin,variant,"-61/16")
    assert len(matches)==1
    assert matches[0]["exact_rank"]==9
    assert matches[0]["corpus_family_key"]=="cef"


def test_corpus_stats_and_browse(tmp_path):
    corpus=CorpusSpec(id="curves2",name="Curves2",cache_file="curves2.db")
    plugin=SimpleNamespace(id="demo",plugin_type="family",corpora=(corpus,))
    path=corpus_path(tmp_path,plugin,corpus)
    _write_corpus(path)

    db=connect_corpus(tmp_path,plugin,corpus)
    try:
        stats=corpus_stats(db)
        assert stats["curves"]==1
        assert stats["exact_curves"]==1
        assert stats["max_exact_rank"]==9
        rows=list_corpus_curves(db,family_key="cef",min_rank=8)
        assert [row["parameter"] for row in rows]==["-61/16"]
    finally:
        db.close()


def test_corpus_count_and_unlimited_listing_respect_exact_filter(tmp_path):
    corpus=CorpusSpec(id="curves2",name="Curves2",cache_file="curves2.db")
    plugin=SimpleNamespace(id="demo",plugin_type="family",corpora=(corpus,))
    path=corpus_path(tmp_path,plugin,corpus)
    _write_corpus(path)

    raw=sqlite3.connect(path)
    raw.execute(
        """INSERT INTO curves(
            id,canonical_key,parameter,family_key,rank_lower,rank_upper,exact_rank,
            observation_count,conflict,metadata_json
        ) VALUES(2,'cef|1/2','1/2','cef',8,NULL,NULL,1,0,'{}')"""
    )
    raw.commit()
    raw.close()

    db=connect_corpus(tmp_path,plugin,corpus)
    try:
        assert count_corpus_curves(db,family_key="cef")==2
        assert count_corpus_curves(db,family_key="cef",exact_only=True)==1
        rows=list_corpus_curves(db,family_key="cef",limit=None)
        assert [row["parameter"] for row in rows]==["-61/16","1/2"]
        exact=list_corpus_curves(db,family_key="cef",exact_only=True,limit=None)
        assert [row["parameter"] for row in exact]==["-61/16"]
    finally:
        db.close()

def test_candidate_corpus_policy_filters_and_annotates_consistently(tmp_path):
    corpus=CorpusSpec(
        id="curves2",name="Curves2",cache_file="curves2.db",
        variant_family_keys=(("cef","cef"),),
    )
    plugin=SimpleNamespace(id="dmt",plugin_type="family",corpora=(corpus,))
    variant=SimpleNamespace(id="cef")
    _write_corpus(corpus_path(tmp_path,plugin,corpus))
    records=[
        {"parameter":"-61/16","score":9.0},
        {"parameter":"1/2","score":8.0},
    ]

    annotated, summary=apply_candidate_corpus_policy(
        tmp_path,plugin,variant,records,"annotate"
    )
    assert [rec["parameter"] for rec in annotated]==["-61/16","1/2"]
    assert annotated[0]["corpus_summary"]["known"] is True
    assert annotated[0]["corpus_matches"][0]["exact_rank"]==9
    assert annotated[1]["corpus_summary"]["known"] is False
    assert summary=={
        "policy":"annotate",
        "declared":1,
        "ready":1,
        "known":1,
        "retained":2,
        "classified":True,
    }

    excluded, summary=apply_candidate_corpus_policy(
        tmp_path,plugin,variant,records,"exclude-known"
    )
    assert [rec["parameter"] for rec in excluded]==["1/2"]
    assert summary["known"]==1
    assert summary["retained"]==1

    known_only, summary=apply_candidate_corpus_policy(
        tmp_path,plugin,variant,records,"known-only"
    )
    assert [rec["parameter"] for rec in known_only]==["-61/16"]
    assert summary["known"]==1
    assert summary["retained"]==1


def test_candidate_corpus_policy_requires_ready_corpus_for_filtering(tmp_path):
    corpus=CorpusSpec(
        id="curves2",name="Curves2",cache_file="curves2.db",
        variant_family_keys=(("cef","cef"),),
    )
    plugin=SimpleNamespace(id="dmt",plugin_type="family",corpora=(corpus,))
    variant=SimpleNamespace(id="cef")
    records=[{"parameter":"1/2","score":8.0}]

    annotated, summary=apply_candidate_corpus_policy(
        tmp_path,plugin,variant,records,"annotate"
    )
    assert annotated==records
    assert summary["ready"]==0
    assert summary["classified"] is False

    with pytest.raises(ValueError, match="no declared corpus is built/ready"):
        apply_candidate_corpus_policy(
            tmp_path,plugin,variant,records,"exclude-known"
        )
    with pytest.raises(ValueError, match="no declared corpus is built/ready"):
        apply_candidate_corpus_policy(
            tmp_path,plugin,variant,records,"known-only"
        )



def test_chart_candidate_library_match_uses_exact_native_identity(tmp_path):
    corpus = CorpusSpec(
        id="curves2",
        name="Curves2",
        cache_file="curves2.db",
        variant_family_keys=(("search", "wrong-chart-key"), ("native", "cef")),
    )
    chart = FamilyChart(
        id="published",
        label="Published",
        variant_id="search",
        native_variant_id="native",
        native_family_spec="dummy-native",
        native_family_key="dmt:native",
        matrix=("-1", "0", "0", "1"),
        candidate_defaults={},
    )
    plugin = SimpleNamespace(
        id="dmt",
        plugin_type="family",
        corpora=(corpus,),
        charts=(chart,),
        default_chart_id="published",
    )
    variant = SimpleNamespace(id="search")
    _write_corpus(corpus_path(tmp_path, plugin, corpus))

    record = {
        "t": "61/16",
        "chart_id": "published",
        "chart_parameter": "61/16",
        "native_parameter": "-61/16",
    }
    identity = candidate_corpus_identity(plugin, variant, corpus, record)
    assert identity == {
        "parameter_space": "native",
        "variant_id": "native",
        "family_key": "cef",
        "parameter": "-61/16",
        "chart_id": "published",
    }

    matches = lookup_candidate_corpora(tmp_path, plugin, variant, record)
    assert len(matches) == 1
    assert matches[0]["exact_rank"] == 9
    assert matches[0]["corpus_family_key"] == "cef"
    assert matches[0]["corpus_match_identity"] == identity


def test_chart_candidate_library_identity_rejects_mismatched_native_parameter():
    corpus = CorpusSpec(
        id="curves2",
        name="Curves2",
        cache_file="curves2.db",
        variant_family_keys=(("native", "cef"),),
    )
    chart = FamilyChart(
        id="published",
        label="Published",
        variant_id="search",
        native_variant_id="native",
        native_family_spec="dummy-native",
        native_family_key="dmt:native",
        matrix=("-1", "0", "0", "1"),
        candidate_defaults={},
    )
    plugin = SimpleNamespace(
        id="dmt",
        plugin_type="family",
        corpora=(corpus,),
        charts=(chart,),
        default_chart_id="published",
    )
    variant = SimpleNamespace(id="search")

    with pytest.raises(ValueError, match="chart/native parameter mismatch"):
        candidate_corpus_identity(
            plugin,
            variant,
            corpus,
            {
                "t": "61/16",
                "chart_id": "published",
                "chart_parameter": "61/16",
                "native_parameter": "1/2",
            },
        )


def test_cross_library_exact_rank_disagreement_is_visible_conflict():
    summary = corpus_match_summary([
        {
            "corpus_id": "a",
            "exact_rank": 9,
            "rank_lower": 9,
            "rank_upper": 9,
            "conflict": False,
        },
        {
            "corpus_id": "b",
            "exact_rank": 10,
            "rank_lower": 10,
            "rank_upper": 10,
            "conflict": False,
        },
    ])

    assert summary["known"] is True
    assert summary["exact_rank"] is None
    assert summary["rank_lower"] == 10
    assert summary["rank_upper"] == 9
    assert summary["conflict"] is True
    assert "exact_rank_disagreement" in summary["conflict_reasons"]
    assert "bounds_disagreement" in summary["conflict_reasons"]


def test_cross_library_exact_outside_other_interval_is_conflict():
    summary = corpus_match_summary([
        {
            "corpus_id": "exact",
            "exact_rank": 9,
            "rank_lower": 9,
            "rank_upper": 9,
            "conflict": False,
        },
        {
            "corpus_id": "interval",
            "exact_rank": None,
            "rank_lower": 5,
            "rank_upper": 8,
            "conflict": False,
        },
    ])

    assert summary["exact_rank"] is None
    assert summary["rank_lower"] == 9
    assert summary["rank_upper"] == 8
    assert summary["conflict"] is True
    assert summary["conflict_reasons"] == ["bounds_disagreement"]


def test_library_seed_candidate_has_typed_known_rank_provenance():
    corpus = CorpusSpec(id="curves2", name="Curves2", cache_file="curves2.db")
    record = corpus_seed_candidate(
        {
            "id": 17,
            "parameter": "-61/16",
            "exact_rank": 9,
            "rank_lower": 9,
            "rank_upper": 9,
            "conflict": 0,
        },
        corpus,
    )

    assert record["score"] == 9.0
    assert record["ranking_value"] == 9.0
    assert record["ranking_kind"] == "known_rank"
    assert record["ranking_provenance"]["source"] == "research_library"
    assert record["ranking_provenance"]["rank_field"] == "exact_rank"
    assert record["score_provenance"]["ranking_kind"] == "known_rank"


def test_library_candidate_ranking_provenance_survives_pool_persistence(tmp_path):
    corpus = CorpusSpec(id="curves2", name="Curves2", cache_file="curves2.db")
    seeded = corpus_seed_candidate(
        {
            "id": 23,
            "parameter": "5/7",
            "exact_rank": None,
            "rank_lower": 8,
            "rank_upper": None,
            "conflict": 0,
        },
        corpus,
    )

    db = connect(tmp_path / "rank42.db")
    try:
        pool = create_pool(
            db,
            name="library-seed",
            plugin_id="demo",
            family_spec="dummy-family",
            generation={"source": "corpus", "corpus_id": corpus.id},
        )
        replace_pool_rows(db, pool["id"], [seeded])
        row = candidate_rows(db, pool["id"])[0]
        metadata = json.loads(row["metadata_json"])

        assert row["score"] == 8.0
        assert metadata["ranking_kind"] == "known_rank"
        assert metadata["ranking_value"] == 8.0
        assert metadata["score_provenance"]["source"] == "research_library"
        assert metadata["score_provenance"]["corpus_id"] == "curves2"
        assert metadata["corpus_id"] == "curves2"
        assert metadata["corpus_curve_id"] == 23
    finally:
        db.close()
