#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
URL="${RANK_HUNTER_OFFICIAL_PLUGINS_URL:-https://github.com/crhillresearch/rh-plugins.git}"
# A release uses a fixed, auditable plugin collection. Researchers can opt into
# a different tag/branch without changing the public installer.
REF="${RANK_HUNTER_OFFICIAL_PLUGINS_REF:-${RANK_HUNTER_OFFICIAL_PLUGINS_BRANCH:-v0.9.2}}"
DEST="${RANK_HUNTER_OFFICIAL_PLUGINS_DIR:-$ROOT/plugins}"

if [[ -d "$DEST/.git" ]]; then
  ORIGIN="$(git -C "$DEST" remote get-url origin 2>/dev/null || true)"
  if [[ "$ORIGIN" != "$URL" ]]; then
    echo "[Rank Hunter] ERROR: existing plugins checkout has unexpected origin:" >&2
    echo "  $DEST" >&2
    echo "  expected: $URL" >&2
    echo "  actual:   ${ORIGIN:-<none>}" >&2
    exit 2
  fi
  if [[ -n "$(git -C "$DEST" status --porcelain --untracked-files=all)" ]]; then
    echo "[Rank Hunter] ERROR: refusing to switch an official plugins checkout with local changes:" >&2
    echo "  $DEST" >&2
    echo "[Rank Hunter] Preserve your plugin changes before updating." >&2
    exit 2
  fi
  echo "[Rank Hunter] Fetching official plugins from $URL ..."
  git -C "$DEST" fetch --tags origin
elif [[ -d "$DEST" ]] && [[ -z "$(find "$DEST" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
  rmdir "$DEST"
  echo "[Rank Hunter] Cloning official plugins from $URL ..."
  git clone --no-checkout "$URL" "$DEST"
elif [[ -e "$DEST" ]]; then
  echo "[Rank Hunter] ERROR: refusing to replace existing non-Git plugins path: $DEST" >&2
  exit 2
else
  echo "[Rank Hunter] Cloning official plugins from $URL ..."
  git clone --no-checkout "$URL" "$DEST"
fi

if git -C "$DEST" show-ref --verify --quiet "refs/tags/$REF"; then
  TARGET="refs/tags/$REF"
elif git -C "$DEST" show-ref --verify --quiet "refs/remotes/origin/$REF"; then
  TARGET="refs/remotes/origin/$REF"
elif [[ "$REF" =~ ^[0-9a-fA-F]{40}$ ]] && git -C "$DEST" cat-file -e "$REF^{commit}" 2>/dev/null; then
  TARGET="$REF"
else
  echo "[Rank Hunter] ERROR: official plugins ref not found: $REF" >&2
  echo "[Rank Hunter] Expected a published tag, remote branch, or fetched 40-character commit." >&2
  exit 2
fi

git -C "$DEST" checkout --detach "$TARGET"
REV="$(git -C "$DEST" rev-parse --short HEAD)"
echo "[Rank Hunter] Official plugins ready: $DEST @ $REV (ref: $REF)"
