"""知识库矛盾检测服务（kb_conflict_service.py）

口径（2026-11-08 评估方设计 + 老大拍板，改之前先读）：
  ① AI 只能**发现**矛盾候选，**不能裁决**——裁决是人的事（kb_decisions 留痕）；
  ② embedding 近邻筛候选：条目两两直接比对是 ~7.6 万对（343 选 2），
    先向量近邻筛出候选对，再进裁决界面——不全量喂给人或 LLM；
  ③ 呈现按「簇」不按「对」：一条与 5 条矛盾，按簇判一次，
    不按 15 对判 15 次（对 vs 簇的体验差异决定人愿不愿意裁决）；
  ④ 只扫 status='ok' 的条目：pending 还没过审、deleted/superseded 不参与检索，
    对已删条目做矛盾检测没有意义。

相似度口径：
  条目级相似度 = 两条目任意 chunk 对余弦相似度的**最大值**。
  同一主题的不同段落分属不同 chunk，max 才能保住「同主题」信号；
  均值会把「一条长文 vs 一条短文只重合一段」的信号稀释掉。

⚠️ 阈值不许拍脑袋：先跑 scripts/kb_conflict_calibrate.py 看真实分布再定
（阈值高了漏矛盾，低了全是噪音）。CALIBRATE 输出的档位只是起步参考。
"""
from __future__ import annotations

import struct
import time

import numpy as np

import knowledge_service as ks

# ─────────────────────────── 纯函数（无 DB 无网络，可单测）───────────────────────────


def unpack_embeddings(blobs):
    """struct 打包的 float32 blob 列表 → (n, dim) L2 归一化矩阵。

    blob 长度不一致（维度不同/损坏）的行整行丢弃——只丢不造。
    """
    arrs = []
    for b in blobs:
        try:
            v = np.frombuffer(b, dtype=np.float32)
        except Exception:
            continue
        if v.size == 0:
            continue
        arrs.append(v)
    if not arrs:
        return np.zeros((0, 0), dtype=np.float32)
    dim = len(arrs[0])
    if any(len(a) != dim for a in arrs):
        arrs = [a for a in arrs if len(a) == dim]
    m = np.stack(arrs).astype(np.float32)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


def entry_pair_max_sims(chunk_mat, entry_of_chunk):
    """chunk 余弦相似度矩阵 → 条目对最大相似度。

    chunk_mat: (n, dim) 已归一化；entry_of_chunk: 长度 n 的条目 id 列表。
    返回 dict[(entry_i, entry_j)] = max_sim（i<j，跨条目；同条目内部对丢弃）。
    n≈800 时全量矩阵 800×800 浮点 ≈ 2.5MB，直接算，不近似。
    """
    if chunk_mat.shape[0] == 0:
        return {}
    sim = chunk_mat @ chunk_mat.T          # (n, n) 余弦（已归一化）
    n = sim.shape[0]
    out = {}
    for i in range(n):
        ei = entry_of_chunk[i]
        row = sim[i]
        # 只往后扫：同条目 chunk 对（entry 相同）跳过
        for j in range(i + 1, n):
            if entry_of_chunk[j] == ei:
                continue
            pair = (ei, entry_of_chunk[j])
            v = float(row[j])
            if v > out.get(pair, 0.0):
                out[pair] = v
    return out


def cluster_edges(edges):
    """候选边（entry_a, entry_b, sim）→ 连通分簇。

    返回 [{entries: frozenset, edges: [(a, b, sim), ...]}]，
    只含有边的条目（孤立条目不进任何簇）。并查集实现。
    """
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for a, b, _ in edges:
        union(a, b)
    groups = {}
    for a, b, s in edges:
        groups.setdefault(find(a), []).append((a, b, s))
    return [{"entries": frozenset({a for a, _, _ in v} | {b for _, b, _ in v}),
             "edges": sorted(v, key=lambda e: -e[2])}
            for v in groups.values()]


def pick_threshold(pairs, sim_threshold):
    """pairs = [(a, b, sim)] → 筛出 sim >= sim_threshold 的候选边（升序）。"""
    return sorted([(a, b, s) for a, b, s in pairs if s >= sim_threshold],
                  key=lambda e: -e[2])


# ─────────────────────────── DB 侧 ───────────────────────────


