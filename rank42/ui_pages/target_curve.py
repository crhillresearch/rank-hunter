from __future__ import annotations
import json
from rank42.feature_hooks import render_feature_hook
from rank42.ui_active_curve import get_active_curve_id
from rank42.ui_handoffs import clear_research_handoff, research_handoff_for_curve
import streamlit as st
from rank42.plugins import Plugin,discover_plugins,is_enabled,load_adapter,variant_for_family_spec,get_variant,search_presets_for_variant,variant_torsion_groups,variant_torsion_record
from rank42.pipeline_catalog import normalize_pipeline,stage_spec,template_stages,validate_pipeline
from rank42.search_config import adapter_search_option_defs, pipeline_search_config, resolve_plugin_search_config, search_preset_provenance
from .common import active_curve_selector,title,curve_options,launch_with_pipeline,search_pipeline_selector,section_title,ratpoints_selector
from rank42.ui_components import rh_key
from .family_search import _render_option_groups, _preset_option_value


def _target_geometry_hint_for_curve(session_state, curve_id):
    hint = session_state.get("target_geometry_hint")
    if not isinstance(hint, dict):
        return None
    try:
        hinted_curve_id = int(hint.get("curve_id"))
    except (TypeError, ValueError):
        return None
    return dict(hint) if hinted_curve_id == int(curve_id) else None


def _plugin_for_curve(db,ctx,row):
    # v0.8.6 direct provenance survives candidate-pool deletion. Legacy rows still
    # fall back to the historical candidate bridge and display-name matching.
    pid=row['plugin_id'] if 'plugin_id' in row.keys() and row['plugin_id'] else None
    family_spec=row['family_spec'] if 'family_spec' in row.keys() and row['family_spec'] else None
    if not pid:
        imported=db.execute("SELECT 1 FROM events WHERE curve_id=? AND message LIKE 'Manual external curve import%' LIMIT 1",(int(row['id']),)).fetchone()
        if imported:
            return None,None
    if not pid:
        rec=db.execute('''SELECT cp.plugin_id,cp.family_spec FROM candidates c JOIN candidate_pools cp ON cp.id=c.pool_id WHERE c.curve_id=? ORDER BY c.id DESC LIMIT 1''',(int(row['id']),)).fetchone()
        pid=rec['plugin_id'] if rec else None
        family_spec=rec['family_spec'] if rec else family_spec
    for p in discover_plugins(ctx.project_root):
        if not isinstance(p,Plugin) or p.plugin_type!='family': continue
        if pid and p.id==pid:
            return p,(variant_for_family_spec(p,family_spec) or get_variant(p))
        if str(p.manifest.get('curve_family_name') or '')==str(row['family']):
            return p,get_variant(p)
        for variant in p.variants:
            if str(variant.manifest.get('curve_family_name') or '')==str(row['family']):
                return p,variant
    return None,None


def _torsion_target_pipeline_choice(preset_id, goal_rank, *, label=None):
    """Build one exact-curve pipeline with an explicit torsion proof gate."""
    stages=[
        rec for rec in template_stages(str(preset_id or 'generator_breaker'),'curve')
        if stage_spec(rec['id']).category!='Candidates'
    ]
    if not any(rec['id']=='exact_torsion' for rec in stages):
        stages.insert(0,normalize_pipeline([{'id':'exact_torsion'}])[0])
    errors=validate_pipeline('curve',stages)
    if errors:
        raise ValueError('torsion target pipeline is incompatible: '+'; '.join(errors))
    return {
        'pipeline':{
            'id':None,
            'name':str(label or preset_id or 'Torsion target'),
            'target_mode':'curve',
            'stages':stages,
            'config':{},
        },
        'target_rank':max(1,int(goal_rank)),
    }


def _curve_target_pipeline_choice(name, goal_rank, stages):
    """Build a validated exact-curve Pipeline choice."""
    stages=normalize_pipeline(stages)
    errors=validate_pipeline('curve',stages)
    if errors:
        raise ValueError('target pipeline is incompatible: '+'; '.join(errors))
    return {
        'pipeline':{
            'id':None,
            'name':str(name),
            'target_mode':'curve',
            'stages':stages,
            'config':{},
        },
        'target_rank':max(1,int(goal_rank)),
    }


