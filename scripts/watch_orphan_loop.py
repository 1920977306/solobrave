#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""孤儿循环哨兵（交接单 §162 防复发②，2026-11-15）。

Helen 泄漏的病征：网关内僵尸循环空转时，**代理流量照涨但 OpenClaw 轨迹停更**
（本次实案：流量持续到 16:16，轨迹与 session store 停在 10:05）。
本脚本按同一判别式巡检：最近窗口内有 KimiProxy 流量的 agent，
其 openclaw 会话库 mtime 若显著早于流量时间 = 疑似孤儿，输出告警。

数据源（只读）：
  - server.log 尾部（KimiProxy 请求进入 行，取最近 --window-min 分钟）
  - ~/.openclaw/agents/<agent_id>/agent/openclaw-agent.sqlite 的 mtime

退出码：0 = 干净；1 = 发现疑似孤儿（launchd 环境下看日志输出）。
用法：python3 scripts/watch_orphan_loop.py [--window-min 15] [--stale-min 30] [--log PATH]
"""
import argparse
import os
import re
import subprocess
import sys
import time

DEFAULT_LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'server.log')
OPENCLAW_AGENTS_DIR = os.path.expanduser('~/.openclaw/agents')
# 请求进入行时间戳格式：2026-11-15 16:16:38,193 [INFO] [KimiProxy] 请求进入: agent_id=XXX
LINE_RE = re.compile(
    r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ .*?\[KimiProxy\] 请求进入: agent_id=(\S+)')


def _recent_traffic(log_path, window_min, tail_bytes=30 * 1024 * 1024):
    """取最近窗口内的 agent 流量 {agent_id: 最新行时间戳}。日志可能巨大，只读尾部。"""
    try:
        size = os.path.getsize(log_path)
        with open(log_path, 'rb') as f:
            f.seek(max(0, size - tail_bytes))
            tail = f.read().decode('utf-8', errors='replace')
    except OSError as e:
        print(f'[orphan-watch] 读取日志失败 {log_path}: {e}', file=sys.stderr)
        return {}
    cutoff = time.time() - window_min * 60
    latest = {}
    for line in tail.splitlines():
        m = LINE_RE.match(line)
        if not m:
            continue
        try:
            ts = time.mktime(time.strptime(m.group(1), '%Y-%m-%d %H:%M:%S'))
        except ValueError:
            continue
        if ts >= cutoff:
            latest[m.group(2)] = max(latest.get(m.group(2), 0), ts)
    return latest


def _session_store_mtime(agent_id):
    store = os.path.join(OPENCLAW_AGENTS_DIR, agent_id, 'agent', 'openclaw-agent.sqlite')
    try:
        return os.path.getmtime(store)
    except OSError:
        return None  # 无会话库（该 agent 从未走 openclaw 链路）


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--window-min', type=int, default=15)
    ap.add_argument('--stale-min', type=int, default=30)
    ap.add_argument('--log', default=DEFAULT_LOG)
    args = ap.parse_args()

    traffic = _recent_traffic(args.log, args.window_min)
    if not traffic:
        print(f'[orphan-watch] 窗口 {args.window_min}min 内无 KimiProxy 流量，干净')
        return 0

    now = time.time()
    suspects = []
    for aid, last_call in sorted(traffic.items()):
        mtime = _session_store_mtime(aid)
        if mtime is None:
            # 无 openclaw 会话库却有代理流量：非 openclaw 链路（如直连接口），
            # 不在本哨兵范围（动态限频闸门在 server 内已兜底形态学特征）。
            print(f'[orphan-watch] {aid}: 有流量但无 openclaw 会话库，跳过（非本哨兵范围）')
            continue
        gap_min = (last_call - mtime) / 60
        if last_call - mtime > args.stale_min * 60:
            suspects.append((aid, gap_min, last_call))

    if suspects:
        for aid, gap, last_call in suspects:
            print(f'[orphan-watch] ⚠️ 疑似孤儿循环: {aid} '
                  f'流量最新 {time.strftime("%H:%M:%S", time.localtime(last_call))}，'
                  f'会话库停更领先 {gap:.0f} 分钟（阈值 {args.stale_min}min）')
        print('[orphan-watch] 处置建议：server 内动态限频闸门应已 429 拒转；'
              '若流量仍在涨请查 server.log 的 隔离拒转/自动隔离 行并上报。')
        return 1

    for aid in sorted(traffic):
        print(f'[orphan-watch] {aid}: 流量与会话库同步，干净')
    return 0


if __name__ == '__main__':
    sys.exit(main())
