#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""半自动重登（P2-⑤ 三件套之③，2026-11-17 老大 19:46「今晚全做完」；
2026-11-18 适配团长端——11-18 早班实锤：两端是两把独立钥匙，达人端扫码不解团长端）。

规格：一条命令，人 1 分钟可修好——
  拉浏览器 → 扫码页就绪 → 推「请扫码」→ 轮询确认登录恢复。

流程（ buyin_worker 控制口 8095 全程代驾，脚本不碰浏览器本体）：
  1. POST /open（团长端）或 /open-daren（达人端）拉起对应浏览器
     （两端互斥：拉起一端会切走另一端，cookie 已落盘的一端重新拉起自动恢复，
      无需二次扫码——§8.2 实证）
  2. 等 browser_up / daren_browser_up=true
     （团长端 /open 直达百应首页，未登录自动落在扫码页——
      「如需登录请扫码」worker 自证；达人端走 登录→达人工作台 引导）
  3. 桌面推「请扫码」横幅 + 落库通知（双通道，[值班] 前缀）
  4. 轮询 /health 对应 logged_in 字段直到 true（默认超时 10 分钟）
     团长端判据 = ecom_us_lt_buyin + sessionid 双 cookie（buyin_auth 单一真源）
  5. 成功 → 再推一条「已恢复」+ 退出码 0；超时 → 1
  6. --end captain 追加：团长端恢复后顺手拉回达人端并确认
     （两端互斥切换，达人端 cookie 在盘上，拉起即自动登录；
      失败只记日志不反噬——团长端已恢复是本单的成功标准）

用法：
  python3 scripts/daren_relogin.py                        # 达人端（默认）
  python3 scripts/daren_relogin.py --end captain          # 团长端（广场班钥匙）
  python3 scripts/daren_relogin.py --timeout 300
  python3 scripts/daren_relogin.py --end captain --step open
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

# 两端契约：控制口端点 / health 字段 / 判据 cookie（buyin_auth 单一真源）
ENDS = {
    'captain': {
        'open_path': '/open', 'up_key': 'browser_up', 'login_key': 'logged_in',
        'name': '团长端',
        'judge': 'ecom_us_lt_buyin + sessionid 双 cookie（buyin_auth）',
        'need': '广场扫描/达人数据采集',
    },
    'daren': {
        'open_path': '/open-daren', 'up_key': 'daren_browser_up',
        'login_key': 'daren_logged_in', 'name': '达人端',
        'judge': 'sid_ucp_sso_v1_daren（buyin_auth DAREN_COOKIE）',
        'need': '商品维度采集/按商品ID搜品',
    },
}


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


def step_open(cfg, timeout_s):
    if _health().get(cfg['up_key']):
        log(f"{cfg['name']}浏览器已在，跳过拉起")
        return True
    try:
        _post(cfg['open_path'])
    except Exception as e:
        log(f"{cfg['open_path']} 失败：{e}")
        return False
    return _wait_for(lambda: _health().get(cfg['up_key']), timeout_s,
                     cfg['up_key'])


def step_guide(cfg):
    """引导至扫码页。达人端：官网 → 登录 → 达人工作台（11-17 实测）。
    团长端：百应精选联盟四步（11-18 截图实证走通）——
      1. 导航身份选择页（/open 只到官网落地页，**那里没有码**，两轮 45 分钟
         零扫码的教训：未登录 buyin 根路由重定向官网，官网无二维码）
      2. 点「招商团长」→ 登录表单
      3. 点「其他登录方式」的抖音图标（img[src*='-ecop'] 第一个）
      4. 落到 open.douyin.com OAuth 页，**真二维码**显示"""
    try:
        if cfg is ENDS['daren']:
            _post('/daren-click', {'text': '登录', 'wait': 3})
            time.sleep(4)
            _post('/daren-click', {'text': '达人工作台', 'wait': 6})
            log('已引导至扫码页（登录→达人工作台）')
            return True
        _post('/click', {
            'url': 'https://buyin.jinritemai.com/mpa/account/'
                   'institution-role-select',
            'text': '招商团长', 'wait': 6})
        time.sleep(8)
        _post('/click', {'selector': "img[src*='-ecop']", 'sel_nth': 0,
                         'wait': 8})
        log('已引导至抖音 OAuth 扫码页（精选联盟→招商团长→抖音图标）')
        return True
    except Exception as e:
        log(f'引导失败：{e}')
        return False


def step_notify(cfg):
    title = f"[值班] 请扫码：{cfg['name']}登录恢复"
    body = (f"{cfg['name']}浏览器已停在扫码页（判据：{cfg['judge']}），"
            f'请用抖音扫码（约 1 分钟）。脚本自动轮询确认，扫完即恢复，'
            f'无需其他操作。用途：{cfg["need"]}。')
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


def step_wait(cfg, timeout_s):
    ok = _wait_for(lambda: _health().get(cfg['login_key']), timeout_s,
                   f"{cfg['login_key']}=true")
    if ok:
        title = f"[值班] {cfg['name']}登录已恢复"
        body = (f"扫码确认，{cfg['login_key']}=true。采集链恢复。")
        dn.desktop_push(title, body)
        log(f"{cfg['name']}登录恢复确认 ✅")
    return ok


def _restore_other_end(other):
    """团长端成功后拉回另一端（互斥切换，盘上 cookie 自动登录，无需扫码）。"""
    try:
        _post(other['open_path'])
    except Exception as e:
        log(f"拉回{other['name']}失败（不阻断，下轮 preflight 会亮灯）：{e}")
        return
    if _wait_for(lambda: _health().get(other['login_key']), 90,
                 f"{other['login_key']}=true"):
        log(f"{other['name']}已从盘上 cookie 自动恢复 ✅")
    else:
        log(f"{other['name']}拉起后未自动登录（可能需补扫一次，"
            f"python3 scripts/daren_relogin.py --end {other_name(other)}）")


def other_name(other):
    return 'captain' if other is ENDS['captain'] else 'daren'


def main():
    ap = argparse.ArgumentParser(description='半自动重登（人 1 分钟，双端通用）')
    ap.add_argument('--end', choices=tuple(ENDS), default='daren',
                    help='哪一端（默认 daren；广场班钥匙 = captain）')
    ap.add_argument('--timeout', type=int, default=600,
                    help='扫码等待窗口秒数（默认 600=10 分钟）')
    ap.add_argument('--step', choices=STEPS, default=None,
                    help='只执行到该步（默认全跑）')
    args = ap.parse_args()
    cfg = ENDS[args.end]

    def upto(step):
        return STEPS.index(args.step) >= STEPS.index(step) if args.step else True

    if not upto('open'):
        return 0
    if not step_open(cfg, 60):
        log('浏览器拉起失败——先 kickstart worker：'
            'launchctl kickstart -k gui/$(id -u)/com.solobrave.buyin-worker')
        return 1
    if not upto('guide'):
        return 0
    if not step_guide(cfg):
        return 1
    if not upto('notify'):
        return 0
    step_notify(cfg)
    if not upto('wait'):
        return 0
    if not step_wait(cfg, args.timeout):
        return 1
    if cfg is ENDS['captain'] and not args.step:
        # 互斥：团长端扫码把达人端切走了，拉回并确认（cookie 自动恢复）
        _restore_other_end(ENDS['daren'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
