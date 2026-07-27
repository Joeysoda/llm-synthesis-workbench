#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PIDS="$ROOT/runtime/pids"

for name in web gateway easy-dataset; do
  pid_file="$PIDS/$name.pid"
  if [ -f "$pid_file" ]; then
    pid=$(cat "$pid_file")
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid"
      echo "已停止 $name（PID $pid）。"
    fi
    rm -f "$pid_file"
  fi
done
