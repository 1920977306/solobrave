"""守门人：复用层的「省了多少」必须按**每种命中自己的真实消耗**算。

★ 背景（2026-10-26 老大拍板「没有消耗肯定是 0 分」之后暴露的）：
  `daren_bot/tools/cache_probe.py` 的复用看板原来把两种缓存命中
  **合并成一个数**（`action IN ('collect_product_cached','collect_product_verdict_cached')`），
  然后按「省 `COST_COLLECT_PRODUCT - COST_PRODUCT_CACHED` 分 + 一次采集 + 一次 LLM」算 ⇒ **两处都错**：

  · **结论缓存命中**（`collect_product_verdict_cached`）是**零消耗**（0 分，
    不调浏览器也不调 LLM）⇒ 它省的是**整笔 `COST_COLLECT_PRODUCT`**，
    **不是** 15 分；合并算 ⇒ **每次少算 5 分**。
  · **商品数据缓存命中**（`collect_product_cached`）只省了「开浏览器采集」，
    **`_llm_product_verdict()` 照跑** ⇒ **它根本没省 LLM**；
    合并算 ⇒ 把没省的 LLM 也算进「省了 N 次 LLM」。

⇒ 守卫目标：① 两种命中**不能**被当成一种算分；
             ② 商品数据缓存命中**不许**被算成「省了 LLM」。
"""
from daren_bot import config
from daren_bot.tools.cache_probe import reuse_savings


def test_verdict_cache_hit_saves_full_price_plus_one_collect_and_one_llm():
    """结论缓存命中 = 零消耗 ⇒ 省的是整笔采集价，且真的省了采集与 LLM。"""
    st = reuse_savings(0, 1, 0)
    assert st["saved"] == config.COST_COLLECT_PRODUCT, (
        "结论缓存命中是零消耗(0 分)，省下的应是整笔 "
        f"{config.COST_COLLECT_PRODUCT} 分，而不是 {config.COST_PRODUCT_CACHED} 分")
    assert st["saved_llm"] == 1
    assert st["saved_collect"] == 1


def test_product_data_cache_hit_saves_price_but_not_llm():
    """商品数据缓存命中 = LLM 照跑 ⇒ 不得计入「省了 LLM」。"""
    st = reuse_savings(0, 0, 1)
    assert st["saved"] == config.COST_COLLECT_PRODUCT - config.COST_PRODUCT_CACHED
    assert st["saved_llm"] == 0, (
        "商品数据缓存命中时 `_llm_product_verdict()` 照跑 ⇒ 没省 LLM，不该计入")
    assert st["saved_collect"] == 1


def test_two_hit_kinds_are_not_merged_into_one_average():
    """两种命中各 1 次 ⇒ 分数必须按各自消耗量相加（合并平均会算错）。"""
    st = reuse_savings(0, 1, 1)
    assert st["saved"] == (config.COST_COLLECT_PRODUCT
                            + config.COST_COLLECT_PRODUCT - config.COST_PRODUCT_CACHED)
    assert st["saved_llm"] == 1, "只有结论命中那一次省了 LLM"
    assert st["saved_collect"] == 2


def test_qa_cache_hit_saves_qa_price_and_one_llm():
    st = reuse_savings(1, 0, 0)
    assert st["saved"] == config.COST_QA
    assert st["saved_llm"] == 1
    assert st["saved_collect"] == 0


def test_no_hit_saves_nothing():
    st = reuse_savings(0, 0, 0)
    assert st == {"saved": 0, "saved_llm": 0, "saved_collect": 0}
