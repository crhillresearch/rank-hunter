"""Bounded specialization + quick rank-bound worker.

This worker exists because constructing a large specialization or reducing it to
minimal form can itself be expensive.  The controller can therefore put the
*entire* quick-screen operation behind one subprocess timeout.
"""

import json
import sys

from sage.all import QQ

from rank42.family_loader import load_family

AINVS_MARKER = "RANK42_AINVS="
RESULT_MARKER = "RANK42_SCREEN_JSON="


def runtime_meta(engine):
    sage_version = None
    engine_version = None
    try:
        from sage.version import version as sage_version_value
        sage_version = str(sage_version_value)
    except Exception:
        pass
    if engine == "pari":
        try:
            from sage.all import pari
            try:
                engine_version = str(pari("version()"))
            except Exception:
                engine_version = sage_version
        except Exception:
            pass
    else:
        engine_version = sage_version
    return {"sage_version": sage_version, "engine_version": engine_version}


def main():
    payload = json.loads(sys.stdin.read())
    family = load_family(payload["family"], need_sections=False)
    t = QQ(str(payload["parameter"]))
    strategy = payload.get("strategy", "pari")

    make_curve = getattr(family, "screen_curve", None)
    if not callable(make_curve):
        make_curve = family.curve

    E = make_curve(t)
    if E is None:
        raise SystemExit("singular/undefined specialization")

    ainvs = [str(a) for a in E.a_invariants()]
    # Emit and flush before descent.  If the rank bound times out, the parent
    # can still persist a model for later explicit analysis.
    print(AINVS_MARKER + json.dumps(ainvs), flush=True)

    if strategy == "pari":
        # Rank evidence is normalized to the global minimal model.  The entire
        # conversion remains inside this worker's hard timeout.
        E = E.global_minimal_model()
        ainvs = [str(a) for a in E.a_invariants()]
        upper = int(E.rank_bound(algorithm="pari"))
        engine = "sage_rank_bound_pari"
    elif strategy == "mwrank":
        # mwrank/eclib needs an integral model; normalize fully for provenance.
        E = E.global_minimal_model()
        ainvs = [str(a) for a in E.a_invariants()]
        upper = int(E.rank_bound(algorithm="mwrank"))
        engine = "sage_rank_bound_mwrank"
    else:
        raise SystemExit(f"unsupported quick strategy: {strategy}")

    result = {
        "upper": upper,
        "strategy": engine,
        "a_invariants": ainvs,
        "screen_model": getattr(family, "screen_model_name", lambda: "family_curve")()
        if callable(getattr(family, "screen_model_name", None)) else "family_curve",
        **runtime_meta(strategy),
    }
    print(RESULT_MARKER + json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
