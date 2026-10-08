from __future__ import annotations
import json
import streamlit as st
from rank42.feature_hooks import render_feature_hook
from rank42.rank_evidence import list_rank_evidence, reduce_rank_state
from .common import title,curve_options,curve_label,launch,setting,select_row


def _evidence_table(db, curve_id):
    rows=list_rank_evidence(db,int(curve_id),limit=12)
    if not rows:
        st.caption('No structured rank-engine evidence recorded yet.')
        return
    data=[]
    for r in rows:
        try:
            options=json.loads(r['options_json'] or '{}')
        except Exception:
            options={}
        data.append({
            'engine':r['engine'], 'status':r['status'], 'failure':options.get('failure_class'),
            'rigorous lower':r['rigorous_lower'], 'rigorous upper':r['rigorous_upper'],
            'exact':r['exact_rank'], 'GRH/conditional upper':r['conditional_analytic_upper'],
            'numerical signal':r['numerical_rank_signal'],
            'elapsed s':round(float(r['elapsed_seconds']),2) if r['elapsed_seconds'] is not None else None,
        })
    st.dataframe(data,width='stretch',hide_index=True)


def page(db,ctx):
    title('Descent','Establish rigorous rank bounds. PARI is first-line; expensive mwrank/deep certification remains explicit and bounded.','Analysis')
    render_feature_hook(db,ctx,'analysis.descent.after_header')
    rows=curve_options(db)
    if not rows: st.info('No curves with a proven lower bound yet.'); return
    row=select_row('Curve',rows,format_func=curve_label)
    state=reduce_rank_state(db,int(row['id']))
    c1,c2,c3=st.columns(3)
    c1.metric('Rigorous lower',f"≥ {state['rigorous_lower']}")
    c2.metric('Rigorous upper',state['rigorous_upper'] if state['rigorous_upper'] is not None else '—')
    c3.metric('Exact rank',state['exact_rank'] if state['exact_rank'] is not None else '—')
    if state['conditional_analytic_upper'] is not None:
        st.caption(f"GRH/conditional analytic upper: {state['conditional_analytic_upper']} — not a rigorous algebraic upper bound.")

    size_limited=db.execute(
        """SELECT 1 FROM rank_evidence
           WHERE curve_id=? AND engine='mwrank'
             AND (
               stderr_summary LIKE '%to long fails%'
               OR stderr_summary LIKE '%too large%'
             )
           LIMIT 1""",
        (int(row['id']),),
    ).fetchone()
    if size_limited is not None:
        st.warning(
            "Large-curve mode: a previous mwrank/eclib descent hit its machine-integer size limit. "
            "That engine/model route is inconclusive, not a negative rank result. PARI bounds, exact point search, "
            "independence certification, and standalone saturation remain available."
        )

    st.subheader('Rank bounds')
    a,b,c,d=st.columns(4)
    engine=a.selectbox('Engine',['auto','pari','mwrank'],format_func=lambda x:{'auto':'Auto · PARI first','pari':'PARI only','mwrank':'mwrank only'}[x])
    pari_timeout=int(b.number_input('PARI timeout',min_value=10,value=int(setting(db,'pari_rank_timeout',300)),step=30))
    mwrank_timeout=int(c.number_input('mwrank timeout',min_value=10,value=int(setting(db,'mwrank_rank_timeout',300)),step=30))
    pari_stack_gib=int(d.number_input('PARI stack cap GiB',min_value=2,max_value=16,value=4,step=2,help='Used only after a genuine PARI stack overflow. Higher values can consume substantial RAM.'))
    escalate=st.checkbox('Escalate a completed PARI gap to mwrank',value=False,help='Off by default so mwrank does not gate normal rank-bound evaluation.')
    if st.button('Run rank bounds',type='primary',width='stretch'):
        py=setting(db,'science_python',ctx.detected_science_python())
        cmd=[py,'-m','rank42.cli','rank-bounds','--db',ctx.db_path,'--curve-id',int(row['id']),'--engine',engine,'--timeout',pari_timeout,'--mwrank-timeout',mwrank_timeout,'--pari-stack-max-gib',pari_stack_gib]
        if escalate: cmd.append('--escalate')
        jid=launch(ctx,db,kind='rank_bounds',label=f'Rank bounds curve #{row["id"]}',command=cmd,metadata={'curve_id':int(row['id']),'engine':engine})
        st.success(f'Started rank-bounds job #{jid}.')
    _evidence_table(db,int(row['id']))

    st.divider()
    st.subheader('Deeper mwrank descent')
    a,b,c,d=st.columns(4)
    timeout=int(a.number_input('Strong timeout',min_value=10,value=int(setting(db,'deep_cert_timeout',900)),step=60))
    first=int(b.number_input('First limit',min_value=1,value=40))
    second=int(c.number_input('Second limit',min_value=1,value=20))
    precision=int(d.number_input('Precision',min_value=64,value=512,step=64))
    if st.button('Run strong mwrank descent',width='stretch'):
        py=setting(db,'science_python',ctx.detected_science_python())
        cmd=[py,'-m','rank42.attack','--db',ctx.db_path,'--id',int(row['id']),'--timeout',timeout,'--first-limit',first,'--second-limit',second,'--precision',precision]
        jid=launch(ctx,db,kind='descent',label=f'Descent curve #{row["id"]}',command=cmd,metadata={'curve_id':int(row['id'])})
        st.success(f'Started descent job #{jid}.')

    st.divider()
    st.subheader('Standalone saturation')
    witness_count=int(db.execute(
        'SELECT COUNT(*) AS n FROM points WHERE curve_id=? AND exact_verified=1 AND rigorous_independent=1',
        (int(row['id']),),
    ).fetchone()['n'] or 0)
    st.caption(
        f"Rigorous point-ledger witnesses: {witness_count}. "
        "This saturates the existing rigorous subgroup without requiring full mwrank 2-descent. "
        "A nontrivial saturation index changes the subgroup basis/index, not the rank."
    )
    if witness_count < int(state['rigorous_lower'] or 0):
        st.info(
            "The rigorous point ledger does not yet contain a complete witness basis for the stored lower bound; "
            "standalone saturation will report inconclusive until that basis is available."
        )
    a,b=st.columns(2)
    sat_prime=int(a.number_input('Saturate through prime',min_value=2,value=100,step=10))
    sat_timeout=int(b.number_input('Saturation timeout',min_value=10,value=int(setting(db,'deep_cert_timeout',900)),step=60))
    if st.button('Saturate rigorous basis',width='stretch'):
        py=setting(db,'science_python',ctx.detected_science_python())
        cmd=[
            py,'-m','rank42.cli','saturate','--db',ctx.db_path,
            '--curve-id',int(row['id']),'--max-prime',sat_prime,'--timeout',sat_timeout,
        ]
        jid=launch(
            ctx,db,kind='saturation',label=f'Saturation curve #{row["id"]}',
            command=cmd,metadata={'curve_id':int(row['id']),'max_prime':sat_prime},
        )
        st.success(f'Started saturation job #{jid}.')

    st.divider()
    cert_timeout=int(st.number_input('Proof-mode exact-rank timeout',min_value=1,value=30,step=10))
    if st.button('Attempt exact-rank certification',width='stretch'):
        py=setting(db,'science_python',ctx.detected_science_python())
        jid=launch(ctx,db,kind='rank_cert',label=f'Exact-rank curve #{row["id"]}',command=[py,'-m','rank42.certify_curve','--db',ctx.db_path,'--id',int(row['id']),'--timeout',cert_timeout],metadata={'curve_id':int(row['id'])})
        st.success(f'Started exact-rank job #{jid}.')
