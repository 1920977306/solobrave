# -*- coding: utf-8 -*-
"""emp_duties 职责链守卫（A 简报，2026-11-10）。

验收①：改职责 ⇒ systemPrompt 真的跟着变（职能不是纯展示标签）⇒ SOUL.md 同步。
测试矩阵：
1. 拼装器结构——按老大简报 A 模板逐段断言（ duty → prompt ）
2. 改职责 → prompt 变（同一员工改 quota/duty_desc，输出必须变化）
3. quota 硬顶 5（_save_duty 收口）
4. _save_duty/_load_duty upsert 往返（tmp 库，零 prod 接触）
5. _apply_duty_to_agent 全链路：agents.json systemPrompt 更新 + SOUL.md 落盘
6. 负向对照：无职责行 → _apply_duty_to_agent 返回 None（探针必须能「红」）

数据全落 tmp（importlib 独立模块实例 + 全路径 monkeypatch），openclaw.json 用
monkeypatch 替身（绝不写真 ~/.openclaw/openclaw.json）。
运行：.venv-test/bin/python -m pytest tests/test_emp_duties.py -v
"""
import importlib.util
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_spec = importlib.util.spec_from_file_location(
    'solobrave_server', os.path.join(ROOT, 'solobrave-server.py'))
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)


XIAOTUO_DUTY = {
    'duty_name': '达人拓展专员',
    'scope': '鞋靴穿搭类目达人',
    'category': '鞋靴箱包',
    'quota': 5,
    'goods': 'Coolchap 女鞋（客单 50-200 元带）+ 生活家居',
    'duty_desc': [
        '每天从达人广场筛 5 个候选达人，逐个看详情',
        '按判定标准给出「排除/存疑/值得联系 + 一句话理由」',
        '合格的按完整字段模板录入达人库（抖音号去重，缺一不入库）',
        '收工出日报：新增/排除原因分布/建议人工过目名单',
    ],
    'supervisor': '商务主管',
}


