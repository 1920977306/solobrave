# -*- coding: utf-8 -*-
"""AI 员工四层健康探针（2026-11-12 派单②）。

背景：11-11 晚 k3 路由丢 tool_use 的定位耗时两小时五轮，全程靠「换东西试」，
缺分层探针（教训已进 docs/PROGRESS-2026-11-10_12.md §7.2）。本脚本一个命令
跑完四层，每层输出 ✅/❌/⚠️ + 一行判据，下次链路问题 5 分钟定位到层。

四层定义（与派单原文一一对应）：
1. 模型层：最近 run 能否返回 tool_use —— 0 次 = k3 型「静默失败」签名
   （不报错、日志正常，表现为「AI 判断这次不用工具」，所有「AI 有没有真干活」
   的验证因此失效）
2. 工具层：工具能否被真实调用并返回 —— tool.result success 计数
3. 会话层：input tokens 余量 —— trace.artifacts usage.input 超阈值预警
   （参考事故：主会话 433,776 input tokens 撞 8192 输出上限，长任务直接断）
4. 投递层：通知/交付是否真落库 —— emp_duties.last_run_at 今日已刷新
   + 今日通知通道存在日报标识

铁律：全程只读（sqlite mode=ro），不改任何业务数据；断言失败只报告不修复。

用法：
  python3 scripts/agent_health_check.py                # 全部在岗员工，人类可读表格
  python3 scripts/agent_health_check.py --json         # 机器可读
  python3 scripts/agent_health_check.py --agent emp_xiaotuo,emp_talent_keeper

退出码：0 = 无 ❌；1 = 存在 ❌（可挂 cron/告警）。
"""
import argparse
import datetime
import json
import sqlite3
import sys
from pathlib import Path

PASS, FAIL, WARN, NA = 'pass', 'fail', 'warn', 'na'
_MARK = {PASS: '✅', FAIL: '❌', WARN: '⚠️', NA: '➖'}

DEFAULT_SESSION_WARN_INPUT = 200_000   # usage.input 超此值 = 接近上下文风险区
DEFAULT_MAX_RUN_AGE_HOURS = 30         # 最近 run 超此龄 = 「近期无运行」⚠️


# ── 基础工具 ──

def open_ro(path):
    """只读打开 sqlite（不存在会报错，调用方兜）。"""
    return sqlite3.connect(f'file:{Path(path)}?mode=ro', uri=True)


def day_start_ms(d=None):
    """本地日期 d（默认今天）零点的 epoch 毫秒。"""
    d = d or datetime.date.today()
    return int(datetime.datetime.combine(d, datetime.time.min).timestamp() * 1000)


def fmt_ts(ms):
    return datetime.datetime.fromtimestamp(ms / 1000).strftime('%m-%d %H:%M')


# ── 轨迹库读取（~/.openclaw/agents/<aid>/agent/openclaw-agent.sqlite） ──

def latest_run(con):
    """最近一个 run 的概要：run_id / 时间 / provider / modelId / tool.call 数。"""
    row = con.execute(
        "SELECT run_id, MAX(created_at) FROM trajectory_runtime_events"
        " WHERE run_id IS NOT NULL GROUP BY run_id"
        " ORDER BY MAX(created_at) DESC LIMIT 1").fetchone()
    if not row or not row[0]:
        return None
    run_id, ts = row
    n_events = con.execute(
        "SELECT COUNT(*) FROM trajectory_runtime_events WHERE run_id=?",
        (run_id,)).fetchone()[0]
    # provider/modelId 必须取同一事件（run 内可能发生 fallback 切换路由，
    # MAX() 跨事件聚合会把两条链的字段拼错位，曾把 minimax/MiniMax-M3
    # 误报成 minimax/k3）——取该 run 最后一个事件的值，即实际服务方。
    prov = con.execute(
        "SELECT json_extract(event_json,'$.provider'),"
        " json_extract(event_json,'$.modelId')"
        " FROM trajectory_runtime_events WHERE run_id=?"
        " ORDER BY created_at DESC, seq DESC LIMIT 1", (run_id,)).fetchone()
    tool_calls = con.execute(
        "SELECT COUNT(*) FROM trajectory_runtime_events"
        " WHERE run_id=? AND json_extract(event_json,'$.type')='tool.call'",
        (run_id,)).fetchone()[0]
    return {'run_id': run_id[:8], 'ts': ts, 'events': n_events,
            'tool_calls': tool_calls,
            'provider': (prov[0] if prov and prov[0] else '?'),
            'model': (prov[1] if prov and prov[1] else '?')}


