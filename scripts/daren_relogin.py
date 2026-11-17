#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""达人端半自动重登（P2-⑤ 三件套之③，2026-11-17 老大 19:46「今晚全做完」）。

规格：一条命令，人 1 分钟可修好——
  拉浏览器 → 引导扫码页 → 推「请扫码」→ 轮询确认登录恢复。

流程（ buyin_worker 控制口 8095 全程代驾，脚本不碰浏览器本体）：
  1. POST /open-daren 拉起达人端浏览器（持久化 profile_daren）
  2. 等 daren_browser_up=true
  3. 点「登录」→ 点「达人工作台」（官网引导路径，11-17 19:08 实测走通）
  4. 桌面推「请扫码」横幅 + 落库通知（双通道，[值班] 前缀）
  5. 轮询 /health daren_logged_in 直到 true（默认超时 10 分钟）
  6. 成功 → 再推一条「已恢复」（daren_login_watch 死→活沿只记日志，
     所以恢复确认由本脚本推）+ 退出码 0；超时 → 1

用法：
  python3 scripts/daren_relogin.py               # 完整重登（人只需扫码）
  python3 scripts/daren_relogin.py --timeout 300 # 5 分钟扫码窗口
  python3 scripts/daren_relogin.py --step open   # 只做到某步（排障用）
        # --step: open（拉起） | guide（引导扫码页） | notify | wait
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import duty_notify as dn

WORKER = os.environ.get('DAREN_WORKER', 'http://127.0.0.1:8095')
DB = os.path.join(ROOT, 'data', 'solobrave.db')
AGENT = 'emp_goods_keeper'

STEPS = ('open', 'guide', 'notify', 'wait')


def log(msg):
    print(f'[{datetime.now():%H:%M:%S}] {msg}')


def _post(path, body=None):
    req = urllib.request.Request(WORKER + path, method='POST',
                                 data=json.dumps(body or {}).encode(),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode('utf-8', errors='ignore'))


def _health():
    try:
        with urllib.request.urlopen(WORKER + '/health', timeout=5) as r:
            return json.loads(r.read().decode('utf-8', errors='ignore'))
    except Exception:
        return {}


def _wait_for(pred, timeout_s, label):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if pred():
            return True
        time.sleep(3)
    log(f'等待超时（{timeout_s}s）：{label}')
    return False


def step_open(timeout_s):
    h = _health()
    if h.get('daren_browser_up'):
        log('达人端浏览器已在，跳过拉起')
        return True
    try:
        _post('/open-daren')
    except Exception as e:
        log(f'open-daren 失败：{e}')
        return False
    return _wait_for(lambda: _health().get('daren_browser_up'), timeout_s,
                     'daren_browser_up')


def step_guide():
    """官网 → 登录 → 达人工作台 → 扫码页（11-17 19:08 实测路径）。"""
    try:
        _post('/daren-click', {'text': '登录', 'wait': 3})
        time.sleep(4)
        _post('/daren-click', {'text': '达人工作台', 'wait': 6})
        log('已引导至扫码页（登录→达人工作台）')
        return True
    except Exception as e:
        log(f'引导失败：{e}')
        return False


def step_notify():
    title = '[值班] 请扫码：达人端登录恢复'
    body = ('浏览器已停在扫码页，请用抖音扫码（约 1 分钟）。'
            '脚本自动轮询确认，扫完即恢复，无需其他操作。')
    dn.desktop_push(title, body)
    try:
        import sqlite3
        conn = sqlite3.connect(DB, timeout=15)
        try:
            dn.ensure_case_tables(conn)
            dn._insert_notification(conn, dn.ADMIN_ID, AGENT, title, body,
                                    int(time.time() * 1000))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        log(f'通知落库失败（不阻断，桌面已推）: {e}')
    log('已推「请扫码」（桌面+落库双通道）')
    return True


def step_wait(timeout_s):
    ok = _wait_for(lambda: _health().get('daren_logged_in'), timeout_s,
                   'daren_logged_in=true')
    if ok:
        title = '[值班] 达人端登录已恢复'
        body = ('扫码确认，daren_logged_in=true。'
                'daren_login_watch 沿检测归位，采集链恢复。')
        dn.desktop_push(title, body)
        log('登录恢复确认 ✅')
    return ok


def main():
    ap = argparse.ArgumentParser(description='达人端半自动重登（人 1 分钟）')
    ap.add_argument('--timeout', type=int, default=600,
                    help='扫码等待窗口秒数（默认 600=10 分钟）')
    ap.add_argument('--step', choices=STEPS, default=None,
                    help='只执行到该步（默认全跑）')
    args = ap.parse_args()

    def upto(step):
        return STEPS.index(args.step) >= STEPS.index(step) if args.step else True

    if not upto('open'):
        return 0
    if not step_open(60):
        log('浏览器拉起失败——先 kickstart worker：'
            'launchctl kickstart -k gui/$(id -u)/com.solobrave.buyin-worker')
        return 1
    if not upto('guide'):
        return 0
    if not step_guide():
        return 1
    if not upto('notify'):
        return 0
    step_notify()
    if not upto('wait'):
        return 0
    return 0 if step_wait(args.timeout) else 1


if __name__ == '__main__':
    sys.exit(main())
