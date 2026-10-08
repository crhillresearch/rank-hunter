#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="${SOURCE:-$ROOT/vendor/ratpoints-gpu}"
PREFIX="${PREFIX:-$HOME/.local}"

if [[ ! -f "$SOURCE/Makefile" ]]; then
  echo "[Rank Hunter] GPU ratpoints source not found at: $SOURCE" >&2
  echo "Initialize submodules first: git submodule update --init --recursive" >&2
  exit 2
fi
if ! command -v nvcc >/dev/null 2>&1; then
  echo "[Rank Hunter] CUDA nvcc not found; GPU ratpoints is unavailable." >&2
  exit 3
fi
if ! command -v make >/dev/null 2>&1; then
  echo "[Rank Hunter] make not found; GPU ratpoints cannot be built." >&2
  exit 3
fi

echo "[Rank Hunter] Building pinned GPU ratpoints source..."
make -C "$SOURCE" clean >/dev/null 2>&1 || true
make -C "$SOURCE"

if [[ ! -x "$SOURCE/ratpoints_gpu" ]]; then
  echo "[Rank Hunter] GPU ratpoints build did not produce an executable." >&2
  exit 4
fi

# This is intentionally a real CUDA smoke test. A successful compile with no
# usable GPU/driver must be reported as unavailable/failed, not as ready.
if ! "$SOURCE/ratpoints_gpu" '1 0 0 0 1' 10 -q -i >/dev/null 2>&1; then
  echo "[Rank Hunter] GPU ratpoints executable failed its CUDA smoke test." >&2
  exit 5
fi

mkdir -p "$PREFIX/bin"
cp "$SOURCE/ratpoints_gpu" "$PREFIX/bin/ratpoints_gpu"
chmod +x "$PREFIX/bin/ratpoints_gpu"

echo "[Rank Hunter] GPU ratpoints ready:"
echo "  vendored build: $SOURCE/ratpoints_gpu"
echo "  installed copy: $PREFIX/bin/ratpoints_gpu"
