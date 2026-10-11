#!/usr/bin/env python3
"""
SoloBrave 存量 pending 摘要清洗（调研 §5.5: AI知识库记忆提炼机制 2026-10-11）

背景:
  memory_summary 表 498 条 pending 积压 —— 审核对象是自由文本摘要, 人工无法逐条看。
  本脚本用 glm-4-flash 批量价值分类:
    高价值(FACT/PREFERENCE/DECISION/CORRECTION/EVENT/INSIGHT 且 confidence≥0.7) → status='active'
    边界(0.4-0.7) → 保持 pending
    低价值(CHITCHAT/NOISE/TASK 或 confidence<0.4) → status='archived' (不删除, 不参与检索)

用法:
  python3 scripts/clean_pending_summaries.py            # 全量清洗
  python3 scripts/clean_pending_summaries.py --dry-run  # 只分类不写库
  python3 scripts/clean_pending_summaries.py --limit 50 # 先跑一批看准确率

LLM 配置链与 _resolve_induce_llm_config 对齐:
  1. data/settings.json 的 llm 字段
  2. SOLOBRAVE_AI_* 环境变量
  3. data/agents.json 第一个有效 key 的员工
"""

import json
import os
import sqlite3
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, 'data', 'solobrave.db')
BATCH = 10

CLASSIFY_PROMPT = """你是知识库价值判定器。对下面每条「会话摘要」判断知识价值，分类为：
FACT(达人/商品/业务具体事实) / PREFERENCE(偏好约束) / DECISION(业务决策) / CORRECTION(对已有知识的修正) / EVENT(具体事件) / INSIGHT(规律性发现) / TASK(纯任务指令无沉淀) / CHITCHAT(寒暄客套) / NOISE(无信息量)。
只返回 JSON 数组，每项: {{"idx": 序号, "category": "类别", "confidence": 0.0-1.0}}

摘要列表：
{items}"""


def is_empty_shell(row):
    """空壳工单判定: 自动创建的每日归纳从未被生成器填充（生成器已不存在），
    key_points/decisions/action_items 全空 → 无需 LLM，直接归档（调研 §5.5 的确定性预过滤）。"""

    def _empty(v):
        s = str(v or '').strip()
        return not s or s == '[]'

    return (_empty(row['key_points']) and _empty(row['decisions']) and _empty(row['action_items']))


def _load_dotenv():
    """与 server._load_dotenv 同款: 项目根 .env → os.environ（不覆盖已有变量）"""
    env_file = os.path.join(BASE, '.env')
    if not os.path.isfile(env_file):
        return
    for line in open(env_file, encoding='utf-8'):
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        k, v = k.strip(), v.strip()
        if k and k not in os.environ:
            os.environ[k] = v


def resolve_llm_config():
    """与 server._resolve_induce_llm_config 同款配置链"""
    _load_dotenv()  # .env 优先（与服务器行为一致）
    try:
        settings = json.load(open(os.path.join(BASE, 'data', 'settings.json')))
        llm = (settings or {}).get('llm') or {}
        if isinstance(llm, dict):
            # 新版形态: {mode, providers: [{name, apiKey, baseUrl, model, priority}]}
            providers = llm.get('providers') or []
            if isinstance(providers, list):
                for p in sorted((x for x in providers if isinstance(x, dict)),
                                key=lambda x: x.get('priority', 99)):
                    if (p.get('apiKey') or '').strip():
                        return {
                            'provider': (p.get('name') or 'zhipu').strip(),
                            'apiKey': p['apiKey'].strip(),
                            'model': (p.get('model') or 'glm-4-flash').strip(),
                            'baseUrl': (p.get('baseUrl') or 'https://open.bigmodel.cn/api/paas/v4/').strip(),
                        }
            if (llm.get('apiKey') or '').strip():
                return {
                    'provider': (llm.get('provider') or 'zhipu').strip(),
                    'apiKey': llm['apiKey'].strip(),
                    'model': (llm.get('model') or 'glm-4-flash').strip(),
                    'baseUrl': (llm.get('baseUrl') or 'https://open.bigmodel.cn/api/paas/v4/').strip(),
                }
    except Exception:
        pass
    ai_key = os.environ.get('SOLOBRAVE_AI_API_KEY', '').strip()
    if ai_key:
        return {
            'provider': os.environ.get('SOLOBRAVE_AI_PROVIDER', 'zhipu').strip(),
            'apiKey': ai_key,
            'model': os.environ.get('SOLOBRAVE_AI_MODEL', 'glm-4-flash').strip(),
            'baseUrl': os.environ.get('SOLOBRAVE_AI_BASE_URL', 'https://open.bigmodel.cn/api/paas/v4/').strip(),
        }
    try:
        agents = json.load(open(os.path.join(BASE, 'data', 'agents.json')))
        for a in agents or []:
            key = (a.get('apiKey') or '').strip()
            if key:
                return {
                    'provider': a.get('aiProvider', '') or a.get('apiProvider', '') or 'zhipu',
                    'apiKey': key,
                    'model': a.get('apiModel', '') or 'glm-4-flash',
                    'baseUrl': a.get('customEndpoint', '') or 'https://open.bigmodel.cn/api/paas/v4/',
                }
    except Exception:
        pass
    return None


