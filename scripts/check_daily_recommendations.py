#!/usr/bin/env python3
"""验收工具：小拓「每日推荐」段 + talent_recommendations 落库核查。

老大 2026-11-13 19:31 钉死四条规格；P2-④（11-16）整改判据②为宁缺毋滥；
班次加密 + P2 小修（11-18，老大 18:29 总授权语境）：
  检查1 改锚：canonical 段标题 = 行首「【每日推荐】」/「## 每日推荐」/「每日推荐」
            （「值得联系」段是旧模板，不算数——13:09 补扫班格式偏差教训）；
            取「最新一封含 canonical 段的日报」验收（零产出班不拖累，如实另报）。
  判据② per-run_id：三班/日下同一天会有多班多 run——
            2a. 当日行 run_id 非空率 100%（P2-④ ③run_id 回填）
            2b. 每 run_id 组行数 ∈ [1,5]（每班宁缺毋滥，≤5 落库侧裁剪）
            2c. 逐班对拍：通知头 run= 且含 canonical 段的班，段条目数 == 该 run 行数
            （无段班/旧模板班不参与对拍，warn 提示覆盖缺口）。

⚠️ 纪律：只 SELECT，不写任何表。退出码 0 = 全过，1 = 任一失败。

用法：python3 scripts/check_daily_recommendations.py [--db PATH] [--date YYYY-MM-DD]
"""
import argparse
import datetime as dt
import json
import re
import sqlite3
import sys

DEFAULT_DB = '/Users/qichen/solobrave-prod/data/solobrave.db'

# canonical「每日推荐」段标题（P1-⑨ 附项后的唯一合法形态；锚钉行首，标题行内
# 允许 【】/##/裸词三种写法，但不匹配「值得联系」等旧模板段）
HEADING_RE = re.compile(r'^[#>\s]*【?每日推荐】?')
# 条目行形态：- 项目符 / 1. 序号 / 行内含 ≥5 位连续数字（达人 id/抖音号）；
# 推荐主体 = 有序号行（对拍口径），序号行缺席时才退化为项目符/数字行（前向兼容）
RECO_RE = re.compile(r'^\d+[.、\)]')
ENTRY_LN_RE = re.compile(r'^(?:[-•]|\d+[.、\)])|.*\d{5,}')
RUN_RE = re.compile(r'run=\s*([A-Za-z0-9_\-]+)')


def extract_section(text: str):
    """返回 (推荐条目行列表, 标题行) 或 (None, None)。锚 = canonical 段标题。
    逐行消费：标题后续行按条目形态收，首个非条目行（风控/新段/空段尾）截断。
    段内混排「- 存疑」附注行时只计有序号的推荐行（对拍 = 落库推荐人数）。"""
    if not text:
        return None, None
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if HEADING_RE.match(ln):
            candidates = []
            for body in lines[i + 1:]:
                s = body.strip()
                if not s:
                    if candidates:
                        break
                    continue
                if ENTRY_LN_RE.match(s):
                    candidates.append(body)
                else:
                    break
            numbered = [b for b in candidates if RECO_RE.match(b.strip())]
            entries = numbered or candidates
            return entries, ln.strip()
    return None, None


failures = []


