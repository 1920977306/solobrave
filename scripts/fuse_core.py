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
VERDICT_RECENT_MS = 6 * 60 * 60_000  # 验卷回溯窗：今天的班都在窗内，历史班不追诉

# 三岗配置：交付锚点 = 任务书点名的「交卷」命令在 tool.call 参数里的特征串
# （轨迹实测：交付走 exec，工具名是通用的 exec/read/write——按名字匹配必漏，
#  必须按 args 文本锚点判。11-16 今晨班 seq60 = python3 tools/xiaotuo_ops.py report）
AGENTS = {
    'emp_xiaotuo': {
        'label': '小拓',
        'anchors': ['xiaotuo_ops.py report'],
    },
    'emp_talent_keeper': {
        'label': '达人库管家',
        'anchors': ['keeper_ops.py', 'square-report'],
    },
    'emp_goods_keeper': {
        'label': '商品库管家',
        'anchors': ['keeper_ops.py', 'square-report'],
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


def detect_delivery_events(anchors, events):
    """验卷：该 run 的 tool.call 参数文本里是否出现任一交付锚点。

    events = [(name, blob), ...]，blob = 参数序列化文本（json.dumps 或原字符串）。
    钉死 11-13 教训：turn success ≠ 交付；判据只看交付命令真跑过没有
    （11-16 实证：交付走 exec 通用名，锚点在 args.command 文本里）。
    """
    for name, blob in events:
        if not blob:
            continue
        for a in anchors:
            if a in blob:
                return True
    return False


def load_tool_calls(agent_id, run_id, db_path=None):
    """取某 run 全部 (name, blob)：验卷的锚点判据用。

    实测轨迹 data 里参数字段叫 args（不叫 arguments）——11-16 首部署即踩，
    两个键都收，blob 为序列化文本。
    """
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
        args = data.get('args')
        if args is None:
            args = data.get('arguments')
        if isinstance(args, str):
            blob = args
        elif args is None:
            blob = ''
        else:
            blob = json.dumps(args, ensure_ascii=False)
        out.append((data.get('name') or '', blob))
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
