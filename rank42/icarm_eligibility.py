"""ICARM's top-ten entry rule, evaluated against a public board snapshot.

Mirrors icarm/elliptic-rank src/gate.ts. This is a local forecast;
the server judges entry against its live board when a submission arrives.
"""
from __future__ import annotations

import json
import math
from bisect import bisect_left


def indexed_entry_board(board):
    """Opt into cached sorted metrics for an immutable catalog snapshot.

    Build a new wrapper if the catalog changes. The input dictionary and
    curve records are left intact; only the wrapper contains the cache.
    """
    result = dict(board)
    result['_rank42_entry_cache'] = {}
    return result


def cheap_conductor_bounds(M, *, known_conductor=None, trial_bound=1000):
    """Bound N using small-prime local data; never factor a large integer.

    Ogg's formula gives f_p <= v_p(Delta_min). For unprocessed primes,
    retain their entire discriminant contribution as a safe upper bound.
    """
    if known_conductor is not None:
        N = int(known_conductor)
        if N < 1:
            raise ValueError('conductor must be positive')
        return {'status': 'exact', 'lower': str(N), 'upper': str(N),
                'log_lower': math.log(N), 'log_upper': math.log(N),
                'method': 'reused_exact_conductor'}
    from sage.all import ZZ, prime_range
    remaining = ZZ(abs(M.discriminant()))
    known = ZZ(1)
    local = []
    F = M.pari_curve()
    for p in prime_range(2, trial_bound + 1):
        if remaining % p:
            continue
        valuation = 0
        while remaining % p == 0:
            remaining //= p
            valuation += 1
        exponent = int(F.elllocalred(p)[0])
        if not 0 <= exponent <= valuation:
            raise ArithmeticError('conductor/discriminant local inequality failed')
        known *= p**exponent
        local.append({'prime': str(p), 'discriminant_exponent': valuation,
                      'conductor_exponent': exponent})
    upper = known*remaining
    return {'status': 'exact' if remaining == 1 else 'bounded',
            'lower': str(known), 'upper': str(upper), 'log_lower': math.log(int(known)),
            'log_upper': math.log(int(upper)), 'unfactored_discriminant_part': str(remaining),
            'local_data': local, 'trial_bound': trial_bound,
            'method': 'small_prime_local_exponents_and_discriminant_upper_bound'}


def entry_placement(board, *, rank, torsion, discriminant, naive_height,
                    faltings_height=None, conductor=None):
    def normalized_torsion(value):
        return json.loads(value) if isinstance(value, str) else value

    torsion = normalized_torsion(torsion)
    metrics = {'disc': abs(int(discriminant)), 'naive': float(naive_height),
               'faltings': float(faltings_height) if faltings_height is not None else None,
               'conductor': int(conductor) if conductor is not None else None}
    fields = {'disc': 'discriminant', 'naive': 'naive_height',
              'faltings': 'faltings_height', 'conductor': 'conductor'}
    cache = board.get('_rank42_entry_cache')
    indexed = None
    key = (int(rank), json.dumps(torsion, separators=(',',':')))
    if cache is not None:
        indexed = cache.get(key)
    if indexed is None:
        rivals = [r for r in board['curves'] if int(r['rank_lower_bound']) >= int(rank)]
        pools = {'overall': rivals, 'same_torsion': [
            r for r in rivals if normalized_torsion(r.get('torsion')) == torsion]}
        if cache is not None:
            indexed = {}
            for pool, rows in pools.items():
                indexed[pool] = {}
                for metric, field in fields.items():
                    values = [r[field] for r in rows if r.get(field) is not None]
                    convert = (lambda v: abs(int(v))) if metric=='disc' else int if metric=='conductor' else float
                    indexed[pool][metric] = sorted(convert(v) for v in values)
            cache[key] = indexed
    placements = {}
    for name in ('overall','same_torsion'):
        places = {}
        for metric, value in metrics.items():
            if value is None:
                places[metric] = None
                continue
            if indexed is not None:
                places[metric] = 1+bisect_left(indexed[name][metric],value)
                continue
            def smaller(row):
                raw = row.get(fields[metric])
                if raw is None:
                    return False
                other = abs(int(raw)) if metric == 'disc' else int(raw) if metric == 'conductor' else float(raw)
                return other < value
            places[metric] = 1 + sum(smaller(r) for r in pools[name])
        placements[name] = places
    qualifying = [f'{pool}.{metric}' for pool, places in placements.items()
                  for metric, place in places.items() if place is not None and place <= 10]
    return {'rank': int(rank), 'qualifies': bool(qualifying), 'qualifying_metrics': qualifying,
            'placements': placements, 'conductor_known': conductor is not None,
            'status': 'qualifies' if qualifying else 'does_not_qualify_on_known_metrics'}


def entry_at_certified_rank(profile, board, rank):
    """Judge the actual certified bound, separately from a search target."""
    metrics = profile['metrics']
    return entry_placement(
        board, rank=rank, torsion=profile['torsion'],
        discriminant=metrics['discriminant'], naive_height=metrics['naive_height'],
        faltings_height=metrics.get('faltings_height'), conductor=metrics.get('conductor'),
    )


def minimal_model_entry_profile(E, board, *, target_rank=31, minimum_rank=20,
                                minimal_model=None, conductor=None, torsion=None, log_conductor_cap=None):
    """Compute sizes without factoring the discriminant or computing conductor.

    Admission forecasts use exact metrics. Separately, small-prime local data
    supplies a rigorous conductor upper bound for the search size policy.
    """
    from sage.all import EllipticCurve, QQ
    from rank42.curve_size_metrics import compute_curve_size_metrics

    M = minimal_model
    if M is None:
        F = E.pari_curve().ellminimalmodel()
        # cypari2 returns (minimal curve, change of variables) for the
        # optional output pointer in ellminimalmodel.
        if F[0].type() == 't_VEC':
            F = F[0]
        M = EllipticCurve(QQ, [QQ(F[i]) for i in range(5)])
    metrics = compute_curve_size_metrics(M, precision_bits=128)
    bounds = cheap_conductor_bounds(M, known_conductor=conductor)
    conductor_screen = ('not_requested' if log_conductor_cap is None else
                        'within_cap' if bounds['log_upper'] <= log_conductor_cap else
                        'above_cap' if bounds['log_lower'] > log_conductor_cap else 'unconfirmed')
    if torsion is None:
        torsion = [int(v) for v in M.pari_curve().elltors()[1]]
    arguments = dict(torsion=torsion, discriminant=str(M.discriminant()),
                     naive_height=metrics['naive_height'], faltings_height=metrics['faltings_height'],
                     conductor=conductor)
    target = entry_placement(board, rank=target_rank, **arguments)
    first = next((r for r in range(minimum_rank, target_rank + 1)
                  if entry_placement(board, rank=r, **arguments)['qualifies']), None)
    return {'target_rank': int(target_rank), 'target_qualifies': target['qualifies'],
            'conductor_bounds': bounds, 'log_conductor_cap': log_conductor_cap,
            'conductor_screen': conductor_screen,
            'minimum_qualifying_rank': first, 'target_judgement': target,
            'minimal_ainvs': [str(v) for v in M.a_invariants()], 'torsion': torsion,
            'metrics': dict(metrics, discriminant=str(M.discriminant()), conductor=conductor),
            'policy': 'require_top_ten_on_a_known_metric'}
