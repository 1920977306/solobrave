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
   主动型/被动型分野（老大 2026-11-12 裁决）：只有 launchd 有调度的主动型岗位
   才查投递层；@驱动的被动型岗位无日调度，投递层豁免（➖），不再误报 ❌。
   判据写进输出头；判定事实源 = LaunchAgents 目录扫描，不是任何手维护名单。

5. 资源层（老大 11-12 增量）：余额 < 2×近 7 日日均消耗 → ⚠️。
   阈值按 credit_usage_log 实算，不写死——这层今天已两次靠人肉发现
   （goods 0 分、xiaotuo 92 分），该机械化的就机械化。

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
import plistlib
import re
import sqlite3
import sys
from pathlib import Path

PASS, FAIL, WARN, NA = 'pass', 'fail', 'warn', 'na'
_MARK = {PASS: '✅', FAIL: '❌', WARN: '⚠️', NA: '➖'}

DEFAULT_SESSION_WARN_INPUT = 200_000   # usage.input 超此值 = 接近上下文风险区
DEFAULT_MAX_RUN_AGE_HOURS = 30         # 最近 run 超此龄 = 「近期无运行」⚠️
DEFAULT_LAUNCH_AGENTS_DIR = str(Path.home() / 'Library' / 'LaunchAgents')


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


# ── 主动型判定（调度事实源 = launchd plist） ──

def scheduled_agents(launch_agents_dir=DEFAULT_LAUNCH_AGENTS_DIR):
    """扫 LaunchAgents 目录，判定每个 agent 是否「有调度（主动型）」。
    这是调度事实的唯一可信源（emp_duties 里没有调度字段）。
    两种 plist 形态都要认：
      ① `... --agent <id>`（keeper_daily.py 形态）
      ② 跑日报脚本无参（xiaotuo_daily.py 形态）——目标 agent 硬编码在
         脚本的 `AGENT_ID = '<id>'` 里，解析脚本源码提取，不维护名单
    返回 {agent_id: [plist 文件名]}。"""
    out = {}
    d = Path(launch_agents_dir)
    if not d.is_dir():
        return out
    for p in d.glob('*.plist'):
        try:
            with open(p, 'rb') as f:
                obj = plistlib.load(f)
        except Exception:
            continue
        args = [str(a) for a in (obj.get('ProgramArguments') or [])]
        if '--agent' in args:
            i = args.index('--agent')
            if i + 1 < len(args) and args[i + 1]:
                out.setdefault(args[i + 1], []).append(p.name)
        for tok in args:
            if tok.endswith('_daily.py') and Path(tok).is_file():
                aid = _script_agent_id(Path(tok))
                if aid:
                    out.setdefault(aid, []).append(p.name)
    return out


def _script_agent_id(script_path):
    """从日报闹钟脚本源码提取硬编码 AGENT_ID = '<id>'；无则 None。"""
    try:
        src = Path(script_path).read_text(encoding='utf-8', errors='replace')
    except Exception:
        return None
    import re
    m = re.search(r"^AGENT_ID\s*=\s*['\"]([^'\"]+)['\"]", src, re.M)
    return m.group(1) if m else None


# ── 资源层（积分余量，老大 11-12：余额 < 2×近 7 日日均消耗 → ⚠️） ──

