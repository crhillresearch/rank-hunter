import json

from rank42.db import connect, upsert_curve, update_curve
import rank42.rank_pipeline as rp
from rank42.rank_evidence import record_rank_evidence


def setup_curve(db,lower=3):
    cid=upsert_curve(db,family='pipe',parameter='1')
    update_curve(db,cid,a_invariants_json=json.dumps(['0','0','0','-1','0']),generic_lower=lower)
    return cid


def fake(engine,status='completed',upper=None):
    return {
        'engine':engine,'engine_version':'test','sage_version':'test','evidence_type':'rank_bounds',
        'status':status,'rigorous':True,'rigorous_lower':None,'rigorous_upper':upper,'exact_rank':None,
        'conditional_analytic_upper':None,'numerical_rank_signal':None,'assumptions':[],'points_found':[],
        'timed_out':status=='timeout','partial':False,'minimal_model_a_invariants':['0','0','0','-1','0'],
        'elapsed_seconds':0.01,'options':{},
    }


def test_auto_is_pari_first_and_does_not_escalate_completed_gap(tmp_path,monkeypatch):
    db=connect(tmp_path/'x.db'); cid=setup_curve(db,3); calls=[]
    monkeypatch.setattr(rp,'runtime_versions',lambda engine:{'engine_version':'test','sage_version':'test'})
    def run(model,engine,timeout,**kwargs): calls.append(engine); return fake(engine,upper=5)
    monkeypatch.setattr(rp,'run_engine_bounds',run)
    out=rp.run_rank_bounds_for_curve(db,cid,engine='auto',timeout=1)
    assert calls==['pari']
    assert out['rigorous_lower']==3 and out['rigorous_upper']==5 and out['exact_rank'] is None


def test_auto_falls_back_to_mwrank_on_pari_error(tmp_path,monkeypatch):
    db=connect(tmp_path/'x.db'); cid=setup_curve(db,3); calls=[]
    monkeypatch.setattr(rp,'runtime_versions',lambda engine:{'engine_version':'test','sage_version':'test'})
    def run(model,engine,timeout,**kwargs):
        calls.append(engine)
        return fake(engine,status='error',upper=None) if engine=='pari' else fake(engine,upper=4)
    monkeypatch.setattr(rp,'run_engine_bounds',run)
    out=rp.run_rank_bounds_for_curve(db,cid,engine='auto',timeout=1,mwrank_timeout=1)
    assert calls==['pari','mwrank']
    assert out['rigorous_upper']==4 and out['exact_rank'] is None


def test_auto_falls_back_to_mwrank_on_pari_timeout_preserving_lower(tmp_path,monkeypatch):
    db=connect(tmp_path/'x.db'); cid=setup_curve(db,7); calls=[]
    monkeypatch.setattr(rp,'runtime_versions',lambda engine:{'engine_version':'test','sage_version':'test'})
    def run(model,engine,timeout,**kwargs):
        calls.append(engine)
        return fake(engine,status='timeout',upper=None) if engine=='pari' else fake(engine,status='timeout',upper=None)
    monkeypatch.setattr(rp,'run_engine_bounds',run)
    out=rp.run_rank_bounds_for_curve(db,cid,engine='auto',timeout=1,mwrank_timeout=1)
    assert calls==['pari','mwrank']
    assert out['rigorous_lower']==7 and out['rigorous_upper'] is None and out['exact_rank'] is None
    assert out['status']=='timeout'


def test_pari_upper_equal_stored_rigorous_lower_certifies_exact(tmp_path,monkeypatch):
    db=connect(tmp_path/'x.db'); cid=setup_curve(db,5)
    monkeypatch.setattr(rp,'runtime_versions',lambda engine:{'engine_version':'test','sage_version':'test'})
    monkeypatch.setattr(rp,'run_engine_bounds',lambda model,engine,timeout,**kwargs:fake(engine,upper=5))
    out=rp.run_rank_bounds_for_curve(db,cid,engine='auto',timeout=1)
    assert out['exact_rank']==5


def test_escalate_runs_mwrank_after_completed_gap(tmp_path,monkeypatch):
    db=connect(tmp_path/'x.db'); cid=setup_curve(db,2); calls=[]
    monkeypatch.setattr(rp,'runtime_versions',lambda engine:{'engine_version':'test','sage_version':'test'})
    def run(model,engine,timeout,**kwargs): calls.append(engine); return fake(engine,upper=4 if engine=='pari' else 3)
    monkeypatch.setattr(rp,'run_engine_bounds',run)
    out=rp.run_rank_bounds_for_curve(db,cid,engine='auto',timeout=1,mwrank_timeout=1,escalate=True)
    assert calls==['pari','mwrank']
    assert out['rigorous_upper']==3

