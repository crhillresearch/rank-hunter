from __future__ import annotations

from rank42.curve_research_state import list_curve_research_states
from rank42.db import connect_existing
from rank42.feature_hooks import render_feature_hook
import json
from datetime import datetime
from pathlib import Path

import streamlit as st

from rank42.ui_log_viewer import render_raw_log
from rank42.ui_findings import hunt_findings
from rank42.ui_results import pipeline_retention_quartic_summary, summarize_job
from rank42.ui_store import get_job, list_jobs, read_log, reconcile_jobs
from .common import title
from rank42.ui_components import metric_card, selectbox, toggle, selectable_dataframe, button, region

SEARCH_SIGNAL_KEYS={
    'candidates_completed','candidates_planned','trials_completed','trials_planned',
    'shortlist_searched','shortlist_planned','new_native_fibers','mapped_extra_points',
    'best_screened_rank','best_rigorous_lower','rigorous_hits','rank_growth_curves',
    'discovered_anchor_fibres','quartic_stages_checked',
}


def _is_search_summary(summary):
    """Classify by scientific result shape, not family/plugin-specific job names."""
    return any(key in summary for key in SEARCH_SIGNAL_KEYS)


ACTIVE={'queued','running','stopping'}



def _best_rigorous_display(db):
    states=list_curve_research_states(db)
    for state in states:
        if state["rank_inconsistent"]:
            continue
        value=int(state["rigorous_lower"] or 0)
        if value>0:
            return f'≥ {value}'
    return '—'
def _duration(row):
    start=row['started_at'] or row['created_at']
    end=row['finished_at']
    if not start:
        return None
    try:
        a=datetime.fromisoformat(str(start).replace('Z','+00:00'))
        b=datetime.fromisoformat(str(end).replace('Z','+00:00')) if end else datetime.now(a.tzinfo)
        return max(0,int((b-a).total_seconds()))
    except Exception:
        return None


def _job_log_signature(row):
    path = Path(str(row["log_path"]))
    try:
        stat = path.stat()
        file_sig = (int(stat.st_size), int(stat.st_mtime_ns))
    except OSError:
        file_sig = (0, 0)
    return (
        str(row["status"]),
        str(row["updated_at"] or ""),
        *file_sig,
    )


def _summary_for_job(row):
    cache = st.session_state.setdefault("_results_summary_cache", {})
    if len(cache) > 1000:
        cache.clear()

    key = str(int(row["id"]))
    signature = _job_log_signature(row)
    cached = cache.get(key)
    if isinstance(cached, dict) and cached.get("signature") == signature:
        return cached.get("summary") or {}

    log = read_log(row["log_path"])
    summary = summarize_job(row, log)
    cache[key] = {
        "signature": signature,
        "summary": summary,
    }
    return summary


def _pairs(db, limit=100, *, wanted_id=None):
    reconcile_jobs(db)
    rows = list(list_jobs(db, limit=limit))
    if wanted_id is not None and all(int(row["id"]) != int(wanted_id) for row in rows):
        wanted = get_job(db, int(wanted_id))
        if wanted is not None:
            rows.append(wanted)
    return [(row, _summary_for_job(row)) for row in rows]


def _scoreboard(db,pairs):
    search=[(r,s) for r,s in pairs if _is_search_summary(s)]
    completed=sum(int(s.get('candidates_completed') or s.get('trials_completed') or s.get('shortlist_searched') or 0) for _,s in search)
    native=sum(int(s.get('new_native_fibers') or 0) for _,s in search)
    mapped=sum(int(s.get('mapped_extra_points') or 0) for _,s in search)
    growth=sum(int(s.get('rank_growth_curves') or s.get('rigorous_hits') or 0) for _,s in search)
    best_screen=max([0,*[int(s.get('best_screened_rank') or 0) for _,s in search]])
    c1,c2,c3,c4,c5,c6=st.columns(6)
    with c1: metric_card('Search runs',len(search),key='results-runs')
    with c2: metric_card('Candidates completed',f'{completed:,}',key='results-completed')
    with c3: metric_card('Native hits',f'{native:,}',key='results-native')
    with c4: metric_card('Mapped extras',f'{mapped:,}',key='results-mapped')
    with c5: metric_card('Rank-growth hits',f'{growth:,}',key='results-growth')
    with c6: metric_card('Best rigorous rank',_best_rigorous_display(db),key='results-best')
    st.caption(f'Best numerical/screened basis seen in parsed search logs: {best_screen}. This is not a proof claim.')




def _fmt_stat(value, *, lower=False):
    if value is None:
        return '—'
    if isinstance(value,float):
        return f'{value:.4f}'
    if lower:
        return f'≥ {int(value)}'
    return f'{int(value):,}' if isinstance(value,int) else str(value)