def load_ok_chunk_embeddings(conn, model="embedding-3"):
    """status='ok' 条目的全部 chunk 向量（口径与 RAG 召回一致）。

    返回 [(entry_id, blob)]；deleted/pending 条目不参与（口径④）。
    """
    rows = conn.execute(
        "SELECT c.entry_id AS eid, c.embedding AS emb FROM kb_entry_chunks c "
        "JOIN kb_entries e ON c.entry_id = e.id "
        "WHERE c.embedding IS NOT NULL AND c.embedding_model = ? "
        "AND e.status = 'ok'", (model,)).fetchall()
    return [(r["eid"], r["emb"]) for r in rows]


def load_entry_meta(conn, entry_ids):
    """裁决界面要看的条目元数据（content 截断到 500 字，界面够用）。"""
    if not entry_ids:
        return {}
    marks = ",".join("?" * len(entry_ids))
    rows = conn.execute(
        f"SELECT id, title, content, category, tier, source, updated_at "
        f"FROM kb_entries WHERE id IN ({marks})", tuple(entry_ids)).fetchall()
    return {r["id"]: {"id": r["id"], "title": r["title"],
                      "content": (r["content"] or "")[:500],
                      "category": r["category"] or "",
                      "tier": r["tier"] or "", "source": r["source"] or "",
                      "updated_at": r["updated_at"] or 0} for r in rows}


def scan(conn, sim_threshold, model="embedding-3"):
    """全量扫一次：近邻筛候选 → 聚簇 → 带元数据。

    返回 {scanned_chunks, ok_entries, candidate_pairs, clusters}；
    clusters 按簇内最高相似度降序（最像矛盾的在前）。
    """
    chunks = load_ok_chunk_embeddings(conn, model=model)
    if not chunks:
        return {"scanned_chunks": 0, "ok_entries": 0, "candidate_pairs": 0,
                "clusters": []}
    entry_of = [eid for eid, _ in chunks]
    mat = unpack_embeddings([b for _, b in chunks])
    pairs = entry_pair_max_sims(mat, entry_of)
    edges = pick_threshold([(a, b, s) for (a, b), s in pairs.items()],
                           sim_threshold)
    clusters = cluster_edges(edges)
    meta = load_entry_meta(conn,
                           [e for c in clusters for e in c["entries"]])
    out_clusters = []
    for c in sorted(clusters,
                    key=lambda c: -max(s for _, _, s in c["edges"])):
        out_clusters.append({
            "entries": [meta[e] for e in c["entries"] if e in meta],
            "edges": [{"a": a, "b": b, "sim": round(s, 4)}
                      for a, b, s in c["edges"]],
            "top_sim": round(max(s for _, _, s in c["edges"]), 4),
        })
    return {"scanned_chunks": len(chunks),
            "ok_entries": len(set(entry_of)),
            "candidate_pairs": len(edges),
            "clusters": out_clusters}


def nearest_neighbor_table(pairs, per_entry=5):
    """校准用：每个条目的 top-N 近邻（按相似度降序）。

    pairs = {(a, b): sim}（entry_pair_max_sims 的输出）或 [(a, b, sim)]。
    返回 {entry: [(other, sim), ...]}，无近邻的条目不出现在表里。
    """
    items = pairs.items() if isinstance(pairs, dict) else pairs
    best = {}
    for (a, b), s in items:
        best.setdefault(a, []).append((b, s))
        best.setdefault(b, []).append((a, s))
    return {e: sorted(v, key=lambda x: -x[1])[:per_entry]
            for e, v in best.items()}


# 默认阈值：★ 拍脑袋起步值，跑 scripts/kb_conflict_calibrate.py 校准后改这里 ★
DEFAULT_SIM_THRESHOLD = 0.90

# ─────────────────────────── 主体归组（口径：嵌入相似度区分不了「同模板不同人」，
# 同一主体（达人/商品）才是矛盾判定的前提——不同主体相似是模板噪音，不是矛盾）───────────────────────────

# 模板词黑名单：从标题/正文提取到这些「主体」全是正则误捕（不是真人名/商品名）
_SUBJECT_STOPWORDS = ("达人档案", "达人分析", "达人画像", "分析报告", "合作建议",
                      "工作流程", "项目经验", "数据规范", "理由", "分析", "报告",
                      "知识点", "总结", "首选", "不建议", "基本盘", "####",
                      "用户", "平台", "商品库")

