#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""值班通知链（P1-⑧，2026-11-17 老大 19:46「立」P1 随批复）。

补「有人被叫醒修」缺环（11-17 实锤：病班 → 熔断✅ → 验卷立案✅ → 没人被叫醒修
→ 8 小时三班同病）：「通知落库 ≠ 有人被推动」——桌面推送才是叫醒。

机制（评审规格 + 老大修正：root_cause 短码枚举，立案时映射、判重按码，24h 幂等）：
  1. 立案双通道必达：notifications 落库（三天日报实证链）+ macOS 桌面横幅
     （osascript display notification，Glass 音效）。desktop_push 永远 best-effort。
  2. 同一 root_cause **连续 2 次**立案 → 直推老大（[值班升级]），24h 内同码只推一次
     （幂等锚 duty_escalations 表）。「连续」= duty_cases 最后两条同码（含本条）。
  3. 立案留痕 duty_cases（谁/什么码/什么时候）——回放、审计、判重单一真源。

短码枚举（钉死，勿自由文本）：
  need_login       达人端（或团长端）登录态失效——P2-⑤ 停摆实因
  roam_search      漫游型工具空转（熔断两条腿的次数腿/占比腿都是这个家族）
  no_delivery      干完活没交卷（验卷判据两条不满足）
  heartbeat_silent 班还活着但静默超 480s（turn 可能挂起）

挂接点：
  - scripts/run_watchdog.py  → roam_search / heartbeat_silent / no_delivery
  - scripts/daren_login_watch.py（P2-⑤ 三件套之②）→ need_login
  - CLI（任何组件/人工/协调边回放）：
      python3 scripts/duty_notify.py file --cause need_login --agent emp_goods_keeper \
          --title "[值班] 达人端登录态失效" --content "..."
      python3 scripts/duty_notify.py push --title "..." --body "..."   # 纯桌面推送

验收（协调边第四方）：回放 11-17 13:36/17:41 两班 need_login 现成样本——
第二次立案时老大桌面收到 [值班升级] 推送。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, 'data', 'solobrave.db')
ADMIN_ID = 'user_7acb72ff'

ROOT_CAUSES = ('need_login', 'roam_search', 'no_delivery', 'heartbeat_silent',
               'worker_thread_sick')   # ⑤ 老大 11-18 00:30 拍板追加（probe=null 归此类）
ESCALATE_WINDOW_MS = 24 * 3600 * 1000   # 同码升级 24h 幂等（老大钉死）


def log(msg):
    print(f'[{datetime.now():%H:%M:%S}] {msg}')


def _now_ms():
    return int(time.time() * 1000)


def desktop_push(title, body=''):
    """macOS 桌面横幅。永远 best-effort：任何失败只吞掉不抛（推送不可反噬主链）。"""
    try:
        script = (f'display notification {json.dumps((body or "")[:200])} '
                  f'with title {json.dumps(title[:100])} sound name "Glass"')
        subprocess.run(['osascript', '-e', script],
                       capture_output=True, timeout=10)
        return True
    except Exception:
        return False


