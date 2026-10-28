# -*- coding: utf-8 -*-
"""角色模板 + 聊天即训练（2026-10-27，老大拍板）

解决两件事：
1. 「新创建的 bot 行为不一样」——人格唯一来源是本目录的 persona.md，
   所有达人租户共用同一份；新增达人零配置自动继承，改模板全体生效。
2. 「聊天即训练」——达人在聊天里的纠正/建议被自动捕获：
     纠正/建议原文 → reflow.push_kb_insight("talent_feedback")（管道 2 已有）
     → 定期沉淀：沉淀器把积累的正确做法合并进 persona.md 的【达人纠正榜】，
       从此全体达人共享这些教训。

人格注入方式：execute_qa 组装 prompt 时调用 qa_persona()，
把模板里「# 训练沉淀」之后的段落注入系统提示词（单层截断，不会递归变大）。
"""
import logging
import re
import time
from pathlib import Path

log = logging.getLogger("daren-bot.persona")

_PERSONA_PATH = Path(__file__).parent / "persona.md"

# 纠正/建议信号（命中其一即视为训练素材）。
_CORRECTION_RE = re.compile(
    r"不对|错了|有误|不是(这|那)?样|别(再|这样)|纠正|你应该|你该|"
    r"建议|能不能(不要|别)|以后(不要|别)|记住|记一下|下次")

# 注入 prompt 时的段落上限（模板主体 + 训练沉淀总预算，防失控膨胀）
_MAX_PERSONA_CHARS = 3000


def persona_text() -> str:
    """读角色模板原文；文件缺失/异常时回退到老硬编码人设（行为与改前一致）。"""
    try:
        t = _PERSONA_PATH.read_text(encoding="utf-8").strip()
        if t:
            return t
    except OSError:
        log.warning("persona.md 读取失败，回退默认人设", exc_info=True)
    return (
        "你是抖音电商达人的AI选品顾问，用第二人称“你”回答问题。\n"
        "只回答抖音带货、选品、商品判断、账号运营、粉丝画像、直播相关的问题，"
        "以及达人对报告/分析内容的疑问和纠错（这类属于业务范围，必须回答，不许拒）。"
    )


def training_block() -> str:
    """取模板里「# 训练沉淀」之后的段落（即沉淀器写入的【达人纠正榜】）。"""
    t = persona_text()
    idx = t.find("# 训练沉淀")
    return t[idx:].strip() if idx >= 0 else ""


def qa_persona() -> str:
    """注入 execute_qa 系统提示词的人格段：模板全文去掉运维注释（> 开头的行），截断到预算。"""
    lines = [ln for ln in persona_text().splitlines() if not ln.strip().startswith('>')]
    return "\n".join(lines).strip()[:_MAX_PERSONA_CHARS]


def is_correction(user_text: str) -> bool:
    """达人这句话是不是在纠正/教 AI。"""
    return bool(_CORRECTION_RE.search(user_text or ""))


def capture_feedback(tenant_key: str, user_text: str, bot_answer: str = "") -> bool:
    """把纠正/建议原文推知识库（talent_feedback，管道 2，服务端过价值闸）。
    尽力而为：任何失败只记日志，不阻塞聊天。返回是否推送成功。"""
    text = (user_text or "").strip()
    if not is_correction(text) or len(text) < 5:
        return False
    # 延迟 import：reflow 顶层会取 executors.SOLOBRAVE_DB，而 executors 顶层 import persona
    # ⇒ 顶层互 import 会踩部分初始化模块。运行期 import 无任何循环问题。
    from . import reflow
    title = f"达人纠正/建议（{tenant_key}，{time.strftime('%Y-%m-%d %H:%M')}）"
    ok = reflow.push_kb_insight("talent_feedback", text, title=title)
    if ok:
        log.info("feedback captured: tenant=%s text=%.40s", tenant_key, text)
    return ok


# ─── 沉淀器：定期把纠正榜合并回人格模板 ───────────────────────────────────────
_LEARN_RE = re.compile(r"## 达人纠正榜\s*(.*?)(?=\n## |\Z)", re.S)


def distill_learnings(items, path=None) -> int:
    """把提炼出的「正确做法」清单合并进 persona.md【达人纠正榜】（幂等，按行去重）。
    items: iterable[str]，每条是一句可执行的行为规则（自然语言）。
    返回新增条数。供定时任务/手动调用。"""
    p = Path(path) if path else _PERSONA_PATH
    try:
        t = p.read_text(encoding="utf-8")
    except OSError:
        log.warning("persona.md 读取失败，跳过沉淀", exc_info=True)
        return 0
    existing = set()
    m = _LEARN_RE.search(t)
    if m:
        existing = {ln.strip().lstrip("- ").strip()
                    for ln in m.group(1).splitlines() if ln.strip().startswith("-")}
    new_items = [it.strip() for it in items
                 if it.strip() and it.strip() not in existing]
    if not new_items:
        return 0
    block = "## 达人纠正榜\n" + "\n".join(f"- {it}" for it in new_items) + "\n"
    if m:
        # 追加到现有榜单末尾
        end = m.end(1)
        t = t[:end].rstrip() + "\n" + "\n".join(f"- {it}" for it in new_items) + t[end:]
    else:
        t = t.rstrip() + "\n\n# 训练沉淀\n\n" + block
    p.write_text(t, encoding="utf-8")
    log.info("distilled %d learnings into persona.md", len(new_items))
    return len(new_items)
