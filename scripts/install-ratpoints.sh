#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="${SOURCE:-$ROOT/vendor/ratpoints}"
PREFIX="${PREFIX:-$HOME/.local}"

if [[ ! -f "$SOURCE/Makefile" ]]; then
  echo "vendored ratpoints source not found at: $SOURCE" >&2
  echo "initialize submodules first: git submodule update --init --recursive" >&2
  exit 2
fi

# Rank Hunter's exact family specializations can produce coefficient strings
# longer than the OS per-argument limit.  The small stdin patch lets ratpoints
# read the coefficient list from standard input when argv[1] is "-".
if ! grep -q 'RANK_HUNTER_STDIN_POLY' "$SOURCE/main.c"; then
  patch -d "$SOURCE" -p1 < "$ROOT/patches/ratpoints-stdin-input.patch"
fi

mkdir -p "$PREFIX/bin"

# Build the pinned vendored source in place.  Do not fetch/pull here: the
# parent Rank Hunter commit owns the exact ratpoints submodule revision.
# Only build the executable: upstream `make all` also builds the PDF manual.
make -C "$SOURCE" distclean >/dev/null 2>&1 || true
if ! make -C "$SOURCE" ratpoints; then
  echo "default AVX2 build failed; retrying the portable SSE build" >&2
  make -C "$SOURCE" distclean >/dev/null 2>&1 || true
  make -C "$SOURCE" ratpoints CCFLAGS1=-DUSE_SSE
fi

# Catch CPUs that compile AVX2 code but cannot execute it.
if ! "$SOURCE/ratpoints" '1 0 0 0 1' 10 -q -i >/dev/null 2>&1; then
  echo "default executable failed smoke test; rebuilding with SSE" >&2
  make -C "$SOURCE" distclean >/dev/null 2>&1 || true
  make -C "$SOURCE" ratpoints CCFLAGS1=-DUSE_SSE
  "$SOURCE/ratpoints" '1 0 0 0 1' 10 -q -i >/dev/null
fi

# Keep the traditional user-local command available while Rank Hunter itself
# defaults to the pinned executable under vendor/ratpoints.
cp "$SOURCE/ratpoints" "$PREFIX/bin/ratpoints"
chmod +x "$PREFIX/bin/ratpoints"

echo "vendored build: $SOURCE/ratpoints"
echo "installed copy: $PREFIX/bin/ratpoints"
echo "Rank Hunter Settings default to the vendored build."