def _plugin_target_pipeline_choice(
    plugin_name,
    options,
    goal_rank,
    *,
    resolved_config=None,
):
    """Serialize Plugin Target controls into the Pipeline plugin-geometry stage."""
    options=dict(options or {})
    geometry_options={
        key:value for key,value in options.items()
        if key not in {'ratpoints','ratpoints_backend','plugin_variant','family_spec'}
    }
    geometry_options['target_lower']=max(1,int(goal_rank))
    certificate_timeout=max(1,int(options.get('certificate_timeout') or 120))
    exact_candidates=max(1,int(options.get('exact_candidates') or 64))
    return _curve_target_pipeline_choice(
        f'{plugin_name} · Plugin target',
        goal_rank,
        [
            {
                'id':'plugin_geometry',
                'config':{
                    'options':geometry_options,
                    'resolved_profile':{
                        key:resolved_config.get(key)
                        for key in (
                            'plugin_id','plugin_version','variant_id',
                            'family_spec','context','preset_id','config_hash',
                        )
                    } if resolved_config else {},
                    'exact_candidates':exact_candidates,
                },
            },
            {
                'id':'independence',
                'config':{
                    'certificate_timeout':certificate_timeout,
                    'max_candidates':exact_candidates,
                },
            },
            'stop_goal',
        ],
    )


def _free_target_pipeline_choice(
    *,
    stages,
    timeout,
    exact_candidates,
    certificate_timeout,
    model_prep_timeout,
    goal_rank,
):
    """Serialize FREE Target controls into a one-curve rational-search Pipeline."""
    heights=sorted(set(int(part.strip()) for part in str(stages).split(',') if part.strip()))
    if not heights or any(height<=0 for height in heights):
        raise ValueError('ratpoints stages must be positive integers')
    timeout=max(1,int(timeout))
    certificate_timeout=max(1,int(certificate_timeout))
    model_prep_timeout=max(1,int(model_prep_timeout))
    exact_candidates=max(0,int(exact_candidates))
    target_stages=[
        {
            'id':'affine_search',
            'config':{
                'heights':heights,
                'charts':1,
                'timeout':timeout,
                'retry_policy':'manual',
                'retry_timeout':max(timeout,4*timeout),
                'model_mode':'minimal',
                'model_prep_timeout':model_prep_timeout,
                'certify_after_search':False,
            },
        },
    ]
    if exact_candidates>0:
        target_stages.extend([
            {
                'id':'independence',
                'config':{
                    'certificate_timeout':certificate_timeout,
                    'max_candidates':exact_candidates,
                },
            },
            'stop_goal',
        ])
    return _curve_target_pipeline_choice(
        'FREE target · Current controls',
        goal_rank,
        target_stages,
    )


def _torsion_target_presets(target_presets):
    """Return plugin-owned target presets that select the torsion backend."""
    return tuple(
        preset for preset in target_presets
        if str((preset.get('target') or {}).get('backend') or '').lower()=='torsion'
        and (preset.get('target') or {}).get('pipeline_preset')
    )


def _apply_target_preset(plugin_id, curve_id, preset, defs):
    settings=dict(preset.get('target') or {})
    by_key={str(rec.get('key')):rec for rec in defs}
    for name,value in settings.items():
        if name=='ratpoints_backend':
            st.session_state[rh_key(f'target-ratpoints-{int(curve_id)}')]=str(value).upper()
            continue
        if name=='backend':
            st.session_state[f'target-mode-{int(curve_id)}']=str(value)
            continue
        if name=='pipeline_preset':
            st.session_state[f'target-torsion-strategy-{int(curve_id)}']=str(value)
            continue
        if name=='goal_rank':
            st.session_state[f'target-torsion-goal-{int(curve_id)}']=max(1,int(value))
            continue
        rec=by_key.get(str(name))
        if rec is None:
            raise ValueError(
                f"preset contains unknown Target search option {name!r}"
            )
        st.session_state[f'target-{plugin_id}-{name}']=_preset_option_value(rec,value)
    st.session_state[f'target-preset-{int(curve_id)}']=str(preset.get('id') or '')


def _target_preset_modified(
    preset,
    opts,
    defs,
    ratpoints_backend,
    *,
    mode=None,
    torsion_strategy=None,
    torsion_goal=None,
):
    expected=dict(preset.get('target') or {})
    known={str(rec.get('key')) for rec in defs}
    for name,value in expected.items():
        if name=='ratpoints_backend':
            if str(ratpoints_backend or '').upper()!=str(value or '').upper():
                return True
        elif name=='backend':
            if str(mode or '')!=str(value):
                return True
        elif name=='pipeline_preset':
            if str(torsion_strategy or '')!=str(value):
                return True
        elif name=='goal_rank':
            if int(torsion_goal or 0)!=int(value):
                return True
        elif name in known and str(opts.get(name))!=str(value):
            return True
    return False


