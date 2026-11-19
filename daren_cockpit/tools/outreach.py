#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""触达跟进管道（单2 dq_8846ebdd308f，老大 11-19 14:23 总授权）——④入系统。

deals 状态机 2026-10-24 建好后零使用（11-19 实证：6 行全 approved 从未流转），
联系结果不回系统 ⇒ 命中率永远 0 数据。本工具把触达变成可记账、可复算、可归因的数据：

- outreach_log 表：谁、何时、什么渠道、哪版话术、结果、对方回了什么（原文留痕）
- deals 状态机接通：触达结果单向推进 deal 状态（回退抛错，错挂比漏挂贵）
- 周日漏斗：推荐→触达→回应→洽谈→成交，纯 SQL 可复算（报告数字=重跑数字）

用法（CLI）：
    python3 daren_cockpit/tools/outreach.py record --talent-id tal_x --channel douyin_dm \
        --dm-version v20261119-1 --result 洽谈 --note "对方问佣金细节"
    python3 daren_cockpit/tools/outreach.py funnel --week 2026-11-17   # 该周周一日期
    python3 daren_cockpit/tools/outreach.py init                        # 幂等建表

设计铁律：
1. result 五态 CHECK 锁（未回/拒绝/洽谈/寄样/成交）——非法值落不了库
2. deals 单向流转：DEAL_ORDER 序位新 ≥ 旧；completed/failed 是终态不再动
3. 漏斗口径写死在本文件 FUNNEL_DEF，周报数字必须能用同一函数重算（守卫锁）
4. dm_version 格式锁 vYYYYMMDD-N（Helen 话术 v20261119-1 即首版）
"""
import argparse
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / 'data' / 'solobrave.db'

RESULTS = ('未回', '拒绝', '洽谈', '寄样', '成交')
CHANNELS = ('douyin_dm', 'wechat', 'phone', 'other')

# deals 状态机序位（与 AGENTS.md §8.4 状态机一致；completed/failed 终态同位 99）
DEAL_ORDER = {
    'pending': 0, 'negotiating': 1, 'sample_sent': 2,
    'approved': 3, 'live': 4, 'completed': 5, 'failed': 99,
}
# outreach 结果 → deal 目标状态（None=不推进）
RESULT_TO_DEAL = {'洽谈': 'negotiating', '寄样': 'sample_sent',
                  '成交': 'completed', '拒绝': 'failed'}

DM_VERSION_RE = re.compile(r'^v\d{8}-\d+$')

FUNNEL_DEF = """漏斗口径（唯一事实来源，重算必须同函数）：
触达率 = 周内被触达达人数 / 周内推荐达人数（talent_recommendations.rec_date ∈ 周）
回应率 = result≠未回 的触达数 / 触达总数
洽谈率 = result∈(洽谈,寄样,成交) / 触达总数
成交率 = result=成交 / 触达总数
按达人去重：同一达人周内多次触达按 1 人计，结果取最优（成交>寄样>洽谈>拒绝>未回）。"""

SCHEMA = '''CREATE TABLE IF NOT EXISTS outreach_log (
    id TEXT PRIMARY KEY,
    talent_id TEXT NOT NULL,
    talent_name TEXT DEFAULT '',
    rec_date TEXT DEFAULT '',
    run_id TEXT DEFAULT '',
    channel TEXT NOT NULL,
    dm_version TEXT DEFAULT '',
    contacted_at INTEGER NOT NULL,
    result TEXT NOT NULL DEFAULT '未回'
        CHECK(result IN ('未回','拒绝','洽谈','寄样','成交')),
    note TEXT DEFAULT '',
    created_by TEXT DEFAULT '',
    created_at INTEGER,
    updated_at INTEGER
)'''


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(SCHEMA)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_outreach_talent ON outreach_log(talent_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_outreach_time ON outreach_log(contacted_at)")
    conn.commit()


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


def record_outreach(conn: sqlite3.Connection, *, talent_id: str,
                    channel: str, result: str, contacted_at: int = None,
                    talent_name: str = '', rec_date: str = '', run_id: str = '',
                    dm_version: str = '', note: str = '',
                    created_by: str = '') -> dict:
    """记一笔触达。校验全在写入前——任何非法值抛 ValueError，不落半吊子数据。"""
    if not talent_id:
        raise ValueError('talent_id 必填')
    if channel not in CHANNELS:
        raise ValueError(f'channel 必须是 {CHANNELS}')
    if result not in RESULTS:
        raise ValueError(f'result 必须是 {RESULTS}')
    if dm_version and not DM_VERSION_RE.match(dm_version):
        raise ValueError('dm_version 格式 vYYYYMMDD-N（如 v20261119-1）')
    ts = contacted_at or _now_ms()
    oid = f'out_{uuid.uuid4().hex[:12]}'
    conn.execute(
        'INSERT INTO outreach_log (id, talent_id, talent_name, rec_date, run_id, '
        'channel, dm_version, contacted_at, result, note, created_by, created_at, '
        'updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (oid, talent_id, talent_name, rec_date, run_id, channel, dm_version,
         ts, result, note, created_by, ts, ts))
    conn.commit()
    return {'id': oid, 'talent_id': talent_id, 'result': result}


def advance_deal(conn: sqlite3.Connection, deal_id: str, new_status: str,
                 note: str = '') -> dict:
    """触达结果推进 deal 状态（单向锁）。回退/终态再动 → ValueError。"""
    if new_status not in DEAL_ORDER:
        raise ValueError(f'未知 deal 状态 {new_status}')
    row = conn.execute('SELECT id, status FROM deals WHERE id = ?',
                       (deal_id,)).fetchone()
    if not row:
        raise ValueError(f'deal 不存在：{deal_id}')
    old = row['status'] if isinstance(row, sqlite3.Row) else row[1]
    if old in ('completed', 'failed'):
        raise ValueError(f'deal {deal_id} 已是终态 {old}，不再流转')
    if DEAL_ORDER[new_status] < DEAL_ORDER.get(old, 0):
        raise ValueError(f'单向锁：{old} → {new_status} 是回退，拒绝')
    ts = _now_ms()
    conn.execute('UPDATE deals SET status = ?, result_note = ?, updated_at = ? '
                 'WHERE id = ?', (new_status, note, ts, deal_id))
    conn.commit()
    return {'id': deal_id, 'from': old, 'to': new_status}


def apply_outcome_to_deals(conn: sqlite3.Connection, *, talent_id: str,
                           result: str, note: str = '') -> list:
    """outreach 结果 → 该达人名下全部在池 deal 推进（RESULT_TO_DEAL 映射）。"""
    target = RESULT_TO_DEAL.get(result)
    if not target:
        return []
    rows = conn.execute(
        "SELECT id FROM deals WHERE talent_id = ? AND status NOT IN ('completed','failed')",
        (talent_id,)).fetchall()
    out = []
    for r in rows:
        did = r['id'] if isinstance(r, sqlite3.Row) else r[0]
        try:
            out.append(advance_deal(conn, did, target, note))
        except ValueError as e:
            out.append({'id': did, 'skipped': str(e)})   # 已在更后状态=幂等跳过
    return out


_BEST = {r: i for i, r in enumerate(RESULTS)}   # 未回0 拒绝1 洽谈2 寄样3 成交4


def render_funnel(conn: sqlite3.Connection, week_start: str) -> dict:
    """周日漏斗。week_start=该周周一 'YYYY-MM-DD'。纯 SQL 可复算（守卫锁口径）。"""
    start = datetime.strptime(week_start, '%Y-%m-%d')
    end = (start + timedelta(days=7)).strftime('%Y-%m-%d')
    recs = conn.execute(
        "SELECT talent_id FROM talent_recommendations WHERE rec_date >= ? AND rec_date < ?",
        (week_start, end)).fetchall()
    rec_talents = {r['talent_id'] if isinstance(r, sqlite3.Row) else r[0] for r in recs}
    logs = conn.execute(
        "SELECT talent_id, result FROM outreach_log WHERE contacted_at >= ? AND contacted_at < ?",
        (int(start.timestamp() * 1000), int(datetime.strptime(end, '%Y-%m-%d').timestamp() * 1000))).fetchall()
    touched = {}
    for r in logs:
        tid = r['talent_id'] if isinstance(r, sqlite3.Row) else r[0]
        res = r['result'] if isinstance(r, sqlite3.Row) else r[1]
        if tid not in touched or _BEST[res] > _BEST[touched[tid]]:
            touched[tid] = res
    n_rec, n_touch = len(rec_talents), len(touched)
    n_reply = sum(1 for v in touched.values() if v != '未回')
    n_talk = sum(1 for v in touched.values() if v in ('洽谈', '寄样', '成交'))
    n_win = sum(1 for v in touched.values() if v == '成交')
    return {'week': week_start, 'recommended': n_rec, 'touched': n_touch,
            'replied': n_reply, 'talking': n_talk, 'won': n_win,
            'touch_rate': round(n_touch / n_rec, 4) if n_rec else 0,
            'reply_rate': round(n_reply / n_touch, 4) if n_touch else 0,
            'talk_rate': round(n_talk / n_touch, 4) if n_touch else 0,
            'win_rate': round(n_win / n_touch, 4) if n_touch else 0,
            'definition': FUNNEL_DEF}


def main() -> int:
    ap = argparse.ArgumentParser(description='触达跟进管道（outreach_log + deals 状态机 + 漏斗）')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('init', help='幂等建表')
    p = sub.add_parser('record', help='记一笔触达')
    p.add_argument('--talent-id', required=True)
    p.add_argument('--channel', required=True, choices=CHANNELS)
    p.add_argument('--result', required=True, choices=RESULTS)
    p.add_argument('--talent-name', default='')
    p.add_argument('--rec-date', default='')
    p.add_argument('--run-id', default='')
    p.add_argument('--dm-version', default='')
    p.add_argument('--note', default='')
    p.add_argument('--created-by', default='')
    p.add_argument('--push-deals', action='store_true', help='同时推进该达人 deals')
    p = sub.add_parser('funnel', help='周日漏斗')
    p.add_argument('--week', required=True, help='该周周一 YYYY-MM-DD')
    args = ap.parse_args()

    conn = sqlite3.connect(str(DB), timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        ensure_schema(conn)
        if args.cmd == 'init':
            print('outreach_log 已就位（幂等）')
        elif args.cmd == 'record':
            out = record_outreach(conn, talent_id=args.talent_id, channel=args.channel,
                                  result=args.result, talent_name=args.talent_name,
                                  rec_date=args.rec_date, run_id=args.run_id,
                                  dm_version=args.dm_version, note=args.note,
                                  created_by=args.created_by)
            pushed = apply_outcome_to_deals(conn, talent_id=args.talent_id,
                                            result=args.result, note=args.note) \
                if args.push_deals else []
            print(f"已记 {out['id']}（{args.talent_id} · {args.result}）"
                  + (f'，推进 deals {len(pushed)} 单' if pushed else ''))
        elif args.cmd == 'funnel':
            for k, v in render_funnel(conn, args.week).items():
                if k != 'definition':
                    print(f'{k}: {v}')
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
