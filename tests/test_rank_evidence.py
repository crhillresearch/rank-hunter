import json
import sqlite3

from rank42.db import CURRENT_SCHEMA_VERSION, connect, get_curve, upsert_curve, update_curve
from rank42.rank_evidence import apply_reduced_rank_state, list_rank_evidence, record_rank_evidence, reduce_rank_records, reduce_rank_state


def curve(db, *, lower=None, upper=None, exact=None):
    cid=upsert_curve(db,family='test',parameter='1',score=0.0)
    update_curve(db,cid,a_invariants_json=json.dumps(['0','0','0','-1','0']),generic_lower=lower,descent_upper=upper,exact_rank=exact)
    return cid


def add(db,cid,**kw):
    data={
        'engine':'pari','evidence_type':'rank_bounds','status':'completed','rigorous':True,
        'rigorous_lower':None,'rigorous_upper':None,'exact_rank':None,
        'conditional_analytic_upper':None,'numerical_rank_signal':None,'assumptions':[],
        'points_found':[],'options':{},
    }
    data.update(kw)
    record_rank_evidence(db,curve_id=cid,model=['0','0','0','-1','0'],data=data)


def test_exact_equality(tmp_path):
    db=connect(tmp_path/'x.db'); cid=curve(db,lower=2)
    add(db,cid,rigorous_upper=2)
    state=apply_reduced_rank_state(db,cid)
    assert state['rigorous_lower']==2
    assert state['rigorous_upper']==2
    assert state['exact_rank']==2
    assert get_curve(db,cid)['exact_rank']==2


def test_unresolved_interval(tmp_path):
    db=connect(tmp_path/'x.db'); cid=curve(db,lower=2)
    add(db,cid,rigorous_upper=4)
    state=reduce_rank_state(db,cid)
    assert state['rigorous_lower']==2 and state['rigorous_upper']==4
    assert state['exact_rank'] is None


def test_existing_stronger_lower_is_preserved(tmp_path):
    db=connect(tmp_path/'x.db'); cid=curve(db,lower=5)
    add(db,cid,rigorous_lower=4,rigorous_upper=6)
    state=reduce_rank_state(db,cid)
    assert state['rigorous_lower']==5 and state['rigorous_upper']==6


def test_timeout_preserves_lower(tmp_path):
    db=connect(tmp_path/'x.db'); cid=curve(db,lower=7)
    add(db,cid,status='timeout',timed_out=True,rigorous_upper=None)
    state=reduce_rank_state(db,cid)
    assert state['rigorous_lower']==7 and state['rigorous_upper'] is None
    assert state['exact_rank'] is None


def test_conditional_upper_is_separate(tmp_path):
    db=connect(tmp_path/'x.db'); cid=curve(db,lower=17)
    add(db,cid,engine='analytic_grh',evidence_type='analytic',rigorous=False,conditional_analytic_upper=20)
    state=reduce_rank_state(db,cid)
    assert state['rigorous_upper'] is None
    assert state['conditional_analytic_upper']==20
    assert state['exact_rank'] is None


def test_numerical_signal_cannot_promote_exact(tmp_path):
    db=connect(tmp_path/'x.db'); cid=curve(db,lower=17)
    add(db,cid,rigorous_upper=18)
    add(db,cid,engine='height_screen',evidence_type='numerical',rigorous=False,numerical_rank_signal=18)
    state=reduce_rank_state(db,cid)
    assert state['rigorous_lower']==17 and state['rigorous_upper']==18
    assert state['numerical_rank_signal']==18
    assert state['exact_rank'] is None



def test_record_reducer_matches_database_reducer(tmp_path):
    db=connect(tmp_path/'x.db'); cid=curve(db,lower=3)
    add(db,cid,rigorous_lower=5,rigorous_upper=7)
    row=get_curve(db,cid)
    evidence=list_rank_evidence(db,cid,limit=10000)
    assert reduce_rank_records(row,evidence)==reduce_rank_state(db,cid)

def test_additive_schema_is_current(tmp_path):
    db=connect(tmp_path/'x.db')
    assert db.execute('PRAGMA user_version').fetchone()[0]==CURRENT_SCHEMA_VERSION
    assert db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rank_evidence'").fetchone() is not None
