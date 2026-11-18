#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""达人端登录态即时告警（P2-⑤ 三件套之②，2026-11-17 老大 19:46「今晚全做完」）。

规格：登录态**转死 <5 分钟必达**（desktop 双通道，不等日报/看门周期）。
11-17 实锤的代价：登录态 11-11 19:34 失效，到 11-17 人工发现，停摆 117h+——
因为失效没有任何即时信号，只有周期性采集静默后才被间接看出。

机制（P1-⑨ 2026-11-18 修正）：
  - **登录态真值 = 盘上 cookie**（sid_ucp_sso_v1_daren @ .buyin-daren-sso.jinritemai.com），
    不是 /health 的 daren_logged_in——后者是浏览器态，互斥切换/部署窗口必假死。
    11-18 13:09 误报实案：A 边部署窗口浏览器态假死 → 立案+升级直推老大，
    而盘上 cookie 活到 2027-01-16。
  - launchd com.solobrave.daren-login-watch StartInterval 120（最坏 2 分钟级延迟，
    <5 分钟规格留 2.5 倍余量）。exit 1 = 设计语义（本轮探得死态/不可达），
    非异常；StartInterval 到点必重跑，无 ThrottleInterval 抑制。
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
import shutil
import sqlite3
import sys
import tempfile
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
KICK_MARKER = os.path.join(ROOT, 'data', 'watchdog-markers',
                           'worker-kick.marker')   # worker_sentry 维护窗
AGENT = 'emp_goods_keeper'   # 达人端登录态的主要消费者 = 商品维度采集链


def log(msg, flush=False):
    print(f'[{datetime.now():%m-%d %H:%M:%S}] {msg}', flush=flush)


def _daren_cookie_alive():
    """盘上 cookie 真值判定（P1-⑨）：存在且未过期。

    sid_ucp_sso_v1_daren @ .buyin-daren-sso.jinritemai.com，profile_daren 独立库。
    浏览器占用时 copy 到临时文件再查（⑧行同款姿势）。Chrome 时间 = 1601 起
    微秒；expires_utc=0 视为会话 cookie，盘上仍在即按活算。
    """
    dbp = os.path.join(ROOT, 'data', 'buyin_browser', 'profile_daren',
                       'Default', 'Cookies')
    if not os.path.exists(dbp):
        return False
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix='.sqlite')
        os.close(fd)
        shutil.copy(dbp, tmp)
        cdb = sqlite3.connect(tmp)
        row = cdb.execute(
            "SELECT expires_utc FROM cookies WHERE name='sid_ucp_sso_v1_daren' "
            "AND host_key LIKE '%buyin-daren-sso.jinritemai.com%' LIMIT 1"
        ).fetchone()
        cdb.close()
        if not row:
            return False
        exp = int(row[0] or 0)
        if exp == 0:
            return True
        now_us = int(time.time() * 1_000_000) + 11644473600_000_000  # epoch→Chrome
        return exp > now_us
    except Exception:
        return False
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


def probe():
    """→ (reachable, cookie_alive)。不可达 = (False, <盘上真值>)。

    reachable 仍走 /health（worker 活没活）；logged_in 语义已改为盘上 cookie
    （浏览器态不当登录态——P1-⑨，互斥切换/部署窗口不再误报）。
    """
    try:
        with urllib.request.urlopen(WORKER + '/health', timeout=5) as r:
            r.read()
        reachable = True
    except Exception:
        reachable = False
    return reachable, _daren_cookie_alive()


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


def _in_maintenance_window(now_ms=None):
    """worker_sentry kickstart 维护窗（marker until_ms 内）→ True。

    重启必然瞬断 daren_logged_in——窗内抑制 need_login 立案，
    否则自愈动作会自己制造误报风暴。窗内**不改状态文件**：
    保持 prev 沿，窗期一过沿检测照常触发，不吞真实死态。
    """
    try:
        with open(KICK_MARKER, encoding='utf-8') as f:
            until = int(json.load(f).get('until_ms', 0))
    except Exception:
        return False
    return (now_ms or int(time.time() * 1000)) < until


def main():
    reachable, logged_in = probe()
    prev = _load_state()
    # 观测性（解剖指令④）：每轮探活必留痕（case_id+db 路径在立案行）——
    # 0 字节日志曾是黑洞：看不出「跑过且健康」和「根本没跑」的区别。
    log(f'probe reachable={reachable} cookie_alive={logged_in} prev={prev} '
        f'db={DB}', flush=True)

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
        if _in_maintenance_window():
            log('worker_sentry 维护窗内（kickstart 瞬断属预期），抑制 need_login 立案')
            return 0   # 不改状态文件：保持沿，窗后真实死态照常报
        conn = sqlite3.connect(DB, timeout=15)
        try:
            dn.ensure_case_tables(conn)
            title = '[值班] 达人端登录态已失效'
            content = (f'盘上 cookie sid_ucp_sso_v1_daren 缺席或过期（P1-⑨ 后判真值，'
                       f'非浏览器态；沿检测，转死 <5 分钟内必达）。影响链：商品维度采集/'
                       f'回源补跑会全卡 need_login。处置：达人端任务暂不可跑；'
                       f'修 = python3 scripts/daren_relogin.py（半自动重登，人 1 分钟）。'
                       f'连续 2 次立案会自动升级直推老大。')
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
