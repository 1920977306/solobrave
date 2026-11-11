#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采集链路最轻健康告警（2026-11-11 老大修订②：管家日报是事后体检，
佣金同步停摆 17 天没人发现 ⇒ 加一道实时监护，一行字就够）。

判据：products.daren_synced_at 最新成功时间距现在 > SYNC_STALE_HOURS（48）
⇒ 采集链路疑似停摆 ⇒ notifications 推管理员一行告警。
幂等：同一天只报一次（按标题里的日期判重）。

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


def main():
    conn = sqlite3.connect(str(DB), timeout=15)
    try:
        row = conn.execute('SELECT MAX(CAST(daren_synced_at AS INTEGER)) '
                           'FROM products').fetchone()
        last = int(row[0] or 0)
        now_ms = int(time.time() * 1000)
        hours = (now_ms - last) / 3_600_000 if last else None
        today = time.strftime('%m月%d日')

        # 幂等：今天已报过就不再报
        dup = conn.execute(
            "SELECT 1 FROM notifications WHERE user_id = ? AND title LIKE ?",
            (ADMIN_USER, f'%采集链路%{today}%')).fetchone()
        if dup:
            print(f'[{today}] 今日已告警，跳过（last={hours:.1f}h 前）' if hours
                  else f'[{today}] 今日已告警，跳过（无同步记录）')
            return

        if last and hours <= SYNC_STALE_HOURS:
            print(f'[{today}] 采集链路健康：上次成功 {hours:.1f}h 前（阈值 {SYNC_STALE_HOURS}h）')
            return

        stale_txt = (f'距上次成功已 {hours:.0f} 小时' if last
                     else '库里没有任何同步成功记录')
        title = f'⚠️ 采集链路停摆告警 · {today}'
        body = (f'达人端商品同步停摆：{stale_txt}（阈值 {SYNC_STALE_HOURS}h）。'
                f'佣金/店铺分等达人端口径可能已全部过期——多半是回源任务久未跑，补跑一次；补跑仍失败再查登录态。')
        conn.execute(
            'INSERT INTO notifications (id, user_id, agent_id, type, title, '
            'content, read, created_at) VALUES (?,?,?,?,?,?,0,?)',
            ('notif_' + uuid.uuid4().hex[:12], ADMIN_USER, 'collector_watch',
             'ops_alert', title, body, now_ms))
        conn.commit()
        print(f'[{today}] 已告警：{body}')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
