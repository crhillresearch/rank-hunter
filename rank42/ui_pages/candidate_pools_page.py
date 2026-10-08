from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import sqlite3

import streamlit as st

from rank42.candidate_ranking_state import pool_candidate_ranking_state
from rank42.candidates import (
    candidate_outcome,
    candidate_rows,
    delete_pool,
    export_pool_jsonl,
    import_jsonl,
    list_pools,
    pool_resume_offset,
    reconcile_pool,
)
from rank42.feature_hooks import render_feature_hook
from rank42.plugins import (
    Plugin,
    discover_plugins,
    get_plugin,
    get_variant,
    plugin_fingerprints,
    variant_for_family_spec,
)
from rank42.ui_components import selectable_dataframe

from .common import section_title, select_row, title


def _reconcile_pool_for_ui(db, pool_id):
    """Manual pool maintenance that fails fast instead of blocking the page."""
    try:
        old_timeout = int(db.execute("PRAGMA busy_timeout").fetchone()[0] or 30000)
    except Exception:
        old_timeout = 30000
    try:
        db.execute("PRAGMA busy_timeout=250")
        reconcile_pool(db, pool_id)
        return True
    except sqlite3.OperationalError as exc:
        try:
            db.rollback()
        except Exception:
            pass
        message = str(exc).lower()
        if "locked" in message or "busy" in message:
            return False
        raise
    finally:
        try:
            db.execute(f"PRAGMA busy_timeout={max(0, old_timeout)}")
        except Exception:
            pass

def _candidate_rank_view(row):
    if bool(row.get("rank_inconsistent")):
        lower = int(row.get("rigorous_lower") or 0)
        upper = row.get("rigorous_upper")
        return f"conflict ≥{lower}" + (f" / ≤{int(upper)}" if upper is not None else "")
    if row.get("exact_rank") is not None:
        return f"= {int(row['exact_rank'])}"
    if row.get("rigorous_lower") is not None:
        return f"≥ {int(row['rigorous_lower'])}"
    return "—"


def _candidate_ranking_view(row):
    ranking = pool_candidate_ranking_state(row)
    return ranking["ranking_label"], ranking["ranking_value"]


def _candidate_corpus_view(row):
    try:
        meta=json.loads(row['metadata_json'] or '{}')
    except Exception:
        meta={}
    summary=meta.get('corpus_summary')
    if not isinstance(summary,dict):
        return '—','—'
    if not summary.get('known'):
        return 'novel','—'
    corpora=', '.join(str(x) for x in (summary.get('corpora') or [])) or 'known'
    exact=summary.get('exact_rank')
    lower=summary.get('rank_lower')
    upper=summary.get('rank_upper')
    if exact is not None:
        rank=f'exact {int(exact)}'
    elif lower is not None and upper is not None:
        rank=f'[{int(lower)},{int(upper)}]'
    elif lower is not None:
        rank=f'≥ {int(lower)}'
    elif upper is not None:
        rank=f'≤ {int(upper)}'
    else:
        rank='known'
    if summary.get('conflict'):
        rank += ' ⚠'
    return corpora,rank


def _render_import_export(db, ctx):
    left,right=st.columns(2)
    with left:
        with st.container(border=True):
            section_title('Export pool','Reproducible JSONL interchange for a stored SQLite pool.')
            pools=list_pools(db,limit=200)
            if pools:
                pool=select_row('Export pool',pools,format_func=lambda p:f"#{p['id']} · {p['name']}",key='cand-export')
                path=ctx.project_root/'.rank42-ui'/'exports'/f"pool-{pool['id']}-{pool['name']}.jsonl"
                export_state_key=f"candidate-export-path-{int(pool['id'])}"
                if st.button(
                    'Prepare JSONL export',
                    width='stretch',
                    key=f"candidate-export-prepare-{int(pool['id'])}",
                ):
                    export_pool_jsonl(db,pool['id'],path)
                    st.session_state[export_state_key]=str(path)
                prepared=st.session_state.get(export_state_key)
                prepared_path=Path(prepared) if prepared else None
                if prepared_path is not None and prepared_path.is_file():
                    st.download_button(
                        'Download JSONL',
                        prepared_path.read_bytes(),
                        file_name=prepared_path.name,
                        mime='application/x-ndjson',
                        width='stretch',
                    )
            else:
                st.info('Nothing to export yet.')
    with right:
        with st.container(border=True):
            section_title('Import JSONL','Attach an external candidate list to a specific plugin and exact family variant.')
            plugins=[p for p in discover_plugins(ctx.project_root) if isinstance(p,Plugin) and p.plugin_type=='family']
            if plugins:
                imported_plugin=st.selectbox('Plugin for imported pool',plugins,format_func=lambda p:f"{p.name} ({p.id})",key='cand-import-plugin')
                imported_variant=get_variant(imported_plugin)
                if len(imported_plugin.variants)>1:
                    imported_variant=st.selectbox('Family variant for imported pool',imported_plugin.variants,format_func=lambda v:f"{v.name} ({v.id})",key='cand-import-variant')
                upload=st.file_uploader('Candidate JSONL',type=['jsonl','ndjson','json'],key='cand-import-file')
                imported_name=st.text_input('Imported pool name',value=f"imported-{datetime.now().strftime('%Y%m%d-%H%M')}")
                if upload and st.button('Import candidate pool',width='stretch',type='primary'):
                    temp=ctx.project_root/'.rank42-ui'/'imports'/upload.name
                    temp.parent.mkdir(parents=True,exist_ok=True)
                    temp.write_bytes(upload.getvalue())
                    spec=imported_variant.family_spec
                    fp=plugin_fingerprints(imported_plugin,imported_variant)
                    pool=import_jsonl(db,temp,name=imported_name,plugin_id=imported_plugin.id,plugin_version=imported_plugin.version,family_spec=spec,generation={'imported_filename':upload.name,'variant_id':imported_variant.id,'variant_name':imported_variant.name},**fp)
                    st.success(f"Imported pool #{pool['id']} with {pool['candidate_count']} candidates.")
            st.caption('JSONL is interchange/export. SQLite candidate pools remain authoritative scientific state.')


