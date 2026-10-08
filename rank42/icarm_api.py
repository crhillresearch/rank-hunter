"""Authenticated ICARM leaderboard API client.

The public catalog synchronization remains in :mod:`rank42.catalog`.  This
module handles the *write* path only: preparing and explicitly submitting a
rigorous local witness set to ICARM's documented ``POST /api/submit`` endpoint.

Secrets are deliberately kept out of ``rank42.db`` and out of UI job command
lines.  ``ICARM_API_TOKEN`` takes precedence; otherwise Rank Hunter reads the
local ``.rank42-ui/icarm-token`` file, which is written mode 0600.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from rank42.catalog import sync_icarm
from rank42.db import connect, get_curve, log_event, now
from rank42.submission import icarm_api_body, stored_bad_primes

ICARM_API_SUBMIT_URL = "https://elliptic-rank.icarm.cloud/api/submit"
ICARM_PROFILE_URL = "https://elliptic-rank.icarm.cloud/profile"
TOKEN_ENV = "ICARM_API_TOKEN"
TOKEN_RELATIVE_PATH = Path(".rank42-ui") / "icarm-token"
USER_AGENT = "Rank-Hunter/0.7.14"


class ICARMAPIError(RuntimeError):
    """Bounded, sanitized error from the ICARM API."""

    def __init__(self, message: str, *, status: int | None = None, payload=None):
        super().__init__(message)
        self.status = status
        self.payload = payload


def _main_db_path(db) -> Path:
    rows = db.execute("PRAGMA database_list").fetchall()
    for row in rows:
        try:
            name = row["name"]
            path = row["file"]
        except Exception:
            name, path = row[1], row[2]
        if str(name) == "main" and str(path):
            return Path(str(path)).resolve()
    raise ICARMAPIError("could not determine rank42.db path for arithmetic metadata computation")


def compute_missing_bad_primes(
    db,
    row,
    *,
    science_command=None,
    project_root: str | os.PathLike = ".",
    timeout: int = 90,
    runner=None,
):
    """Compute/persist missing bad-prime metadata with the Sage backend.

    The Streamlit UI intentionally does not import Sage.  Instead this invokes
    the configured scientific Python command with ``rank42.curve_metadata``.
    The command has a hard timeout and never changes the stored curve model or
    witness coordinates; it only persists global-minimal arithmetic metadata.
    """
    if stored_bad_primes(row) is not None:
        return row
    curve_id = int(row["id"])
    command = list(science_command or [sys.executable])
    if not command:
        raise ICARMAPIError("scientific Python command is empty")
    cmd = [
        *command,
        "-m",
        "rank42.curve_metadata",
        "--db",
        str(_main_db_path(db)),
        "--id",
        str(curve_id),
    ]
    run = runner or subprocess.run
    try:
        proc = run(
            cmd,
            cwd=str(Path(project_root).resolve()),
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout)),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ICARMAPIError(
            f"bad-prime computation timed out after {int(timeout)} s for curve #{curve_id}"
        ) from exc
    except OSError as exc:
        raise ICARMAPIError(f"could not start bad-prime computation: {exc}") from exc
    if int(getattr(proc, "returncode", 1) or 0) != 0:
        detail = (getattr(proc, "stderr", "") or getattr(proc, "stdout", "") or "").strip()
        if len(detail) > 800:
            detail = detail[-800:]
        raise ICARMAPIError(
            f"bad-prime computation failed for curve #{curve_id}" + (f": {detail}" if detail else "")
        )
    refreshed = get_curve(db, curve_id)
    if refreshed is None or stored_bad_primes(refreshed) is None:
        raise ICARMAPIError(
            f"bad-prime computation completed but curve #{curve_id} still has no stored bad-prime metadata"
        )
    return refreshed


def token_path(project_root: str | os.PathLike = ".") -> Path:
    return Path(project_root).resolve() / TOKEN_RELATIVE_PATH


def _clean_token(value) -> str:
    token = str(value or "").strip()
    if "\n" in token or "\r" in token:
        raise ValueError("ICARM token must be one line")
    return token


def token_source(project_root: str | os.PathLike = ".", *, environ=None) -> str | None:
    env = os.environ if environ is None else environ
    if _clean_token(env.get(TOKEN_ENV, "")):
        return "environment"
    path = token_path(project_root)
    if path.exists() and _clean_token(path.read_text(encoding="utf-8")):
        return "local file"
    return None


def read_token(project_root: str | os.PathLike = ".", *, environ=None) -> str:
    env = os.environ if environ is None else environ
    token = _clean_token(env.get(TOKEN_ENV, ""))
    if token:
        return token
    path = token_path(project_root)
    if not path.exists():
        return ""
    return _clean_token(path.read_text(encoding="utf-8"))


def write_token(project_root: str | os.PathLike, token: str) -> Path:
    token = _clean_token(token)
    if not token:
        raise ValueError("ICARM token is empty")
    target = token_path(project_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".icarm-token-", dir=str(target.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(token + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
    return target


def delete_local_token(project_root: str | os.PathLike) -> bool:
    path = token_path(project_root)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def masked_token(project_root: str | os.PathLike = ".", *, environ=None) -> str:
    token = read_token(project_root, environ=environ)
    if not token:
        return "not configured"
    if len(token) <= 10:
        return "configured"
    return f"{token[:6]}…{token[-4:]}"


def _decode_json(raw: bytes):
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def submit_payload(
    payload: dict,
    *,
    token: str,
    timeout: int = 30,
    url: str = ICARM_API_SUBMIT_URL,
    opener=None,
) -> dict:
    """POST an already prepared exact ICARM payload.

    The token is sent only in the Authorization header and is never inserted
    into returned diagnostics or exception messages.
    """
    token = _clean_token(token)
    if not token:
        raise ICARMAPIError("ICARM API token is not configured")
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    open_fn = opener or urllib.request.urlopen
    try:
        with open_fn(request, timeout=float(timeout)) as response:
            raw = response.read()
            status = int(getattr(response, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        raw = exc.read() if hasattr(exc, "read") else b""
        parsed = _decode_json(raw)
        detail = None
        if isinstance(parsed, dict):
            detail = parsed.get("error") or parsed.get("message") or parsed.get("errors")
        message = f"ICARM submission rejected (HTTP {exc.code})"
        if detail:
            message += f": {detail}"
        raise ICARMAPIError(message, status=int(exc.code), payload=parsed) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ICARMAPIError(f"ICARM submission failed: {exc}") from exc

    parsed = _decode_json(raw)
    if not isinstance(parsed, dict):
        raise ICARMAPIError(f"ICARM returned non-JSON response (HTTP {status})", status=status)
    if status != 200 or parsed.get("ok") is not True:
        detail = parsed.get("error") or parsed.get("message") or parsed.get("errors")
        raise ICARMAPIError(
            f"ICARM submission was not accepted (HTTP {status})" + (f": {detail}" if detail else ""),
            status=status,
            payload=parsed,
        )
    return parsed


def default_commentary(row, *, version: str = "0.7.14") -> str:
    lower = max(int(row["descent_lower"] or 0), int(row["exact_rank"] or 0))
    rank_text = f"exact rank {int(row['exact_rank'])}" if row["exact_rank"] is not None else f"rigorous rank lower bound >= {lower}"
    return (
        f"Found with Rank Hunter v{version}; family={row['family']}; "
        f"parameter={row['parameter']}; local status: {rank_text}."
    )


def submit_curve(
    db,
    row,
    *,
    project_root: str | os.PathLike = ".",
    token: str | None = None,
    commentary: str | None = None,
    timeout: int = 30,
    opener=None,
) -> dict:
    body = icarm_api_body(row, commentary=commentary)
    active_token = _clean_token(token) if token is not None else read_token(project_root)
    result = submit_payload(body, token=active_token, timeout=timeout, opener=opener)
    board = result.get("leaderboard") or {}
    outcome = str(board.get("status") or "accepted")
    rank = board.get("rank")
    suffix = f" rank >= {rank}" if rank is not None else ""
    log_event(db, int(row["id"]), "catalog", f"ICARM API submission {outcome}{suffix}")
    return result


def refresh_submission_match(
    db,
    curve_id: int,
    result: dict,
    *,
    project_root: str | os.PathLike = ".",
    timeout: int = 30,
):
    """Best-effort refresh after a successful write, using ICARM's canonical key.

    The API documents ``canonical.key`` as a Q-isomorphism identifier.  After
    refreshing the public snapshot, matching that exact key avoids requiring
    Sage inside the Streamlit environment.  Failure to refresh never changes
    the successful submission result.
    """
    key = str(((result.get("canonical") or {}).get("key")) or "").strip()
    if not key:
        return None
    cache_dir = Path(project_root).resolve() / ".rank42-cache" / "catalogs" / "icarm"
    sync_icarm(db, cache_dir=cache_dir, timeout=int(timeout))
    row = db.execute(
        "SELECT * FROM external_curves WHERE source='icarm' AND curve_key=? AND active=1",
        (key,),
    ).fetchone()
    if row is None:
        return None
    db.execute(
        """
        INSERT INTO curve_catalog_checks(
            curve_id,source,status,source_label,source_url,source_rank,metadata_json,checked_at
        ) VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(curve_id,source) DO UPDATE SET
            status=excluded.status, source_label=excluded.source_label,
            source_url=excluded.source_url, source_rank=excluded.source_rank,
            metadata_json=excluded.metadata_json, checked_at=excluded.checked_at
        """,
        (
            int(curve_id), "icarm", "known", str(row["source_id"]), row["source_url"],
            int(row["rank_lower_bound"] or 0),
            json.dumps({"match": "ICARM canonical.key after API submission", "curve_key": key}, sort_keys=True),
            now(),
        ),
    )
    db.commit()
    return row


def parse_args():
    ap = argparse.ArgumentParser(description="Prepare or explicitly submit a rigorous Rank Hunter curve to ICARM")
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--id", type=int, required=True)
    ap.add_argument("--project-root", default=".")
    ap.add_argument("--commentary")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--metadata-timeout", type=int, default=90)
    ap.add_argument("--science-python", default=None, help="scientific Python/Sage command for missing arithmetic metadata")
    ap.add_argument("--submit", action="store_true", help="perform POST /api/submit")
    ap.add_argument("--yes", action="store_true", help="required with --submit; confirms the external write")
    return ap.parse_args()


def main():
    args = parse_args()
    db = connect(args.db)
    try:
        row = get_curve(db, int(args.id))
        if row is None:
            raise SystemExit(f"curve #{args.id} not found")
        commentary = args.commentary if args.commentary is not None else default_commentary(row)
        if stored_bad_primes(row) is None:
            science_command = shlex.split(args.science_python) if args.science_python else [sys.executable]
            row = compute_missing_bad_primes(
                db, row, project_root=args.project_root, science_command=science_command,
                timeout=int(args.metadata_timeout),
            )
        payload = icarm_api_body(row, commentary=commentary)
        if not args.submit:
            print(json.dumps(payload, indent=2, sort_keys=True))
            return
        if not args.yes:
            raise SystemExit("refusing external submission without --yes")
        result = submit_curve(
            db,
            row,
            project_root=args.project_root,
            commentary=commentary,
            timeout=int(args.timeout),
        )
        print(json.dumps(result, indent=2, sort_keys=True))
    finally:
        db.close()


if __name__ == "__main__":
    main()
