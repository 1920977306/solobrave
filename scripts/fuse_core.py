#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""熔断/验卷核心逻辑（P1-⑦，2026-11-16）——纯函数层，无副作用（只读轨迹库）。

判据口径（§145 终稿 + §147 严格版，两条熔断腿、无时长腿、无积分腿）：
  calls = COUNT(type='tool.call') 按 run 分组（run_id 列，权威）
  search = 其中 data.name == 'tool_search'
  熔断触发：calls > 100，或 (share > 0.40 且 calls > 30)

轨迹库实测形态（2026-11-16）：event_json 是**合法 JSON**（双引号），
ast.literal_eval 遇到 true/false/null 会炸——所以 json.loads 优先、ast 兜底
（与派单规格的「ast 优先」相反，两种都收，谁行用谁）。
"""
import ast
import json
import re
import sqlite3
from collections import defaultdict

COUNT_LEG = 100          # calls > 100 → 次数腿触发
SHARE_LEG = 0.40         # share > 40%
SHARE_MIN_CALLS = 30     # 且 calls > 30 → 占比腿触发
HEARTBEAT_SILENT_MS = 480_000    # 最后事件静默 > 8 分钟 → 心跳告警（不中止）
DELIVERY_QUIET_MS = 30 * 60_000  # run 静默 > 30 分钟 → 具备验卷条件
RUN_STALE_MS = 60 * 60_000       # run 最后事件超过 1 小时 → 不再视为活跃

# 三岗配置：交付工具 = 任务书点名的「交卷」工具（验卷判据）
AGENTS = {
    'emp_xiaotuo': {
        'label': '小拓',
        # 交付 = xiaotuo_ops 工具以 report 子命令调用（--rec 可选）
        'delivery': [{'name': 'xiaotuo_ops', 'arg_contains': 'report'}],
    },
    'emp_talent_keeper': {
        'label': '达人库管家',
        'delivery': [{'name': 'keeper_ops', 'arg_contains': None}],
    },
    'emp_goods_keeper': {
        'label': '商品库管家',
        'delivery': [{'name': 'keeper_ops', 'arg_contains': None}],
    },
}

TRAJ_DB = ('file:/Users/qichen/.openclaw/agents/{agent_id}/agent/'
           'openclaw-agent.sqlite?mode=ro')


def parse_event(event_json):
    """event_json → dict；JSON 优先，Python 字面量兜底，都失败返回 {}。"""
    if not event_json:
        return {}
    try:
        d = json.loads(event_json)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    try:
        d = ast.literal_eval(event_json)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def load_runs(agent_id, db_path=None, now_ms=None):
    """读某员工轨迹库 → {run_id: {calls, search, last_ts, ended}}。

    ended = 该 run 出现过 type='session.ended'。静默 run（无此事件）保持 ended=False
    ——这正是心跳看门要抓的形态。
    """
    path = db_path or TRAJ_DB.format(agent_id=agent_id)
    db = sqlite3.connect(path, uri=path.startswith('file:'))
    try:
        rows = db.execute(
            'SELECT run_id, event_json, created_at FROM trajectory_runtime_events'
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        db.close()
    runs = defaultdict(lambda: {'calls': 0, 'search': 0, 'last_ts': 0,
                                'ended': False, 'tool_names': []})
    for rid, ej, cat in rows:
        if not rid:
            continue
        r = runs[rid]
        if cat and cat > r['last_ts']:
            r['last_ts'] = cat
        d = parse_event(ej)
        etype = d.get('type')
        if etype == 'session.ended':
            r['ended'] = True
            continue
        if etype == 'tool.call':
            r['calls'] += 1
            data = d.get('data') or {}
            name = data.get('name') or ''
            r['tool_names'].append(name)
            if name == 'tool_search':
                r['search'] += 1
    return dict(runs)


def verdict(calls, search):
    """复合判据 → (放行?, 理由)。纯函数，回放与活机共用同一口径。"""
    share = search / calls if calls else 0.0
    if calls > COUNT_LEG:
        return False, f'次数腿({calls}>{COUNT_LEG})'
    if calls > SHARE_MIN_CALLS and share > SHARE_LEG:
        return False, f'占比腿({search}/{calls}={share:.0%}>{SHARE_LEG:.0%})'
    return True, f'放行({calls}次,search占比{share:.0%})'


def detect_delivery_events(delivery_wants, events):
    """带参数版验卷：events = [(name, args_dict_or_str), ...]。

    delivery_wants = 岗位配置里的交付工具清单（不再按 agent_id 反查全局表，
    看门狗扫描时配置随岗传入——测试/新岗接入都不依赖写死全局）。
    xiaotuo 的交付形态是 xiaotuo_ops + report 子命令；管家是 keeper_ops 体检输出。
    """
    for want in delivery_wants:
        for name, args in events:
            if want['name'] not in (name or ''):
                continue
            if want['arg_contains'] is None:
                return True
            blob = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
            if want['arg_contains'] in blob:
                return True
    return False


def load_tool_calls(agent_id, run_id, db_path=None):
    """取某 run 全部 (name, args)：验卷的参数级判据用。"""
    path = db_path or TRAJ_DB.format(agent_id=agent_id)
    db = sqlite3.connect(path, uri=path.startswith('file:'))
    try:
        rows = db.execute(
            'SELECT event_json FROM trajectory_runtime_events WHERE run_id = ?',
            (run_id,)).fetchall()
    except sqlite3.Error:
        return []
    finally:
        db.close()
    out = []
    for (ej,) in rows:
        d = parse_event(ej)
        if d.get('type') != 'tool.call':
            continue
        data = d.get('data') or {}
        out.append((data.get('name') or '', data.get('arguments')))
    return out


# ─── 进程定位（熔断中止的执行面）─────────────────────────────────────────────

def parse_openclaw_pids(ps_output, agent_id):
    """从 `ps -eo pid,args` 输出里挑出某员工 openclaw CLI 的 PID。

    匹配铁律：args 同时含 openclaw、'agent'、'--agent <agent_id>'——
    三条件缺一不可，防误杀其他员工/其他 node 进程。
    """
    pids = []
    pat = re.compile(r'--agent\s+' + re.escape(agent_id) + r'(\s|$)')
    for line in ps_output.splitlines():
        line = line.strip()
        if not line or 'openclaw' not in line:
            continue
        if ' agent ' not in line and not line.endswith(' agent'):
            continue
        if not pat.search(line):
            continue
        m = re.match(r'^(\d+)\s', line)
        if m:
            pids.append(int(m.group(1)))
    return pids


# ─── 基线回放样本（HEALTH-BASELINE.md 七行 + 今晨班）────────────────────────

BASELINE_SAMPLES = [
    # (run前缀, calls, search, 期望放行, 备注)
    ('8fd9fa77', 45, 0, True, '11-11 健康班（minimax 直连）'),
    ('10:47keeper', 59, 3, True, '11-14 达人库管家（贴线）'),
    ('11:17keeper', 85, 2, True, '11-14 商品库管家（完美班）'),
    ('2eca23af', 109, 41, False, '11-13 09:30 没交卷（次数腿兜底）'),
    ('3aff1c5f', 334, 147, False, '11-13 12:21 漫游（占比腿双中）'),
    ('cf2304f3', 51, 0, True, '11-13 夜验班 minimax fresh'),
    ('8ca29af4', 36, 1, True, '11-16 今晨班（人工放过，必须放行）'),
]
