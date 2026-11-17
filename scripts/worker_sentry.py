#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""buyin_worker 线程病哨兵（worker_sentry，2026-11-18 协调边指令包第 3 条）。

背景：线程病已发作多次（11-14 09:30 班卡死 buyin_worker 停摆 63h+、11-17 19:0x
复发致控制口拒绝打开达人端浏览器）——症状 = worker 进程活着但事件循环被
「Sync API inside the asyncio loop」污染，采集链静默停摆，只有周期性系统告警
间接暴露，人工发现时损失已是小时级。

机制（协调边脑边规格）：
  1. 签名扫描：被扫日志中出现「Sync API inside the asyncio loop」= 病 1 轮。
     worker.log 与 launchd.out.log 对同一事件双写 ⇒ 扫两份取并集（增量位点
     推进，重复行天然不算新轮）。
  2. 连续 2 轮病 → 自动 kickstart（launchctl kickstart -k），冷却 30 分钟
     （KICK_COOLDOWN_S）——单次抖动不重启（误杀代价 = 重启掉浏览器），
     连续两轮才动手（复发现实已发生两次，等不到第三次）。
  3. kick 前先写维护窗 marker（worker-kick.marker，until=+15min）：
     daren_login_watch 在窗内抑制 need_login 立案——重启必然瞬断 daren_logged_in，
     不抑制就是「自愈动作自己制造误报风暴」。
  4. kick 顺序按 §8.2 实证姿势：先 /graceful-daren 落盘 cookie（best-effort）→
     kickstart → 轮询 /health 可达 → POST /open-daren（盘上 cookie 还在，
     无需扫码，§8.2 已实证）。
  5. 立案走 P1-⑧ 值班通知链：root_cause=worker_thread_sick
     （老大 11-18 00:30 拍板第 5 码，probe=null 归此类）——
     可见、可回溯（升级正文带 case_id，幽灵升级教训）。

验收（协调边第四方）：模拟坏状态（往被扫日志按 [MM-DD HH:MM:SS] 格式写一行
签名）→ 连跑两轮 → 自动 kickstart + worker-kick.marker 落盘 + duty_cases
可见立案。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import duty_notify as dn

DB = os.path.join(ROOT, 'data', 'solobrave.db')
WORKER = os.environ.get('DAREN_WORKER', 'http://127.0.0.1:8095')
LAUNCHD_LABEL = 'com.solobrave.buyin-worker'
AGENT = 'emp_buyin_worker'

SIGNATURE = 'Sync API inside the asyncio loop'
LOG_DIR = os.path.join(ROOT, 'data', 'buyin_browser')
LOG_FILES = ('worker.log', 'launchd.out.log')
STATE_FILE = os.path.join(ROOT, 'data', 'watchdog-markers',
                          'worker-sentry.state')
KICK_MARKER = os.path.join(ROOT, 'data', 'watchdog-markers',
                           'worker-kick.marker')

KICK_COOLDOWN_S = 30 * 60          # 同一哨兵两次 kick 的最小间隔（协调边规格）
MAINTENANCE_WINDOW_MS = 15 * 60 * 1000   # 维护窗：kick 后 daren_login_watch 抑制立案时长
HEALTH_WAIT_S = 60                 # kick 后等 /health 可达的最久时间
LINE_TS_RE = re.compile(r'\[(\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]')


def log(msg):
    print(f'[{datetime.now():%m-%d %H:%M:%S}] {msg}')


def _line_ts_ms(line, year):
    m = LINE_TS_RE.match(line)
    if not m:
        return None
    try:
        dt = datetime.strptime(f'{year}-{m.group(1)}', '%Y-%m-%d %H:%M:%S')
        return int(dt.timestamp() * 1000)
    except (ValueError, OSError, OverflowError):
        return None


def scan_logs(log_dir, log_files, since_ms=0, now_ms=None):
    """扫签名增量。→ (found_new: bool, max_ts_ms: int)。

    since_ms = 上次扫描位点（行时间戳 > 位点才算新轮）；
    max_ts_ms = 本轮扫到的最大行时间戳（新位点；没扫到带戳行则回退 since_ms，
    防止日志截断/格式漂移把位点倒退）。
    """
    now_ms = now_ms or int(time.time() * 1000)
    year = datetime.fromtimestamp(now_ms / 1000).year
    found = False
    max_ts = since_ms
    for name in log_files:
        path = os.path.join(log_dir, name)
        try:
            with open(path, encoding='utf-8', errors='ignore') as f:
                for line in f:
                    if SIGNATURE not in line:
                        continue
                    ts = _line_ts_ms(line, year)
                    if ts is None:
                        continue
                    if ts > since_ms:
                        found = True
                        if ts > max_ts:
                            max_ts = ts
        except OSError:
            continue  # 日志缺失 ≠ 病（文件没建出来不构成证据）
    return found, max_ts


