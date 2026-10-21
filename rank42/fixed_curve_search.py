"""Family-free rational-point search for one stored elliptic curve.

This is the v0.8 Target Curve fallback.  It does not assume a generic family or
subgroup.  It searches a short Weierstrass model with ratpoints, maps hits back
exactly, stores every exact point, and uses the exact lower-bound certificate
only when a stored rigorous witness basis exists.
"""
from __future__ import annotations
import argparse, json, time
from fractions import Fraction

from sage.all import QQ, EllipticCurve
from rank42.db import connect,get_curve,log_event,proven_lower
from rank42.exact_lb import ExactCertificateFailure,ExactCertificateTimeout,run_exact_certificate
from rank42.point_promotion import promote_certified_subgroup
from rank42.points import rigorous_witness_basis, upsert_point
from rank42.model_prep import ModelPrepFailure, ModelPrepTimeout, run_global_minimal_model
from rank42.ratpoints import RatpointsFailure,RatpointsNotFound,RatpointsTimeout,probe_version,run_ratpoints
from rank42.search_models import completed_square_polynomial, recover_weierstrass_y

MARKER='RANK42_FIXED_CURVE_SEARCH_RESULT='

def _stages(text):
    vals=sorted(set(int(x.strip()) for x in str(text).split(',') if x.strip()))
    if not vals or vals[0]<=0: raise argparse.ArgumentTypeError('positive comma-separated stages required')
    return vals

def _key(P):
    Q=-P
    return min((QQ(P[0]),QQ(P[1])),(QQ(Q[0]),QQ(Q[1])))

def parse_args():
    ap=argparse.ArgumentParser(description='Family-free ratpoints search on one stored curve')
    ap.add_argument('--db',default='rank42.db'); ap.add_argument('--curve-id',type=int,required=True)
    ap.add_argument('--stages',type=_stages,default=_stages('1000,10000,100000'))
    ap.add_argument('--timeout',type=int,default=20); ap.add_argument('--ratpoints')
    ap.add_argument('--exact-candidates',type=int,default=8); ap.add_argument('--certificate-timeout',type=int,default=120)
    ap.add_argument('--rank-cert-timeout',type=int,default=0)
    ap.add_argument('--model-prep-timeout',type=int,default=10,help='hard timeout for global minimal-model preparation')
    ap.add_argument('--legacy-resume',action='store_true',help=argparse.SUPPRESS)
    return ap.parse_args()

def _certify_exact_candidates(
    db,
    *,
    curve_id,
    E,
    rigorous_basis,
    candidates,
    exact_candidates,
    certificate_timeout,
):
    basis=list(rigorous_basis)
    growth=0
    attempts=0
    known={_key(P) for P in basis}
    for P in list(candidates)[:max(0,int(exact_candidates))]:
        attempts+=1
        trial=basis+[P]
        try:
            cert=run_exact_certificate(
                E.a_invariants(),
                trial,
                timeout=certificate_timeout,
            )
        except (ExactCertificateTimeout,ExactCertificateFailure) as exc:
            print(f'[exact] candidate {attempts}: inconclusive {exc}')
            continue
        status=str(cert.get('status') or 'unknown')
        print(f'[exact] candidate {attempts}: {status}')
        if cert.get('independent'):
            promoted=promote_certified_subgroup(
                db,
                curve_id=curve_id,
                E=E,
                points=trial,
                source='fixed_curve_ratpoints',
                search_ref='fixed_curve:exact',
                certificate=cert.get('certificate'),
                metadata={'fixed_curve_search': True},
                engine='fixed_curve_exact_certificate',
            )
            basis=trial
            growth+=1
            known.add(_key(P))
            if promoted['rank_inconsistent']:
                log_event(
                    db,
                    curve_id,
                    'error',
                    (
                        'Family-free fixed-curve exact certificate conflicts with '
                        'existing rigorous rank evidence'
                    ),
                )
            else:
                log_event(
                    db,
                    curve_id,
                    'best',
                    (
                        'Family-free fixed-curve exact certificate improves '
                        f'rigorous lower bound to {len(basis)}'
                    ),
                )
    return basis,growth,attempts

