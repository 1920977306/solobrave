#!/usr/bin/env python3
"""重建贺主管和荔枝用户账号"""
import json, os, uuid, hashlib
from datetime import datetime

USERS_PATH = os.path.join(os.path.dirname(__file__), 'data', 'users.json')

with open(USERS_PATH, 'r', encoding='utf-8') as f:
    users = json.load(f)

def hash_password(password, salt=None):
    if salt is None:
        salt = uuid.uuid4().hex[:16]
    h = hashlib.sha256((salt + password).encode('utf-8')).hexdigest()
    return h, salt

# 检查是否已存在
existing_ids = {u['id'] for u in users}
existing_usernames = {u['username'] for u in users}

new_users = []

# 贺主管 (Helen的创建者)
if 'user_30f05078' not in existing_ids:
    pwd_hash, salt = hash_password('ayn123')
    new_users.append({
        'id': 'user_30f05078',
        'username': 'ayn',
        'passwordHash': pwd_hash,
        'passwordSalt': salt,
        'role': 'employee',
        'displayName': '贺主管',
        'avatar': 19,
        'agentQuota': 5,
        'apiQuota': 10000,
        'createdAt': '2026-05-31T11:46:18.146923',
        'teamIds': [],
        'subordinateIds': [],
        'roleTemplateId': None,
        'status': 'active',
        'lastLoginAt': None
    })
    print('已添加: 贺主管 (user_30f05078, username=ayn, password=ayn123)')

# 荔枝
if 'user_c6eac88f' not in existing_ids:
    pwd_hash, salt = hash_password('lizhi123')
    new_users.append({
        'id': 'user_c6eac88f',
        'username': 'lizhi',
        'passwordHash': pwd_hash,
        'passwordSalt': salt,
        'role': 'employee',
        'displayName': '荔枝',
        'avatar': 12,
        'agentQuota': 5,
        'apiQuota': 10000,
        'createdAt': '2026-06-20T10:00:00.000000',
        'teamIds': [],
        'subordinateIds': [],
        'roleTemplateId': None,
        'status': 'active',
        'lastLoginAt': None
    })
    print('已添加: 荔枝 (user_c6eac88f, username=lizhi, password=lizhi123)')

if new_users:
    users.extend(new_users)
    with open(USERS_PATH, 'w', encoding='utf-8') as f:
        json.dump(users, f, ensure_ascii=False, indent=2)
    print(f'\n=== 完成，users.json 现有 {len(users)} 个用户 ===')
else:
    print('所有用户已存在，无需更新')