_PAREN_RE = __import__("re").compile(r"[（(【\[].*?[）)】\]]")


def _clean_subject(name):
    """主体名归一化：剥括号备注（平台昵称装饰如「小楚当妈（捡漏版）」）、去空白。
    归一化只用于**分组键**，展示仍用原文。过滤后不像名字 ⇒ ''。"""
    if not name:
        return ""
    n = _PAREN_RE.sub("", name).strip()
    n = n.strip("*#：:，。、 ")
    if not (2 <= len(n) <= 12):
        return ""
    if any(w in n for w in _SUBJECT_STOPWORDS):
        return ""
    return n


def extract_subject(title, content):
    """从条目提取主体（达人/商品名）→ (原始名, 归一化分组键)。

    三个来源按精度排序：「」引用 > 标题前缀（达人画像：X / 报告：X）> 正文模式。
    提取不到 ⇒ ('', '')（不硬猜，交还人工/LLM 通道）。
    """
    import re
    text = content or ""
    head = (title or "") + "\n" + text[:300]
    m = re.search(r"「([^「」]{1,20})」", head)
    if m:
        raw = m.group(1).strip()
        key = _clean_subject(raw)
        if key:
            return raw, key
    m = re.match(r"^达人画像[:：]\s*(.+)$", title or "")
    if m:
        raw = m.group(1).strip()
        key = _clean_subject(raw)
        if key:
            return raw, key
    # 「名字 达人XX报告/档案」——名字在「达人」前（# 发财周周 达人合作匹配分析报告）
    m = re.search(r"(?:^|\n)#?\s*([^\s，。：:「」#*]{2,12})\s*达人", head)
    if m:
        raw = m.group(1).strip()
        key = _clean_subject(raw)
        if key:
            return raw, key
    m = re.search(r"(?:分析报告|报告)[:：]\s*([^\s，。：:「」]{2,12})", head)
    if m:
        raw = m.group(1).strip()
        key = _clean_subject(raw)
        if key:
            return raw, key
    return "", ""


def build_subject_groups(entries):
    """entries = [(entry_id, title, content)] → {分组键: [entry_id, ...]}。
    只有 ≥2 条的组才是矛盾检测候选（单条组无对可判）。"""
    groups = {}
    for eid, title, content in entries:
        _, key = extract_subject(title, content)
        if key:
            groups.setdefault(key, []).append(eid)
    return {k: v for k, v in groups.items() if len(v) >= 2}


def scan_by_subject(conn, model="embedding-3"):
    """主体归组扫描：同主体 ≥2 条的组 = 候选簇（矛盾或重复，人裁决）。

    嵌入相似度不参与过滤（同主体即候选），只用于簇内排序和界面展示。
    返回 {subject, entries:[meta], pairs:[{a,b,sim}], top_sim}，top_sim 降序。
    """
    rows = conn.execute(
        "SELECT id, title, content, category, tier, source, updated_at "
        "FROM kb_entries WHERE status='ok'").fetchall()
    entries = [(r["id"], r["title"], r["content"]) for r in rows]
    groups = build_subject_groups(entries)
    if not groups:
        return {"subjects": 0, "clusters": []}
    # 相似度表（簇内展示/排序用）
    chunks = load_ok_chunk_embeddings(conn, model=model)
    mat = unpack_embeddings([b for _, b in chunks])
    entry_of = [e for e, _ in chunks]
    pair_sims = entry_pair_max_sims(mat, entry_of)
    meta = load_entry_meta(conn, [e for g in groups.values() for e in g])
    clusters = []
    for key, eids in groups.items():
        present = [e for e in eids if e in meta]
        if len(present) < 2:
            continue
        edges = []
        for i in range(len(present)):
            for j in range(i + 1, len(present)):
                s = pair_sims.get((present[i], present[j]))
                if s is None:
                    s = pair_sims.get((present[j], present[i]))
                edges.append({"a": present[i], "b": present[j],
                              "sim": round(s, 4) if s is not None else None})
        edges.sort(key=lambda e: -(e["sim"] or 0))
        clusters.append({"subject": key,
                         "entries": [meta[e] for e in present],
                         "pairs": edges,
                         "top_sim": edges[0]["sim"] if edges else None})
    clusters.sort(key=lambda c: -(c["top_sim"] or 0))
    return {"subjects": len(groups), "clusters": clusters}