def call_llm(cfg, prompt):
    """OpenAI 兼容 chat/completions，返回文本或 None"""
    url = cfg['baseUrl'].rstrip('/') + '/chat/completions'
    body = json.dumps({
        'model': cfg['model'],
        'temperature': 0.1,
        'max_tokens': 800,
        'messages': [{'role': 'user', 'content': prompt}],
    }).encode('utf-8')
    req = urllib.request.Request(url, data=body, headers={
        'Content-Type': 'application/json',
        'Authorization': f"Bearer {cfg['apiKey']}",
    }, method='POST')
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    choices = data.get('choices') or []
    if not choices:
        return None
    return (choices[0].get('message') or {}).get('content')


def parse_json_array(raw):
    if not raw:
        return []
    text = raw.strip()
    if text.startswith('```'):
        text = text.split('```')[1]
        if text.startswith('json'):
            text = text[4:]
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, list) else []
    except json.JSONDecodeError:
        pass
    # 容错: 截取首个 [ 到最后一个 ]
    i, j = text.find('['), text.rfind(']')
    if i >= 0 and j > i:
        try:
            obj = json.loads(text[i:j + 1])
            return obj if isinstance(obj, list) else []
        except json.JSONDecodeError:
            return []
    return []


def summary_text(row):
    parts = [row['title'] or '']
    for col in ('key_points', 'decisions', 'action_items', 'pending'):
        v = row[col]
        if v:
            parts.append(str(v))
    return ' '.join(parts)[:400]


def main():
    dry_run = '--dry-run' in sys.argv
    limit = 0
    if '--limit' in sys.argv:
        limit = int(sys.argv[sys.argv.index('--limit') + 1])

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    sql = "SELECT * FROM memory_summary WHERE status = 'pending' ORDER BY created_at"
    rows = conn.execute(sql).fetchall()
    if limit:
        rows = rows[:limit]
    print(f'待清洗 pending: {len(rows)} 条{" (dry-run)" if dry_run else ""}')

    stats = {'active': 0, 'archived': 0, 'keep_pending': 0, 'error': 0, 'empty_shell': 0}

    # 第一遍: 空壳工单确定性归档（不调 LLM）
    remaining = []
    for r in rows:
        if is_empty_shell(r):
            stats['empty_shell'] += 1
            if not dry_run:
                conn.execute("UPDATE memory_summary SET status='archived' WHERE id=?", (r['id'],))
        else:
            remaining.append(r)
    if not dry_run:
        conn.commit()
    print(f'  空壳工单(确定性归档): {stats["empty_shell"]}, 进入 LLM 分类: {len(remaining)}')
    rows = remaining
    if not rows:
        print(f'\n完成: {stats}')
        conn.close()
        return

    cfg = resolve_llm_config()
    if not cfg:
        print('剩余摘要有内容但无可用 LLM 配置（settings.llm providers / SOLOBRAVE_AI_* / agents.json）')
        sys.exit(1)
    print(f"LLM: {cfg['provider']}/{cfg['model']} via {cfg['baseUrl']}")

    done = 0
    for bi in range(0, len(rows), BATCH):
        batch = rows[bi:bi + BATCH]
        items = '\n'.join(f"{i}. {summary_text(r)[:300]}" for i, r in enumerate(batch))
        result = []
        for attempt in range(2):
            try:
                raw = call_llm(cfg, CLASSIFY_PROMPT.format(items=items))
                result = parse_json_array(raw)
                if result:
                    break
            except Exception as e:
                print(f'  批次 {bi // BATCH + 1} 第 {attempt + 1} 次调用失败: {e}')
                time.sleep(3)
        verdict = {}
        for item in result:
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item.get('idx', -1))
                cat = str(item.get('category', '')).upper().strip()
                conf = float(item.get('confidence', 0.5) or 0.5)
            except (TypeError, ValueError):
                continue
            if cat in ('FACT', 'PREFERENCE', 'DECISION', 'CORRECTION', 'EVENT', 'INSIGHT') and conf >= 0.7:
                verdict[idx] = 'active'
            elif cat in ('CHITCHAT', 'NOISE', 'TASK') or conf < 0.4:
                verdict[idx] = 'archived'
            else:
                verdict[idx] = 'keep_pending'

        # 模型常以 1-based 返回序号（prompt 列表是 1. 2. 3.）——检测到 1-based 就归一化
        if verdict and 0 not in verdict and min(verdict) >= 1 and max(verdict) <= len(batch):
            verdict = {k - 1: v for k, v in verdict.items()}

        for i, r in enumerate(batch):
            v = verdict.get(i)
            if v is None:
                stats['error'] += 1
                continue
            if v == 'keep_pending':
                stats['keep_pending'] += 1
                continue
            stats[v] += 1
            if not dry_run:
                conn.execute('UPDATE memory_summary SET status = ? WHERE id = ?', (v, r['id']))
        if not dry_run:
            conn.commit()
        done += len(batch)
        print(f'  进度 {done}/{len(rows)}: {stats}')
        time.sleep(0.5)  # 温和限速

    print(f'\n完成: {stats}')
    conn.close()


if __name__ == '__main__':
    main()
