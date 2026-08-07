#!/usr/bin/env python3
"""Fix agents.json: archived flags + toolsDoc influencer→talent + recharge credits"""
import json, os, urllib.request, urllib.error

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
AGENTS_PATH = os.path.join(DATA_DIR, 'agents.json')
BASE_URL = 'http://localhost:8081'

# --- Step 1: Fix agents.json ---
with open(AGENTS_PATH, 'r', encoding='utf-8') as f:
    agents = json.load(f)

changes = []
for a in agents:
    aid = a.get('id', '')
    # Archived: 测试1 and 子龙
    if aid in ('emp_1780653794443_9796', 'emp_zilong'):
        if not a.get('archived'):
            a['archived'] = True
            changes.append(f'  archived=true: {aid} ({a.get("name")})')
    # toolsDoc: /api/influencers → /api/talents
    td = a.get('toolsDoc', '')
    if '/api/influencers' in td:
        old_td = td
        a['toolsDoc'] = td.replace('/api/influencers', '/api/talents')
        changes.append(f'  toolsDoc fix: {aid} ({a.get("name")})')

if changes:
    # Backup
    import shutil
    shutil.copy2(AGENTS_PATH, AGENTS_PATH + '.bak')
    with open(AGENTS_PATH, 'w', encoding='utf-8') as f:
        json.dump(agents, f, ensure_ascii=False, indent=2)
    print('[agents.json] 已修改:')
    for c in changes:
        print(c)
    print(f'[agents.json] 备份: {AGENTS_PATH}.bak')
else:
    print('[agents.json] 无需修改')

# --- Step 2: Recharge credits ---
def api_call(method, path, data=None, token=None):
    url = BASE_URL + path
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    body = json.dumps(data).encode('utf-8') if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode('utf-8'))

# Login
login = api_call('POST', '/api/auth/login', {'username': 'admin', 'password': 'admin123'})
token = login.get('token', '')
if not token:
    print('[ERROR] 登录失败:', login)
else:
    print(f'[登录] 成功, token={token[:20]}...')

# Recharge 4 employees
RECHARGE_LIST = [
    ('emp_1779430403964', '貂蝉'),
    ('emp_1779955656118', '上官婉儿'),
    ('emp_1780132768182', '孔明'),
    ('emp_1780199176680', 'Helen'),
]
AMOUNT = 100000

for aid, name in RECHARGE_LIST:
    result = api_call('POST', '/api/credits/recharge', {'agent_id': aid, 'amount': AMOUNT}, token)
    if 'error' in result or 'detail' in result:
        print(f'[充值] ❌ {name}({aid}): {result}')
    else:
        bal = result.get('balance', '?')
        print(f'[充值] ✅ {name}({aid}): +{AMOUNT}, 余额={bal}')

print('\n完成！接下来需要运行 sync_workspace.py + clear_sessions.py + 网关重启。')
