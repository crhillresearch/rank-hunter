#!/usr/bin/env bash
set -euo pipefail

log() {
  printf '[Rank Hunter / WSL] %s\n' "$*"
}

if [[ "$(id -u)" -ne 0 ]]; then
  echo "[Rank Hunter / WSL] ERROR: bootstrap-prereqs.sh must run as root." >&2
  exit 2
fi

if ! command -v apt-get >/dev/null 2>&1; then
  echo "[Rank Hunter / WSL] ERROR: this Linux environment does not provide apt-get." >&2
  exit 2
fi

log "Installing Rank Hunter Linux prerequisites..."
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
  ca-certificates \
  curl \
  git \
  build-essential \
  libgmp-dev \
  python3 \
  python3-venv \
  pkg-config \
  zstd

log "Linux prerequisites ready."
