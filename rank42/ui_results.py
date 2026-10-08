"""Parse Rank Hunter UI job logs into operator-facing result summaries.

This module deliberately treats the durable job log as the per-job source of
truth.  Scientific totals still live in SQLite; these summaries answer the
operator question "what did *this run* find?" without conflating concurrent or
repeated searches.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from typing import Any


_RE_CANDIDATE = re.compile(r"^\[(\d+)/(\d+)\]\s+curve\s+#(\d+)\b(?:\s+t=([^\s]+)\s+score=([-+0-9.eE]+))?", re.MULTILINE)
_RE_CANDIDATE_DONE = re.compile(r"^\[candidate done\]\s+(\d+)/(\d+)\s*$", re.MULTILINE)
_RE_CHART = re.compile(r"^\s*\[chart\s+(\d+)/(\d+)\]", re.MULTILINE)
_RE_STAGE = re.compile(r"^\s*H=(\d+)\s+(.+)$", re.MULTILINE)
_RE_NATIVE = re.compile(r"NEW NATIVE QUARTIC HIT\(S\):\s*(\d+)")
_RE_MAPPED = re.compile(r"mapped unique points outside known ±sections\s*=\s*(\d+)")
_RE_PASS = re.compile(r"PASS\s*->\s*screened basis size\s+(\d+)")
_RE_EXTRA_HIT = re.compile(r"EXTRA-POINT HIT:\s*basis\s+\d+\s*->\s*(\d+)")
_RE_HEADER_CANDIDATES = re.compile(r"^candidates\s*=\s*(\d+)\s*$", re.MULTILINE)
_RE_HEADER_CHARTS = re.compile(r"^charts/candidate\s*=\s*(\d+)\s*$", re.MULTILINE)

_RE_STAGE1 = re.compile(r"\[stage\s+1/(\d+)\]\s+retained\s+([\d,]+)\s+from\s+([\d,]+)")
_RE_STAGE_N = re.compile(r"\[stage\s+(\d+)/(\d+)\]\s+rescored\s+([\d,]+);\s+retained\s+([\d,]+)")
_RE_DONE_WRITE = re.compile(r"\[done\]\s+wrote\s+([\d,]+)\s+candidates\s+to\s+(.+)$", re.MULTILINE)
_RE_BEST = re.compile(r"\[best\]\s*\(\s*([-+0-9.eE]+)")

_RE_PLAY_PLANNED = re.compile(r"^PLAYGROUND planned\s*=\s*(\d+)\s*$", re.MULTILINE)
_RE_PLAY_TRIAL = re.compile(
    r"^\[playground (\d+)/(\d+)\] mode=([^\s]+) source=(.*?) A=(-?\d+) B=(-?\d+)\s*$",
    re.MULTILINE,
)
_RE_PLAY_DONE = re.compile(
    r"^\[playground done\] (\d+)/(\d+) candidates=(\d+) screened=(\d+) rigorous=(\d+) status=([^\s]+)\s*$",
    re.MULTILINE,
)
_RE_PLAY_HIT = re.compile(
    r"^\[playground hit\] curve #(\d+) rigorous_lower=(\d+) A=(-?\d+) B=(-?\d+) points=(\d+)\s*$",
    re.MULTILINE,
)
_RE_PLAY_RESULT = re.compile(r"^RANK42_PLAYGROUND_RESULT=(\{.*\})$", re.MULTILINE)

_RE_HIGH_SCORE = re.compile(r"^\[(?:high-rank|general-hunt) score\] (\d+)/(\d+) best=([-+0-9.eE]+)\s*$", re.MULTILINE)
_RE_HIGH_SHORTLIST = re.compile(r"^\[(?:high-rank|general-hunt) shortlist\] kept=(\d+) best=([^\s]+) cutoff=([^\s]+)\s*$", re.MULTILINE)
_RE_HIGH_PLANNED = re.compile(r"^(?:HIGH-RANK|GENERAL-HUNT) planned\s*=\s*(\d+)\s*$", re.MULTILINE)
_RE_HIGH_TRIAL = re.compile(
    r"^\[(?:high-rank|general-hunt) (\d+)/(\d+)\] mode=([^\s]+) source=(.*?) A=(-?\d+) B=(-?\d+) nagao=([-+0-9.eE]+)\s*$",
    re.MULTILINE,
)
_RE_HIGH_STAGE = re.compile(r"^\[(?:high-rank|general-hunt) stage\] (\d+)/(\d+) H=(\d+) (.+)$", re.MULTILINE)
_RE_HIGH_DONE = re.compile(
    r"^\[(?:high-rank|general-hunt) done\] (\d+)/(\d+) rational_points=(\d+) screened=(\d+) rigorous=(\d+) status=([^\s]+)\s*$",
    re.MULTILINE,
)
_RE_HIGH_HIT = re.compile(
    r"^\[(?:high-rank|general-hunt) hit\] curve #(\d+) rigorous_lower=(\d+) A=(-?\d+) B=(-?\d+) points=(\d+) nagao=([-+0-9.eE]+)\s*$",
    re.MULTILINE,
)
_RE_HIGH_RESULT = re.compile(r"^RANK42_(?:HIGH_RANK_PLAYGROUND|GENERAL_HUNT)_RESULT=(\{.*\})$", re.MULTILINE)
_RE_FOCUS_STAGE = re.compile(r"^\[focus stage\] H=(\d+) (.+)$", re.MULTILINE)
_RE_FOCUS_HIT = re.compile(r"^\[focus hit\] curve #(\d+) rigorous_lower=(\d+) points=(\d+)\s*$", re.MULTILINE)
_RE_FOCUS_RESULT = re.compile(r"^RANK42_PLAYGROUND_FOCUS_RESULT=(\{.*\})$", re.MULTILINE)
_RE_MESTRE_STAGE = re.compile(r"^\s*\[mestre stage\] mode=([^\s]+) H=(\d+) (.+)$", re.MULTILINE)
_RE_MESTRE_MAPPED = re.compile(r"mapped unique points outside (?:known subgroup|listed basis)\s*=\s*(\d+)")
_RE_MESTRE_HIT = re.compile(r"^\s*\[mestre hit\] curve #(\d+) rigorous_lower=(\d+) points=(\d+)\s*$", re.MULTILINE)
_RE_MESTRE_RESULT = re.compile(r"^RANK42_MESTRE_SEARCH_RESULT=(\{.*\})$", re.MULTILINE)
_RE_ANY_RESULT = re.compile(r"^(RANK(?:42|_HUNTER)_[A-Z0-9_]+(?:_RESULT|_POOL))=(\{.*\})$", re.MULTILINE)


def _int(text: str) -> int:
    return int(str(text).replace(",", ""))


def _metadata(row: Any) -> dict[str, Any]:
    raw = row["metadata_json"] if row is not None and "metadata_json" in row.keys() else None
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def pipeline_retention_quartic_summary(db, row: Any) -> dict[str, Any] | None:
    """Describe persisted quartic hits whose Pipeline curve was not retained.

    Builder retention is allowed to remove a fresh curve while preserving its
    durable quartic-search history.  This projection makes that distinction
    explicit for the Results UI without changing retention policy.
    """
    if row is None or str(row["kind"]) != "pipeline_search":
        return None

    metadata = _metadata(row)
    run_id = metadata.get("pipeline_run_id")
    try:
        run_id = int(run_id)
    except (TypeError, ValueError):
        return None

    required = {
        "search_pipeline_runs",
        "search_pipeline_candidates",
        "quartic_searches",
        "quartic_points",
    }
    names = {
        str(rec["name"])
        for rec in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if not required.issubset(names):
        return None

    run = db.execute(
        "SELECT run_config_json FROM search_pipeline_runs WHERE id=?",
        (run_id,),
    ).fetchone()
    if run is None:
        return None
    try:
        run_config = json.loads(run["run_config_json"] or "{}")
    except (TypeError, ValueError):
        run_config = {}
    if not isinstance(run_config, dict):
        run_config = {}
    floor = max(0, int(run_config.get("retention_floor") or 0))
    if floor <= 0:
        return {
            "run_id": run_id,
            "retention_floor": floor,
            "quartic_hits": 0,
            "candidates": [],
        }

    started = None
    finished = None
    keys = set(row.keys())
    if "started_at" in keys:
        started = row["started_at"]
    if not started and "created_at" in keys:
        started = row["created_at"]
    if "finished_at" in keys:
        finished = row["finished_at"]

    sql = """
        WITH dropped AS (
            SELECT parameter,
                   MAX(COALESCE(rigorous_lower,0)) AS rigorous_lower
            FROM search_pipeline_candidates
            WHERE run_id=? AND curve_id IS NULL
            GROUP BY parameter
        )
        SELECT qs.parameter AS parameter,
               dropped.rigorous_lower AS rigorous_lower,
               COUNT(qp.id) AS quartic_hits,
               COUNT(DISTINCT qs.id) AS quartic_searches
        FROM quartic_searches qs
        JOIN quartic_points qp ON qp.search_id=qs.id
        JOIN dropped ON dropped.parameter=qs.parameter
        WHERE qs.curve_id IS NULL
          AND dropped.rigorous_lower < ?
    """
    args = [run_id, floor]
    if started:
        sql += " AND qp.created_at>=?"
        args.append(str(started))
    if finished:
        sql += " AND qp.created_at<=?"
        args.append(str(finished))
    sql += """
        GROUP BY qs.parameter,dropped.rigorous_lower
        HAVING COUNT(qp.id)>0
        ORDER BY COUNT(qp.id) DESC,qs.parameter
    """

    candidates = [
        {
            "parameter": str(rec["parameter"]),
            "rigorous_lower": int(rec["rigorous_lower"] or 0),
            "quartic_hits": int(rec["quartic_hits"] or 0),
            "quartic_searches": int(rec["quartic_searches"] or 0),
        }
        for rec in db.execute(sql, args).fetchall()
    ]
    return {
        "run_id": run_id,
        "retention_floor": floor,
        "quartic_hits": sum(rec["quartic_hits"] for rec in candidates),
        "candidates": candidates,
    }


def _base(row: Any) -> dict[str, Any]:
    return {
        "job_id": int(row["id"]),
        "kind": str(row["kind"]),
        "label": str(row["label"]),
        "job_status": str(row["status"]),
        "exit_code": row["exit_code"],
        "result_status": "RUNNING" if row["status"] in {"queued", "running", "stopping"} else "UNKNOWN",
        "interesting": False,
    }


def summarize_chart_job(row: Any, log: str) -> dict[str, Any]:
    out = _base(row)
    markers = list(_RE_CANDIDATE.finditer(log))
    header_candidates = _RE_HEADER_CANDIDATES.search(log)
    header_charts = _RE_HEADER_CHARTS.search(log)
    stages = list(_RE_STAGE.finditer(log))
    completed = list(_RE_CANDIDATE_DONE.finditer(log))
    current = markers[-1] if markers else None

    native_hits = sum(_int(m.group(1)) for m in _RE_NATIVE.finditer(log))
    mapped = sum(_int(m.group(1)) for m in _RE_MAPPED.finditer(log))
    pass_sizes = [_int(m.group(1)) for m in _RE_PASS.finditer(log)]
    hit_sizes = [_int(m.group(1)) for m in _RE_EXTRA_HIT.finditer(log)]
    best = max([14, *pass_sizes, *hit_sizes])
    growth_curves = len(list(_RE_EXTRA_HIT.finditer(log)))

    out.update(
        {
            "candidates_planned": _int(header_candidates.group(1)) if header_candidates else _metadata(row).get("count"),
            "candidates_tested": len(markers),
            "candidates_completed": len(completed) if completed else (len(markers) if row["status"] == "succeeded" else 0),
            "current_candidate": _int(current.group(1)) if current else None,
            "current_curve_id": _int(current.group(3)) if current else None,
            "current_parameter": current.group(4) if current and current.group(4) is not None else None,
            "charts_per_candidate": _int(header_charts.group(1)) if header_charts else None,
            "charts_searched": len(list(_RE_CHART.finditer(log))),
            "quartic_stages_checked": len(stages),
            "timeouts": sum("TIMEOUT" in m.group(2) for m in stages),
            "errors": sum("ERROR" in m.group(2) for m in stages)
            + log.count("CONSTRUCTION ERROR:")
            + log.count("FULL MODEL ERROR:")
            + log.count("UNEXPECTED ERROR:"),
            "new_native_fibers": native_hits,
            "mapped_extra_points": mapped,
            "rank_growth_curves": growth_curves,
            "best_screened_rank": best,
        }
    )

    if row["status"] in {"queued", "running", "stopping"}:
        status = "RUNNING"
    elif row["status"] != "succeeded":
        status = "JOB FAILED"
    elif best > 15:
        status = "RECORD CANDIDATE"
    elif growth_curves > 0 or best >= 15:
        status = "RANK GROWTH"
    elif mapped > 0:
        status = "EXTRA POINT"
    elif native_hits > 0:
        status = "NEW FIBER"
    else:
        status = "NO HITS"

    out["result_status"] = status
    out["interesting"] = status in {"NEW FIBER", "EXTRA POINT", "RANK GROWTH", "RECORD CANDIDATE"}
    return out


def summarize_nagao_job(row: Any, log: str) -> dict[str, Any]:
    out = _base(row)
    stage1 = _RE_STAGE1.search(log)
    later = list(_RE_STAGE_N.finditer(log))
    done = _RE_DONE_WRITE.search(log)
    best_scores = [float(m.group(1)) for m in _RE_BEST.finditer(log)]

    survivor_counts = []
    parameters_screened = None
    if stage1:
        survivor_counts.append(_int(stage1.group(2)))
        parameters_screened = _int(stage1.group(3))
    survivor_counts.extend(_int(m.group(4)) for m in later)

    out.update(
        {
            "parameters_screened": parameters_screened,
            "stage_survivors": survivor_counts,
            "candidates_written": _int(done.group(1)) if done else None,
            "output_file": done.group(2).strip() if done else None,
            "best_nagao_score": max(best_scores) if best_scores else None,
        }
    )
    if row["status"] in {"queued", "running", "stopping"}:
        status = "RUNNING"
    elif row["status"] == "succeeded":
        status = "CANDIDATES READY"
    else:
        status = "JOB FAILED"
    out["result_status"] = status
    return out




def _structured_result(regex, log: str) -> dict[str, Any] | None:
    matches = list(regex.finditer(log))
    if not matches:
        return None
    try:
        value = json.loads(matches[-1].group(1))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def summarize_workbench(row: Any, log: str) -> dict[str, Any]:
    out = _base(row)
    planned_match = _RE_PLAY_PLANNED.search(log)
    trials = list(_RE_PLAY_TRIAL.finditer(log))
    done = list(_RE_PLAY_DONE.finditer(log))
    hits = list(_RE_PLAY_HIT.finditer(log))
    payload = _structured_result(_RE_PLAY_RESULT, log)
    current = trials[-1] if trials else None
    best = max([int(m.group(2)) for m in hits], default=0)
    if payload:
        best = max(best, int(payload.get("best_rigorous_lower") or 0))
    out.update(
        {
            "trials_planned": int(planned_match.group(1)) if planned_match else (payload or {}).get("trials_planned"),
            "trials_tested": len(trials) if trials else (payload or {}).get("trials_tested", 0),
            "trials_completed": len(done),
            "rigorous_hits": len(hits) if hits else (payload or {}).get("rigorous_hits", 0),
            "best_rigorous_lower": best,
            "current_trial": int(current.group(1)) if current else None,
            "current_mode": current.group(3) if current else (payload or {}).get("mode"),
            "current_source": current.group(4) if current else None,
            "current_A": int(current.group(5)) if current else None,
            "current_B": int(current.group(6)) if current else None,
            "target_lower": (payload or {}).get("target_lower") or _metadata(row).get("target"),
        }
    )
    if row["status"] in {"queued", "running", "stopping"}:
        status = "RUNNING"
    elif row["status"] != "succeeded":
        status = "JOB FAILED"
    elif out["rigorous_hits"]:
        status = "PLAYGROUND HIT"
    else:
        status = "NO PROVEN HIT"
    out["result_status"] = status
    out["interesting"] = bool(out["rigorous_hits"])
    return out


def summarize_general_hunt(row: Any, log: str) -> dict[str, Any]:
    out = _base(row)
    scores = list(_RE_HIGH_SCORE.finditer(log))
    shortlist = _RE_HIGH_SHORTLIST.search(log)
    planned = _RE_HIGH_PLANNED.search(log)
    trials = list(_RE_HIGH_TRIAL.finditer(log))
    stages = list(_RE_HIGH_STAGE.finditer(log))
    done = list(_RE_HIGH_DONE.finditer(log))
    hits = list(_RE_HIGH_HIT.finditer(log))
    payload = _structured_result(_RE_HIGH_RESULT, log)
    current = trials[-1] if trials else None
    last_score = scores[-1] if scores else None
    best_screened = max([int(m.group(4)) for m in done], default=0)
    best_points = max([int(m.group(3)) for m in done], default=0)
    best_rigorous = max([int(m.group(2)) for m in hits], default=0)
    if payload:
        best_screened = max(best_screened, int(payload.get("best_screened_rank") or 0))
        best_points = max(best_points, int(payload.get("best_rational_point_count") or 0))
        best_rigorous = max(best_rigorous, int(payload.get("best_rigorous_lower") or 0))
    out.update({
        "pool_scored": int(last_score.group(1)) if last_score else (payload or {}).get("pool_scored", 0),
        "pool_total": int(last_score.group(2)) if last_score else (payload or {}).get("pool_scored", 0),
        "shortlist_planned": int(planned.group(1)) if planned else ((int(shortlist.group(1)) if shortlist else (payload or {}).get("shortlist_planned", 0))),
        "shortlist_searched": len(trials) if trials else (payload or {}).get("shortlist_searched", 0),
        "trials_completed": len(done),
        "rigorous_hits": len(hits) if hits else (payload or {}).get("rigorous_hits", 0),
        "best_rigorous_lower": best_rigorous,
        "best_screened_rank": best_screened,
        "best_rational_point_count": best_points,
        "ratpoints_timeouts": (payload or {}).get("ratpoints_timeouts", sum(1 for m in stages if "timeout" in m.group(4))),
        "best_nagao_score": (float(shortlist.group(2)) if shortlist and shortlist.group(2) != "none" else ((float(last_score.group(3)) if last_score else (payload or {}).get("best_nagao_score")))),
        "current_trial": int(current.group(1)) if current else None,
        "current_mode": current.group(3) if current else (payload or {}).get("mode"),
        "current_source": current.group(4) if current else None,
        "current_A": int(current.group(5)) if current else None,
        "current_B": int(current.group(6)) if current else None,
        "current_nagao": float(current.group(7)) if current else None,
        "target_lower": (payload or {}).get("target_lower") or _metadata(row).get("target"),
        "nagao_bound": (payload or {}).get("nagao_bound") or _metadata(row).get("nagao_bound"),
    })
    if row["status"] in {"queued", "running", "stopping"}:
        status = "RUNNING"
    elif row["status"] != "succeeded":
        status = "JOB FAILED"
    elif out["rigorous_hits"]:
        status = "GENERAL HUNT HIT"
    else:
        status = "NO PROVEN HIT"
    out["result_status"] = status
    out["interesting"] = bool(out["rigorous_hits"])
    return out


def summarize_playground_focus(row: Any, log: str) -> dict[str, Any]:
    out = _base(row)
    payload = _structured_result(_RE_FOCUS_RESULT, log) or {}
    hits = list(_RE_FOCUS_HIT.finditer(log))
    stages = list(_RE_FOCUS_STAGE.finditer(log))
    last_hit = hits[-1] if hits else None
    out.update({
        "curve_id": payload.get("curve_id") or (int(last_hit.group(1)) if last_hit else _metadata(row).get("curve_id")),
        "target_lower": payload.get("target_lower") or _metadata(row).get("target"),
        "focus_strategy": payload.get("focus_strategy"),
        "rational_points": int(payload.get("rational_points") or 0),
        "unique_exact_points_seen": int(payload.get("unique_exact_points_seen") or payload.get("rational_points") or 0),
        "subgroup_candidates_screened": int(payload.get("subgroup_candidates_screened") or 0),
        "numerically_novel_candidates": int(payload.get("numerically_novel_candidates") or 0),
        "best_novelty_relative_residual": payload.get("best_novelty_relative_residual"),
        "exact_candidate_attempts": int(payload.get("exact_candidate_attempts") or 0),
        "exact_dependent": int(payload.get("exact_dependent") or 0),
        "exact_inconclusive": int(payload.get("exact_inconclusive") or 0),
        "height_chunk_failures": int(payload.get("height_chunk_failures") or 0),
        "screened_rank": int(payload.get("screened_rank") or 0),
        "best_rigorous_lower": int(payload.get("best_rigorous_lower") or (int(last_hit.group(2)) if last_hit else 0)),
        "exact_rank": payload.get("exact_rank"),
        "ratpoints_height_reached": payload.get("ratpoints_height_reached") or (int(stages[-1].group(1)) if stages else None),
        "ratpoints_timed_out": bool(payload.get("ratpoints_timed_out")),
    })
    if row["status"] in {"queued", "running", "stopping"}:
        status = "RUNNING"
    elif row["status"] != "succeeded":
        status = "JOB FAILED"
    else:
        status = str(payload.get("status") or ("FOCUS RANK GROWTH" if hits else "FOCUS COMPLETE"))
    out["result_status"] = status
    out["interesting"] = status == "FOCUS RANK GROWTH"
    return out


def summarize_mestre_job(row: Any, log: str) -> dict[str, Any]:
    out = _base(row)
    payload = _structured_result(_RE_MESTRE_RESULT, log) or {}
    markers = list(_RE_CANDIDATE.finditer(log))
    completed = list(_RE_CANDIDATE_DONE.finditer(log))
    current = markers[-1] if markers else None
    stages = list(_RE_MESTRE_STAGE.finditer(log))
    hits = list(_RE_MESTRE_HIT.finditer(log))

    native_hits = int(payload.get("new_native_fibers") or sum(_int(m.group(1)) for m in _RE_NATIVE.finditer(log)))
    mapped = int(payload.get("mapped_extra_points") or sum(_int(m.group(1)) for m in _RE_MESTRE_MAPPED.finditer(log)))
    rigorous = int(payload.get("best_rigorous_lower") or max([0, *[_int(m.group(2)) for m in hits]]))
    screened = int(payload.get("best_screened_rank") or rigorous)
    growth_curves = int(payload.get("rank_growth_curves") or len({m.group(1) for m in hits}))

    out.update(
        {
            "candidates_planned": int(payload.get("candidates_planned") or _metadata(row).get("count") or 0),
            "candidates_tested": len(markers),
            "candidates_completed": int(payload.get("candidates_completed") or len(completed)),
            "current_candidate": _int(current.group(1)) if current else None,
            "current_curve_id": _int(current.group(3)) if current else None,
            "current_parameter": current.group(4) if current and current.group(4) is not None else None,
            "quartic_stages_checked": int(payload.get("quartic_stages_checked") or len(stages)),
            "timeouts": int(payload.get("ratpoints_timeouts") or sum("TIMEOUT" in m.group(3) for m in stages)),
            "errors": sum("ERROR" in m.group(3) for m in stages) + log.count("CONSTRUCTION ERROR:") + log.count("UNEXPECTED ERROR:"),
            "new_native_fibers": native_hits,
            "mapped_extra_points": mapped,
            "rank_growth_curves": growth_curves,
            "best_screened_rank": screened,
            "best_rigorous_lower": rigorous,
            "subgroup_candidates_screened": int(payload.get("subgroup_candidates_screened") or 0),
            "numerically_novel_candidates": int(payload.get("numerically_novel_candidates") or 0),
            "exact_candidate_attempts": int(payload.get("exact_candidate_attempts") or 0),
            "exact_dependent": int(payload.get("exact_dependent") or 0),
            "exact_inconclusive": int(payload.get("exact_inconclusive") or 0),
            "exact_scheduled_novel": int(payload.get("exact_scheduled_novel") or 0),
            "exact_scheduled_controls": int(payload.get("exact_scheduled_controls") or 0),
            "exact_skipped_non_novel": int(payload.get("exact_skipped_non_novel") or 0),
            "symmetry_duplicates_skipped": int(payload.get("symmetry_duplicates_skipped") or 0),
            "search_mode": payload.get("search_mode") or _metadata(row).get("search_mode"),
            "search_geometry": payload.get("search_geometry") or _metadata(row).get("search_geometry") or "native",
            "chart_strategy": payload.get("chart_strategy") or _metadata(row).get("chart_strategy"),
            "charts_searched": int(payload.get("charts_searched") or 0),
            "charts_base": int(payload.get("charts_base") or 0),
            "charts_subgroup": int(payload.get("charts_subgroup") or 0),
            "charts_free": int(payload.get("charts_free") or 0),
            "discovered_anchor_fibres": int(payload.get("discovered_anchor_fibres") or 0),
        }
    )

    if row["status"] in {"queued", "running", "stopping"}:
        status = "RUNNING"
    elif row["status"] != "succeeded":
        status = "JOB FAILED"
    elif growth_curves > 0:
        status = "RANK GROWTH"
    elif mapped > 0:
        status = "EXTRA POINT"
    elif native_hits > 0:
        status = "NEW FIBER"
    else:
        status = "NO HITS"
    out["result_status"] = status
    out["interesting"] = status in {"NEW FIBER", "EXTRA POINT", "RANK GROWTH"}
    return out



def summarize_independence(row: Any, log: str) -> dict[str, Any]:
    """Summarize the exact Independence Workbench result marker."""
    out = _base(row)
    matches = list(_RE_ANY_RESULT.finditer(log))
    payload = {}
    for match in reversed(matches):
        if match.group(1) == "RANK42_INDEPENDENCE_RESULT":
            try:
                value = json.loads(match.group(2))
            except Exception:
                value = {}
            if isinstance(value, dict):
                payload = value
            break
    for key in (
        "curve_id", "selected", "exact_attempts", "new_independent",
        "exact_dependent", "exact_inconclusive", "timeouts", "errors",
        "new_explicit_basis", "best_rigorous_lower",
    ):
        if key in payload:
            out[key] = payload.get(key)

    if row["status"] in {"queued", "running", "stopping"}:
        status = "RUNNING"
    elif row["status"] != "succeeded":
        status = "JOB FAILED"
    elif payload.get("status") == "evidence_conflict":
        status = "EVIDENCE CONFLICT"
    elif int(payload.get("new_independent") or 0) > 0:
        status = "RANK GROWTH"
    else:
        status = "INDEPENDENCE COMPLETE"
    out["result_status"] = status
    out["interesting"] = status in {"RANK GROWTH", "EVIDENCE CONFLICT"}
    return out


def summarize_catalog_sync(row: Any, log: str) -> dict[str, Any]:
    out = _base(row)
    if row["status"] in {"queued", "running", "stopping"}:
        out["result_status"] = "RUNNING"
    elif row["status"] == "succeeded":
        out["result_status"] = "EXTERNAL SYNCED"
    else:
        out["result_status"] = "JOB FAILED"
    return out


def summarize_record_extension(row: Any, log: str) -> dict[str, Any]:
    out = _base(row)
    rigorous = "RIGOROUS EXTERNAL-CURVE EXTENSION" in log
    if row["status"] in {"queued", "running", "stopping"}:
        status = "RUNNING"
    elif row["status"] != "succeeded":
        status = "JOB FAILED"
    elif rigorous:
        status = "RIGOROUS EXTENSION"
    elif '"status": "timeout"' in log:
        status = "FIXED-CURVE TIMEOUT"
    else:
        status = "FIXED-CURVE COMPLETE"
    out["result_status"] = status
    out["interesting"] = rigorous
    return out

def summarize_structured_fallback(row: Any, log: str) -> dict[str, Any] | None:
    """Parse common result-marker JSON from plugin/custom search jobs."""
    matches=list(_RE_ANY_RESULT.finditer(log))
    if not matches:
        return None
    try:
        payload=json.loads(matches[-1].group(2))
    except Exception:
        return None
    if not isinstance(payload,dict):
        return None
    out=_base(row)
    for key in (
        'candidates_completed','candidates_planned','new_native_fibers','mapped_extra_points',
        'numerically_novel_candidates','best_rigorous_lower','best_screened_rank','rigorous_hits','rank_growth_curves',
        'discovered_anchor_fibres','trials_completed','trials_planned','pool_id','candidates',
        'best_lower','best_curve_id','pool_candidates','candidates_total','candidates_done',
        'stopped_on_goal'
    ):
        if key in payload:
            out[key]=payload.get(key)
    if 'candidates' in payload and 'candidates_written' not in out:
        out['candidates_written']=payload.get('candidates')
    if payload.get('best_lower') is not None and out.get('best_rigorous_lower') is None:
        out['best_rigorous_lower']=payload.get('best_lower')
    if row['status'] in {'queued','running','stopping'}:
        status='RUNNING'
    elif row['status']!='succeeded':
        status='JOB FAILED'
    elif any(int(payload.get(key) or 0)>0 for key in ('numerically_novel_candidates','rigorous_hits','rank_growth_curves')):
        status='RANK GROWTH'
    elif int(payload.get('new_native_fibers') or 0)>0:
        status='NEW FIBER'
    elif 'new_native_fibers' not in payload and int(payload.get('discovered_anchor_fibres') or 0)>0:
        # Legacy structured search payloads sometimes exposed only a discovered
        # anchor count. Once a plugin reports explicit new_native_fibers, that
        # field is authoritative; an existing anchor pool is not a new hit.
        status='NEW FIBER'
    elif ('candidates' in payload and str(row['kind'])=='candidate_generate'):
        status='CANDIDATES READY'
    elif any(k in payload for k in ('new_native_fibers','numerically_novel_candidates','mapped_extra_points')):
        status='NO HITS'
    else:
        status='JOB COMPLETE'
    out['result_status']=status
    out['interesting']=status in {'NEW FIBER','RANK GROWTH','RECORD CANDIDATE','EXTRA POINT'}
    return out


def summarize_job(row: Any, log: str) -> dict[str, Any]:
    kind = str(row["kind"])
    structured = summarize_structured_fallback(row, log)
    if structured is not None and kind in {"candidate_generate","family_search","free_family_search","target_plugin","target_free","geometry_search","pipeline_search"}:
        return structured
    if kind in {"kihara_chart_batch", "kihara_chart_target"}:
        return summarize_chart_job(row, log)
    if kind == "nagao_search":
        return summarize_nagao_job(row, log)
    if kind == "catalog_sync":
        return summarize_catalog_sync(row, log)
    if kind == "record_extension":
        return summarize_record_extension(row, log)
    if kind == "independence":
        return summarize_independence(row, log)
    if kind in {"general_hunt", "high_rank_playground"}:
        return summarize_general_hunt(row, log)
    if kind == "playground":
        return summarize_workbench(row, log)
    if kind == "playground_focus":
        return summarize_playground_focus(row, log)
    if kind in {"mestre_quartic_batch", "mestre_quartic_target"}:
        return summarize_mestre_job(row, log)
    if kind == "catalog_score":
        out = _base(row)
        out["result_status"] = "RUNNING" if row["status"] in {"queued", "running", "stopping"} else ("CALIBRATION READY" if row["status"] == "succeeded" else "JOB FAILED")
        return out
    out = _base(row)
    if row["status"] in {"queued", "running", "stopping"}:
        out["result_status"] = "RUNNING"
    elif row["status"] == "succeeded":
        out["result_status"] = "JOB COMPLETE"
    else:
        out["result_status"] = "JOB FAILED"
    return out


def chart_history_sums(summaries: list[dict[str, Any]]) -> dict[str, int]:
    chart = [
        s for s in summaries
        if s.get("kind") in {
            "kihara_chart_batch", "kihara_chart_target",
            "mestre_quartic_batch", "mestre_quartic_target",
        }
    ]
    return {
        "candidates_tested": sum(int(s.get("candidates_tested") or 0) for s in chart),
        "new_native_fibers": sum(int(s.get("new_native_fibers") or 0) for s in chart),
        "mapped_extra_points_reported": sum(int(s.get("mapped_extra_points") or 0) for s in chart),
        "rank_growth_runs": sum(int(s.get("rank_growth_curves") or 0) for s in chart),
        "best_screened_rank": max([14, *[int(s.get("best_screened_rank") or 14) for s in chart]]),
    }
