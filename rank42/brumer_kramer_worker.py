"""Isolated Brumer-Kramer class-group / 2-Selmer bound worker."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from sage.all import QQ, EllipticCurve, NumberField
from sage.schemes.elliptic_curves.ell_local_data import check_prime

RESULT_MARKER = "RANK42_BRUMER_KRAMER_RESULT="


def _sage_version():
    try:
        from sage.version import version
        return str(version)
    except Exception:
        return None


def _emit(payload):
    print(RESULT_MARKER + json.dumps(payload, sort_keys=True), flush=True)


def main():
    payload = json.loads(sys.stdin.read())
    class_group_proof = bool(payload.get("class_group_proof", False))

    E = EllipticCurve(
        QQ,
        [QQ(str(x)) for x in payload["a_invariants"]],
    ).global_minimal_model()

    if int(E.two_torsion_rank()) != 0:
        _emit({
            "status": "unsupported",
            "reason": "brumer_kramer_requires_E_Q_2_zero",
            "two_torsion_rank": int(E.two_torsion_rank()),
            "minimal_model_a_invariants": [str(x) for x in E.a_invariants()],
        })
        return

    f = E.two_division_polynomial().monic()
    if not bool(f.is_irreducible()):
        _emit({
            "status": "unsupported",
            "reason": "two_division_polynomial_not_irreducible",
            "two_division_polynomial": str(f),
            "minimal_model_a_invariants": [str(x) for x in E.a_invariants()],
        })
        return

    K = NumberField(f, "a")
    started = datetime.now(timezone.utc).isoformat()
    class_group = K.class_group(proof=class_group_proof)
    finished = datetime.now(timezone.utc).isoformat()

    invariants = [int(x) for x in class_group.invariants()]
    class_group_2_rank = sum(1 for n in invariants if n % 2 == 0)

    curve_discriminant = int(E.discriminant())
    u_term = 1 if curve_discriminant < 0 else 2
    phi_m = []
    phi_a = []
    additive_splitting = {}
    local_records = []
    n_term = 0

    for local in E.local_data(proof=True):
        prime = int(check_prime(QQ, local.prime()))
        reduction_type = local.bad_reduction_type()
        discriminant_valuation = int(local.discriminant_valuation())
        rec = {
            "prime": prime,
            "bad_reduction_type": (
                None if reduction_type is None else int(reduction_type)
            ),
            "discriminant_valuation": discriminant_valuation,
            "kodaira_symbol": str(local.kodaira_symbol()),
        }
        if reduction_type in (-1, 1):
            rec["reduction"] = "multiplicative"
            if discriminant_valuation % 2 == 0:
                phi_m.append(prime)
                n_term += 1
                rec["in_phi_m"] = True
            else:
                rec["in_phi_m"] = False
        elif reduction_type == 0:
            rec["reduction"] = "additive"
            phi_a.append(prime)
            prime_factors = list(K.factor(prime))
            primes_above = len(prime_factors)
            additive_splitting[str(prime)] = {
                "primes_above": primes_above,
                "factorization": [
                    {
                        "ramification_index": int(e),
                        "residue_degree": int(P.residue_class_degree()),
                    }
                    for P, e in prime_factors
                ],
            }
            n_term += primes_above - 1
            rec["primes_above_in_cubic_field"] = primes_above
        else:
            rec["reduction"] = "good"
        local_records.append(rec)

    selmer_upper = int(class_group_2_rank + u_term + n_term)
    assumptions = (
        []
        if class_group_proof
        else ["Generalized Riemann Hypothesis for class-group certification"]
    )

    _emit({
        "status": "completed",
        "engine": "rank42.brumer_kramer",
        "engine_version": "1",
        "sage_version": _sage_version(),
        "theorem": "Brumer-Kramer Proposition 7.1",
        "theorem_scope": "E(Q)[2]=0",
        "class_group_proof": class_group_proof,
        "assumptions": assumptions,
        "minimal_model_a_invariants": [str(x) for x in E.a_invariants()],
        "curve_discriminant": str(curve_discriminant),
        "two_torsion_rank": 0,
        "two_division_polynomial": str(f),
        "cubic_field_polynomial": str(K.defining_polynomial()),
        "cubic_field_discriminant": str(K.discriminant()),
        "class_group_order": str(class_group.order()),
        "class_group_invariants": invariants,
        "class_group_2_rank": int(class_group_2_rank),
        "u_term": int(u_term),
        "phi_m": sorted(phi_m),
        "phi_a": sorted(phi_a),
        "additive_splitting": additive_splitting,
        "local_records": local_records,
        "n_term": int(n_term),
        "selmer_2_dimension_upper": selmer_upper,
        "mordell_weil_rank_upper": selmer_upper,
        "started_at": started,
        "finished_at": finished,
    })


if __name__ == "__main__":
    main()
