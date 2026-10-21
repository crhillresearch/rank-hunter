#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
SCIENCE_PYTHON="${RANK_HUNTER_SCIENCE_PYTHON:-${RANK42_SCIENCE_PYTHON:-python}}"
PROBE_OPTIONAL="${RANK_HUNTER_CATALOG_PROBE_OPTIONAL:-0}"

if ! "$SCIENCE_PYTHON" -c 'import sage.all' >/dev/null 2>&1; then
  echo "[Rank Hunter] ERROR: $SCIENCE_PYTHON is not the Sage scientific Python." >&2
  echo "Activate your Sage conda environment, then rerun this script." >&2
  exit 2
fi

echo "[Rank Hunter] Installing PostgreSQL driver into scientific Python: $SCIENCE_PYTHON"
"$SCIENCE_PYTHON" -m pip install -r requirements-science-catalog.txt

echo
echo "[Rank Hunter] Probing the public read-only LMFDB SQL mirror with conductor 88024..."
if "$SCIENCE_PYTHON" -m rank42.novelty --probe-lmfdb-sql --timeout 10; then
  echo
  echo "[Rank Hunter] LMFDB SQL catalog transport is ready."
elif [[ "$PROBE_OPTIONAL" == "1" ]]; then
  echo
  echo "[Rank Hunter] WARNING: catalog driver is installed, but the live LMFDB probe is unavailable." >&2
  echo "[Rank Hunter] Diagnostics can retry the probe later." >&2
else
  exit 4
fi
