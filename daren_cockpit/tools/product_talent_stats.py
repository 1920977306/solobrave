"""达人端「商品详情 top 带货达人」采集落库（闭环二期数据源，2026-11-07）。

链路（老大口径 AGENTS.md §8.1：商品数据走达人端，详情页有「前几位带货达人 + 视频」）：

    buyin_worker（页面驱动，签名天然具备）
      → 导航商品决策页 → 点「带货内容」tab（页面自己发
        POST /pc/selection/decision/pack_detail，已扫码实测钉死）
      → worker 流量监听截全量响应 → /daren-kol 取件
      → parse_top_talents()（纯函数，只丢不造）
      → platform_product_talent（主库，本模块落库）
      → daren_bot.reco_log.backfill_sold()（推荐→出单配对回填）

★ 采集口为什么是页面驱动（2026-11-07 实测定案）：
  外部 fetch 重放 pack_detail **经常被平台风控返回空 200**（19:43-19:55
  六连空，参数逐字段对齐页面 XHR 也没用），而页面自己的 XHR 每次都能
  拿到全量数据 ⇒ 不跟签名斗智斗勇，直接让页面发请求、我们截响应。

实测契约（2026-11-07 达人端扫码实测，样本：漫花抽纸
product_id=3644272059510584408 / promotion_id=3696059559421573288，
原始响应留档 data/buyin_browser/manhua_content.json）：

  请求（buyin_worker 白名单 /pc/selection/decision/pack_detail，页面上下文 fetch）：
    POST https://buyin.jinritemai.com/pc/selection/decision/pack_detail
    {"scene_info": {"request_page": 2},
     "biz_id": "<promotion_id>",          # ⚠️ 是推广 id 不是商品 id
     "biz_id_type": 2,
     "data_module": "dynamic",
     "dynamic_params": {"param_type": 12,
       "sale_content_data_params": {"content_type": "video",
         "time_range": "30", "page_no": 1, "count": 12,
         "query_key": "", "sort_field": "sales_amount", "sort_type": "desc"}}}
    content_type: video/live/window/artc；time_range: 7/30/90。
    ⚠️ 必须先有 promotion_id：选品库 material_list 搜商品ID → 列表项的
    promotion_id（即决策页 URL 的 commodity_id）。

  响应：
    data.model.sale_content_data.content_list[]（同层 count = 总条数，翻页
    用 page_no*count 与 count 比）。单条关键字段（实测钉死）：
      author_name           达人昵称
      author_sec_id         加密 sec_uid（留档 extra_json，不作配对键）
      author_fans_count     粉丝数
      share_url             「https://www.douyin.com/share/user/<达人ID>」
                            → 尾部数字 = 平台达人 id（对 talents.platform_talent_id）
      format_sales          销量区间文案（如「2.5千+」）
      format_sales_amount   GMV 区间文案（如「1千+」）
      play_count/like_count/comment_count/share_count/order_conversion_rate
      content_id/content_title/content_cover/media_info.video_url/publish_time
      is_self_sale          是否自卖

三条铁律：
  ① 只丢不造：平台**只给区间文案不给真值**（format_sales_amount/format_sales），
    gmv/sales 一律 NULL——区间原文在 extra_json，报表层自取，绝不把
    「1千+」的下限冒充真值塞进 REAL 列。
  ② 达人标识不靠格式猜（§8.1 三类混装）：author_id 只从 share_url 尾部数字
    取（= talents.platform_talent_id 同口径）；nickname 用 author_name；
    douyin_id 接口没给，留空。
  ③ 「不在 top N」≠「没出单」：榜单只列前几位，配对不上保持 sold_gmv NULL，
    那是「采不到」，不是「卖了 0」。
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path

# ─────────────────────────── 解析（纯函数）───────────────────────────

_SHARE_URL_RE = re.compile(r"/share/user/(\d+)")
_LIST_PATH = ("data", "model", "sale_content_data", "content_list")


def _extract_list(raw):
    """响应 → content_list。结构不是实测契约 ⇒ None（由调用方判失败，不造空榜）。"""
    node = raw
    for key in _LIST_PATH:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node if isinstance(node, list) else None


def normalize_top_talent(item: dict) -> dict | None:
    """一条原始视频记录 → 规范行。author_id 与 nickname 都空 ⇒ None（丢弃，不凑数）。"""
    if not isinstance(item, dict):
        return None
    share = str(item.get("share_url") or "")
    m = _SHARE_URL_RE.search(share)
    author_id = m.group(1) if m else ""
    nickname = str(item.get("author_name") or "").strip()
    if not (author_id or nickname):
        return None
    fans = item.get("author_fans_count")
    try:
        fans_count = int(fans) if fans is not None else None
    except (TypeError, ValueError):
        fans_count = None
    return {
        "author_id": author_id,          # share_url 尾部数字；空 = 接口没给
        "douyin_id": "",                 # 接口没给真抖音号（只丢不造）
        "nickname": nickname,
        "gmv": None,                     # 平台只给区间文案，真值没给（只丢不造）
        "sales": None,
        "fans_count": fans_count,
    }


def parse_top_talents(raw, platform_product_id: str,
                      now: int | None = None) -> list[dict]:
    """接口原始响应 → 规范行列表（带 rank / stat_date / captured_at）。

    按达人聚合：同一达人多条视频（列表已按销售额降序）只留第一条
    （= 该达人卖得最好的那条），rank 取最高名次。响应结构不是实测契约
    ⇒ 返回 []（只丢不造，由调用方判失败）。
    """
    now = int(now or time.time())
    lst = _extract_list(raw)
    if lst is None:
        return []
    rows = []
    seen = set()                     # author_id 优先，其次 nickname
    for item in lst:
        norm = normalize_top_talent(item)
        if norm is None:
            continue
        key = norm["author_id"] or f"n:{norm['nickname']}"
        if key in seen:
            continue                 # 同达人后面的视频名次更低，丢弃
        seen.add(key)
        norm.update({
            "platform_product_id": str(platform_product_id),
            "rank": len(rows) + 1,   # 聚合后的「前几位」名次（1 起）
            "stat_date": time.strftime("%Y-%m-%d", time.localtime(now)),
            "captured_at": now,
            "extra_json": json.dumps(item, ensure_ascii=False)[:4000],
        })
        rows.append(norm)
    return rows


# ─────────────────────────── 采集（走 buyin_worker）───────────────────────────

WORKER = "http://127.0.0.1:8095"
WORKER_PATH = "/pc/selection/decision/pack_detail"
DECISION_URL = ("https://buyin.jinritemai.com/dashboard/merch-picking-library/"
                "merch-promoting?commodity_id={pid}&id={pid}")


def build_request(promotion_id: str, content_type: str = "video",
                  time_range: str = "30", page_no: int = 1,
                  count: int = 12) -> dict:
    """实测钉死的请求体（见模块 docstring 契约）。直连 fetch 用；
    页面驱动流程下这个请求由页面自己发出，签名天然具备。"""
    return {"scene_info": {"request_page": 2},
            "biz_id": str(promotion_id),
            "biz_id_type": 2,
            "data_module": "dynamic",
            "dynamic_params": {"param_type": 12,
                               "sale_content_data_params": {
                                   "content_type": content_type,
                                   "time_range": str(time_range),
                                   "page_no": int(page_no),
                                   "count": int(count),
                                   "query_key": "",
                                   "sort_field": "sales_amount",
                                   "sort_type": "desc"}},
            "extra": {}}


def _http_json(url: str, payload: dict | None = None, timeout: int = 30) -> dict:
    import urllib.request
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"},
        method="POST" if payload is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _wait_job(cid: str, timeout_s: int = 180) -> tuple[dict | None, str]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(3)
        try:
            job = _http_json(f"{WORKER}/jobs/{cid}")
        except Exception as e:  # noqa: BLE001
            return None, f"查 job 失败：{e}"
        st = job.get("status")
        if st == "done":
            raw = job.get("result")
            if not raw:
                return None, "worker 返回空结果"
            try:
                return (json.loads(raw) if isinstance(raw, str) else raw), ""
            except json.JSONDecodeError as e:
                return None, f"结果不是 JSON：{e}"
        if st in ("failed", "error"):
            return None, f"worker 采集失败：{job.get('stage') or job.get('error')}"
    return None, "超时"


def _poll_state(endpoint: str, match, timeout_s: float) -> dict | None:
    """轮询 worker 的 STATE 型端点（/daren-search /daren-click），
    直到返回结果满足 match(out)（STATE 里混着历史结果，必须内容匹配）。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(2)
        try:
            out = _http_json(f"{WORKER}{endpoint}")
        except Exception:  # noqa: BLE001
            continue
        if out.get('error') and 'not run yet' in str(out.get('error')):
            continue
        try:
            if match(out):
                return out
        except Exception:  # noqa: BLE001
            continue
    return None


