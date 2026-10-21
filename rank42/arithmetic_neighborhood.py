"""Exact, bounded arithmetic-neighborhood computations for core Tools."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from fractions import Fraction
import subprocess
from typing import Iterable

from rank42.curve_research_state import curve_rank_summary_map


WORKER_SCHEMA = "rank42/arithmetic-neighborhood/isogeny-class-v1"
TWIST_SCHEMA = "rank42/arithmetic-neighborhood/twist-neighborhood-v1"
PROBE_SCHEMA = "rank42/arithmetic-neighborhood/isogeny-probe-v1"
RATIONAL_PRIME_ISOGENY_DEGREES = (2, 3, 5, 7, 11, 13, 17, 19, 37, 43, 67, 163)
WORKER_DIR = Path(__file__).resolve().parent / "neighborhood_workers"


class WorkerTimeout(RuntimeError):
    """A bounded scientific worker exceeded its timeout; result is inconclusive."""



@dataclass(frozen=True)
class StoredCurve:
    curve_id: int
    family: str
    parameter: str
    ainvs: tuple[str, str, str, str, str]
    score: float | None
    root_number: int | None
    generic_lower: int | None
    descent_lower: int | None
    exact_rank: int | None
    conductor: str | None
    discriminant: str | None
    authoritative_lower: int | None = None
    rank_inconsistent: bool = False

    @property
    def rigorous_lower(self) -> int | None:
        if self.authoritative_lower is not None:
            return self.authoritative_lower
        vals = [
            value for value in
            (self.exact_rank, self.descent_lower, self.generic_lower)
            if value is not None
        ]
        return max(vals) if vals else None


def _table_columns(db, table: str) -> set[str]:
    try:
        rows = db.execute(f"PRAGMA table_info({table})").fetchall()
    except Exception:
        return set()
    out = set()
    for row in rows:
        try:
            out.add(str(row["name"]))
        except Exception:
            out.add(str(row[1]))
    return out


def _select_expr(columns: set[str], name: str) -> str:
    return name if name in columns else f"NULL AS {name}"


def parse_ainvs(raw) -> tuple[str, str, str, str, str] | None:
    if raw is None:
        return None
    value = raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return None
    if isinstance(value, dict):
        value = [value.get(k) for k in ("a1", "a2", "a3", "a4", "a6")]
    if not isinstance(value, (list, tuple)) or len(value) != 5:
        return None
    if any(item is None for item in value):
        return None
    return tuple(str(item) for item in value)


def _canonical_q(value: object) -> str:
    text = str(value).strip()
    try:
        q = Fraction(text)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"invalid rational coefficient: {text!r}") from exc
    if q.denominator == 1:
        return str(q.numerator)
    return f"{q.numerator}/{q.denominator}"


def model_key(ainvs: Iterable[object]) -> str:
    values = tuple(_canonical_q(value) for value in ainvs)
    if len(values) != 5:
        raise ValueError("elliptic-curve a-invariants must contain five entries")
    return json.dumps(values, separators=(",", ":"))


def list_stored_curves(db) -> list[StoredCurve]:
    columns = _table_columns(db, "curves")
    if not {"id", "a_invariants_json"}.issubset(columns):
        return []
    wanted = (
        "id", "family", "parameter", "a_invariants_json", "score",
        "root_number", "generic_lower", "descent_lower", "exact_rank",
        "conductor", "discriminant",
    )
    select = ", ".join(_select_expr(columns, name) for name in wanted)
    rows = db.execute(
        f"""
        SELECT {select}
        FROM curves
        WHERE a_invariants_json IS NOT NULL
          AND TRIM(a_invariants_json)<>''
        ORDER BY id DESC
        """
    ).fetchall()
    rank_states = curve_rank_summary_map(db, [int(row["id"]) for row in rows])
    out = []
    for row in rows:
        data = dict(row)
        rank_state = rank_states.get(int(data["id"])) or {}
        ainvs = parse_ainvs(data.get("a_invariants_json"))
        if ainvs is None:
            continue
        out.append(
            StoredCurve(
                curve_id=int(data["id"]),
                family=str(data.get("family") or "curve"),
                parameter=str(data.get("parameter") or ""),
                ainvs=ainvs,
                score=(
                    float(data["score"]) if data.get("score") is not None else None
                ),
                root_number=(
                    int(data["root_number"])
                    if data.get("root_number") is not None else None
                ),
                generic_lower=(
                    int(data["generic_lower"])
                    if data.get("generic_lower") is not None else None
                ),
                descent_lower=(
                    int(data["descent_lower"])
                    if data.get("descent_lower") is not None else None
                ),
                exact_rank=(
                    int(rank_state["exact_rank"])
                    if rank_state.get("exact_rank") is not None else None
                ),
                conductor=(
                    str(data["conductor"])
                    if data.get("conductor") is not None else None
                ),
                discriminant=(
                    str(data["discriminant"])
                    if data.get("discriminant") is not None else None
                ),
                authoritative_lower=(
                    int(rank_state["rigorous_lower"])
                    if int(rank_state.get("rigorous_lower") or 0) > 0
                    or rank_state.get("exact_rank") is not None
                    else None
                ),
                rank_inconsistent=bool(rank_state.get("rank_inconsistent")),
            )
        )
    return out


def stored_model_index(records: Iterable[StoredCurve]) -> dict[str, list[StoredCurve]]:
    out: dict[str, list[StoredCurve]] = {}
    for record in records:
        out.setdefault(model_key(record.ainvs), []).append(record)
    return out


def worker_cache_key(record: StoredCurve) -> str:
    return f"{record.curve_id}:{model_key(record.ainvs)}"


def twist_cache_key(record: StoredCurve, bound: int) -> str:
    return f"{worker_cache_key(record)}:twists:{max(1, int(bound))}"


def probe_cache_key(
    record: StoredCurve,
    primes: Iterable[int],
    per_degree_timeout: int,
) -> str:
    degrees = ",".join(str(int(p)) for p in primes)
    return (
        f"{worker_cache_key(record)}:probe:{degrees}:"
        f"{max(2, int(per_degree_timeout))}"
    )


def run_isogeny_worker(
    *,
    science_python: str,
    worker_path=None,
    ainvs: Iterable[object],
    timeout: int = 90,
) -> dict:
    python = str(science_python or "").strip()
    if not python:
        raise RuntimeError("Rank Hunter scientific Python is not configured")
    worker = Path(worker_path or WORKER_DIR / "isogeny_worker.py").resolve()
    if not worker.is_file():
        raise RuntimeError(f"isogeny worker not found: {worker}")

    request = {
        "schema": "rank42/arithmetic-neighborhood/isogeny-request-v1",
        "ainvs": [str(value) for value in ainvs],
    }
    try:
        proc = subprocess.run(
            [python, str(worker)],
            input=json.dumps(request),
            text=True,
            capture_output=True,
            timeout=max(5, int(timeout)),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise WorkerTimeout(
            f"isogeny-class computation timed out after {max(5, int(timeout))}s"
        ) from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            "isogeny worker failed"
            + (f": {detail[-1200:]}" if detail else "")
        )
    try:
        payload = json.loads(proc.stdout)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("isogeny worker returned invalid JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema") != WORKER_SCHEMA:
        raise RuntimeError("isogeny worker returned an unsupported schema")
    if not isinstance(payload.get("nodes"), list) or not isinstance(
        payload.get("edges"), list
    ):
        raise RuntimeError("isogeny worker returned malformed graph data")
    return payload



def run_twist_worker(
    *,
    science_python: str,
    worker_path=None,
    ainvs: Iterable[object],
    bound: int,
    timeout: int = 120,
) -> dict:
    python = str(science_python or "").strip()
    if not python:
        raise RuntimeError("Rank Hunter scientific Python is not configured")
    worker = Path(worker_path or WORKER_DIR / "twist_worker.py").resolve()
    if not worker.is_file():
        raise RuntimeError(f"twist worker not found: {worker}")

    limit = max(1, min(int(bound), 250))
    request = {
        "schema": "rank42/arithmetic-neighborhood/twist-request-v1",
        "ainvs": [str(value) for value in ainvs],
        "bound": limit,
    }
    try:
        proc = subprocess.run(
            [python, str(worker)],
            input=json.dumps(request),
            text=True,
            capture_output=True,
            timeout=max(5, int(timeout)),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"quadratic-twist computation timed out after {max(5, int(timeout))}s"
        ) from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            "quadratic-twist worker failed"
            + (f": {detail[-1200:]}" if detail else "")
        )
    try:
        payload = json.loads(proc.stdout)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("quadratic-twist worker returned invalid JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema") != TWIST_SCHEMA:
        raise RuntimeError("quadratic-twist worker returned an unsupported schema")
    if not isinstance(payload.get("twists"), list):
        raise RuntimeError("quadratic-twist worker returned malformed neighborhood data")
    return payload



def run_isogeny_probe(
    *,
    science_python: str,
    worker_path=None,
    ainvs: Iterable[object],
    primes: Iterable[int] = RATIONAL_PRIME_ISOGENY_DEGREES,
    per_degree_timeout: int = 12,
) -> dict:
    python = str(science_python or "").strip()
    if not python:
        raise RuntimeError("Rank Hunter scientific Python is not configured")
    worker = Path(worker_path or WORKER_DIR / "isogeny_probe_worker.py").resolve()
    if not worker.is_file():
        raise RuntimeError(f"isogeny probe worker not found: {worker}")

    allowed = set(RATIONAL_PRIME_ISOGENY_DEGREES)
    requested = []
    for value in primes:
        degree = int(value)
        if degree not in allowed:
            raise ValueError(f"unsupported rational prime isogeny degree: {degree}")
        if degree not in requested:
            requested.append(degree)

    timeout = max(2, int(per_degree_timeout))
    hits = []
    checked = []
    timed_out = []
    errors = []

    for degree in requested:
        request = {
            "schema": "rank42/arithmetic-neighborhood/isogeny-probe-request-v1",
            "ainvs": [str(value) for value in ainvs],
            "degree": degree,
        }
        try:
            proc = subprocess.run(
                [python, str(worker)],
                input=json.dumps(request),
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            timed_out.append(degree)
            continue

        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            errors.append(
                {
                    "degree": degree,
                    "detail": detail[-800:] if detail else "worker failed",
                }
            )
            continue

        try:
            payload = json.loads(proc.stdout)
        except (TypeError, ValueError):
            errors.append({"degree": degree, "detail": "invalid JSON"})
            continue
        if not isinstance(payload, dict) or payload.get("schema") != PROBE_SCHEMA:
            errors.append({"degree": degree, "detail": "unsupported schema"})
            continue

        checked.append(degree)
        for item in payload.get("isogenies") or []:
            hits.append(item)

    return {
        "schema": "rank42/arithmetic-neighborhood/isogeny-probe-summary-v1",
        "requested_degrees": requested,
        "checked_degrees": checked,
        "timed_out_degrees": timed_out,
        "errors": errors,
        "hits": hits,
        "complete_for_requested_degrees": not timed_out and not errors,
    }
