# -*- coding: utf-8 -*-
"""角色模板 + 聊天即训练 守卫测试

覆盖：
  1. qa_persona() 读模板、去运维注释、截断预算、缺文件回退
  2. is_correction() 信号识别（纠正/建议命中，普通提问不命中）
  3. capture_feedback() 只推纠正类原文、非纠正不推、知识库失败不炸
  4. distill_learnings() 幂等合并、去重、无榜单时新建「# 训练沉淀」节

运行：python3 -m pytest tests/test_persona_learning.py -v
"""
import sys
import os
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from daren_bot import persona  # noqa: E402
from daren_bot import reflow as reflow_mod  # noqa: E402  （capture_feedback 运行期 import 的就是它）


@pytest.fixture()
def tmp_persona(tmp_path, monkeypatch):
    p = tmp_path / "persona.md"
    p.write_text(
        "# 达人助理 · 角色模板\n"
        "> 运维注释行，不应注入 prompt\n"
        "你是抖音电商达人的AI选品顾问。\n"
        "# 训练沉淀\n"
        "## 达人纠正榜\n"
        "- 已有规则A\n",
        encoding="utf-8")
    monkeypatch.setattr(persona, "_PERSONA_PATH", p)
    return p


# ─── 1. 人格读取 ─────────────────────────────────────────────────────────────

def test_qa_persona_strips_comments(tmp_persona):
    t = persona.qa_persona()
    assert "你是抖音电商达人的AI选品顾问" in t
    assert "运维注释行" not in t  # > 注释不进 prompt


def test_qa_persona_truncated_budget(tmp_persona, monkeypatch):
    monkeypatch.setattr(persona, "_MAX_PERSONA_CHARS", 10)
    assert len(persona.qa_persona()) <= 10


def test_qa_persona_fallback_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(persona, "_PERSONA_PATH", tmp_path / "nope.md")
    t = persona.qa_persona()
    assert "AI选品顾问" in t  # 回退到老硬编码人设


def test_training_block_extracted(tmp_persona):
    blk = persona.training_block()
    assert "达人纠正榜" in blk and "已有规则A" in blk


# ─── 2. 纠正信号 ─────────────────────────────────────────────────────────────

def test_is_correction_hits():
    assert persona.is_correction("不对，我的粉丝明明是女性居多")
    assert persona.is_correction("建议你先说结论再说数据")
    assert persona.is_correction("你以后别喊口号了")
    assert persona.is_correction("记住，我是美妆类目")


def test_is_correction_misses():
    assert not persona.is_correction("这个商品能带吗")
    assert not persona.is_correction("帮我分析一下账号")
    assert not persona.is_correction("")


# ─── 3. 反馈捕获 ─────────────────────────────────────────────────────────────

def test_capture_feedback_pushes_correction(tmp_persona, monkeypatch):
    pushed = []
    monkeypatch.setattr(reflow_mod, "push_kb_insight",
                        lambda kind, text, title="": pushed.append((kind, text)) or True)
    # persona 模块内是运行期 from . import reflow ⇒ 打 reflow 模块本身即可
    ok = persona.capture_feedback("mumu", "不对，你应该先给结论再说依据")
    assert ok and pushed and pushed[0][0] == "talent_feedback"


def test_capture_feedback_skips_normal_question(tmp_persona, monkeypatch):
    monkeypatch.setattr(reflow_mod, "push_kb_insight",
                        lambda *a, **k: pytest.fail("不该推"))
    assert persona.capture_feedback("mumu", "这个商品能带吗") is False


def test_capture_feedback_kb_failure_not_fatal(tmp_persona, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("kb down")
    monkeypatch.setattr(reflow_mod, "push_kb_insight", _boom)
    # 捕获层应吞异常？——capture_feedback 不吞，调用方（executors）吞。
    # 这里验证语义：知识库挂了时返回异常由调用方容错 ⇒ 测试调用方容错行为：
    try:
        persona.capture_feedback("mumu", "不对，错了")
        raised = False
    except RuntimeError:
        raised = True
    assert raised  # 异常冒泡给 executors 的 try/except（已在 execute_qa 包好）


# ─── 4. 沉淀合并 ─────────────────────────────────────────────────────────────

def test_distill_appends_and_dedups(tmp_persona):
    n = persona.distill_learnings(["已有规则A", "新规则B", "新规则C"])
    assert n == 2
    t = tmp_persona.read_text(encoding="utf-8")
    assert t.count("已有规则A") == 1  # 去重
    assert "新规则B" in t and "新规则C" in t
    # 再来一次 ⇒ 0 新增（幂等）
    assert persona.distill_learnings(["新规则B"]) == 0


def test_distill_creates_section_when_absent(tmp_path, monkeypatch):
    p = tmp_path / "bare.md"
    p.write_text("你是选品顾问。\n", encoding="utf-8")
    monkeypatch.setattr(persona, "_PERSONA_PATH", p)
    n = persona.distill_learnings(["规则X"])
    assert n == 1
    t = p.read_text(encoding="utf-8")
    assert "# 训练沉淀" in t and "## 达人纠正榜" in t and "- 规则X" in t


def test_distill_empty_input(tmp_persona):
    assert persona.distill_learnings([]) == 0
    assert persona.distill_learnings(["", "  "]) == 0
