from __future__ import annotations

from datetime import datetime

import streamlit as st

from rank42.candidate_generate import CANDIDATE_ENGINES
from rank42.corpus import corpus_status
from rank42.pipeline_catalog import normalize_pipeline, validate_pipeline
from rank42.feature_hooks import render_feature_hook
from rank42.search_config import search_preset_provenance
from rank42.plugins import (
    Plugin,
    charts_for_variant,
    discover_plugins,
    family_rank_label,
    get_variant,
    is_enabled,
    search_presets_for_variant,
    variant_candidate_defaults,
)

from .common import launch_with_pipeline, section_title, title


def _plugin_choices(db,ctx):
    return [p for p in discover_plugins(ctx.project_root) if isinstance(p,Plugin) and p.plugin_type=='family' and is_enabled(db,p) and 'candidate_generation' in p.capabilities]

def _candidate_control_keys(plugin_id, variant_id):
    return {
        "a_min": f"cand-amin-{plugin_id}-{variant_id}",
        "a_max": f"cand-amax-{plugin_id}-{variant_id}",
        "b_min": f"cand-bmin-{plugin_id}-{variant_id}",
        "b_max": f"cand-bmax-{plugin_id}-{variant_id}",
        "stage_bounds": f"cand-bounds-{plugin_id}-{variant_id}",
        "stage_keeps": f"cand-keeps-{plugin_id}-{variant_id}",
        "top": f"cand-top-{plugin_id}-{variant_id}",
        "engine": f"cand-engine-{plugin_id}-{variant_id}",
        "sample_count": f"cand-sample-count-{plugin_id}-{variant_id}",
        "sample_seed": f"cand-sample-seed-{plugin_id}-{variant_id}",
    }

def _sync_candidate_state_version(plugin_id, variant_id, plugin_version):
    """Discard stale controls when a plugin changes its candidate defaults."""
    marker = f"candidate-default-version-{plugin_id}-{variant_id}"
    version = str(plugin_version)
    if str(st.session_state.get(marker) or "") == version:
        return False
    for key in _candidate_control_keys(plugin_id, variant_id).values():
        st.session_state.pop(key, None)
    st.session_state.pop(f"candidate-preset-{plugin_id}-{variant_id}", None)
    st.session_state[marker] = version
    return True

def _apply_candidate_preset(plugin_id, variant_id, preset):
    values = dict(preset.get("candidate") or {})
    mapping = _candidate_control_keys(plugin_id, variant_id)
    for name, value in values.items():
        key = mapping.get(name)
        if key:
            st.session_state[key] = value
    st.session_state[f"candidate-preset-{plugin_id}-{variant_id}"] = str(preset.get("id") or "")

def _candidate_preset_modified(preset, current):
    expected = dict(preset.get("candidate") or {})
    return any(str(current.get(key)) != str(value) for key, value in expected.items())

def _parse_positive_csv(text, label):
    try:
        values=[int(part.strip()) for part in str(text).split(',') if part.strip()]
    except ValueError as exc:
        raise ValueError(f"{label} must be comma-separated integers") from exc
    if not values or any(value<=0 for value in values):
        raise ValueError(f"{label} must contain positive integers")
    return values


def _candidate_pipeline_choice(pool_name, bounds, keeps, top, corpus_policy):
    bounds_values=_parse_positive_csv(bounds, "Prime stages")
    keeps_values=_parse_positive_csv(keeps, "Stage keeps")
    if len(bounds_values)!=len(keeps_values):
        raise ValueError("Stage keeps must match Prime stages")
    if bounds_values!=sorted(bounds_values) or len(set(bounds_values))!=len(bounds_values):
        raise ValueError("Prime stages must be strictly increasing")
    if any(right>left for left,right in zip(keeps_values,keeps_values[1:])):
        raise ValueError("Stage keeps must be nonincreasing")
    if int(top)>int(keeps_values[-1]):
        raise ValueError("Final candidates cannot exceed the final Stage keep")
    stages=[
        {
            "id":"nagao_screen",
            "config":{"prime_bound":int(bounds_values[0]),"keep":int(keeps_values[0])},
        },
    ]
    for bound,keep in zip(bounds_values[1:],keeps_values[1:]):
        stages.append({
            "id":"nagao_rescore",
            "config":{"prime_bound":int(bound),"keep":int(keep)},
        })
    if str(corpus_policy)!="off":
        stages.append({
            "id":"corpus_filter",
            "config":{"policy":str(corpus_policy)},
        })
    stages=normalize_pipeline(stages)
    errors=validate_pipeline("family",stages)
    if errors:
        raise ValueError("candidate Pipeline is incompatible: "+"; ".join(errors))
    return {
        "pipeline":{
            "id":None,
            "name":f"Generate candidates · {pool_name}",
            "target_mode":"family",
            "stages":stages,
            "config":{},
        },
        "target_rank":1,
    }


