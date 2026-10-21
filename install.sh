#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

[[ -f "$ROOT/rank_hunter_control_center.py" ]] || {
  echo "[Rank Hunter] ERROR: install.sh must be run from a Rank Hunter checkout." >&2
  exit 2
}
[[ -d "$ROOT/.git" || -f "$ROOT/.git" ]] || {
  echo "[Rank Hunter] ERROR: this directory is not a Git checkout: $ROOT" >&2
  exit 2
}

MODE="full"
DRY_RUN=0
SCIENCE_PYTHON_ARG=""
DB_PATH="${RANK_HUNTER_DB:-${RANK42_DB:-rank42.db}}"

usage() {
  cat <<'EOF'
Rank Hunter installer

Run this script from an existing Rank Hunter checkout. It installs into this
checkout only; it never creates or relocates the Rank Hunter repository.

Usage:
  bash install.sh [--lite] [--science-python PATH] [--db PATH] [--dry-run]

Modes:
  full (default)  Install application/runtime requirements, initialize pinned
                  submodules, and build/smoke CPU plus available GPU ratpoints.
  --lite          Install application/runtime requirements, official plugins,
                  and database state, but skip native ratpoints builds.
                  Existing CPU/GPU paths are preserved.

Options:
  --science-python PATH  Sage-capable Python executable to use for scientific jobs.
  --db PATH              Rank Hunter SQLite database path (default: rank42.db).
  --dry-run              Print the installation plan without changing files.
  -h, --help             Show this help.
EOF
}

log() { printf '[Rank Hunter] %s\n' "$*"; }
warn() { printf '[Rank Hunter] WARNING: %s\n' "$*" >&2; }
die() { printf '[Rank Hunter] ERROR: %s\n' "$*" >&2; exit 2; }

quote_cmd() {
  local out="" arg
  for arg in "$@"; do
    printf -v out '%s%q ' "$out" "$arg"
  done
  printf '%s' "$out"
}

run() {
  log "+ $(quote_cmd "$@")"
  if (( DRY_RUN == 0 )); then
    "$@"
  fi
}