def check_credits(db_con, agent_id, now_ms, window_days=7):
    """余额 < 2×近 window_days 日日均消耗 → WARN。阈值全部实算，不写死。

    口径（老大拍板）：日均 = 近 7 日 credits_used 总和 / 7（不管有没有跑满
    7 天——消耗加速时日均跟着涨，预警只会更早）。无消耗记录且余额为 0
    → WARN「上岗即欠费」（knowledge_admin 案同款：上岗前必须先充）。
    credit_usage_log.created_at 是 TEXT（datetime localtime 格式）。"""
    row = db_con.execute(
        "SELECT balance FROM credit_accounts WHERE agent_id=?", (agent_id,)).fetchone()
    if not row:
        return NA, 'credit_accounts 无此员工账户'
    balance = row[0] or 0
    since = datetime.datetime.fromtimestamp(
        now_ms / 1000 - window_days * 86400).strftime('%Y-%m-%d %H:%M:%S')
    used = db_con.execute(
        "SELECT COALESCE(SUM(credits_used),0) FROM credit_usage_log"
        " WHERE agent_id=? AND created_at>=?", (agent_id, since)).fetchone()[0]
    daily = used / window_days
    if used == 0 and balance == 0:
        return WARN, ('近 7 日无消耗记录且余额为 0 —— 上岗即欠费，'
                      '排班前必须先充值（老大 11-12 上岗检查单）')
    if daily > 0 and balance < 2 * daily:
        return WARN, (f'余额 {balance} < 2×近 7 日日均消耗（{daily:.0f}×2'
                      f'={2*daily:.0f}）—— 撑不到两天')
    basis = f'余额 {balance}，近 7 日日均消耗 {daily:.0f}'
    return (PASS if used > 0 else NA), basis


# ── 汇总入口 ──

def run_checks(db_path, traj_dir, agents=None, day_start=None,
               warn_input=DEFAULT_SESSION_WARN_INPUT,
               max_age_hours=DEFAULT_MAX_RUN_AGE_HOURS, now_ms=None,
               launch_agents_dir=DEFAULT_LAUNCH_AGENTS_DIR):
    now_ms = now_ms or int(datetime.datetime.now().timestamp() * 1000)
    start_ms = day_start_ms(day_start)
    db_con = open_ro(db_path)
    if agents is None:
        # 在岗 = 有职责名且非「循环设计中」（designing 是老大 11-12 裁决的
        # 摘岗标：名片保留，但探针不把它当在岗员工要日报）
        agents = [r[0] for r in db_con.execute(
            "SELECT agent_id FROM emp_duties"
            " WHERE duty_name!='' AND duty_status!='designing'"
            " ORDER BY agent_id")]
    sched = scheduled_agents(launch_agents_dir)
    out = []
    for aid in agents:
        rec = {'agent_id': aid, 'scheduled': aid in sched}
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
        if rec['scheduled']:
            rec['delivery'] = check_delivery(db_con, aid, start_ms)
        else:
            # 被动型（@驱动）岗位没有日调度，投递层无可判 ⇒ 豁免（老大 11-12 裁决）
            rec['delivery'] = (NA,
                               f'被动型（无 launchd 调度，投递层豁免；老大 11-12 裁决）')
        rec['credits'] = check_credits(db_con, aid, now_ms)
        out.append(rec)
    db_con.close()
    return out


# ── 第六层「产物对拍」（老大 11-13 规格：锁所有数字，不只撞名） ──
# 性质与前五层不同：前五层 = 运行时健康；本层 = 日报产物 vs 工具实数。
# 失败模式（11-12 塌缩事故）：「摘要数字 ≠ 工具实数」——对日报里每个数成立。
# 输出必须与运行时分列标注，别把产物问题混进运行时结论。

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _keeper_ops():
    try:
        sys.path.insert(0, str(_REPO_ROOT))
        import tools.keeper_ops as ko
        return ko
    except Exception:
        return None


def today_report_text(db_con, agent_id, start_ms):
    """今日产物文本 = 职责行 last_run_summary + 今日通知通道里的日报内容。"""
    parts = []
    row = db_con.execute(
        "SELECT last_run_summary FROM emp_duties WHERE agent_id=?",
        (agent_id,)).fetchone()
    if row and row[0]:
        parts.append(str(row[0]))
    for (title, content) in db_con.execute(
            "SELECT title, content FROM notifications"
            " WHERE created_at>=? AND (agent_id=? OR title LIKE '%日报%'"
            " OR title LIKE '%深看%') ORDER BY created_at DESC LIMIT 5",
            (start_ms, agent_id)):
        for t in (title, content):
            if t:
                parts.append(str(t))
    return '\n'.join(parts)


