# -*- coding: utf-8 -*-
"""「聊天即知识」守卫测试：达人助理产生的分析/问答进知识库

覆盖 reflow.push_product_verdict / push_qa_exchange 的准入规则：
  - 选品结论 ≥10 字才推（match_pattern），标题带租户和日期
  - 问答答案 ≥30 字且不拒答才推（talent_insight），含问答对全文
  - 寒暄/拒答/报错话术不推；知识库挂了不炸（返回 False）

运行：python3 -m pytest tests/test_kb_produced_data.py -v
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from daren_bot import reflow  # noqa: E402


@pytest.fixture()
def post_recorder(monkeypatch):
    calls = []
    monkeypatch.setattr(reflow, "_post",
                        lambda path, payload: calls.append((path, payload)) or (200, {}))
    return calls


# ─── 选品判断结论 ────────────────────────────────────────────────────────────

def test_verdict_pushed(post_recorder):
    md = "## 结论：推荐\n- 佣金 25%，GPM 符合你的类目\n- 粉丝画像匹配度 82%"
    assert reflow.push_product_verdict("mumu", "户外折叠椅", md) is True
    path, payload = post_recorder[0]
    assert path == "/internal/kb/ingest"
    assert payload["kind"] == "match_pattern"
    assert "户外折叠椅" in payload["title"] and "mumu" in payload["title"]
    assert payload["text"] == md


def test_verdict_too_short_skipped(post_recorder):
    assert reflow.push_product_verdict("mumu", "x", "还行") is False
    assert post_recorder == []


def test_verdict_empty_skipped(post_recorder):
    assert reflow.push_product_verdict("mumu", "x", "  ") is False
    assert post_recorder == []


# ─── 问答沉淀 ────────────────────────────────────────────────────────────────

def test_qa_pushed(post_recorder):
    q = "我这个账号下周该主攻短视频还是直播？"
    ans = ("结论：先短视频。依据：你近 30 天短视频 GPM 是直播的 1.8 倍，"
           "且粉丝活跃高峰在 20-23 点。建议：下周保持日更 2 条挂车短视频，"
           "直播减到 2 场测试期。")
    assert reflow.push_qa_exchange("mumu", q, ans) is True
    path, payload = post_recorder[0]
    assert payload["kind"] == "talent_insight"
    assert q in payload["text"] and ans in payload["text"]
    assert q[:20] in payload["title"]


def test_qa_short_answer_skipped(post_recorder):
    assert reflow.push_qa_exchange("mumu", "在吗", "在的，你说～") is False
    assert post_recorder == []


def test_qa_refusal_skipped(post_recorder):
    long_refusal = "拒" * 40
    assert reflow.push_qa_exchange("mumu", "今天天气怎么样", long_refusal) is False
    assert post_recorder == []


def test_qa_kb_down_not_fatal(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("kb down")
    monkeypatch.setattr(reflow, "_post", _boom)
    assert reflow.push_qa_exchange(
        "mumu", "q", "这是一个足够长的实质性回答" * 3) is False
    assert reflow.push_product_verdict("mumu", "x", "足够长的选品结论内容") is False
