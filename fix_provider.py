import json, shutil, os

agents_path = os.path.expanduser('~/Desktop/solobrave-test/data/agents.json')
backup_path = agents_path + '.bak.provider'

# backup
shutil.copy2(agents_path, backup_path)
print(f'Backup: {backup_path}')

with open(agents_path, 'r', encoding='utf-8') as f:
    agents = json.load(f)

changed = []
for a in agents:
    old = a.get('apiProvider', '')
    if old in ('kimi', 'kimi-for-coding', 'kimicode'):
        a['apiProvider'] = 'openclaw'
        changed.append(f"{a.get('name','?')} ({a.get('id','?')}): {old} -> openclaw")
    # also fix aiProvider if set to kimi variants
    old_ai = a.get('aiProvider', '')
    if old_ai in ('kimi', 'kimi-for-coding', 'kimicode'):
        a['aiProvider'] = 'openclaw'
        changed.append(f"{a.get('name','?')} ({a.get('id','?')}): aiProvider {old_ai} -> openclaw")

with open(agents_path, 'w', encoding='utf-8') as f:
    json.dump(agents, f, ensure_ascii=False, indent=2)

print(f'\nChanged {len(changed)} agents:')
for c in changed:
    print(f'  {c}')
print('\nDone.')
