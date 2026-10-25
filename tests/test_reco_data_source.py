"""推荐指数的**数据源纪律**（2026-10-25 加的护栏）。

锁住一条铁律：**达人可见的推荐里不许出现我方出单数据**（AGENTS.md §8.1
「商品数据只走达人端，不向达人暴露我方出单数据」）。

★ 这颗雷的隐蔽处（为什么值得单独立一条闸门）：
  `products.platform_order_daren_count` 列名带 `platform_` 前缀、**看着像平台数据**，
  实际是**团长端**的「我方机构出单达人数」（口径见
  `daren_cockpit/tools/filing_products.py` 的注释）。
  它一度被 `product_reco` 当成「达人已出单数」维度参与打分
  （覆盖 81%、12 种取值）⇒ 一旦把推荐器接进飞书（老大目标 3），
  达人就能从自己的推荐指数里反推我们机构出了多少单。

合规替代 = **达人端** `cooper_author_num` = 平台全商品带货达人数
  （存 `products.influencer_count`）—— 达人端数据，且区分度更好（24 种 vs 12 种）。

⚠️ 闸门的作用范围刻意**只盯推荐链路**：
  `talent_count` / `platform_pay_gmv` 在 `solobrave-server.py`、
  `captain_stats.py` 里是**合法的内部经营用法**（商品档案、团长端看板），
  全仓一刀切会误伤 ⇒ 只在「推荐器 + 推荐取数的两个消费端」上卡。
"""
import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MOD_PATH = ROOT / "daren_cockpit" / "tools" / "product_reco.py"

spec = importlib.util.spec_from_file_location("product_reco", MOD_PATH)
pr = importlib.util.module_from_spec(spec)
sys.modules["product_reco"] = pr          # ★ dataclass + future annotations 需要
spec.loader.exec_module(pr)


# ★ 我方（团长端 / 内部经营）口径的列。
#   这些数据**可以**进内部运营看板，**不许**进达人可见的推荐。
OUR_SIDE_COLS = (
    "platform_order_daren_count",   # 团长端「我方机构出单达人数」← 真正的泄露源
    "platform_order_pay_num",       # 团长端出单数
    "platform_pay_gmv",             # 团长端支付 GMV
    "platform_settle_gmv",          # 团长端结算 GMV
    "talent_count",                 # 我方合作达人数
)

# 合法的达人端替代品
DAREN_SIDE_COL = "influencer_count"


def _literals_of(rel_path: str, func_name: str = "") -> set:
    """文件（或某个函数）里出现的所有**字符串字面量**。

    ★ 用 AST 而不是正则：列名写在**注释**里是允许的（那是在说明别用它），
      判红只认真正参与取数/打分的字面量。
    """
    path = ROOT / rel_path
    tree = ast.parse(path.read_text(encoding="utf-8"))
    target = tree
    if func_name:
        found = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and node.name == func_name:
                found = node
                break
        assert found is not None, f"{rel_path} 里找不到函数 {func_name}"
        target = found
    return {n.value for n in ast.walk(target)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)}


# ─────────────── 行为：取数口径 ───────────────

def test_sold_dimension_reads_daren_side_influencer_count():
    """★ 达人出单数维度必须读**达人端** `influencer_count`。"""
    row = pr._normalized_row({"influencer_count": 24,
                              "platform_order_daren_count": 999})
    assert row["talents_sold"] == 24.0, \
        "达人出单数维度没读达人端 influencer_count（读了别的口径）"


def test_sold_dimension_never_falls_back_to_our_sales_data():
    """★ 只剩我方出单数据时，必须当「没有真值」（0），不许拿去打分。

    这是**行为**闸门：只查源码不够 —— 万一哪天有人改了 fallback 顺序，
    这里会先红。
    """
    row = pr._normalized_row({"platform_order_daren_count": 999})
    assert row["talents_sold"] == 0.0, "我方出单数据漏进了推荐打分！"


def test_sold_dimension_label_says_platform_not_our_sales():
    """★ 维度文案不许再叫「达人已出单数」—— 那个名字会让人以为它是我方出单。"""
    labels = [d.label for d in list(pr.BASE_DIMS) + list(pr.OPTIONAL_DIMS)]
    assert "达人已出单数" not in labels, \
        "维度名还叫「达人已出单数」，下一个人还会拿我方出单数去填它"


# ─────────────── 结构：推荐器本体 ───────────────

def test_reco_engine_has_no_our_side_columns():
    """★ 推荐器里不得出现任何我方口径的列。"""
    lits = _literals_of("daren_cockpit/tools/product_reco.py")
    bad = sorted(set(OUR_SIDE_COLS) & lits)
    assert not bad, f"推荐器里出现我方出单列（会泄露我方经营数据给达人）：{bad}"


def test_reco_engine_reads_daren_side_column():
    """★ 推荐器必须读达人端 `influencer_count`（不能只是「没读我方列」）。"""
    lits = _literals_of("daren_cockpit/tools/product_reco.py")
    assert DAREN_SIDE_COL in lits, \
        f"推荐器没读达人端 {DAREN_SIDE_COL}"


# ─────────────── 结构：推荐取数的两个消费端 ───────────────

@pytest.mark.parametrize("rel_path,func_name", [
    ("daren_cockpit/server.py", "_build_reco_chain"),
    ("solobrave-server.py", "_handle_get_product_recommendations"),
])
def test_recommend_consumers_use_daren_side_column(rel_path, func_name):
    """★ 两个推荐取数的消费端都必须取达人端列、都不许取我方出单达人数。

    ⚠️ 只扫**这个函数**的 AST：同一个文件里 `talent_count` 等在商品档案、
       团长端看板上是合法用法，全文件一刀切会误伤。
    """
    lits = _literals_of(rel_path, func_name)
    assert "platform_order_daren_count" not in lits, \
        f"{rel_path}::{func_name} select 了我方出单达人数（会泄露给达人）"
    assert DAREN_SIDE_COL in lits, \
        f"{rel_path}::{func_name} 没取达人端 {DAREN_SIDE_COL}"
