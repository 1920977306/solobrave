#!/bin/bash
# dev_server.sh — 18210/8443 服务统一管理 (老大 2026-10-09 01:15「可以的」)
#
# 用法:
#   scripts/dev_server.sh status          # 查看两个服务状态
#   scripts/dev_server.sh start  dev      # 起 18210 (side-restore 工作区)
#   scripts/dev_server.sh start  prod     # 起 8443  (solobrave-prod)
#   scripts/dev_server.sh stop   dev|prod
#   scripts/dev_server.sh restart dev|prod   # 停旧进程 + 拉起 (代码变更后用这个)
#
# 口径: dev=18210 打工作区磁盘文件 (改完即生效, 前端钉扎破缓存)
#       prod=8443 打 /Users/qichen/solobrave-prod (铁律16 唯一事实源)
#       进程按端口锁定, 重复 start 不会起第二份
set -euo pipefail

WORKTREE="${WORKTREE:-/Users/qichen/sb-dev/side-restore}"
PROD_DIR="${PROD_DIR:-/Users/qichen/solobrave-prod}"

pid_on_port() { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | head -1 || true; }
cwd_of() { lsof -p "$1" 2>/dev/null | awk '$4=="cwd"{print $NF}' || true; }

status_one() {
  local name="$1" port="$2" expect_dir="$3" pid
  pid=$(pid_on_port "$port")
  if [ -n "$pid" ]; then
    echo "$name (:$port) RUNNING pid=$pid cwd=$(cwd_of "$pid")"
  else
    echo "$name (:$port) STOPPED (期望目录 $expect_dir)"
  fi
}

start_one() {
  local name="$1" port="$2" dir="$3"; shift 3
  local pid
  pid=$(pid_on_port "$port")
  if [ -n "$pid" ]; then
    local cur; cur=$(cwd_of "$pid")
    if [ "$cur" = "$dir" ]; then
      echo "$name 已在跑 pid=$pid, 跳过"; return 0
    fi
    echo "FATAL: :$port 被 pid=$pid (cwd=$cur) 占用, 不是期望目录 $dir" >&2
    echo "先 scripts/dev_server.sh stop $name 或手动 kill $pid" >&2
    exit 1
  fi
  (cd "$dir" && nohup python3 solobrave-server.py "$@" > "/tmp/sb${port}.log" 2>&1 &)
  sleep 3
  pid=$(pid_on_port "$port")
  [ -n "$pid" ] || { echo "FATAL: $name 启动失败, 看 /tmp/sb${port}.log" >&2; exit 1; }
  echo "$name 已启动 pid=$pid (log: /tmp/sb${port}.log)"
}

stop_one() {
  local name="$1" port="$2"
  local pid; pid=$(pid_on_port "$port")
  if [ -z "$pid" ]; then echo "$name 未在跑"; return 0; fi
  kill "$pid"; sleep 2
  pid=$(pid_on_port "$port")
  [ -z "$pid" ] || { kill -9 "$pid" 2>/dev/null || true; }
  echo "$name 已停止"
}

CMD="${1:-status}"; TARGET="${2:-}"

case "$TARGET" in
  dev)  PORT=18210; DIR="$WORKTREE"; ARGS=(18210 --data "$WORKTREE/data") ;;
  prod) PORT=8443;  DIR="$PROD_DIR";  ARGS=(8080) ;;   # 8080=http 口, https 8443 由脚本内部绑定
  "")   : ;;
  *)    echo "FATAL: 未知目标 $TARGET (dev|prod)" >&2; exit 1 ;;
esac

case "$CMD" in
  status)
    status_one dev 18210 "$WORKTREE"
    status_one prod 8443 "$PROD_DIR"
    ;;
  start)  [ -n "$TARGET" ] || { echo "FATAL: start 需要 dev|prod" >&2; exit 1; }
          start_one "$TARGET" "$PORT" "$DIR" "${ARGS[@]}" ;;
  stop)   [ -n "$TARGET" ] || { echo "FATAL: stop 需要 dev|prod" >&2; exit 1; }
          stop_one "$TARGET" "$PORT" ;;
  restart)[ -n "$TARGET" ] || { echo "FATAL: restart 需要 dev|prod" >&2; exit 1; }
          stop_one "$TARGET" "$PORT"; start_one "$TARGET" "$PORT" "$DIR" "${ARGS[@]}" ;;
  *) echo "FATAL: 未知命令 $CMD (status|start|stop|restart)" >&2; exit 1 ;;
esac