def test_compatible_rank_bounds_are_reused_from_cache(tmp_path,monkeypatch):
    db=connect(tmp_path/'x.db'); cid=setup_curve(db,2); calls=[]
    monkeypatch.setattr(rp,'runtime_versions',lambda engine:{'engine_version':'test','sage_version':'test'})
    def run(model,engine,timeout,**kwargs): calls.append(engine); return fake(engine,upper=4)
    monkeypatch.setattr(rp,'run_engine_bounds',run)
    first=rp.run_rank_bounds_for_curve(db,cid,engine='pari',timeout=11)
    second=rp.run_rank_bounds_for_curve(db,cid,engine='pari',timeout=11)
    assert calls==['pari']
    assert second['runs'][0].get('cached') is True
    assert first['rigorous_upper']==second['rigorous_upper']==4


def test_auto_reports_mwrank_inconclusive_without_losing_lower(tmp_path,monkeypatch):
    db=connect(tmp_path/'x.db'); cid=setup_curve(db,0); calls=[]
    monkeypatch.setattr(rp,'runtime_versions',lambda engine:{'engine_version':'test','sage_version':'test'})
    def run(model,engine,timeout,**kwargs):
        calls.append(engine)
        return fake(engine,status='error',upper=None) if engine=='pari' else fake(engine,status='inconclusive',upper=None)
    monkeypatch.setattr(rp,'run_engine_bounds',run)
    out=rp.run_rank_bounds_for_curve(db,cid,engine='auto',timeout=1,mwrank_timeout=1)
    assert calls==['pari','mwrank']
    assert out['rigorous_lower']==0 and out['rigorous_upper'] is None
    assert out['status']=='inconclusive'
    row=db.execute('SELECT status,error FROM curves WHERE id=?',(cid,)).fetchone()
    assert row['status']=='rank_bounds_inconclusive'
    assert 'inconclusive' in row['error']


def test_noncompleted_rank_bounds_are_not_reused_from_cache(tmp_path,monkeypatch):
    db=connect(tmp_path/'x.db'); cid=setup_curve(db,0); calls=[]
    monkeypatch.setattr(rp,'runtime_versions',lambda engine:{'engine_version':'test','sage_version':'test'})
    def run(model,engine,timeout,**kwargs):
        calls.append(engine)
        return fake(engine,status='error',upper=None)
    monkeypatch.setattr(rp,'run_engine_bounds',run)
    first=rp.run_rank_bounds_for_curve(db,cid,engine='pari',timeout=11)
    second=rp.run_rank_bounds_for_curve(db,cid,engine='pari',timeout=11)
    assert first['status']=='error' and second['status']=='error'
    assert calls==['pari','pari']


def test_auto_skips_known_mwrank_size_limit_for_same_model(tmp_path, monkeypatch):
    db=connect(tmp_path/'x.db'); cid=setup_curve(db,7); calls=[]
    model=['0','0','0','-1','0']
    record_rank_evidence(
        db,
        curve_id=cid,
        model=model,
        data={
            'engine':'mwrank','evidence_type':'rank_bounds','status':'inconclusive',
            'rigorous':True,'rigorous_lower':None,'rigorous_upper':None,'exact_rank':None,
            'conditional_analytic_upper':None,'numerical_rank_signal':None,
            'assumptions':[],'points_found':[],
            'options':{'timeout':1,'failure_class':'MWRANK_SIZE_LIMIT'},
        },
    )
    monkeypatch.setattr(rp,'runtime_versions',lambda engine:{'engine_version':'test','sage_version':'test'})
    monkeypatch.setattr(rp,'eclib_rh_available',lambda:False)
    def run(model,engine,timeout,**kwargs):
        calls.append(engine)
        return fake(engine,status='timeout',upper=None)
    monkeypatch.setattr(rp,'run_engine_bounds',run)
    out=rp.run_rank_bounds_for_curve(db,cid,engine='auto',timeout=1,mwrank_timeout=1)
    assert calls==['pari']
    assert out['rigorous_lower']==7
    assert out['rigorous_upper'] is None
    assert out['status']=='timeout'
