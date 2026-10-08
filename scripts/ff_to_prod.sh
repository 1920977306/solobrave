#!/bin/bash
# ff_to_prod.sh — ff 合并门禁 (老大 2026-10-09 01:13「可以的」)
#
# 流程: 先跑 r84 回归 (tests/e2e_r84_regression.py, 打 18210 开发服务)
#       全过 → 工作区分支变基到 prod 最新 → prod 快进合并
#       挂   → 中止, 不合
#
# 用法:
#   scripts/ff_to_prod.sh            # 合并当前工作区分支进 prod
#   DRY_RUN=1 scripts/ff_to_prod.sh  # 只跑回归, 不做 git 操作
#
# 环境变量: WORKTREE (默认 /Users/qichen/sb-dev/side-restore)
#           PROD_DIR  (默认 /Users/qichen/solobrave-prod)
#           BASE      (回归目标, 默认 http://127.0.0.1:18210)
set -euo pipefail

WORKTREE="${WORKTREE:-/Users/qichen/sb-dev/side-restore}"
PROD_DIR="${PROD_DIR:-/Users/qichen/solobrave-prod}"
BASE="${BASE:-http://127.0.0.1:18210}"
DRY_RUN="${DRY_RUN:-}"

cd "$WORKTREE"
echo "== 1/3 回归门禁: $BASE =="
STARTED_SERVER=0
if ! lsof -nP -iTCP:18210 -sTCP:LISTEN >/dev/null 2>&1; then
  if [ "$BASE" = "http://127.0.0.1:18210" ]; then
    echo "18210 未启动, 临时拉起开发服务…"
    nohup python3 solobrave-server.py 18210 --data "$WORKTREE/data" > /tmp/sb18210-gate.log 2>&1 &
    STARTED_SERVER=$!
    sleep 3
  else
    echo "FATAL: $BASE 不可达且非本机 18210, 请先起服务" >&2; exit 1
  fi
fi
if ! python3 tests/e2e_r84_regression.py; then
  [ -n "$STARTED_SERVER" ] && [ "$STARTED_SERVER" != "0" ] && kill "$STARTED_SERVER" 2>/dev/null || true
  echo "FATAL: 回归未过, 中止合并 (铁律: 挂了不合)" >&2
  exit 1
fi
if [ -n "$STARTED_SERVER" ] && [ "$STARTED_SERVER" != "0" ]; then
  kill "$STARTED_SERVER" 2>/dev/null || true
fi

if [ -n "$DRY_RUN" ]; then
  echo "== DRY_RUN: 回归通过, 跳过 git 操作 =="
  exit 0
fi

echo "== 2/3 变基到 prod 最新 =="
PROD_HEAD=$(git -C "$PROD_DIR" rev-parse HEAD)
git rebase "$PROD_HEAD"

echo "== 3/3 prod 快进合并 =="
git -C "$PROD_DIR" merge --ff-only "$(git rev-parse HEAD)"
echo "OK: prod 已快进到 $(git rev-parse --short HEAD)"
echo "提示: 服务端代码变更需重启 8443/18210 才生效; 前端钉扎已 bump 的刷新即可"
