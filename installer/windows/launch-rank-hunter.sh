#!/usr/bin/env bash
set -euo pipefail

REPO_DIR_NAME="${1:-rank-hunter}"
PORT="${2:-8501}"
ROOT="$HOME/$REPO_DIR_NAME"
STATE_DIR="$HOME/.rankhunter"
PID_FILE="$STATE_DIR/ui.pid"
LOG_FILE="$STATE_DIR/ui.log"

[[ "$REPO_DIR_NAME" =~ ^[A-Za-z0-9._-]+$ ]] || exit 2
[[ "$PORT" =~ ^[0-9]+$ ]] || exit 2
[[ -x "$ROOT/scripts/run-ui.sh" ]] || exit 3

mkdir -p "$STATE_DIR"

if [[ -f "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ "$old_pid" =~ ^[0-9]+$ ]] && kill -0 "$old_pid" >/dev/null 2>&1; then
    # If the recorded process is already serving the requested port, this is an
    # idempotent launch. Let Windows reconnect to it.
    if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
      exec 3>&-
      exec 3<&-
      exit 0
    fi
  fi
  rm -f "$PID_FILE"
fi

cd "$ROOT"
: >"$LOG_FILE"

# WSL can race an immediately-returning Windows-side wsl.exe against a newly
# backgrounded Linux process. Start the UI fully detached, then keep this WSL
# session alive until the server is actually listening. Once healthy, the
# detached process survives independently and Windows may safely return.
nohup setsid env \
  STREAMLIT_SERVER_PORT="$PORT" \
  STREAMLIT_SERVER_ADDRESS="127.0.0.1" \
  bash scripts/run-ui.sh >"$LOG_FILE" 2>&1 < /dev/null &
ui_pid="$!"
printf '%s\n' "$ui_pid" >"$PID_FILE"

for _ in $(seq 1 180); do
  if ! kill -0 "$ui_pid" >/dev/null 2>&1; then
    wait "$ui_pid" || rc="$?"
    rm -f "$PID_FILE"
    exit "${rc:-1}"
  fi

  if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
    exec 3>&-
    exec 3<&-
    exit 0
  fi

  sleep 0.5
done

kill "$ui_pid" >/dev/null 2>&1 || true
rm -f "$PID_FILE"
exit 4