_NUM = lambda pat, text: (int(m.group(1)) if (m := re.search(pat, text)) else None)


def _num_or_warn(diffs, label, reported, actual):
    """单个数字对拍：缺失记 diff（缺失即塌缩签名——宽松口径必须存在）。"""
    if reported is None:
        diffs.append(f'{label}：日报未报该数字（缺失即塌缩签名）')
    elif reported != actual:
        diffs.append(f'{label}：日报报 {reported} vs 工具实数 {actual}')


def check_product_talent(db_con, report_text, health, relations_rows):
    """达人库管家产物对拍。返回 (status, basis)。"""
    if not report_text.strip():
        return NA, '今日无日报产物文本'
    diffs = []
    _num_or_warn(diffs, '严格口径', _NUM(r'严格口径\s*(\d+)', report_text),
                  len(health['dup_maybe']))
    _num_or_warn(diffs, '宽松口径', _NUM(r'宽松口径\s*(\d+)', report_text),
                  len(health['dup_loose']))
    _num_or_warn(diffs, '确定重复', _NUM(r'确定重复\s*(\d+)', report_text),
                  len(health['dup_sure']))
    _num_or_warn(diffs, '字段缺口', _NUM(r'字段缺口\s*(\d+)', report_text),
                  len(health['gaps']))
    _num_or_warn(diffs, '过期标记', _NUM(r'过期标记\s*(\d+)', report_text),
                  len(health['stale']))
    shells_n = _NUM(r'空壳候选\s*(\d+)', report_text)
    if shells_n is not None:   # 第六行（老大 11-13 新格式）报了才比
        _num_or_warn(diffs, '空壳候选', shells_n, len(health['shells']))
    # 已标组覆盖：labeled=same_team/unknown 的组必须整组出现在日报里
    # （明赫三撞是其中一例，不是唯一——老大 11-13：别做专用守卫）
    labeled = [r for r in relations_rows
               if r[2] in ('same_team', 'unknown')]
    if labeled:
        names_by_id = {str(tid): str(nm or '').strip()
                       for (tid, nm) in db_con.execute(
                           'SELECT id, name FROM talents').fetchall()}
        covered, missing = 0, []
        for a, b, label in [(r[0], r[1], r[2]) for r in labeled]:
            na, nb = names_by_id.get(a, ''), names_by_id.get(b, '')
            if na and nb and na in report_text and nb in report_text:
                covered += 1
            else:
                missing.append(f'{na or a}×{nb or b}({label})')
        if missing:
            diffs.append(f'已标组缺组：{len(missing)}/{len(labeled)} 组未整组'
                         f'出现在日报：{("; ".join(missing))[:200]}')
        else:
            pass
    if diffs:
        return WARN, '；'.join(diffs)
    return PASS, (f'三口径+缺口/过期数全对拍一致；已标组 {covered}/{len(labeled)}'
                  f' 整组覆盖' if labeled else '三口径+缺口/过期数全对拍一致')


def check_product_goods(db_con, report_text, health):
    if not report_text.strip():
        return NA, '今日无日报产物文本'
    diffs = []
    _num_or_warn(diffs, '佣金过期', _NUM(r'佣金过期\s*(\d+)', report_text),
                  len(health['commission_stale']))
    _num_or_warn(diffs, '字段缺口', _NUM(r'字段缺口\s*(\d+)', report_text),
                  len(health['gaps']))
    _num_or_warn(diffs, '疑似下架', _NUM(r'疑似下架\s*(\d+)', report_text),
                  len(health['suspect_off']))
    if diffs:
        return WARN, '；'.join(diffs)
    return PASS, '佣金过期/字段缺口/疑似下架三数全对拍一致'


