"""LLM 推理块清洗 `strip_think` 的行为（2026-10-22）。

背景：套餐环切到 MiniMax-M3（reasoning 模型）后，OpenAI 协议返回的 content 开头就是
`<think>We need answer…`。三个地方各有一份实现，其中两份各带一个 bug：

1. `executors.py` 旧版：`out.strip() or text.strip()`
   → **整个 content 都是 think 块时**（M3 有时把最终草稿也写进 think 里），
   out 为空 → 兜底把**原文**还回去 = 根本没剥 → 英文思维链直接进 notes /
   飞书 Base「报告摘要」/ H5 摘要 / 达人报告卡（实测 1293 字）。
2. `buyin_worker.py`：压根没剥（同源同 bug 的第三例）。

现在唯一实现在 `daren_bot/llm_text.py`，本文件锁住它的行为。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from daren_bot.llm_text import strip_think  # noqa: E402


class TestNoThink:
    def test_plain_text_untouched(self):
        s = "就是普通内容"
        assert strip_think(s) == s

    def test_empty(self):
        assert strip_think("") == ""
        assert strip_think(None) == ""


class TestThinkWithBodyAfter:
    def test_body_after_think_is_kept(self):
        out = strip_think("<think>思考过程</think>\n最终答案")
        assert out == "最终答案"
        assert "think" not in out.lower()

    def test_body_preserves_chinese(self):
        out = strip_think("<think>English reasoning</think>洁洁爱户外/1.3万/贵州遵义")
        assert out.startswith("洁洁爱户外")
        assert "reasoning" not in out


class TestWholeContentIsThink:
    """⚠️ 这是旧实现翻车的那一档：M3 把最终草稿也写进 think 里。"""

    def test_answer_inside_think_is_extracted(self):
        raw = ("<think>\nLet me draft:\n\n"
               "洁洁爱户外/1.3万/贵州·遵义/服饰内衣 | 核心数据：累计GMV25万-50万\n\n"
               "Let me count\n</think>")
        out = strip_think(raw)
        assert out.startswith("洁洁爱户外/1.3万")
        assert "Let me" not in out
        assert "<think>" not in out

    def test_never_falls_back_to_raw_english_thinking(self):
        """旧实现会在这里把原文吐回去 → 英文思维链污染 notes。"""
        raw = "<think>We need to answer this in English only, no Chinese at all.</think>"
        out = strip_think(raw)
        assert out == "", "剥完没有中文正文时必须返回空，不能回退原文"
        assert "We need" not in out

    def test_english_with_chinese_term_is_not_mistaken_for_answer(self):
        """英文思考里夹带中文术语（实测：Potential concern "结算额" …）不能当答案。"""
        raw = '<think>\nPotential concern "结算额" perhaps total GMV\n\nLet me refine\n</think>'
        out = strip_think(raw)
        assert out == "", "英文思考夹中文术语被误判成答案了"
        assert "Potential" not in out

    def test_lead_label_line_is_stripped(self):
        """实测 M3 的草稿带 'Draft:' 标签行 → 会直接写进 notes，必须剥。"""
        raw = "<think>\nDraft:\n洁洁爱户外/1.3万/贵州·遵义 | 累计GMV25万-50万\n</think>"
        out = strip_think(raw)
        assert out.startswith("洁洁爱户外")
        assert "Draft" not in out

    def test_multiple_think_blocks(self):
        raw = "<think>思考A</think>正文<think>思考B</think>"
        assert "think" not in strip_think(raw).lower()


class TestUnclosedThink:
    def test_unclosed_think_keeps_content(self):
        """未闭合的 think 不该把内容吃掉（宁可不动，也不要丢）。"""
        raw = "<think>没有闭合"
        assert strip_think(raw) == raw