while (($#)); do
  case "$1" in
    --lite)
      MODE="lite"
      shift
      ;;
    --science-python)
      (($# >= 2)) || die "--science-python requires a path"
      SCIENCE_PYTHON_ARG="$2"
      shift 2
      ;;
    --db)
      (($# >= 2)) || die "--db requires a path"
      DB_PATH="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown installer argument: $1"
      ;;
  esac
done

detect_science_python() {
  local candidate
  if [[ -n "$SCIENCE_PYTHON_ARG" ]]; then
    printf '%s\n' "$SCIENCE_PYTHON_ARG"
    return
  fi

  for candidate in       "${RANK_HUNTER_SCIENCE_PYTHON:-}"       "${RANK42_SCIENCE_PYTHON:-}"       "$(command -v python 2>/dev/null || true)"       "$(command -v python3 2>/dev/null || true)"; do
    [[ -n "$candidate" ]] || continue
    if (( DRY_RUN == 1 )); then
      printf '%s\n' "$candidate"
      return
    fi
    if [[ -x "$candidate" ]] && "$candidate" -c 'import sage.all' >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      return
    fi
  done

  if command -v sage >/dev/null 2>&1; then
    candidate="$(sage -python -c 'import sys; print(sys.executable)' 2>/dev/null || true)"
    if [[ -n "$candidate" && -x "$candidate" ]] && "$candidate" -c 'import sage.all' >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      return
    fi
  fi

  if command -v conda >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1; then
    while IFS= read -r candidate; do
      [[ -n "$candidate" && -x "$candidate" ]] || continue
      if "$candidate" -c 'import sage.all' >/dev/null 2>&1; then
        printf '%s\n' "$candidate"
        return
      fi
    done < <(
      conda env list --json 2>/dev/null | python3 -c '
import json, os, sys
try:
    envs = json.load(sys.stdin).get("envs", [])
except Exception:
    envs = []
for env in envs:
    print(os.path.join(env, "bin", "python"))
'
    )
  fi

  return 1
}

require_cmd() {
  local name="$1"
  command -v "$name" >/dev/null 2>&1 || die "required host tool not found: $name"
}

SCIENCE_PYTHON="$(detect_science_python || true)"
[[ -n "$SCIENCE_PYTHON" ]] || die "no Sage-capable scientific Python found; activate Sage or pass --science-python PATH"

if (( DRY_RUN == 0 )); then
  require_cmd git
  require_cmd python3
  python3 -m venv --help >/dev/null 2>&1 || die "python3 venv support is missing (install python3-venv on Debian/Ubuntu)"
  [[ -x "$SCIENCE_PYTHON" ]] || die "scientific Python is not executable: $SCIENCE_PYTHON"
  "$SCIENCE_PYTHON" -c 'import sage.all' >/dev/null 2>&1 || die "$SCIENCE_PYTHON cannot import sage.all"
  "$SCIENCE_PYTHON" -m venv --help >/dev/null 2>&1 || die "$SCIENCE_PYTHON does not provide the venv module"
  if [[ "$MODE" == "full" ]]; then
    require_cmd make
    if command -v cc >/dev/null 2>&1; then
      CC_BIN="$(command -v cc)"
    elif command -v gcc >/dev/null 2>&1; then
      CC_BIN="$(command -v gcc)"
    else
      die "full install requires a C compiler (cc or gcc)"
    fi
    printf '#include <gmp.h>\n' | "$CC_BIN" -x c -E - >/dev/null 2>&1 ||       die "GMP development headers are required for native ratpoints builds"
  fi
else
  log "dry-run: prerequisite execution checks are skipped"
fi

log "installer mode: $MODE"
log "scientific Python: $SCIENCE_PYTHON"
log "database: $DB_PATH"

if [[ "$MODE" == "full" ]]; then
  run git submodule update --init --recursive
else
  log "lite mode: skipping native vendor submodule/build work"
fi

run bash scripts/install-official-plugins.sh

run env   RANK_HUNTER_SCIENCE_PYTHON="$SCIENCE_PYTHON"   RANK_HUNTER_UI_PYTHON="$SCIENCE_PYTHON"   bash scripts/install-ui.sh

CATALOG_STATUS="planned"
if (( DRY_RUN == 1 )); then
  log "+ env RANK_HUNTER_SCIENCE_PYTHON=$(printf '%q' "$SCIENCE_PYTHON") RANK_HUNTER_CATALOG_PROBE_OPTIONAL=1 bash scripts/install-catalog-sql.sh"
else
  env     RANK_HUNTER_SCIENCE_PYTHON="$SCIENCE_PYTHON"     RANK_HUNTER_CATALOG_PROBE_OPTIONAL=1     bash scripts/install-catalog-sql.sh
  CATALOG_STATUS="driver ready; live probe attempted"
fi

if (( DRY_RUN == 1 )); then
  log "+ $SCIENCE_PYTHON -c 'open_database_with_migrations(...)'"
  DB_STATUS="planned"
else
  RANK_HUNTER_INSTALL_DB="$DB_PATH" "$SCIENCE_PYTHON" - <<'PY'
import os
from pathlib import Path
from rank42.migration_manager import open_database_with_migrations

path = Path(os.environ["RANK_HUNTER_INSTALL_DB"])
db = open_database_with_migrations(path)
try:
    version = int(db.execute("PRAGMA user_version").fetchone()[0])
finally:
    db.close()
print(f"[Rank Hunter] database/schema ready: {path} (user_version={version})")
PY
  DB_STATUS="ready"
fi

CPU_STATUS="not built"
GPU_STATUS="not built"
CPU_READY=0
GPU_READY=0
CPU_PATH="$ROOT/vendor/ratpoints/ratpoints"
GPU_PATH="$ROOT/vendor/ratpoints-gpu/ratpoints_gpu"

if [[ "$MODE" == "full" ]]; then
  run bash scripts/install-ratpoints.sh
  if (( DRY_RUN == 0 )); then
    [[ -x "$CPU_PATH" ]] || die "CPU ratpoints installer returned without a vendored executable"
    "$CPU_PATH" '1 0 0 0 1' 10 -q -i >/dev/null 2>&1 || die "CPU ratpoints failed post-install smoke test"
    CPU_READY=1
    CPU_STATUS="ready ($CPU_PATH)"
  else
    CPU_STATUS="planned"
  fi

  if (( DRY_RUN == 1 )); then
    log "GPU ratpoints: conditional build/smoke when nvcc and a usable CUDA device are available"
    GPU_STATUS="conditional"
  elif command -v nvcc >/dev/null 2>&1; then
    if bash scripts/install-ratpoints-gpu.sh; then
      GPU_READY=1
      GPU_STATUS="ready ($GPU_PATH)"
    else
      GPU_STATUS="failed/unavailable (CPU remains usable)"
      warn "GPU ratpoints did not pass build/smoke; continuing with CPU support."
    fi
  else
    GPU_STATUS="unavailable (nvcc not found)"
  fi
else
  if (( DRY_RUN == 1 )); then
    CPU_STATUS="configured external path preserved (not inspected in dry-run)"
    GPU_STATUS="configured external path preserved (not inspected in dry-run)"
  else
    read_stored_runtime() {
      local key="$1"
      RANK_HUNTER_INSTALL_DB="$DB_PATH" RANK_HUNTER_INSTALL_KEY="$key" "$SCIENCE_PYTHON" - <<'PY'
import json
import os
from rank42.migration_manager import open_database_with_migrations

db = open_database_with_migrations(os.environ["RANK_HUNTER_INSTALL_DB"])
try:
    row = db.execute(
        "SELECT value_json FROM ui_settings WHERE key=?",
        (os.environ["RANK_HUNTER_INSTALL_KEY"],),
    ).fetchone()
finally:
    db.close()
if row is not None:
    try:
        value = json.loads(row["value_json"])
    except Exception:
        value = None
    if isinstance(value, str) and value.strip():
        print(value.strip())
PY
    }

    LITE_CPU="$(read_stored_runtime ratpoints)"
    LITE_GPU="$(read_stored_runtime ratpoints_gpu)"
    if [[ -n "$LITE_CPU" ]]; then
      if [[ -x "$LITE_CPU" ]]; then
        CPU_STATUS="configured external ($LITE_CPU)"
      else
        CPU_STATUS="configured but unavailable ($LITE_CPU)"
      fi
    elif command -v ratpoints >/dev/null 2>&1; then
      CPU_STATUS="PATH external ($(command -v ratpoints))"
    else
      CPU_STATUS="not configured; set CPU ratpoints in System -> Settings"
    fi
    if [[ -n "$LITE_GPU" ]]; then
      if [[ -x "$LITE_GPU" ]]; then
        GPU_STATUS="configured external ($LITE_GPU)"
      else
        GPU_STATUS="configured but unavailable ($LITE_GPU)"
      fi
    elif command -v ratpoints_gpu >/dev/null 2>&1; then
      GPU_STATUS="PATH external ($(command -v ratpoints_gpu))"
    else
      GPU_STATUS="not configured; set GPU ratpoints in System -> Settings"
    fi
  fi
fi

PLUGIN_STATUS="planned"
if (( DRY_RUN == 0 )); then
  OFFICIAL_PLUGINS="$ROOT/plugins"
  if ! git -C "$OFFICIAL_PLUGINS" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    die "official plugins checkout is missing at plugins/: $OFFICIAL_PLUGINS"
  fi
  PLUGIN_REV="$(git -C "$OFFICIAL_PLUGINS" rev-parse --short HEAD)"
  PLUGIN_COUNT="$(
    PYTHONPATH="$ROOT" "$SCIENCE_PYTHON" - <<'PY'
from pathlib import Path
from rank42.plugins import Plugin, discover_plugins

root = Path.cwd()
records = discover_plugins(root)
invalid = [rec for rec in records if not isinstance(rec, Plugin)]
if invalid:
    details = "; ".join(str(getattr(rec, "error", rec)) for rec in invalid[:3])
    raise SystemExit(f"invalid plugin manifests: {details}")
print(sum(isinstance(rec, Plugin) for rec in records))
PY
  )"
  if [[ "$PLUGIN_COUNT" -le 0 ]]; then
    die "no discoverable plugins found after official plugin install"
  fi
  PLUGIN_STATUS="ready ($PLUGIN_REV; $PLUGIN_COUNT discovered)"
fi

if (( DRY_RUN == 0 )); then
  RANK_HUNTER_INSTALL_DB="$DB_PATH"   RANK_HUNTER_INSTALL_SCIENCE="$SCIENCE_PYTHON"   RANK_HUNTER_INSTALL_CPU="$([[ "$CPU_READY" == "1" ]] && printf '%s' "$CPU_PATH" || true)"   RANK_HUNTER_INSTALL_GPU="$([[ "$GPU_READY" == "1" ]] && printf '%s' "$GPU_PATH" || true)"   "$SCIENCE_PYTHON" - <<'PY'
import os
from rank42.migration_manager import open_database_with_migrations
from rank42.settings_registry import seed_setting_if_missing

db = open_database_with_migrations(os.environ["RANK_HUNTER_INSTALL_DB"])
try:
    seed_setting_if_missing(db, "science_python", os.environ["RANK_HUNTER_INSTALL_SCIENCE"])
    cpu = os.environ.get("RANK_HUNTER_INSTALL_CPU")
    gpu = os.environ.get("RANK_HUNTER_INSTALL_GPU")
    if cpu:
        seed_setting_if_missing(db, "ratpoints", cpu)
    if gpu:
        seed_setting_if_missing(db, "ratpoints_gpu", gpu)
finally:
    db.close()
PY
fi

UI_RUNTIME="$ROOT/.venv-ui/bin/python"
if (( DRY_RUN == 0 )); then
  [[ -x "$UI_RUNTIME" ]] || die "UI runtime missing after install: $UI_RUNTIME"
  "$UI_RUNTIME" -c 'import sage.all, streamlit' >/dev/null 2>&1 ||     die "UI runtime cannot import both Sage and Streamlit after installation"
fi

echo
echo "========== Rank Hunter installation summary =========="
printf 'Mode:                %s\n' "$MODE"
printf 'Scientific Python:   %s\n' "$SCIENCE_PYTHON"
printf 'UI environment:      %s\n' "$ROOT/.venv-ui"
printf 'Database/schema:     %s (%s)\n' "$DB_PATH" "$DB_STATUS"
printf 'Catalog transport:   %s\n' "$CATALOG_STATUS"
printf 'CPU ratpoints:       %s\n' "$CPU_STATUS"
printf 'GPU ratpoints:       %s\n' "$GPU_STATUS"
printf 'Official plugins:    %s\n' "$PLUGIN_STATUS"
printf 'Launch:              bash scripts/run-ui.sh\n'
echo "======================================================"

if [[ "$MODE" == "lite" ]]; then
  echo
  echo "[Rank Hunter] Lite mode never changes configured ratpoints executable paths."
  echo "[Rank Hunter] If CPU/GPU paths are not already valid, set them under System -> Settings."
fi
