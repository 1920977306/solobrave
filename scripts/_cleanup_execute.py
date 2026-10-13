# -*- coding: utf-8 -*-
"""知识库存量垃圾清洗 — 第3步「老大确认后执行」。
delete 类软删归档到 trash 表(不物理删, 留30天), 原表删除避免检索召回;
keep_fact/review 不动。幂等: 已进 trash 的 id 跳过。
"""
import sys, os, json, time, sqlite3, importlib.util

BASE = '/Users/qichen/solobrave-prod'
DB = os.path.join(BASE, 'data', 'solobrave.db')
CKPT = os.path.join(BASE, 'scripts', '_classify_ckpt.jsonl')
RETENTION = 30 * 86400  # 30天

sys.path.insert(0, BASE)
spec = importlib.util.spec_from_file_location('sbserver', os.path.join(BASE, 'solobrave-server.py'))
sb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sb)
mp = sb.memory_pipeline

done = {}
for line in open(CKPT):
    try:
        r = json.loads(line)
        done[r['id']] = r
    except Exception:
        pass

# 规则预过滤直接判 delete 的 7 条不在 ckpt 里, 需重放规则补齐
conn0 = sb._db_conn()
rule_extra = {}
for r in conn0.execute("SELECT id, emp_id, value FROM memory WHERE status='active' AND is_filler=0"):
    rule = mp.rule_classify_message((r['value'] or '').strip())
    if rule and r['id'] not in done:
        rule_extra[r['id']] = {'verdict': 'delete', 'cat': rule[0],
                               'conf': 1.0, 'reason': '规则: ' + rule[1]}
for r in conn0.execute("SELECT id, pattern_text FROM knowledge_patterns WHERE status='draft'"):
    rule = mp.rule_classify_message((r['pattern_text'] or '').strip())
    if rule and r['id'] not in done:
        rule_extra[r['id']] = {'verdict': 'delete', 'cat': rule[0],
                               'conf': 1.0, 'reason': '规则: ' + rule[1]}
conn0.close()
if rule_extra:
    print(f'rule-prefilter deletes not in ckpt: {len(rule_extra)}', flush=True)
    done.update(rule_extra)

mem_del = [rid for rid, r in done.items()
           if r['verdict'] == 'delete' and rid.startswith('mem_')]
ptn_del = [rid for rid, r in done.items()
           if r['verdict'] == 'delete' and not rid.startswith('mem_')]
print(f'delete targets: memory={len(mem_del)}, pattern={len(ptn_del)}', flush=True)

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
conn.execute('''CREATE TABLE IF NOT EXISTS trash (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    original_id TEXT NOT NULL,
    emp_id TEXT DEFAULT '',
    payload TEXT NOT NULL,
    verdict_cat TEXT DEFAULT '',
    reason TEXT DEFAULT '',
    deleted_at INTEGER,
    restore_before INTEGER)''')
conn.execute('CREATE INDEX IF NOT EXISTS idx_trash_kind ON trash(kind, original_id)')

already = {row['original_id'] for row in conn.execute('SELECT original_id FROM trash')}
mem_del = [i for i in mem_del if i not in already]
ptn_del = [i for i in ptn_del if i not in already]
print(f'after idempotency filter: memory={len(mem_del)}, pattern={len(ptn_del)}', flush=True)

now = int(time.time())

def archive(kind, table, ids):
    n = 0
    for rid in ids:
        row = conn.execute(f'SELECT * FROM {table} WHERE id=?', (rid,)).fetchone()
        if not row:
            print(f'  WARN missing {kind} {rid}', flush=True)
            continue
        r = done.get(rid, {})
        d = dict(row)
        for k, v in list(d.items()):
            if isinstance(v, bytes):
                import base64
                d[k] = '__b64__:' + base64.b64encode(v).decode()
        conn.execute(
            'INSERT INTO trash (kind, original_id, emp_id, payload, verdict_cat, reason, deleted_at, restore_before)'
            ' VALUES (?,?,?,?,?,?,?,?)',
            (kind, rid, row['emp_id'] if kind == 'memory' else '',
             json.dumps(d, ensure_ascii=False),
             r.get('cat', ''), r.get('reason', ''), now, now + RETENTION))
        conn.execute(f'DELETE FROM {table} WHERE id=?', (rid,))
        conn.execute(
            'INSERT INTO kb_operation_log (id, entry_id, operation, operator_id, details, created_at)'
            ' VALUES (?,?,?,?,?,?)',
            (f'trash-{kind}-{rid}', rid, f'trash_{kind}',
             'cleanup-batch-20261012',
             json.dumps({'cat': r.get('cat'), 'reason': r.get('reason')}, ensure_ascii=False), now))
        n += 1
    return n

conn.execute('BEGIN')
try:
    nm = archive('memory', 'memory', mem_del)
    np_ = archive('pattern', 'knowledge_patterns', ptn_del)
    conn.commit()
except Exception:
    conn.rollback()
    raise

left_m = conn.execute("SELECT count(*) c FROM memory WHERE status='active' AND is_filler=0").fetchone()['c']
left_p = conn.execute("SELECT count(*) c FROM knowledge_patterns WHERE status='draft'").fetchone()['c']
trash_n = conn.execute('SELECT count(*) c FROM trash').fetchone()['c']
conn.close()
print(json.dumps({'archived_memory': nm, 'archived_pattern': np_,
                  'memory_active_left': left_m, 'pattern_draft_left': left_p,
                  'trash_total': trash_n}, ensure_ascii=False))
