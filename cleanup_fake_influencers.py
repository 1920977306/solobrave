# -*- coding: utf-8 -*-
"""清理达人库中被 AI 员工录入的假数据（data/influencers/index.json + 详情文件）。

默认 dry-run：列出所有达人及录入者身份（admin 用户 / 普通用户 / AI 员工 / 未知），不删任何东西。
  python cleanup_fake_influencers.py                                  # 只列出，不删除
  python cleanup_fake_influencers.py --delete inf_xxx [inf_yyy ...]   # 删除指定 ID
  python cleanup_fake_influencers.py --delete-non-admin               # 删除所有录入者不是 admin 的记录

删除前自动备份 index.json 到 index.json.bak-<时间戳>，并同步删除 data/influencers/<id>.json 详情文件。
注意：本脚本只清理 JSON 达人库（/api/influencers）。SQLite talents 表是另一套存储，
如也被污染需另行核对（表在 data/solobrave.db 的 talents 表）。
"""
import json
import os
import shutil
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
INFLUENCER_DIR = os.path.join(DATA_DIR, 'influencers')
INDEX_FILE = os.path.join(INFLUENCER_DIR, 'index.json')
USERS_FILE = os.path.join(DATA_DIR, 'users.json')
AGENTS_FILE = os.path.join(DATA_DIR, 'agents.json')

sys.stdout.reconfigure(encoding='utf-8')


def load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def resolve_creator(created_by, users, agents):
    """返回 (描述, 是否admin)。createdBy 可能是用户ID、agent ID 或 localhost。"""
    if not created_by:
        return ('未知(空)', False)
    if created_by == 'localhost':
        return ('localhost内部', True)  # 服务器内部写入，视同 admin
    u = next((x for x in users if x.get('id') == created_by), None)
    if u:
        return (f"用户:{u.get('username') or created_by}({u.get('role')})", u.get('role') == 'admin')
    a = next((x for x in agents if x.get('id') == created_by), None)
    if a:
        owner = next((x for x in users if x.get('id') == a.get('createdBy')), None)
        owner_desc = f"{owner.get('username')}({owner.get('role')})" if owner else f"{a.get('createdBy')}(未知)"
        return (f"AI员工:{a.get('name') or created_by},创建者:{owner_desc}",
                bool(owner and owner.get('role') == 'admin'))
    return (f'未知ID:{created_by}', False)


def delete_ids(data, ids):
    before = len(data['influencers'])
    data['influencers'] = [i for i in data['influencers'] if i.get('id') not in ids]
    removed = before - len(data['influencers'])
    ts = time.strftime('%Y%m%d-%H%M%S')
    shutil.copy(INDEX_FILE, INDEX_FILE + f'.bak-{ts}')
    with open(INDEX_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    for inf_id in ids:
        detail = os.path.join(INFLUENCER_DIR, f'{inf_id}.json')
        if os.path.exists(detail):
            os.remove(detail)
    print(f'已删除 {removed} 条记录，index.json 已备份为 index.json.bak-{ts}')


def main():
    data = load_json(INDEX_FILE, {'influencers': []})
    users = load_json(USERS_FILE, [])
    agents = load_json(AGENTS_FILE, [])
    influencers = data.get('influencers', [])

    args = sys.argv[1:]
    if '--delete' in args:
        ids = [a for a in args[args.index('--delete') + 1:] if not a.startswith('--')]
        if not ids:
            print('错误：--delete 后需要至少一个达人 ID')
            sys.exit(1)
        known = {i.get('id') for i in influencers}
        unknown = [i for i in ids if i not in known]
        if unknown:
            print(f'警告：以下 ID 不存在于达人库: {unknown}')
        delete_ids(data, set(ids) & known)
        return

    rows = []
    for i in influencers:
        desc, is_admin = resolve_creator(i.get('createdBy'), users, agents)
        rows.append((i.get('id'), i.get('name'), desc, is_admin))

    print(f'共 {len(rows)} 条达人记录：')
    for inf_id, name, desc, is_admin in rows:
        tag = 'admin' if is_admin else '非admin⚠️'
        print(f'  [{tag}] {inf_id} | {name} | 录入者: {desc}')

    if '--delete-non-admin' in args:
        targets = {r[0] for r in rows if not r[3]}
        if not targets:
            print('\n没有非 admin 录入的记录，无需清理')
            return
        print(f'\n将删除 {len(targets)} 条非 admin 录入的记录: {sorted(targets)}')
        confirm = input('确认删除？输入 yes 继续: ').strip()
        if confirm == 'yes':
            delete_ids(data, targets)
        else:
            print('已取消')
    else:
        non_admin = [r for r in rows if not r[3]]
        print(f'\n其中非 admin 录入 {len(non_admin)} 条（dry-run，未删除）。')
        print('复核后用 --delete <id> 删除指定记录，或 --delete-non-admin 批量删除非 admin 录入。')


if __name__ == '__main__':
    main()
