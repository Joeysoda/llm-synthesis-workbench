#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

command -v uv >/dev/null 2>&1 || {
  echo "缺少 uv，请先安装 uv。"
  exit 1
}
command -v npm >/dev/null 2>&1 || {
  echo "缺少 Node.js/npm。"
  exit 1
}

"$ROOT/scripts/fetch-upstreams.sh"
uv sync --dev
uv pip install --python "$ROOT/.venv/bin/python" -e "$ROOT/upstream/synthetic-data-kit"
npm --prefix "$ROOT/web" install

echo "本机依赖准备完成。"
echo "LLM 验收前请撤销旧密钥，设置新的 DEEPSEEK_API_KEY 和 LLM_CREDENTIAL_ROTATED=true。"
