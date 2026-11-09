# -*- coding: utf-8 -*-
"""阶段 1 权限资产守卫测试（2026-11-08）

覆盖：
  1. label_is_admin_sites：产出含 [platform]/[tenant] 标注 + 计数自洽
  2. migrate_subjects_seed.build_seeds：users/agents/bot_tenants → 种子行判据
  3. migrate_subjects_seed dry-run 默认不落库；--apply 落库且幂等
"""
import importlib.util
import json
import os
import sqlite3

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


label_mod = _load('label_is_admin_sites', 'scripts/label_is_admin_sites.py')
seed_mod = _load('migrate_subjects_seed', 'scripts/migrate_subjects_seed.py')

# 独立服务端模块实例（AuthResult/_subject_kind 测试用），路径全指 tmp
_srv = _load('solobrave_server', 'solobrave-server.py')


@pytest.fixture()
def mod(tmp_path, monkeypatch):
    monkeypatch.setattr(_srv, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(_srv, 'DB_PATH', str(tmp_path / 'solobrave.db'))
    monkeypatch.setattr(_srv, 'AGENTS_FILE', str(tmp_path / 'agents.json'))
    monkeypatch.setattr(_srv, 'USERS_FILE', str(tmp_path / 'users.json'))
    monkeypatch.setattr(_srv, 'PERMISSIONS_FILE', str(tmp_path / 'permissions.json'))
    monkeypatch.setattr(_srv, 'GROUPS_FILE', str(tmp_path / 'groups.json'))
    monkeypatch.setattr(_srv, 'TEAMS_FILE', str(tmp_path / 'teams.json'))
    monkeypatch.setattr(_srv, 'SECRET_FILE', str(tmp_path / '.secret'))
    monkeypatch.setattr(_srv, 'SETTINGS_FILE', str(tmp_path / 'settings.json'))
    monkeypatch.setattr(_srv, 'CHATS_DIR', str(tmp_path / 'chats'))
    monkeypatch.setattr(_srv, 'MEMORY_DIR', str(tmp_path / 'memory'))
    monkeypatch.setattr(_srv, 'INFLUENCER_DIR', str(tmp_path / 'influencers'))
    _srv._ensure_data_dir()
    _srv.init_db()
    return _srv


# ─── 1. 打标清单生成器 ──────────────────────────────────────────────────────

def test_label_generator_counts_consistent(capsys):
    label_mod.main()
    out = capsys.readouterr().out
    assert '[platform]' in out and '[tenant]' in out
    assert '**[framework]**' in out
    # 阶段2批2收口：业务消费清零；剩余行 = framework + definition + comment
    rows = [l for l in out.splitlines() if l.startswith('| :')]
    assert 3 <= len(rows) <= 12, f'站点数异常：{len(rows)}'
    assert '**0 处消费**' in out


def test_label_generator_known_sites(capsys):
    label_mod.main()
    out = capsys.readouterr().out
    # 阶段2批2后业务消费全部切换：tenant/platform 均清零，批1/批2函数不再出现
    assert '### [tenant] 0 处（已清零 ✅）' in out
    assert '### [platform] 0 处（已清零 ✅）' in out
    assert '_handle_get_permissions' not in out


# ─── 2. build_seeds 判据 ────────────────────────────────────────────────────

def _seeds():
    users = [{'id': 'u_admin', 'role': 'admin', 'tenant_id': 't_default'},
             {'id': 'u_staff', 'role': 'employee', 'tenant_id': 't_default'}]
    agents = [{'id': 'emp_x', 'name': '小X', 'createdBy': 'u_staff',
               'tenant_id': 't_default'}]
    bots = [{'tenant_key': 'daren_boss', 'role': 'daren', 'agent_of': ''}]
    return seed_mod.build_seeds(users, agents, bots)


def test_build_seeds_kinds():
    rows = {r['subject_id']: r for r in _seeds()}
    assert rows['u_admin']['kind'] == 'super_admin'
    assert rows['u_staff']['kind'] == 'biz_staff'
    assert rows['emp_x']['kind'] == 'agent_persona'
    assert rows['emp_x']['agent_of'] == 'u_staff'      # 挂真实所有人
    assert rows['bot:daren_boss']['kind'] == 'creator'
    assert rows['main']['kind'] == 'platform_internal'  # 系统内置，不扣积分


# ─── 3. dry-run / apply / 幂等 ──────────────────────────────────────────────

def _seed_files(tmp_path):
    users = tmp_path / 'users.json'
    users.write_text(json.dumps(
        [{'id': 'u_admin', 'role': 'admin', 'tenant_id': 't_default'}]))
    agents = tmp_path / 'agents.json'
    agents.write_text(json.dumps([]))
    daren_db = tmp_path / 'daren.db'
    conn = sqlite3.connect(str(daren_db))
    conn.execute('CREATE TABLE bot_tenants (tenant_key TEXT, role TEXT, agent_of TEXT)')
    conn.execute("INSERT INTO bot_tenants VALUES ('tk1', 'daren', '')")
    conn.commit()
    conn.close()
    return str(users), str(agents), str(daren_db)


def test_seed_dry_run_default_writes_nothing(tmp_path, capsys):
    users, agents, daren_db = _seed_files(tmp_path)
    out_db = str(tmp_path / 'out.db')
    seed_mod.main(['--db', out_db, '--users', users,
                   '--agents', agents, '--daren-db', daren_db])
    out = capsys.readouterr().out
    assert 'DRY-RUN' in out
    assert not os.path.exists(out_db)


def test_seed_apply_idempotent(tmp_path, capsys):
    users, agents, daren_db = _seed_files(tmp_path)
    out_db = str(tmp_path / 'out.db')
    argv = ['--apply', '--db', out_db, '--users', users,
            '--agents', agents, '--daren-db', daren_db]
    seed_mod.main(argv)
    seed_mod.main(argv)
    conn = sqlite3.connect(out_db)
    try:
        n = conn.execute('SELECT COUNT(*) FROM subjects').fetchone()[0]
    finally:
        conn.close()
    assert n == 3          # u_admin + bot:tk1 + main(system_default)，重跑不产生重复行


# ─── 4. 批 1 切换守卫（2026-11-09） ─────────────────────────────────────────

BATCH1_FNS = {
    '_handle_get_permissions', '_handle_update_role_permissions',
    '_handle_update_user_permissions',
    '_can_access_team', '_handle_get_user_subordinates', '_check_group_access',
    '_handle_get_groups', '_handle_update_group', '_handle_batch_save_groups',
    '_handle_delete_group', '_handle_add_group_member',
    '_handle_remove_group_member', '_handle_get_team', '_handle_update_team',
    '_handle_delete_team', '_handle_add_team_member',
    '_handle_remove_team_member',
    '_handle_get_proposals', '_handle_get_proposal',
    '_handle_post_proposal_resolve', '_handle_delete_proposal',
    '_handle_get_agent_calls',
}


def _fn_spans():
    """fn → (start,end) 行号（顶层 def 和 indent-4 方法都算）。"""
    import re
    lines = open(os.path.join(ROOT, 'solobrave-server.py')).read().splitlines()
    spans = {}
    for i, l in enumerate(lines, 1):
        m = re.match(r'^(?: {4})?def ([\w]+)\(', l)
        if m:
            spans.setdefault(m.group(1), [i, None])
            for name, prev in spans.items():
                if prev[1] is None and prev[0] != i:
                    prev[1] = i - 1
    for prev in spans.values():
        if prev[1] is None:
            prev[1] = len(lines)
    return spans, lines


def test_batch1_sites_switched_to_platform_admin():
    spans, lines = _fn_spans()
    for fn in BATCH1_FNS:
        s, e = spans[fn]
        body = '\n'.join(lines[s - 1:e])
        assert 'auth.is_platform_admin' in body, f'{fn} 未切换到 is_platform_admin'
        bare = body.replace('auth.is_platform_admin', '')
        assert 'auth.is_admin' not in bare, f'{fn} 仍残留裸 auth.is_admin'


# 批 2（2026-11-09）：充值/平台口子铁律 super_admin only（收回并联）
BATCH2_REVOKE_FNS = {
    '_handle_credits_admin_grant', '_handle_credit_recharge',
    '_handle_credit_recharge_generic', '_handle_post_feishu_credits_recharge',
    '_handle_post_feishu_console_agent_op', '_handle_post_feishu_tenant_create',
}
# 读+本租户裁剪（二期）：保留 is_tenant_admin 并联，is_admin 半边已切 platform
BATCH2_KEEP_FNS = {
    '_handle_get_feishu_credits', '_handle_get_feishu_console',
    '_handle_get_feishu_chat', '_handle_get_feishu_facts',
    '_handle_post_feishu_fact_delete',
}


def test_batch2_revoke_fns_super_admin_only():
    spans, lines = _fn_spans()
    for fn in BATCH2_REVOKE_FNS:
        s, e = spans[fn]
        body = '\n'.join(lines[s - 1:e])
        assert 'is_platform_admin' in body, f'{fn} 未切 platform_admin'
        assert 'auth.is_tenant_admin' not in body, \
            f'{fn} 充值口子仍保留 is_tenant_admin 并联（铁律：super_admin only）'
        assert 'auth.is_admin' not in body.replace('auth.is_platform_admin', ''), \
            f'{fn} 仍残留裸 auth.is_admin'


def test_batch2_keep_fns_still_allow_tenant_admin():
    spans, lines = _fn_spans()
    for fn in BATCH2_KEEP_FNS:
        s, e = spans[fn]
        body = '\n'.join(lines[s - 1:e])
        assert 'auth.is_platform_admin or auth.is_tenant_admin' in body, \
            f'{fn} 读接口应保留 tenant_admin 并联（tenant_owner 裁剪二期）'


def test_require_admin_uses_platform_admin():
    spans, lines = _fn_spans()
    s, e = spans['_require_admin']
    body = '\n'.join(lines[s - 1:e])
    assert 'auth.is_platform_admin' in body


# 批 3（2026-11-09）：数据管道 4 处 + 用量观测 2 处切 is_platform_admin，[platform] 收口
BATCH3_FNS = {
    '_handle_post_pipe_start', '_handle_get_pipe_progress',
    '_handle_post_pipe_cancel', '_handle_get_pipe_list',
    '_handle_get_llm_usage', '_handle_get_token_usage',
}


def test_batch3_sites_switched():
    spans, lines = _fn_spans()
    for fn in BATCH3_FNS:
        s, e = spans[fn]
        body = '\n'.join(lines[s - 1:e])
        assert 'auth.is_platform_admin' in body, f'{fn} 未切换到 is_platform_admin'
        bare = body.replace('auth.is_platform_admin', '')
        assert 'auth.is_admin' not in bare, f'{fn} 仍残留裸 auth.is_admin'


# 阶段2批0（2026-11-09）：C 桶平台维护面 8 处切 is_platform_admin（老大 16:18 拍板：平台维护归 platform only）
BATCH0_FNS = {
    '_handle_post_kb_reindex', '_handle_post_kb_retry_embedding',
    '_handle_post_kb_retry_all_failed', '_handle_cleanup_kb_dangling',
    '_handle_post_verify_kb_index', '_handle_post_repair_kb_index',
    '_handle_get_knowledge_patterns', '_handle_get_stats_compute',
}


def test_batch0_platform_maintenance_switched():
    spans, lines = _fn_spans()
    for fn in BATCH0_FNS:
        s, e = spans[fn]
        body = '\n'.join(lines[s - 1:e])
        assert 'auth.is_platform_admin' in body, f'{fn} 未切换到 is_platform_admin'
        bare = body.replace('auth.is_platform_admin', '')
        assert 'auth.is_admin' not in bare, f'{fn} 仍残留裸 auth.is_admin'
    # ks 层字面量 is_admin=True（系统级操作语义）保留，不算消费
    s, e = spans['_handle_cleanup_kb_dangling']
    body = '\n'.join(lines[s - 1:e])
    assert 'tenant_manager=True' in body


# 阶段2批1（2026-11-09）：A 桶业务管理面 40 处并联——统一判 is_tenant_manager
# （= is_platform_admin or is_tenant_owner；充值口子禁用，super_admin only 铁律不受影响）
STAGE2_BATCH1_FNS = [
    '_check_talent_write_permission', '_get_accessible_agent_ids',
    '_handle_get_agents', '_handle_get_agent', '_handle_get_agent_stats',
    '_handle_get_agent_activity', '_handle_create_agent_inner',
    '_handle_update_agent', '_handle_delete_agent', '_check_agent_access',
    '_handle_post_dreaming', '_handle_get_tasks', '_handle_get_task',
    '_handle_post_task', '_handle_put_task', '_handle_delete_task',
    '_handle_get_products', '_handle_post_product', '_handle_get_brands',
    '_handle_get_deals', '_handle_get_deal_detail', '_handle_search_talents',
    '_handle_get_talent', '_handle_promote_talent', '_handle_get_influencers',
    '_handle_get_analyzed_talents', '_talent_id_by_name',
    '_extract_talent_from_text', '_build_single_talent_injection',
    '_build_talent_injection_text',
]


def test_stage2_batch1_uses_tenant_manager():
    spans, lines = _fn_spans()
    for fn in STAGE2_BATCH1_FNS:
        s, e = spans[fn]
        body = '\n'.join(lines[s - 1:e])
        assert 'auth.is_tenant_manager' in body, f'{fn} 未切到 is_tenant_manager'
        stripped = body.replace('auth.is_tenant_manager', '')
        assert 'auth.is_admin' not in stripped, f'{fn} 仍残留裸 auth.is_admin'


def test_tenant_owner_property(mod):
    """is_tenant_owner：kind 行优先（tenant_owner 才算），无行回退 role=='tenant_admin'。"""
    mod._SUBJECT_KIND_CACHE.clear()
    conn = sqlite3.connect(mod.DB_PATH)
    conn.execute(seed_mod.DDL)
    conn.execute("INSERT INTO subjects (subject_id, kind, tenant_id, agent_of,"
                 " packs, source, created_at) VALUES ('u_partner', 'tenant_owner',"
                 " 't_default', '', '[]', 'test', 0)")
    conn.commit()
    conn.close()

    def _ar(uid, role):
        return mod.AuthResult(user_info={'userId': uid, 'role': role})

    assert _ar('u_partner', 'employee').is_tenant_owner is True
    mod._SUBJECT_KIND_CACHE.clear()
    assert _ar('u_partner', 'employee').is_tenant_manager is True
    # super_admin 是超集：tenant_manager 恒真
    mod._SUBJECT_KIND_CACHE.clear()
    assert _ar('user_7acb72ff', 'admin').is_tenant_manager is True
    # 无 kind 行 → 回退 role
    mod._SUBJECT_KIND_CACHE.clear()
    assert _ar('u_stranger', 'tenant_admin').is_tenant_owner is True
    mod._SUBJECT_KIND_CACHE.clear()
    assert _ar('u_stranger', 'employee').is_tenant_owner is False


def test_credit_bypass_reads_subjects_kind(mod):
    """_record_credit_usage 不扣减名单改读 subjects.kind：super_admin/platform_internal
    不扣，无 kind 行照扣（fail-closed）。替换原硬编码 ('main','user_7acb72ff')。"""
    import sqlite3 as _sq
    conn = _sq.connect(mod.DB_PATH)
    try:
        conn.execute(seed_mod.DDL)
        conn.execute("INSERT INTO subjects (subject_id, kind, tenant_id, agent_of,"
                     " packs, source, created_at) VALUES ('main', 'platform_internal',"
                     " 't_default', '', '[]', 'test', 0)")
        conn.execute("INSERT INTO subjects (subject_id, kind, tenant_id, agent_of,"
                     " packs, source, created_at) VALUES ('u_boss', 'super_admin',"
                     " 't_default', '', '[]', 'test', 0)")
        conn.commit()
        for a in ('main', 'u_boss', 'agent_x'):
            mod._ensure_credit_account(conn, a)

        def _consumed(a):
            return conn.execute('SELECT total_consumed FROM credit_accounts'
                                ' WHERE agent_id = ?', (a,)).fetchone()[0]

        mod._record_credit_usage(conn, 'main', 3000, 0, 0)
        mod._record_credit_usage(conn, 'u_boss', 3000, 0, 0)
        charged = mod._record_credit_usage(conn, 'agent_x', 3000, 0, 0)
        assert _consumed('main') == 0 and _consumed('u_boss') == 0, \
            'platform_internal / super_admin 不应扣减'
        assert charged > 0 and _consumed('agent_x') == charged, \
            '无 kind 行的业务 agent 必须照扣'
        # 同一口径的余额闸门：豁免名单同样读 subjects.kind
        conn.commit()
        assert mod._check_credit_balance('main') == (999999, True)
        assert mod._check_credit_balance('u_boss') == (999999, True)
        _b, _ok = mod._check_credit_balance('agent_x')
        assert not _ok, '无 kind 行的业务 agent 不应拿虚拟余额'
        # 无 subjects 表（异常路径）→ fail-closed 照扣
        conn.execute('DROP TABLE subjects')
        before = _consumed('agent_x')
        mod._record_credit_usage(conn, 'agent_x', 3000, 0, 0)
        assert _consumed('agent_x') > before, '表缺失时也必须照扣（不多送积分）'
    finally:
        conn.close()


def test_is_platform_admin_reads_subjects_kind(mod, monkeypatch):
    """有 kind 行按 kind；无行回退 role=='admin'。"""
    mod._SUBJECT_KIND_CACHE.clear()
    conn = sqlite3.connect(mod.DB_PATH)
    conn.execute(seed_mod.DDL)          # subjects 表由 migration 脚本建，非 init_db
    conn.execute("INSERT INTO subjects (subject_id, kind, tenant_id, agent_of,"
                 " packs, source, created_at) VALUES ('u_biz', 'biz_staff',"
                 " 't_default', '', '[]', 'test', 0)")
    conn.commit()
    conn.close()

    def _ar(uid, role):
        return mod.AuthResult(user_info={'userId': uid, 'role': role})

    assert _ar('u_biz', 'admin').is_platform_admin is False
    mod._SUBJECT_KIND_CACHE.clear()
    assert _ar('u_biz', 'employee').is_platform_admin is False
    # 无 kind 行 → 回退 role
    mod._SUBJECT_KIND_CACHE.clear()
    assert _ar('u_stranger', 'admin').is_platform_admin
    mod._SUBJECT_KIND_CACHE.clear()
    assert _ar('u_stranger', 'employee').is_platform_admin is False
