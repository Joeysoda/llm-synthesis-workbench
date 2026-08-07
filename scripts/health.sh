#!/bin/sh
set -eu

check() {
  name=$1
  url=$2
  if curl --silent --fail --max-time 3 "$url" >/dev/null; then
    echo "✓ $name $url"
  else
    echo "✗ $name $url"
    return 1
  fi
}

probe() {
  name=$1
  url=$2
  if response=$(curl --silent --fail --max-time 5 -X POST \
      -H 'Content-Type: application/json' -d '{}' "$url") \
    && printf '%s' "$response" | python3 -c \
      'import json, sys; sys.exit(0 if json.load(sys.stdin).get("ok") else 1)'; then
    echo "✓ $name"
  else
    echo "✗ $name"
    return 1
  fi
}

status=0
MODE=${1:-core}
GATEWAY_PORT=${GATEWAY_PORT:-18000}
check "Gateway" "http://127.0.0.1:$GATEWAY_PORT/api/v1/health" || status=1
check "Web" "http://127.0.0.1:5173" || status=1
check "五工具集成状态" "http://127.0.0.1:$GATEWAY_PORT/api/v2/integrations/status" || status=1

case "$MODE" in
  core)
    ;;
  easy)
    check "Easy Dataset" "http://127.0.0.1:1717/api/projects" || status=1
    ;;
  kaqg)
    probe "KAQG worker / Neo4j / Mosquitto" \
      "http://127.0.0.1:$GATEWAY_PORT/api/v2/integrations/kaqg/probe" || status=1
    ;;
  all)
    check "Easy Dataset" "http://127.0.0.1:1717/api/projects" || status=1
    probe "KAQG worker / Neo4j / Mosquitto" \
      "http://127.0.0.1:$GATEWAY_PORT/api/v2/integrations/kaqg/probe" || status=1
    ;;
  *)
    echo "用法：$0 [core|easy|kaqg|all]" >&2
    exit 2
    ;;
esac
exit "$status"
