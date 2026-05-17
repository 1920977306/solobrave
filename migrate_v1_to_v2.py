#!/usr/bin/env python3
"""
V1 → V2 数据迁移脚本
===========================
功能：
1. 为现有用户添加新字段（teamIds, subordinateIds, status, lastLoginAt, roleTemplateId）
2. 创建默认的 teams.json
3. 备份原始数据到 ~/.solobrave-data/backup_v1/

执行方式：python3 migrate_v1_to_v2.py
"""

import json
import os
import shutil
from datetime import datetime

# 数据目录
DATA_DIR = os.path.join(os.path.expanduser('~'), '.solobrave-data')
BACKUP_DIR = os.path.join(DATA_DIR, 'backup_v1')
USERS_FILE = os.path.join(DATA_DIR, 'users.json')
TEAMS_FILE = os.path.join(DATA_DIR, 'teams.json')
AGENTS_FILE = os.path.join(DATA_DIR, 'agents.json')


def ensure_dir(path):
    """确保目录存在"""
    os.makedirs(path, exist_ok=True)


def read_json(filepath, default=None):
    """读取 JSON 文件"""
    if not os.path.isfile(filepath):
        return default if default is not None else None
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default if default is not None else None


def write_json(filepath, data):
    """写入 JSON 文件"""
    ensure_dir(os.path.dirname(filepath))
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def backup_file(filepath):
    """备份文件到 backup_v1 目录"""
    if os.path.isfile(filepath):
        ensure_dir(BACKUP_DIR)
        filename = os.path.basename(filepath)
        backup_path = os.path.join(BACKUP_DIR, filename)
        # 如果已存在备份，加时间戳
        if os.path.exists(backup_path):
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_path = os.path.join(BACKUP_DIR, f'{filename}.{ts}')
        shutil.copy2(filepath, backup_path)
        print(f'  ✅ 备份: {filename} → {backup_path}')
        return True
    return False


def migrate_users():
    """迁移用户数据"""
    print('\n📦 迁移 users.json...')
    
    users = read_json(USERS_FILE, [])
    if not isinstance(users, list):
        print('  ⚠️ users.json 格式错误，跳过')
        return
    
    if len(users) == 0:
        print('  ℹ️ 没有用户数据，跳过')
        return
    
    migrated = 0
    for user in users:
        changed = False
        
        # 添加 teamIds
        if 'teamIds' not in user:
            user['teamIds'] = []
            changed = True
        
        # 添加 subordinateIds
        if 'subordinateIds' not in user:
            user['subordinateIds'] = []
            changed = True
        
        # 添加 roleTemplateId
        if 'roleTemplateId' not in user:
            user['roleTemplateId'] = None
            changed = True
        
        # 添加 status
        if 'status' not in user:
            user['status'] = 'active'
            changed = True
        
        # 添加 lastLoginAt
        if 'lastLoginAt' not in user:
            user['lastLoginAt'] = None
            changed = True
        
        # 确保 role 是有效的（添加 leader）
        if 'role' in user and user['role'] not in ('admin', 'leader', 'employee'):
            user['role'] = 'employee'
            changed = True
        
        if changed:
            migrated += 1
    
    if migrated > 0:
        backup_file(USERS_FILE)
        write_json(USERS_FILE, users)
        print(f'  ✅ 迁移完成: {migrated}/{len(users)} 个用户已更新')
    else:
        print(f'  ℹ️ 所有用户已是最新格式')


def migrate_teams():
    """迁移/创建 teams.json"""
    print('\n📦 迁移/创建 teams.json...')
    
    # 检查是否已有 teams.json
    existing_teams = read_json(TEAMS_FILE, [])
    if isinstance(existing_teams, list) and len(existing_teams) > 0:
        print(f'  ℹ️ teams.json 已存在，包含 {len(existing_teams)} 个小组，跳过')
        return
    
    # 创建默认 teams.json
    teams = []
    backup_file(TEAMS_FILE)
    write_json(TEAMS_FILE, teams)
    print(f'  ✅ 创建空的 teams.json')


def migrate_agents():
    """确保 agents.json 中的 agentIds 被正确设置"""
    print('\n📦 检查 agents.json...')
    
    agents = read_json(AGENTS_FILE, [])
    if not isinstance(agents, list):
        print('  ⚠️ agents.json 格式错误，跳过')
        return
    
    # 简单检查，不需要修改
    print(f'  ℹ️ agents.json 包含 {len(agents)} 个 agents')


def main():
    print('=' * 56)
    print('  SoloBrave V1 → V2 数据迁移')
    print('=' * 56)
    print(f'\n📂 数据目录: {DATA_DIR}')
    
    # 确保目录存在
    ensure_dir(DATA_DIR)
    
    # 创建备份目录
    ensure_dir(BACKUP_DIR)
    print(f'📂 备份目录: {BACKUP_DIR}')
    
    # 执行迁移
    migrate_users()
    migrate_teams()
    migrate_agents()
    
    print('\n' + '=' * 56)
    print('  ✅ 迁移完成！')
    print('=' * 56)
    print('''
📋 迁移内容：
  • users.json - 添加新字段（teamIds, subordinateIds, status, lastLoginAt, roleTemplateId）
  • teams.json - 创建/保留现有小组数据
  • 原始文件已备份到 backup_v1/ 目录

⚠️ 注意事项：
  • 迁移前请确保后端服务器已停止
  • 如果迁移失败，可从 backup_v1/ 恢复
  • leader 角色需要在管理界面手动分配小组
    ''')


if __name__ == '__main__':
    main()
