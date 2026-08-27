# -*- coding: utf-8 -*-
"""修复 talents 表中 created_by = 'localhost' 的历史脏数据。

背景：AI 员工本地调用录入达人时若未携带 X-Agent-Id，created_by 被落成 'localhost'，
不匹配任何真实用户，子账号（如荔枝）按 created_by = 自己 user_id 查询时永远查不到。
服务端已在录入接口拒绝匿名 localhost 写入（见 solobrave-server.py 的 SubpoolGuard），
本脚本负责修复存量数据。

默认 dry-run：列出所有 created_by='localhost' 的达人及推断的归属，不改数据。
  python fix_talent_localhost_owner.py                              # 只列出，不修改
  python fix_talent_localhost_owner.py --infer                      # 按 group_id → groups.json 的 createdBy 反查归属并写入
  python fix_talent_localhost_owner.py --owner user_c6eac88f        # 全部强制归属指定用户
  python fix_talent_localhost_owner.py --owner user_c6eac88f --ids tal_a tal_b   # 只改指定记录

写入前自动备份数据库到 solobrave.db.bak-<时间戳>。
推断规则（--infer）：达人 group_id 指向的群组（data/groups.json）的 createdBy 即归属人；
群组不存在或无 createdBy 的记录跳过并列出，需用 --owner 手动指定。
"""
import json
import os
import shutil
import sqlite3
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
DB_PATH = os.path.join(DATA_DIR, 'solobrave.db')
GROUPS_FILE = os.path.join(DATA_DIR, 'groups.json')

sys.stdout.reconfigure(encoding='utf-8')


def load_group_owners():
    """返回 {group_id: createdBy}"""
    try:
        with open(GROUPS_FILE, encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return {}
    groups = data if isinstance(data, list) else data.get('groups', [])
    return {g.get('id'): g.get('createdBy') for g in groups if g.get('id')}


def main():
    args = sys.argv[1:]
    owner = None
    if '--owner' in args:
        idx = args.index('--owner')
        if idx + 1 >= len(args) or args[idx + 1].startswith('--'):
            print('错误：--owner 后需要用户 ID')
            sys.exit(1)
        owner = args[idx + 1]
    ids = []
    if '--ids' in args:
        ids = [a for a in args[args.index('--ids') + 1:] if not a.startswith('--')]
    infer = '--infer' in args

    if not os.path.isfile(DB_PATH):
        print(f'数据库不存在: {DB_PATH}')
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, name, douyin_id, group_id, created_at FROM talents WHERE created_by = 'localhost'"
    ).fetchall()

    if ids:
        rows = [r for r in rows if r['id'] in set(ids)]

    if not rows:
        print("没有 created_by = 'localhost' 的达人记录，无需修复")
        conn.close()
        return

    group_owners = load_group_owners()

    # 为每条记录确定目标归属
    plan = []   # (row, target_owner, source)
    skipped = []
    for r in rows:
        if owner:
            plan.append((r, owner, '--owner 指定'))
        elif infer:
            g_owner = group_owners.get(r['group_id'] or '')
            if g_owner:
                plan.append((r, g_owner, f"群组 {r['group_id']} 的 createdBy"))
            else:
                skipped.append(r)
        else:
            g_owner = group_owners.get(r['group_id'] or '')
            plan.append((r, g_owner or '(无法推断)', f"群组 {r['group_id']} 的 createdBy" if g_owner else '无群组线索'))

    print(f"共 {len(rows)} 条 created_by = 'localhost' 的达人记录：")
    for r, target, source in plan:
        print(f"  {r['id']} | {r['name']} | 抖音号:{r['douyin_id'] or '-'} | 归属 → {target}（{source}）")
    for r in skipped:
        print(f"  {r['id']} | {r['name']} | 跳过：群组 {r['group_id'] or '-'} 无 createdBy，需 --owner 手动指定")

    if not owner and not infer:
        print('\ndry-run，未修改。用 --infer 按群组归属修复，或 --owner <user_id> 强制指定归属。')
        conn.close()
        return

    if not plan:
        print('\n没有可修复的记录')
        conn.close()
        return

    ts = time.strftime('%Y%m%d-%H%M%S')
    shutil.copy(DB_PATH, DB_PATH + f'.bak-{ts}')
    for r, target, _source in plan:
        conn.execute(
            "UPDATE talents SET created_by = ?, updated_at = ? WHERE id = ?",
            (target, int(time.time() * 1000), r['id']))
    conn.commit()
    conn.close()
    print(f"\n已修复 {len(plan)} 条记录，数据库已备份为 solobrave.db.bak-{ts}")
    if skipped:
        print(f"剩余 {len(skipped)} 条未修复，请用 --owner 指定归属后重跑")


if __name__ == '__main__':
    main()