def ensure_case_tables(conn):
    conn.execute(
        '''CREATE TABLE IF NOT EXISTS duty_cases (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            root_cause TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL
        )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_duty_cases_time '
                 'ON duty_cases(root_cause, created_at)')
    conn.execute(
        '''CREATE TABLE IF NOT EXISTS duty_escalations (
            root_cause TEXT PRIMARY KEY,
            last_at INTEGER NOT NULL
        )''')
    conn.commit()


def _insert_notification(conn, user_id, agent_id, title, content, now_ms):
    conn.execute(
        'INSERT INTO notifications (id, user_id, agent_id, type, title, content,'
        ' read, created_at) VALUES (?,?,?,?,?,?,0,?)',
        ('notif_' + uuid.uuid4().hex[:12], user_id, agent_id,
         'task_reminder', title, content, now_ms))


def _consecutive_same_cause(conn, cause):
    """duty_cases 最后两条（含未插入的本条由调用方保证已插入）同码 = 连击。"""
    rows = conn.execute(
        'SELECT root_cause FROM duty_cases ORDER BY created_at DESC, id DESC '
        'LIMIT 2').fetchall()
    return len(rows) == 2 and rows[0][0] == cause and rows[1][0] == cause


def _escalate_if_needed(conn, cause, agent_id, now_ms):
    """连击判定 + 24h 幂等 + 升级双通道。返回是否真升级（测试断言用）。

    ★ 升级正文必须带 case_id 清单（老大 11-18 00:30 拍板）：推给老大的每一条
      告警都要能回溯到具体立案——「通知存在但案卷缺席」视为链路 bug，不允许。
    """
    if not _consecutive_same_cause(conn, cause):
        return False
    row = conn.execute('SELECT last_at FROM duty_escalations WHERE root_cause = ?',
                       (cause,)).fetchone()
    if row and now_ms - row[0] < ESCALATE_WINDOW_MS:
        return False  # 24h 内同码已升级过（幂等）
    # 取连击的两条立案 id（回溯锚）
    pair = conn.execute(
        'SELECT id FROM duty_cases WHERE root_cause = ? '
        'ORDER BY created_at DESC, id DESC LIMIT 2', (cause,)).fetchall()
    case_ids = [r[0] for r in pair]
    conn.execute('INSERT OR REPLACE INTO duty_escalations (root_cause, last_at)'
                 ' VALUES (?,?)', (cause, now_ms))
    title = f'[值班升级] 同根因连击：{cause}'
    content = (f'root_cause={cause} 连续 2 次立案（24h 幂等窗口内每码只升级一次）。'
               f'立案链：{case_ids[1]} → {case_ids[0]}（最新来自 agent={agent_id}）。'
               f'回溯：SELECT * FROM duty_cases WHERE id IN 上述两 id。'
               f'按 P1-⑧ 规格直接推老大：该修修根因了，别让它出第三次。')
    _insert_notification(conn, ADMIN_ID, agent_id, title, content, now_ms)
    conn.commit()
    desktop_push(title, content)
    return True


def file_case(conn, agent_id, cause, title, content, *,
              now_ms=None, notify=True):
    """立案：留痕 duty_cases + （默认）双通道通知 + 连击升级判定。

    notify=False 用于 run_watchdog 这类「通知已自己落库」的调用方——
    本函数只做留痕 + 升级，不重复插通知/不重复推桌面。
    返回 {'case_id': str, 'escalated': bool}。
    """
    if cause not in ROOT_CAUSES:
        raise ValueError(f'root_cause 必须是短码枚举 {ROOT_CAUSES}，got {cause!r}')
    now_ms = now_ms or _now_ms()
    case_id = f'dc_{uuid.uuid4().hex[:12]}'
    conn.execute(
        'INSERT INTO duty_cases (id, agent_id, root_cause, title, content,'
        ' created_at) VALUES (?,?,?,?,?,?)',
        (case_id, agent_id, cause, title, content, now_ms))
    if notify:
        _insert_notification(conn, ADMIN_ID, agent_id, title, content, now_ms)
        conn.commit()
        desktop_push(title, content)
    else:
        conn.commit()
    escalated = _escalate_if_needed(conn, cause, agent_id, now_ms)
    if escalated:
        log(f'[值班升级] {cause} 连击，已直推老大 case={case_id}')
    return {'case_id': case_id, 'escalated': escalated}


def map_fuse_reason(why):
    """熔断理由 → root_cause 短码。两条腿（次数/占比）都是漫游型家族（§145 原旨：
    熔断拦的就是「工具越调越多」的漫游班），统一 roam_search。"""
    return 'roam_search'


def main(argv=None):
    ap = argparse.ArgumentParser(description='值班通知链立案/推送 CLI')
    ap.add_argument('--db', default=DB, help='solobrave.db 路径（测试注入）')
    sub = ap.add_subparsers(dest='cmd', required=True)

    f = sub.add_parser('file', help='立案（留痕+通知+连击升级判定）')
    f.add_argument('--cause', required=True, choices=ROOT_CAUSES)
    f.add_argument('--agent', required=True)
    f.add_argument('--title', required=True)
    f.add_argument('--content', default='')
    f.add_argument('--now', type=int, default=None, help='冻结毫秒（回放用）')

    p = sub.add_parser('push', help='纯桌面推送（不立案件）')
    p.add_argument('--title', required=True)
    p.add_argument('--body', default='')

    args = ap.parse_args(argv)
    if args.cmd == 'push':
        ok = desktop_push(args.title, args.body)
        print('pushed' if ok else 'push failed (best-effort, 不阻断)')
        return 0

    conn = sqlite3.connect(args.db, timeout=15)
    try:
        ensure_case_tables(conn)
        # CLI 立案属于独立通道，通知由本函数落库
        out = file_case(conn, args.agent, args.cause, args.title, args.content,
                        now_ms=args.now)
        print(json.dumps(out, ensure_ascii=False))
        return 0
    finally:
        conn.close()


if __name__ == '__main__':
    sys.exit(main())
