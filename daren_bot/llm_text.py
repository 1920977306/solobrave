"""LLM 文本输出的**统一清洗** —— 唯一事实来源。

为什么要单独成文件（2026-10-22 踩坑）：

同一条「剥掉推理块」的逻辑，仓库里一度有**三份**，其中两份各带一个 bug：

1. `daren_bot/executors.py:_strip_think`
   ```python
   out = re.sub(r'<think>.*?</think>', '', text, flags=re.S)
   return out.strip() or text.strip()      # ⚠️ out 为空时把**原文**还回去 = 根本没剥
   ```
   平时不暴露（模型 think 后面总有正文），但 MiniMax-M3 **有时把最终草稿也写进 think 里**，
   `</think>` 之后是空的 → `out` 为空 → 兜底把整段英文思维链原样吐出来。
   实测后果：画像摘要变成 1293 字的英文思考，直接进 `talents.notes` → 飞书 Base
   「报告摘要」+ H5 摘要 + 达人报告卡全部被污染。

2. `buyin_worker.py` 里**根本没剥**（`daren_bot/executors.py` 是另一份独立拷贝，
   这份是**第三份**，直接 `return content`）→ 同款污染。

本模块的 `strip_think()` 是唯一实现，规则：

- 没有 `<think>` → 原样返回（普通模型零影响）
- 剥掉 think 块后**还有正文** → 返回正文
- 剥掉后**正文为空**（答案在 think 里）→ 取 think 块中**最后一个含中日韩字符的段落**
  作为正文候选（M3 的草稿通常在最后一段）
- 连一个含中文的段落都没有 → 返回**空串**（让调用方走「空内容」分支 → 降级到下一个通道）。
  **绝不 fallback 回原文** —— 那等于没剥。

用法：
    from daren_bot.llm_text import strip_think
"""
from __future__ import annotations

import re

_THINK_BLOCK = re.compile(r"<think>(.*?)</think>", re.S)
_CJK = re.compile(r"[一-鿿぀-ヿ가-힯]")
_LATIN = re.compile(r"[A-Za-z]")

# think 段落的「像答案」判据：
#   中文为主（中文字数 ≥ 拉丁字母数的一半）且中文不少于 8 个。
# ⚠️ 只判「含中文」是不够的 —— 实测 MiniMax-M3 的英文思考里会夹带中文术语，
#   例如 'Potential concern "结算额" perhaps total GM' 含 3 个汉字却是纯英文思考。
_MIN_CJK = 8


def _has_cjk(s: str) -> bool:
    return bool(_CJK.search(s or ""))


def _looks_like_answer(s: str) -> bool:
    """这段 think 是不是「模型起草的答案」而不是英文思考过程。"""
    s = s or ""
    cjk = len(_CJK.findall(s))
    latin = len(_LATIN.findall(s))
    if cjk < _MIN_CJK:
        return False
    # 中文为主：汉字数至少是拉丁字母数的一半
    return cjk * 2 >= latin


def _think_paragraphs(text: str) -> list:
    """think 块按空行切段，按出现顺序返回（去掉空段）。"""
    out = []
    for block in _THINK_BLOCK.findall(text or ""):
        for seg in re.split(r"\n\s*\n", block):
            seg = seg.strip()
            if seg:
                out.append(seg)
    return out


def _strip_lead_labels(seg: str) -> str:
    """剥掉候选答案开头的「纯英文标签行」。

    实测 MiniMax-M3 的 think 里有这种结构：
        Draft:
        洁洁爱户外/12804粉丝/贵州·遵义/… | 核心数据：…
    「Draft:」是它的草稿标签（纯 ASCII 行），不属于答案 → 必须剥，
    否则会直接写进 notes → 飞书摘要开头挂着「Draft:」。
    """
    lines = (seg or "").split("\n")
    while lines:
        head = lines[0].strip()
        if not head:
            lines.pop(0)
            continue
        # 纯 ASCII 且不含中日韩字符 → 当作标签行剥掉
        if not _CJK.search(head) and _LATIN.search(head):
            lines.pop(0)
            continue
        break
    return "\n".join(lines).strip()


def strip_think(text: str) -> str:
    """剥掉 OpenAI 协议返回里混入的 `<think>…</think>` 推理块。

    ⚠️ 与旧实现的关键差异：**剥完为空时不再回退到原文**。
    旧写法 `out.strip() or text.strip()` 在「整个 content 都是 think 块」时
    会把英文思维链原样返回 → 直接污染 notes / 飞书摘要 / H5 摘要。
    """
    text = text or ""
    if "<think>" not in text:
        return text
    out = _THINK_BLOCK.sub("", text).strip()
    if out:
        return _strip_lead_labels(out)
    # 答案被模型写进了 think 块里 → 取最后一个「像中文答案」的段落（M3 的草稿通常在最后）
    for seg in reversed(_think_paragraphs(text)):
        if _looks_like_answer(seg):
            return _strip_lead_labels(seg)
    # 没有可用正文 → 真的没正文，返回空（上层按「空内容」处理，别回退原文）
    return ""
