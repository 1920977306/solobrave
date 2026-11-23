#!/usr/bin/env python3
"""历史回填：11-20~11-23 小拓日报推荐段 → talent_recommendations（①号单 2026-11-23）。

背景：11-20 起「联系方式可得」gate 让 payload recommendations 恒为空数组，B 表四天
零行（投递语义盲区，根因 buyin_worker 原 4007 行只信 payload）。本脚本用同一兜底
解析器（_square_recs_from_report_text）从存量日报 notifications 解析补录。

口径（协调边定）：
- 通知按（日期, 班次）分组取**末版**（改档版为准：11-20 09:30/13:30 各有同班次补充）；
- 判定以 talents.remark.square_judgment.verdict 为准（库单一真源，不猜散文）；
- 11-22 18:03/18:05 的「待触达清单（14位）」是跨班累积清单（含日期锚），解析器
  整体拒解析 ⇒ 11-22 真零行，不造数；
- 按日清多余行：DELETE 该日旧行后 INSERT OR REPLACE（rec_id 同日同人幂等）。

用法：
  python3 scripts/backfill_square_recs.py                # dry-run，只打印对拍表
  python3 scripts/backfill_square_recs.py --apply        # 实际写库
对拍期望（协调边验收口径）：11-20=9 人、11-21=8 人、11-22=0 人；不符 exit 1。
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import buyin_worker as bw  # noqa: E402

DB = ROOT / 'data' / 'solobrave.db'
EXPECTED = {'2026-11-20': 9, '2026-11-21': 8, '2026-11-22': 0}

_SHIFT_RE = re.compile(r'广场即时班\s*·\s*(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})')
_SHIFT_FALLBACK_RE = re.compile(r'第\d/3班\s*(\d{2}:\d{2})')
_RUN_RE = re.compile(r'run=(ssq_\w+)')


def _shift_key(content: str, created_at: int):
    m = _SHIFT_RE.search(content) or _SHIFT_FALLBACK_RE.search(content)
    if m:
        if m.re is _SHIFT_RE:
            return (m.group(1), m.group(2))
        # 回退口径只有时刻：日期取通知创建日
        import datetime as dt
        d = dt.datetime.fromtimestamp(created_at / 1000).strftime('%Y-%m-%d')
        return (d, m.group(1))
    return ('', '')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=str(DB))
    ap.add_argument('--apply', action='store_true', help='实际写库（默认 dry-run）')
    ap.add_argument('--through', default=None,
                    help='回填截止日（含），默认昨天；在飞日不许填')
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    # ★ 边界铁律（11-23 事故实证）：历史回填是「封账」工具，绝不许碰在飞日——
    # 在飞日的真源是 decide 双写/日报交付链，回填脚本只从「日报正文」一个口径
    # 解析，先删后插会把当班活数据整体冲掉（今晚 11-23 五行被冲空实锤）。
    # 范围硬顶到昨天；要补今天就叫「修复」不叫「回填」，走单独派单。
    import datetime as _dt
    through = (_dt.date.fromisoformat(args.through)
               if args.through else _dt.date.today() - _dt.timedelta(days=1))
    through_ms = int(_dt.datetime.combine(
        through + _dt.timedelta(days=1), _dt.time()).timestamp() * 1000)
    from_ms = int(_dt.datetime.combine(
        through - _dt.timedelta(days=8), _dt.time()).timestamp() * 1000)
    rows = conn.execute(
        "SELECT id, title, content, created_at FROM notifications "
        "WHERE agent_id = 'emp_xiaotuo' AND type = 'task_reminder' "
        "AND title LIKE '达人筛选专员日报%' "
        "AND created_at BETWEEN ? AND ? ORDER BY created_at",
        (from_ms, through_ms)).fetchall()
    if not rows:
        print('!! 范围内无小拓日报通知'); sys.exit(1)

    # 按（日期, 班次）分组取末版
    shifts = {}
    for r in rows:
        key = _shift_key(r['content'], r['created_at'])
        if key not in shifts or r['created_at'] > shifts[key]['created_at']:
            shifts[key] = r

    print(f'{"日期":<12}{"班次":<8}{"版本":<20}{"run_id":<28}{"解析":<4}名单')
    per_day = {}
    day_rows = {}
    for (d, t), r in sorted(shifts.items()):
        recs = bw._square_recs_from_report_text(r['content'], conn)
        import datetime as dt
        ver = dt.datetime.fromtimestamp(r['created_at'] / 1000).strftime('%m-%d %H:%M')
        rid_m = _RUN_RE.search(r['content'])
        rid = rid_m.group(1) if rid_m else ''
        names = '、'.join(x['talent_name'] for x in recs) or '—'
        print(f'{d or "?":<12}{t or "?":<8}{ver:<20}{rid:<28}{len(recs):<4}{names}')
        if not d:
            continue
        per_day[d] = per_day.get(d, 0) + len(recs)
        rows_norm = bw._square_normalize_recs(recs, r['created_at'], rid)
        day_rows.setdefault(d, []).extend(rows_norm)

    print('\n=== 逐日对拍 ===')
    ok = True
    for d in sorted(per_day):
        exp = EXPECTED.get(d)
        got = per_day[d]
        mark = '✅' if exp == got else ('⚠️ 无期望口径' if exp is None else '❌')
        if exp is not None and exp != got:
            ok = False
        print(f'{d}: 解析 {got} 人 / 期望 {exp if exp is not None else "—"} {mark}')
    for d in EXPECTED:
        if d not in per_day:
            print(f'{d}: 无日报班次（期望 {EXPECTED[d]} 人）')
            if EXPECTED[d] != 0:
                ok = False

    if not args.apply:
        print('\n(dry-run：加 --apply 写库。先对拍，行数不符不要 apply)')
        sys.exit(0 if ok else 1)

    if not ok:
        print('\n对拍不符，拒绝写库。'); sys.exit(1)

    # ★ apply 期第二道闸（双保险，防 --through 被显式传成在飞日）：
    # 任何 d >= 今天 一律跳过——历史回填绝不许碰在飞日（11-23 事故实证）。
    import datetime as _dt2
    today_str = _dt2.date.today().isoformat()
    for d, nrow in sorted(day_rows.items()):
        if d >= today_str:
            print(f'!! {d} 为在飞日，按铁律跳过（回填不碰今天）')
            continue
        with conn:
            conn.execute('DELETE FROM talent_recommendations WHERE rec_date = ?', (d,))
            conn.executemany(
                'INSERT OR REPLACE INTO talent_recommendations '
                '(rec_id, rec_date, talent_id, talent_name, matched_rules, reason_text, '
                ' snapshot_json, pipeline_status, run_id, created_at, updated_at) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], 'recommended',
                  r[7], r[8], r[9]) for r in nrow])
        print(f'{d}: 写入 {len(nrow)} 行（先清后插）')
    conn.close()
    print('回填完成。')


if __name__ == '__main__':
    main()
