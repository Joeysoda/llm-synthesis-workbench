#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
RUNTIME="$ROOT/runtime"
PIDS="$RUNTIME/pids"
LOGS="$RUNTIME/logs"
EASY="$ROOT/upstream/easy-dataset"
GATEWAY_PORT=${GATEWAY_PORT:-18000}
GATEWAY_BASE="http://127.0.0.1:$GATEWAY_PORT"

mkdir -p "$PIDS" "$LOGS"

start_service() {
  name=$1
  workdir=$2
  shift 2
  pid_file="$PIDS/$name.pid"
  if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    echo "$name 已在运行。"
    return
  fi
  (
    cd "$workdir"
    nohup "$@" >"$LOGS/$name.log" 2>&1 &
    echo $! >"$pid_file"
  )
  echo "已启动 ${name}，PID $(cat "$pid_file")。"
}

if curl --silent --fail --max-time 2 "http://127.0.0.1:1717/api/projects" >/dev/null 2>&1; then
  echo "easy-dataset 已在 127.0.0.1:1717 运行。"
elif [ -f "$EASY/.next/BUILD_ID" ]; then
  start_service easy-dataset "$EASY" npm run start -- -H 127.0.0.1
else
  echo "Easy Dataset 尚未构建；先继续启动网关和前端。可在 $EASY 执行 npm run build。"
fi

start_service gateway "$ROOT" env GATEWAY_PORT="$GATEWAY_PORT" \
  "$ROOT/.venv/bin/uvicorn" app.main:app --app-dir gateway \
  --host 127.0.0.1 --port "$GATEWAY_PORT"
start_service web "$ROOT/web" env NEXT_PUBLIC_GATEWAY_BASE="$GATEWAY_BASE" \
  npm run dev -- --hostname 127.0.0.1 --port 5173

sleep 2
"$ROOT/scripts/health.sh" || true
echo "工作台：http://localhost:5173"