def _in(out: dict, *keys: str) -> str:
    return " ".join(str(out.get(k) or '') for k in keys)


def fetch_top_talents(promotion_id: str, content_type: str = "video",
                      time_range: str = "30", timeout_s: int = 180
                      ) -> tuple[list[dict], str]:
    """promotion_id → 采集 + 解析 + 按达人聚合的规范行列表。

    采集口（2026-11-07 扫码实测定案）：外部 fetch 重放 pack_detail
    **经常被平台风控返回空 200**（19:43-19:55 六连空实测），而页面自己的
    XHR 必带完整签名 ⇒ **页面驱动**：导航商品决策页 → 点「带货内容」tab
    → worker 在流量监听里截全量响应 → /daren-kol 取件。

    返回 (rows, err)：err 非空 = 采集失败（rows 为 []，调用方别当空榜用）；
    「商品无带货内容」也会走 err（与「采不到」区分不了时宁说采不到）。
    """
    promotion_id = str(promotion_id).strip()
    if not promotion_id:
        return [], "promotion_id 为空"
    deadline = time.time() + timeout_s
    try:
        _http_json(f"{WORKER}/daren-kol?clear=1")           # ① 清捕获桶
        _http_json(f"{WORKER}/daren-search",                # ② 导航决策页
                   {"pid": "x", "tag": f"nav_{promotion_id[-8:]}",
                    "url": DECISION_URL.format(pid=promotion_id)})
        out = _poll_state(
            '/daren-search',
            lambda o: promotion_id in _in(o, 'goto_url', 'result_url',
                                          'start_url', 'url'),
            90)
        if out is None:
            return [], "导航商品决策页超时"
        if 'merch-promoting' not in _in(out, 'goto_url', 'result_url', 'url'):
            return [], f"决策页导航失败：{_in(out, 'error', 'goto_url')[:150]}"
        _http_json(f"{WORKER}/daren-click",                 # ③ 点带货内容 tab
                   {"text": "带货内容", "tag": f"kol_{promotion_id[-8:]}",
                    "wait": 8})
        _poll_state('/daren-click',
                    lambda o: (o.get('text') == '带货内容'
                               and promotion_id in _in(o, 'url')),
                    90)
        # ④ 轮询取件（页面 XHR 的响应被 worker 截获存桶）
        while time.time() < deadline:
            ent = _http_json(
                f"{WORKER}/daren-kol?promotion_id={promotion_id}")
            if ent.get('ok'):
                data = json.loads(ent['text'])
                product_id = ent.get('product_id') or promotion_id
                rows = parse_top_talents(data, product_id)
                if not rows:
                    return [], "捕获到响应但解析不出达人行（结构不符）"
                return rows, ""
            time.sleep(3)
        return [], ("采集超时：页面 XHR 未返回带货内容（该商品可能无带货内容，"
                    "或页面加载失败）")
    except Exception as e:  # noqa: BLE001
        return [], f"采集异常：{e}"


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
            " gmv, sales, fans_count, extra_json, captured_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(stat_date, platform_product_id, author_id, nickname)"
            " DO UPDATE SET rank=excluded.rank, douyin_id=excluded.douyin_id,"
            " gmv=excluded.gmv, sales=excluded.sales,"
            " fans_count=excluded.fans_count,"
            " extra_json=excluded.extra_json, captured_at=excluded.captured_at",
            (_row_id(r), r["stat_date"], r["platform_product_id"],
             int(r.get("rank") or 0), r.get("author_id") or "",
             r.get("douyin_id") or "", r.get("nickname") or "",
             r.get("gmv"), r.get("sales"), r.get("fans_count"),
             r.get("extra_json") or "{}", int(r.get("captured_at") or 0)))
        n += 1
    conn.commit()
    return n


