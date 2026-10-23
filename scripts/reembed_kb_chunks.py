#!/usr/bin/env python3
"""
知识库向量重算（换语义模型时用）

═══════════════════════════════════════════════════════════════════════
场景（2026-10-23 实测）：
  老大买的智谱资源包覆盖 `embedding-3`，**不覆盖 `embedding-2`**
  ⇒ `embedding-2` 返回 429 {"code":"1113","message":"余额不足或无可用资源包,请充值。"}
    （智谱把「该模型无资源包」和「余额不足」用同一句文案，具有误导性）

换模型的后果（必须一起处理）：
  RAG 召回 SQL 硬条件 = `c.embedding_model = <query模型>`
  ⇒ 存量 790 个 chunk（embedding-2 / 1024 维）**在新模型下静默不召回**
    （不报错，只是搜不到 —— 最隐蔽的失效）

本脚本：
  - 把「embedding_model 与当前配置不一致」的 chunk 全部用新模型重算
  - **绕过 embedding_cache**（直调 get_embedding，不走 get_embedding_cached）
    ⇒ 缓存主键虽含 model，但显式绕过更安全（避免任何串味）
  - 逐条写入，失败不影响其它；失败条目保持旧向量（不破坏现状）
  - 不动 status / content / 分段

用法:
  python3 scripts/reembed_kb_chunks.py              # 只看，不写（dry-run）
  python3 scripts/reembed_kb_chunks.py --apply      # 真重算
  python3 scripts/reembed_kb_chunks.py --apply --batch-size 32
"""

import argparse
import importlib.util
import os
import struct
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def load_server():
    """加载 solobrave-server（先加载 .env ⇒ 拿到生产的智谱配置）"""
    env_path = os.path.join(ROOT, '.env')
    if os.path.exists(env_path):
        with open(env_path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    spec = importlib.util.spec_from_file_location('solobrave_server_re', os.path.join(ROOT, 'solobrave-server.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.ks.set_data_dir(os.path.join(ROOT, 'data'))
    return mod


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='真重算（默认 dry-run）')
    ap.add_argument("--batch-size", type=int, default=16, help="（保留参数位；实际按单条调用）")
    ap.add_argument('--limit', type=int, default=0, help='最多处理多少 chunk（0 = 不限）')
    args = ap.parse_args()

    srv = load_server()
    ks = srv.ks
    cfg = srv.get_embedding_config()
    target_model = cfg.get('model')
    provider = cfg.get('provider')
    base_url = cfg.get('baseUrl')
    key = cfg.get('apiKey') or ''

    print('═══ 目标配置 ═══')
    print(f"  provider = {provider}")
    print(f"  model    = {target_model}")
    print(f"  baseUrl  = {base_url}")
    print(f"  apiKey   = {key[:6]}...len={len(key)}")

    conn = ks._db_conn()
    try:
        rows = conn.execute(
            'SELECT id, entry_id, content, embedding_model, length(embedding) AS blen '
            'FROM kb_entry_chunks WHERE embedding IS NOT NULL AND embedding_model != ? '
            'ORDER BY id', (target_model,)
        ).fetchall()
        total_chunks = conn.execute('SELECT COUNT(*) FROM kb_entry_chunks').fetchone()[0]
        stale_models = conn.execute(
            'SELECT embedding_model, COUNT(*) c FROM kb_entry_chunks WHERE embedding IS NOT NULL '
            'GROUP BY embedding_model'
        ).fetchall()
    finally:
        conn.close()

    print()
    print('═══ 现状 ═══')
    print(f'  chunk 总数 = {total_chunks}')
    for m in stale_models:
        print(f"  模型 {m['embedding_model'] or '(空)':16s} {m['c']} 个")
    print(f'  需重算 = {len(rows)}')

    if not rows:
        print()
        print('✅ 没有需要重算的 chunk')
        return 0

    if args.limit:
        rows = rows[:args.limit]
        print(f'  （--limit 限制为 {len(rows)}）')

    # ── 连通性探测（避免白跑）──
    print()
    print('═══ 连通性探测 ═══')
    try:
        probe = srv.get_embedding('重算探测', key, provider, model=target_model, base_url=base_url)
        if not probe:
            print('❌ embedding 返回空 —— 放弃（不写库）')
            return 1
        print(f'✅ {target_model} 可用（维度 {len(probe)}）')
    except Exception as e:
        body = ''
        try:
            body = e.read().decode('utf-8', 'ignore')[:250]
        except Exception:
            pass
        print(f'❌ embedding 调用失败: {type(e).__name__}: {str(e)[:160]}')
        if body:
            print(f'   响应体: {body}')
        print('   ⇒ 不写库。确认模型/资源包可用后再跑。')
        return 1

    if not args.apply:
        print()
        print('（dry-run，未写库）加 --apply 执行重算')
        return 0

    # ── 真重算 ──
    print()
    print('═══ 开始重算 ═══')
    t0 = time.time()
    ok = fail = 0
    errors = []

    def _write(chunk_id, vec, model):
        blob = struct.pack(f'{len(vec)}f', *vec)
        c = ks._db_conn()
        try:
            c.execute('UPDATE kb_entry_chunks SET embedding=?, embedding_model=? WHERE id=?',
                      (blob, model, chunk_id))
            c.commit()
        finally:
            c.close()

    for i, r in enumerate(rows, 1):
        try:
            # ★ 直调 get_embedding（不走 get_embedding_cached）—— 显式避开缓存串味
            #   （缓存主键虽含 model，但这里显式绕过更保险）
            # ★ 只用单条接口：knowledge_service 只有 get_embedding(text,...)，
            #   没有批量接口 —— 智谱虽支持 input 数组，但仓库里没封装，不凭空造 API
            v = srv.get_embedding(r['content'], key, provider, model=target_model, base_url=base_url)
            if not v:
                raise RuntimeError('返回空')
            _write(r['id'], v, target_model)
            ok += 1
        except Exception as e:
            fail += 1
            errors.append((r['id'], f'{type(e).__name__}: {str(e)[:110]}'))
        if i % 50 == 0 or i == len(rows):
            print(f'  [{i}/{len(rows)}] ok={ok} fail={fail}  {time.time()-t0:.1f}s')
            # 连续失败 ⇒ 疑似模型/额度问题，早停（别把 790 条全试一遍）
            if fail >= 5 and fail >= ok:
                print('   ⇒ 连续失败，疑似模型/额度问题，早停')
                break

    # ── 结果 ──
    conn = ks._db_conn()
    try:
        after = conn.execute(
            'SELECT embedding_model, COUNT(*) c FROM kb_entry_chunks WHERE embedding IS NOT NULL '
            'GROUP BY embedding_model').fetchall()
        remaining = conn.execute(
            'SELECT COUNT(*) FROM kb_entry_chunks WHERE embedding IS NOT NULL AND embedding_model != ?',
            (target_model,)
        ).fetchone()[0]
    finally:
        conn.close()

    print()
    print('═══ 结果 ═══')
    print(f'  成功 {ok} / 失败 {fail} / 耗时 {time.time()-t0:.1f}s')
    print(f'  剩余旧模型 chunk = {remaining}')
    for m in after:
        print(f"  模型 {m['embedding_model'] or '(空)':16s} {m['c']} 个")
    if errors:
        print('  失败样本:')
        for cid, msg in errors[:5]:
            print(f'    {cid}: {msg}')
    return 0 if remaining == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