def page(db, ctx, *, embedded=False):
    if not embedded:
        title(
            "Candidate Pools",
            "Inspect, maintain, import, and export durable candidate pools.",
            icon="filter_alt",
        )
    render_feature_hook(db, ctx, "candidates.after_header")

    current = str(st.session_state.get("candidate_pools_view") or "Browse")
    if current not in {"Browse", "Import & Export"}:
        current = "Browse"

    if current == "Import & Export":
        if st.button(
            "Back to Pools",
            icon=":material/arrow_back:",
            key="candidate-pools-export-back",
            width="content",
        ):
            st.session_state["candidate_pools_view"] = "Browse"
            st.rerun()
        st.html("<div style='height:.85rem'></div>")
        _render_import_export(db, ctx)
        return

    st.session_state["candidate_pools_view"] = "Browse"
    pools=list_pools(db,limit=200)

    title_col, transfer_col = st.columns(
        [5.4, 1.2],
        vertical_alignment="top",
    )
    with title_col:
        section_title(
            'Stored pools',
            'SQLite is authoritative. Search completion advances only after durable candidate writes.',
        )
    with transfer_col:
        if st.button(
            "Import/Export",
            icon=":material/file_export:",
            key="candidate-pools-export-open",
            width="stretch",
        ):
            st.session_state["candidate_pools_view"] = "Import & Export"
            st.rerun()
    st.html("<div style='height:.35rem'></div>")

    if not pools:
        st.info('No candidate pools yet.')
    else:
        selected=selectable_dataframe(
            [{
                'id':p['id'],'name':p['name'],'plugin':p['plugin_id'],
                'variant':(json.loads(p['generation_json'] or '{}').get('variant_id') or '—'),
                'chart':(json.loads(p['generation_json'] or '{}').get('chart_id') or 'native'),
                'count':p['candidate_count'],'status':p['status'],'created':p['created_at'][:19]
            } for p in pools],
            pools,
            semantic='candidate-pools-table',
            selected_id=st.session_state.get('candidates_selected_pool_id'),
            selection_state_key='candidates_selected_pool_id',
        )
        resume=pool_resume_offset(db,selected['id'])
        m1,m2,m3=st.columns(3)
        m1.metric('Candidates',int(selected['candidate_count']))
        m2.metric('Resume offset',int(resume))
        m3.metric('Remaining',max(0,int(selected['candidate_count'])-int(resume)))

        preview_limit = st.selectbox(
            "Preview rows",
            [100, 250, 500, 1000],
            index=1,
            key=f"candidate-preview-limit-{int(selected['id'])}",
            help="Only the preview is limited; the stored pool is unchanged.",
        )
        rows=candidate_rows(
            db,
            selected['id'],
            limit=int(preview_limit),
        )

        with st.expander('Pool maintenance', expanded=False):
            st.caption(
                'Reconcile legacy candidate-to-curve links only when needed. '
                'This can scan the entire pool and is never run during page load.'
            )
            if st.button(
                'Reconcile pool state',
                key=f"candidate-reconcile-{int(selected['id'])}",
                width='stretch',
                icon=':material/sync:',
            ):
                with st.spinner('Reconciling candidate-to-curve links…'):
                    if _reconcile_pool_for_ui(db, selected['id']):
                        st.success('Pool state reconciled.')
                    else:
                        st.warning(
                            'Pool reconciliation deferred because the database is busy.'
                        )
        picked=selectable_dataframe(
            [{
                '#':r['rank_order'],'parameter':r['parameter'],
                'ranking':_candidate_ranking_view(r)[0],'ranking value':_candidate_ranking_view(r)[1],
                'prime bound':r['prime_bound'],
                'state':r['status'],'outcome':candidate_outcome(r),'curve id':r['curve_id'],'curve status':r['curve_status'],
                'rigorous rank':_candidate_rank_view(r),
                'corpus':_candidate_corpus_view(r)[0],
                'corpus rank':_candidate_corpus_view(r)[1],
            } for r in rows],
            rows,
            semantic=f"candidate-table-{selected['id']}",
            selected_id=st.session_state.get(f"candidate_selected_id_{selected['id']}"),
            selection_state_key=f"candidate_selected_id_{selected['id']}",
        )

        # Reproducibility fingerprint audit for this historical pool.
        try:
            installed=get_plugin(ctx.project_root,selected['plugin_id'])
        except Exception:
            installed=None
        if installed and installed.plugin_type=='family':
            historical_variant=variant_for_family_spec(installed,selected['family_spec']) or get_variant(installed)
            current_fp=plugin_fingerprints(installed,historical_variant)
            drift=[]
            if str(selected['plugin_version'] or '') != str(installed.version or ''):
                drift.append(f"version {selected['plugin_version'] or '—'} → {installed.version}")
            for col,label in [('family_sha256','family'),('adapter_sha256','adapter'),('plugin_manifest_sha256','manifest')]:
                old=selected[col] if col in selected.keys() else None; new=current_fp.get(col)
                if old and new and old!=new: drift.append(f'{label} SHA changed')
            if drift:
                st.warning('Pool reproducibility drift: '+ ' · '.join(drift))
            elif selected['family_sha256'] if 'family_sha256' in selected.keys() else None:
                st.success('Pool fingerprints match the currently installed plugin files.')
            else:
                st.info('Legacy pool: content hashes were not stored when this pool was generated.')
        with st.expander('Pool reproducibility'):
            st.code(f"plugin {selected['plugin_id']} @ {selected['plugin_version'] or '—'}\nfamily {str(selected['family_sha256'] or 'legacy')[:20]}\nadapter {str(selected['adapter_sha256'] or 'legacy')[:20]}\nmanifest {str(selected['plugin_manifest_sha256'] or 'legacy')[:20]}",language='text')
            st.code(str(selected['family_spec']),language='text')

        if rows and picked is not None:
            try: pmeta=json.loads(picked['metadata_json'] or '{}')
            except Exception: pmeta={}
            last=pmeta.get('last_search') if isinstance(pmeta.get('last_search'),dict) else {}
            st.markdown(f"**Outcome:** {candidate_outcome(picked)}")
            q1,q2,q3,q4=st.columns(4)
            ranking_label, ranking_value = _candidate_ranking_view(picked)
            shown_ranking = '—' if ranking_value is None else f"{float(ranking_value):.4f}"
            q1.metric(ranking_label, shown_ranking)
            q2.metric('Historical quick upper',last.get('quick_upper') if last.get('quick_upper') is not None else '—')
            q3.metric('Historical descent upper',last.get('descent_upper') if last.get('descent_upper') is not None else '—')
            q4.metric('Live rigorous rank',_candidate_rank_view(picked))
            b1,b2=st.columns(2)
            if b1.button('Open curve',key=f"candidate-open-curve-{picked['id']}",disabled=not bool(picked['curve_id']),width='stretch'):
                st.session_state['curves_selected_id']=int(picked['curve_id']); st.session_state['rh_page']='Curves'; st.rerun()
            if b2.button('Open job result',key=f"candidate-open-job-{picked['id']}",disabled=not bool(last.get('job_id')),width='stretch'):
                st.session_state['results_selected_job_id']=int(last['job_id']); st.session_state['rh_page']='Results'; st.rerun()
            with st.expander('Full candidate/search metadata'):
                st.json(pmeta)

        if st.button('Open in Search',width='stretch',type='primary'):
            st.session_state['family_search_pool_id']=int(selected['id']); st.session_state['search_tab']='Family'; st.session_state['rh_page']='Search'; st.rerun()

        with st.expander('Delete candidate pool'):
            st.caption('Deletes this queue and its candidate rows only. Curves, points, lattices, and other scientific results are preserved.')
            active=[]
            for job in db.execute("SELECT id,label,status,metadata_json FROM ui_jobs WHERE status IN ('queued','running','stopping') ORDER BY id DESC").fetchall():
                try: meta=json.loads(job['metadata_json'] or '{}')
                except Exception: meta={}
                if int(meta.get('pool_id') or -1)==int(selected['id']): active.append(job)
            if active:
                st.warning('This pool has active work. Stop or let the job finish before deleting the pool.')
            confirm=st.checkbox(f"I understand this will delete pool #{int(selected['id'])} and {int(selected['candidate_count'])} candidate queue rows.",key=f"delete-pool-confirm-{int(selected['id'])}")
            if st.button('Delete pool',type='secondary',width='stretch',disabled=(not confirm or bool(active)),key=f"delete-pool-{int(selected['id'])}"):
                if delete_pool(db,int(selected['id'])):
                    if int(st.session_state.get('family_search_pool_id') or -1)==int(selected['id']):
                        st.session_state.pop('family_search_pool_id',None)
                    st.success(f"Deleted candidate pool #{int(selected['id'])}. Scientific curve/result rows were not deleted.")
                    st.rerun()
