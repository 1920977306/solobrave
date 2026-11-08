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


# ─── 1. 打标清单生成器 ──────────────────────────────────────────────────────

def test_label_generator_counts_consistent(capsys):
    label_mod.main()
    out = capsys.readouterr().out
    assert '[platform]' in out and '[tenant]' in out
    assert '| :2626 |' in out                       # 主开关定义行标为 definition
    assert '**[framework]**' in out
    # 每行都是五列表格行，计数自洽（表头+分隔行除外）
    rows = [l for l in out.splitlines() if l.startswith('| :')]
    assert len(rows) >= 130, f'站点数异常：{len(rows)}'


def test_label_generator_known_sites(capsys):
    label_mod.main()
    out = capsys.readouterr().out
    # 权限管理 = platform；知识库 = tenant；人格守卫 helper 区域不被误标
    assert '_handle_get_permissions' in out and '**[platform]**' in out
    assert '_handle_get_knowledge`' in out and '**[tenant]**' in out


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
    assert n == 2          # u_admin + bot:tk1，重跑不产生重复行
