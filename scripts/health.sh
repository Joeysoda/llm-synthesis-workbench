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

status=0
GATEWAY_PORT=${GATEWAY_PORT:-18000}
check "Gateway" "http://127.0.0.1:$GATEWAY_PORT/api/v1/health" || status=1
check "Web" "http://localhost:5173" || status=1
check "Easy Dataset" "http://127.0.0.1:1717/api/projects" || status=1
exit "$status"
