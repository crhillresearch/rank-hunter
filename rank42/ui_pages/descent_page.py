from __future__ import annotations
import json
import streamlit as st
from rank42.feature_hooks import render_feature_hook
from rank42.ui_active_curve import get_active_curve_id
from rank42.rank_evidence import list_rank_evidence, reduce_rank_state
from rank42.analyze_workspace import curve_analysis_snapshot
from rank42.analysis_planner import analysis_plan
from .common import active_curve_selector,title,curve_options,launch,setting


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
    title('Descent','Close the rigorous rank interval using the shared Analysis Planner and bounded exact proof methods.','Analysis')
    render_feature_hook(db,ctx,'analysis.descent.after_header')
    rows=curve_options(db)
    if not rows: st.info('No curves with a proven lower bound yet.'); return
    wanted=get_active_curve_id(st.session_state,'descent_curve_id','analyze_curve_id')
    row=active_curve_selector(
        db,
        rows,
        wanted=wanted,
        state_prefix='descent',
        widget_prefix='descent-curve',
        compatibility_keys=('descent_curve_id',),
    )
    if row is None: return
    state=reduce_rank_state(db,int(row['id']))
    snapshot=curve_analysis_snapshot(db,int(row['id']))
    plan=analysis_plan(snapshot)
    proof_action=next((a for a in plan['actions'] if a.get('kind')=='descent'),None)

    with st.container(border=True):
        st.markdown('**Research goal: determine the strongest rigorous rank statement justified by current evidence.**')
        if state['exact_rank'] is not None:
            st.success(f"Rank is already exact: {state['exact_rank']}. No additional upper-bound attack is required.")
        elif proof_action is not None:
            st.markdown(f"**Recommended next step:** {proof_action['title']}")
            st.caption(proof_action['why'])
            st.caption(f"Method guidance: {proof_action['recommended_method']}")
            prior=proof_action.get('prior_attempt_state') or {}
            history=prior.get('method_history') or {}
            if history.get('all_exhausted_at_budget'):
                st.warning('Tracked upper-bound methods are exhausted at the current planning budgets. Review history or deliberately raise a budget before retrying.')
            elif history.get('stronger_budget_available'):
                st.info('A stronger budget remains available for at least one previously attempted upper-bound method.')
        else:
            st.info('No rank-proof action is currently recommended ahead of higher-priority Analysis work.')

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
            "independence certification, and the dedicated Saturation workbench remain available."
        )

    st.subheader('Rigorous rank-bound methods')
    # SYSTEM-16: Settings supplies editable starting budgets only. These
    # per-action controls own the timeout passed to the launched command.
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
    st.subheader('Advanced mwrank descent')
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
    with st.container(border=True):
        st.subheader('Subgroup saturation')
        st.caption(
            'Subgroup primitivity, index recovery, regulator inspection, and '
            'prime-by-prime saturation now live in the dedicated Saturation workbench.'
        )
        if st.button(
            'Open Saturation',
            width='stretch',
            key=f'descent-open-saturation-{int(row["id"])}',
            icon=':material/hub:',
        ):
            st.session_state['saturation_curve_id']=int(row['id'])
            st.session_state['rh_page']='Saturation'
            st.rerun()

    st.divider()
    cert_timeout=int(st.number_input('Proof-mode exact-rank timeout',min_value=1,value=30,step=10))
    if st.button('Attempt exact-rank certification',width='stretch'):
        py=setting(db,'science_python',ctx.detected_science_python())
        jid=launch(ctx,db,kind='rank_cert',label=f'Exact-rank curve #{row["id"]}',command=[py,'-m','rank42.certify_curve','--db',ctx.db_path,'--id',int(row['id']),'--timeout',cert_timeout],metadata={'curve_id':int(row['id'])})
        st.success(f'Started exact-rank job #{jid}.')
