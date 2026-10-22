"""结论 / 状态的**视觉同源映射**——H5 驾驶舱与飞书 BaseApp 的唯一事实来源。

为什么要有这个文件（2026-10-22 老大反馈「两个设计风格没有一样，不然会以为是两个系统」）：
H5（`daren_cockpit/index.html`）和飞书 App（BaseApp `ZguOb2xCba10pVsRMlXcFmgenGd`）
展示的是**同一个达人（hlb725 洁洁爱户外）的同一套数据**，但之前两边各写各的：

| 同一个词 | H5 原来 | 飞书原来 | 观感 |
|---|---|---|---|
| 潜力型 | 绿底（且**所有**结论一律绿底） | **紫色**标签 | 同一句话两个颜色 |
| 🟢 能带 | 绿底 | **红色**标签（emoji 与颜色反了） | 同上 |
| 🔴 别带 | 红底 | **橙色**标签 | 同上 |
| 风险型 | 绿底（错） | 橙色 | 同上 |
| 待评估 | 琥珀 | 蓝色 | 同上 |

「同一句话必须同色」是硬约束，否则用户一眼就看出是两个系统。

本模块是那个约束的**唯一事实来源**：

- `daren_cockpit/server.py` 用它算 H5 的一句话结论（不再内联 dict）
- `daren_cockpit/index.html` 的 `VERDICT_EMOJI` 是它的 **JS 孪生**（跨语言一致性由
  `daren_bot/tools/verify_visual_sync.py` 锁住）
- 飞书 Base 单选字段的 `options.hue` 由它约束（守卫脚本校验实际 hue）
- 飞书 App 经营总览页「本周一句话」text 块由它约束（守卫脚本校验实际文本）

改这里 = 改四个地方。守卫脚本会让任何一边漏改立刻 exit 1。
"""

# 达人评级 + 选品结论 → 展示映射。
# line 只覆盖**已有**结论句，不给没定义的结论编句子（只丢不造）；
# 未命中时用 NO_LINE_FALLBACK，与历史行为完全一致。
#
# key        = 结论的「裸词」（H5 里用的就是这个，emoji 由前端按映射补）
# feishu_option = 飞书 Base 单选字段里的**选项真名**。⚠️ 两张表不一样：
#              账号档案·评估结论 = 「潜力型」（裸词）
#              商品推荐记录·结论   = 「🟢 能带」（带 emoji 前缀！）
#              H5 拼 CSS 类名前必须剥掉前缀，否则 v-🟢 能带 这个类不存在 → 标签没底色。
VERDICT_UI = {
    # —— 达人评级（飞书 Base「账号档案 · 评估结论」）——
    "潜力型": {"line": "涨粉健康、带货稳，优先接高佣标品", "emoji": "🟢", "hue": "Green",
               "feishu_option": "潜力型"},
    "成长型": {"line": None, "emoji": "🟢", "hue": "Lime",
               "feishu_option": "成长型"},
    "稳健型": {"line": None, "emoji": "🟡", "hue": "Yellow",
               "feishu_option": "稳健型"},
    "成熟型": {"line": None, "emoji": "🟡", "hue": "Yellow",
               "feishu_option": "成熟型"},
    "风险型": {"line": None, "emoji": "🔴", "hue": "Red",
               "feishu_option": "风险型"},
    # —— 选品结论（飞书 Base「商品推荐记录 · 结论」）——
    "能带": {"line": "账号状态好，适合加大带货频率", "emoji": "🟢", "hue": "Green",
             "feishu_option": "🟢 能带"},
    "慎带": {"line": "近期数据波动，选品从严、先测品", "emoji": "🟡", "hue": "Orange",
             "feishu_option": "🟡 慎带"},
    "别带": {"line": "账号数据异常，先养内容再带货", "emoji": "🔴", "hue": "Red",
             "feishu_option": "🔴 别带"},
}


def verdict_key(raw: str) -> str:
    """飞书字段原值 → 结论裸词（剥掉 emoji 前缀）。

    「🟢 能带」→「能带」；「潜力型」→「潜力型」。H5 拼 .v-* CSS 类名前必须过这个，
    否则会拼出不存在的类名 → 结论标签掉底色（2026-10-22 实测发现）。
    """
    s = str(raw or "")
    out = []
    for ch in s:
        if ch.isalnum() or "一" <= ch <= "鿿":
            out.append(ch)
    return "".join(out)


# 结论没定义一句话时的兜底（与 2026-10-22 之前 H5 的行为逐字一致）
NO_LINE_FALLBACK = "数据积累中，每周一更新结论"

# 合作机会状态（飞书 Base「合作机会 · 状态」）→ hue，与 H5 .st-* 语义色一一对应
COOP_STATUS_HUE = {
    "待评估": "Orange",   # H5: #fdf3e7 / #fa9d3b 琥珀
    "有意向": "Wathet",   # H5: #e8f0fe / #2f6fed 蓝
    "已合作": "Green",    # H5: #e8f8ef / #05914d 绿
    "不合适": "Gray",    # H5: #f2f2f2 / #999 灰
}

# 账号档案「最新快照」→ hue：是=绿（正/当前），否=灰（历史）
SNAPSHOT_HUE = {"是": "Green", "否": "Gray"}


def verdict_line(verdict: str) -> str:
    """H5 顶部「本周一句话」的文案。与历史行为逐字一致（未定义的结论走兜底）。"""
    item = VERDICT_UI.get(verdict) or {}
    return item.get("line") or NO_LINE_FALLBACK


def verdict_emoji(verdict: str) -> str:
    """结论 → emoji（H5 JS 孪生的 Python 侧，守卫脚本用它对拍）。"""
    item = VERDICT_UI.get(verdict) or {}
    return item.get("emoji") or "⚪"