def main():
    args=parse_args()
    if not args.legacy_resume:
        raise SystemExit(
            'standalone FREE Target Search is retired; start new work from '
            'Target, or resume an existing native job from Jobs'
        )
    db=connect(args.db); row=get_curve(db,args.curve_id)
    if row is None or not row['a_invariants_json']: raise SystemExit(f'curve #{args.curve_id} unavailable or lacks a-invariants')
    print('RANK HUNTER TARGET CURVE · FREE', flush=True)
    print('='*62, flush=True)
    print('curve id =',row['id'], flush=True)
    print('current rigorous lower =',proven_lower(row), flush=True)
    print('[model] constructing stored elliptic curve', flush=True)
    E=EllipticCurve(QQ,[QQ(str(x)) for x in json.loads(row['a_invariants_json'])])
    print('stored model =',list(E.a_invariants()), flush=True)

    # Prefer a minimal model when it can be obtained cheaply, but search a
    # completed-square cubic rather than converting to a short Weierstrass
    # model.  The completed-square transform preserves x exactly:
    #   W = 2*y + a1*x + a3
    #   W^2 = 4*x^3 + b2*x^2 + 2*b4*x + b6.
    # If minimization is expensive, use the stored model directly.
    search_curve=E
    search_to_stored=lambda P: E(P)
    search_model_label='stored'
    try:
        print(f'[model] global minimal model · hard timeout={int(args.model_prep_timeout)}s', flush=True)
        prepared=run_global_minimal_model(E.a_invariants(),timeout=max(1,int(args.model_prep_timeout)))
        Em=EllipticCurve(QQ,[QQ(str(x)) for x in prepared['a_invariants']])
        minimal_to_stored=Em.isomorphism_to(E)
        search_curve=Em
        search_to_stored=lambda P: minimal_to_stored(P)
        search_model_label='minimal'
        print(f'[model] minimal model ready in {prepared["runtime"]:.3f}s', flush=True)
        print('minimal model =',list(Em.a_invariants()), flush=True)
    except ModelPrepTimeout as exc:
        print(f'[model] TIMEOUT {exc}; searching completed-square cubic of stored model', flush=True)
    except ModelPrepFailure as exc:
        print(f'[model] ERROR {exc}; searching completed-square cubic of stored model', flush=True)
    except Exception as exc:
        print(f'[model] WARNING could not use minimal model ({exc}); searching stored model', flush=True)

    search_ainvs=list(search_curve.a_invariants())
    poly=completed_square_polynomial(search_ainvs)
    print(
        '[model] completed-square search model (%s): W^2 = (%s) + (%s)x + (%s)x^2 + (%s)x^3'
        % (search_model_label, poly[0], poly[1], poly[2], poly[3]),
        flush=True,
    )
    try:
        print('[ratpoints] probing executable', flush=True)
        rp=probe_version(args.ratpoints)
    except RatpointsNotFound as exc: raise SystemExit(str(exc))
    except RatpointsTimeout as exc: raise SystemExit(str(exc))
    print(f'[ratpoints] executable = {rp["executable"]}', flush=True)
    found={}
    for H in args.stages:
        try:
            res=run_ratpoints(poly,H,executable=rp['executable'],timeout=args.timeout)
        except RatpointsTimeout:
            print(f'[ratpoints] H={H} TIMEOUT', flush=True); continue
        except RatpointsFailure as exc:
            print(f'[ratpoints] H={H} ERROR {exc}', flush=True); continue
        added=0
        for q in res['points']:
            try:
                xq=QQ(str(q.x))
                wq=QQ(str(q.y))
                yq=QQ(str(recover_weierstrass_y(search_ainvs, xq, wq)))
                Ps=search_curve(xq,yq)
                P=search_to_stored(Ps)
            except Exception:
                continue
            if P.is_zero(): continue
            k=_key(P)
            if k in found: continue
            found[k]=P; added+=1
            upsert_point(db,curve_id=row['id'],x=P[0],y=P[1],source='fixed_curve_ratpoints',role='candidate_extra',exact_verified=True,independence_status='unknown',search_ref=f'fixed_curve:H={H}')
        print(f'[ratpoints] H={H} total={len(res["points"])} new_exact={added}', flush=True)
    rigorous_basis,witness_lower,witness_complete=rigorous_witness_basis(db,row['id'],E)
    print(f'[exact] rigorous point-ledger basis = {len(rigorous_basis)}/{witness_lower}')
    growth=0; attempts=0
    known={_key(P) for P in rigorous_basis}
    candidates=[P for k,P in found.items() if k not in known]
    if witness_complete:
        rigorous_basis,growth,attempts=_certify_exact_candidates(
            db,
            curve_id=row['id'],
            E=E,
            rigorous_basis=rigorous_basis,
            candidates=candidates,
            exact_candidates=args.exact_candidates,
            certificate_timeout=args.certificate_timeout,
        )
    else:
        print('[exact] rigorous point-ledger witness basis is incomplete; exact points saved without rank promotion')
    result={'curve_id':int(row['id']),'exact_points':len(found),'exact_attempts':attempts,'rank_growth':growth,'rigorous_lower':max(int(proven_lower(get_curve(db,row['id']))),len(rigorous_basis))}
    print(MARKER+json.dumps(result,sort_keys=True))

if __name__=='__main__': main()
