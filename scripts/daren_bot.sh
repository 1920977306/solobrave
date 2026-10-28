#!/bin/bash
# daren_bot.sh — 达人飞书机器人服务统一管理（2026-10-28 起 launchd 单管）
#
# ⚠️ 2026-10-28 改制：本服务由 launchd 单管（com.solobrave.daren-bot，
#    KeepAlive + RunAtLoad + 开机自启，日志 logs/daren-bot.launchd.log）。
#    本脚本只是 launchctl 的薄包装 —— **禁止** nohup 自起：
#    旧脚本 stop 杀掉的其实是 launchd 的进程，KeepAlive 会立刻再拉一个，
#    脚本又 nohup 拉一个 ⇒ 双开互撞（8092 Address already in use）
#    + 任务认领竞态（2026-10-28 实案，交接单 §97）。
#
# 用法:
#   scripts/daren_bot.sh status|start|stop|restart
set -euo pipefail

DIR="/Users/qichen/solobrave-prod"
PORT=8092
LABEL="com.solobrave.daren-bot"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
DOMAIN="gui/$(id -u)"

pid_on_port() { lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN -t 2>/dev/null | head -1 || true; }
loaded() { launchctl list "${LABEL}" >/dev/null 2>&1; }

CMD="${1:-status}"
case "${CMD}" in
  status)
    if loaded; then
      echo "daren-bot (launchd ${LABEL}) LOADED pid=$(pid_on_port)"
      curl -s "http://127.0.0.1:${PORT}/healthz" || true; echo
    else
      echo "daren-bot (launchd ${LABEL}) NOT LOADED (STOPPED)"
    fi
    ;;
  start)
    if ! loaded; then
      launchctl bootstrap "${DOMAIN}" "${PLIST}"
      echo "daren-bot 已加载并启动 (launchd bootstrap)"
    else
      launchctl kickstart "${DOMAIN}/${LABEL}"
      echo "daren-bot 已在运行, kickstart 确保存活"
    fi
    sleep 3
    pid=$(pid_on_port)
    [ -n "${pid}" ] || { echo "FATAL: 启动失败, 看 ${DIR}/logs/daren-bot.launchd.log" >&2; exit 1; }
    echo "daren-bot RUNNING pid=${pid} (log: ${DIR}/logs/daren-bot.launchd.log)"
    ;;
  stop)
    if loaded; then
      launchctl bootout "${DOMAIN}/${LABEL}"
      echo "daren-bot 已停止并卸载 (launchd bootout)"
    else
      echo "daren-bot 未加载 (本就不在跑)"
    fi
    ;;
  restart)
    if loaded; then
      launchctl kickstart -k "${DOMAIN}/${LABEL}"
      sleep 3
      pid=$(pid_on_port)
      [ -n "${pid}" ] || { echo "FATAL: 重启失败, 看 ${DIR}/logs/daren-bot.launchd.log" >&2; exit 1; }
      echo "daren-bot 已重启 pid=${pid} (log: ${DIR}/logs/daren-bot.launchd.log)"
      curl -s "http://127.0.0.1:${PORT}/healthz" || true; echo
    else
      "$0" start
    fi
    ;;
  *) echo "FATAL: 未知命令 ${CMD} (status|start|stop|restart)" >&2; exit 1 ;;
esac
