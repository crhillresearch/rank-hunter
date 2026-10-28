from __future__ import annotations

from datetime import datetime
import json
import subprocess
import sys

import streamlit as st

from rank42.candidate_engines import CANDIDATE_ENGINES
from rank42.torsion import MAZUR_TORSION_CHOICES
from rank42.pipeline_catalog import normalize_pipeline, validate_pipeline
from rank42.feature_hooks import render_feature_hook
from rank42.search_config import search_preset_provenance
from rank42.settings_registry import science_python_bootstrap_value
from rank42.plugins import (
    Plugin,
    charts_for_variant,
    discover_plugins,
    family_rank_label,
    get_variant,
    is_enabled,
    search_presets_for_variant,
    variant_candidate_defaults,
    variant_torsion_groups,
    variant_torsion_provider_role,
)

from .common import launch_with_pipeline, section_title, setting, title


SOURCE_METHODS = ("Family", "Torsion Group", "Custom Equation")


def _source_switch(current):
    return st.segmented_control(
        "Generation source", SOURCE_METHODS, default=current,
        key="candidate-generation-method", width="content",
        label_visibility="collapsed",
    )


def _plugin_choices(db,ctx):
    return [p for p in discover_plugins(ctx.project_root) if isinstance(p,Plugin) and p.plugin_type=='family' and is_enabled(db,p) and 'candidate_generation' in p.capabilities]


def _torsion_provider_choices(db, ctx, group):
    """Use manifest hints in the UI; exact provider checks run in the worker."""
    providers = []
    for plugin in _plugin_choices(db, ctx):
        for variant in plugin.variants:
            if group not in variant_torsion_groups(plugin, variant):
                continue
            role = variant_torsion_provider_role(plugin, variant)
            priority = 0 if role == "canonical_universal" else 1
            providers.append({
                "provider_key": f"{plugin.id}:{variant.id}",
                "plugin_name": plugin.name,
                "variant_name": variant.name,
                "priority": priority,
            })
    return sorted(providers, key=lambda item: (
        item["priority"], item["plugin_name"].lower(),
        item["variant_name"].lower(),
    ))

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
        "score_method": f"cand-score-method-{plugin_id}-{variant_id}",
        "compact_share": f"cand-compact-share-{plugin_id}-{variant_id}",
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
            st.session_state[key] = (
                int(round(float(value) * 100)) if name == "compact_share" else value
            )
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


