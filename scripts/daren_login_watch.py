#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""达人端登录态即时告警（P2-⑤ 三件套之②，2026-11-17 老大 19:46「今晚全做完」）。

规格：登录态**转死 <5 分钟必达**（desktop 双通道，不等日报/看门周期）。
11-17 实锤的代价：登录态 11-11 19:34 失效，到 11-17 人工发现，停摆 117h+——
因为失效没有任何即时信号，只有周期性采集静默后才被间接看出。

机制：
  - launchd com.solobrave.daren-login-watch StartInterval 120（最坏 2 分钟级延迟，
    <5 分钟规格留 2.5 倍余量）。
  - 沿检测：alive→dead 才立案 need_login（沿不重复报，防警报疲劳——
    绿灯静默纪律）；死→活恢复只记日志不推送。
  - 立案走 P1-⑧ 值班通知链：notifications 落库 + 桌面横幅双通道 +
    连击升级（连续 2 次 need_login → 直推老大）。
  - 控制口不可达（worker 挂了）≠ 登录态死：只推一次桌面横幅（带 [值班] 前缀），
    不立案 need_login（不污连击计数）；恢复后清标记。

状态文件：data/watchdog-markers/daren-login-watch.state（json：{"alive": bool}）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import duty_notify as dn

DB = os.path.join(ROOT, 'data', 'solobrave.db')
WORKER = os.environ.get('DAREN_WORKER', 'http://127.0.0.1:8095')
STATE_FILE = os.path.join(ROOT, 'data', 'watchdog-markers',
                          'daren-login-watch.state')
AGENT = 'emp_goods_keeper'   # 达人端登录态的主要消费者 = 商品维度采集链


def log(msg):
    print(f'[{datetime.now():%m-%d %H:%M:%S}] {msg}')


def probe():
    """→ (reachable, logged_in)。不可达 = (False, False)。"""
    try:
        with urllib.request.urlopen(WORKER + '/health', timeout=5) as r:
            d = json.loads(r.read().decode('utf-8', errors='ignore'))
        return True, bool(d.get('daren_logged_in'))
    except Exception:
        return False, False


def _load_state():
    try:
        with open(STATE_FILE, encoding='utf-8') as f:
            return bool(json.load(f).get('alive'))
    except Exception:
        return None  # 首次运行/状态丢失 → 由调用方决定默认语义


def _save_state(alive):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump({'alive': alive, 'ts': int(time.time() * 1000)}, f)


def main():
    reachable, logged_in = probe()
    prev = _load_state()

    if not reachable:
        # worker 挂了 ≠ 登录态死：不立案，推一次横幅（幂等 = 状态文件保持 alive 语义不清，
        # 用「prev is not False」判：上次没报过不可达才报）
        if prev is not False:
            dn.desktop_push('[值班] buyin_worker 控制口不可达',
                            '8095 /health 无响应——worker 可能已挂，'
                            'launchctl kickstart -k gui/$(id -u)/com.solobrave.buyin-worker')
            log('控制口不可达，已桌面推送（未立案 need_login）')
        _save_state(False)
        return 1

    if logged_in:
        if prev is False:
            log('登录态已恢复（死→活沿，静默不推送——绿灯静默纪律）')
        _save_state(True)
        return 0

    # alive→dead 沿（含首次运行即死：prev is None 视为「沿」，立即报——
    # 宁可一次沿误报，不可漏报首见死）
    if prev is not False:
        conn = sqlite3.connect(DB, timeout=15)
        try:
            dn.ensure_case_tables(conn)
            title = '[值班] 达人端登录态已失效'
            content = (f'探测 {WORKER}/health daren_logged_in=false（沿检测，转死 <5 分钟内'
                       f'必达）。影响链：商品维度采集/回源补跑会全卡 need_login。'
                       f'处置：当班跳过不烧积分；修 = python3 scripts/daren_relogin.py'
                       f'（半自动重登，人 1 分钟）。连续 2 次立案会自动升级直推老大。')
            out = dn.file_case(conn, AGENT, 'need_login', title, content)
            log(f'登录态转死，已立案 need_login case={out["case_id"]} '
                f'escalated={out["escalated"]}')
        finally:
            conn.close()
    else:
        log('登录态仍死（沿已报过，不重复推送——防警报疲劳）')
    _save_state(False)
    return 1


if __name__ == '__main__':
    sys.exit(main())
