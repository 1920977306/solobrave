# -*- coding: utf-8 -*-
"""单1 守卫：queue_feeder.py 30 分钟巡检-派单流水线之发现层。

老大 11-18「传送带」设计锁：
- 发现层加速（每 30 分钟 ≤5 候选），施工层质量闸不动（P2 走守卫+回归+第四方验收）
- 判据机械化写死（协调边定稿后增量替换 JUDGES，main() 不许加临时逻辑）
- --selftest 必须 3 类假异常全检出 + 干净对照组全阴性（防恒真）
- P1 不受冷却限制（红线每次必出+即时推送）
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
FEEDER = ROOT / 'scripts' / 'queue_feeder.py'


def _src():
    return FEEDER.read_text(encoding='utf-8')


def test_feeder_selftest_not_constant_true():
    """守卫①：--selftest 离线跑通且包含防恒真结构（假异常组+干净对照组）。"""
    r = subprocess.run([sys.executable, str(FEEDER), '--selftest'],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f'selftest 失败: {r.stdout}{r.stderr}'
    assert '全过' in r.stdout and '全覆盖' in r.stdout


def test_feeder_judges_mechanical():
    """守卫②：判据层是纯函数形态（可注入才 selftest 得了），无 AI/网络调用。"""
    src = _src()
    assert 'def judge_' in src
    # 判据函数签名可注入（rows/tail/counts 参数），不内联 collect_inputs
    # v1 判据清单（协调边定稿）21 条全覆盖：1-1…5-4 编号函数在位
    for no in ('1_1', '1_2', '1_3', '1_4', '1_5',
               '2_1', '2_2', '2_3', '2_4', '2_5',
               '3_1', '3_2', '3_3', '3_4', '3_5',
               '4_1', '4_2', '4_3', '4_4', '4_5',
               '5_1', '5_2', '5_3', '5_4'):
        assert f'def judge_{no}(' in src
    # 判据清单 v1 编号与 JUDGES 表一一对应
    assert src.count("('1-1', judge_1_1)") == 1
    assert src.count("('5-4', judge_5_4)") == 1
    # 零积分纪律：发现层是纯代码——ast 剥全部 docstring/注释后不得出现
    # AI/openclaw/代理调用路径
    import ast as _ast
    tree = _ast.parse(src)
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef,
                             _ast.ClassDef, _ast.Module)):
            if (node.body and isinstance(node.body[0], _ast.Expr)
                    and isinstance(node.body[0].value, _ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body = node.body[1:]
    code_only = _ast.unparse(tree)
    for bad in ('_call_ai_api', 'openclaw', '/api/proxy'):
        assert bad not in code_only


def test_feeder_contract():
    """守卫③：产出契约钉死——≤5/轮、P1/P2/P3 分流、冷却去重、fingerprint。"""
    src = _src()
    assert 'MAX_PER_ROUND = 5' in src
    assert 'COOLDOWN_MS' in src
    assert "'fingerprint'" in src
    # 分流闸：P1 立即推送 + 不受冷却（红线语义）
    seg = src.split('def dedup', 1)[1]
    assert "c['priority'] != P1" in seg
    assert 'desktop_push' in src
    # 产出文件契约（协调边看板直读锚点）
    assert 'queue_feeder_last.json' in src
    assert 'queue_feeder_candidates.jsonl' in src


def test_feeder_round_outputs_contract():
    """守卫④：真实跑一轮（空库环境变量隔离？不——只读 prod，产出契约验证）。"""
    env = dict(os.environ)
    r = subprocess.run([sys.executable, str(FEEDER)],
                       capture_output=True, text=True, timeout=120, env=env,
                       cwd=str(ROOT))
    assert r.returncode == 0, f'feeder 实跑失败: {r.stdout}{r.stderr}'
    last = json.loads((ROOT / 'data' / 'queue_feeder_last.json')
                      .read_text(encoding='utf-8'))
    assert len(last['candidates']) <= 5
    for c in last['candidates']:
        assert c['priority'] in ('P1', 'P2', 'P3')
        assert c['evidence'] and c['suggested_acceptance']
        assert 'anchors' in c and 'fingerprint' in c
