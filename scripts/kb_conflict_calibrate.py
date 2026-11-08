#!/usr/bin/env python3
"""矛盾检测阈值校准：看真实相似度分布，再定 DEFAULT_SIM_THRESHOLD。

用法：
  python3 scripts/kb_conflict_calibrate.py            # 人工看分布 + 各档样本对
  python3 scripts/kb_conflict_calibrate.py --json     # 全量 pair 表导出（对量分析）

判读口径：
  - 「nearest neighbor 分布」看阈值切在哪能把「同主题」和「噪音」分开；
  - 每个 sim 档抽几对打印标题+正文片段，人逐对判「像矛盾候选 / 只是同类」；
  - 定阈值回写 kb_conflict_service.DEFAULT_SIM_THRESHOLD（别拍脑袋）。
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import knowledge_service as ks
import kb_conflict_service as kc

BUCKETS = [(0.98, 1.01), (0.95, 0.98), (0.90, 0.95), (0.85, 0.90),
           (0.80, 0.85), (0.70, 0.80), (0.0, 0.70)]


def main():
    ks.set_data_dir(str(Path(__file__).resolve().parents[1] / "data"))
    conn = ks._db_conn()
    chunks = kc.load_ok_chunk_embeddings(conn)
    entry_of = [e for e, _ in chunks]
    mat = kc.unpack_embeddings([b for _, b in chunks])
    pairs = kc.entry_pair_max_sims(mat, entry_of)
    meta = kc.load_entry_meta(conn, sorted({e for p in pairs for e in p}))

    # ① 最近邻分布（每个条目离它最近的条目有多远）
    nn = kc.nearest_neighbor_table(pairs, per_entry=1)
    nn_sims = [v[0][1] for v in nn.values()]
    total = len(nn_sims) or 1
    print(f"== ok 条目 {len(set(entry_of))} 个，chunk {len(chunks)} 个，"
          f"条目对 {len(pairs)} 对 ==")
    print("\n== 最近邻相似度分布（每个条目离最近条目的距离）==")
    for lo, hi in BUCKETS:
        n = sum(1 for s in nn_sims if lo <= s < hi)
        pct = round(n / total * 100, 1)
        bar = "#" * int(pct / 2)
        print(f"  [{lo:.2f},{hi:.2f})  {n:4d} 条  {pct:5.1f}%  {bar}")

    # ② 候选对总量随阈值的变化
    print("\n== sim >= 阈值 ⇒ 候选对数量 ==")
    for th in (0.98, 0.95, 0.92, 0.90, 0.88, 0.85, 0.80, 0.75):
        n = sum(1 for s in pairs.values() if s >= th)
        print(f"  >= {th:.2f}  →  {n:5d} 对")

    # ③ 各档抽样人工判读
    print("\n== 各相似度档样本对（人工判：像矛盾候选 / 只是同类）==")
    for lo, hi in BUCKETS[:-1]:
        samp = [(a, b, s) for (a, b), s in pairs.items()
                if lo <= s < hi]
        samp.sort(key=lambda x: -x[2])
        samp = samp[:4]
        if not samp:
            continue
        print(f"\n── 档 [{lo:.2f},{hi:.2f}) ──")
        for a, b, s in samp:
            ta = (meta.get(a) or {}).get("title", a)
            tb = (meta.get(b) or {}).get("title", b)
            ca = ((meta.get(a) or {}).get("content", "") or "")[:120].replace("\n", " ")
            cb = ((meta.get(b) or {}).get("content", "") or "")[:120].replace("\n", " ")
            print(f"  sim={s:.3f}")
            print(f"    A[{ta}] {ca}")
            print(f"    B[{tb}] {cb}")

    if "--json" in sys.argv:
        out = [{"a": a, "b": b, "sim": round(s, 4),
                "title_a": (meta.get(a) or {}).get("title", ""),
                "title_b": (meta.get(b) or {}).get("title", "")}
               for (a, b), s in
               sorted(pairs.items(), key=lambda kv: -kv[1])]
        Path("data/kb-conflict-pairs.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=1))
        print(f"\n全量 pair 表已导出 data/kb-conflict-pairs.json（{len(out)} 对）")
    conn.close()


if __name__ == "__main__":
    main()
