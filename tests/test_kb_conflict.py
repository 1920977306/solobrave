"""知识库矛盾检测（kb_conflict_service.py）的测试。

口径钉死（2026-11-08 校准后定案，改之前先读模块 docstring）：
- 嵌入相似度区分不了「同模板不同人」（达人画像/分析报告模板相似度 0.95+ 是噪音）
  ⇒ 矛盾候选 = **同主体**（达人/商品名）≥2 条的组；嵌入相似度只排序不过滤；
- AI 发现、人裁决：裁决走 /api/knowledge/conflicts/decide 落 kb_decisions；
- 主体名归一化剥括号备注（「小楚当妈（捡漏版）」=「小楚当妈」），分组键≠展示名；
- 模板词（达人档案/工作流程/####…）不是主体，提取到也要滤掉。
"""
import importlib
import sqlite3
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

kc = importlib.import_module("kb_conflict_service")


def _blob(vec):
    return struct.pack(f"{len(vec)}f", *vec)


# ─────────────── unpack_embeddings ───────────────

def test_unpack_normalizes_and_drops_bad_rows():
    m = kc.unpack_embeddings([_blob([3.0, 4.0]), _blob([1.0, 0.0]), b""])
    assert m.shape == (2, 2)
    assert np.isclose(np.linalg.norm(m[0]), 1.0)
    assert np.isclose(m[0][0], 0.6)          # 3-4-5 归一化


def test_unpack_mixed_dims_drops_inconsistent():
    m = kc.unpack_embeddings([_blob([1.0, 0.0, 0.0]), _blob([1.0, 0.0])])
    assert m.shape == (2, 2) or m.shape == (1, 3)   # 只留同维度的


def test_unpack_empty():
    assert kc.unpack_embeddings([]).shape == (0, 0)


# ─────────────── entry_pair_max_sims ───────────────

def test_entry_pair_max_takes_best_chunk_pair():
    # e1 两个 chunk，e2 一个 chunk；条目对相似度取 chunk 对最大值
    e1a, e1b = [1.0, 0.0], [0.0, 1.0]
    e2 = [1.0, 0.0]
    mat = kc.unpack_embeddings([_blob(e1a), _blob(e1b), _blob(e2)])
    pairs = kc.entry_pair_max_sims(mat, ["e1", "e1", "e2"])
    assert len(pairs) == 1
    assert np.isclose(pairs[("e1", "e2")], 1.0)   # e1a·e2 = 1（max，不是均值 .5）


def test_entry_pair_skips_same_entry_chunks():
    mat = kc.unpack_embeddings([_blob([1.0, 0.0]), _blob([0.0, 1.0])])
    assert kc.entry_pair_max_sims(mat, ["e1", "e1"]) == {}


# ─────────────── cluster_edges ───────────────

def test_cluster_edges_connected_components():
    edges = [("a", "b", 0.9), ("b", "c", 0.8), ("x", "y", 0.95)]
    clusters = kc.cluster_edges(edges)
    assert len(clusters) == 2
    big = next(c for c in clusters if len(c["entries"]) == 3)
    assert big["entries"] == frozenset({"a", "b", "c"})
    # 簇内边按相似度降序
    sims = [s for _, _, s in big["edges"]]
    assert sims == sorted(sims, reverse=True)


# ─────────────── extract_subject / build_subject_groups ───────────────

def test_extract_subject_quote_bracket_strip():
    raw, key = kc.extract_subject("", "「小楚当妈（捡漏版）」的分析报告")
    assert raw == "小楚当妈（捡漏版）" and key == "小楚当妈"


def test_extract_subject_huaxiang_title():
    assert kc.extract_subject("达人画像：喵不李", "") == ("喵不李", "喵不李")


def test_extract_subject_report_prefix():
    raw, key = kc.extract_subject("", "# 发财周周 达人合作匹配分析报告")
    assert key == "发财周周"


