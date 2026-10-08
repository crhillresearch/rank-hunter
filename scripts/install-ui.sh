#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

SCIENCE_PYTHON="${RANK_HUNTER_SCIENCE_PYTHON:-${RANK42_SCIENCE_PYTHON:-$(command -v python || true)}}"
UI_PYTHON="${RANK_HUNTER_UI_PYTHON:-${RANK42_UI_PYTHON:-$SCIENCE_PYTHON}}"
UI_VENV="${RANK_HUNTER_UI_VENV:-${RANK42_UI_VENV:-.venv-ui}}"

if [[ -z "$SCIENCE_PYTHON" || ! -x "$SCIENCE_PYTHON" ]]; then
  echo "[Rank Hunter] ERROR: scientific Python was not found or is not executable." >&2
  echo "Activate Sage or set RANK_HUNTER_SCIENCE_PYTHON, then rerun this script." >&2
  exit 2
fi
if ! "$SCIENCE_PYTHON" -c 'import sage.all' >/dev/null 2>&1; then
  echo "[Rank Hunter] ERROR: $SCIENCE_PYTHON cannot import sage.all." >&2
  echo "Activate the supported Sage/scientific Python, then rerun this script." >&2
  exit 2
fi
if [[ -z "$UI_PYTHON" || ! -x "$UI_PYTHON" ]]; then
  echo "[Rank Hunter] ERROR: UI venv base Python was not found: $UI_PYTHON" >&2
  exit 2
fi

mkdir -p .rank42-ui
# One-way bootstrap marker only. On first UI startup Rank Hunter seeds
# ui_settings.science_python if that setting is absent; an existing persisted
# setting is authoritative and is never overwritten from this marker.
printf '%s\n' "$SCIENCE_PYTHON" > .rank42-ui/science-python

echo "[Rank Hunter] scientific Python:  $SCIENCE_PYTHON"
echo "[Rank Hunter] UI venv base:      $UI_PYTHON"
echo "[Rank Hunter] UI environment:    $UI_VENV"
echo

# The UI process still imports Sage on its main thread before Streamlit starts.
# Build the isolated UI venv from a Sage-capable Python and expose only the
# base environment's scientific packages; UI requirements themselves are
# installed into the venv, never into the Sage environment.
if ! "$UI_PYTHON" -m venv --system-site-packages "$UI_VENV"; then
  echo >&2
  echo "[Rank Hunter] Could not create the UI virtual environment." >&2
  echo "Ensure the selected scientific Python provides the venv module." >&2
  echo "On Ubuntu/WSL, system Python may additionally require python3-venv." >&2
  exit 3
fi

"$UI_VENV/bin/python" -m pip install --upgrade pip
"$UI_VENV/bin/python" -m pip install -r requirements.txt

if ! "$UI_VENV/bin/python" -c 'import sage.all, streamlit' >/dev/null 2>&1; then
  echo "[Rank Hunter] ERROR: UI environment cannot import both Sage and Streamlit." >&2
  echo "Recreate $UI_VENV from a Sage-capable Python and rerun this script." >&2
  exit 4
fi

cat > .rank42-ui/launch-command <<EOF2
$UI_VENV/bin/python -m rank42.streamlit_launcher run rank_hunter_control_center.py -- --db rank42.db
EOF2

echo
echo "[Rank Hunter] UI dependencies installed in $UI_VENV without modifying Sage packages."
echo "[Rank Hunter] UI runtime:         $UI_VENV/bin/python"
echo "[Rank Hunter] Scientific jobs:    $SCIENCE_PYTHON"
echo "[Rank Hunter] Launch from this project directory with:"
echo "  bash scripts/run-ui.sh"
