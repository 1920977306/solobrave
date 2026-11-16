#!/usr/bin/env python3
"""一次性验收工具：小拓「每日推荐 5 人」段 + talent_recommendations 落库核查。

老大 2026-11-13 19:31 钉死四条规格，明早（2026-11-14）首验后一键跑：
  1. 当日小拓日报通知 → 文本含「每日推荐」段（段标题 + ≥5 个达人条目）
  2. talent_recommendations 当日记录数 = 5（±0，同日去重后）
  3. 每条 matched_rules 是合法 JSON 且非空数组（空数组 = 没存规则，⚠️）
  4. 每条 reason_text 非空且含数字（可验证事实的最低形态）

⚠️ 纪律：一次性验收工具，**不进 agent_health_check 探针**——「新探针层」在
老大拍板的冻结范围内。验收完使命即结束；常驻化须等管线稳定 + 老大明确解冻。

只读：只 SELECT，不写任何表。退出码 0 = 全过，1 = 任一失败。

用法：python3 scripts/check_daily_recommendations.py [--db PATH] [--date YYYY-MM-DD]
"""
import argparse
import datetime as dt
import json
import re
import sqlite3
import sys

DEFAULT_DB = '/Users/qichen/solobrave-prod/data/solobrave.db'

failures = []


def report(ok: bool, msg: str, warn_only: bool = False) -> bool:
    tag = '✅' if ok else ('⚠️' if warn_only else '❌')
    print(f'{tag} {msg}')
    if not ok:
        failures.append(msg)
    return ok


def day_range_ms(date_str: str):
    d0 = dt.datetime.strptime(date_str, '%Y-%m-%d')
    start = int(d0.timestamp() * 1000)
    return start, start + 24 * 3600 * 1000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=DEFAULT_DB)
    ap.add_argument('--date', default=dt.date.today().isoformat(),
                    help='验收日期，默认今天（本地）')
    args = ap.parse_args()
    day0, day1 = day_range_ms(args.date)

    print(f'── 每日推荐验收 · {args.date} · 库 {args.db} ──')

    # 纯 SELECT（行为只读）；不用 mode=ro URI——本库 WAL 模式下 ro 打不开（preflight_925 同款坑）
    conn = sqlite3.connect(args.db)
    cur = conn.cursor()
    entries = None  # 检查1 解析出的「每日推荐」段条目数（检查2b 对拍用）

    # ── 检查 1：日报通知含「每日推荐」段 ──
    row = cur.execute(
        "select title, content from notifications "
        "where agent_id='emp_xiaotuo' and title like '%日报%' "  # 标题以职责名为前缀（重组装后=「达人筛选专员日报」，旧=「小拓日报」），不能钉死「小拓日报%」——11-13 夜验实踩
        "  and title not like '%未交%' and created_at>=? and created_at<? "
        "order by created_at desc limit 1",
        (day0, day1)).fetchone()
    if not row:
        report(False, '检查1 当日无「小拓日报」通知（可能未跑/未交卷）')
    else:
        title, content = row
        text = content or ''
        m = re.search(r'每日推荐[^\n]*\n((?:.+\n)+?)(?=\n[^\s#]|\Z)', text)
        if not m:
            report(False, f'检查1 日报（{title}）无「每日推荐」段')
        else:
            body = m.group(1)
            entries = [ln for ln in body.splitlines()
                       if re.search(r'\d{5,}', ln) or re.match(r'\s*\d+[.、\)]', ln)
                       or re.match(r'\s*[-•]', ln)]
            # ★ P2-④ 配套（施工披露项，超出单的 4 条明文——不整改则零候选日必假红）：
            # 下界 5→1 与判据②「宁缺毋滥」对齐；「最多 5」由落库侧裁，此处只管段在位。
            report(len(entries) >= 1,
                   f'检查1 「每日推荐」段在位，识别到达人条目 {len(entries)} 条'
                   f'（{title}；下界已随判据②整改为 ≥1）')

    # ── 检查 2/3/4：talent_recommendations 落库 ──
    exists = cur.execute(
        "select 1 from sqlite_master where type='table' "
        "and name='talent_recommendations'").fetchone()
    if not exists:
        report(False, '检查2-4 talent_recommendations 表不存在（管线未交付？）')
    else:
        # ★ P2-④（2026-11-16）：rec_date 唯一口径 = 'YYYY-MM-DD' TEXT（整改后写入侧口径）。
        # 判据②整改（宁缺毋滥）：≥1 且 ≤5 且 = 日报「每日推荐」段实际人数——
        # 不再钉死 =5（零候选日如实报零，硬要 5 人会逼 AI 凑数编造）。
        rows = cur.execute(
            "select rec_id, matched_rules, reason_text from talent_recommendations "
            "where rec_date=?", (args.date,)).fetchall()
        if not rows:
            # 存量毫秒行兜底（迁移脚本跑完前的过渡态）
            rows = cur.execute(
                "select rec_id, matched_rules, reason_text from talent_recommendations "
                "where rec_date>=? and rec_date<?", (day0, day1)).fetchall()
        report(1 <= len(rows) <= 5,
               f'检查2 当日推荐记录数 = {len(rows)}（整改口径：≥1 且 ≤5，宁缺毋滥）')
        if entries is not None:
            report(len(rows) == len(entries),
                   f'检查2b 落库数 = 日报推荐段人数（{len(rows)} vs {len(entries)}）')

        bad_rules = []
        for rec_id, mr, _ in rows:
            try:
                parsed = json.loads(mr)
                if not isinstance(parsed, list) or not parsed:
                    bad_rules.append((rec_id, '空数组或非数组'))
            except (TypeError, json.JSONDecodeError):
                bad_rules.append((rec_id, '非法 JSON'))
        report(not bad_rules,
               f'检查3 matched_rules 全部为非空 JSON 数组'
               f'（{len(rows) - len(bad_rules)}/{len(rows)} 条合规）'
               + (f'，问题：{bad_rules}' if bad_rules else ''), warn_only=True)

        bad_reason = [rec_id for rec_id, _, rt in rows
                      if not (rt or '').strip() or not re.search(r'\d', rt)]
        report(not bad_reason,
               f'检查4 reason_text 全部非空且含数字'
               f'（{len(rows) - len(bad_reason)}/{len(rows)} 条合规）'
               + (f'，问题：{bad_reason}' if bad_reason else ''), warn_only=True)

    conn.close()
    print(f'── 结果：{"全部通过" if not failures else str(len(failures)) + " 项未过"} ──')
    return 0 if not failures else 1


if __name__ == '__main__':
    sys.exit(main())