@pytest.mark.parametrize("title,content", [
    ("达人画像：达人档案", ""),                # 模板词不是主体
    ("工作流程", ""),
    ("知识点：#### 1. 达人评级：A", ""),
    ("达人分析：理由：粉丝量级中等", ""),        # 理由/分析不是主体
])
def test_extract_subject_rejects_template_words(title, content):
    assert kc.extract_subject(title, content) == ("", "")


def test_extract_subject_not_found_returns_empty():
    assert kc.extract_subject("随便什么标题", "没有任何模式的正文") == ("", "")


def test_build_subject_groups_only_multi():
    entries = [
        ("e1", "达人画像：喵不李", ""),
        ("e2", "达人画像：喵不李", ""),
        ("e3", "达人画像：七月姐姐", ""),      # 单条组不进候选
        ("e4", "随便", ""),
    ]
    groups = kc.build_subject_groups(entries)
    assert groups == {"喵不李": ["e1", "e2"]}


# ─────────────── scan_by_subject（tmp 库全流程）───────────────

def _mk_conn(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "sb.db"))
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE kb_entries (id TEXT PRIMARY KEY, title TEXT,"
                 " content TEXT, category TEXT DEFAULT '', tier TEXT DEFAULT '',"
                 " source TEXT DEFAULT '', status TEXT DEFAULT 'ok',"
                 " updated_at INTEGER DEFAULT 0)")
    conn.execute("CREATE TABLE kb_entry_chunks (id TEXT PRIMARY KEY,"
                 " entry_id TEXT, chunk_index INTEGER, content TEXT,"
                 " embedding BLOB, embedding_model TEXT DEFAULT '',"
                 " created_at INTEGER DEFAULT 0)")
    return conn


def _seed_entry(conn, eid, title, content, status="ok", vec=None):
    conn.execute("INSERT INTO kb_entries (id, title, content) VALUES (?,?,?)",
                 (eid, title, content))
    if status != "ok":
        conn.execute("UPDATE kb_entries SET status=? WHERE id=?", (status, eid))
    if vec is not None:
        conn.execute("INSERT INTO kb_entry_chunks (id, entry_id, chunk_index,"
                     " content, embedding, embedding_model) VALUES (?,?,?,?,?,?)",
                     (f"ch_{eid}", eid, 0, content[:50], _blob(vec), "embedding-3"))
    conn.commit()


def test_scan_by_subject_clusters_same_subject_only(tmp_path):
    conn = _mk_conn(tmp_path)
    v = [1.0, 0.0, 0.0]
    w = [0.0, 1.0, 0.0]
    _seed_entry(conn, "e1", "达人画像：喵不李", "内容A", vec=v)
    _seed_entry(conn, "e2", "「喵不李」分析报告", "内容B", vec=w)
    _seed_entry(conn, "e3", "达人画像：七月姐姐", "内容C", vec=v)   # 同向量不同主体
    _seed_entry(conn, "e4", "达人画像：喵不李", "已删的", status="deleted", vec=w)
    out = kc.scan_by_subject(conn)
    assert out["subjects"] == 1            # 七月姐姐单条、deleted 不算
    assert len(out["clusters"]) == 1
    c = out["clusters"][0]
    assert c["subject"] == "喵不李"
    assert {e["id"] for e in c["entries"]} == {"e1", "e2"}
    assert len(c["pairs"]) == 1
    assert c["pairs"][0]["sim"] is None or -1.0 <= c["pairs"][0]["sim"] <= 1.0
    conn.close()


def test_scan_by_subject_empty_db(tmp_path):
    conn = _mk_conn(tmp_path)
    out = kc.scan_by_subject(conn)
    assert out["clusters"] == [] and out["subjects"] == 0
    conn.close()


def test_pick_threshold_filters_and_sorts():
    pairs = [("a", "b", 0.7), ("c", "d", 0.95), ("e", "f", 0.9)]
    out = kc.pick_threshold(pairs, 0.9)
    assert [e[:2] for e in out] == [("c", "d"), ("e", "f")]