def tool_results_24h(con, since_ms):
    """近 24h tool.result 成功/失败计数。"""
    rows = con.execute(
        "SELECT json_extract(event_json,'$.data.success')"
        " FROM trajectory_runtime_events"
        " WHERE json_extract(event_json,'$.type')='tool.result'"
        " AND created_at>=?", (since_ms,)).fetchall()
    ok = sum(1 for (s,) in rows if s)
    bad = len(rows) - ok
    return ok, bad


def latest_input_tokens(con):
    """最近 trace.artifacts 的 usage.input（无则 None）。"""
    row = con.execute(
        "SELECT json_extract(event_json,'$.data.usage.input')"
        " FROM trajectory_runtime_events"
        " WHERE json_extract(event_json,'$.type')='trace.artifacts'"
        " ORDER BY created_at DESC LIMIT 1").fetchone()
    return row[0] if row else None


# ── 四层判定（纯函数，输入连接，便于测试） ──

def check_model(con, now_ms, max_age_hours):
    """模型层：最近 run 有 tool.call = 能返回 tool_use；0 次 = 静默失败签名。"""
    run = latest_run(con)
    if run is None:
        return NA, '轨迹库无 run 记录'
    age_h = (now_ms - run['ts']) / 3_600_000
    route = f"{run['provider']}/{run['model']}"
    if run['tool_calls'] == 0:
        return FAIL, (f"最近 run {run['run_id']}（{fmt_ts(run['ts'])}，{route}）"
                      f" tool.call=0 —— 疑似静默失败（k3 案同款签名）")
    if age_h > max_age_hours:
        return WARN, (f"最近 run 在 {age_h:.0f} 小时前（{route}，"
                      f"tool.call={run['tool_calls']}），超 {max_age_hours}h 阈值")
    return PASS, f"最近 run {run['run_id']}（{fmt_ts(run['ts'])}，{route}）tool.call={run['tool_calls']}"


def check_tool(con, now_ms):
    """工具层：近 24h 有 tool.result 成功 = 工具真被调用并返回。"""
    ok, bad = tool_results_24h(con, now_ms - 24 * 3_600_000)
    if ok == 0 and bad == 0:
        return WARN, '近 24h 无 tool.result（今日未跑或工具层未触达）'
    if ok == 0:
        return FAIL, f'近 24h tool.result 全部失败（{bad} 次，无一次成功）'
    extra = f'，失败 {bad} 次' if bad else ''
    return PASS, f'近 24h tool.result 成功 {ok} 次{extra}'


def check_session(con, warn_input):
    """会话层：最近 usage.input 超阈值 = 接近上下文撞顶风险区。"""
    tokens = latest_input_tokens(con)
    if tokens is None:
        return NA, '无 usage 数据（trace.artifacts 缺 usage.input）'
    if tokens >= warn_input:
        return WARN, (f'input tokens={tokens:,} ≥ 阈值 {warn_input:,}'
                      f' —— 接近上下文风险区，长任务建议新会话键重跑')
    return PASS, f'input tokens={tokens:,}（阈值 {warn_input:,}）'


