"""达人端「商品详情 top 带货达人」采集落库（闭环二期数据源，2026-11-07）。

链路（老大口径 AGENTS.md §8.1：商品数据走达人端，详情页有「前几位带货达人 + 视频」）：

    buyin_worker（页面上下文，带签名）
      → 商品详情 top 带货达人接口（路径待扫码实测后钉死，见 buyin_worker 白名单）
      → parse_top_talents()（纯函数，只丢不造）
      → platform_product_talent（主库，本模块落库）
      → daren_bot.reco_log.backfill_sold()（推荐→出单配对回填）

三条铁律：
  ① 只丢不造：接口没给的维度（gmv/sales）一律 NULL；解析不出来的行**丢弃**，
    不编排行/名字凑数。
  ② 达人标识不靠格式猜（§8.1 三类混装）：author_id / douyin_id / nickname
    三个都留，配对优先 author_id；rank 反映详情页第几位。
  ③ 「不在 top N」≠「没出单」：榜单只列前几位，配对不上保持 sold_gmv NULL，
    那是「采不到」，不是「卖了 0」。
"""
from __future__ import annotations

import hashlib
import json
import time

# ─────────────────────────── 解析（纯函数）───────────────────────────
# ⚠️ 字段映射为「扫码实测前」的候选键多路尝试：每维度取第一个非空候选键。
#   实测拿到真响应后：把命中的键序钉死、删掉其余候选，并在这里写实测样例链接。
#   在那之前**不许**把任何解析结果写进 sold 回填链路以外的地方当结论展示。

_AUTHOR_ID_KEYS = ("author_id", "authorId", "uid", "user_id", "promoter_id",
                   "kol_id", "dy_uid")
_DOUYIN_ID_KEYS = ("douyin_id", "douyinId", "dy_id", "sec_uid")
_NICKNAME_KEYS = ("nickname", "name", "nick_name", "author_name", "title")
_GMV_KEYS = ("gmv", "pay_gmv", "settle_gmv", "sales_gmv", "amount",
             "pay_amount")
_SALES_KEYS = ("sales", "pay_num", "order_pay_num", "sold_num", "sale_num",
               "order_count")


def _first(item: dict, keys) -> str:
    for k in keys:
        v = item.get(k)
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return ""


def _first_num(item: dict, keys):
    for k in keys:
        v = item.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        try:
            return float(str(v).replace(",", ""))
        except (TypeError, ValueError):
            continue
    return None


def normalize_top_talent(item: dict) -> dict | None:
    """一条原始达人记录 → 规范行。 identifier 一个都没有（author_id /
    douyin_id / nickname 全空）⇒ 返回 None（丢弃，不凑数）。"""
    if not isinstance(item, dict):
        return None
    author_id = _first(item, _AUTHOR_ID_KEYS)
    douyin_id = _first(item, _DOUYIN_ID_KEYS)
    nickname = _first(item, _NICKNAME_KEYS)
    if not (author_id or douyin_id or nickname):
        return None
    return {
        "author_id": author_id,
        "douyin_id": douyin_id,
        "nickname": nickname,
        "gmv": _first_num(item, _GMV_KEYS),      # None = 平台没给（只丢不造）
        "sales": _first_num(item, _SALES_KEYS),
    }


def parse_top_talents(raw, platform_product_id: str,
                      now: int | None = None) -> list[dict]:
    """接口原始响应 → 规范行列表（带 rank / stat_date / captured_at）。

    结构兼容：直接 list / {"data": [...]} / {"data": {"list": [...]}} /
    {"list": [...]}——哪层是 list 用哪层；全都不是 ⇒ 返回 []
    （只丢不造：结构不认识就不产出假空榜以外的任何东西，由调用方判失败）。
    """
    now = int(now or time.time())
    node = raw
    for _ in range(4):
        if isinstance(node, list):
            break
        if isinstance(node, dict):
            node = (node.get("data") if node.get("data") is not None
                    else node.get("list"))
            continue
        node = None
        break
    if not isinstance(node, list):
        return []
    rows = []
    for i, item in enumerate(node):
        norm = normalize_top_talent(item)
        if norm is None:
            continue
        norm.update({
            "platform_product_id": str(platform_product_id),
            "rank": i + 1,                       # 详情页第几位（1 起）
            "stat_date": time.strftime("%Y-%m-%d", time.localtime(now)),
            "captured_at": now,
            "extra_json": json.dumps(item, ensure_ascii=False)[:4000],
        })
        rows.append(norm)
    return rows


# ─────────────────────────── 落库 ───────────────────────────

def _row_id(row: dict) -> str:
    key = "|".join([row["stat_date"], row["platform_product_id"],
                    row["author_id"], row["nickname"]])
    return "ppt_" + hashlib.sha1(key.encode()).hexdigest()[:24]


def save_rows(conn, rows: list[dict]) -> int:
    """幂等落库（UNIQUE(stat_date, platform_product_id, author_id, nickname)
    冲突 ⇒ 覆盖更新，同一商品同一天重复采集不会堆重复行）。"""
    if not rows:
        return 0
    n = 0
    for r in rows:
        conn.execute(
            "INSERT INTO platform_product_talent (id, stat_date,"
            " platform_product_id, rank, author_id, douyin_id, nickname,"
            " gmv, sales, extra_json, captured_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(stat_date, platform_product_id, author_id, nickname)"
            " DO UPDATE SET rank=excluded.rank, douyin_id=excluded.douyin_id,"
            " gmv=excluded.gmv, sales=excluded.sales,"
            " extra_json=excluded.extra_json, captured_at=excluded.captured_at",
            (_row_id(r), r["stat_date"], r["platform_product_id"],
             int(r.get("rank") or 0), r.get("author_id") or "",
             r.get("douyin_id") or "", r.get("nickname") or "",
             r.get("gmv"), r.get("sales"),
             r.get("extra_json") or "{}", int(r.get("captured_at") or 0)))
        n += 1
    conn.commit()
    return n
