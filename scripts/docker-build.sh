#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
MODE=${1:-core}

if ! docker info >/dev/null 2>&1; then
  echo "Docker 引擎尚未运行。请先打开 Docker Desktop。" >&2
  exit 1
fi

"$ROOT/scripts/fetch-upstreams.sh"
cd "$ROOT"

case "$MODE" in
  core)
    docker compose build gateway web
    ;;
  easy)
    docker compose build easy-dataset
    ;;
  kaqg)
    docker compose build kaqg-worker
    ;;
  medical)
    docker compose build synthea-worker gateway web
    ;;
  all)
    docker compose build
    ;;
  *)
    echo "用法：$0 [core|easy|kaqg|medical|all]" >&2
    exit 2
    ;;
esac

echo "镜像构建完成。下一步执行：./scripts/docker-up.sh $MODE"
