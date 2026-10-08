from __future__ import annotations
import json
import streamlit as st
from rank42 import __version__
from rank42.icarm_api import default_commentary,masked_token,read_token,token_source
from rank42.submission import icarm_submission_payload,stored_bad_primes
from .common import title,curve_options,curve_label,launch,setting,select_row

def page(db,ctx):
    title('ICARM Submit','Prepare complete witness data, calculate missing bad primes, and explicitly submit rigorous discoveries.','Resources')
    rows=curve_options(db)
    eligible=[r for r in rows if int(r['descent_lower'] or 0)>0 and r['a_invariants_json']]
    if not eligible: st.info('No curves have an explicit witness-backed rigorous lower bound yet.'); return
    wanted=st.session_state.get('icarm_curve_id'); idx=next((i for i,r in enumerate(eligible) if wanted and int(r['id'])==int(wanted)),0)
    row=select_row('Curve',eligible,index=idx,format_func=curve_label)
    bad=stored_bad_primes(row)
    c1,c2,c3=st.columns(3); c1.metric('Witness lower',f"≥ {int(row['descent_lower'] or 0)}"); c2.metric('Stored witness points',len(json.loads(row['generators_json'] or '[]'))); c3.metric('Bad primes','ready' if bad is not None else 'missing')
    commentary=st.text_area('Commentary',value=default_commentary(row,version=__version__),height=120)
    py=setting(db,'science_python',ctx.detected_science_python())
    if bad is None:
        if st.button('Calculate bad primes / prepare packet',type='primary',width='stretch'):
            cmd=[py,'-m','rank42.icarm_api','--db',ctx.db_path,'--id',int(row['id']),'--project-root',ctx.project_root,'--science-python',py,'--commentary',commentary]
            jid=launch(ctx,db,kind='icarm_prepare',label=f'Prepare ICARM curve #{row["id"]}',command=cmd,metadata={'curve_id':int(row['id'])}); st.success(f'Started metadata/preparation job #{jid}.')
        return
    try:
        payload=icarm_submission_payload(row); st.subheader('Submission packet'); st.json(payload)
    except Exception as exc: st.error(str(exc)); return
    configured=bool(read_token(ctx.project_root)); st.caption(f"ICARM API token: {'configured via '+str(token_source(ctx.project_root)) if configured else 'not configured'}")
    confirm=st.checkbox('I confirm this external submission uses the displayed rigorous witness data.')
    if st.button('Submit to ICARM',type='primary',width='stretch',disabled=not(configured and confirm)):
        cmd=[py,'-m','rank42.icarm_api','--db',ctx.db_path,'--id',int(row['id']),'--project-root',ctx.project_root,'--science-python',py,'--commentary',commentary,'--submit','--yes']
        jid=launch(ctx,db,kind='icarm_submit',label=f'Submit ICARM curve #{row["id"]}',command=cmd,metadata={'curve_id':int(row['id'])}); st.success(f'Started explicit ICARM submission job #{jid}.')
