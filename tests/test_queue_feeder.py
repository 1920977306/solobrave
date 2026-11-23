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


def test_feeder_archived_agents_excluded_from_balance_redline():
    """守卫⑤（老大 11-18 18:29）：挂起员工（knowledge_admin 首例）余额恒 0，
    5-1 必须排除——否则 P1 不受冷却，每 30 分钟直推老大桌面 = 告警疲劳。"""
    import importlib.util as _il
    spec = _il.spec_from_file_location('queue_feeder', FEEDER)
    qf = _il.module_from_spec(spec)
    spec.loader.exec_module(qf)
    tmp = ROOT / 'data' / '.tmp_archived_test.json'
    tmp.write_text(json.dumps([
        {'id': 'emp_active', 'archived': False},
        {'id': 'emp_arch', 'archived': True},
        {'id': 'emp_no_flag'},          # 无字段 = 在岗
    ]), encoding='utf-8')
    try:
        got = qf.load_archived_agent_ids(str(tmp))
        assert got == {'emp_arch'}, f'归档集合错误: {got}'
        # 空文件/坏 JSON ⇒ fail-open 空集（不排除，宁可多报）
        bad = ROOT / 'data' / '.tmp_archived_bad.json'
        bad.write_text('not json', encoding='utf-8')
        assert qf.load_archived_agent_ids(str(bad)) == set()
        missing = qf.load_archived_agent_ids(str(ROOT / 'data' / '.nope.json'))
        assert missing == set()
    finally:
        tmp.unlink(missing_ok=True)
        (ROOT / 'data' / '.tmp_archived_bad.json').unlink(missing_ok=True)
    # collect_inputs 确实接上了排除（源码锚：bal_low 过滤 archived）
    src = _src()
    seg = src.split('bal_low =', 1)[1].split('burn_today', 1)[0]
    assert 'not in archived' in seg and 'load_archived_agent_ids()' in src


def test_feeder_4_3_pause_lock():
    """守卫⑥（11-20 交付管道迁移止血）：4-3（B 表当日 0 推荐）整条暂停。

    班级交付已切即时班管道（talents + square_judgment），B 表停更期间 4-3 必出假旗
    （11-20 17:47 轮实证）。恢复 = P2 迁移单完成 + 判据改锚 talents.created_at，
    两处同时改；单解常量本测试必红（验收网铁律：判据强度一个不许松）。"""
    import importlib.util as _il
    spec = _il.spec_from_file_location('queue_feeder', FEEDER)
    qf = _il.module_from_spec(spec)
    spec.loader.exec_module(qf)
    assert qf.JUDGE_4_3_PAUSED is True, \
        '4-3 暂停锁被解除：恢复须迁移单完成 + 改锚 talents（判据强度不许松）'
    # 正例条件全部成立（工作日 19 点、B 表 0 行）也必须空——假旗不得复现
    assert qf.judge_4_3(19, 0, True) == []
    # 解除路径的负向锁：pause 常量翻 False 时正例必须能出单（防止有人把暂停
    # 做成恒空死代码——管道恢复后 4-3 必须能重新咬合）
    qf.JUDGE_4_3_PAUSED = False
    try:
        out = qf.judge_4_3(19, 0, True)
        assert out and out[0]['judge'] == '4-3'
        assert qf.judge_4_3(19, 3, True) == []   # 负例语义不随暂停损坏
        assert qf.judge_4_3(19, 0, False) == []  # 非工作日不出
    finally:
        qf.JUDGE_4_3_PAUSED = True


def test_feeder_4_1_dual_anchor():
    """守卫⑦（单3 2026-11-23，dq_fe64e2825cda）：4-1 改双锚——
    MAX(talent_collects 最后成功, talents 最后写入) > 48h 才报。

    实证背景：square-scan 直写 talents 不经过 talent_collects，周四距 weekly
    采集窗 87h 是设计态（11-20 判误报实证）——单锚必假旗，判据锚错了表。"""
    import importlib.util as _il
    spec = _il.spec_from_file_location('queue_feeder', FEEDER)
    qf = _il.module_from_spec(spec)
    spec.loader.exec_module(qf)
    now = 1_800_000_000_000
    H = 3600_000
    # ① 设计态复现：collects 停摆 87h、talents 4h 前有写入（square-scan 直写）
    #    ⇒ 双锚取 talents，age=4 ⇒ 不出 4-1（当初 11-20 的误报形态被锁死）
    age = qf.dual_anchor_age(now, now - 87 * H, now - 4 * H)
    assert age == 4 and qf.judge_4_1(age) == []
    # ② 周三（weekly 窗内）talents 有写入但距窗尾 >48h 的边界：仍不出
    age = qf.dual_anchor_age(now, now - 90 * H, now - 50 * H)
    assert age == 50, '取较新锚'
    # ③ 真停摆：两锚都 >48h ⇒ 出单
    age = qf.dual_anchor_age(now, now - 100 * H, now - 63 * H)
    assert qf.judge_4_1(age), '双锚停摆必须出 4-1'
    # ④ 宁缺不编造：两锚全空 ⇒ None ⇒ 不出
    assert qf.dual_anchor_age(now, None, None) is None
    assert qf.dual_anchor_age(now, 0, None) is None
    # ⑤ 边界 48h 本身不报（>48 才报，与旧语义一致）
    assert qf.judge_4_1(48) == []
    assert qf.judge_4_1(49), '49h 必须报'
    # ⑥ scan 主流程确实接了双锚（源码锚：不再出现单锚 c_age 算法）
    src = _src()
    assert 'c_age = dual_anchor_age(now_ms, c_last, t_last)' in src, \
        '主流程必须调用双锚合成，回退单锚即打回'
    assert 'MAX(updated_at) FROM talents' in src