@pytest.fixture()
def mod(tmp_path, monkeypatch):
    """独立模块实例 + 全部数据路径指 tmp + openclaw entries 替身，init_db 建全量 schema。"""
    monkeypatch.setattr(srv, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(srv, 'DB_PATH', str(tmp_path / 'solobrave.db'))
    monkeypatch.setattr(srv, 'AGENTS_FILE', str(tmp_path / 'agents.json'))
    monkeypatch.setattr(srv, 'USERS_FILE', str(tmp_path / 'users.json'))
    monkeypatch.setattr(srv, 'PERMISSIONS_FILE', str(tmp_path / 'permissions.json'))
    monkeypatch.setattr(srv, 'GROUPS_FILE', str(tmp_path / 'groups.json'))
    monkeypatch.setattr(srv, 'TEAMS_FILE', str(tmp_path / 'teams.json'))
    monkeypatch.setattr(srv, 'SECRET_FILE', str(tmp_path / '.secret'))
    monkeypatch.setattr(srv, 'SETTINGS_FILE', str(tmp_path / 'settings.json'))
    monkeypatch.setattr(srv, 'CHATS_DIR', str(tmp_path / 'chats'))
    monkeypatch.setattr(srv, 'MEMORY_DIR', str(tmp_path / 'memory'))
    monkeypatch.setattr(srv, 'INFLUENCER_DIR', str(tmp_path / 'influencers'))
    # openclaw.json 替身：测试员工的 workspace 指向 tmp（绝不写真配置）
    ws = tmp_path / 'ws-test-apply'
    fake_entries = {'emp_test_apply': {'workspace': str(ws)}}
    monkeypatch.setattr(srv, '_openclaw_agent_entries', lambda: fake_entries)
    srv._ensure_data_dir()
    srv.init_db()
    return srv


def _mk_agent(mod, agent_id='emp_test_apply', **kw):
    agents = mod._load_agents(include_archived=True)
    agents = [a for a in agents if a.get('id') != agent_id]
    agents.append(dict({'id': agent_id, 'name': '测试小拓', 'openclawName': 'test-apply',
                        'systemPrompt': '', 'createdBy': 'user_7acb72ff'}, **kw))
    mod._save_agents(agents)


def test_build_prompt_structure(mod):
    p = mod.build_duty_system_prompt(XIAOTUO_DUTY)
    # 模板六段（按简报 A 原文结构）
    assert '你是达人拓展专员' in p
    assert '商品盘 = Coolchap 女鞋（客单 50-200 元带）+ 生活家居' in p
    assert '每天 5 个深看名额，宁缺毋滥' in p
    assert '判定三关' in p
    assert '1.带得动（近30天有带货结算额，带过鞋靴箱包或同类目' in p
    assert '2.靠得住' in p and '3.不重复' in p
    assert '「值得联系」必须附一句话理由' in p
    assert '只丢不造：平台没给的字段留空，绝不编造粉丝画像' in p
    assert '日报格式：今日深看N | 新增N | 排除N（主因）' in p
    # 职责清单四条按序拼装
    assert '1. 每天从达人广场筛 5 个候选达人，逐个看详情' in p
    assert '4. 收工出日报' in p
    # 工具与权限口径（与 square_scan 工具层铁律一致）
    assert 'square_scan(category, quota≤5)' in p
    assert 'source=square_scan_<日期>' in p
    assert '遇风控 10001010A/验证码/滑块立即停手绝不重试' in p


def test_prompt_follows_duty_change(mod):
    """验收①核心：改职责 → prompt 真的跟着变。"""
    p1 = mod.build_duty_system_prompt(XIAOTUO_DUTY)
    changed = dict(XIAOTUO_DUTY, quota=3, duty_name='达人拓展专员（二期）',
                   duty_desc=XIAOTUO_DUTY['duty_desc'][:2])
    p2 = mod.build_duty_system_prompt(changed)
    assert p1 != p2, '改了 quota/duty_name/duty_desc，prompt 必须变'
    assert '每天 3 个深看名额' in p2
    assert '你是达人拓展专员（二期）' in p2
    # 未改的部分保持不动（拼装是确定性的）
    assert '只丢不造' in p2


def test_prompt_default_category_fallback(mod):
    duty = {'duty_name': '达人拓展专员'}
    p = mod.build_duty_system_prompt(duty)
    assert '带过达人拓展专员或同类目' in p, 'category 缺省回退 duty_name（拼装确定性）'


def test_save_duty_quota_hard_cap(mod):
    mod._ensure_emp_duties()
    d = mod._save_duty('emp_test_quota', {'duty_name': 'x', 'quota': 50})
    assert d['quota'] == 5, 'quota 硬顶 5'
    d = mod._save_duty('emp_test_quota', {'quota': 'abc'})
    assert d['quota'] == 5, '非数字回退 5'
    d = mod._save_duty('emp_test_quota', {'quota': 0})
    assert d['quota'] == 1, '0 压到 1'


def test_save_load_duty_roundtrip(mod):
    mod._ensure_emp_duties()
    mod._save_duty('emp_test_rt', dict(XIAOTUO_DUTY))
    d = mod._load_duty('emp_test_rt')
    assert d['duty_name'] == '达人拓展专员'
    assert d['scope'] == '鞋靴穿搭类目达人'
    desc = json.loads(d['duty_desc'])
    assert isinstance(desc, list) and len(desc) == 4
    # upsert：只改一个字段，其它保留
    mod._save_duty('emp_test_rt', {'quota': 2})
    d = mod._load_duty('emp_test_rt')
    assert d['quota'] == 2 and d['scope'] == '鞋靴穿搭类目达人'
    # 负向对照：不存在的员工返回 None
    assert mod._load_duty('emp_no_such') is None


def test_apply_duty_full_chain(mod, tmp_path):
    """验收①落点：职责 → agents.json systemPrompt → workspace SOUL.md。"""
    mod._ensure_emp_duties()
    _mk_agent(mod)
    ws = tmp_path / 'ws-test-apply'

    mod._save_duty('emp_test_apply', dict(XIAOTUO_DUTY))
    applied = mod._apply_duty_to_agent('emp_test_apply')
    assert applied and applied['soul_synced'] is True
    assert applied['conflict'] == ''

    # agents.json 的 systemPrompt 已重组装
    agents = mod._load_agents(include_archived=True)
    agent = next(a for a in agents if a.get('id') == 'emp_test_apply')
    assert agent['systemPrompt'] == applied['prompt']
    assert '判定三关' in agent['systemPrompt']

    # workspace SOUL.md 真的写进去了
    soul = (ws / 'SOUL.md').read_text(encoding='utf-8')
    assert soul == applied['prompt']
    assert '日报格式' in soul

    # 改职责再 apply：两边都跟着变
    mod._save_duty('emp_test_apply', {'quota': 2, 'duty_name': '达人拓展专员V2'})
    applied2 = mod._apply_duty_to_agent('emp_test_apply')
    assert '每天 2 个深看名额' in applied2['prompt']
    assert (ws / 'SOUL.md').read_text(encoding='utf-8') == applied2['prompt']

    # 负向对照：无职责行 → None（探针能红）
    conn = mod._db_conn()
    conn.execute("DELETE FROM emp_duties WHERE agent_id = 'emp_test_apply'")
    conn.commit()
    conn.close()
    assert mod._apply_duty_to_agent('emp_test_apply') is None


def test_apply_duty_conflict_blocks_write(mod, tmp_path, monkeypatch):
    """归属冲突闸门：workspace 属于别的 agent ⇒ 不写 SOUL.md，报 conflict。"""
    mod._ensure_emp_duties()
    _mk_agent(mod)
    ws = tmp_path / 'ws-test-apply'
    # 测试员工的 workspace 与 emp_other 撞同一个目录 ⇒ 写冲突被闸门拦住。
    # ⚠️ _workspace_owner_agent 按 entries 迭代序取第一个匹配：emp_other 排前面，
    #    才能模拟「目录归别人」的真实事故形态（木木 → 贾维斯 workspace-main）。
    monkeypatch.setattr(mod, '_openclaw_agent_entries',
                        lambda: {'emp_other': {'workspace': str(ws)},
                                 'emp_test_apply': {'workspace': str(ws)}})
    mod._save_duty('emp_test_apply', dict(XIAOTUO_DUTY))
    applied = mod._apply_duty_to_agent('emp_test_apply')
    assert applied['soul_synced'] is False
    assert applied['conflict'] == 'emp_other'
    assert not (ws / 'SOUL.md').exists(), '冲突时绝不允许落盘覆盖别人的 SOUL.md'


def test_prompt_follows_boundary_change(mod):
    """修订1（2026-11-10 增量派单）：boundary 是五要素之一，进出 prompt 必须跟着变。"""
    no_b = {k: v for k, v in XIAOTUO_DUTY.items() if k != 'boundary'}
    p1 = mod.build_duty_system_prompt(no_b)
    assert '【边界】' not in p1, '空 boundary 不输出边界段（向后兼容）'
    p2 = mod.build_duty_system_prompt(dict(no_b, boundary='联系达人、谈佣金、寄样、结算'))
    assert '【边界】你不负责：联系达人、谈佣金、寄样、结算——这些有别人做，不要越界代劳。' in p2
    assert p1 != p2


def test_boundary_full_chain_roundtrip(mod, tmp_path):
    """修订1 落点：改 boundary ⇒ _save_duty 白名单 ⇒ systemPrompt ⇒ SOUL.md（与 quota 往返同款）。"""
    mod._ensure_emp_duties()
    _mk_agent(mod)
    ws = tmp_path / 'ws-test-apply'

    mod._save_duty('emp_test_apply', dict(XIAOTUO_DUTY,
                                          boundary='联系达人、谈佣金、寄样、结算'))
    applied = mod._apply_duty_to_agent('emp_test_apply')
    assert '【边界】你不负责：联系达人、谈佣金、寄样、结算' in applied['prompt']
    assert (ws / 'SOUL.md').read_text(encoding='utf-8') == applied['prompt']

    # 往返：清空 boundary ⇒ SOUL.md 里的边界段消失
    mod._save_duty('emp_test_apply', {'boundary': ''})
    applied2 = mod._apply_duty_to_agent('emp_test_apply')
    assert '【边界】' not in applied2['prompt']
    assert (ws / 'SOUL.md').read_text(encoding='utf-8') == applied2['prompt']


def test_ensure_emp_duties_migrates_existing_table(mod):
    """存量表迁移：boundary 列幂等补加（CREATE IF NOT EXISTS 不会给已存在的表加列）。"""
    mod._ensure_emp_duties()
    conn = mod._db_conn()
    conn.execute("INSERT INTO emp_duties (agent_id, duty_name) VALUES ('emp_mig', 'x')")
    conn.commit()
    conn.execute("ALTER TABLE emp_duties RENAME TO emp_duties_old")
    # 模拟旧库：没有 boundary 列的新建表 + 旧数据
    conn.execute('''CREATE TABLE emp_duties (
        agent_id TEXT PRIMARY KEY, duty_name TEXT DEFAULT '', scope TEXT DEFAULT '',
        category TEXT DEFAULT '', quota INTEGER DEFAULT 5, goods TEXT DEFAULT '',
        duty_desc TEXT DEFAULT '[]', supervisor TEXT DEFAULT '',
        last_run_at INTEGER DEFAULT 0, last_run_summary TEXT DEFAULT '',
        created_at INTEGER, updated_at INTEGER)''')
    conn.execute("INSERT INTO emp_duties SELECT agent_id, duty_name, scope, category, quota,"
                 " goods, duty_desc, supervisor, last_run_at, last_run_summary,"
                 " created_at, updated_at FROM emp_duties_old")
    conn.execute("DROP TABLE emp_duties_old")
    conn.commit()
    conn.close()
    mod._ensure_emp_duties()   # 迁移发生在这里
    d = mod._save_duty('emp_mig', {'boundary': '不越界'})
    assert d['boundary'] == '不越界'


# ── ②主管协同位（2026-11-10）：supervisor_agent_id ──

def test_supervisor_agent_id_in_prompt(mod):
    """supervisor_agent_id 落 prompt：汇报对象段必须含 agent id + 抄送/被@语义。"""
    duty = dict(XIAOTUO_DUTY, supervisor='Helen', supervisor_agent_id='emp_1780199176680')
    p = mod.build_duty_system_prompt(duty)
    assert 'emp_1780199176680' in p
    assert '日报同时抄送给她' in p
    assert '被 @ 必须用自己的职能回应' in p


def test_supervisor_agent_id_absent_prompt_unchanged(mod):
    """无 supervisor_agent_id ⇒ 汇报对象段保持原文（向后兼容，无空括号）。"""
    p = mod.build_duty_system_prompt(dict(XIAOTUO_DUTY))
    assert 'agent:' not in p
    assert '【汇报对象】商务主管' in p


def test_supervisor_agent_id_roundtrip_and_apply(mod, tmp_path):
    """supervisor_agent_id 走 _save_duty 白名单 ⇒ SOUL.md 跟着变（职能驱动同款验证）。"""
    mod._ensure_emp_duties()
    _mk_agent(mod)
    ws = tmp_path / 'ws-test-apply'
    mod._save_duty('emp_test_apply', dict(XIAOTUO_DUTY, supervisor='Helen',
                                          supervisor_agent_id='emp_1780199176680'))
    applied = mod._apply_duty_to_agent('emp_test_apply')
    assert 'emp_1780199176680' in applied['prompt']
    assert 'emp_1780199176680' in (ws / 'SOUL.md').read_text(encoding='utf-8')
    # 往返：清空 ⇒ 汇报对象段回退纯文本
    mod._save_duty('emp_test_apply', {'supervisor_agent_id': ''})
    applied2 = mod._apply_duty_to_agent('emp_test_apply')
    assert 'agent:' not in applied2['prompt']


def test_reports_query(mod):
    """名片双向数据源：supervisor_agent_id 反查直接下属。"""
    mod._ensure_emp_duties()
    mod._save_duty('emp_sub1', dict(XIAOTUO_DUTY, supervisor_agent_id='emp_boss'))
    mod._save_duty('emp_sub2', dict(XIAOTUO_DUTY, supervisor_agent_id='emp_boss'))
    mod._save_duty('emp_other', dict(XIAOTUO_DUTY, supervisor_agent_id='emp_someone_else'))
    conn = mod._db_conn()
    rows = conn.execute(
        'SELECT agent_id FROM emp_duties WHERE supervisor_agent_id = ?',
        ('emp_boss',)).fetchall()
    conn.close()
    assert sorted(r[0] for r in rows) == ['emp_sub1', 'emp_sub2']


def test_ensure_emp_duties_migrates_supervisor_cols(mod):
    """存量表迁移：supervisor_agent_id / zero_streak 列幂等补加。"""
    mod._ensure_emp_duties()
    conn = mod._db_conn()
    conn.execute("INSERT INTO emp_duties (agent_id) VALUES ('emp_mig2')")
    conn.commit()
    conn.execute("ALTER TABLE emp_duties RENAME TO emp_duties_old")
    # 模拟 11-10 早期的库：有 boundary、没有主管列
    conn.execute('''CREATE TABLE emp_duties (
        agent_id TEXT PRIMARY KEY, duty_name TEXT DEFAULT '', scope TEXT DEFAULT '',
        category TEXT DEFAULT '', quota INTEGER DEFAULT 5, goods TEXT DEFAULT '',
        duty_desc TEXT DEFAULT '[]', supervisor TEXT DEFAULT '', boundary TEXT DEFAULT '',
        last_run_at INTEGER DEFAULT 0, last_run_summary TEXT DEFAULT '',
        created_at INTEGER, updated_at INTEGER)''')
    conn.execute("INSERT INTO emp_duties (agent_id, duty_name, scope, category, quota,"
                 " goods, duty_desc, supervisor, boundary, last_run_at, last_run_summary,"
                 " created_at, updated_at) SELECT agent_id, duty_name, scope, category,"
                 " quota, goods, duty_desc, supervisor, boundary, last_run_at,"
                 " last_run_summary, created_at, updated_at FROM emp_duties_old")
    conn.execute("DROP TABLE emp_duties_old")
    conn.commit()
    conn.close()
    mod._ensure_emp_duties()
    d = mod._load_duty('emp_mig2')
    assert 'supervisor_agent_id' in d and 'zero_streak' in d
