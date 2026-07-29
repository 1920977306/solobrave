import json, shutil

path = '/Users/qichen/Desktop/solobrave-test/data/agents.json'
shutil.copy2(path, path + '.bak.empid')

with open(path, 'r', encoding='utf-8') as f:
    agents = json.load(f)

# (old, new) pairs - applied to systemPrompt, soulDoc, toolsDoc
replacements = [
    # === Curl search payloads: replace projectId with empId ===
    ('"query":"来源：孔明","projectId":"grp_1779608571712"', '"query":"业务分析","empId":"emp_1780132768182"'),
    ('"query":"来源：Helen","projectId":"grp_1779608571712"', '"query":"跟进结果","empId":"emp_1780199176680"'),
    ('"query":"来源：上官婉儿","projectId":"grp_1779608571712"', '"query":"进度汇总","empId":"emp_1779955656118"'),
    ('"query":"[分析]","projectId":"grp_1779608571712"', '"query":"[分析]","empId":"emp_1780132768182"'),
    ('"query":"[跟进结果]","projectId":"grp_1779608571712"', '"query":"[跟进结果]","empId":"emp_1780199176680"'),

    # === soulDoc/toolsDoc descriptive text (try both Chinese and ASCII quotes) ===
    ('\u201c\u6765\u6e90\uff1a\u5b54\u660e\u201d', '\u5b54\u660e\u7684\u5206\u6790\uff08\u7528empId\u8fc7\u6ee4\uff09'),
    ('\u201c\u6765\u6e90\uff1aHelen\u201d', 'Helen\u7684\u8ddf\u8fdb\u7ed3\u679c\uff08\u7528empId\u8fc7\u6ee4\uff09'),
    ('\u201c\u6765\u6e90\uff1a\u4e0a\u5b98\u5a49\u513f\u201d', '\u4e0a\u5b98\u5a49\u513f\u7684\u8fdb\u5ea6\u6c47\u603b\uff08\u7528empId\u8fc7\u6ee4\uff09'),
    ('"\u6765\u6e90\uff1a\u5b54\u660e"', '\u5b54\u660e\u7684\u5206\u6790\uff08\u7528empId\u8fc7\u6ee4\uff09'),
    ('"\u6765\u6e90\uff1aHelen"', 'Helen\u7684\u8ddf\u8fdb\u7ed3\u679c\uff08\u7528empId\u8fc7\u6ee4\uff09'),
    ('"\u6765\u6e90\uff1a\u4e0a\u5b98\u5a49\u513f"', '\u4e0a\u5b98\u5a49\u513f\u7684\u8fdb\u5ea6\u6c47\u603b\uff08\u7528empId\u8fc7\u6ee4\uff09'),

    # === toolsDoc rule text ===
    ('\u641c\u7d22\u548c\u5b58\u50a8\u77e5\u8bc6\u5e93\u5fc5\u987b\u5e26 projectId \u53c2\u6570\uff0c\u53ea\u770b\u540c\u9879\u76ee\u7ec4\u7684\u5185\u5bb9', '\u641c\u7d22\u77e5\u8bc6\u5e93\u7528empId\u8fc7\u6ee4\u961f\u53cb\u5185\u5bb9\uff0c\u5b58\u50a8\u65f6\u5e26projectId'),
    ('\u5148\u67e5\u9879\u76ee\u7ec4\u62ffprojectId\uff0c\u518d\u641c\u7d22/\u5b58\u50a8\u77e5\u8bc6\u5e93', '\u641c\u7d22\u77e5\u8bc6\u5e93\u7528empId\u8fc7\u6ee4\uff0c\u5b58\u50a8\u65f6\u5e26projectId'),
]

count = 0
for agent in agents:
    for field in ['systemPrompt', 'soulDoc', 'toolsDoc', 'idDoc', 'userDoc']:
        if field not in agent or not agent[field]:
            continue
        for old, new in replacements:
            if old in agent[field]:
                count += agent[field].count(old)
                agent[field] = agent[field].replace(old, new)

with open(path, 'w', encoding='utf-8') as f:
    json.dump(agents, f, ensure_ascii=False, indent=2)

print(f'Done! {count} replacements made. Backup: {path}.bak.empid')