def _dynamic_stats(row,s):
    """Job-kind-aware scientific status surface; screens remain labelled as screens."""
    kind=str(row['kind'])
    cards=[]
    if kind in {'candidate_generate','candidates','nagao_generate'} or s.get('candidates_written') is not None:
        cards=[('Parameters screened',s.get('parameters_screened')),('Candidates ready',s.get('candidates_written')),('Best Nagao',s.get('best_nagao_score')),('Stage survivors',(s.get('stage_survivors') or ['—'])[-1])]
    elif kind == 'independence':
        cards=[
            ('Selected',s.get('selected')),
            ('New independent',s.get('new_independent')),
            ('Dependent',s.get('exact_dependent')),
            ('Inconclusive',s.get('exact_inconclusive')),
            ('Rigorous lower',f"≥ {int(s.get('best_rigorous_lower') or 0)}" if s.get('best_rigorous_lower') is not None else '—'),
        ]
    elif _is_search_summary(s):
        completed=s.get('candidates_completed',s.get('trials_completed',s.get('shortlist_searched')))
        planned=s.get('candidates_planned',s.get('trials_planned',s.get('shortlist_planned')))
        progress=f'{int(completed or 0)}/{int(planned)}' if planned else _fmt_stat(completed)
        current=s.get('current_parameter') or s.get('current_trial') or '—'
        native=s.get('new_native_fibers',s.get('quartic_stages_checked'))
        cards=[('Completed',progress),('Current',current),('Native stage / hits',native),('Best screened',s.get('best_screened_rank'))]
        if s.get('mapped_extra_points') is not None: cards.append(('Mapped extras',s.get('mapped_extra_points')))
        if s.get('best_rigorous_lower') is not None: cards.append(('Rigorous lower',f"≥ {int(s.get('best_rigorous_lower') or 0)}"))
        if s.get('rigorous_hits') is not None or s.get('rank_growth_curves') is not None: cards.append(('Rank growth',s.get('rigorous_hits',s.get('rank_growth_curves'))))
    else:
        preferred=[('Best rigorous',s.get('best_rigorous_lower')),('Exact rank',s.get('exact_rank')),('Points',s.get('rational_points')),('Screened rank',s.get('screened_rank'))]
        cards=[x for x in preferred if x[1] is not None]
    if not cards:
        return
    cols=st.columns(min(4,len(cards)))
    for i,(label,value) in enumerate(cards):
        cols[i%len(cols)].metric(label,_fmt_stat(value) if not isinstance(value,str) else value)
def _display_result(row,s):
    if row['status'] in {'stopped','killed','interrupted'}:
        return str(row['status']).upper()
    return s.get('result_status') or '—'


def _table_row(row,s):
    planned=s.get('candidates_planned') or s.get('trials_planned') or s.get('shortlist_planned')
    completed=s.get('candidates_completed')
    if completed is None: completed=s.get('trials_completed')
    if completed is None: completed=s.get('shortlist_searched')
    progress=(f'{int(completed or 0)}/{int(planned)}' if planned else (str(int(completed)) if completed is not None else '—'))
    return {
        'job':int(row['id']),
        'result':_display_result(row,s),
        'process':row['status'],
        'kind':row['kind'],
        'label':row['label'],
        'progress':progress,
        'best proven':s.get('best_rigorous_lower'),
        'best screened':s.get('best_screened_rank'),
        'mapped extras':s.get('mapped_extra_points'),
        'native hits':s.get('new_native_fibers'),
        'rigorous hits':s.get('rigorous_hits'),
        'runtime s':_duration(row),
        'pid':row['pid'],
        'created':row['created_at'],
        'finished':row['finished_at'],
    }


def _detail(db,row,s,science_python=None):
    st.markdown(f"### Job #{row['id']} · {row['label']}")
    result=_display_result(row,s)
    st.html(f'<div class="rh-result-outcome rh-result-{str(result).lower().replace(" ","-")}"><strong>{result}</strong></div>')
    _dynamic_stats(row,s)
    c1,c2,c3=st.columns(3)
    c1.metric('Process',row['status'])
    c2.metric('PID',row['pid'] or '—')
    c3.metric('Exit',row['exit_code'] if row['exit_code'] is not None else '—')

    planned=s.get('candidates_planned') or s.get('trials_planned') or s.get('shortlist_planned')
    completed=s.get('candidates_completed')
    if completed is None: completed=s.get('trials_completed')
    if completed is None: completed=s.get('shortlist_searched')
    if planned:
        st.progress(min(1.0,float(completed or 0)/max(1,float(planned))),text=f'Candidates completed: {int(completed or 0)}/{int(planned)}')

    render_job_scientific_result(
        db,
        row,
        s,
        science_python=science_python,
        show_dynamic_stats=False,
    )


