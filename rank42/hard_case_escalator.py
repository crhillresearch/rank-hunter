"""Autonomous exact-resolution ladder for bookmarked hard point cases.

The escalator is deliberately conservative: every mathematical promotion is
performed by an existing exact Rank Hunter proof path. The orchestrator only
chooses the next method, records what happened, and stops as soon as the
candidate or the curve is rigorously resolved.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from rank42.analysis_method_history import (
    basis_fingerprint_from_curve_row as _basis_fingerprint_from_row,
    budget_dominates as _budget_dominates,
    prior_stage_attempt as _prior_stage_attempt,
    latest_basis_fingerprint_before as _latest_basis_fingerprint_before,
    stage_options as _stage_options,
)
from rank42.classical_descent import (
    ClassicalDescentFailure,
    ClassicalDescentTimeout,
    run_classical_descent,
)
from rank42.db import connect, get_curve, log_event, proven_lower, update_curve
from rank42.point_promotion import promote_exact_rank_certificate
from rank42.points import rigorous_witness_basis, set_point_hard_flag, upsert_point
from rank42.rank_cert import (
    RankCertificationFailure,
    RankCertificationTimeout,
    run_rank_certification,
)
from rank42.rank_evidence import (
    record_rank_evidence,
    reduce_rank_state,
)

MARKER = "RANK42_HARD_CASE_ESCALATOR_RESULT="
COMPAT_MARKER = "RANK42_INDEPENDENCE_RESULT="
ENGINE = "rank42.hard_case_escalator"
ENGINE_VERSION = "3"


def parse_args():
    ap = argparse.ArgumentParser(description="Autonomous hard-case exact-resolution ladder")
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--curve-id", type=int, required=True)
    ap.add_argument("--point-id", type=int, action="append", default=[])
    ap.add_argument("--certificate-timeout", type=int, default=600)
    ap.add_argument("--max-halvings", type=int, default=250)
    ap.add_argument("--max-prime", type=int, default=2000000)
    ap.add_argument("--max-columns", type=int, default=1280)
    ap.add_argument("--basis-saturation-timeout", type=int, default=600)
    ap.add_argument("--trial-saturation-timeout", type=int, default=300)
    ap.add_argument("--saturation-probe-timeout", type=int, default=90)
    ap.add_argument("--deep-saturation", action="store_true")
    ap.add_argument("--saturation-only", action="store_true")
    ap.add_argument("--descent-timeout", type=int, default=300)
    ap.add_argument("--rank-cert-timeout", type=int, default=180)
    ap.add_argument("--saturation-primes", default="2,3,5,7")
    return ap.parse_args()


def _metadata(rec):
    try:
        value = json.loads(rec["metadata_json"] or "{}")
    except Exception:
        value = {}
    return value if isinstance(value, dict) else {}


def _point_row(db, curve_id, point_id):
    return db.execute(
        "SELECT * FROM points WHERE id=? AND curve_id=?",
        (int(point_id), int(curve_id)),
    ).fetchone()


def _resolution(rec):
    if rec is None:
        return "missing"
    if int(rec["rigorous_independent"] or 0):
        return "independent"
    if str(rec["independence_status"] or "") == "dependent":
        return "dependent"
    return None


def _active_basis_count(row):
    try:
        raw = json.loads(row["generators_json"] or "[]")
    except Exception:
        raw = []
    return len(raw) if isinstance(raw, list) else 0



def _record_stage_attempt(
    db,
    *,
    curve_id,
    model,
    method,
    basis_fingerprint,
    budget,
    status,
    point_id=None,
    prime=None,
    detail=None,
):
    options = {
        "source": "hard_case_escalator",
        "method": str(method),
        "basis_fingerprint": basis_fingerprint,
        "budget": dict(budget or {}),
        "point_id": None if point_id is None else int(point_id),
        "prime": None if prime is None else int(prime),
    }
    return record_rank_evidence(
        db,
        curve_id=int(curve_id),
        model=model,
        data={
            "engine": ENGINE,
            "engine_version": ENGINE_VERSION,
            "evidence_type": "hard_case_stage",
            "status": str(status),
            "rigorous": False,
            "rigorous_lower": None,
            "rigorous_upper": None,
            "exact_rank": None,
            "assumptions": [],
            "points_found": [],
            "options": options,
            "stdout_summary": json.dumps(detail or {}, sort_keys=True),
        },
    )


def _legacy_independence_exhaustion(
    db,
    *,
    curve_id,
    point_id,
    basis_fingerprint,
    method,
    budget,
    prime=None,
):
    """Recognize expensive failures recorded before the stage-history ledger existed."""
    rows = db.execute(
        """
        SELECT * FROM rank_evidence
        WHERE curve_id=? AND engine='rank42.independence_check'
          AND evidence_type='exact_certificate'
        ORDER BY id DESC
        LIMIT 500
        """,
        (int(curve_id),),
    ).fetchall()
    for rec in rows:
        if str(rec["status"] or "") not in {"timeout", "error", "inconclusive"}:
            continue
        options = _stage_options(rec)
        # Basis preconditioning is a property of the active subgroup, not of
        # the particular bookmarked candidate that happened to trigger it.
        if method != "basis_2_saturation":
            if int(options.get("candidate_point_id") or -1) != int(point_id):
                continue
        if str(options.get("basis_fingerprint_before") or "") != str(basis_fingerprint or ""):
            continue
        strategy = str(options.get("strategy") or "")
        resolution_path = options.get("resolution_path")
        prior_budget = {
            "saturation_timeout": options.get("saturation_timeout"),
            "max_halvings": options.get("max_halvings"),
            "max_prime": options.get("max_prime"),
            "max_columns": options.get("max_columns"),
        }
        prior_budget = {k: v for k, v in prior_budget.items() if v is not None}
        comparable = {k: v for k, v in dict(budget or {}).items() if k in prior_budget}
        if not _budget_dominates(prior_budget, comparable):
            continue

        if method == "basis_2_saturation":
            if strategy == "saturation" and resolution_path == "basis_precondition":
                return rec
        elif method == "trial_saturation":
            # Before targeted-prime metadata existed, the saturation strategy
            # was the p=2 full-trial route. Preserve that expensive timeout.
            if int(prime or 2) == 2 and strategy == "saturation" and resolution_path in {None, "full_trial_saturation"}:
                return rec
    return None



_HEARTBEAT_SECONDS_RE = re.compile(r"still running ·\s*(\d+)s elapsed")
_TARGETED_PRIME_RE = re.compile(r"targeted full-trial p=(\d+) saturation")



def _job_command(job_row):
    try:
        raw = json.loads(job_row["command_json"] or "[]")
    except Exception:
        raw = []
    return [str(x) for x in raw] if isinstance(raw, list) else []


def _command_value(job_row, *names):
    command = _job_command(job_row)
    for name in names:
        try:
            idx = command.index(str(name))
        except ValueError:
            continue
        if idx + 1 < len(command):
            return command[idx + 1]
    return None


def _int_command_value(job_row, *names, default=None):
    value = _command_value(job_row, *names)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _job_metadata(row):
    try:
        value = json.loads(row["metadata_json"] or "{}")
    except Exception:
        value = {}
    return value if isinstance(value, dict) else {}


def _log_elapsed_seconds(log):
    values = [int(m.group(1)) for m in _HEARTBEAT_SECONDS_RE.finditer(str(log or ""))]
    return max(values) if values else 0


def _active_interrupted_stage(job_row, log):
    """Infer the expensive hard-case stage active when a UI job was stopped."""
    metadata = _job_metadata(job_row)
    mode = str(metadata.get("mode") or "")
    text = str(log or "")
    elapsed = _log_elapsed_seconds(text)
    point_ids = metadata.get("point_ids")
    point_id = None
    if isinstance(point_ids, list) and point_ids:
        try:
            point_id = int(point_ids[0])
        except (TypeError, ValueError):
            point_id = None

    basis_started = text.rfind("[stage 1/3] rigorous-basis 2-saturation started")
    basis_terminal = max(
        text.rfind("[stage 1/3] rigorous-basis 2-saturation completed"),
        text.rfind("[stage 1/3] rigorous-basis 2-saturation TIMEOUT"),
        text.rfind("[stage 1/3] rigorous-basis 2-saturation ERROR"),
    )
    if basis_started >= 0 and basis_started > basis_terminal:
        return {
            "method": "basis_2_saturation",
            "prime": None,
            "point_id": None,
            "elapsed_seconds": elapsed,
            "configured_budget": int(
                metadata.get("basis_saturation_timeout")
                or metadata.get("saturation_timeout")
                or 0
            ),
            "mode": mode,
        }

    # Targeted saturation emits the outer escalator label before the child
    # stage marker. Use the last such label so p=3 does not get mistaken for p=2.
    matches = list(_TARGETED_PRIME_RE.finditer(text))
    if matches:
        last = matches[-1]
        prime = int(last.group(1))
        tail = text[last.start():]
        started = tail.rfind(f"[stage 1/2] exact p={prime} saturation started")
        terminal = max(
            tail.rfind(f"[stage 1/2] p={prime} saturation completed"),
            tail.rfind(f"[stage 1/2] p={prime} saturation TIMEOUT"),
            tail.rfind(f"[stage 1/2] p={prime} saturation ERROR"),
        )
        if started >= 0 and started > terminal:
            return {
                "method": "trial_saturation",
                "prime": prime,
                "point_id": point_id,
                "elapsed_seconds": elapsed,
                "configured_budget": int(metadata.get("trial_saturation_timeout") or 0),
                "mode": mode,
            }

    for descent_mode in ("mwrank_selmer", "simon_known", "mwrank_coverings"):
        marker = f"[escalator] {descent_mode}"
        pos = text.rfind(marker)
        if pos >= 0:
            tail = text[pos:]
            if " completed " not in tail and " TIMEOUT " not in tail and " ERROR " not in tail:
                return {
                    "method": f"descent:{descent_mode}",
                    "prime": None,
                    "point_id": None,
                    "elapsed_seconds": elapsed,
                    "configured_budget": int(metadata.get("descent_timeout") or 0),
                    "mode": mode,
                }

    proof_pos = text.rfind("[escalator] Sage proof-mode exact-rank stage started")
    if proof_pos >= 0:
        tail = text[proof_pos:]
        if "EXACT RANK =" not in tail and "proof-mode rank TIMEOUT" not in tail and "proof-mode rank ERROR" not in tail:
            return {
                "method": "sage_proof_rank",
                "prime": None,
                "point_id": None,
                "elapsed_seconds": elapsed,
                "configured_budget": int(metadata.get("rank_cert_timeout") or 0),
                "mode": mode,
            }
    return None


def record_interrupted_ui_job(db, job_row, *, log=None):
    """Persist a stopped/killed hard-case stage using the work actually spent."""
    if job_row is None or str(job_row["kind"] or "") != "independence":
        return None
    metadata = _job_metadata(job_row)
    mode = str(metadata.get("mode") or "")
    if mode not in {
        "hard_case_saturation",
        "hard_case_basis_first_saturation",
        "hard_case_escalator",
    }:
        return None
    if log is None:
        try:
            log = Path(str(job_row["log_path"])).read_text(
                encoding="utf-8", errors="replace"
            )
        except Exception:
            return None
    stage = _active_interrupted_stage(job_row, log)
    if stage is None:
        return None

    curve_id = metadata.get("curve_id")
    if curve_id is None:
        return None
    row = get_curve(db, int(curve_id))
    if row is None:
        return None
    current_fp = _basis_fingerprint_from_row(row)
    historical_fp = _latest_basis_fingerprint_before(
        db,
        int(curve_id),
        job_row["started_at"] or job_row["created_at"],
    )
    basis_fp = historical_fp or current_fp
    if current_fp and historical_fp and current_fp != historical_fp:
        # Do not attach an interrupted legacy job to a basis that subsequently changed.
        return None

    elapsed = max(1, int(stage.get("elapsed_seconds") or 0))
    method = str(stage["method"])
    if method in {"basis_2_saturation", "trial_saturation"}:
        # Preserve the certificate knobs too, but charge saturation only for
        # time actually spent. This lets a 780s interrupted saturation suppress
        # a 600s replay while still allowing a genuinely stronger certificate
        # configuration to run later.
        budget = {
            "saturation_timeout": elapsed,
            "certificate_timeout": _int_command_value(
                job_row, "--certificate-timeout", "--timeout", default=0
            ),
            "max_halvings": _int_command_value(
                job_row, "--max-halvings", default=0
            ),
            "max_prime": _int_command_value(
                job_row, "--max-prime", default=0
            ),
            "max_columns": _int_command_value(
                job_row, "--max-columns", default=0
            ),
        }
    elif method.startswith("descent:"):
        budget = {"timeout": elapsed}
    else:
        budget = {"timeout": elapsed}

    point_id = stage.get("point_id")
    prime = stage.get("prime")
    existing = _prior_stage_attempt(
        db,
        curve_id=int(curve_id),
        method=method,
        basis_fingerprint=basis_fp,
        budget=budget,
        point_id=point_id,
        prime=prime,
        point_scoped=(method not in {"basis_2_saturation"} and point_id is not None),
    )
    if existing is not None:
        return int(existing["id"])

    model = json.loads(row["a_invariants_json"] or "[]")
    evidence_id = _record_stage_attempt(
        db,
        curve_id=int(curve_id),
        model=model,
        method=method,
        basis_fingerprint=basis_fp,
        budget=budget,
        status="interrupted",
        point_id=point_id,
        prime=prime,
        detail={
            "ui_job_id": int(job_row["id"]),
            "ui_job_status": str(job_row["status"] or ""),
            "observed_elapsed_seconds": elapsed,
            "configured_budget": int(stage.get("configured_budget") or 0),
            "mode": stage.get("mode"),
        },
    )
    return int(evidence_id)


def _recover_legacy_interrupted_stage(
    db,
    *,
    curve_id,
    method,
    basis_fingerprint,
    budget,
    point_id=None,
    prime=None,
):
    """Backfill old killed/stopped hard-case UI jobs into stage evidence."""
    table = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ui_jobs'"
    ).fetchone()
    if table is None:
        return None
    rows = db.execute(
        """
        SELECT * FROM ui_jobs
        WHERE kind='independence' AND status IN ('killed','stopped')
        ORDER BY id DESC
        LIMIT 200
        """
    ).fetchall()
    for job in rows:
        metadata = _job_metadata(job)
        if int(metadata.get("curve_id") or -1) != int(curve_id):
            continue
        try:
            log = Path(str(job["log_path"])).read_text(
                encoding="utf-8", errors="replace"
            )
        except Exception:
            continue
        stage = _active_interrupted_stage(job, log)
        if stage is None or str(stage.get("method")) != str(method):
            continue
        if prime is not None and int(stage.get("prime") or -1) != int(prime):
            continue
        if method != "basis_2_saturation":
            stage_point = stage.get("point_id")
            if stage_point is not None and int(stage_point) != int(point_id or -1):
                continue
        recovered_id = record_interrupted_ui_job(db, job, log=log)
        if recovered_id is None:
            continue
        rec = db.execute(
            "SELECT * FROM rank_evidence WHERE id=?",
            (int(recovered_id),),
        ).fetchone()
        if rec is None:
            continue
        options = _stage_options(rec)
        if str(options.get("basis_fingerprint") or "") != str(basis_fingerprint or ""):
            continue
        if not _budget_dominates(options.get("budget"), budget):
            continue
        return rec
    return None



def _saturation_family_blocker(
    db,
    *,
    curve_id,
    point_id,
    basis_fingerprint,
):
    """Return evidence that full-trial saturation is expensive on this trial.

    One timeout/error/interruption is treated as evidence about the method
    family, not merely about one prime. The user can explicitly override this
    with --deep-saturation.
    """
    rows = db.execute(
        """
        SELECT * FROM rank_evidence
        WHERE curve_id=? AND engine=? AND evidence_type='hard_case_stage'
        ORDER BY id DESC
        LIMIT 500
        """,
        (int(curve_id), ENGINE),
    ).fetchall()
    for rec in rows:
        if str(rec["status"] or "") not in {"timeout", "error", "interrupted"}:
            continue
        options = _stage_options(rec)
        if str(options.get("method") or "") != "trial_saturation":
            continue
        if str(options.get("basis_fingerprint") or "") != str(basis_fingerprint or ""):
            continue
        if int(options.get("point_id") or -1) != int(point_id):
            continue
        return rec

    # Compatibility with the pre-escalator full-trial p=2 saturation evidence.
    rows = db.execute(
        """
        SELECT * FROM rank_evidence
        WHERE curve_id=? AND engine='rank42.independence_check'
          AND evidence_type='exact_certificate'
        ORDER BY id DESC
        LIMIT 500
        """,
        (int(curve_id),),
    ).fetchall()
    for rec in rows:
        if str(rec["status"] or "") not in {"timeout", "error"}:
            continue
        options = _stage_options(rec)
        if int(options.get("candidate_point_id") or -1) != int(point_id):
            continue
        if str(options.get("basis_fingerprint_before") or "") != str(basis_fingerprint or ""):
            continue
        if str(options.get("strategy") or "") != "saturation":
            continue
        path = options.get("resolution_path")
        # Old full-trial runs had no resolution_path. Newer explicit full-trial
        # runs use full_trial_saturation. Basis-only preconditioning is excluded.
        if path in {None, "", "full_trial_saturation"}:
            return rec
    return None


def _skip_saturation_family(stages, primes, blocker):
    evidence_id = int(blocker["id"]) if blocker is not None else None
    status = str(blocker["status"] or "unknown") if blocker is not None else "unknown"
    prime_text = ",".join(str(int(p)) for p in primes)
    reason = (
        f"full-trial saturation already classified expensive on this basis/trial "
        f"({status}); skipping remaining prime ladder [{prime_text}]"
    )
    text = f"[escalator] SKIP full-trial saturation family · {reason}"
    if evidence_id is not None:
        text += f" · evidence #{evidence_id}"
    print(text, flush=True)
    stages.append({
        "stage": "full-trial saturation family",
        "status": "skipped",
        "reason": reason,
        "prior_evidence_id": evidence_id,
        "primes": [int(p) for p in primes],
    })


def _trial_saturation_budget(args, prime):
    configured = int(args.trial_saturation_timeout)
    if bool(getattr(args, "deep_saturation", False)) or int(prime) <= 2:
        return configured
    return min(configured, max(1, int(args.saturation_probe_timeout)))


def _skip_stage(stages, *, name, prior, reason):
    evidence_id = int(prior["id"]) if prior is not None else None
    text = f"[escalator] SKIP {name} · {reason}"
    if evidence_id is not None:
        text += f" · evidence #{evidence_id}"
    print(text, flush=True)
    stages.append({
        "stage": name,
        "status": "skipped",
        "reason": reason,
        "prior_evidence_id": evidence_id,
    })


def _extract_independence_payload(log):
    prefix = COMPAT_MARKER
    for line in reversed(str(log or "").splitlines()):
        if line.startswith(prefix):
            try:
                value = json.loads(line[len(prefix):])
            except Exception:
                return {}
            return value if isinstance(value, dict) else {}
    return {}


def _close_from_exact_ceiling(db, curve_id, point_id):
    state = reduce_rank_state(db, int(curve_id))
    exact = state.get("exact_rank")
    if exact is None:
        return None
    row = get_curve(db, int(curve_id))
    if row is None or _active_basis_count(row) < int(exact):
        return None
    rec = _point_row(db, curve_id, point_id)
    if rec is None or _resolution(rec):
        return _resolution(rec)

    meta = _metadata(rec)
    history = meta.get("hard_case_resolution_history")
    if not isinstance(history, list):
        history = []
    history.append({
        "engine": ENGINE,
        "mechanism": "exact_rank_ceiling",
        "exact_rank": int(exact),
        "rigorous_lower": int(state["rigorous_lower"]),
    })
    meta["hard_case_resolution_history"] = history[-20:]
    meta["last_hard_case_resolution"] = history[-1]
    updated = upsert_point(
        db,
        curve_id=int(curve_id),
        x=rec["x"],
        y=rec["y"],
        source=rec["source"],
        role=rec["role"],
        exact_verified=True,
        independence_status="dependent",
        rigorous_independent=False,
        search_ref=rec["search_ref"] or "analysis:hard-case-escalator",
        plugin_id=rec["plugin_id"],
        metadata=meta,
    )
    if updated is not None:
        set_point_hard_flag(db, int(updated["id"]), False)
    log_event(
        db,
        int(curve_id),
        "info",
        f"Hard-case point #{int(point_id)} closed by exact rank ceiling {int(exact)}",
    )
    return "dependent"


def _clear_bookmark_for_exact_curve(db, curve_id, point_id, exact_rank):
    rec = _point_row(db, curve_id, point_id)
    if rec is None:
        return "curve_exact"
    meta = _metadata(rec)
    history = meta.get("hard_case_resolution_history")
    if not isinstance(history, list):
        history = []
    history.append({
        "engine": ENGINE,
        "mechanism": "curve_exact_rank",
        "exact_rank": int(exact_rank),
        "point_relation": "not_individually_resolved",
    })
    meta["hard_case_resolution_history"] = history[-20:]
    meta["last_hard_case_resolution"] = history[-1]
    upsert_point(
        db,
        curve_id=int(curve_id),
        x=rec["x"],
        y=rec["y"],
        source=rec["source"],
        role=rec["role"],
        exact_verified=True,
        independence_status=rec["independence_status"],
        rigorous_independent=bool(rec["rigorous_independent"]),
        search_ref=rec["search_ref"] or "analysis:hard-case-escalator",
        plugin_id=rec["plugin_id"],
        metadata=meta,
    )
    set_point_hard_flag(db, int(point_id), False)
    log_event(
        db,
        int(curve_id),
        "info",
        f"Hard-case point #{int(point_id)} bookmark cleared because curve rank is exact {int(exact_rank)}; "
        "the individual point relation was not needed to resolve the curve",
    )
    return "curve_exact"



def _stream_child(cmd, *, label, timeout):
    print(f"[escalator] {label}", flush=True)
    started = time.monotonic()
    proc = subprocess.Popen(
        [str(x) for x in cmd],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    lines = []
    try:
        assert proc.stdout is not None
        while True:
            line = proc.stdout.readline()
            if line:
                line = line.rstrip("\n")
                lines.append(line)
                print(line, flush=True)
            if proc.poll() is not None:
                remainder = proc.stdout.read()
                if remainder:
                    for extra in remainder.splitlines():
                        lines.append(extra)
                        print(extra, flush=True)
                break
            if time.monotonic() - started > float(timeout):
                proc.kill()
                proc.wait()
                print(
                    f"[escalator] {label} OUTER TIMEOUT after {int(timeout)}s",
                    flush=True,
                )
                return -9, "\n".join(lines)
            if not line:
                time.sleep(0.05)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    return int(proc.returncode or 0), "\n".join(lines)


def _independence_command(
    args,
    point_ids,
    *,
    strategy,
    saturation_timeout=None,
    saturation_prime=None,
    defer_full_trial=False,
):
    cmd = [
        sys.executable,
        "-m",
        "rank42.independence_check",
        "--db",
        args.db,
        "--curve-id",
        str(int(args.curve_id)),
        "--strategy",
        str(strategy),
        "--timeout",
        str(int(args.certificate_timeout)),
        "--max-halvings",
        str(int(args.max_halvings)),
        "--max-prime",
        str(int(args.max_prime)),
        "--max-columns",
        str(int(args.max_columns)),
    ]
    if saturation_timeout is not None:
        cmd += ["--saturation-timeout", str(int(saturation_timeout))]
    if saturation_prime is not None:
        cmd += ["--saturation-prime", str(int(saturation_prime))]
    if defer_full_trial:
        cmd.append("--no-full-trial-fallback")
    for pid in point_ids:
        cmd += ["--point-id", str(int(pid))]
    return cmd


def _run_independence_stage(
    db,
    args,
    point_id,
    stages,
    *,
    name,
    strategy,
    saturation_timeout=None,
    saturation_prime=None,
    defer_full_trial=False,
    history_method=None,
    basis_fingerprint=None,
):
    method = str(history_method or strategy)
    current_budget = {
        "certificate_timeout": int(args.certificate_timeout),
        "max_halvings": int(args.max_halvings),
        "max_prime": int(args.max_prime),
        "max_columns": int(args.max_columns),
    }
    if saturation_timeout is not None:
        current_budget["saturation_timeout"] = int(saturation_timeout)

    prior = _prior_stage_attempt(
        db,
        curve_id=args.curve_id,
        method=method,
        basis_fingerprint=basis_fingerprint,
        budget=current_budget,
        point_id=point_id,
        prime=saturation_prime,
        point_scoped=(method != "basis_2_saturation"),
    )
    if prior is None and method in {"basis_2_saturation", "trial_saturation"}:
        prior = _legacy_independence_exhaustion(
            db,
            curve_id=args.curve_id,
            point_id=point_id,
            basis_fingerprint=basis_fingerprint,
            method=method,
            budget=current_budget,
            prime=saturation_prime,
        )
    if prior is None:
        prior = _recover_legacy_interrupted_stage(
            db,
            curve_id=args.curve_id,
            method=method,
            basis_fingerprint=basis_fingerprint,
            budget=current_budget,
            point_id=point_id,
            prime=saturation_prime,
        )
    if prior is not None:
        prior_status = str(prior["status"] or "unknown")
        _skip_stage(
            stages,
            name=name,
            prior=prior,
            reason=(
                f"same active basis already exhausted this method with "
                f"equal-or-stronger budget ({prior_status})"
            ),
        )
        return None

    cmd = _independence_command(
        args,
        [point_id],
        strategy=strategy,
        saturation_timeout=saturation_timeout,
        saturation_prime=saturation_prime,
        defer_full_trial=defer_full_trial,
    )
    outer_budget = (
        int(saturation_timeout or 0)
        + int(args.certificate_timeout)
        + 120
    )
    rc, log = _stream_child(cmd, label=name, timeout=max(180, outer_budget))
    rec = _point_row(db, args.curve_id, point_id)
    resolved = _resolution(rec)
    state = reduce_rank_state(db, int(args.curve_id))
    payload = _extract_independence_payload(log)
    if resolved:
        stage_status = "completed"
    elif rc == -9 or int(payload.get("timeouts") or 0) > 0:
        stage_status = "timeout"
    elif rc != 0 or int(payload.get("errors") or 0) > 0:
        stage_status = "error"
    else:
        stage_status = "inconclusive"

    row = get_curve(db, int(args.curve_id))
    model = json.loads(row["a_invariants_json"] or "[]") if row is not None else []
    stage_evidence_id = _record_stage_attempt(
        db,
        curve_id=args.curve_id,
        model=model,
        method=method,
        basis_fingerprint=basis_fingerprint,
        budget=current_budget,
        status=stage_status,
        point_id=None if method == "basis_2_saturation" else point_id,
        prime=saturation_prime,
        detail={
            "label": name,
            "returncode": rc,
            "resolution": resolved,
            "independence_result": payload,
        },
    )
    stages.append({
        "stage": name,
        "status": stage_status,
        "returncode": rc,
        "resolution": resolved,
        "stage_evidence_id": int(stage_evidence_id),
        "rigorous_lower": state.get("rigorous_lower"),
        "rigorous_upper": state.get("rigorous_upper"),
        "exact_rank": state.get("exact_rank"),
    })
    return resolved


def _load_basis(db, curve_id):
    from sage.all import QQ, EllipticCurve

    row = get_curve(db, int(curve_id))
    if row is None:
        raise ValueError(f"curve #{int(curve_id)} not found")
    ainvs = json.loads(row["a_invariants_json"] or "[]")
    if len(ainvs) != 5:
        raise ValueError("curve has no stored five-term Weierstrass model")
    E = EllipticCurve(QQ, [QQ(str(x)) for x in ainvs])
    basis, required, complete = rigorous_witness_basis(db, int(curve_id), E)
    if not complete:
        raise ValueError(
            f"rigorous basis incomplete: replayed {len(basis)}/{int(required)}"
        )
    return row, E, basis


def _record_descent(db, curve_id, model, result, mode):
    upper = result.get("rigorous_upper")
    evidence_id = record_rank_evidence(
        db,
        curve_id=int(curve_id),
        model=model,
        data={
            "engine": str(result.get("engine") or mode),
            "engine_version": result.get("engine_version"),
            "sage_version": result.get("sage_version"),
            "evidence_type": "descent",
            "status": "completed",
            "rigorous": True,
            # Classical lower outputs are intentionally not promoted here.
            "rigorous_lower": None,
            "rigorous_upper": None if upper is None else int(upper),
            "exact_rank": None,
            "assumptions": [],
            "points_found": list(result.get("points") or []),
            "minimal_model_a_invariants": result.get("minimal_model_a_invariants"),
            "options": {
                "source": "hard_case_escalator",
                "mode": str(mode),
                **dict(result.get("options") or {}),
            },
            "elapsed_seconds": result.get("runtime_seconds"),
        },
    )
    state = apply_reduced_rank_state(db, int(curve_id))
    return evidence_id, state


def _store_descent_points(db, curve_id, result, mode):
    ids = []
    for xy in result.get("points") or []:
        if not isinstance(xy, (list, tuple)) or len(xy) < 2:
            continue
        rec = upsert_point(
            db,
            curve_id=int(curve_id),
            x=xy[0],
            y=xy[1],
            source=f"hard_case_escalator:{mode}",
            role="candidate_extra",
            exact_verified=True,
            independence_status="unknown",
            rigorous_independent=False,
            search_ref=f"analysis:hard-case-escalator:{mode}",
            metadata={
                "hard_case_escalator": True,
                "descent_mode": str(mode),
            },
        )
        if rec is not None and not int(rec["rigorous_independent"] or 0):
            if str(rec["independence_status"] or "") != "dependent":
                ids.append(int(rec["id"]))
    return list(dict.fromkeys(ids))


def _run_descent(db, args, point_id, stages, mode, *, basis_fingerprint=None):
    method = f"descent:{mode}"
    current_budget = {"timeout": int(args.descent_timeout)}
    prior = _prior_stage_attempt(
        db,
        curve_id=args.curve_id,
        method=method,
        basis_fingerprint=basis_fingerprint,
        budget=current_budget,
        point_id=None,
        point_scoped=False,
    )
    if prior is not None:
        _skip_stage(
            stages,
            name=mode,
            prior=prior,
            reason=(
                "same active basis already ran this descent with "
                f"equal-or-stronger budget ({str(prior['status'] or 'unknown')})"
            ),
        )
        return _close_from_exact_ceiling(db, args.curve_id, point_id)

    row0 = get_curve(db, int(args.curve_id))
    model0 = json.loads(row0["a_invariants_json"] or "[]") if row0 is not None else []
    try:
        row, E, basis = _load_basis(db, args.curve_id)
        result = run_classical_descent(
            E.a_invariants(),
            basis,
            mode=mode,
            timeout=int(args.descent_timeout),
            heartbeat_label=f"{mode} curve #{int(args.curve_id)}",
            heartbeat_seconds=30,
        )
    except ClassicalDescentTimeout as exc:
        print(f"[escalator] {mode} TIMEOUT · {exc}", flush=True)
        evidence_id = _record_stage_attempt(
            db,
            curve_id=args.curve_id,
            model=model0,
            method=method,
            basis_fingerprint=basis_fingerprint,
            budget=current_budget,
            status="timeout",
            detail={"error": str(exc)},
        )
        stages.append({
            "stage": mode,
            "status": "timeout",
            "error": str(exc),
            "stage_evidence_id": int(evidence_id),
        })
        return None
    except Exception as exc:
        print(f"[escalator] {mode} ERROR · {exc}", flush=True)
        evidence_id = _record_stage_attempt(
            db,
            curve_id=args.curve_id,
            model=model0,
            method=method,
            basis_fingerprint=basis_fingerprint,
            budget=current_budget,
            status="error",
            detail={"error": str(exc)},
        )
        stages.append({
            "stage": mode,
            "status": "error",
            "error": str(exc),
            "stage_evidence_id": int(evidence_id),
        })
        return None

    evidence_id, state = _record_descent(
        db,
        args.curve_id,
        [str(x) for x in E.a_invariants()],
        result,
        mode,
    )
    print(
        f"[escalator] {mode} completed · rigorous upper="
        f"{state.get('rigorous_upper') if state.get('rigorous_upper') is not None else '—'}"
        f" · current lower={state.get('rigorous_lower')}",
        flush=True,
    )

    closed = _close_from_exact_ceiling(db, args.curve_id, point_id)
    if closed:
        stage_evidence_id = _record_stage_attempt(
            db,
            curve_id=args.curve_id,
            model=[str(x) for x in E.a_invariants()],
            method=method,
            basis_fingerprint=basis_fingerprint,
            budget=current_budget,
            status="completed",
            detail={
                "descent_evidence_id": int(evidence_id),
                "resolution": closed,
                "rigorous_upper": state.get("rigorous_upper"),
            },
        )
        stages.append({
            "stage": mode,
            "status": "completed",
            "evidence_id": int(evidence_id),
            "stage_evidence_id": int(stage_evidence_id),
            "rigorous_upper": state.get("rigorous_upper"),
            "rigorous_lower": state.get("rigorous_lower"),
            "points_returned": len(result.get("points") or []),
        })
        return closed

    new_ids = [
        pid
        for pid in _store_descent_points(db, args.curve_id, result, mode)
        if int(pid) != int(point_id)
    ]
    if new_ids:
        print(
            f"[escalator] exact-certifying {len(new_ids)} point(s) returned by {mode}",
            flush=True,
        )
        cmd = _independence_command(
            args,
            new_ids,
            strategy="standard",
        )
        _stream_child(
            cmd,
            label=f"{mode} returned-point exact certification",
            timeout=max(180, int(args.certificate_timeout) * max(1, len(new_ids)) + 120),
        )
        # A newly enlarged basis can change the selected point's relation.
        row_after = get_curve(db, int(args.curve_id))
        new_fp = _basis_fingerprint_from_row(row_after) if row_after is not None else basis_fingerprint
        _run_independence_stage(
            db,
            args,
            point_id,
            stages,
            name=f"retest selected point after {mode}",
            strategy="standard",
            history_method=f"retest_after:{mode}",
            basis_fingerprint=new_fp,
        )
        rec = _point_row(db, args.curve_id, point_id)
        if _resolution(rec):
            closed = _resolution(rec)

    stage_evidence_id = _record_stage_attempt(
        db,
        curve_id=args.curve_id,
        model=[str(x) for x in E.a_invariants()],
        method=method,
        basis_fingerprint=basis_fingerprint,
        budget=current_budget,
        status="completed",
        detail={
            "descent_evidence_id": int(evidence_id),
            "resolution": closed,
            "rigorous_upper": state.get("rigorous_upper"),
            "points_returned": len(result.get("points") or []),
        },
    )
    stages.append({
        "stage": mode,
        "status": "completed",
        "evidence_id": int(evidence_id),
        "stage_evidence_id": int(stage_evidence_id),
        "rigorous_upper": state.get("rigorous_upper"),
        "rigorous_lower": state.get("rigorous_lower"),
        "points_returned": len(result.get("points") or []),
    })
    return closed


def _run_proof_rank(db, args, point_id, stages, *, basis_fingerprint=None):
    row = get_curve(db, int(args.curve_id))
    ainvs = json.loads(row["a_invariants_json"] or "[]")
    lower_before = int(proven_lower(row))
    method = "sage_proof_rank"
    current_budget = {"timeout": int(args.rank_cert_timeout)}
    prior = _prior_stage_attempt(
        db,
        curve_id=args.curve_id,
        method=method,
        basis_fingerprint=basis_fingerprint,
        budget=current_budget,
        point_id=None,
        point_scoped=False,
    )
    if prior is not None:
        _skip_stage(
            stages,
            name="Sage proof-mode exact rank",
            prior=prior,
            reason=(
                "same active basis already ran proof-mode rank with "
                f"equal-or-stronger budget ({str(prior['status'] or 'unknown')})"
            ),
        )
        return _close_from_exact_ceiling(db, args.curve_id, point_id)

    print("[escalator] Sage proof-mode exact-rank stage started", flush=True)
    try:
        result = run_rank_certification(
            ainvs,
            timeout=int(args.rank_cert_timeout),
            heartbeat_label=f"proof-mode rank curve #{int(args.curve_id)}",
            heartbeat_seconds=30,
        )
    except RankCertificationTimeout as exc:
        print(f"[escalator] proof-mode rank TIMEOUT · {exc}", flush=True)
        stage_evidence_id = _record_stage_attempt(
            db,
            curve_id=args.curve_id,
            model=ainvs,
            method=method,
            basis_fingerprint=basis_fingerprint,
            budget=current_budget,
            status="timeout",
            detail={"error": str(exc)},
        )
        stages.append({
            "stage": "sage_proof_rank",
            "status": "timeout",
            "error": str(exc),
            "stage_evidence_id": int(stage_evidence_id),
        })
        return None
    except RankCertificationFailure as exc:
        print(f"[escalator] proof-mode rank ERROR · {exc}", flush=True)
        stage_evidence_id = _record_stage_attempt(
            db,
            curve_id=args.curve_id,
            model=ainvs,
            method=method,
            basis_fingerprint=basis_fingerprint,
            budget=current_budget,
            status="error",
            detail={"error": str(exc)},
        )
        stages.append({
            "stage": "sage_proof_rank",
            "status": "error",
            "error": str(exc),
            "stage_evidence_id": int(stage_evidence_id),
        })
        return None

    exact = int(result["exact_rank"])
    if exact < lower_before:
        raise RuntimeError(
            f"proof-mode exact rank {exact} is below rigorous lower {lower_before}"
        )
    promoted = promote_exact_rank_certificate(
        db,
        curve_id=int(args.curve_id),
        model=ainvs,
        exact_rank=exact,
        certificate=result,
        engine="sage_proof_rank",
        source="hard_case_escalator",
        metadata={
            "timeout": int(args.rank_cert_timeout),
            "sage_version": result.get("sage_version"),
            "minimal_model_a_invariants": result.get("a_invariants") or ainvs,
            "elapsed_seconds": result.get("_runtime_seconds"),
            "basis_fingerprint": basis_fingerprint,
        },
    )
    evidence_id = int(promoted["evidence_id"])
    if not promoted["projected"] or promoted["exact_rank"] != exact:
        detail = {
            "exact_rank": exact,
            "rank_evidence_id": evidence_id,
            "rank_inconsistent": bool(promoted["rank_inconsistent"]),
            "reduced_rigorous_lower": promoted["rigorous_lower"],
            "reduced_rigorous_upper": promoted["rigorous_upper"],
            "error": "proof-mode exact rank conflicts with existing rigorous rank evidence",
        }
        stage_evidence_id = _record_stage_attempt(
            db,
            curve_id=args.curve_id,
            model=ainvs,
            method=method,
            basis_fingerprint=basis_fingerprint,
            budget=current_budget,
            status="error",
            detail=detail,
        )
        stages.append({
            "stage": "sage_proof_rank",
            "status": "error",
            "error": detail["error"],
            "exact_rank": exact,
            "evidence_id": evidence_id,
            "stage_evidence_id": int(stage_evidence_id),
        })
        print(
            f"[escalator] proof-mode exact rank CONFLICT · exact={exact}",
            flush=True,
        )
        return None

    print(f"[escalator] EXACT RANK = {exact}", flush=True)
    stage_evidence_id = _record_stage_attempt(
        db,
        curve_id=args.curve_id,
        model=ainvs,
        method=method,
        basis_fingerprint=basis_fingerprint,
        budget=current_budget,
        status="completed",
        detail={"exact_rank": exact, "rank_evidence_id": evidence_id},
    )
    stages.append({
        "stage": "sage_proof_rank",
        "status": "exact",
        "exact_rank": exact,
        "evidence_id": int(evidence_id),
        "stage_evidence_id": int(stage_evidence_id),
    })
    closed = _close_from_exact_ceiling(db, args.curve_id, point_id)
    if closed:
        return closed
    return _clear_bookmark_for_exact_curve(
        db, args.curve_id, point_id, exact
    )


def _record_escalator_summary(db, args, point_id, status, stages):
    row = get_curve(db, int(args.curve_id))
    state = reduce_rank_state(db, int(args.curve_id))
    model = json.loads(row["a_invariants_json"] or "[]")
    return record_rank_evidence(
        db,
        curve_id=int(args.curve_id),
        model=model,
        data={
            "engine": ENGINE,
            "engine_version": ENGINE_VERSION,
            "evidence_type": "hard_case_resolution",
            "status": str(status),
            "rigorous": False,
            "rigorous_lower": None,
            "rigorous_upper": None,
            "exact_rank": None,
            "assumptions": [],
            "points_found": [],
            "options": {
                "point_id": int(point_id),
                "certificate_timeout": int(args.certificate_timeout),
                "basis_saturation_timeout": int(args.basis_saturation_timeout),
                "trial_saturation_timeout": int(args.trial_saturation_timeout),
                "saturation_probe_timeout": int(args.saturation_probe_timeout),
                "deep_saturation": bool(args.deep_saturation),
                "saturation_only": bool(args.saturation_only),
                "descent_timeout": int(args.descent_timeout),
                "rank_cert_timeout": int(args.rank_cert_timeout),
                "saturation_primes": str(args.saturation_primes),
            },
            "stdout_summary": json.dumps(
                {
                    "resolution": status,
                    "rank_state": state,
                    "stages": stages,
                },
                sort_keys=True,
            ),
        },
    )


def _parse_primes(text):
    out = []
    for token in str(text or "").split(","):
        token = token.strip()
        if not token:
            continue
        p = int(token)
        if p < 2:
            raise ValueError("saturation primes must be >=2")
        if p not in out:
            out.append(p)
    return out or [2, 3, 5, 7]


def resolve_point(db, args, point_id):
    point_id = int(point_id)
    rec = _point_row(db, args.curve_id, point_id)
    if rec is None:
        raise ValueError(
            f"point #{point_id} does not belong to curve #{int(args.curve_id)}"
        )
    if not int(rec["exact_verified"] or 0):
        raise ValueError(f"point #{point_id} is not exact-verified")

    stages = []
    start_state = reduce_rank_state(db, int(args.curve_id))
    print("=" * 78, flush=True)
    print(
        f"HARD-CASE ESCALATOR · curve #{int(args.curve_id)} · point #{point_id}",
        flush=True,
    )
    print(
        f"start: rigorous rank >= {start_state.get('rigorous_lower')} · "
        f"upper={start_state.get('rigorous_upper') if start_state.get('rigorous_upper') is not None else 'unknown'}",
        flush=True,
    )

    resolved = _resolution(rec) or _close_from_exact_ceiling(
        db, args.curve_id, point_id
    )
    if resolved:
        _record_escalator_summary(db, args, point_id, resolved, stages)
        return resolved, stages

    # Cheap/orthogonal attacks come first. Saturation is intentionally last:
    # a hard 19-point trial should not burn minutes per prime before we try
    # descents or proof-mode exact rank. Advanced --saturation-only deliberately
    # bypasses these stages and basis preconditioning.
    if not bool(args.saturation_only):
        for mode in ("mwrank_selmer", "simon_known", "mwrank_coverings"):
            basis_fingerprint = _basis_fingerprint_from_row(
                get_curve(db, int(args.curve_id))
            )
            resolved = _run_descent(
                db,
                args,
                point_id,
                stages,
                mode,
                basis_fingerprint=basis_fingerprint,
            )
            if resolved:
                _record_escalator_summary(db, args, point_id, resolved, stages)
                return resolved, stages

        basis_fingerprint = _basis_fingerprint_from_row(
            get_curve(db, int(args.curve_id))
        )
        resolved = _run_proof_rank(
            db,
            args,
            point_id,
            stages,
            basis_fingerprint=basis_fingerprint,
        )
        if resolved:
            _record_escalator_summary(db, args, point_id, resolved, stages)
            return resolved, stages

        basis_fingerprint = _basis_fingerprint_from_row(
            get_curve(db, int(args.curve_id))
        )
        resolved = _run_independence_stage(
            db,
            args,
            point_id,
            stages,
            name="prepare/cache rigorous 2-saturated basis + exact candidate certificate",
            strategy="saturation",
            saturation_timeout=int(args.basis_saturation_timeout),
            defer_full_trial=True,
            history_method="basis_2_saturation",
            basis_fingerprint=basis_fingerprint,
        )
        if resolved:
            _record_escalator_summary(db, args, point_id, resolved, stages)
            return resolved, stages

    basis_fingerprint = _basis_fingerprint_from_row(
        get_curve(db, int(args.curve_id))
    )
    primes = _parse_primes(args.saturation_primes)
    family_blocker = None
    if not bool(args.deep_saturation):
        family_blocker = _saturation_family_blocker(
            db,
            curve_id=args.curve_id,
            point_id=point_id,
            basis_fingerprint=basis_fingerprint,
        )
    if family_blocker is not None:
        _skip_saturation_family(stages, primes, family_blocker)
    else:
        for index, prime in enumerate(primes):
            stage_timeout = _trial_saturation_budget(args, prime)
            resolved = _run_independence_stage(
                db,
                args,
                point_id,
                stages,
                name=(
                    f"targeted full-trial p={prime} saturation + exact certificate"
                    + (
                        f" · probe {stage_timeout}s"
                        if int(prime) > 2 and not bool(args.deep_saturation)
                        else ""
                    )
                ),
                strategy="trial-saturation",
                saturation_timeout=stage_timeout,
                saturation_prime=int(prime),
                history_method="trial_saturation",
                basis_fingerprint=basis_fingerprint,
            )
            if resolved:
                _record_escalator_summary(db, args, point_id, resolved, stages)
                return resolved, stages

            # Re-read evidence after the attempt. One expensive failure means
            # the family is expensive; do not mechanically burn the rest of the
            # prime ladder unless the researcher explicitly chose deep mode.
            if not bool(args.deep_saturation) and index + 1 < len(primes):
                family_blocker = _saturation_family_blocker(
                    db,
                    curve_id=args.curve_id,
                    point_id=point_id,
                    basis_fingerprint=basis_fingerprint,
                )
                if family_blocker is not None:
                    _skip_saturation_family(
                        stages, primes[index + 1 :], family_blocker
                    )
                    break

    state = reduce_rank_state(db, int(args.curve_id))
    reason = (
        f"escalator exhausted · rigorous rank >= {state.get('rigorous_lower')} · "
        f"rigorous upper "
        f"{state.get('rigorous_upper') if state.get('rigorous_upper') is not None else 'unknown'}"
    )
    set_point_hard_flag(db, point_id, True, reason=reason)
    log_event(
        db,
        int(args.curve_id),
        "warn",
        f"Hard-case escalator exhausted for point #{point_id}: {reason}",
    )
    _record_escalator_summary(db, args, point_id, "research_unresolved", stages)
    return "research_unresolved", stages


def main():
    args = parse_args()
    if not args.point_id:
        raise SystemExit("at least one --point-id is required")
    db = connect(args.db)
    try:
        results = []
        for pid in dict.fromkeys(int(x) for x in args.point_id):
            status, stages = resolve_point(db, args, pid)
            state = reduce_rank_state(db, int(args.curve_id))
            results.append({
                "point_id": int(pid),
                "status": status,
                "stages": stages,
                "rigorous_lower": state.get("rigorous_lower"),
                "rigorous_upper": state.get("rigorous_upper"),
                "exact_rank": state.get("exact_rank"),
            })

        final_state = reduce_rank_state(db, int(args.curve_id))
        independent = [
            r["point_id"] for r in results if r["status"] == "independent"
        ]
        dependent = [
            r["point_id"] for r in results if r["status"] == "dependent"
        ]
        unresolved = [
            r["point_id"]
            for r in results
            if r["status"] not in {"independent", "dependent", "curve_exact"}
        ]
        payload = {
            "curve_id": int(args.curve_id),
            "status": (
                "rank_growth"
                if independent
                else ("resolved" if not unresolved else "research_unresolved")
            ),
            "selected": len(results),
            "new_independent": len(independent),
            "exact_dependent": len(dependent),
            "exact_inconclusive": len(unresolved),
            "independent_point_ids": independent,
            "dependent_point_ids": dependent,
            "inconclusive_point_ids": unresolved,
            "best_rigorous_lower": final_state.get("rigorous_lower"),
            "rigorous_upper": final_state.get("rigorous_upper"),
            "exact_rank": final_state.get("exact_rank"),
            "results": results,
        }
        print(MARKER + json.dumps(payload, sort_keys=True), flush=True)
        print(COMPAT_MARKER + json.dumps(payload, sort_keys=True), flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
