"""商品意图：达人点名了某个具体商品，却没给商品标识时 → 引导补「链接或商品ID」。

为什么需要它（2026-10-22 老大截图实锤）
--------------------------------------------------
达人问：
    「Disney/迪士尼草莓熊磁吸二合一暖手生日礼物送女友闺蜜冬季保暖 这款可以带不」
bot 回：
    「我是你的带货选品助手，账号分析、选品建议、商品判断都可以找我，其他问题帮不上忙哦～」
老大：怎么可以这样回复，应该回复「请提供一下商品的链接或者商品ID」。

**根因不是模型笨**（设计层问题）：
商品判断有**硬前置** —— 必须拿到商品标识（链接 / 商品ID / 截图）才能查真值
（价格、佣金、月销、类目），这是 §8「商品ID → 平台接口」的数据契约。
但**路由层从没表达过这个前置**：
    商品名 + 判断意图 → 路由到 QA → 模型看不到实物 → 拒答（提示词教它无关问题输出「拒」）
    → 拒答分支没有「缺标识 → 要标识」这一档 → 掉进通用兜底「其他问题帮不上忙」。
⇒ 「这个请求需要什么输入」被压成了「这个请求答不了」。**要标识 ≠ 答不了。**

设计：把「商品判断意图 × 缺商品标识」显式化成一层，和「无关问题」区分开。
三层防御（任何一层单独生效都能正确回话）：
    L1 路由层（确定性，零 LLM / 零积分）：本模块 `missing_product_ref_question`
    L2 商品ID 路由：达人按引导语只回一串数字 → 也要能收（`find_bare_product_id`）
    L3 拒答兜底层：模型都拒了才触发，判据可以放宽（executors 侧调用）

**唯一事实来源**：话术与判据都只在本模块，handler / executors 都从这里取，
别在别处再写一份「请提供链接或商品ID」—— 改一处就够。
"""
import re

# 达人要补的东西（老大原话口径：链接 或 商品ID）
NEED_PRODUCT_REF_REPLY = (
    "请提供一下商品的链接或者商品ID（发商品截图也行），"
    "我才能判断这款能不能带～"
)

# ---- 判据 ------------------------------------------------------------------

# 商品指向词：达人点名了「某一个具体商品」（不是泛泛谈类目）
_POINTER_RE = re.compile(
    r"这款|这个款|本款|这款商品|这个商品|这款产品|这个产品|"
    r"这款宝贝|这个宝贝|这款单品|这个单品|这款货|这个货|"
    r"这款礼物|这个礼物|这个链接|这款链接|这瓶|这盒|这包|这双|这个礼盒|这款礼盒"
)

# 判断词：达人在问「能不能带 / 值不值 / 适不适合我」
_JUDGE_RE = re.compile(
    r"能不能带|可以带|能带|带不带|带不|能带货|带货吗|值得带|值不值|值得买|"
    r"适合我|适不适合我|能不能卖|能卖吗|能不能推|能推吗|能推|"
    r"匹配吗|合适吗|可以买吗|要不要带|该不该带|好不好带|能不能带不|可以带不"
)

# 商品ID：17-20 位数字（抖音官方口径 19 位，历史见过 18 位）
_BARE_ID_RE = re.compile(r"(?<!\d)(\d{17,20})(?!\d)")

# 任何 http 链接（链接里的数字不算裸 ID）
_URL_RE = re.compile(r"https?://", re.I)


def has_product_ref(text: str) -> bool:
    """这条消息里是否已经带了商品标识（链接 或 裸商品ID）。"""
    t = (text or "").strip()
    if not t:
        return False
    if _URL_RE.search(t):
        return True
    return bool(find_bare_product_id(t))


def find_bare_product_id(text: str) -> str:
    """从文本里找**裸**商品ID（不在链接里的）。

    为什么需要它（2026-10-22）：handler 的 `DOUYIN_RE` 是 `^[A-Za-z0-9_]{5,24}$`
    —— 一串 19 位纯数字**会被当成抖音号**抢走。
    所以既然我们让达人「发商品ID」，就必须能收下它，否则是空头支票。
    """
    t = (text or "").strip()
    if not t:
        return ""
    if _URL_RE.search(t):
        # 链接里的数字归链接路由管，不单独当裸 ID
        t = re.sub(r"https?://\S+", " ", t)
    m = _BARE_ID_RE.search(t)
    return m.group(1) if m else ""


def wants_product_judgement(text: str) -> bool:
    """是否在问某个具体商品的「能不能带」。"""
    t = (text or "").strip()
    if not t:
        return False
    return bool(_JUDGE_RE.search(t))


def has_product_pointer(text: str) -> bool:
    """是否点名了某一个具体商品。"""
    t = (text or "").strip()
    if not t:
        return False
    return bool(_POINTER_RE.search(t))


def missing_product_ref_question(text: str) -> bool:
    """L1 判据：点名了具体商品 + 在问能不能带 + 但没给商品标识。

    刻意**保守**：必须同时有「商品指向词」和「判断词」。
    「我适合带食品吗」这种泛类目策略问题没有指向词 → 不命中，
    照旧走 LLM（能基于账号数据答），别把策略问题变成要链接。
    """
    t = (text or "").strip()
    if not t:
        return False
    if has_product_ref(t):
        return False
    return has_product_pointer(t) and wants_product_judgement(t)


def refusal_needs_product_ref(text: str) -> bool:
    """L3 判据：模型**已经拒答**了，才用来决定回「要标识」还是通用兜底。

    比 L1 宽是安全的 —— 前提是模型拒了：
    泛类目策略问题模型不会拒，拒了才说明它要的是「某个具体商品的真值」。
    """
    t = (text or "").strip()
    if not t:
        return False
    if has_product_ref(t):
        return False
    return wants_product_judgement(t)