def _database_path(db):
    """Return the main SQLite path without leaking the live connection."""
    rows = db.execute("PRAGMA database_list").fetchall()
    for row in rows:
        try:
            name = str(row["name"])
            path = str(row["file"])
        except (TypeError, KeyError, IndexError):
            name = str(row[1])
            path = str(row[2])
        if name == "main":
            return path
    return ""


@st.fragment(run_every="1s")
def _render_live_raw_log_fragment(db_path, job_id):
    """Refresh one active Raw log with a thread-local SQLite connection."""
    fresh_db = connect_existing(db_path)
    try:
        fresh = get_job(fresh_db, int(job_id))
        if fresh is None:
            st.warning(f"Job #{int(job_id)} is no longer available.")
            return
        render_raw_log(read_log(fresh["log_path"]))
        terminal = str(fresh["status"]) not in ACTIVE
    finally:
        fresh_db.close()

    if terminal:
        # Switch the whole detail view to the terminal/static path once.
        st.rerun()


def _render_pipeline_retention_notice(db, row):
    summary = pipeline_retention_quartic_summary(db, row)
    if not summary or int(summary.get("quartic_hits") or 0) <= 0:
        return

    hits = int(summary["quartic_hits"])
    floor = int(summary["retention_floor"])
    candidates = list(summary.get("candidates") or [])
    candidate_count = len(candidates)
    hit_word = "hit" if hits == 1 else "hits"
    curve_word = "curve" if candidate_count == 1 else "curves"

    st.info(
        f"**{hits:,} persisted quartic {hit_word} · "
        f"{candidate_count:,} {curve_word} not retained "
        f"(retention floor ≥{floor})**\n\n"
        "These searches are still stored in Rank Hunter, but the candidate "
        "curve rows were intentionally removed because their rigorous lower "
        "bounds stayed below this Pipeline's retention floor. The curve-scoped "
        "Quartics page therefore will not list these hits."
    )
    st.dataframe(
        [
            {
                "parameter": rec["parameter"],
                "quartic hits": int(rec["quartic_hits"]),
                "quartic searches": int(rec["quartic_searches"]),
                "rigorous lower at retention": int(rec["rigorous_lower"]),
                "retention": f"not kept (< {floor})",
            }
            for rec in candidates
        ],
        hide_index=True,
        width="stretch",
        height=min(300, max(110, 42 + 35 * min(candidate_count, 6))),
    )


def _render_job_raw_log(db, row):
    """Always render Raw log; active Jobs refresh in an isolated fragment."""
    st.markdown("#### Raw log")
    if str(row["status"]) in ACTIVE:
        db_path = _database_path(db)
        if db_path:
            st.caption("Live · refreshing every second")
            _render_live_raw_log_fragment(db_path, int(row["id"]))
            return
        st.caption("Live refresh unavailable for this in-memory database")
    render_raw_log(read_log(row["log_path"]))


def render_job_scientific_result(
    db,
    row,
    summary,
    *,
    science_python=None,
    show_dynamic_stats=True,
):
    """Shared scientific interpretation for one durable Job."""
    if show_dynamic_stats:
        _dynamic_stats(row, summary)

    _render_pipeline_retention_notice(db, row)
    _render_job_raw_log(db, row)

    _hunt_recap(db, row, summary, science_python)