# ─────────────────────────── 周采集编排（feedback loop）───────────────────────────

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _monday_utc8(ts: float | None = None) -> str:
    """本周一（北京时间）的 YYYY-MM-DD。周闸门按自然周切。"""
    from datetime import datetime, timedelta, timezone
    d = datetime.fromtimestamp(ts or time.time(),
                               tz=timezone(timedelta(hours=8))).date()
    return (d - timedelta(days=d.weekday())).isoformat()


def collect_for_matched_products(db_path=None, cache_path=None, limit: int = 15,
                                 log_fn=print,
                                 attempts_path=None) -> dict:
    """周采集钩子：把挂载了平台商品 ID 的商品逐个采「前几位带货达人」落库。

    触发点：daren_bot weekly.post_collect（周采集成功后）。post_collect 是
    **每个达人**都会调的 ⇒ 必须有周闸门：本周采够配额就整体跳过。

    数据源：daren-dim-cache.json（商品ID → promotion_id，dim collector
    跑 material_list 的缓存，47/47 全覆盖）——不重复调商品查询接口。

    周闸门双记录（2026-11-07 实测修补）：无内容商品不落库，没这层的话
    同一批无内容商品会每周反复吃掉配额——
      - 落库行（stat_date>=本周一）= 本周采到了；
      - attempts 文件（{商品ID: 日期}）= 今天尝试过（含无内容/失败）；
      ⇒ todo = 本周没落库、且今天没尝试过的。

    只丢不造：单个商品失败（无带货内容/风控/超时）记 err 继续下一个；
    「商品无带货内容」不是技术故障，与失败分列。
    """
    root = _repo_root()
    db_path = Path(db_path) if db_path else root / "data" / "solobrave.db"
    cache_path = Path(cache_path) if cache_path else root / "data" / "daren-dim-cache.json"
    attempts_path = Path(attempts_path) if attempts_path else \
        root / "data" / "loop2-attempts.json"
    out = {"candidates": 0, "quota": 0, "done": 0, "no_content": 0,
           "errors": 0, "skipped": False}
    if not cache_path.exists() or not db_path.exists():
        out["skipped"] = True
        return out
    cache = json.loads(cache_path.read_text())
    attempts = {}
    if attempts_path.exists():
        try:
            attempts = json.loads(attempts_path.read_text())
        except Exception:  # noqa: BLE001
            attempts = {}
    today = time.strftime("%Y-%m-%d", time.localtime())
    conn = sqlite3.connect(str(db_path), timeout=15)
    try:
        ensure = None
        try:
            from daren_cockpit.tools import captain_stats
            ensure = captain_stats.ensure_schema
        except Exception:  # noqa: BLE001
            pass
        if ensure:
            ensure(conn)
        # 待采 = cache 里有 promotion_id 的商品
        promo_by_pid = {str(k): str(v.get("promotion_id") or "")
                        for k, v in cache.items()
                        if isinstance(v, dict) and v.get("promotion_id")}
        out["candidates"] = len(promo_by_pid)
        # 周闸门：本周一（含）已采过 或 今天已尝试过 的都跳过
        monday = _monday_utc8()
        done_this_week = {r[0] for r in conn.execute(
            "SELECT DISTINCT platform_product_id FROM platform_product_talent"
            " WHERE stat_date>=?", (monday,))}
        todo = [(pid, promo) for pid, promo in promo_by_pid.items()
                if pid not in done_this_week and attempts.get(pid) != today]
        quota = min(limit, len(todo))
        out["quota"] = quota
        if quota <= 0:
            out["skipped"] = True
            return out
        todo = todo[:quota]
    finally:
        conn.close()

    def _mark(pid: str):
        attempts[pid] = today
        try:
            attempts_path.write_text(json.dumps(attempts, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            pass

    import random
    for pid, promo in todo:
        rows, err = fetch_top_talents(promo)
        _mark(pid)                                       # 成功与否都记尝试
        if rows:
            conn = sqlite3.connect(str(db_path), timeout=15)
            try:
                n = save_rows(conn, rows)
            finally:
                conn.close()
            out["done"] += 1
            log_fn(f"[loop2] top 达人采集 ok: {pid} → {n} 达人")
        elif "无带货内容" in err or "解析不出" in err:
            out["no_content"] += 1
            log_fn(f"[loop2] {pid} 无带货内容（跳过，不算失败）")
        else:
            out["errors"] += 1
            log_fn(f"[loop2] {pid} 采集失败：{err}")
        time.sleep(random.uniform(2.0, 5.0))   # 减速防风控
    return out