def _widget_default_kwargs(key, *, value=None, index=None):
    """Avoid Streamlit default+Session-State warnings after preset callbacks."""
    if key in st.session_state:
        return {}
    if index is not None:
        return {"index": int(index)}
    return {"value": value}


def page(db, ctx, *, embedded=False):
    if not embedded:
        title(
            "Generate Candidates",
            "Generate Nagao/Mestre-ranked specialization pools. Candidate pools are search queues, not rank claims.",
            icon="group_add",
        )
    render_feature_hook(db, ctx, "candidates.after_header")
    plugins=_plugin_choices(db,ctx)
    if not plugins:
        with st.container(border=True):
            section_title('No candidate-generation plugin is active','Validate and activate a plugin before generating a pool.')
            if st.button('Open Plugins',type='primary',width='stretch'):
                st.session_state['rh_page']='Plugin Families'; st.rerun()
    else:
        plugin=st.selectbox('Plugin',plugins,format_func=lambda p:f"{p.name} · {family_rank_label(p, compact=True)}")
        variant=get_variant(plugin)
        if len(plugin.variants)>1:
            variant=st.selectbox('Family variant',plugin.variants,format_func=lambda v:f"{v.name} ({v.id}) · {family_rank_label(plugin, v, compact=True)}",key=f'cand-variant-{plugin.id}')
        chart_options=[None,*charts_for_variant(plugin,variant.id)]
        chart=None
        if len(chart_options)>1:
            chart=st.selectbox('Parameter chart',chart_options,format_func=lambda c:'Native parameter (no chart)' if c is None else f"{c.label} ({c.id})",key=f'cand-chart-{plugin.id}-{variant.id}')
        defaults=variant_candidate_defaults(plugin,variant)
        if chart is not None:
            defaults.update(chart.candidate_defaults)
        _sync_candidate_state_version(plugin.id, variant.id, plugin.version)
        presets=tuple(p for p in search_presets_for_variant(plugin,variant) if p.get('candidate'))
        form_col,guide_col=st.columns([1.08,.92])
        with form_col:
            with st.form('candidate-generate'):
                section_title('Generate a pool','Start broad. Let Nagao scoring decide which exact specializations deserve search time.')
                suffix=f"-{variant.id}" if len(plugin.variants)>1 else ''
                if chart is not None: suffix += f'-{chart.id}'
                name=st.text_input('Pool name',value=f"{plugin.id}{suffix}-{datetime.now().strftime('%Y%m%d-%H%M')}")
                c1,c2,c3,c4=st.columns(4)
                amin_key=f'cand-amin-{plugin.id}-{variant.id}'
                amax_key=f'cand-amax-{plugin.id}-{variant.id}'
                bmin_key=f'cand-bmin-{plugin.id}-{variant.id}'
                bmax_key=f'cand-bmax-{plugin.id}-{variant.id}'
                amin=c1.number_input('a min',step=1,key=amin_key,**_widget_default_kwargs(amin_key,value=int(defaults.get('a_min',-5000))))
                amax=c2.number_input('a max',step=1,key=amax_key,**_widget_default_kwargs(amax_key,value=int(defaults.get('a_max',5000))))
                bmin=c3.number_input('b min',min_value=1,step=1,key=bmin_key,**_widget_default_kwargs(bmin_key,value=int(defaults.get('b_min',1))))
                bmax=c4.number_input('b max',min_value=1,step=1,key=bmax_key,**_widget_default_kwargs(bmax_key,value=int(defaults.get('b_max',250))))
                d1,d2,d3=st.columns(3)
                bounds_key=f'cand-bounds-{plugin.id}-{variant.id}'
                keeps_key=f'cand-keeps-{plugin.id}-{variant.id}'
                top_key=f'cand-top-{plugin.id}-{variant.id}'
                bounds=d1.text_input('Prime stages',key=bounds_key,**_widget_default_kwargs(bounds_key,value=str(defaults.get('stage_bounds','1000,5000'))))
                keeps=d2.text_input('Stage keeps',key=keeps_key,**_widget_default_kwargs(keeps_key,value=str(defaults.get('stage_keeps','10000,1000'))))
                top=d3.number_input('Final candidates',min_value=1,step=100,key=top_key,**_widget_default_kwargs(top_key,value=int(defaults.get('top',1000))))
                engine_choices=list(CANDIDATE_ENGINES)
                engine_default=str(defaults.get('engine','sieve'))
                engine_key=f'cand-engine-{plugin.id}-{variant.id}'
                engine=st.selectbox('Nagao engine',engine_choices,key=engine_key,**_widget_default_kwargs(engine_key,index=engine_choices.index(engine_default) if engine_default in engine_choices else 0))
                sample_count=int(defaults.get('sample_count',200000))
                sample_seed=int(defaults.get('sample_seed',42))
                if engine=='sampled':
                    s1,s2=st.columns(2)
                    sample_count_key=f'cand-sample-count-{plugin.id}-{variant.id}'
                    sample_seed_key=f'cand-sample-seed-{plugin.id}-{variant.id}'
                    sample_count=s1.number_input('Sample count',min_value=1,step=1000,key=sample_count_key,**_widget_default_kwargs(sample_count_key,value=sample_count))
                    sample_seed=s2.number_input('Sample seed',step=1,key=sample_seed_key,**_widget_default_kwargs(sample_seed_key,value=sample_seed))
                corpus_policy='off'
                if plugin.corpora:
                    corpus_labels={
                        'annotate':'Annotate known fibers',
                        'exclude-known':'Novel only · exclude known',
                        'known-only':'Known only · library replay',
                        'off':'Ignore library',
                    }
                    corpus_policy=st.selectbox(
                        'Library use',
                        list(corpus_labels),
                        format_func=lambda value:corpus_labels[value],
                        key=f'cand-corpus-policy-{plugin.id}-{variant.id}',
                        help='Research Libraries are read-only reference data; this never imports historical library curves into rank42.db.',
                    )
                submitted=st.form_submit_button('Generate Nagao pool',type='primary',width='stretch',icon=':material/group_add:')
            if submitted:
                try:
                    pipeline_choice=_candidate_pipeline_choice(
                        name,bounds,keeps,top,corpus_policy,
                    )
                except ValueError as exc:
                    st.error(str(exc))
                    return
                candidate_generation={
                    'a_min':int(amin),'a_max':int(amax),
                    'b_min':int(bmin),'b_max':int(bmax),
                    'engine':str(engine),'top':int(top),
                    'sample_count':int(sample_count) if engine=='sampled' else None,
                    'sample_seed':int(sample_seed) if engine=='sampled' else None,
                    'chart_id':chart.id if chart else None,
                }
                active_preset_id=str(
                    st.session_state.get(
                        f"candidate-preset-{plugin.id}-{variant.id}"
                    ) or ''
                ) or None
                active_preset=next(
                    (
                        rec for rec in presets
                        if str(rec.get('id') or '')==str(active_preset_id or '')
                    ),
                    None,
                )
                preset_settings={
                    'a_min':int(amin),'a_max':int(amax),
                    'b_min':int(bmin),'b_max':int(bmax),
                    'stage_bounds':str(bounds),'stage_keeps':str(keeps),
                    'top':int(top),'engine':str(engine),
                    'sample_count':int(sample_count) if engine=='sampled' else None,
                    'sample_seed':int(sample_seed) if engine=='sampled' else None,
                }
                preset_provenance=search_preset_provenance(
                    plugin,
                    variant,
                    context='candidate',
                    preset_id=active_preset_id,
                    effective_settings=preset_settings,
                    modified=(
                        _candidate_preset_modified(active_preset,preset_settings)
                        if active_preset is not None
                        else False
                    ),
                )
                run_config={'candidate_only':True}
                if preset_provenance is not None:
                    run_config['search_preset']=preset_provenance
                launch_metadata={
                    'plugin_id':plugin.id,
                    'plugin_variant':variant.id,
                    'chart_id':chart.id if chart else None,
                    'pool_name':name,
                    'corpus_policy':corpus_policy,
                    'launch_surface':'candidate_generate',
                }
                if preset_provenance is not None:
                    launch_metadata['search_preset']=preset_provenance
                jid=launch_with_pipeline(
                    ctx,db,
                    pipeline_choice=pipeline_choice,
                    target_mode='family',
                    target={
                        'plugin_id':plugin.id,
                        'variant_id':variant.id,
                        'output_pool_name':str(name),
                        'candidate_generation':candidate_generation,
                    },
                    run_config=run_config,
                    native_kind='candidate_generate',
                    native_label=f'Generate candidates · {name}',
                    native_command=[],
                    native_metadata=launch_metadata,
                    require_pipeline=True,
                )
                st.success(f'Started candidate Pipeline job #{jid}.')
        with guide_col:
            if presets:
                with st.container(border=True):
                    section_title('Search style','Family-owned presets. Clicking one fills the visible controls; you can still edit anything afterward.')
                    pcols=st.columns(2)
                    for i,preset in enumerate(presets):
                        with pcols[i % 2]:
                            st.button(
                                str(preset.get('label') or preset.get('id') or 'Preset'),
                                key=f"candidate-preset-button-{plugin.id}-{variant.id}-{preset.get('id')}",
                                width='stretch',
                                on_click=_apply_candidate_preset,
                                args=(plugin.id,variant.id,preset),
                                help=str(preset.get('description') or '') or None,
                            )
                    active_id=str(st.session_state.get(f"candidate-preset-{plugin.id}-{variant.id}") or '')
                    active=next((p for p in presets if str(p.get('id'))==active_id),None)
                    if active is not None:
                        current={
                            'a_min':amin,'a_max':amax,'b_min':bmin,'b_max':bmax,
                            'stage_bounds':bounds,'stage_keeps':keeps,'top':top,'engine':engine,
                            'sample_count':sample_count,'sample_seed':sample_seed,
                        }
                        modified=_candidate_preset_modified(active,current)
                        label=str(active.get('label') or active.get('id'))
                        st.caption(f"Preset: **{label}**" + (" · Modified" if modified else ""))
                        if active.get('description'):
                            st.caption(str(active['description']))
            with st.container(border=True):
                section_title('Generation strategy','These controls only rank parameters; Family Search does the rational-point work.')
                st.markdown(f'''**{plugin.name}**  
Variant: **{variant.name}** (`{variant.id}`)  
Rank metadata: **{family_rank_label(plugin, variant)}**  
Chart: **{chart.label if chart else 'native parameter'}**

- `a/b` ranges define exact rational parameters in the selected chart.
- Prime stages progressively refine the finite-field score.
- Stage keeps prune aggressively between scoring rounds.
- Final candidates is the durable SQLite pool size.
- Prefer broad moderate ranges before extreme denominators unless the family has evidence that deeper denominators pay off.
''')
                st.caption('Candidate defaults come from the active plugin/variant manifest so each family can tune its search economics.')
                if plugin.corpora:
                    ready=sum(1 for corpus in plugin.corpora if corpus_status(ctx.project_root,plugin,corpus).get('ready'))
                    st.caption(f'Libraries: **{ready}/{len(plugin.corpora)} ready** · candidate matching is exact and read-only.')