def check_delivery(db_con, agent_id, start_ms):
    """投递层：last_run_at 今日已刷新 + 今日通知通道有日报标识。"""
    row = db_con.execute(
        "SELECT last_run_at, last_run_summary FROM emp_duties WHERE agent_id=?",
        (agent_id,)).fetchone()
    if not row:
        return NA, 'emp_duties 无此员工'
    last_run_at, summary = row
    if not last_run_at or last_run_at < start_ms:
        return FAIL, f'last_run_at={fmt_ts(last_run_at) if last_run_at else "空"}，今日未交付'
    hit = db_con.execute(
        "SELECT COUNT(*) FROM notifications WHERE created_at>=?"
        " AND (agent_id=? OR title LIKE '%日报%' OR title LIKE '%深看%')",
        (start_ms, agent_id)).fetchone()[0]
    if hit == 0:
        return WARN, f'已交付（{fmt_ts(last_run_at)}）但今日通知通道无日报标识（可能投递到别处）'
    s = (summary or '')[:40]
    return PASS, f'last_run_at={fmt_ts(last_run_at)}，今日日报通知 {hit} 条' + (f'；{s}' if s else '')


# ── 汇总入口 ──

def run_checks(db_path, traj_dir, agents=None, day_start=None,
               warn_input=DEFAULT_SESSION_WARN_INPUT,
               max_age_hours=DEFAULT_MAX_RUN_AGE_HOURS, now_ms=None):
    now_ms = now_ms or int(datetime.datetime.now().timestamp() * 1000)
    start_ms = day_start_ms(day_start)
    db_con = open_ro(db_path)
    if agents is None:
        agents = [r[0] for r in db_con.execute(
            "SELECT agent_id FROM emp_duties WHERE duty_name!='' ORDER BY agent_id")]
    out = []
    for aid in agents:
        rec = {'agent_id': aid}
        traj = Path(traj_dir) / aid / 'agent' / 'openclaw-agent.sqlite'
        if traj.exists():
            con = open_ro(traj)
            try:
                rec['model'] = check_model(con, now_ms, max_age_hours)
                rec['tool'] = check_tool(con, now_ms)
                rec['session'] = check_session(con, warn_input)
            finally:
                con.close()
        else:
            na = (NA, f'轨迹库不存在：{traj}')
            rec['model'] = rec['tool'] = rec['session'] = na
        rec['delivery'] = check_delivery(db_con, aid, start_ms)
        out.append(rec)
    db_con.close()
    return out


def render_table(results):
    lines = [f"{'员工':<22}{'模型层':<6}{'工具层':<6}{'会话层':<6}{'投递层':<6}判据"]
    lines.append('-' * 100)
    for r in results:
        lines.append(f"{r['agent_id']:<22}"
                     f"{_MARK[r['model'][0]]:<6}{_MARK[r['tool'][0]]:<6}"
                     f"{_MARK[r['session'][0]]:<6}{_MARK[r['delivery'][0]]:<6}")
        for layer in ('model', 'tool', 'session', 'delivery'):
            lines.append(f"  {'└' if layer == 'delivery' else '├'} {layer:<8} {r[layer][1]}")
    return '\n'.join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description='AI 员工四层健康探针（只读）')
    ap_con = Path(__file__).resolve().parent.parent
    ap.add_argument('--db', default=str(ap_con / 'data' / 'solobrave.db'))
    ap.add_argument('--traj-dir', default=str(Path.home() / '.openclaw' / 'agents'))
    ap.add_argument('--agent', help='逗号分隔的 agent_id 列表（默认：emp_duties 全部在岗员工）')
    ap.add_argument('--json', action='store_true', help='机器可读输出')
    ap.add_argument('--session-warn-input', type=int, default=DEFAULT_SESSION_WARN_INPUT)
    ap.add_argument('--max-run-age-hours', type=float, default=DEFAULT_MAX_RUN_AGE_HOURS)
    args = ap.parse_args(argv)
    agents = args.agent.split(',') if args.agent else None
    results = run_checks(args.db, args.traj_dir, agents,
                         warn_input=args.session_warn_input,
                         max_age_hours=args.max_run_age_hours)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=1))
    else:
        print(render_table(results))
    has_fail = any(FAIL in (r['model'][0], r['tool'][0], r['session'][0], r['delivery'][0])
                   for r in results)
    return 1 if has_fail else 0


if __name__ == '__main__':
    sys.exit(main())
