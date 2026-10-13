# -*- coding: utf-8 -*-
"""知识库存量垃圾清洗 — 第2步「只分类不删除」。
用 Phase 1 同款 glm-4-flash 价值判定器给存量打标: delete / keep_fact / review。
产出清单 markdown 供老大过目; 本脚本零写库操作。
"""
import sys, os, json, time, importlib.util, re

BASE = '/Users/qichen/solobrave-prod'
OUT = '/Users/qichen/Documents/kimi/tasks/2026-09-30/15-47-22-6b6fc1a8/cleanup-classify-list.md'
sys.path.insert(0, BASE)

spec = importlib.util.spec_from_file_location('sbserver', os.path.join(BASE, 'solobrave-server.py'))
sb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sb)
mp = sb.memory_pipeline

# 包装: 默认 timeout=30 对 8 条一批的分类生成不够用(实测 read timeout),
# 换成 120s 长超时; 闭包内 _call_chat_completion 是模块全局查找, 猴子补丁即可生效
_orig_ccc = sb._call_chat_completion
def _ccc_long_timeout(provider, key, model, endpoint, messages, timeout=30, max_tokens=1000, **kw):
    return _orig_ccc(provider, key, model, endpoint, messages, timeout=120, max_tokens=4000, **kw)
sb._call_chat_completion = _ccc_long_timeout

classify_call = sb._memory_value_classify_call()
assert classify_call, 'glm-4-flash 配置缺失, 无法分类'

conn = sb._db_conn()
mem_rows = conn.execute(
    "SELECT id, emp_id, value FROM memory WHERE status='active' AND is_filler=0 ORDER BY created_at"
).fetchall()
ptn_rows = conn.execute(
    "SELECT id, pattern_text FROM knowledge_patterns WHERE status='draft' ORDER BY created_at"
).fetchall()
conn.close()

TARGETS = []
for r in mem_rows:
    TARGETS.append({'kind': 'memory', 'id': r['id'], 'emp': r['emp_id'], 'text': (r['value'] or '').strip()})
for r in ptn_rows:
    TARGETS.append({'kind': 'pattern', 'id': r['id'], 'emp': '', 'text': (r['pattern_text'] or '').strip()})

print(f'targets: memory={len(mem_rows)}, pattern={len(ptn_rows)}, total={len(TARGETS)}')

KEEP_CATS = {'FACT', 'PREFERENCE', 'DECISION', 'CORRECTION', 'EVENT', 'INSIGHT'}
DROP_CATS = {'CHITCHAT', 'NOISE', 'TASK'}

PROMPT = """你是知识库价值判定器。对下面每条内容判断知识价值，分类为：
FACT(达人/商品/业务具体事实) / PREFERENCE(偏好约束) / DECISION(业务决策) / CORRECTION(修正) / EVENT(具体事件) / INSIGHT(规律发现) / TASK(任务指令) / CHITCHAT(寒暄客套) / NOISE(无信息量)。
只返回 JSON 数组，每项: {{"idx": 序号, "category": "类别", "confidence": 0.0-1.0, "reason": "判定理由,10字内"}}
拿不准的给低 confidence。

内容列表：
{items}"""

# ① 规则预过滤(零成本) + 断点续跑(已分类的跳过)
CKPT = os.path.join(BASE, 'scripts', '_classify_ckpt.jsonl')
done = {}
if os.path.exists(CKPT):
    with open(CKPT) as f:
        for line in f:
            try:
                r = json.loads(line)
                done[r['id']] = r
            except Exception:
                pass
print(f'checkpoint resume: {len(done)} already classified', flush=True)

need_llm = []
for t in TARGETS:
    d = done.get(t['id'])
    if d and d.get('cat') != 'UNPARSED':
        t.update(d)
        continue
    rule = mp.rule_classify_message(t['text'])
    if rule:
        t['verdict'] = 'delete'
        t['cat'] = rule[0]
        t['conf'] = 1.0
        t['reason'] = '规则: ' + rule[1]
    else:
        need_llm.append(t)
print(f'rule-filtered: {len(TARGETS) - len(need_llm) - len(done)}, need LLM: {len(need_llm)}', flush=True)

# ② LLM 批分类
_OBJ_RE = re.compile(r'\{[^{}]*"idx"[^{}]*\}', re.DOTALL)
def parse_arr(raw):
    """逐对象解析, 容忍整体截断: 能捞出几个算几个(每项自带 idx, 丢的可重跑)。"""
    out = []
    for m in _OBJ_RE.finditer(raw or ''):
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict) and 'idx' in obj:
                out.append(obj)
        except Exception:
            continue
    return out

BATCH = 8

