#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采集链路最轻健康告警（2026-11-11 老大修订②：管家日报是事后体检，
佣金同步停摆 17 天没人发现 ⇒ 加一道实时监护，一行字就够）。

判据：两条采集链各自的最晚成功时间 > SYNC_STALE_HOURS（48）⇒ 该链疑似停摆 ⇒ notifications 推管理员。
幂等：同链同一天只报一次（标题带链名+日期判重）。P2-③ 起双锚分列（见 CHAINS 注释）。

挂 launchd 每日 10:40（三个管家日报 9:30/10:17/10:35 全部跑完之后）。
只读 + 只写 notifications，不碰业务表。
"""
import os
import sqlite3
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'data' / 'solobrave.db'
SYNC_STALE_HOURS = 48
ADMIN_USER = 'user_7acb72ff'


# ★ P2-③ 断点口径核查修正（2026-11-16）：双锚分列——
#   ①商品同步锚 = products.daren_synced_at（达人端商品回源）
#   ②达人采集锚 = talent_collects MAX(created_at)（广场扫描）
#   11-16 实证：扫描锚 09:47 新鲜但商品锚已停 117h——两条链独立，单锚告警会被
#   另一条链的「成功」误读成误报。现两锚各自判各自报，标题带链名，按「链+日」幂等。
CHAINS = {
    '商品同步': {
        'sql': 'SELECT MAX(CAST(daren_synced_at AS INTEGER)) FROM products',
        'hint': '佣金/店铺分等达人端口径可能已全部过期——多半是回源任务久未跑，补跑一次；补跑仍失败再查登录态。',
    },
    '达人采集': {
        'sql': 'SELECT MAX(created_at) FROM talent_collects',
        'hint': '广场扫描停摆——查 buyin_worker 浏览器线程（asyncio 循环污染史）与 launchd 在岗。',
    },
}


def check_chain(conn, chain, now_ms, today):
    last = int(conn.execute(CHAINS[chain]['sql']).fetchone()[0] or 0)
    hours = (now_ms - last) / 3_600_000 if last else None

    # 幂等：该链今天已报过就不再报
    dup = conn.execute(
        "SELECT 1 FROM notifications WHERE user_id = ? AND title LIKE ?",
        (ADMIN_USER, f'%{chain}%{today}%')).fetchone()
    if dup:
        print(f'[{today}][{chain}] 今日已告警，跳过（last={hours:.1f}h 前）' if hours
              else f'[{today}][{chain}] 今日已告警，跳过（无记录）')
        return
    if last and hours <= SYNC_STALE_HOURS:
        print(f'[{today}][{chain}] 健康：上次成功 {hours:.1f}h 前（阈值 {SYNC_STALE_HOURS}h）')
        return
    stale_txt = (f'距上次成功已 {hours:.0f} 小时' if last
                 else '库里没有任何成功记录')
    title = f'⚠️ {chain}停摆告警 · {today}'
    body = (f'{chain}停摆：{stale_txt}（阈值 {SYNC_STALE_HOURS}h）。'
            f'{CHAINS[chain]["hint"]}'
            f'（两链独立：另一条链新鲜不代表本条恢复。）')
    conn.execute(
        'INSERT INTO notifications (id, user_id, agent_id, type, title, '
        'content, read, created_at) VALUES (?,?,?,?,?,?,0,?)',
        ('notif_' + uuid.uuid4().hex[:12], ADMIN_USER, 'collector_watch',
         'ops_alert', title, body, now_ms))
    conn.commit()
    print(f'[{today}][{chain}] 已告警：{body}')


def main():
    conn = sqlite3.connect(str(DB), timeout=15)
    try:
        now_ms = int(time.time() * 1000)
        today = time.strftime('%m月%d日')
        for chain in CHAINS:
            check_chain(conn, chain, now_ms, today)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
