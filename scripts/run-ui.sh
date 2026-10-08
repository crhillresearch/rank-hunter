#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

SCIENCE_PYTHON_FILE="$PROJECT_ROOT/.rank42-ui/science-python"
if [[ ! -f "$SCIENCE_PYTHON_FILE" ]]; then
  echo "[Rank Hunter] Scientific Python marker not found: $SCIENCE_PYTHON_FILE" >&2
  echo "Activate the Sage environment, then run: bash install.sh" >&2
  exit 2
fi

SCIENCE_PYTHON="$(head -n 1 "$SCIENCE_PYTHON_FILE")"
UI_RUNTIME_DEFAULT="$PROJECT_ROOT/.venv-ui/bin/python"
UI_RUNTIME="${RANK_HUNTER_UI_RUNTIME_PYTHON:-${RANK_HUNTER_RUNTIME_PYTHON:-${RANK42_RUNTIME_PYTHON:-$UI_RUNTIME_DEFAULT}}}"

if [[ ! -x "$SCIENCE_PYTHON" ]]; then
  echo "[Rank Hunter] Scientific Python is not executable: $SCIENCE_PYTHON" >&2
  exit 2
fi
if ! "$SCIENCE_PYTHON" -c 'import sage.all' >/dev/null 2>&1; then
  echo "[Rank Hunter] Scientific Python cannot import Sage: $SCIENCE_PYTHON" >&2
  exit 2
fi
if [[ ! -x "$UI_RUNTIME" ]]; then
  echo "[Rank Hunter] UI runtime is not executable: $UI_RUNTIME" >&2
  echo "Run: bash install.sh" >&2
  exit 2
fi
if ! "$UI_RUNTIME" -c 'import sage.all, streamlit' >/dev/null 2>&1; then
  echo "[Rank Hunter] UI runtime must provide both Sage and Streamlit:" >&2
  echo "  $UI_RUNTIME" >&2
  echo "Rerun: bash install.sh" >&2
  exit 2
fi

# Scientific subprocesses use the persisted Settings value after first launch.
# This environment variable is a bootstrap/fallback only and does not overwrite
# an existing ui_settings.science_python row.
export RANK_HUNTER_SCIENCE_PYTHON="$SCIENCE_PYTHON"

# Sage currently imports two deprecated private mpmath compatibility modules.
MPMATH_WARNING_FILTERS="ignore::DeprecationWarning:mpmath.rational,ignore::DeprecationWarning:mpmath.math2"
export PYTHONWARNINGS="${PYTHONWARNINGS:+$PYTHONWARNINGS,}$MPMATH_WARNING_FILTERS"

export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"
DB_PATH="${RANK_HUNTER_DB:-${RANK42_DB:-rank42.db}}"

ACTIVE_THEME=""
NATIVE_THEME=""
IFS=$'\t' read -r ACTIVE_THEME NATIVE_THEME < <(
  "$UI_RUNTIME" -m rank42.theme_runtime     --project-root "$PROJECT_ROOT"     --db "$DB_PATH"
)
export RANK_HUNTER_ACTIVE_THEME_ID="$ACTIVE_THEME"

STREAMLIT_THEME_ARGS=()
if [[ -n "$NATIVE_THEME" ]]; then
  STREAMLIT_THEME_ARGS+=(--theme.base "$NATIVE_THEME")
fi

exec "$UI_RUNTIME" -m rank42.streamlit_launcher run "$PROJECT_ROOT/rank_hunter_control_center.py"   --server.headless true   --browser.gatherUsageStats false   "${STREAMLIT_THEME_ARGS[@]}"   --   --db "$DB_PATH"   --project-root "$PROJECT_ROOT"
