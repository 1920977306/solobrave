#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""达人画像批量回填知识库（talents 表 → kb_entries/kb_entry_chunks, embedding-2 向量化）。

背景（老大 2026-10-11 拍板「提升 AI 对达人的分析 · 第①层 数据注入」）:
  Ray 等员工私聊分析达人时是「盲分析」——纯文本进出无工具调用, 只能凭 RAG 检索命中。
  达人表 139 列但 AI 生成字段近空, 知识库里没有按达人组织的结构化画像,
  导致「检索没命中就瞎编」。本脚本把每个达人已有字段渲染成结构化中文画像入 kb。

设计:
  - 一达人一条目: title='达人画像：{name}', id='kb_talent_{talent_id}'
  - 只写有值字段(空/0/空JSON 跳过), 不造「未提供」噪音, 保证向量干净
  - scope='global' 全员可读 / status='ok' / category='达人库'
  - 复用 knowledge_service 现成管道: _save_kb_chunks_without_embedding(500,100)
    + _vectorize_kb_chunks(get_embedding_config(None)) → .env override 落到 zhipu embedding-2
  - 幂等可重跑: 条目已存在 / chunk 已有 embedding-2 向量 → SKIP
    (中断后重跑自动续, 配合 timeout 分段执行)

用法:
  python3 scripts/backfill_talent_profiles_kb.py               # dry-run 打印将建条目
  python3 scripts/backfill_talent_profiles_kb.py --execute     # 真写入
  python3 scripts/backfill_talent_profiles_kb.py --execute --only <talent_id>
