"""Full local checks and explicitly heuristic search plans for coverings."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from fractions import Fraction
from pathlib import Path

from sage.all import PolynomialRing, QQ, sage_eval

from rank42.lattice_store import update_covering, update_covering_metadata
from rank42.pointed_quartic import quartic_complexity


WORKER = Path(__file__).with_name("covering_local_worker.py")
MARKER = "RANK42_COVERING_LOCAL="


class CoveringLocalFailure(RuntimeError):
    pass


class CoveringLocalTimeout(CoveringLocalFailure):
    def __init__(self, message, *, runtime=None):
        super().__init__(message)
        self.runtime = runtime


def run_covering_local_analysis(coefficients, *, timeout=30):
    timeout = max(1, int(timeout))
    started = time.monotonic()
    try:
        cp = subprocess.run(
            [sys.executable, str(WORKER)],
            input=json.dumps({"coefficients": list(coefficients)}),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CoveringLocalTimeout(
            f"covering local analysis exceeded {timeout}s",
            runtime=time.monotonic() - started,
        ) from exc
    marker = None
    for line in reversed((cp.stdout or "").splitlines()):
        if line.startswith(MARKER):
            marker = line[len(MARKER):]
            break
    if marker is None:
        tail = (cp.stderr or cp.stdout or "").strip()[-2000:]
        raise CoveringLocalFailure(
            f"covering local worker returned no result (exit={cp.returncode}): {tail}"
        )
    try:
        result = json.loads(marker)
    except Exception as exc:
        raise CoveringLocalFailure("covering local worker returned invalid JSON") from exc
    if cp.returncode != 0 or result.get("status") == "error":
        raise CoveringLocalFailure(str(result.get("error") or "worker failed"))
    result["elapsed_seconds"] = time.monotonic() - started
    return result


def _q(value):
    return value if isinstance(value, Fraction) else Fraction(str(value))


def _bits(value):
    value = _q(value)
    return max(
        max(1, abs(value.numerator).bit_length()),
        max(1, value.denominator.bit_length()),
    )


def _map_profile(mapping):
    ring = PolynomialRing(QQ, names=("u", "v"))
    field = ring.fraction_field()
    u, v = (field(x) for x in ring.gens())
    profile = []
    for coordinate in ("x", "y"):
        value = field(sage_eval(str(mapping[coordinate]), locals={"u": u, "v": v}))
        numerator = value.numerator()
        denominator = value.denominator()
        coefficients = list(numerator.coefficients()) + list(denominator.coefficients())
        profile.append({
            "coordinate": coordinate,
            "degree": max(int(numerator.total_degree()), int(denominator.total_degree())),
            "coefficient_bits": max((_bits(x) for x in coefficients), default=1),
        })
    return {
        "max_degree": max(item["degree"] for item in profile),
        "max_coefficient_bits": max(item["coefficient_bits"] for item in profile),
        "coordinates": profile,
    }


def covering_height_plan(
    coefficients,
    mapping,
    *,
    discriminant,
    base_height=10000,
    max_height=10000000,
    base_timeout=30,
    max_timeout=300,
):
    coefficient_complexity = quartic_complexity(coefficients)
    map_profile = _map_profile(mapping)
    discriminant_bits = max(1, abs(int(discriminant)).bit_length())
    score = (
        int(coefficient_complexity[0])
        + discriminant_bits // 4
        + map_profile["max_coefficient_bits"] // 2
        + 8 * map_profile["max_degree"]
    )
    tier = min(3, max(0, score // 64))
    labels = ("easy", "moderate", "hard", "extreme")
    height = min(max(1, int(max_height)), max(1, int(base_height)) * (10**tier))
    timeout = min(max(1, int(max_timeout)), max(1, int(base_timeout)) * (tier + 1))
    return {
        "kind": "heuristic_covering_search_plan",
        "recommended_search": True,
        "rigorous_height_bound": False,
        "difficulty_score": score,
        "difficulty_tier": labels[tier],
        "recommended_height": height,
        "recommended_timeout": timeout,
        "coefficient_complexity": list(coefficient_complexity),
        "discriminant_bits": discriminant_bits,
        "map_profile": map_profile,
        "formula_version": 1,
    }


def _metadata(row):
    try:
        value = json.loads(row["metadata_json"] or "{}")
    except Exception:
        value = {}
    return value if isinstance(value, dict) else {}


def plan_covering_searches(
    db,
    *,
    curve_id,
    max_coverings=16,
    timeout=30,
    base_height=10000,
    max_height=10000000,
    base_timeout=30,
    max_timeout=300,
    reject_locally_insoluble=True,
):
    rows = db.execute(
        """SELECT * FROM coverings
           WHERE curve_id=? AND status IN ('ready','searched','mapped')
           ORDER BY id LIMIT ?""",
        (int(curve_id), max(1, int(max_coverings))),
    ).fetchall()
    branches = []
    soluble = []
    for row in rows:
        covering_id = int(row["id"])
        quartic = json.loads(row["quartic_json"])
        mapping = {"x": row["map_x"], "y": row["map_y"]}
        try:
            local = run_covering_local_analysis(
                quartic["coefficients"], timeout=timeout
            )
            status = str(local.get("status") or "")
            if status == "unsupported":
                branch = {
                    "covering_id": covering_id,
                    "status": "unsupported",
                    "reason": local.get("reason"),
                    "local_analysis": local,
                }
            elif status == "locally_insoluble":
                plan = {
                    "recommended_search": False,
                    "reason": "rigorous_local_obstruction",
                }
                branch = {
                    "covering_id": covering_id,
                    "status": "completed",
                    "local_status": status,
                    "rigorous_rejection": True,
                    "obstruction": local.get("obstruction"),
                    "height_plan": plan,
                }
                metadata = _metadata(row)
                metadata["covering_local_height_plan"] = {
                    "local_analysis": local,
                    "height_plan": plan,
                    "proof_scope": "all_completions_of_Q",
                }
                update_covering_metadata(db, covering_id, metadata)
                if reject_locally_insoluble:
                    update_covering(db, covering_id, status="locally_obstructed")
            elif status == "everywhere_locally_soluble" and bool(local.get("all_places_checked")):
                plan = covering_height_plan(
                    quartic["coefficients"],
                    mapping,
                    discriminant=local["discriminant"],
                    base_height=base_height,
                    max_height=max_height,
                    base_timeout=base_timeout,
                    max_timeout=max_timeout,
                )
                branch = {
                    "covering_id": covering_id,
                    "status": "completed",
                    "local_status": status,
                    "rigorous_rejection": False,
                    "height_plan": plan,
                }
                soluble.append((plan["difficulty_score"], covering_id, branch, row, local))
            else:
                raise CoveringLocalFailure("worker did not attest complete local coverage")
            branches.append(branch)
        except CoveringLocalTimeout as exc:
            branches.append({
                "covering_id": covering_id,
                "status": "timeout",
                "error": str(exc),
                "elapsed_seconds": exc.runtime,
            })
        except Exception as exc:
            branches.append({
                "covering_id": covering_id,
                "status": "error",
                "error": repr(exc),
            })

    for priority, (_score, covering_id, branch, row, local) in enumerate(
        sorted(soluble), 1
    ):
        branch["height_plan"]["search_priority"] = priority
        metadata = _metadata(row)
        metadata["covering_local_height_plan"] = {
            "local_analysis": local,
            "height_plan": branch["height_plan"],
            "proof_scope": "all_completions_of_Q",
        }
        update_covering_metadata(db, covering_id, metadata)

    completed = sum(x["status"] == "completed" for x in branches)
    timeouts = sum(x["status"] == "timeout" for x in branches)
    errors = sum(x["status"] == "error" for x in branches)
    unsupported = sum(x["status"] == "unsupported" for x in branches)
    obstructed = sum(bool(x.get("rigorous_rejection")) for x in branches)
    if not rows:
        outer, reason = "inconclusive", "no_exact_coverings_available"
    elif completed == len(branches):
        outer, reason = "completed", None
    elif completed:
        outer, reason = "partial", "covering_local_coverage_partial"
    elif timeouts and not errors:
        outer, reason = "timeout", "covering_local_analysis_timeout"
    elif unsupported == len(branches):
        outer, reason = "unsupported", "no_supported_covering_models"
    else:
        outer, reason = "error", "covering_local_analysis_failed"
    return {
        "status": outer,
        "reason": reason,
        "module_label": "Local Solubility & Covering Height Planner",
        "coverings_seen": len(rows),
        "completed_branches": completed,
        "timeout_branches": timeouts,
        "error_branches": errors,
        "unsupported_branches": unsupported,
        "locally_obstructed": obstructed,
        "branches": branches,
        "local_proof_scope": "all_completions_of_Q_when_completed",
        "height_guidance_is_rigorous": False,
        "rank_evidence_created": False,
        "retryable": bool(timeouts or errors),
        "attempt_complete": True,
        "mathematical_outcome": outer,
    }
