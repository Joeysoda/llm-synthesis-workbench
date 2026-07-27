#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
UPSTREAM="$ROOT/upstream"

clone_at() {
  name=$1
  repository=$2
  commit=$3
  target="$UPSTREAM/$name"

  if [ -d "$target/.git" ]; then
    current=$(git -C "$target" rev-parse HEAD)
    if [ "$current" = "$commit" ]; then
      echo "$name 已准备完成：${commit}"
      return
    fi
    if [ -n "$(git -C "$target" status --porcelain)" ]; then
      echo "$target 存在未提交修改，未自动覆盖。" >&2
      exit 1
    fi
    git -C "$target" fetch --depth 1 origin "$commit"
  elif [ -e "$target" ]; then
    echo "$target 已存在但不是 Git 仓库，请先检查该目录。" >&2
    exit 1
  else
    git clone --filter=blob:none --no-checkout "$repository" "$target"
    git -C "$target" fetch --depth 1 origin "$commit"
  fi

  git -C "$target" checkout --detach "$commit"
  echo "$name 已固定到 ${commit}"
}

mkdir -p "$UPSTREAM"

clone_at \
  easy-dataset \
  https://github.com/ConardLi/easy-dataset.git \
  4002b09d9c5726cafb9f61a8d12765cb96a2d94b

clone_at \
  synthetic-data-kit \
  https://github.com/meta-llama/synthetic-data-kit.git \
  27a5541b2cc3537954c381eafc5398ab0838a397

clone_at \
  synlogic \
  https://github.com/MiniMax-AI/SynLogic.git \
  d8c527fd17edb739172619efb9b681805fc74b8d

echo "三个上游项目已准备完成。"