def run_batch(arg):
    start, batch = arg
    cleaned = [re.sub(r'\s+', ' ', t['text'])[:150] for t in batch]
    items = '\n'.join(f"{i}. {t}" for i, t in enumerate(cleaned))
    raw = None
    for attempt in (1, 2, 3, 4, 5):
        try:
            raw = classify_call(PROMPT.format(items=items))
            break
        except Exception as e:
            print(f'  batch {start} attempt {attempt} failed: {type(e).__name__}', flush=True)
            time.sleep(15)
    arr = parse_arr(raw)
    by_idx = {}
    for pos, it in enumerate(arr):
        if isinstance(it, dict):
            try:
                by_idx[int(it.get('idx', -1))] = it
            except (TypeError, ValueError):
                continue
        elif isinstance(it, str):
            # 模型偷懒返回纯字符串数组时, 按位置对位
            if pos < len(batch):
                by_idx[pos] = {'category': it, 'confidence': 0.7, 'reason': ''}
    # 1-based 归一化
    if by_idx and 0 not in by_idx and min(by_idx) >= 1 and max(by_idx) <= len(batch):
        by_idx = {k - 1: v for k, v in by_idx.items()}
    results = []
    for i, t in enumerate(batch):
        it = by_idx.get(i)
        if not it:
            results.append({'id': t['id'], 'verdict': 'review', 'cat': 'UNPARSED', 'conf': 0.0, 'reason': 'LLM 未返回该项, 转人工'})
            continue
        cat = str(it.get('category', '')).upper()
        try:
            conf = float(it.get('confidence', 0.5) or 0.5)
        except (TypeError, ValueError):
            conf = 0.5
        reason = str(it.get('reason', ''))[:60]
        if cat in DROP_CATS:
            verdict = 'delete'
        elif cat in KEEP_CATS:
            verdict = 'keep_fact' if conf >= 0.6 else 'review'
        else:
            verdict = 'review'
        results.append({'id': t['id'], 'verdict': verdict, 'cat': cat, 'conf': conf, 'reason': reason or cat})
    return start, results

batches = [(s, need_llm[s:s + BATCH]) for s in range(0, len(need_llm), BATCH)]
all_results = {}
_ckpt_f = open(CKPT, 'a')
from concurrent.futures import ThreadPoolExecutor
with ThreadPoolExecutor(max_workers=2) as ex:
    for start, results in ex.map(run_batch, batches):
        for r in results:
            all_results[r['id']] = r
            _ckpt_f.write(json.dumps(r, ensure_ascii=False) + '\n')
        _ckpt_f.flush()
        print(f'  batch {start} done ({len(all_results)}/{len(TARGETS)})', flush=True)
_ckpt_f.close()

# 结果回填(增量断点已落盘, 此处只合并内存)
for t in TARGETS:
    r = all_results.get(t['id']) or done.get(t['id'])
    if r:
        t.update(r)
errors = sum(1 for r in all_results.values() if r['cat'] == 'UNPARSED')

# ③ 产出清单
groups = {'delete': [], 'keep_fact': [], 'review': []}
for t in TARGETS:
    groups[t['verdict']].append(t)

lines = []
lines.append('# 知识库存量清洗分类清单（只分类, 未删除任何东西）')
lines.append('')
lines.append(f'- 扫描: 记忆 {len(mem_rows)} 条(active 且未标碎片) + draft 规律 {len(ptn_rows)} 条 = {len(TARGETS)} 条')
lines.append(f'- 规则预过滤: {len(TARGETS) - len(need_llm)} 条 | LLM 判定: {len(need_llm)} 条 | LLM 未返回转人工: {errors} 条')
lines.append(f'- 结果: **delete {len(groups["delete"])}** / **keep_fact {len(groups["keep_fact"])}** / **review {len(groups["review"])}**')
lines.append(f'- 数据库备份: data/solobrave-backup-20261012-192929.db (integrity ok, 行数已核对)')
lines.append('')
labels = {'delete': ('❌ delete（建议删除/归档）', '按 CHITCHAT/NOISE/TASK 或无信息量判定'),
          'keep_fact': ('✅ keep_fact（业务事实, 保留进知识层）', '达人/商品/决策/修正/规律类事实'),
          'review': ('🟡 review（拿不准, 标红等人工逐条看）', '低置信度或解析失败, pending 不动')}
for key in ('delete', 'keep_fact', 'review'):
    title, desc = labels[key]
    lines.append(f'## {title} — {len(groups[key])} 条')
    lines.append(f'_{desc}_')
    lines.append('')
    lines.append('| # | 类型 | ID | 内容前100字 | 类别 | 置信度 | 理由 |')
    lines.append('|---|---|---|---|---|---|---|')
    for i, t in enumerate(groups[key], 1):
        text = t['text'][:100].replace('|', '\\|').replace('\n', ' ')
        lines.append(f"| {i} | {t['kind']} | {t['id']} | {text} | {t.get('cat', '')} | {t.get('conf', '')} | {str(t.get('reason', '')).replace('|', '/')} |")
    lines.append('')

with open(OUT, 'w') as f:
    f.write('\n'.join(lines))
print('written:', OUT)
print(json.dumps({k: len(v) for k, v in groups.items()}))