def page(db,ctx):
    title(
        'Target',
        'Attack one promising specialization harder. Torsion mode preserves the exact prescribed-torsion gate even when the family has no target adapter; Plugin mode uses family-native structure; FREE treats the curve in isolation.',
        'Search',
    )
    render_feature_hook(db,ctx,'target.after_header')
    rows=curve_options(db)
    if not rows:
        st.info('No curves in the database yet.')
        return
    wanted=get_active_curve_id(st.session_state,'target_curve_id')
    row=active_curve_selector(
        db,
        rows,
        wanted=wanted,
        state_prefix='target',
        widget_prefix='target-curve',
        compatibility_keys=('target_curve_id',),
    )
    if row is None: return

    c1,c2,c3,c4=st.columns(4)
    c1.metric('Rigorous lower',f"≥ {row['rigorous_lower']}")
    c2.metric('Exact rank',row['exact_rank'] if row['exact_rank'] is not None else '—')
    stored_points=int(db.execute('SELECT COUNT(*) n FROM points WHERE curve_id=?',(int(row['id']),)).fetchone()['n'] or 0)
    if not stored_points:
        try:
            stored_points=len(json.loads(row['generators_json'] or '[]'))
        except Exception:
            stored_points=0
    c3.metric('Stored points',stored_points)
    c4.metric('Nagao score',f"{row['score']:.3f}" if row['score'] is not None else '—')
    if 'plugin_id' in row.keys() and row['plugin_id']:
        st.caption(
            f"Provenance: `{row['plugin_id']}` v{row['plugin_version'] or '—'} · "
            f"family `{row['family_spec'] or '—'}`"
        )

    research_handoff = research_handoff_for_curve(
        st.session_state,
        int(row["id"]),
    )
    if research_handoff is not None:
        with st.container(border=True):
            header_cols = st.columns([0.78, 0.22], vertical_alignment="center")
            with header_cols[0]:
                st.markdown("**Research selection handoff**")
            with header_cols[1]:
                if st.button(
                    "Clear research context",
                    use_container_width=True,
                    key=f"clear-target-research-context-{int(row['id'])}",
                ):
                    clear_research_handoff(st.session_state)
                    st.rerun()
            family = str(research_handoff.get("family") or row["family"] or "—")
            kind = str(research_handoff.get("kind") or "selection")
            count = len(research_handoff.get("curve_ids") or ())
            selection = research_handoff.get("selection") or {}
            item_count = selection.get("item_count")
            item_text = (
                f" · selection items: {int(item_count):,}"
                if isinstance(item_count, int) and item_count >= 0
                else ""
            )
            st.caption(
                f"Source: {research_handoff['source']} · kind: {kind} · family: {family} · "
                f"stored curves: {count:,}{item_text} · "
                f"hash: `{research_handoff['selection_hash'][:12]}`"
            )
            st.caption(
                "This context is informational only. Target Search remains the execution owner, "
                "and no rank/search controls were changed by the Workspace handoff."
            )

    geometry_hint = _target_geometry_hint_for_curve(
        st.session_state,
        int(row["id"]),
    )
    if geometry_hint is not None:
        with st.container(border=True):
            st.markdown("**MW Geometry scheduling hint**")
            st.caption(
                "Numerical context only. Target controls remain unchanged until you edit them."
            )
            h1, h2, h3 = st.columns(3)
            h1.metric("Lattice", f"#{int(geometry_hint['lattice_id'])}")
            condition = geometry_hint.get("condition")
            h2.metric(
                "Condition",
                "—" if condition is None else f"{float(condition):.4g}",
            )
            weakest = geometry_hint.get("weakest_ratio")
            h3.metric(
                "Weakest λ ratio",
                "—" if weakest is None else f"{float(weakest):.3e}",
            )
            pair = geometry_hint.get("strongest_pair")
            if isinstance(pair, dict):
                st.caption(
                    f"Strongest stored relationship: P{int(pair['left'])} ↔ P{int(pair['right'])} · "
                    f"|ρ|={float(pair['absolute_correlation']):.4f}"
                )
            st.caption(
                "Rank Hunter does not automatically translate this numerical geometry into "
                "search bounds or a proof claim."
            )

    plugin,variant=_plugin_for_curve(db,ctx,row)
    plugin_enabled=bool(plugin and is_enabled(db,plugin))
    target_presets=tuple(
        rec for rec in (
            search_presets_for_variant(plugin,variant) if plugin else ()
        )
        if rec.get('target')
    )
    torsion_groups=(
        tuple(variant_torsion_groups(plugin,variant))
        if plugin_enabled and variant is not None
        else ()
    )
    torsion_record=(
        variant_torsion_record(plugin,variant)
        if torsion_groups
        else None
    )
    torsion_presets=_torsion_target_presets(target_presets)

    defs=[]
    opts={}
    modes=['FREE']
    if plugin_enabled and 'target_search' in plugin.capabilities:
        modes.insert(0,'Plugin')
    if torsion_groups:
        modes.insert(0,'Torsion')
    mode_key=f'target-mode-{row["id"]}'
    if st.session_state.get(mode_key) not in modes:
        st.session_state[mode_key]=modes[0]

    torsion_strategy=None
    torsion_goal=None
    torsion_group=None

    setup_col,guide_col=st.columns([1.08,.92])
    with setup_col:
        with st.container(border=True):
            section_title(
                'Search setup',
                f"Curve #{row['id']} · {row['family']} · t={row['parameter']}",
            )
            mode=st.radio('Backend',modes,horizontal=True,key=mode_key)
            rp_backend,rp_exec=ratpoints_selector(
                db,
                key=f'target-ratpoints-{row["id"]}',
            )

            if mode=='Torsion':
                if len(torsion_groups)>1:
                    torsion_group=st.selectbox(
                        'Exact torsion group',
                        list(torsion_groups),
                        key=f'target-torsion-group-{row["id"]}',
                    )
                else:
                    torsion_group=torsion_groups[0]
                    st.caption(f'Exact torsion target: **{torsion_group}**')

                strategy_records=list(torsion_presets)
                if strategy_records:
                    strategy_ids=[
                        str((rec.get('target') or {}).get('pipeline_preset'))
                        for rec in strategy_records
                    ]
                    strategy_labels={
                        str((rec.get('target') or {}).get('pipeline_preset')):
                        str(rec.get('label') or rec.get('id'))
                        for rec in strategy_records
                    }
                else:
                    strategy_ids=[
                        'generator_breaker',
                        'geometry',
                        'geometry_grinder',
                        'aggressive_rank_hunter',
                    ]
                    strategy_labels={
                        'generator_breaker':'Generator Breaker',
                        'geometry':'Fiber Expansion',
                        'geometry_grinder':'Geometry Grinder',
                        'aggressive_rank_hunter':'Deep',
                    }
                # Preserve first occurrence when two plugin buttons happen to use
                # the same underlying core recipe.
                strategy_ids=list(dict.fromkeys(strategy_ids))
                strategy_key=f'target-torsion-strategy-{row["id"]}'
                if st.session_state.get(strategy_key) not in strategy_ids:
                    st.session_state[strategy_key]=strategy_ids[0]
                torsion_strategy=st.selectbox(
                    'Target strategy',
                    strategy_ids,
                    format_func=lambda value: strategy_labels.get(value,value.replace('_',' ').title()),
                    key=strategy_key,
                    help='Plugin-owned buttons select reusable exact-curve Pipeline recipes. Exact Torsion is inserted as the first proof gate.',
                )

                goal_default=(
                    int(torsion_record['goal_rank'])
                    if torsion_record is not None
                    else max(1,int(row['rigorous_lower'] or 0)+1)
                )
                goal_key=f'target-torsion-goal-{row["id"]}'
                goal_kwargs={}
                if goal_key not in st.session_state:
                    goal_kwargs['value']=goal_default
                torsion_goal=int(
                    st.number_input(
                        'Rigorous rank goal',
                        min_value=1,
                        step=1,
                        key=goal_key,
                        **goal_kwargs,
                    )
                )
                strategy_label=strategy_labels.get(
                    torsion_strategy,
                    torsion_strategy.replace('_',' ').title(),
                )
                pipeline_choice=_torsion_target_pipeline_choice(
                    torsion_strategy,
                    torsion_goal,
                    label=f'{plugin.name} · {strategy_label}',
                )
                st.caption(
                    'Torsion mode exact-checks the stored curve before any point/rank stage. '
                    'A torsion mismatch terminates the target run; heuristic or mapped points never bypass this gate.'
                )
                if st.button(
                    'Search target curve',
                    type='primary',
                    width='stretch',
                    key='target-torsion-launch',
                    disabled=not rp_exec,
                ):
                    active_preset_id=str(
                        st.session_state.get(
                            f'target-preset-{int(row["id"])}'
                        ) or ''
                    ) or None
                    active_preset=next(
                        (
                            rec for rec in target_presets
                            if str(rec.get('id') or '')
                            == str(active_preset_id or '')
                        ),
                        None,
                    )
                    preset_provenance=search_preset_provenance(
                        plugin,
                        variant,
                        context='target',
                        preset_id=active_preset_id,
                        effective_settings={
                            'backend':'Torsion',
                            'pipeline_preset':str(torsion_strategy),
                            'goal_rank':int(torsion_goal),
                            'ratpoints_backend':str(rp_backend),
                            'torsion_group':str(torsion_group),
                        },
                        modified=(
                            _target_preset_modified(
                                active_preset,
                                {},
                                defs,
                                rp_backend,
                                mode=mode,
                                torsion_strategy=torsion_strategy,
                                torsion_goal=torsion_goal,
                            )
                            if active_preset is not None
                            else False
                        ),
                    )
                    metadata={
                        'curve_id':int(row['id']),
                        'plugin_id':plugin.id,
                        'plugin_variant':variant.id if variant else None,
                        'torsion_group':str(torsion_group),
                        'target_strategy':str(torsion_strategy),
                    }
                    run_config={
                        'ratpoints_backend':rp_backend,
                        'ratpoints':rp_exec,
                        'certificate_timeout':300,
                        'exact_candidates':256,
                        'record_breaker_mode':True,
                    }
                    if preset_provenance is not None:
                        metadata['search_preset']=preset_provenance
                        run_config['search_preset']=preset_provenance
                    jid=launch_with_pipeline(
                        ctx,db,
                        pipeline_choice=pipeline_choice,
                        target_mode='curve',
                        target={
                            'curve_id':int(row['id']),
                            'torsion_group':str(torsion_group),
                            'plugin_id':plugin.id,
                            'variant_id':variant.id if variant else None,
                        },
                        run_config=run_config,
                        native_kind='target_torsion',
                        native_label=f"Torsion target #{row['id']} · {plugin.name}",
                        native_command=[],
                        native_metadata=metadata,
                        require_pipeline=True,
                    )
                    st.success(f'Started torsion target job #{jid}.')

            elif mode=='Plugin':
                profile_goal=pipeline_search_config(
                    plugin,
                    variant,
                ).get('target_rank')
                pipeline_choice=search_pipeline_selector(
                    db,
                    target_mode='curve',
                    key=f'target-{row["id"]}',
                    suggested_goal=(
                        max(1,int(profile_goal))
                        if profile_goal is not None
                        else max(1,int(row['rigorous_lower'] or 0)+1)
                    ),
                    builtin_label='Built-in Pipeline · Plugin geometry',
                )
                variant_label=f" · {variant.name}" if variant and len(plugin.variants)>1 else ''
                st.caption(f'Using **{plugin.name}{variant_label}** target adapter.')
                adapter=load_adapter(plugin)
                opts={}
                defs=adapter_search_option_defs(
                    plugin,
                    variant,
                    adapter,
                    context='target',
                )
                visible_defs=[rec for rec in defs if rec['key']!='limit']
                opts.update(_render_option_groups(visible_defs,f'target-{plugin.id}'))
                opts['limit']=1
                opts['ratpoints']=rp_exec
                opts['ratpoints_backend']=rp_backend
                opts['plugin_variant']=variant.id if variant else get_variant(plugin).id
                opts['family_spec']=variant.family_spec if variant else plugin.family_spec
                for key in (
                    'chart_id','chart_parameter','native_family_key',
                    'native_family_spec','native_parameter','chart_map_fingerprint'
                ):
                    if key in row.keys() and row[key] is not None:
                        opts[key]=row[key]
                if st.button(
                    'Search target curve',
                    type='primary',
                    width='stretch',
                    key='target-plugin-launch',
                    disabled=not rp_exec,
                ):
                    preset_provenance=None
                    if pipeline_choice.get('builtin'):
                        active_preset_id=str(
                            st.session_state.get(
                                f'target-preset-{int(row["id"])}'
                            ) or ''
                        ) or None
                        resolved_config=resolve_plugin_search_config(
                            plugin,
                            variant,
                            adapter,
                            context='target',
                            preset_id=active_preset_id,
                            overrides=opts,
                        )
                        effective_options={
                            **resolved_config['options'],
                            **resolved_config['controls'],
                        }
                        active_preset=next(
                            (
                                rec for rec in target_presets
                                if str(rec.get('id') or '')
                                == str(active_preset_id or '')
                            ),
                            None,
                        )
                        preset_provenance=search_preset_provenance(
                            plugin,
                            variant,
                            context='target',
                            preset_id=active_preset_id,
                            effective_settings=effective_options,
                            modified=(
                                _target_preset_modified(
                                    active_preset,
                                    opts,
                                    defs,
                                    rp_backend,
                                    mode=mode,
                                    torsion_strategy=torsion_strategy,
                                    torsion_goal=torsion_goal,
                                )
                                if active_preset is not None
                                else False
                            ),
                        )
                        pipeline_choice=_plugin_target_pipeline_choice(
                            plugin.name,
                            effective_options,
                            pipeline_choice['target_rank'],
                            resolved_config=resolved_config,
                        )
                    metadata={
                        'curve_id':int(row['id']),
                        'plugin_id':plugin.id,
                        'plugin_variant':opts['plugin_variant'],
                        'chart_id':opts.get('chart_id'),
                        'chart_parameter':opts.get('chart_parameter'),
                        'native_family_key':opts.get('native_family_key'),
                        'native_family_spec':opts.get('native_family_spec'),
                        'native_parameter':opts.get('native_parameter'),
                        'chart_map_fingerprint':opts.get('chart_map_fingerprint'),
                    }
                    run_config={
                        'ratpoints_backend':rp_backend,
                        'ratpoints':rp_exec,
                        'certificate_timeout':int(opts.get('certificate_timeout') or 120),
                        'exact_candidates':max(1,int(opts.get('exact_candidates') or 64)),
                    }
                    if preset_provenance is not None:
                        metadata['search_preset']=preset_provenance
                        run_config['search_preset']=preset_provenance
                    jid=launch_with_pipeline(
                        ctx,db,
                        pipeline_choice=pipeline_choice,
                        target_mode='curve',
                        target={
                            'curve_id':int(row['id']),
                            'plugin_id':plugin.id,
                            'variant_id':opts['plugin_variant'],
                        },
                        run_config=run_config,
                        native_kind='target_plugin',
                        native_label=f"Target #{row['id']} · {plugin.name}",
                        native_command=[],
                        native_metadata=metadata,
                        require_pipeline=True,
                    )
                    st.success(f'Started target job #{jid}.')

            else:
                pipeline_choice=search_pipeline_selector(
                    db,
                    target_mode='curve',
                    key=f'target-{row["id"]}',
                    suggested_goal=max(1,int(row['rigorous_lower'] or 0)+1),
                    builtin_label='Built-in Pipeline · Current FREE controls',
                )
                st.caption(
                    'FREE mode assumes no usable family/torsion structure. Exact points are stored; '
                    'rank changes only after an exact independence certificate.'
                )
                a,b,c,d=st.columns(4)
                stages=a.text_input('ratpoints stages','1000,10000,100000')
                timeout=int(b.number_input('Seconds per stage',min_value=1,value=20,step=5))
                exact=int(c.number_input('Exact candidates',min_value=0,value=8,step=1))
                prep_timeout=int(
                    d.number_input(
                        'Model prep timeout',
                        min_value=1,
                        value=10,
                        step=5,
                        help='Hard timeout for global minimal-model preparation; FREE search falls back exactly if it expires.',
                    )
                )
                cert=int(st.number_input('Certificate timeout',min_value=1,value=120,step=30))
                if st.button(
                    'Search target curve',
                    type='primary',
                    width='stretch',
                    key='target-free-launch',
                    disabled=not rp_exec,
                ):
                    if pipeline_choice.get('builtin'):
                        try:
                            pipeline_choice=_free_target_pipeline_choice(
                                stages=stages,
                                timeout=timeout,
                                exact_candidates=exact,
                                certificate_timeout=cert,
                                model_prep_timeout=prep_timeout,
                                goal_rank=pipeline_choice['target_rank'],
                            )
                        except ValueError as exc:
                            st.error(str(exc))
                            return
                    jid=launch_with_pipeline(
                        ctx,db,
                        pipeline_choice=pipeline_choice,
                        target_mode='curve',
                        target={'curve_id':int(row['id'])},
                        run_config={
                            'ratpoints_backend':rp_backend,
                            'ratpoints':rp_exec,
                            'certificate_timeout':cert,
                            'exact_candidates':max(1,exact),
                        },
                        native_kind='target_free',
                        native_label=f"FREE target #{row['id']}",
                        native_command=[],
                        native_metadata={'curve_id':int(row['id'])},
                        require_pipeline=True,
                    )
                    st.success(f'Started target job #{jid}.')

    with guide_col:
        if target_presets:
            with st.container(border=True):
                section_title(
                    'Search style',
                    'Family-owned target presets fill the visible controls. Torsion presets can switch the backend and Pipeline recipe without requiring a target adapter.',
                )
                pcols=st.columns(2)
                for i,preset in enumerate(target_presets):
                    preset_target=dict(preset.get('target') or {})
                    preset_backend=str(preset_target.get('backend') or 'Plugin')
                    if preset_backend=='Torsion':
                        disabled='Torsion' not in modes
                    else:
                        disabled=(
                            mode!='Plugin'
                            or not plugin
                            or not plugin_enabled
                            or 'target_search' not in plugin.capabilities
                        )
                    with pcols[i%2]:
                        st.button(
                            str(preset.get('label') or preset.get('id') or 'Preset'),
                            key=f'target-preset-button-{row["id"]}-{preset.get("id")}',
                            width='stretch',
                            disabled=disabled,
                            on_click=_apply_target_preset,
                            args=(plugin.id,int(row['id']),preset,defs),
                            help=str(preset.get('description') or '') or None,
                        )
                active_id=str(
                    st.session_state.get(f'target-preset-{int(row["id"])}') or ''
                )
                active=next(
                    (p for p in target_presets if str(p.get('id'))==active_id),
                    None,
                )
                if active is not None:
                    modified=_target_preset_modified(
                        active,
                        opts,
                        defs,
                        rp_backend,
                        mode=mode,
                        torsion_strategy=torsion_strategy,
                        torsion_goal=torsion_goal,
                    )
                    label=str(active.get('label') or active.get('id'))
                    st.caption(
                        f'Preset: **{label}**'+(' · Modified' if modified else '')
                    )
                    if active.get('description'):
                        st.caption(str(active['description']))

        with st.container(border=True):
            section_title(
                'What targeting changes',
                'Broad family search is for discovery; this page spends more time on one curve that already earned attention.',
            )
            if torsion_groups:
                record_note=''
                if torsion_record is not None:
                    record_note=(
                        f" · record lower ≥{int(torsion_record['rank_lower'])}"
                        f" · plugin goal ≥{int(torsion_record['goal_rank'])}"
                    )
                st.markdown(
                    f'''**Torsion-aware target available**  
Plugin: **{plugin.name}**  
Variant: **{variant.name if variant else 'default'}**  
Exact target: **{', '.join(torsion_groups)}**{record_note}

Use **Torsion** mode for these curves even when the plugin has no `target_search`
adapter. Rank Hunter keeps the exact torsion proof boundary and then attacks the
stored curve with reusable point/geometry Pipelines.
'''
                )
            elif plugin_enabled and 'target_search' in plugin.capabilities:
                st.markdown(
                    f'''**Family-aware target available**  
Plugin: **{plugin.name}**  
Variant: **{variant.name if variant else 'default'}**

Use Plugin mode when you want the family’s exact native representation, known subgroup handling, chart logic, or other specialized attack path.
'''
                )
            else:
                st.info(
                    'No enabled plugin target adapter or prescribed-torsion target was resolved for this curve. FREE search remains available.'
                )
            st.markdown(
                '''
**Escalate a curve when:**
- it already has a strong rigorous lower bound;
- repeated searches produce plausible extra points;
- numerical subgroup residuals suggest genuine novelty;
- or the curve is important enough to justify lattice/saturation work.

**Do not escalate just because:**
- its Nagao score is high;
- a descent timed out;
- or many mapped points were already shown to be dependent.
'''
            )
            if st.button('Open curve workspace',width='stretch'):
                st.session_state['curves_selected_id']=int(row['id'])
                st.session_state['rh_page']='Curves'
                st.rerun()
