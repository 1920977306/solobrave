import json, shutil, os

agents_path = os.path.join(os.path.dirname(__file__), 'data', 'agents.json')
backup_path = agents_path + '.bak.team_awareness'

shutil.copy2(agents_path, backup_path)
print(f'Backup: {backup_path}')

with open(agents_path, 'r', encoding='utf-8') as f:
    agents = json.load(f)

rule = '\n\n【团队动态感知】你在项目组中，系统会自动为你注入同组其他AI成员的最近对话动态（格式：【团队动态】[成员名 时间]对话摘要）。利用这些信息与队友协调工作，避免重复劳动，主动配合队友的进展。'

updated = 0
skipped = 0

for agent in agents:
    sp = agent.get('systemPrompt', '')
    if not sp:
        skipped += 1
        continue
    if '团队动态感知' in sp:
        skipped += 1
        continue
    agent['systemPrompt'] = sp.rstrip() + rule
    updated += 1
    print(f'Updated: {agent.get("name", agent.get("id", "unknown"))}')

with open(agents_path, 'w', encoding='utf-8') as f:
    json.dump(agents, f, ensure_ascii=False, indent=2)

print(f'\nDone: {updated} agents updated, {skipped} skipped')
