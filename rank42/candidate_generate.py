"""Generate Nagao-ranked candidates directly into a v0.8 candidate pool."""
from __future__ import annotations

import argparse
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

from rank42.candidates import create_pool, export_pool_jsonl, replace_pool_rows
from rank42.candidate_engines import CANDIDATE_ENGINES
from rank42.corpus import apply_candidate_corpus_policy
from rank42.db import connect, now
from rank42.family_loader import load_family
from rank42.nagao import family_score_provenance
from rank42.plugins import get_chart, get_plugin, get_variant, load_adapter, plugin_fingerprints
from rank42.search import (
    canonical_candidate_pair, family_parameter_symmetry, get_tables,
    rescore_stage, score_rational, stage0_sampled, stage0_scalar, stage0_sieve,
)


def _csv_ints(text):
    vals=[int(x.strip()) for x in str(text).split(',') if x.strip()]
    if not vals: raise argparse.ArgumentTypeError('expected comma-separated integers')
    return vals


def parse_args():
    ap=argparse.ArgumentParser(description='Generate a first-class Rank Hunter candidate pool')
    ap.add_argument('--project-root',default='.')
    ap.add_argument('--db',default='rank42.db')
    ap.add_argument('--plugin',required=True)
    ap.add_argument('--variant',help='Optional family variant id for a multi-family plugin')
    ap.add_argument('--chart',help='Optional exact Family parameter chart id')
    ap.add_argument('--pool-name',required=True)
    ap.add_argument('--pipeline-run-id',type=int,default=None)
    ap.add_argument('--a-min',type=int,required=True); ap.add_argument('--a-max',type=int,required=True)
    ap.add_argument('--b-min',type=int,default=1); ap.add_argument('--b-max',type=int,required=True)
    ap.add_argument('--prime-bound',type=int,default=1000)
    ap.add_argument('--stage-bounds',type=_csv_ints,default=None)
    ap.add_argument('--stage-keeps',type=_csv_ints,default=None)
    ap.add_argument('--engine',choices=CANDIDATE_ENGINES,default='sieve')
    ap.add_argument('--score-method', choices=['nagao','mestre_nagao'], default='nagao')
    ap.add_argument('--sample-count',type=int,default=200000)
    ap.add_argument('--sample-seed',type=int,default=42)
    ap.add_argument('--exclude-parameters',default='',
                    help='Comma-separated rational parameters already searched by this campaign')
    ap.add_argument(
        '--corpus-policy',
        choices=['annotate','exclude-known','known-only','off'],
        default='annotate',
        help='Use plugin corpora to annotate/filter candidates without importing corpus curves.',
    )
    ap.add_argument('--block-size',type=int,default=1<<18)
    ap.add_argument('--top',type=int,default=1000)
    ap.add_argument('--compact-share',type=float,default=0.0,
                    help='Reserve a fraction of final candidates for a compact parameter box')
    ap.add_argument('--compact-a-abs',type=int,default=100)
    ap.add_argument('--compact-b-max',type=int,default=40)
    ap.add_argument('--cache-dir',default='.rank42-cache')
    ap.add_argument('--no-cache',action='store_true')
    ap.add_argument('--export-jsonl')
    return ap.parse_args()


def _stages(args):
    bounds=args.stage_bounds or [args.prime_bound]
    if sorted(bounds)!=bounds or len(set(bounds))!=len(bounds):
        raise SystemExit('--stage-bounds must be strictly increasing')
    if args.stage_keeps is None:
        if len(bounds)==1: keeps=[args.top]
        else:
            keeps=[max(args.top*20,args.top)]
            for _ in range(1,len(bounds)-1): keeps.append(max(args.top*5,args.top))
            keeps.append(args.top)
    else:
        keeps=args.stage_keeps
        if len(keeps)!=len(bounds): raise SystemExit('--stage-keeps must match --stage-bounds')
        if any(k<=0 for k in keeps): raise SystemExit('--stage-keeps must be positive')
        if any(b>a for a,b in zip(keeps,keeps[1:])): raise SystemExit('--stage-keeps must be nonincreasing')
        if keeps[-1] < args.top: raise SystemExit('final stage keep must be >= --top')
    return bounds, keeps


