#!/bin/bash
# daren_bot.sh — 达人飞书机器人服务统一管理 (对齐 dev_server.sh 口径, 2026-10-12)
#
# 用法:
#   scripts/daren_bot.sh status|start|stop|restart
#
# 进程按端口锁定, 重复 start 不会起第二份; 日志 logs/daren-bot.log
set -euo pipefail

DIR="/Users/qichen/solobrave-prod"
PORT=8092
PID_FILE="$DIR/data/daren-bot/daren_bot.pid"

pid_on_port() { lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1 || true; }

CMD="${1:-status}"
case "$CMD" in
  status)
    pid=$(pid_on_port)
    if [ -n "$pid" ]; then
      echo "daren-bot (:$PORT) RUNNING pid=$pid"
      curl -s "http://127.0.0.1:$PORT/healthz" || true; echo
    else
      echo "daren-bot (:$PORT) STOPPED"
    fi
    ;;
  start)
    pid=$(pid_on_port)
    if [ -n "$pid" ]; then echo "daren-bot 已在跑 pid=$pid, 跳过"; exit 0; fi
    mkdir -p "$DIR/data/daren-bot" "$DIR/logs"
    (cd "$DIR" && nohup .venv-daren/bin/python -m daren_bot.main serve \
        >> "$DIR/logs/daren-bot.log" 2>&1 & echo $! > "$PID_FILE")
    sleep 3
    pid=$(pid_on_port)
    [ -n "$pid" ] || { echo "FATAL: 启动失败, 看 $DIR/logs/daren-bot.log" >&2; exit 1; }
    echo "daren-bot 已启动 pid=$pid (log: $DIR/logs/daren-bot.log)"
    ;;
  stop)
    pid=$(pid_on_port)
    if [ -z "$pid" ]; then echo "daren-bot 未在跑"; exit 0; fi
    kill "$pid"; sleep 2
    pid=$(pid_on_port)
    [ -z "$pid" ] || kill -9 "$pid" 2>/dev/null || true
    echo "daren-bot 已停止"
    ;;
  restart)
    "$0" stop; "$0" start
    ;;
  *) echo "FATAL: 未知命令 $CMD (status|start|stop|restart)" >&2; exit 1 ;;
esac