"""
import argparse
import json
import os
import sqlite3
import sys
import time
import traceback

BASE = os.environ.get('SOLOBRAVE_BASE',
                       os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(BASE, 'data', 'solobrave.db')

sys.path.insert(0, BASE)
import importlib.util
_spec = importlib.util.spec_from_file_location(
    'solobrave_server', os.path.join(BASE, 'solobrave-server.py'))
_server_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_server_mod)
_server_mod._load_dotenv(os.path.join(BASE, '.env'))
import knowledge_service as ks

CREATED_BY = 'backfill-talent-profiles'
CATEGORY_NAME = '达人库'

# (字段, 中文标签)。只列出对分析有用的; 联系方式类(phone/wechat/email)刻意排除——
# 画像给所有员工检索读, 联系方式属敏感信息, 留在达人详情页按需看。
FIELD_MAP = [
    ('platform', '平台'), ('douyin_id', '平台ID'), ('city', '城市'), ('location', '所在地'),
    ('talent_type', '达人类型'), ('category', '主推类目'), ('main_category', '主力类目'),
    ('content_style', '内容风格'), ('bio', '简介'), ('tags', '标签'),
    ('followers', '粉丝量'), ('fan_growth_rate', '粉丝增长率'),
    ('avg_views', '平均播放量'), ('video_plays', '视频播放量'),
    ('total_gmv', '总GMV'), ('monthly_settlement', '月结算额'),
    ('live_ratio', '直播GMV占比'), ('video_ratio', '视频GMV占比'),
    ('live_sessions', '直播场次'), ('live_views', '直播观看'),
    ('live_gpm', '直播GPM'), ('video_gpm', '视频GPM'),
    ('average_price', '客单价'), ('video_avg_price', '视频客单价'),
    ('live_stream_sessions', '带货直播场次'), ('avg_session_gmv', '场均结算额'),
    ('video_completion_rate', '视频完播率'), ('video_interaction_rate', '视频互动率'),
    ('fan_gender', '粉丝性别分布'), ('fan_age', '粉丝年龄分布'),
    ('fan_region', '粉丝地域分布'), ('fan_city_tier', '粉丝城市等级'),
    ('fan_crowd', '粉丝人群'), ('fan_price_range', '粉丝价格带'),
    ('fan_category', '粉丝品类偏好'), ('fan_group_activity', '粉丝活跃度'),
    ('hot_brands', '常带品牌'), ('hot_categories', '常带类目'),
    ('top_products', 'TOP商品'), ('top_categories', 'TOP类目'), ('top_brands', 'TOP品牌'),
    ('price_distribution', '价格带分布'), ('brand_commission', '品牌佣金'),
    ('cooperation_status', '合作状态'), ('commission_requirement', '佣金要求'),
    ('cooperation_requirements', '合作要求'), ('last_cooperation', '最近合作'),
    ('cooperation_days', '合作天数'), ('total_products', '带货商品数'),
    ('total_shops', '合作店铺数'), ('fulfillment_score', '履约分'),
    ('rating_score', '评分'), ('level', '等级'), ('risk_rating', '风险评级'),
    ('ai_rating', 'AI评级'), ('ai_summary', 'AI总结'), ('ai_analysis', 'AI分析'),
    ('remark', '备注'), ('notes', '备注'), ('agency', '所属机构'),
    ('data_period', '数据周期'), ('status', '状态'),
]


def log(m):
    print(f'[talent-kb] {m}', flush=True)


def _norm(v):
    """字段值 → 可读字符串; 空值返回 ''"""
    if v is None:
        return ''
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v == 0:
        return ''
    if isinstance(v, (int, float)):
        # 大数字加分位
        if isinstance(v, int) or v == int(v):
            return f'{int(v):,}'
        return f'{v:g}'
    s = str(v).strip()
    if not s or s in ('0', '0.0', '[]', '{}', 'null', 'None', '-'):
        return ''
    # JSON 字段(hot_brands 等)解析后紧凑渲染
    if s[0] in '[{':
        try:
            obj = json.loads(s)
        except Exception:
            return s
        if not obj:
            return ''
        if isinstance(obj, dict):
            if all(isinstance(x, (int, float)) for x in obj.values()):
                return '、'.join(f'{k} {v:g}' if isinstance(v, float) and v < 1
                                 else f'{k} {int(v):,}' if isinstance(v, (int, float))
                                 else f'{k} {v}' for k, v in obj.items())
            return json.dumps(obj, ensure_ascii=False)
        if isinstance(obj, list):
            parts = []
            for item in obj:
                if isinstance(item, dict):
                    parts.append(' / '.join(str(x) for x in item.values() if x not in (None, '')))
                else:
                    parts.append(str(item))
            return '、'.join(p for p in parts if p)
    return s


def render_profile(row):
    """一达人 → 结构化画像文本（只写有值字段）"""
    lines = []
    for key, label in FIELD_MAP:
        v = _norm(row.get(key))
        if v:
            lines.append(f'{label}：{v}')
    return '\n'.join(lines)


def get_category_id(conn):
    r = conn.execute(
        "SELECT id FROM knowledge_categories WHERE name = ? AND (project_id IS NULL OR project_id = '') LIMIT 1",
        (CATEGORY_NAME,)).fetchone()
    return r[0] if r else None


def backup_db():
    os.makedirs(os.path.join(BASE, 'data', 'backups'), exist_ok=True)
    ts = time.strftime('%Y%m%d_%H%M%S')
    dst = os.path.join(BASE, 'data', 'backups', f'pre-talent-kb-backfill-{ts}.db')
    src = sqlite3.connect(DB_PATH)
    try:
        dst_con = sqlite3.connect(dst)
        try:
            src.backup(dst_con)
        finally:
            dst_con.close()
    finally:
        src.close()
    log(f'已备份: {dst}')
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--execute', action='store_true')
    ap.add_argument('--only', default='', help='只处理指定 talent_id')
    ap.add_argument('--limit', type=int, default=0, help='每次最多处理 N 条(分段跑用)')
    args = ap.parse_args()

    cfg = ks.get_embedding_config(None)
    log(f'embedding: provider={cfg["provider"]} model={cfg["model"]} key={"set" if cfg["apiKey"] else "EMPTY"}')
    if cfg['model'] != 'embedding-2' or not cfg['apiKey']:
        log('!! 当前生效 embedding 配置不是 embedding-2 或无 key, 中止（产出向量检索不到）')
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        if args.only:
            talents = conn.execute('SELECT * FROM talents WHERE id = ?', (args.only,)).fetchall()
        else:
            talents = conn.execute('SELECT * FROM talents ORDER BY id').fetchall()
    finally:
        conn.close()

    # 幂等预筛: 跳过已有画像条目或非空名字的达人
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        existing = {r[0] for r in conn.execute(
            "SELECT id FROM kb_entries WHERE created_by = ?", (CREATED_BY,))}
        cat_id = get_category_id(conn)
    finally:
        conn.close()

    todo = []
    for t in talents:
        if f'kb_talent_{t["id"]}' in existing:
            continue
        text = render_profile(dict(t))
        if not text:
            continue
        todo.append((dict(t), text))
        if args.limit and len(todo) >= args.limit:
            break

    log(f'达人总数 {len(talents)}, 已有画像 {len(existing)}, 本轮待建 {len(todo)}')
    if not todo:
        log('无待建, 收工')
        return

    if not args.execute:
        for t, text in todo[:5]:
            log(f'--- dry-run 样例: {t["name"]} ---\n{text[:300]}')
        log(f'dry-run 完毕: {len(todo)} 条待建。加 --execute 真写入')
        return

    backup_db()
    now = int(time.time() * 1000)
    ok_n = skip_n = fail_n = 0
    for t, text in todo:
        eid = f'kb_talent_{t["id"]}'
        title = f'达人画像：{t["name"]}'
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            try:
                conn.execute('''
                    INSERT INTO kb_entries
                    (id, title, content, category, category_id, scope, emp_id, status,
                     chunk_count, created_by, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 'global', '', 'ok', 0, ?, ?, ?)
                ''', (eid, title, text, CATEGORY_NAME, cat_id, CREATED_BY, now, now))
                conn.commit()
            finally:
                conn.close()
            ks._save_kb_chunks_without_embedding(eid, '', text, 500, 100)
            ks._vectorize_kb_chunks(eid, '', cfg['apiKey'], cfg['provider'],
                                    cfg['model'], base_url=cfg['baseUrl'])
            ok_n += 1
            log(f'OK    {title} ({len(text)} 字)')
        except Exception as e:
            fail_n += 1
            log(f'FAIL  {title}: {e}')
            traceback.print_exc()
        time.sleep(0.15)  # embedding API 限速友好

    log(f'完成: 新建 {ok_n}, 失败 {fail_n}, 跳过 {skip_n}')


if __name__ == '__main__':
    main()