def run_product_checks(db_path, agents, day_start=None, now_ms=None):
    """产物对拍独立入口（与运行时分列）。agents 限定 maintain 岗。"""
    ko = _keeper_ops()
    if ko is None:
        return [{'agent_id': a, 'product': (NA, 'tools.keeper_ops 不可导入')}
                for a in agents]
    now_ms = now_ms or int(datetime.datetime.now().timestamp() * 1000)
    start_ms = day_start_ms(day_start)
    db_con = open_ro(db_path)
    db_con.row_factory = sqlite3.Row   # keeper_ops 内部 dict(row) 依赖 Row
    out = []
    try:
        for aid in agents:
            rec = {'agent_id': aid}
            try:
                if aid == 'emp_talent_keeper':
                    health = ko.talent_healthcheck(db_con)
                    rel_rows = [tuple(r) for r in db_con.execute(
                        'SELECT a_talent_id, b_talent_id, relation_label'
                        ' FROM talent_relations')]
                    text = today_report_text(db_con, aid, start_ms)
                    rec['product'] = check_product_talent(
                        db_con, text, health, rel_rows)
                elif aid == 'emp_goods_keeper':
                    health = ko.goods_healthcheck(db_con)
                    text = today_report_text(db_con, aid, start_ms)
                    rec['product'] = check_product_goods(db_con, text, health)
                else:
                    rec['product'] = (NA, '产物对拍暂未覆盖该岗位')
            except Exception as e:
                rec['product'] = (NA, f'对拍异常（只读）: {e}')
            out.append(rec)
    finally:
        db_con.close()
    return out


def render_product(results):
    lines = ['产物对拍（与运行时分列；性质不同：这是「日报写的」vs「工具算的」）']
    for r in results:
        lines.append(f"{r['agent_id']:<22}{_MARK[r['product'][0]]:<6}{r['product'][1]}")
    return '\n'.join(lines)


def render_table(results):
    lines = ['判据：主动型 = launchd 有调度（查投递层）；被动型（@驱动）投递层豁免'
             ' —— 老大 2026-11-12 裁决']
    lines.append(f"{'员工':<22}{'型':<6}{'模型':<6}{'工具':<6}{'会话':<6}{'投递':<6}{'资源':<6}")
    lines.append('-' * 100)
    for r in results:
        mode = '主动' if r['scheduled'] else '被动'
        lines.append(f"{r['agent_id']:<22}{mode:<6}"
                     f"{_MARK[r['model'][0]]:<6}{_MARK[r['tool'][0]]:<6}"
                     f"{_MARK[r['session'][0]]:<6}{_MARK[r['delivery'][0]]:<6}"
                     f"{_MARK[r['credits'][0]]:<6}")
        for layer in ('model', 'tool', 'session', 'delivery', 'credits'):
            lines.append(f"  {'└' if layer == 'credits' else '├'} {layer:<8} {r[layer][1]}")
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
    ap.add_argument('--launch-agents-dir', default=DEFAULT_LAUNCH_AGENTS_DIR,
                    help='launchd plist 目录（判主动/被动型的事实源）')
    ap.add_argument('--no-product', action='store_true',
                    help='跳过第六层产物对拍（与运行时分列，不影响退出码）')
    args = ap.parse_args(argv)
    agents = args.agent.split(',') if args.agent else None
    results = run_checks(args.db, args.traj_dir, agents,
                         warn_input=args.session_warn_input,
                         max_age_hours=args.max_run_age_hours,
                         launch_agents_dir=args.launch_agents_dir)
    product = [] if args.no_product else run_product_checks(
        args.db, agents if agents else
        [r['agent_id'] for r in results if r['agent_id'].endswith('_keeper')],
        now_ms=int(datetime.datetime.now().timestamp() * 1000))
    if args.json:
        print(json.dumps({'runtime': results, 'product': product},
                         ensure_ascii=False, indent=1))
    else:
        print(render_table(results))
        if product:
            print()
            print(render_product(product))
    has_fail = any(FAIL in (r['model'][0], r['tool'][0], r['session'][0], r['delivery'][0])
                   for r in results)
    return 1 if has_fail else 0


if __name__ == '__main__':
    sys.exit(main())
