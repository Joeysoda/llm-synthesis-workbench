#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

if ! docker info >/dev/null 2>&1; then
  echo "Docker 引擎尚未运行。请先打开 Docker Desktop，再重新执行本脚本。" >&2
  exit 1
fi

"$ROOT/scripts/fetch-upstreams.sh"

cd "$ROOT"
DOCKER_BUILDKIT=0 COMPOSE_BAKE=false docker compose -f "$ROOT/docker-compose.yml" up -d --build
echo
docker compose -f "$ROOT/docker-compose.yml" ps
echo
echo "工作台：http://127.0.0.1:5173"
echo "Gateway：http://127.0.0.1:18000"
echo "Easy Dataset：http://127.0.0.1:1717"
