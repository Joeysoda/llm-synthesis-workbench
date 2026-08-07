#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
MODE=${1:-core}
COMPOSE="docker compose -f $ROOT/docker-compose.yml"

if ! docker info >/dev/null 2>&1; then
  echo "Docker 引擎尚未运行。请先打开 Docker Desktop，再重新执行本脚本。" >&2
  exit 1
fi

cd "$ROOT"

start() {
  if ! $COMPOSE up -d --no-build "$@"; then
    echo >&2
    echo "启动失败。如果提示镜像不存在，请先执行：./scripts/docker-build.sh $MODE" >&2
    exit 1
  fi
}

case "$MODE" in
  core)
    start --no-deps gateway web
    ;;
  easy)
    start --no-deps gateway web easy-dataset
    ;;
  kaqg)
    start neo4j mosquitto kaqg-worker
    start --no-deps gateway web
    ;;
  all)
    start
    ;;
  *)
    echo "用法：$0 [core|easy|kaqg|all]" >&2
    exit 2
    ;;
esac

echo
docker compose -f "$ROOT/docker-compose.yml" ps
echo
echo "工作台：http://127.0.0.1:5173"
echo "Gateway：http://127.0.0.1:18000"
case "$MODE" in
  easy|all) echo "Easy Dataset：http://127.0.0.1:1717" ;;
esac
case "$MODE" in
  kaqg|all) echo "KAQG worker、Neo4j、Mosquitto：仅 Docker 内部网络可访问" ;;
esac
echo "启动模式：${MODE}（本次未执行镜像构建）"
