#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
MODE=${1:-all}
cd "$ROOT"

case "$MODE" in
  core)
    docker compose stop web gateway
    ;;
  easy)
    docker compose stop easy-dataset
    ;;
  kaqg)
    docker compose stop kaqg-worker neo4j mosquitto
    ;;
  all)
    docker compose stop
    ;;
  *)
    echo "用法：$0 [core|easy|kaqg|all]" >&2
    exit 2
    ;;
esac

docker compose ps