def main():
    args=parse_args()
    if (args.b_min<=0 or args.a_min>args.a_max or args.b_min>args.b_max
            or args.top<=0 or not 0.0<=args.compact_share<=1.0
            or args.compact_a_abs<=0 or args.compact_b_max<=0):
        raise SystemExit('invalid candidate range')
    project=Path(args.project_root).resolve()
    plugin=get_plugin(project,args.plugin)
    if 'candidate_generation' not in plugin.capabilities:
        raise SystemExit(f'plugin {plugin.id} does not declare candidate_generation')
    chart=get_chart(plugin,args.chart) if args.chart else None
    if chart is not None:
        if args.variant and str(args.variant) != chart.variant_id:
            raise SystemExit(f'chart {chart.id} belongs to variant {chart.variant_id}, not {args.variant}')
        variant=get_variant(plugin,chart.variant_id)
    else:
        variant=get_variant(plugin,args.variant)
    family=load_family(variant.family_spec)
    excluded=set()
    for raw in str(args.exclude_parameters or '').split(','):
        if not raw.strip():
            continue
        try:
            q=Fraction(raw.strip())
        except (ValueError, ZeroDivisionError):
            raise SystemExit(f'invalid excluded parameter {raw!r}') from None
        excluded.add(canonical_candidate_pair(family,q.numerator,q.denominator))
    args.excluded_candidate_pairs=excluded
    fingerprints=plugin_fingerprints(plugin,variant)
    bounds,keeps=_stages(args)
    db=connect(args.db)
    generation={
        'a_min':args.a_min,'a_max':args.a_max,'b_min':args.b_min,'b_max':args.b_max,
        'stage_bounds':bounds,'stage_keeps':keeps,'engine':args.engine,'top':args.top,
        'score_method':args.score_method,
        'compact_share':args.compact_share,
        'compact_a_abs':args.compact_a_abs if args.compact_share else None,
        'compact_b_max':args.compact_b_max if args.compact_share else None,
        'sample_count':args.sample_count if args.engine == 'sampled' else None,
        'sample_seed':args.sample_seed if args.engine == 'sampled' else None,
        'excluded_parameters':sorted(f'{a}/{b}' for a,b in excluded),
        'corpus_policy':args.corpus_policy,
        'pipeline_run_id':args.pipeline_run_id,
        'family_name':family.name(),'family_spec':variant.family_spec,
        'variant_id':variant.id,'variant_name':variant.name,
        'chart_id': chart.id if chart else None,
        'chart_label': chart.label if chart else None,
        'native_family_spec': chart.native_family_spec if chart else variant.family_spec,
        'native_variant_id': chart.native_variant_id if chart else variant.id,
        'native_family_key': chart.native_family_key if chart else f'{plugin.id}:{variant.id}',
        'chart_map_fingerprint': chart.fingerprint if chart else None,
    }
    pool=create_pool(db,name=args.pool_name,plugin_id=plugin.id,plugin_version=plugin.version,
                     family_spec=variant.family_spec,generation=generation,status='generating',
                     **fingerprints)
    print(f'[plugin] {plugin.name} {plugin.version}', flush=True)
    print(f'[variant] {variant.id} · {variant.name}', flush=True)
    print(f'[family] {family.name()}', flush=True)
    if chart:
        print(f'[chart] {chart.id} · {chart.label} · native {chart.native_family_key}')
    print(f'[pool] #{pool["id"]} {pool["name"]}', flush=True)
    symmetry=family_parameter_symmetry(family)
    if symmetry: print(f'[symmetry] {symmetry}', flush=True)

    shim=SimpleNamespace(**vars(args), family=variant.family_spec)
    tables={}
    tables=get_tables(family,variant.family_spec,bounds[0],tables,shim)
    if args.engine=='sieve':
        rows,tested=stage0_sieve(shim,tables,keeps[0],family)
    elif args.engine=='sampled':
        rows,tested=stage0_sampled(shim,tables,keeps[0],family)
    else:
        rows,tested=stage0_scalar(shim,tables,keeps[0],family)
    compact_rows=[]
    compact_available=(
        args.compact_share>0
        and max(args.a_min,-args.compact_a_abs)<=min(args.a_max,args.compact_a_abs)
        and args.b_min<=min(args.b_max,args.compact_b_max)
    )
    if compact_available:
        compact_args=dict(vars(shim))
        compact_args['a_min']=max(args.a_min,-args.compact_a_abs)
        compact_args['a_max']=min(args.a_max,args.compact_a_abs)
        compact_args['b_max']=min(args.b_max,args.compact_b_max)
        compact_shim=SimpleNamespace(**compact_args)
        compact_keeps=[max(1,round(keep*args.compact_share)) for keep in keeps]
        if args.engine=='sieve':
            compact_rows,compact_tested=stage0_sieve(compact_shim,tables,compact_keeps[0],family)
        elif args.engine=='sampled':
            compact_rows,compact_tested=stage0_sampled(compact_shim,tables,compact_keeps[0],family)
        else:
            compact_rows,compact_tested=stage0_scalar(compact_shim,tables,compact_keeps[0],family)
        print(f'[compact] tested {compact_tested:,}; retained {len(compact_rows)} '
              f'from a={compact_args["a_min"]}..{compact_args["a_max"]}, '
              f'b={compact_args["b_min"]}..{compact_args["b_max"]}',flush=True)
    for rec in rows: rec['prime_bound']=bounds[0]
    for rec in compact_rows: rec['prime_bound']=bounds[0]
    print(f'[stage 1/{len(bounds)}] retained {len(rows)} from {tested:,}', flush=True)
    for idx,bound in enumerate(bounds[1:],1):
        tables=get_tables(family,variant.family_spec,bound,tables,shim)
        before=len(rows); rows=rescore_stage(rows,tables,keeps[idx],bound)
        print(f'[stage {idx+1}/{len(bounds)}] rescored {before}; retained {len(rows)}', flush=True)
        if compact_rows:
            compact_before=len(compact_rows)
            compact_rows=rescore_stage(compact_rows,tables,compact_keeps[idx],bound)
            print(f'[compact stage {idx+1}/{len(bounds)}] rescored '
                  f'{compact_before}; retained {len(compact_rows)}',flush=True)
    rows=sorted(rows,key=lambda r:(r['score'],r['a'],r['b']),reverse=True)
    if compact_rows:
        compact_rows=sorted(compact_rows,key=lambda r:(r['score'],r['a'],r['b']),reverse=True)
        quota=max(1,round(args.top*args.compact_share))
        selected=[]; seen=set()
        def add_unique(source,limit):
            for rec in source:
                if len(selected)>=limit: break
                key=Fraction(int(rec['a']),int(rec['b']))
                if key in seen: continue
                seen.add(key); selected.append(rec)
        add_unique(compact_rows,quota)
        compact_selected=len(selected)
        add_unique(rows,args.top)
        add_unique(compact_rows,args.top)
        rows=selected
        print(f'[compact mix] reserved={compact_selected} total={len(rows)} '
              f'of requested={args.top}',flush=True)
    else:
        rows=rows[:args.top]
    # Some algebraic families have a small number of mathematically important
    # seed fibers that should be examined even when their heuristic score would
    # place them below the screening cutoff. Keep the configured pool size by
    # reserving its first slots for those exact parameter values.
    seed_callback=getattr(family,'candidate_seed_parameters',None)
    if callable(seed_callback):
        seeds=[]; seed_keys=set()
        for value in seed_callback():
            q=Fraction(str(value))
            if q.denominator<=0: continue
            a,b=canonical_candidate_pair(family,q.numerator,q.denominator)
            key=(int(a),int(b))
            if key in seed_keys or key in excluded: continue
            seed_keys.add(key)
            score,used=score_rational(a,b,tables)
            seeds.append({'family':family.name(),'a':int(a),'b':int(b),
                          'score':float(score),'prime_terms':int(used),
                          'prime_bound':int(bounds[-1]),
                          'forced_family_seed':True})
        if seeds:
            rows=[row for row in rows if (int(row['a']),int(row['b'])) not in seed_keys]
            rows=seeds+rows[:max(0,args.top-len(seeds))]
    for rec in rows:
        rec['ranking_kind']=args.score_method
        rec['ranking_value']=float(rec['score'])
        rec['score_provenance']=family_score_provenance(
            rec['a'],rec['b'],tables,rec.get('prime_bound') or bounds[-1],args.score_method
        )
        rec['score_provenance']['ranking_kind']=args.score_method
    adapter=load_adapter(plugin)
    exact_rows=[]; seen_native=set()
    for rec in rows:
        chart_parameter=f"{rec['a']}/{rec['b']}"
        parameter=chart_parameter
        native_parameter=chart_parameter
        reduction=None
        if chart:
            canonical, reduction = chart.canonicalize(chart_parameter)
            # Optional plugin callback may supply a stronger proven exact canonicalizer.
            callback=getattr(adapter,'canonicalize_chart_parameter',None) if adapter is not None else None
            if callable(callback):
                canonical = callback(chart.id, str(canonical))
            parameter=str(canonical)
            native_parameter=str(chart.forward(parameter))
            # Exact round trip is a hard contract check.
            if str(chart.inverse(native_parameter)) != str(canonical):
                raise SystemExit(f'chart {chart.id} failed exact QQ round trip at {parameter}')
            key=(chart.native_family_key,native_parameter)
            if key in seen_native:
                continue
            seen_native.add(key)
            rec['chart_id']=chart.id
            rec['chart_parameter']=parameter
            rec['native_family_key']=chart.native_family_key
            rec['native_variant_id']=chart.native_variant_id
            rec['native_family_spec']=chart.native_family_spec
            rec['native_parameter']=native_parameter
            rec['chart_map_fingerprint']=chart.fingerprint
            rec['chart_pgl2_matrix']=list(chart.matrix)
            if reduction and reduction.get('input') != reduction.get('canonical'):
                rec['symmetry_reduction']=reduction
        else:
            rec['native_family_key']=f'{plugin.id}:{variant.id}'
            rec['native_variant_id']=variant.id
            rec['native_family_spec']=variant.family_spec
            rec['native_parameter']=parameter
        rec['t']=parameter
        # Preserve exact a/b for the parameter actually emitted, not the pre-reduction scan pair.
        q=Fraction(str(parameter)); rec['a']=q.numerator; rec['b']=q.denominator
        rec['family_spec']=variant.family_spec; rec['plugin_id']=plugin.id; rec['plugin_variant']=variant.id
        rec['search_engine']=args.engine; rec['stage_bounds']=bounds
        rec['family_name']=family.name()
        if symmetry: rec['parameter_symmetry']=symmetry
        exact_rows.append(rec)
    rows=exact_rows

    try:
        rows, corpus_summary = apply_candidate_corpus_policy(
            project, plugin, variant, rows, args.corpus_policy
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if corpus_summary["policy"] != "off" and corpus_summary["declared"]:
        if not corpus_summary["ready"]:
            print(
                f'[corpus] policy={corpus_summary["policy"]} '
                f'ready=0/{corpus_summary["declared"]} '
                '— corpus unavailable; candidates left unclassified'
            )
        else:
            print(
                f'[corpus] policy={corpus_summary["policy"]} '
                f'ready={corpus_summary["ready"]}/{corpus_summary["declared"]} '
                f'known={corpus_summary["known"]} retained={corpus_summary["retained"]}'
            )

    count=replace_pool_rows(db,pool['id'],rows)
    if args.export_jsonl:
        path=export_pool_jsonl(db,pool['id'],args.export_jsonl)
        db.execute('UPDATE candidate_pools SET source_path=?,updated_at=? WHERE id=?',(str(path),now(),pool['id'])); db.commit()
        print(f'[export] {path}')
    print(f'RANK_HUNTER_CANDIDATE_POOL={{"pool_id":{int(pool["id"])},"candidates":{count}}}')

if __name__=='__main__': main()