def _hunt_recap(db, row, summary, science_python):
    if not _is_search_summary(summary):
        return
    findings = hunt_findings(db, row, read_log(row['log_path']))
    if not findings:
        return

    with region(f'results-hunt-recap-{row["id"]}', border=True):
        st.subheader('Hunt recap')
        st.caption(
            'This job only · no campaign required. '
            'The table is a compact recap; use the candidate picker below to reopen a stored curve.'
        )

        metrics = [
            ('Candidates in run', f'{len(findings):,}'),
            (
                'Curves created during run',
                f'{sum(bool(f["new_curve"]) for f in findings):,}',
            ),
        ]
        if summary.get('new_native_fibers') is not None:
            metrics.append(
                ('New native x-fibers', _fmt_stat(summary.get('new_native_fibers')))
            )
        if summary.get('mapped_extra_points') is not None:
            metrics.append(
                ('Mapped extras', _fmt_stat(summary.get('mapped_extra_points')))
            )
        growth = summary.get('rank_growth_curves')
        if growth is None:
            growth = summary.get('rigorous_hits')
        if growth is not None:
            metrics.append(('Rank-growth signals', _fmt_stat(growth)))

        metric_cols = st.columns(len(metrics))
        for col, (label, value) in zip(metric_cols, metrics):
            col.metric(label, value)

        st.caption(
            'Curve creation is matched by timestamp; rigorous lower bounds are current '
            'and may have changed since the run.'
        )

        show_native = any(f.get('native_hits') is not None for f in findings)
        show_mapped = any(f.get('mapped_extras') is not None for f in findings)
        show_growth = any(bool(f.get('rank_growth_signal')) for f in findings)
        show_completed = any(f.get('completed') is not None for f in findings)

        table = []
        for finding in findings:
            record = {
                '#': finding['position'],
                'parameter': finding['parameter'],
                'curve': finding['curve_id'],
                'created in run window': finding['new_curve'],
                'rigorous lower now': finding['rigorous_lower_now'],
            }
            if show_native:
                record['new native x'] = finding['native_hits']
            if show_mapped:
                record['mapped extras'] = finding['mapped_extras']
            if show_growth:
                record['rank growth signal'] = finding['rank_growth_signal']
            if show_completed:
                record['completed'] = finding['completed']
            table.append(record)

        st.dataframe(
            table,
            hide_index=True,
            width='stretch',
            height=min(430, max(150, 42 + 35 * min(len(table), 10))),
        )

        reopenable = [f for f in findings if f.get('curve_id') is not None]
        if not reopenable:
            st.caption('No stored curve from this run is available to reopen.')
            return

        by_position = {int(f['position']): f for f in reopenable}
        positions = list(by_position)
        selected_position = st.selectbox(
            'Reopen candidate',
            positions,
            format_func=lambda position: (
                f"#{position} · t={by_position[position]['parameter']} · "
                f"curve #{by_position[position]['curve_id']}"
            ),
            key=f'results-findings-reopen-{row["id"]}',
        )
        picked = by_position[int(selected_position)]

        if button(
            'Open selected curve',
            semantic=f'results-findings-open-{row["id"]}-{picked["position"]}',
        ):
            st.session_state['curves_selected_id'] = int(picked['curve_id'])
            st.session_state['curves-filter'] = ''
            st.session_state['curves-minrank'] = 0
            st.session_state['curves-family'] = 'All'
            st.session_state['curves-evidence'] = 'Any'
            st.session_state['curves-status'] = 'Any'
            st.session_state['rh_page'] = 'Curves'
            st.rerun()


def render_jobs_results(db, ctx):
    """Render the scientific Results workspace inside Jobs."""
    render_feature_hook(db, ctx, 'manage.jobs.results.after_header')

    wanted=st.session_state.pop('results_selected_job_id',None)
    history_limit=int(
        st.selectbox(
            'Results window',
            [50,100,250,500],
            index=1,
            key='results-history-limit',
            help='Only this many recent job logs are parsed. Open a specific older job from Jobs to include it explicitly.',
        )
    )
    pairs=_pairs(db,limit=history_limit,wanted_id=wanted)
    _scoreboard(db,pairs)
    st.caption(f'Scoreboard and filters cover the loaded window of {len(pairs):,} job(s).')
    if not pairs:
        st.info('No Rank Hunter jobs yet.')
        return

    kinds=sorted({str(r['kind']) for r,_ in pairs})
    f1,f2,f3=st.columns([1.15,0.85,1.35])
    with f1:
        mode=selectbox(
            'Jobs',
            ['All','Active','Completed','Stopped / killed','Failed / interrupted'],
            semantic='results-mode',
        )
    with f2:
        interesting=toggle(
            'Interesting only',
            value=False,
            semantic='results-interesting',
        )
    with f3:
        kind=selectbox(
            'Kind',
            ['All kinds',*kinds],
            semantic='results-kind',
        )

    shown=pairs
    if mode=='Active': shown=[x for x in shown if x[0]['status'] in ACTIVE]
    elif mode=='Completed': shown=[x for x in shown if x[0]['status']=='succeeded']
    elif mode=='Stopped / killed': shown=[x for x in shown if x[0]['status'] in {'stopped','killed'}]
    elif mode=='Failed / interrupted': shown=[x for x in shown if x[0]['status'] in {'failed','interrupted'}]
    if interesting: shown=[x for x in shown if bool(x[1].get('interesting'))]
    if kind!='All kinds': shown=[x for x in shown if x[0]['kind']==kind]

    if not shown:
        st.info('No jobs match these filters.')
        return
    table=[_table_row(r,s) for r,s in shown]
    by_id={int(r['id']):(r,s) for r,s in shown}
    selected=selectable_dataframe(
        table,
        table,
        semantic='results-table',
        selected_id=wanted,
        id_key='job',
        selection_state_key='results_table_job_id',
    )
    if selected is None:
        return
    row,s=by_id[int(selected['job'])]
    _detail(db,row,s,ctx.detected_science_python())


def page(db, ctx):
    """Legacy compatibility route; canonical navigation lives under Jobs -> Results."""
    title(
        'Results',
        'Scientific interpretation of durable Jobs. Canonical home: Manage → Jobs → Results.',
        'System',
    )
    render_jobs_results(db, ctx)