def _load_state():
    try:
        with open(STATE_FILE, encoding='utf-8') as f:
            d = json.load(f)
        return {'last_scan_ms': int(d.get('last_scan_ms', 0)),
                'sick_rounds': int(d.get('sick_rounds', 0)),
                'last_kick_ms': int(d.get('last_kick_ms', 0))}
    except Exception:
        return {'last_scan_ms': 0, 'sick_rounds': 0, 'last_kick_ms': 0}


def _save_state(st):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(st, f)


def write_kick_marker(now_ms, until_ms):
    """维护窗 marker：daren_login_watch 在窗内抑制 need_login 立案。"""
    os.makedirs(os.path.dirname(KICK_MARKER), exist_ok=True)
    with open(KICK_MARKER, 'w', encoding='utf-8') as f:
        json.dump({'until_ms': until_ms, 'reason': 'worker_sentry kickstart',
                   'set_at_ms': now_ms}, f)


def _post(path, timeout=10):
    """worker 控制口 POST，best-effort（任何失败不反噬哨兵主链）。"""
    try:
        req = urllib.request.Request(WORKER + path, data=b'',
                                     method='POST')
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status < 400
    except Exception:
        return False


def kickstart():
    """§8.2 实证姿势：graceful-daren 落盘 cookie → kick → 等 health → open-daren。"""
    _post('/graceful-daren', timeout=15)
    target = f'gui/{os.getuid()}/{LAUNCHD_LABEL}'
    try:
        subprocess.run(['launchctl', 'kickstart', '-k', target],
                       capture_output=True, timeout=30)
    except Exception as e:
        log(f'kickstart 调用失败（不重试，下轮哨兵再说）：{e}')
        return False
    deadline = time.time() + HEALTH_WAIT_S
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(WORKER + '/health',
                                        timeout=5) as r:
                if r.status < 400:
                    break
        except Exception:
            pass
        time.sleep(3)
    _post('/open-daren', timeout=30)
    return True


def main(now_ms=None):
    now_ms = now_ms or int(time.time() * 1000)
    st = _load_state()
    found, max_ts = scan_logs(LOG_DIR, LOG_FILES,
                              since_ms=st['last_scan_ms'], now_ms=now_ms)
    # 位点单调推进：本轮扫过的行（含旧行）下轮一律不算新轮。
    st['last_scan_ms'] = max(st['last_scan_ms'], max_ts)

    if not found:
        if st['sick_rounds']:
            log(f'线程病签名未再出现，病轮清零（原 {st["sick_rounds"]} 轮）')
        st['sick_rounds'] = 0
        _save_state(st)
        return 0

    st['sick_rounds'] += 1
    log(f'检测到线程病签名（第 {st["sick_rounds"]} 轮连续）')
    if st['sick_rounds'] < 2:
        _save_state(st)
        return 0  # 单次抖动不重启（误杀代价 = 掉浏览器）

    if now_ms - st['last_kick_ms'] < KICK_COOLDOWN_S * 1000:
        log('冷却期内（30min），本轮只计轮不重启')
        _save_state(st)
        return 0

    write_kick_marker(now_ms, now_ms + MAINTENANCE_WINDOW_MS)
    ok = kickstart()
    st['last_kick_ms'] = now_ms
    st['sick_rounds'] = 0
    _save_state(st)

    conn = sqlite3.connect(DB, timeout=15)
    try:
        dn.ensure_case_tables(conn)
        title = '[值班] buyin_worker 线程病自愈：已 kickstart'
        content = (
            f'连续 2 轮扫描到「{SIGNATURE}」签名，'
            f'哨兵已自动 kickstart（{LAUNCHD_LABEL}）并按 §8.2 姿势 '
            f'/graceful-daren → kick → /open-daren 恢复登录态浏览器。'
            f'维护窗 15 分钟内登录看门不立案 need_login（防自愈误报）。'
            f'若反复自愈 = 上游调用方在 asyncio 循环里碰 Sync API，'
            f'根因在调用代码不在 worker，请顺着签名行上下文查。')
        out = dn.file_case(conn, AGENT, 'worker_thread_sick',
                           title, content, now_ms=now_ms)
        log(f'已立案 worker_thread_sick case={out["case_id"]} '
            f'escalated={out["escalated"]} kick={"成功" if ok else "调用失败"}')
    finally:
        conn.close()
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
