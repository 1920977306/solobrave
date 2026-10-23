#!/usr/bin/env python3
"""
知识库缺向量补齐工具（2026-10-23）

═══════════════════════════════════════════════════════════════════════
背景（老大口径「语义是智谱，我买的资源包」）：
  - 生产 embedding = **智谱 embedding-2**（`.env` 里 `SOLOBRAVE_EMBEDDING_PROVIDER=zhipu`，
    环境变量优先级 > settings.json）
  - 智谱在余额/资源包不可用时返回 **HTTP 429** 且 body 是
    `{"code":"1113","message":"余额不足或无可用资源包,请充值。"}`
    ⇒ **智谱把余额问题也报 429**（别按状态码猜原因，必须读 body）

真问题：
  - 向量化失败时，条目已入库、`kb_entry_chunks` 行已建但 `embedding` 留 NULL
  - RAG 召回条件 = `e.status='ok' AND c.embedding IS NOT NULL AND c.embedding_model=<模型>`
    ⇒ 无向量的条目**完全不被召回**（不是「召回差」，是「召不回」）
  - 旧的 `kb_entries_retry_all_failed_embedding` 只扫 `status IN ('embedding_failed','error')`
    ⇒ 自动入库路径（`_vectorize_kb_entry`）失败时不改 status ⇒ **扫不到 ⇒ 永久补不回**

本工具按「**有 chunk 但 embedding 为 NULL**」扫描（与 `kb_entry_stats.pendingChunks`
同一个口径），余额恢复后跑一次即可把缺的向量补齐。

用法:
  python3 scripts/backfill_missing_embeddings.py            # 只看，不写（默认 = dry-run）
  python3 scripts/backfill_missing_embeddings.py --apply    # 真补

★ 只补 embedding，不动 status / content / title / 分段（分段已存在）。
★ 逐条独立：一条失败不影响其他（余额问题会每条都失败，所以默认建议先 --dry-run）。
"""

import argparse
import importlib.util
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# server 依赖同目录模块（douyin_parser 等）⇒ 必须把项目根放进 sys.path
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def load_server():
    """加载 solobrave-server（会加载 .env ⇒ 拿到生产的智谱配置）"""
    # 先加载 .env（server 启动时也会做同样的事）
    env_path = os.path.join(ROOT, '.env')
    if os.path.exists(env_path):
        with open(env_path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    spec = importlib.util.spec_from_file_location('solobrave_server_bf', os.path.join(ROOT, 'solobrave-server.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.ks.set_data_dir(os.path.join(ROOT, 'data'))
    return mod


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='真补（默认只 dry-run）')
    ap.add_argument('--limit', type=int, default=200, help='最多处理多少条（默认 200）')
    args = ap.parse_args()

    srv = load_server()
    ks = srv.ks

    cfg = srv.get_embedding_config()
    print('═══ embedding 配置 ═══')
    print(f"  provider = {cfg.get('provider')}")
    print(f"  model    = {cfg.get('model')}")
    print(f"  baseUrl  = {cfg.get('baseUrl')}")
    key = cfg.get('apiKey') or ''
    print(f"  apiKey   = {key[:6]}...len={len(key)}")

    # ── 扫「有 chunk 但无向量」的条目（跟 kb_entry_stats.pendingChunks 同口径）──
    conn = ks._db_conn()
    try:
        rows = conn.execute('''
            SELECT e.id, e.title, e.status, COUNT(c.id) AS missing_chunks
            FROM kb_entries e
            JOIN kb_entry_chunks c ON c.entry_id = e.id
            WHERE c.embedding IS NULL
            GROUP BY e.id
            ORDER BY e.id
            LIMIT ?
        ''', (args.limit,)).fetchall()
        total_missing = conn.execute(
            "SELECT COUNT(*) FROM kb_entry_chunks WHERE embedding IS NULL"
        ).fetchone()[0]
    finally:
        conn.close()

    print()
    print('═══ 缺向量的条目 ═══')
    print(f"  缺向量 chunk 总数 = {total_missing}")
    print(f"  受影响条目数     = {len(rows)}")
    for r in rows[:20]:
        print(f"    {r['id']:24s} status={r['status']:10s} 缺{r['missing_chunks']:3d}段  {(r['title'] or '')[:28]}")
    if len(rows) > 20:
        print(f"    ... 另 {len(rows) - 20} 条")

    if not rows:
        print()
        print('✅ 没有缺向量的条目 —— 无需处理')
        return 0

    # ── 先探一次连通性（避免白跑 200 次）──
    print()
    print('═══ 连通性探测 ═══')
    try:
        probe = srv.get_embedding('连通性探测 embedding', key, cfg.get('provider', 'openai'),
                                  model=cfg.get('model'), base_url=cfg.get('baseUrl'))
        if not probe:
            print('❌ embedding 返回空 —— 放弃（不写库）')
            return 1
        print(f'✅ embedding 可用（维度 {len(probe)}）')
    except Exception as e:
        body = ''
        try:
            body = e.read().decode('utf-8', 'ignore')[:200]  # HTTPError
        except Exception:
            pass
        print(f'❌ embedding 调用失败: {type(e).__name__}: {str(e)[:160]}')
        if body:
            print(f'   响应体: {body}')
        print('   ⇒ 余额/资源包恢复后再跑；本工具不会写库。')
        return 1

    if not args.apply:
        print()
        print('（dry-run 模式，未写库）加 --apply 执行补齐')
        return 0

    # ── 真补 ──
    print()
    print('═══ 开始补齐 ═══')
    ok = fail = 0
    errors = []
    for i, r in enumerate(rows, 1):
        entry_id = r['id']
        try:
            ks._vectorize_kb_chunks(entry_id, '', key, cfg.get('provider', 'openai'),
                                    cfg.get('model'), base_url=cfg.get('baseUrl'))
            ok += 1
            print(f"  [{i}/{len(rows)}] ✓ {entry_id}")
        except Exception as e:
            fail += 1
            msg = f'{type(e).__name__}: {str(e)[:120]}'
            errors.append((entry_id, msg))
            print(f"  [{i}/{len(rows)}] ✗ {entry_id} {msg}")
            # 余额问题会连着失败 ⇒ 早停，别把 limit 条全试一遍
            if '429' in msg or '1113' in msg:
                print('   ⇒ 余额/资源包问题，早停（修好后重跑即可）')
                break

    conn = ks._db_conn()
    try:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM kb_entry_chunks WHERE embedding IS NULL").fetchone()[0]
    finally:
        conn.close()

    print()
    print('═══ 结果 ═══')
    print(f'  成功 {ok} / 失败 {fail} / 剩余缺向量 chunk {remaining}')
    return 0 if fail == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
