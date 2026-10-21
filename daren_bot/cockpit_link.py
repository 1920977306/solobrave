"""达人经营驾驶舱专属链接 —— 对话侧与卡片的**唯一**入口来源。

背景（2026-10-20 交接项①，2026-10-21 补做）:
  老大问「我怎么打开驾驶舱」→ 机器人答不上来（当时 Kimi 额度耗尽）；
  恢复后实测又答成了「抖音官方达人驾驶舱」——问的其实是**我们自己的 7188 网页**。
  架构铁律（DAREN-ARCHITECTURE.md 三层分工）:
    **飞书是门，网页是工作台** —— 机器人只做触达与导航，
    看板与分析在 7188（公网 https://daren.sczlgyl.com），飞书侧不重做仪表盘。

链接形态（一人一链）:
  https://daren.sczlgyl.com/?t=<tenant_key>&k=<该达人的专属 k>

⚠️ k 等同于访问密码:
  - **不进日志明文**（只记 tenant_key）
  - 只出现在「发给达人本人」的飞书消息与卡片里
  - 落盘只在 daren_cockpit/cockpit_keys.json（已 600 + gitignore）
  - 没开通 k 时返回空串，调用方**跳过**而不是报错（不因缺链接阻断主流程）
"""
import json
import re
from pathlib import Path

COCKPIT_BASE_URL = "https://daren.sczlgyl.com"

# 与 daren_cockpit/server.py 的 KEYS_PATH 保持一致（同一个文件）
KEYS_PATH = Path(__file__).resolve().parent.parent / "daren_cockpit" / "cockpit_keys.json"

# 达人语境里这几个词 100% 指我们自己的系统
_COCKPIT_WORD = r"(驾驶舱|工作台|后台|看板|驾驶室)"
# 但如果主语是抖音/字节/官方，那是问抖音的，不该劫持
_FOREIGN_SUBJ = r"(抖音|字节|平台|官方|小红书|快手)[^。！？]{0,8}(驾驶舱|工作台|后台|看板)"

_LINK_RE = re.compile(_COCKPIT_WORD)
_FOREIGN_RE = re.compile(_FOREIGN_SUBJ)
# 驾驶舱域名（达人把自己收到的链接发回来时，别被当成商品链接扣 20 分）
_COCKPIT_HOST = "daren.sczlgyl.com"


def is_cockpit_url(text: str) -> bool:
    """这条消息是不是驾驶舱链接本身。"""
    return _COCKPIT_HOST in (text or "")


def is_cockpit_link_question(text: str) -> bool:
    """这句话是不是「要驾驶舱入口」。

    判定口径（刻意做简单，少分支少误判）:
      只要提到「驾驶舱/工作台/后台/看板」→ 走入口导航（**零成本**，不扣积分）。
      理由: 达人说「驾驶舱数据怎么样」时，他要的是**入口**——链接里就是完整看板，
      比在飞书里用文字复述一遍数据更好。真正的数据分析问法（不含这些词）
      照旧走 LLM 问答。
      例外: 主语是抖音/字节/官方时 → 不劫持，那是问抖音官方产品。
    """
    t = (text or "").strip()
    if not t or not _LINK_RE.search(t):
        return False
    return not _FOREIGN_RE.search(t)


def tenant_link_key(tenant_key: str) -> str:
    """取该达人的专属 k；读不到返回空串。"""
    try:
        keys = json.loads(KEYS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(keys, dict):
        return ""
    k = keys.get(tenant_key) or ""
    return k if isinstance(k, str) else ""


def tenant_cockpit_link(tenant_key: str) -> str:
    """该达人的专属驾驶舱链接；没开通 k 返回空串。"""
    k = tenant_link_key(tenant_key)
    if not k:
        return ""
    return f"{COCKPIT_BASE_URL}/?t={tenant_key}&k={k}"