def _candidate_pipeline_choice(
    pool_name, bounds, keeps, top, corpus_policy, *, target_mode="family",
    score_method="mestre_nagao", compact_share=0.0,
    compact_a_abs=100, compact_b_max=40,
):
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
    if score_method not in {"nagao", "mestre_nagao"}:
        raise ValueError("Choose Nagao or Mestre–Nagao scoring")
    if not 0 <= float(compact_share) <= 1:
        raise ValueError("Compact share must be between 0 and 1")
    screen_config = {"prime_bound": int(bounds_values[0]), "keep": int(keeps_values[0])}
    if score_method != "mestre_nagao":
        screen_config["score_method"] = score_method
    if compact_share:
        screen_config.update(
            compact_share=float(compact_share),
            compact_a_abs=max(1, int(compact_a_abs)),
            compact_b_max=max(1, int(compact_b_max)),
        )
    stages=[
        {
            "id":"nagao_screen",
            "config":screen_config,
        },
    ]
    for bound,keep in zip(bounds_values[1:],keeps_values[1:]):
        stages.append({
            "id":"nagao_rescore",
            "config":{
                "prime_bound":int(bound),"keep":int(keep),
                **({"score_method":score_method} if score_method != "mestre_nagao" else {}),
            },
        })
    if str(corpus_policy)!="off":
        stages.append({
            "id":"corpus_filter",
            "config":{"policy":str(corpus_policy)},
        })
    if target_mode == "torsion":
        stages.append({"id":"exact_torsion", "config":{}})
    stages=normalize_pipeline(stages)
    errors=validate_pipeline(target_mode,stages)
    if errors:
        raise ValueError("candidate Pipeline is incompatible: "+"; ".join(errors))
    return {
        "pipeline":{
            "id":None,
            "name":f"Generate candidates · {pool_name}",
            "target_mode":target_mode,
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


def _render_torsion_generation(db, ctx, *, embedded=False, new_search=False):
    with st.container(key="rh-candidates-source", border=True):
        st.markdown("### Generate from torsion group" if new_search else "### Generate from")
        if not new_search:
            _source_switch("Torsion Group")
        st.caption("Choose a prescribed torsion group and the families to scan.")
        group = st.selectbox(
            "Torsion group", MAZUR_TORSION_CHOICES,
            key="candidate-torsion-group",
        )
        providers = _torsion_provider_choices(db, ctx, group)
        by_key = {str(provider["provider_key"]): provider for provider in providers}
        if not providers:
            st.warning("No enabled candidate-generating family declares this torsion group.")
            return
        selected = st.multiselect(
            "Families to scan", list(by_key),
            default=list(by_key)[:min(3, len(by_key))],
            format_func=lambda key: (
                f"{by_key[key]['plugin_name']} · {by_key[key]['variant_name']}"
            ),
            key=f"candidate-torsion-providers-{group}",
        )
        st.caption(
            f"{len(selected)} of {len(providers)} providers selected. Each creates "
            "its own searchable pool. Search with the Torsion backend to check "
            "each specialization's exact group."
        )
    with st.container(key="rh-candidates-create", border=True):
        st.markdown("### Score & save provider pools")
        st.caption("Apply one parameter range and scoring plan to the selected families.")
        with st.form("candidate-torsion-generate", border=False):
            name = st.text_input(
                "Pool name prefix",
                value=f"torsion-{group.lower().replace(' × ', 'x').replace(' ', '')}-"
                      f"{datetime.now().strftime('%Y%m%d-%H%M')}",
            )
            c1,c2,c3,c4 = st.columns(4)
            amin = int(c1.number_input("a min", value=-3000, step=1))
            amax = int(c2.number_input("a max", value=3000, step=1))
            bmin = int(c3.number_input("b min", min_value=1, value=1, step=1))
            bmax = int(c4.number_input("b max", min_value=1, value=300, step=1))
            top = int(st.number_input(
                "Final candidates per family", min_value=1, value=250, step=50,
            ))
            score_method = st.selectbox(
                "Ranking method", ["mestre_nagao", "nagao"],
                format_func=lambda value: (
                    "Mestre–Nagao" if value == "mestre_nagao" else "Nagao"
                ),
            )
            with st.expander("Sampling and scoring limits", expanded=False,
                             icon=":material/tune:"):
                d1,d2 = st.columns(2)
                bounds = d1.text_input("Prime stages", value="127,523")
                keeps = d2.text_input("Stage keeps", value="1000,250")
                engine = st.selectbox("Parameter sampling", list(CANDIDATE_ENGINES))
                sample_count = 200000
                sample_seed = 42
                if engine == "sampled":
                    s1,s2 = st.columns(2)
                    sample_count = int(s1.number_input(
                        "Sample count", min_value=1, value=200000, step=1000,
                    ))
                    sample_seed = int(s2.number_input("Sample seed", value=42, step=1))
                compact_share = st.slider(
                    "Share reserved for small parameters", 0, 50, 0, step=5,
                    help="Reserve part of each pool for a smaller parameter box.",
                ) / 100
            submitted = st.form_submit_button(
                "Generate torsion pools", type="primary", width="stretch",
                icon=":material/group_add:",
            )
    if submitted:
        if not selected:
            st.error("Select at least one family to scan.")
            return
        if amin > amax or bmin > bmax:
            st.error("Minimum parameters must not exceed maximum parameters.")
            return
        try:
            choice = _candidate_pipeline_choice(
                name, bounds, keeps, top, "off", target_mode="torsion",
                score_method=score_method, compact_share=compact_share,
            )
        except ValueError as exc:
            st.error(str(exc))
            return
        jid = launch_with_pipeline(
            ctx, db, pipeline_choice=choice, target_mode="torsion",
            target={
                "torsion_group": group,
                "selected_provider_keys": selected,
                "output_pool_name": name,
                "candidate_generation": {
                    "a_min": amin, "a_max": amax, "b_min": bmin,
                    "b_max": bmax, "engine": engine, "top": top,
                    "sample_count": sample_count if engine == "sampled" else None,
                    "sample_seed": sample_seed if engine == "sampled" else None,
                    "score_method": score_method,
                },
            },
            run_config={"candidate_only": True},
            native_kind="candidate_generate",
            native_label=f"Generate torsion pools · {group}",
            native_command=[],
            native_metadata={
                "pool_name": name, "torsion_group": group,
                "launch_surface": "candidate_generate",
            },
            require_pipeline=True,
        )
        st.session_state["candidate_generate_recent_job"] = int(jid)
    recent_job_id = st.session_state.get("candidate_generate_recent_job")
    if recent_job_id:
        with st.container(key="rh-candidates-recent-job", border=True):
            info, action = st.columns([3, 1], vertical_alignment="center")
            info.markdown(f"**Latest generation job** · #{int(recent_job_id)}")
            if action.button("Open job", icon=":material/terminal:",
                             key=f"candidate-torsion-open-job-{recent_job_id}",
                             width="stretch"):
                st.session_state["manage_job_id"] = int(recent_job_id)
                st.session_state["jobs_section_pending"] = "Queue"
                st.session_state["rh_page"] = "Jobs"
                st.rerun()


def _register_custom_equation(db, ctx, formula):
    science = str(setting(
        db, "science_python",
        science_python_bootstrap_value(ctx.project_root, fallback=sys.executable),
    ))
    command = [
        science, "-m", "rank42.custom_candidate_family",
        "--project-root", str(ctx.project_root),
    ]
    try:
        process = subprocess.run(
            command, input=json.dumps(formula), text=True,
            capture_output=True, timeout=60, check=False,
            cwd=str(ctx.project_root),
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Equation validation took longer than 60 seconds") from exc
    if process.returncode:
        detail = (process.stderr or process.stdout or "Equation validation failed").strip()
        raise ValueError(detail[-900:])
    marker = "RANK42_CUSTOM_FAMILY_RESULT="
    line = next(
        (value for value in process.stdout.splitlines() if value.startswith(marker)),
        None,
    )
    if line is None:
        raise ValueError("Equation validator returned no family registration")
    return json.loads(line[len(marker):])


def _render_custom_generation(db, ctx, *, embedded=False, new_search=False):
    with st.container(key="rh-candidates-source", border=True):
        st.markdown("### Generate from custom equation" if new_search else "### Generate from")
        if not new_search:
            _source_switch("Custom Equation")
        st.caption("Enter a one-parameter Weierstrass equation over Q(t). Rank Hunter checks the generic model exactly before generating candidates.")
        name = st.text_input(
            "Family name", value="My custom family", key="candidate-custom-family-name",
        )
        parameter = st.text_input(
            "Parameter symbol", value="t", key="candidate-custom-parameter",
        ).strip()
        st.caption("y² + a₁xy + a₃y = x³ + a₂x² + a₄x + a₆")
        columns = st.columns(5)
        coefficients = [
            column.text_input(
                f"a{index}", value=default,
                key=f"candidate-custom-a{index}",
            )
            for column, index, default in zip(
                columns, (1, 2, 3, 4, 6), ("0", "0", "0", "-t^2", "1"),
            )
        ]
    with st.container(key="rh-candidates-create", border=True):
        st.markdown("### Score & save candidates")
        st.caption("Scan rational parameter values. A saved pool can be opened in Family Search.")
        with st.form("candidate-custom-generate", border=False):
            pool_name = st.text_input(
                "Pool name", value=f"custom-equation-{datetime.now().strftime('%Y%m%d-%H%M')}",
            )
            c1,c2,c3,c4 = st.columns(4)
            amin = int(c1.number_input("a min", value=-3000, step=1))
            amax = int(c2.number_input("a max", value=3000, step=1))
            bmin = int(c3.number_input("b min", min_value=1, value=1, step=1))
            bmax = int(c4.number_input("b max", min_value=1, value=300, step=1))
            top = int(st.number_input(
                "Final candidates to keep", min_value=1, value=250, step=50,
            ))
            with st.expander("Sampling and scoring limits", expanded=False,
                             icon=":material/tune:"):
                d1,d2 = st.columns(2)
                bounds = d1.text_input("Prime stages", value="127,523")
                keeps = d2.text_input("Stage keeps", value="1000,250")
                score_method = st.selectbox(
                    "Ranking method", ["mestre_nagao", "nagao"],
                    format_func=lambda value: (
                        "Mestre–Nagao" if value == "mestre_nagao" else "Nagao"
                    ),
                )
                engine = st.selectbox(
                    "Parameter sampling", list(CANDIDATE_ENGINES), index=2,
                )
                sample_count = 10000
                sample_seed = 42
                if engine == "sampled":
                    s1,s2 = st.columns(2)
                    sample_count = int(s1.number_input(
                        "Sample count", min_value=1, value=10000, step=1000,
                    ))
                    sample_seed = int(s2.number_input("Sample seed", value=42, step=1))
            submitted = st.form_submit_button(
                "Generate equation pool", type="primary", width="stretch",
                icon=":material/functions:",
            )
    if submitted:
        if amin > amax or bmin > bmax:
            st.error("Minimum parameters must not exceed maximum parameters.")
            return
        try:
            choice = _candidate_pipeline_choice(
                pool_name, bounds, keeps, top, "off", score_method=score_method,
            )
            formula = {
                "name": name, "parameter": parameter,
                "a_invariants": coefficients,
            }
            with st.spinner("Checking the exact equation…"):
                registered = _register_custom_equation(db, ctx, formula)
            jid = launch_with_pipeline(
                ctx, db, pipeline_choice=choice, target_mode="family",
                target={
                    "plugin_id": registered["plugin_id"],
                    "variant_id": registered["variant_id"],
                    "output_pool_name": pool_name,
                    "candidate_generation": {
                        "a_min": amin, "a_max": amax,
                        "b_min": bmin, "b_max": bmax,
                        "engine": engine, "top": top,
                        "sample_count": sample_count if engine == "sampled" else None,
                        "sample_seed": sample_seed if engine == "sampled" else None,
                        "score_method": score_method,
                    },
                },
                run_config={"candidate_only": True},
                native_kind="candidate_generate",
                native_label=f"Generate equation pool · {name}",
                native_command=[],
                native_metadata={
                    "plugin_id": registered["plugin_id"],
                    "pool_name": pool_name,
                    "launch_surface": "candidate_generate",
                    "source_method": "custom_equation",
                },
                require_pipeline=True,
            )
            st.session_state["candidate_generate_recent_job"] = int(jid)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            st.error(str(exc))
            return
    recent_job_id = st.session_state.get("candidate_generate_recent_job")
    if recent_job_id:
        with st.container(key="rh-candidates-recent-job", border=True):
            info, action = st.columns([3, 1], vertical_alignment="center")
            info.markdown(f"**Latest generation job** · #{int(recent_job_id)}")
            if action.button("Open job", icon=":material/terminal:",
                             key=f"candidate-custom-open-job-{recent_job_id}",
                             width="stretch"):
                st.session_state["manage_job_id"] = int(recent_job_id)
                st.session_state["jobs_section_pending"] = "Queue"
                st.session_state["rh_page"] = "Jobs"
                st.rerun()


def page(db, ctx, *, embedded=False, source_method=None, new_search=False):
    if not embedded:
        title(
            "Generate Candidates",
            "Build candidate queues with the ordering method suited to each family. Scores prioritize work; they are not rank claims.",
            icon="group_add",
        )
    render_feature_hook(db, ctx, "candidates.after_header")
    method = str(source_method or st.session_state.get("candidate-generation-method") or "Family")
    if method not in SOURCE_METHODS:
        method = "Family"
    if method == "Torsion Group":
        return _render_torsion_generation(db, ctx, embedded=embedded, new_search=new_search)
    if method == "Custom Equation":
        return _render_custom_generation(db, ctx, embedded=embedded, new_search=new_search)
    plugins=_plugin_choices(db,ctx)
    if not plugins:
        with st.container(key='rh-candidates-source', border=True):
            st.markdown('### Generate from')
            if not new_search:
                _source_switch('Family')
            section_title('No candidate-generation plugin is active','Validate and activate a plugin before generating a pool.')
            if st.button('Open Plugins',type='primary',width='stretch'):
                st.session_state['rh_page']='Plugin Families'; st.rerun()
    else:
        with st.container(key='rh-candidates-source', border=True):
            st.markdown('### Generate from family' if new_search else '### Generate from')
            if not new_search:
                _source_switch('Family')
            st.caption('Choose a family; its generator supplies the parameterization and candidate ordering.')
            plugin=st.selectbox('Plugin',plugins,format_func=lambda p:f"{p.name} · {family_rank_label(p, compact=True)}",label_visibility='collapsed')
            generation_mode = str(plugin.manifest.get('candidate_generation_mode') or '')
            indexed_base = generation_mode == 'indexed_genus_one_base'
            lattice_base = generation_mode == 'rank_two_genus_one_lattice'
            parameterized_base = indexed_base or lattice_base
            if indexed_base:
                st.caption('This family uses integer multiples nG on a genus-one base; Rank Hunter maps and scores each exact fiber directly.')
            elif lattice_base:
                st.caption('This family uses points mG1+nG2 on a rank-two genus-one base; Rank Hunter maps and scores the resulting exact fibers directly.')
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
        form_col,guide_col=(st.columns([1.3,.7], gap="medium") if presets else (st.container(),None))
        with form_col:
            with st.container(key='rh-candidates-create', border=True):
                st.markdown('### Build indexed fibers' if indexed_base else '### Scan the base lattice' if lattice_base else '### Score & save candidates')
                st.caption('Each selector gives a rational point on the genus-one base and a section on its elliptic fiber.' if indexed_base else 'Set the lattice shell range. Rank Hunter maps those points to exact fibers and scores them.' if lattice_base else 'Choose a rational parameter range. Rank Hunter will score it and save the strongest candidates.')
                with st.form('candidate-generate', border=False):
                    suffix=f"-{variant.id}" if len(plugin.variants)>1 else ''
                    if chart is not None: suffix += f'-{chart.id}'
                    name=st.text_input('Pool name',value=f"{plugin.id}{suffix}-{datetime.now().strftime('%Y%m%d-%H%M')}")
                    if parameterized_base:
                        c1,c2=st.columns(2)
                        c3=c4=None
                    else:
                        c1,c2,c3,c4=st.columns(4)
                    amin_key=f'cand-amin-{plugin.id}-{variant.id}'
                    amax_key=f'cand-amax-{plugin.id}-{variant.id}'
                    bmin_key=f'cand-bmin-{plugin.id}-{variant.id}'
                    bmax_key=f'cand-bmax-{plugin.id}-{variant.id}'
                    if indexed_base:
                        amin=c1.number_input('First multiple n',step=1,key=amin_key,**_widget_default_kwargs(amin_key,value=int(defaults.get('a_min',1))))
                        amax=c2.number_input('Last multiple n',step=1,key=amax_key,**_widget_default_kwargs(amax_key,value=int(defaults.get('a_max',500))))
                        bmin=bmax=1
                    elif lattice_base:
                        amin=c1.number_input('First lattice shell',min_value=1,step=1,key=amin_key,**_widget_default_kwargs(amin_key,value=int(defaults.get('a_min',1))))
                        amax=c2.number_input('Last lattice shell',min_value=1,step=1,key=amax_key,**_widget_default_kwargs(amax_key,value=int(defaults.get('a_max',4))))
                        bmin=bmax=1
                    else:
                        amin=c1.number_input('a min',step=1,key=amin_key,**_widget_default_kwargs(amin_key,value=int(defaults.get('a_min',-5000))))
                        amax=c2.number_input('a max',step=1,key=amax_key,**_widget_default_kwargs(amax_key,value=int(defaults.get('a_max',5000))))
                        bmin=c3.number_input('b min',min_value=1,step=1,key=bmin_key,**_widget_default_kwargs(bmin_key,value=int(defaults.get('b_min',1))))
                        bmax=c4.number_input('b max',min_value=1,step=1,key=bmax_key,**_widget_default_kwargs(bmax_key,value=int(defaults.get('b_max',250))))
                    top_key=f'cand-top-{plugin.id}-{variant.id}'
                    top=st.number_input('Final candidates to keep',min_value=1,step=100,key=top_key,**_widget_default_kwargs(top_key,value=int(defaults.get('top',1000))))
                    with st.expander('Scoring options', expanded=False, icon=':material/tune:'):
                        d1,d2=st.columns(2)
                        bounds_key=f'cand-bounds-{plugin.id}-{variant.id}'
                        keeps_key=f'cand-keeps-{plugin.id}-{variant.id}'
                        bounds=d1.text_input('Prime stages',key=bounds_key,**_widget_default_kwargs(bounds_key,value=str(defaults.get('stage_bounds','1000,5000'))))
                        keeps=d2.text_input('Stage keeps',key=keeps_key,**_widget_default_kwargs(keeps_key,value=str(defaults.get('stage_keeps','10000,1000'))))
                        if parameterized_base:
                            st.caption('Direct Mestre–Nagao scores are calculated on the actual fibers, not on residues of the lattice coordinates.')
                            score_method='mestre_nagao'
                            engine='sieve'
                        else:
                            score_method=st.selectbox(
                                'Ranking method', ['mestre_nagao','nagao'],
                                format_func=lambda value:'Mestre–Nagao' if value=='mestre_nagao' else 'Nagao',
                                key=f'cand-score-method-{plugin.id}-{variant.id}',
                                **_widget_default_kwargs(
                                    f'cand-score-method-{plugin.id}-{variant.id}',
                                    index=0 if defaults.get('score_method','mestre_nagao')=='mestre_nagao' else 1,
                                ),
                            )
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
                        compact_share=0 if parameterized_base else st.slider(
                            'Share reserved for small parameters',0,50,
                            **_widget_default_kwargs(
                                f'cand-compact-share-{plugin.id}-{variant.id}',
                                value=int(round(100*float(defaults.get('compact_share',0)))),
                            ),
                            step=5,
                            key=f'cand-compact-share-{plugin.id}-{variant.id}',
                            help='Keep some compact parameters alongside the strongest global scores.',
                        )/100
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
                    submitted=st.form_submit_button('Build indexed fiber pool' if indexed_base else 'Build lattice fiber pool' if lattice_base else 'Generate Nagao pool',type='primary',width='stretch',icon=':material/group_add:')
            if submitted:
                try:
                    pipeline_choice=_candidate_pipeline_choice(
                        name,bounds,keeps,top,corpus_policy,
                        score_method=score_method,compact_share=compact_share,
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
                    'score_method':score_method,
                    'compact_share':compact_share,
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
                    'score_method':score_method,'compact_share':compact_share,
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
                st.session_state['candidate_generate_recent_job']=int(jid)
            recent_job_id=st.session_state.get('candidate_generate_recent_job')
            if recent_job_id:
                with st.container(key='rh-candidates-recent-job', border=True):
                    info, action=st.columns([3, 1], vertical_alignment='center')
                    info.markdown(f'**Latest generation job** · #{int(recent_job_id)}')
                    if action.button('Open job', icon=':material/terminal:',
                                     key=f'candidate-generate-open-job-{recent_job_id}',
                                     width='stretch'):
                        st.session_state['manage_job_id']=int(recent_job_id)
                        st.session_state['jobs_section_pending']='Queue'
                        st.session_state['rh_page']='Jobs'
                        st.rerun()
        if presets:
            with guide_col:
                with st.container(key='rh-candidates-presets', border=True):
                    if new_search:
                        st.markdown('### :material/extension: Plugin Presets')
                        st.caption('Choose a generation preset for this family and variant.')
                    else:
                        section_title('Search style','Clicking one fills the visible controls; you can still edit anything afterward.')
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