def report(ok: bool, msg: str, warn_only: bool = False) -> bool:
    tag = '✅' if ok else ('⚠️' if warn_only else '❌')
    print(f'{tag} {msg}')
    if not ok and not warn_only:
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

    # ── 检查 1：日报 canonical「每日推荐」段（最新含段通知） ──
    rows = cur.execute(
        "select title, content from notifications "
        "where agent_id='emp_xiaotuo' and title like '%日报%' "  # 标题以职责名为前缀，不能钉死「小拓日报%」（11-13 夜验实踩）
        "  and title not like '%未交%' and created_at>=? and created_at<? "
        "order by created_at desc",
        (day0, day1)).fetchall()
    section_entries = []      # 检查2c 对拍用：(run_id, 条目数)
    uncovered_runs = []       # 有 run= 头但无 canonical 段的通知（旧模板/零产出班）
    checked_title = None
    for title, content in rows:
        entries, heading = extract_section(content or '')
        run_m = RUN_RE.search(content or '')
        if entries:
            if checked_title is None:
                report(len(entries) >= 1,
                       f'检查1 「每日推荐」段在位（{heading}），识别到达人条目 '
                       f'{len(entries)} 条（{title}；canonical 锚，≥1 宁缺毋滥）')
                checked_title = title
            if run_m:
                section_entries.append((run_m.group(1), len(entries)))
        elif run_m:
            uncovered_runs.append((run_m.group(1), title))
    if checked_title is None:
        report(False, '检查1 当日日报无一封含 canonical「每日推荐」段'
                      '（可能未跑/未交卷/全是旧模板段）')
    if uncovered_runs:
        report(True, f'检查1 附注 {len(uncovered_runs)} 班未含 canonical 段'
                     f'（旧模板或零产出，不参与对拍）：'
                     + '; '.join(f'{r}/{t}' for r, t in uncovered_runs),
               warn_only=True)

    # ── 检查 2a/2b/2c：talent_recommendations 落库（per-run_id） ──
    exists = cur.execute(
        "select 1 from sqlite_master where type='table' "
        "and name='talent_recommendations'").fetchone()
    if not exists:
        report(False, '检查2 talent_recommendations 表不存在（管线未交付？）')
    else:
        # ★ P2-④（2026-11-16）：rec_date 唯一口径 = 'YYYY-MM-DD' TEXT（整改后写入侧口径）。
        rows = cur.execute(
            "select rec_id, run_id, matched_rules, reason_text "
            "from talent_recommendations where rec_date=?", (args.date,)).fetchall()
        if not rows:
            # 存量毫秒行兜底（迁移脚本跑完前的过渡态）
            rows = cur.execute(
                "select rec_id, run_id, matched_rules, reason_text "
                "from talent_recommendations where rec_date>=? and rec_date<?",
                (day0, day1)).fetchall()
        report(1 <= len(rows) <= 15,
               f'检查2 当日推荐记录总数 = {len(rows)}（班次加密后上界 15 = 3 班 × 5 人）')

        # 2a run_id 非空率（P2-④ ③回填）
        null_rid = [r[0] for r in rows if not (r[1] or '').strip()]
        report(not null_rid,
               f'检查2a run_id 非空率 {len(rows)-len(null_rid)}/{len(rows)}'
               + (f'，空行：{null_rid}' if null_rid else ''))

        # 2b 每 run_id 组行数 ∈ [1,5]（每班宁缺毋滥）
        by_run = {}
        for r in rows:
            by_run.setdefault((r[1] or '').strip() or '(空)', []).append(r)
        bad_run = {k: len(v) for k, v in by_run.items() if not 1 <= len(v) <= 5}
        report(not bad_run,
               f'检查2b per-run 行数全部 ∈ [1,5]（{len(by_run)} 个 run）'
               + (f'，越界：{bad_run}' if bad_run else ''))

        # 2c 逐班对拍：通知 canonical 段条目数 == 该 run 落库行数
        mism = []
        for run_id, cnt in section_entries:
            got = len(by_run.get(run_id, []))
            if got != cnt:
                mism.append(f'{run_id}: 段{cnt} vs 库{got}')
        report(not mism,
               f'检查2c 对拍 {len(section_entries)} 班（canonical 段人数 == 落库行数）'
               + (f'，不一致：{mism}' if mism else ''))

        # 检查3 matched_rules 合法 JSON 非空数组
        bad_rules = []
        for rec_id, _, mr, _ in rows:
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

        # 检查4 reason_text 非空且含数字
        bad_reason = [r[0] for r in rows
                      if not (r[3] or '').strip() or not re.search(r'\d', r[3])]
        report(not bad_reason,
               f'检查4 reason_text 全部非空且含数字'
               f'（{len(rows) - len(bad_reason)}/{len(rows)} 条合规）'
               + (f'，问题：{bad_reason}' if bad_reason else ''), warn_only=True)

    conn.close()
    print(f'── 结果：{"全部通过" if not failures else str(len(failures)) + " 项未过"} ──')
    return 0 if not failures else 1


if __name__ == '__main__':
    sys.exit(main())
