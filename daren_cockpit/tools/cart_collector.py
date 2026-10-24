#!/usr/bin/env python3
"""达人选品车采集器 —— **排品池的真源**（达人真实「接了哪些品」）。

起因（老大 2026-10-24 反馈）
----------------------------
「达人1 没有做到我说的那个功能：达人手里每天都要接很多的品，那这些品就需要 AI 帮忙安排、提醒」。

排查发现排品算法、提醒调度全都建好了，但**排品池永远是空的** ⇒ 入口从根上搞错了：
我们靠「达人发一句魔法口令（接了 xxx）」来登记接品，**现实中没人会发这条口令**
（实测：飞书两个租户 200+ 条消息里，`接了`/`今天推啥` 触发数 = 0）。

达人真实的「接的品」在**平台的选品车**里 —— 他在选品广场点「加选品车」就是接了这个品。
平台自己给的契约（2026-10-24 实测）：

| 用途 | 接口 | 关键字段 |
|---|---|---|
| **当前登录达人身份** | `GET /index/getUser` | `user_name`(昵称) / `origin_uid`(抖音uid) / `shop_id` / `user_id` |
| **选品车商品** | `POST /selection_cart_pc_api/card/m_get_card` | body `{page,page_size,key_word,view_type,common_filter,version}`；`data.cart_view_card[].cards[].product_card` |
| 选品车角标计数 | `POST /selection_cart_pc_api/comm/m_get_cart_sum` | body `{sum_scene:[0]}`；`data.cart_sums[]`（sum_type=1 全部 / 2 未加橱窗） |

商品卡字段口径（★ 单位坑）：
- `base_product_info.price` = **分**（6800 ⇒ ¥68.00）
- `business_operation_info.cos_ratio` = 佣金率**万分比**（1000 ⇒ 10%）
- `business_operation_info.ad_cos_ratio` = 投放期佣金万分比（600 ⇒ 6%）
- `business_operation_info.monthly_sale` / `good_ratio` / `is_in_window`
- `base_shop_info.exp_score` = 店铺体验分；`base_shop_info.shop_name`
- `base_product_info.new_category_id` = 一级/二级/三级类目 + 类目id

落库两处：
1. `products` —— 按 `platform_product_id` **补建档**（选品车的品经常不在我们库里）
2. `deals` —— 登记成排品池的合作单（`deal_source='选品车'`，`status='approved'`）

⚠️ 只读达人端（`PRAGMA query_only` 语义：不改达人端任何状态）。
⚠️ 只写**达人端登录账号自己的**选品车 ⇒ 数据归属到那一个达人（不是全体达人）。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

DB = _ROOT / "data" / "solobrave.db"
WORKER = os.environ.get("SOLOBRAVE_WORKER", "http://127.0.0.1:8095")

CART_PATH = "/selection_cart_pc_api/card/m_get_card"
CART_SUM_PATH = "/selection_cart_pc_api/comm/m_get_cart_sum"
IDENTITY_PATH = "/index/getUser"

DEAL_SOURCE = "选品车"


# ────────────────────────── 达人端取数 ──────────────────────────

def _worker_fetch(path: str, method: str = "GET", body: Any = None,
                  timeout: int = 30, tag: str = "") -> dict:
    """走 buyin_worker 控制口在达人端页面上下文里 fetch（带登录 cookie）。

    ★ 用 `tag` 定位自己的结果 —— `/daren-fetch` 只有一个槽位，
      连发多个请求会互相覆盖（2026-10-24 踩过：取到的都是第一个的结果）。
    """
    payload = {"path": path, "method": method}
    if body is not None:
        payload["body"] = body
    payload["tag"] = tag or path.strip("/").replace("/", "_")[:40]
    req = urllib.request.Request(
        f"{WORKER}/daren-fetch", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        r.read()
    # 结果是异步的：轮询 GET /daren-fetch?tag=…（取**自己**那条）
    for _ in range(30):
        time.sleep(1.0)
        with urllib.request.urlopen(
                f"{WORKER}/daren-fetch?tag={payload['tag']}", timeout=timeout) as r:
            out = json.loads(r.read().decode("utf-8"))
        if out.get("error") == "not run yet":
            continue
        res = out.get("out") or {}
        text = res.get("text")
        if not text:
            if out.get("error"):
                return {}
            continue
        try:
            inner = json.loads(text)
        except (TypeError, ValueError):
            continue
        if inner.get("code") == 0 or inner.get("data"):
            return inner
    return {}


def fetch_identity() -> dict:
    """当前登录的达人身份（`/index/getUser`）。"""
    d = _worker_fetch(IDENTITY_PATH, "GET")
    return (d or {}).get("data") or {}


def fetch_cart_sum() -> list[dict]:
    d = _worker_fetch(CART_SUM_PATH, "POST", {"sum_scene": [0]})
    return ((d or {}).get("data") or {}).get("cart_sums") or []


def fetch_cart(page: int = 1, page_size: int = 50) -> list[dict]:
    """选品车商品卡列表。"""
    d = _worker_fetch(CART_PATH, "POST", {
        "page": page, "page_size": page_size, "key_word": "",
        "view_type": 0, "common_filter": {}, "version": 2})
    out = []
    for view in ((d or {}).get("data") or {}).get("cart_view_card") or []:
        for c in view.get("cards") or []:
            pc = c.get("product_card")
            if pc:
                out.append(pc)
    return out


# ────────────────────────── 解析 ──────────────────────────

def _yuan(cent: Any) -> float:
    """分 → 元。"""
    try:
        return round(float(cent) / 100.0, 2)
    except (TypeError, ValueError):
        return 0.0


def _wan_ratio(v: Any) -> float:
    """万分比 → 百分数。1000 ⇒ 10.0%"""
    try:
        return round(float(v) / 100.0, 2)
    except (TypeError, ValueError):
        return 0.0


def parse_card(pc: dict) -> dict:
    """商品卡 → 归一化商品维度（单位全部转成「元 / 百分数」）。"""
    base = pc.get("base_product_info") or {}
    biz = pc.get("business_operation_info") or {}
    shop = pc.get("base_shop_info") or {}
    cat = base.get("new_category_id") or {}
    tags = []
    ti = pc.get("tag_info") or {}
    for t in ti.get("text_product_tag") or []:
        if isinstance(t, dict) and t.get("text"):
            tags.append(str(t["text"]))
    comm: dict[str, Any] = {}
    main = _wan_ratio(biz.get("cos_ratio"))
    ad = _wan_ratio(biz.get("ad_cos_ratio"))
    if main > 0:
        comm["default"] = main
    if ad > 0:
        comm["投放期"] = ad
    cat_path = "/".join(x for x in (
        str(cat.get("first_name") or ""), str(cat.get("second_name") or ""),
        str(cat.get("third_name") or "")) if x)
    return {
        "platform_product_id": str(pc.get("product_id") or ""),
        "promotion_id": str(pc.get("promotion_id") or ""),
        "name": (str(base.get("title") or "").strip()),
        "price": _yuan(base.get("price")),
        "commission_rates": comm,
        "category": cat_path,
        "category_ids": "/".join(str(cat.get(k) or "") for k in
                                  ("first_id", "second_id", "third_id")),
        "shop_score": float(shop.get("exp_score") or 0) or None,
        "shop_name": str(shop.get("shop_name") or ""),
        "monthly_sales": int(biz.get("monthly_sale") or 0),
        "good_ratio": float(biz.get("good_ratio") or 0) or None,
        "in_window": bool(biz.get("is_in_window")),
        "tags": tags,
        "detail_url": str(base.get("detail_url") or ""),
        "cover": str(base.get("cover") or ""),
    }


# ────────────────────────── 达人归属 ──────────────────────────

def match_talent(conn, identity: dict) -> Optional[dict]:
    """把达人端登录账号 → 我们库里的 `talents` 行。

    判据顺序（越靠前越可信）：
      ① `origin_uid` = 我们存的 `douyin_uid`（平台唯一 uid，最准）
      ② 昵称 `user_name` 命中 `talents.name`（我们库里的名字常带后缀，用「包含」匹配）
      ③ 橱窗名 `shop_name` 命中
    """
    uid = str(identity.get("origin_uid") or "").strip()
    if uid:
        r = conn.execute("SELECT * FROM talents WHERE douyin_uid=?",
                         (uid,)).fetchone()
        if r:
            return dict(r)
    name = str(identity.get("user_name") or "").strip()
    if name:
        for r in conn.execute("SELECT * FROM talents WHERE name LIKE ?",
                              (f"%{name}%",)):
            return dict(r)
    for s in identity.get("shops") or []:
        sn = str(s.get("shop_name") or "").strip()
        if sn:
            for r in conn.execute("SELECT * FROM talents WHERE name LIKE ?",
                                  (f"%{sn}%",)):
                return dict(r)
    return None


# ────────────────────────── 落库 ──────────────────────────

def _upsert_product(conn, p: dict, now: int) -> str:
    """按 `platform_product_id` 建档（已存在则补空缺字段，不覆盖已有真值）。"""
    row = conn.execute(
        "SELECT id FROM products WHERE platform_product_id=?",
        (p["platform_product_id"],)).fetchone()
    if row:
        pid = row["id"]
        # 只补空缺（不覆盖已有真值）
        for col, val in (("price", p["price"]),
                         ("category", p["category"]),
                         ("shop_score", p["shop_score"]),
                         ("good_ratio", p["good_ratio"]),
                         ("monthly_sales", p["monthly_sales"])):
            if val in (None, "", 0):
                continue
            cur = conn.execute(
                f"SELECT {col} FROM products WHERE id=?", (pid,)).fetchone()
            if cur and cur[0] in (None, "", 0):
                conn.execute(f"UPDATE products SET {col}=? WHERE id=?", (val, pid))
        if p["commission_rates"]:
            cur = conn.execute("SELECT commission_rates FROM products WHERE id=?",
                               (pid,)).fetchone()
            try:
                old = json.loads((cur[0] if cur else "") or "{}")
            except (TypeError, ValueError):
                old = {}
            if not isinstance(old, dict):
                old = {}
            if not old.get("default") and p["commission_rates"].get("default"):
                old["default"] = p["commission_rates"]["default"]
                if p["commission_rates"].get("投放期"):
                    old["投放期"] = p["commission_rates"]["投放期"]
                conn.execute("UPDATE products SET commission_rates=? WHERE id=?",
                             (json.dumps(old, ensure_ascii=False), pid))
        conn.execute("UPDATE products SET daren_synced_at=? WHERE id=?",
                     (now, pid))
        return pid
    # 新建档
    pid = "prod_cart_" + p["platform_product_id"][-10:]
    conn.execute(
        "INSERT INTO products (id,name,price,category,category_ids,"
        "commission_rates,shop_score,good_ratio,monthly_sales,tags,"
        "platform_product_id,status,daren_synced_at,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (pid, p["name"], p["price"], p["category"], p["category_ids"],
         json.dumps(p["commission_rates"], ensure_ascii=False) if p["commission_rates"] else None,
         p["shop_score"], p["good_ratio"], p["monthly_sales"] or None,
         json.dumps(p["tags"], ensure_ascii=False) if p["tags"] else None,
         p["platform_product_id"], "active", now, now, now))
    return pid


def _register_deal(conn, talent_id: str, product_id: str, p: dict,
                   now: int) -> str:
    """登记成排品池的合作单（幂等）。返回 deal_id 或 ''。"""
    exist = conn.execute(
        "SELECT id FROM deals WHERE talent_id=? AND product_id=? "
        "AND status IN ('negotiating','sample_sent','approved') LIMIT 1",
        (talent_id, product_id)).fetchone()
    if exist:
        return exist["id"]
    deal_id = "deal_" + p["platform_product_id"][-10:] + "_" + str(now)[-4:]
    comm = (p["commission_rates"] or {}).get("default") or 0
    conn.execute(
        "INSERT INTO deals (id,talent_id,product_id,product_name,deal_type,"
        "commission_rate,status,scheduled_at,deal_source,created_by,"
        "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (deal_id, talent_id, product_id, p["name"], "video", comm, "approved",
         0, DEAL_SOURCE, "cart_collector", now, now))
    return deal_id


# ────────────────────────── 主流程 ──────────────────────────

def sync(apply: bool = False) -> dict:
    ident = fetch_identity()
    if not ident:
        return {"ok": False, "error": "取不到达人端身份（/index/getUser 没返回）"}
    sums = fetch_cart_sum()
    cards = fetch_cart()
    products = [parse_card(c) for c in cards]
    out = {
        "ok": True,
        "identity": {"user_name": ident.get("user_name"),
                     "origin_uid": ident.get("origin_uid"),
                     "shop_id": ident.get("shop_id")},
        "cart_sum": [{"type": s.get("sum_type"), "name": s.get("name"),
                      "count": s.get("count")} for s in sums],
        "cart_count": len(products),
        "products": [{"id": p["platform_product_id"], "name": p["name"][:30],
                       "price": p["price"], "comm": p["commission_rates"],
                       "shop_score": p["shop_score"],
                       "good_ratio": p["good_ratio"]} for p in products],
    }
    if not apply:
        out["mode"] = "dry-run"
        return out
    conn = sqlite3.connect(str(DB), timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        t = match_talent(conn, ident)
        if not t:
            out["error"] = (f"选品车主人「{ident.get('user_name')}」在我们库里"
                            f"找不到对应达人（uid={ident.get('origin_uid')}）")
            out["mode"] = "apply"
            return out
        out["talent"] = {"id": t["id"], "name": t["name"],
                         "douyin_id": t.get("douyin_id"),
                         "nickname": ident.get("user_name")}
        now = int(time.time())
        # 达人端昵称（平台真值）—— 库里的 name 可能是运营随手写的
        #   （实测：「我的小号发给你跟我分析一下名称一百」），提醒标题用它才好看
        nick = str(ident.get("user_name") or "").strip()
        if nick and t.get("platform_nickname") != nick:
            try:
                conn.execute("UPDATE talents SET platform_nickname=? WHERE id=?",
                             (nick, t["id"]))
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE talents ADD COLUMN "
                             "platform_nickname TEXT DEFAULT ''")
                conn.execute("UPDATE talents SET platform_nickname=? WHERE id=?",
                             (nick, t["id"]))
        made_products, made_deals = [], []
        for p in products:
            if not p["platform_product_id"]:
                continue
            pid = _upsert_product(conn, p, now)
            if pid not in made_products:
                made_products.append(pid)
            did = _register_deal(conn, t["id"], pid, p, now)
            if did:
                made_deals.append(did)
        conn.commit()
        out["mode"] = "apply"
        out["products_upserted"] = made_products
        out["deals_registered"] = made_deals
        out["deal_count"] = len(made_deals)
    finally:
        conn.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="达人选品车采集（排品池真源）")
    ap.add_argument("--apply", action="store_true",
                    help="真写库（默认只看不写）")
    args = ap.parse_args()
    res = sync(apply=args.apply)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
