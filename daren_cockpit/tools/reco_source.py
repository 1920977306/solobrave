"""达人可见推荐链的**取数层** —— 唯一真源。

两个消费者（**必须共用一份**）：
  ① `daren_cockpit/server.py` —— 运营驾驶舱 `/api/cockpit` 的 `reco` 字段
  ② `daren_bot/handler.py`   —— 飞书达人问「推荐点什么 / 选品建议」

★ 同一份事实多个消费者 ⇒ 取数只写一份（别各写一套 SQL，
  上一轮刚踩过「两个消费端口径不一致」的老坑，见 AGENTS.md §8.8）。

⚠️ 为什么不直接 import `daren_cockpit.server`：
   那个模块**顶层就起线程池**（`_EXEC = ThreadPoolExecutor(max_workers=4)`）
   ⇒ bot 侧 import 会把线程池一起带起来 ⇒ 取数下沉到 `tools/` 这个纯工具层。

★★ 本模块是**达人可见面**的取数口 ⇒ 铁律：
   **只取达人端口径的列**（价格/佣金/类目/店铺体验分/好评率/榜单/带货达人数…）。
   ⛔ 绝不取我方口径的列：
      · `platform_order_daren_count` = 团长端「我方机构出单达人数」
      · `talent_count`               = 我方合作达人数
      · `platform_pay_gmv` / `platform_settle_gmv` = 我方 GMV
      —— 违反 AGENTS.md §8.1「不向达人暴露我方出单数据」。
   ⚠️ `platform_order_daren_count` 列名带 `platform_` 前缀、**看着像平台数据**，
      实际是我方团长端数据 ⇒ 极难靠看名字发现，闸门见 `tests/test_reco_data_source.py`。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from daren_bot import config
from daren_cockpit.tools import product_reco as reco

DEFAULT_DB = config.ROOT / "data" / "solobrave.db"

# ★ 达人可见面允许取的列（**白名单**，不是黑名单 ——
#   黑名单会被新加的我方列悄悄绕过，白名单不会）。
#   `influencer_count` = 达人端 `cooper_author_num` = 平台全商品带货达人数（达人端口径）。
PRODUCT_COLS = (
    "id", "name", "price", "price_range", "category", "commission_rates",
    "tags", "conversion_rate",
    "shop_score", "good_ratio", "video_sales_7d", "sales_7d", "board_rank",
    "influencer_count",
)

# 新维度列可能还没迁移（旧库）⇒ 有才取，缺了评分器会当「缺失」退出加权
_OPTIONAL_COLS = ("shop_score", "good_ratio", "video_sales_7d", "sales_7d",
                  "board_rank", "influencer_count")


def _db_path(db_path: Any = None) -> Path:
    return Path(db_path) if db_path else DEFAULT_DB


def load_product_pool(db_path: Any = None) -> list[dict]:
    """在架商品池（**达人可见面**的列）。

    ★ 只取 `PRODUCT_COLS` 白名单里的列 ⇒ 我方出单数据在这一层就被挡住，
      不会因为下游改代码而漏进来。
    """
    path = _db_path(db_path)
    if not path.is_file():
        return []
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        have = {r[1] for r in conn.execute("PRAGMA table_info(products)")}
        want = [c for c in PRODUCT_COLS if c in have]
        rows = conn.execute(
            f"SELECT {','.join(want)} FROM products WHERE status!='archived'"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def build_chain(talent_row: dict | None, top_n: int = 6,
                db_path: Any = None) -> dict:
    """达人 → 选品推理链（画像 → 类目策略 → 商品推荐 → 推荐指数★）。

    ⚠️ 拿不到画像就返回空 dict —— **不编粉丝画像**（只丢不造）。
    """
    if not talent_row:
        return {}
    try:
        products = load_product_pool(db_path)
        return reco.recommend(dict(talent_row), products, top_n=top_n)
    except Exception as e:  # noqa: BLE001
        import logging
        logging.warning("reco chain failed: %s", e)
        return {}
