#!/usr/bin/env bash
set -euo pipefail

STATE_DIR="$HOME/.rankhunter"
PID_FILE="$STATE_DIR/ui.pid"

if [[ ! -f "$PID_FILE" ]]; then
  exit 0
fi

pid="$(cat "$PID_FILE" 2>/dev/null || true)"
if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" >/dev/null 2>&1; then
  kill "$pid" >/dev/null 2>&1 || true
fi

rm -f "$PID_FILE"
