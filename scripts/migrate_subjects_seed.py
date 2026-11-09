#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""subjects 种子数据 migration（阶段 1 前置 #5，2026-11-08）

把三套身份（users.json / agents.json / bot_tenants）映射成 subjects 大表种子行。
**默认 dry-run**：只打印将写入什么 + 与对账文档 §4 期望的差异，不加 --apply 不落库。

用法：
  python3 scripts/migrate_subjects_seed.py            # dry-run（只看不动）
  python3 scripts/migrate_subjects_seed.py --apply    # 落库（幂等 INSERT OR REPLACE）

依据：docs/SUBJECTS-PACKS-RECONCILE.md §4/§5；种子判据：
  users.role=='admin'      → kind=super_admin（admin 同时是 t_default 的 tenant_owner）
  users 其余               → kind=biz_staff
  agents（AI 员工）         → kind=agent_persona，agent_of=createdBy（挂真实所有人）
  bot_tenants（达人主体）   → kind=creator，agent_of=库内现值（合伙人层级投影）
  'main'（系统内置默认 agent）→ kind=platform_internal，不扣积分（2026-11-09 收口硬编码名单）
"""
import argparse
import json
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SOLO_DB = os.path.join(ROOT, 'data', 'solobrave.db')
DEFAULT_USERS = os.path.join(ROOT, 'data', 'users.json')
DEFAULT_AGENTS = os.path.join(ROOT, 'data', 'agents.json')
DEFAULT_DAREN_DB = os.path.join(ROOT, 'data', 'daren-bot', 'daren_bot.db')

# 对账文档 §4 的期望（用于 dry-run 差异告警，数据漂移时提示人工核）
EXPECT_AGENT_OWNER = {
    'Helen': 'user_30f05078',          # → 挂贺主管
    '木木': None,                      # None = 不硬编码，打印出来人工确认
}

DDL = '''
CREATE TABLE IF NOT EXISTS subjects (
    subject_id TEXT PRIMARY KEY,        -- user_id / agent_id / bot tenant_key
    kind TEXT NOT NULL,                 -- super_admin|tenant_owner|biz_staff|creator|agent_persona|platform_internal
    tenant_id TEXT DEFAULT 't_default',
    agent_of TEXT DEFAULT '',           -- 业务归属上级（合伙人层级投影，人格≠身份守卫适用）
    packs TEXT DEFAULT '[]',            -- JSON：能力包 id 列表（阶段 2 推导模块白名单）
    source TEXT DEFAULT '',             -- users.json | agents.json | bot_tenants
    created_at INTEGER
)
'''


def build_seeds(users, agents, bot_tenants):
    """纯函数：三套身份 → subjects 种子行列表。可单测。"""
    rows = []
    for u in users:
        uid = u.get('id') or u.get('userId') or ''
        if not uid:
            continue
        kind = 'super_admin' if u.get('role') == 'admin' else 'biz_staff'
        rows.append({'subject_id': uid, 'kind': kind,
                     'tenant_id': u.get('tenant_id') or 't_default',
                     'agent_of': '', 'source': 'users.json'})
    for a in agents:
        aid = a.get('id') or ''
        if not aid:
            continue
        rows.append({'subject_id': aid, 'kind': 'agent_persona',
                     'tenant_id': a.get('tenant_id') or 't_default',
                     'agent_of': a.get('createdBy') or '',
                     'source': 'agents.json',
                     'name': a.get('name') or ''})   # name 仅用于告警匹配，不落库
    for t in bot_tenants:
        key = t.get('tenant_key') or ''
        if not key:
            continue
        rows.append({'subject_id': 'bot:' + key, 'kind': 'creator',
                     'tenant_id': 't_default',
                     'agent_of': t.get('agent_of') or '',
                     'source': 'bot_tenants'})
    # 系统内置主体：默认 agent 'main'（无 users/agents 记录，历史靠硬编码 id 名单白嫖积分）
    rows.append({'subject_id': 'main', 'kind': 'platform_internal',
                 'tenant_id': 't_default', 'agent_of': '',
                 'source': 'system_default'})
    return rows


def diff_warnings(rows):
    """与对账文档 §4 期望的差异 → 告警字符串列表。"""
    warns = []
    by_name = {}
    agents = [r for r in rows if r['source'] == 'agents.json']
    for r in agents:
        by_name[r['subject_id']] = r
    for name, expect_owner in EXPECT_AGENT_OWNER.items():
        hit = [r for r in agents if r.get('name') == name or name in (r.get('name') or '')]
        if not hit:
            warns.append(f'[缺失] 文档点名的 {name} 在 agents.json 没找到——映射前先解决归属')
            continue
        for r in hit:
            if expect_owner and r['agent_of'] != expect_owner:
                warns.append(f'[漂移] {name} 现挂 {r["agent_of"] or "(空)"}'
                             f'，文档期望 {expect_owner}——确认后再 --apply')
            if expect_owner is None:
                warns.append(f'[待确认] {name} 现挂 {r["agent_of"] or "(空)"}'
                             f'（文档说「挂真实所有人，10-27 对账有结论」）——老大拍板')
    bots = [r for r in rows if r['source'] == 'bot_tenants']
    known = {'bot:daren_boss', 'bot:daren_test2', 'bot:mumu'}
    extra = [r['subject_id'] for r in bots if r['subject_id'] not in known]
    if extra:
        warns.append(f'[新增] bot_tenants 有文档之外的租户：{extra}——一并映射为 creator，请知悉')
    return warns


def main(argv=None):
    ap = argparse.ArgumentParser(description='subjects 种子 migration（默认 dry-run）')
    ap.add_argument('--apply', action='store_true', help='真正落库（幂等）')
    ap.add_argument('--db', default=DEFAULT_SOLO_DB)
    ap.add_argument('--users', default=DEFAULT_USERS)
    ap.add_argument('--agents', default=DEFAULT_AGENTS)
    ap.add_argument('--daren-db', default=DEFAULT_DAREN_DB)
    args = ap.parse_args(argv)

    users = json.load(open(args.users))
    agents = json.load(open(args.agents))
    conn = sqlite3.connect(args.daren_db)
    try:
        conn.row_factory = sqlite3.Row
        bot_rows = [dict(r) for r in conn.execute(
            'SELECT tenant_key, role, agent_of FROM bot_tenants')]
    finally:
        conn.close()

    rows = build_seeds(users, agents, bot_rows)
    mode = 'APPLY' if args.apply else 'DRY-RUN'
    print(f'== subjects 种子 migration [{mode}] ==')
    print(f'{"subject_id":<40} {"kind":<14} {"agent_of":<16} source')
    for r in rows:
        print(f'{r["subject_id"]:<40} {r["kind"]:<14} {r["agent_of"] or "-":<16} {r["source"]}')

    for w in diff_warnings(rows):
        print('!', w)

    if not args.apply:
        print('\n（dry-run 未落库。确认无误后加 --apply）')
        return 0

    conn = sqlite3.connect(args.db)
    try:
        conn.execute(DDL)
        now = int(time.time() * 1000)
        for r in rows:
            conn.execute('''
                INSERT OR REPLACE INTO subjects
                    (subject_id, kind, tenant_id, agent_of, packs, source, created_at)
                VALUES (?, ?, ?, ?, '[]', ?, ?)
            ''', (r['subject_id'], r['kind'], r['tenant_id'],
                  r['agent_of'], r['source'], now))
        conn.commit()
        n = conn.execute('SELECT COUNT(*) FROM subjects').fetchone()[0]
        print(f'\n落库完成：subjects 共 {n} 行')
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
