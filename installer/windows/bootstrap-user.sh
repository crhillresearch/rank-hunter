#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/crhillresearch/rank-hunter.git"
REPO_REF="main"
REPO_DIR_NAME="rank-hunter"
SCIENCE_PYTHON="/opt/rankhunter/miniforge3/envs/sage/bin/python"
SOURCE_BUNDLE=""
SOURCE_COMMIT=""

usage() {
  cat <<'EOF'
Usage:
  bootstrap-user.sh [--repo-url URL] [--repo-ref REF] [--repo-dir NAME]
                    [--science-python PATH] [--source-bundle PATH]
                    [--source-commit SHA]
EOF
}

while (($#)); do
  case "$1" in
    --repo-url)
      REPO_URL="$2"
      shift 2
      ;;
    --repo-ref)
      REPO_REF="$2"
      shift 2
      ;;
    --repo-dir)
      REPO_DIR_NAME="$2"
      shift 2
      ;;
    --science-python)
      SCIENCE_PYTHON="$2"
      shift 2
      ;;
    --source-bundle)
      SOURCE_BUNDLE="$2"
      shift 2
      ;;
    --source-commit)
      SOURCE_COMMIT="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[Rank Hunter / WSL] ERROR: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

[[ "$REPO_DIR_NAME" =~ ^[A-Za-z0-9._-]+$ ]] || {
  echo "[Rank Hunter / WSL] ERROR: invalid repo directory name: $REPO_DIR_NAME" >&2
  exit 2
}

ROOT="$HOME/$REPO_DIR_NAME"

log() {
  printf '[Rank Hunter / WSL] %s\n' "$*"
}

if [[ "$(id -u)" -eq 0 ]]; then
  echo "[Rank Hunter / WSL] ERROR: bootstrap-user.sh must run as the normal Ubuntu user." >&2
  exit 2
fi

[[ -x "$SCIENCE_PYTHON" ]] || {
  echo "[Rank Hunter / WSL] ERROR: Sage Python not found: $SCIENCE_PYTHON" >&2
  exit 2
}
"$SCIENCE_PYTHON" -c 'import sage.all' >/dev/null

if [[ -n "$SOURCE_BUNDLE" ]]; then
  [[ -f "$SOURCE_BUNDLE" ]] || {
    echo "[Rank Hunter / WSL] ERROR: bundled source not found: $SOURCE_BUNDLE" >&2
    exit 2
  }
  [[ -n "$SOURCE_COMMIT" ]] || {
    echo "[Rank Hunter / WSL] ERROR: bundled source requires --source-commit." >&2
    exit 2
  }
fi

if [[ -e "$ROOT" && ! -d "$ROOT/.git" && ! -f "$ROOT/.git" ]]; then
  echo "[Rank Hunter / WSL] ERROR: resolved install path is occupied and is not a Git checkout: $ROOT" >&2
  exit 2
fi

if [[ ! -e "$ROOT" ]]; then
  if [[ -n "$SOURCE_BUNDLE" ]]; then
    log "Installing bundled Rank Hunter source..."
    git clone --branch "$REPO_REF" "$SOURCE_BUNDLE" "$ROOT"
  else
    log "Cloning Rank Hunter..."
    git clone "$REPO_URL" "$ROOT"
  fi
fi

[[ -d "$ROOT/.git" || -f "$ROOT/.git" ]] || {
  echo "[Rank Hunter / WSL] ERROR: Rank Hunter source did not produce a Git checkout: $ROOT" >&2
  exit 2
}

cd "$ROOT"

if [[ -n "$SOURCE_BUNDLE" ]]; then
  if [[ -n "$(git status --porcelain)" ]]; then
    log "Checkout has local changes; preserving them and installing the current tree."
  else
    log "Loading pinned Rank Hunter source from bundled Git data..."
    git fetch "$SOURCE_BUNDLE" "$REPO_REF"
    git checkout --detach "$SOURCE_COMMIT"
    [[ "$(git rev-parse HEAD)" == "$SOURCE_COMMIT" ]] || {
      echo "[Rank Hunter / WSL] ERROR: bundled source commit verification failed." >&2
      exit 3
    }
  fi
  git remote set-url origin "$REPO_URL"
else
  if [[ "$(git remote get-url origin)" != "$REPO_URL" ]]; then
    log "Preserving existing origin remote: $(git remote get-url origin)"
  fi

  git fetch --prune origin

  if [[ -z "$(git status --porcelain)" ]]; then
    if git ls-remote --exit-code --heads origin "$REPO_REF" >/dev/null 2>&1; then
      if git show-ref --verify --quiet "refs/heads/$REPO_REF"; then
        git checkout "$REPO_REF"
      else
        git checkout -b "$REPO_REF" --track "origin/$REPO_REF"
      fi
      git merge --ff-only "origin/$REPO_REF"
    elif git ls-remote --exit-code --tags origin "refs/tags/$REPO_REF" >/dev/null 2>&1; then
      git fetch --tags origin
      git checkout --detach "refs/tags/$REPO_REF"
    else
      git fetch origin "$REPO_REF"
      git checkout --detach FETCH_HEAD
    fi
  else
    log "Checkout has local changes; preserving them and installing the current tree."
  fi
fi
log "Running Rank Hunter installer..."
bash install.sh --science-python "$SCIENCE_PYTHON"

log "Rank Hunter checkout ready: $ROOT"
