#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1 逾期升级推送（P2-③，2026-11-16）：补建「升级→通知投递」缺失机制。

实证根因（§158④）：dispatch_queue.p1_escalated_at 自始只有 schema，**全仓无任何
代码写它或发升级通知**——11-15 09:49 那笔标记是一次性手工 sqlite，通知零条。
老大没收到升级 = 机制根本不存在，不是通知通道故障。

机制（本脚本 + launchd 每日扫）：
- 扫描 priority=1 且 status IN ('待领','施工') 的单；
- 账龄 = now - (claimed_at || created_at) > 24h 且 p1_escalated_at=0
  → 置 p1_escalated_at（幂等）+ notifications 推老大 [升级]。
- 「必达」回归口径：标记与通知在同一事务里落库，通知表即送达证据。

用法：python3 scripts/dispatch_escalate.py [--dry-run]
退出码：0 = 无升级 / 升级已推；1 = 检查出错。
"""
import argparse
import sqlite3
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / 'data' / 'solobrave.db'
ADMIN_USER = 'user_7acb72ff'
ESCALATE_AFTER_MS = 24 * 3600 * 1000


def select_escalations(rows, now_ms, age_ms=ESCALATE_AFTER_MS):
    """纯函数：挑出过期待升级单（可单测）。

    rows: iterable of dict(id, priority, status, created_at, claimed_at, p1_escalated_at, summary)
    账龄锚点：claimed_at（已认领从认领时算）否则 created_at；p1_escalated_at>0 已升级跳过。
    """
    out = []
    for r in rows:
        if r.get('priority') != 1:
            continue
        if r.get('status') not in ('待领', '施工'):
            continue
        if r.get('p1_escalated_at'):
            continue
        anchor = r.get('claimed_at') or r.get('created_at') or 0
        if now_ms - int(anchor) > age_ms:
            out.append(r)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    now_ms = int(time.time() * 1000)
    conn = sqlite3.connect(str(DB), timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(
            'SELECT id, priority, status, created_at, claimed_at, p1_escalated_at,'
            ' substr(summary,1,80) AS summary FROM dispatch_queue')]
        due = select_escalations(rows, now_ms)
        if not due:
            print(f'[dispatch-escalate] 无逾期待升级 P1（扫描 {len(rows)} 单）')
            return 0
        for r in due:
            age_h = (now_ms - int(r.get('claimed_at') or r.get('created_at'))) / 3.6e6
            title = f'[升级] P1 超 24h 无人闭环：{r["id"]}'
            body = (f'单 {r["id"]} 已挂 {age_h:.0f} 小时（{r["status"]}）。'
                    f'{r["summary"]}…\n'
                    f'处置：①新对话 GET /api/dispatch-queue?status=待领 拉单认领；'
                    f'②若已在施工，施工方报进度；③无人接则拍板转边/降优先级。')
            print(f'{"[dry-run] " if args.dry_run else ""}升级 {r["id"]}（{age_h:.0f}h）')
            if args.dry_run:
                continue
            # 标记 + 通知同事务：标记即送达证据，杜绝「标了没通知」半态
            conn.execute('BEGIN IMMEDIATE')
            fresh = conn.execute(
                'SELECT p1_escalated_at FROM dispatch_queue WHERE id = ?',
                (r['id'],)).fetchone()
            if not fresh or fresh[0]:
                conn.execute('ROLLBACK')
                print(f'  {r["id"]} 已被他人先升级，跳过')
                continue
            conn.execute('UPDATE dispatch_queue SET p1_escalated_at = ?, updated_at = ?'
                         ' WHERE id = ?', (now_ms, now_ms, r['id']))
            conn.execute(
                'INSERT INTO notifications (id, user_id, agent_id, type, title, content,'
                ' read, created_at) VALUES (?,?,?,?,?,?,0,?)',
                ('notif_' + uuid.uuid4().hex[:12], ADMIN_USER, 'dispatch_escalate',
                 'ops_alert', title, body, now_ms))
            conn.commit()
            print(f'  已标记 + 通知落库：{title}')
        return 0
    except Exception as e:
        print(f'[dispatch-escalate] 出错: {e}', file=sys.stderr)
        try:
            conn.execute('ROLLBACK')
        except Exception:
            pass
        return 1
    finally:
        conn.close()


if __name__ == '__main__':
    sys.exit(main())
