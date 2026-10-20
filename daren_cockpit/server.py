#!/usr/bin/env python3
"""达人经营驾驶舱（网页版）——微信风白色简约。

数据源：
- daren_bot.db：租户 -> 绑定抖音号 / 租户多维表格 ID
- solobrave.db（只读）：talents / products / product_talent_match
- 飞书多维表格（lark-cli，120s 内存缓存）：租户 profile/log/products 表 + 样板库合作机会

用法：python3 server.py --host 127.0.0.1 --port 7100
"""
import argparse
import json
import re
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))  # 引入 daren_bot
from daren_bot import config, db as botdb  # noqa: E402

SOLOBRAVE_DB = config.ROOT / "data" / "solobrave.db"
TEMPLATE = (ROOT / "index.html").read_text(encoding="utf-8")

DEMO_BASE = "MZEdbVJMeah4x9sYYzncuhnbnnb"   # 样板库（合作机会）
COOP_TABLE = "tblolFFeJTNfyInn"


def _lark_cli() -> str:
    """lark-cli 绝对路径：launchd 等极简 PATH 环境下 shutil.which 找不到，
    兜底常装目录；都找不到返回 'lark-cli'（让 subprocess 报错进 except）。"""
    import shutil
    p = shutil.which("lark-cli")
    if p:
        return p
    for cand in ("/opt/homebrew/bin/lark-cli", "/usr/local/bin/lark-cli"):
        if Path(cand).exists():
            return cand
    return "lark-cli"

_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()
CACHE_TTL = 120

from concurrent.futures import ThreadPoolExecutor  # noqa: E402
_EXEC = ThreadPoolExecutor(max_workers=4)


# ---------- 工具 ----------

def _cache_get(key):
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1]
    return None


def _cache_set(key, val):
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), val)


def lark_records(base_token: str, table_id: str):
    """lark-cli record-list，返回 [ {字段名: 值}, ... ]。失败返回 None。"""
    key = f"rec:{base_token}:{table_id}"
    hit = _cache_get(key)
    if hit is not None:
        return hit
    try:
        import os
        env = dict(os.environ)
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" \
            + os.pathsep + env.get("PATH", "")
        out = subprocess.run(
            [_lark_cli(), "base", "+record-list",
             "--base-token", base_token, "--table-id", table_id,
             "--as", "user", "--format", "json"],
            capture_output=True, text=True, timeout=30, env=env)
        d = json.loads(out.stdout)
        if not d.get("ok"):
            return None
        payload = d["data"]
        names = payload.get("fields") or []
        rows = [dict(zip(names, r)) for r in payload.get("data", [])]
        _cache_set(key, rows)
        return rows
    except Exception:
        return None


_GMVTEXT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[万kK]?\s*[-~—～]\s*[¥￥]?\s*(\d+(?:\.\d+)?)\s*([万kK]?)")

def gmv_mid_yuan(total_gmv, total_gmv_text):
    """精确 GMV 优先；否则档位文本中值（元）。返回 (display_text, mid_yuan|None)。"""
    try:
        v = float(total_gmv or 0)
    except Exception:
        v = 0.0
    if v > 0:
        return f"¥{v/10000:.1f}万", v
    text = (total_gmv_text or "").strip()
    if text:
        m = _GMVTEXT_RE.search(text)
        if m:
            lo, hi = float(m.group(1)), float(m.group(2))
            unit = m.group(3) or ("万" if "万" in text else "")
            mult = 10000 if unit == "万" else (1000 if unit in ("k", "K") else 1)
            mid = (lo + hi) / 2 * mult
            return f"{text.replace('¥','')}（档位中值 ¥{mid/10000:.1f}万）", mid
        return text, None
    return "暂无数据", None


def _num(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def _pct(v):
    """0.9906 / 93.5 / '93.5%' 统一 → '99.1%' / '93.5%'。无法解析返回 None。"""
    if v in (None, ""):
        return None
    s = str(v).replace("%", "").strip()
    try:
        f = float(s)
    except Exception:
        return None
    if f <= 1:
        f *= 100
    return f"{f:.1f}%"


def _fmt_date(v):
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        return time.strftime("%Y-%m-%d", time.localtime(v / 1000))
    s = str(v)
    return s[:10] if len(s) >= 10 else s


def _clean_reason(v, limit=110):
    """匹配理由多为 AI 生成的 markdown：去掉「## 结论」段（结论已有标签），
    抹掉 ## 标题和 - 列表符号，压成一行可读文本。"""
    if not v:
        return ""
    lines, skip = [], False
    for ln in str(v).splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith("##"):
            skip = ln.lstrip("#").strip() == "结论"
            continue
        if skip:
            continue
        lines.append(ln.lstrip("- ").strip())
    return "；".join(lines)[:limit]


# ---------- 数据组装 ----------

def load_cockpit(tenant_key: str):
    tenant = botdb.get_tenant(tenant_key)
    if not tenant:
        return None
    dyid = tenant.get("bind_douyin_id") or ""
    table_ids = {}
    try:
        table_ids = json.loads(tenant.get("bitable_table_ids") or "{}")
    except Exception:
        pass
    tb_app = tenant.get("bitable_app_token") or ""

    # 本地 talent
    talent = None
    products_local = []
    try:
        con = sqlite3.connect(str(SOLOBRAVE_DB))
        # query_only 引擎级禁写，比 mode=ro URI 更强；且不受 sqlite 版本×WAL 只读打开限制
        con.execute("PRAGMA query_only=ON")
        con.row_factory = sqlite3.Row
        talent = con.execute(
            "SELECT * FROM talents WHERE douyin_id=? ORDER BY updated_at DESC LIMIT 1",
            (dyid,)).fetchone()
        if talent:
            tid = talent["id"]
            for m in con.execute(
                    "SELECT m.match_score, m.match_reason, p.name, p.price, "
                    "p.commission_rates, p.monthly_sales, p.id AS pid "
                    "FROM product_talent_match m JOIN products p ON p.id=m.product_id "
                    "WHERE m.talent_id=? ORDER BY m.match_score DESC LIMIT 3", (tid,)):
                rd = dict(m)
                comm = ""
                try:
                    cr = json.loads(rd.get("commission_rates") or "{}")
                    comm = f"{max(cr.values()):.0f}%" if cr else ""
                except Exception:
                    pass
                products_local.append({
                    "name": rd["name"], "price": rd["price"], "comm": comm,
                    "sales": rd["monthly_sales"] or 0,
                    "score": round(_num(rd["match_score"]), 1),
                    "reason": _clean_reason(rd["match_reason"]),
                    "verdict": "", "date": "",
                })
        con.close()
    except Exception:
        pass

    # 飞书四路查询并行（串行时冷启动 ~5s，并行后 ~1.3s）
    futs = {}
    if tb_app:
        futs["profile"] = _EXEC.submit(lark_records, tb_app, table_ids.get("profile", ""))
        futs["log"] = _EXEC.submit(lark_records, tb_app, table_ids.get("log", ""))
        futs["products"] = _EXEC.submit(lark_records, tb_app, table_ids.get("products", ""))
    futs["coop"] = _EXEC.submit(lark_records, DEMO_BASE, COOP_TABLE)
    bundle = {k: f.result() for k, f in futs.items()}

    # 飞书：profile（趋势+结论+摘要）
    trend, verdict, summary, kpi = [], "", "", {}
    rows = bundle.get("profile")
    if rows:
        mine = [r for r in rows if str(r.get("抖音号") or "") == dyid]
        mine.sort(key=lambda r: str(r.get("日期") or ""))
        for r in mine:
            trend.append({"date": _fmt_date(r.get("日期")),
                          "fans": int(_num(r.get("粉丝")))})
        if mine:
            last = mine[-1]
            verdict = (last.get("评估结论") or [""])
            verdict = verdict[0] if isinstance(verdict, list) else str(verdict)
            summary = str(last.get("报告摘要") or "")[:60]
            kpi = {"fans": int(_num(last.get("粉丝"))),
                   "gmv_raw": last.get("累计GMV"),
                   "video_ratio": last.get("视频占比")}

    # 飞书：log（AI 互动分布）
    interact = {}
    rows = bundle.get("log")
    if rows:
        for r in rows:
            t = str(r.get("类型") or "其他")
            interact[t] = interact.get(t, 0) + 1

    # 飞书：tenant products（带结论的选品，优先于本地匹配）
    cards = []
    rows = bundle.get("products")
    if rows:
        for r in sorted(rows, key=lambda x: str(x.get("日期") or ""), reverse=True)[:3]:
            v = r.get("结论") or ""
            v = v[0] if isinstance(v, list) else str(v)
            cards.append({
                "name": str(r.get("商品名") or ""), "verdict": v,
                "price": str(r.get("价格") or ""),
                "comm": str(r.get("佣金率") or ""),
                "sales": str(r.get("销量") or ""),
                "reason": _clean_reason(r.get("匹配理由")),
                "date": _fmt_date(r.get("日期")),
            })
    if not cards:
        cards = products_local

    # 飞书：样板库合作机会
    coop = []
    rows = bundle.get("coop")
    if rows:
        for r in sorted(rows, key=lambda x: str(x.get("提交时间") or ""), reverse=True):
            st = r.get("状态") or ""
            st = st[0] if isinstance(st, list) else str(st)
            coop.append({
                "brand": str(r.get("品牌/商家") or ""),
                "contact": str(r.get("联系人") or ""),
                "phone": str(r.get("联系方式") or ""),
                "category": str(r.get("合作类目") or ""),
                "budget": str(r.get("预算/佣金") or ""),
                "note": str(r.get("合作说明") or ""),
                "status": st, "date": _fmt_date(r.get("提交时间")),
            })

    # KPI 计算
    fans = kpi.get("fans") or int(_num(talent and talent["followers"]))
    gmv_text, _mid = gmv_mid_yuan(kpi.get("gmv_raw"),
                                  talent and talent["total_gmv_text"])
    vr = _pct(kpi.get("video_ratio")) or _pct(talent and talent["video_ratio"]) \
        or _pct(talent and talent["video_gmv_ratio"])
    delta = None
    if len(trend) >= 2 and trend[0]["fans"]:
        delta = trend[-1]["fans"] - trend[0]["fans"]
    verdict_line = {
        "潜力型": "涨粉健康、带货稳，优先接高佣标品",
        "能带": "账号状态好，适合加大带货频率",
        "慎带": "近期数据波动，选品从严、先测品",
        "别带": "账号数据异常，先养内容再带货",
    }.get(verdict, "数据积累中，每周一更新结论")

    return {
        "tenant": tenant_key, "dyid": dyid,
        "name": (talent and talent["name"]) or tenant_key,
        "verdict": verdict or "评估中",
        "verdict_line": verdict_line,
        "summary": summary,
        "fans": fans, "fans_delta": delta,
        "gmv_text": gmv_text,
        "video_ratio": vr or "暂无",
        "video_note": "纯视频达人" if vr and float(vr.replace('%','')) > 80 else "直播/视频混合",
        "trend": trend[-8:],
        "trend_note": f"近 {len(trend)} 次采集" if trend else "暂无采集记录",
        "cards": cards,
        "interact": interact,
        "coop": coop,
        "updated": time.strftime("%Y-%m-%d %H:%M"),
    }


def _rating_band(score):
    """评分分桶（运营看板阶段分布用）：优质/良好/待观察/未评估。"""
    s = _num(score, -1)
    if s < 0:
        return "未评估"
    if s >= 4.5:
        return "优质"
    if s >= 3:
        return "良好"
    return "待观察"


def load_roster():
    """运营总览：全部达人名单 + 评分分布 + 合作管道。对应 /api/roster。"""
    tenants = []
    try:
        con = sqlite3.connect(str(config.DB_PATH))
        con.execute("PRAGMA query_only=ON")
        trows = con.execute(
            "SELECT t.tenant_key, t.bind_douyin_id, t.created_at, "
            "       COALESCE(c.balance, 0) AS credits "
            "FROM bot_tenants t LEFT JOIN bot_credits_accounts c "
            "ON c.tenant_key = t.tenant_key ORDER BY t.created_at").fetchall()
        con.close()
    except Exception:
        trows = []

    talent_by_dyid = {}
    try:
        con = sqlite3.connect(str(SOLOBRAVE_DB))
        con.execute("PRAGMA query_only=ON")
        con.row_factory = sqlite3.Row
        for r in con.execute(
                "SELECT douyin_id, name, followers, total_gmv_text, rating_score, "
                "       video_ratio, updated_at FROM talents"):
            dy = str(r["douyin_id"] or "")
            # 同号多行取最新
            if dy and (dy not in talent_by_dyid
                       or (r["updated_at"] or 0) > (talent_by_dyid[dy].get("updated_at") or 0)):
                talent_by_dyid[dy] = dict(r)
        con.close()
    except Exception:
        pass

    for tk, dyid, created, credits in trows:
        t = talent_by_dyid.get(str(dyid or "")) or {}
        gmv_text, _ = gmv_mid_yuan(t.get("total_gmv"), t.get("total_gmv_text"))
        band = _rating_band(t.get("rating_score"))
        tenants.append({
            "tenant": tk, "dyid": dyid or "",
            "name": t.get("name") or tk,
            "fans": int(_num(t.get("followers"))),
            "gmv_text": gmv_text or "暂无",
            "band": band,
            "score": (_num(t.get("rating_score"), -1)),
            "credits": int(_num(credits)),
            "updated": _fmt_date(t.get("updated_at")) or "未采集",
            "joined": _fmt_date(created * 1000 if created and created < 1e12 else created),
        })

    # 合作管道（与驾驶舱同一缓存数据源）
    coop_rows = lark_records(DEMO_BASE, COOP_TABLE) or []
    pipe = {}
    coop_recent = []
    for r in coop_rows:
        st = r.get("状态") or ""
        st = st[0] if isinstance(st, list) else str(st)
        st = st or "待评估"
        pipe[st] = pipe.get(st, 0) + 1
        coop_recent.append({"brand": str(r.get("品牌/商家") or ""),
                            "category": str(r.get("合作类目") or ""),
                            "status": st, "date": _fmt_date(r.get("提交时间"))})
    coop_recent.sort(key=lambda x: x["date"], reverse=True)

    bands = {}
    for t in tenants:
        bands[t["band"]] = bands.get(t["band"], 0) + 1
    total_fans = sum(t["fans"] for t in tenants)
    return {
        "tenants": tenants,
        "kpi": {"talents": len(tenants),
                "total_fans": total_fans,
                "updated_this_week": sum(1 for t in tenants if t["updated"] >= "2026-10-13"),
                "pending_coop": pipe.get("待评估", 0),
                "total_coop": len(coop_rows)},
        "bands": bands,
        "coop_pipe": pipe,
        "coop_recent": coop_recent[:5],
        "updated": time.strftime("%Y-%m-%d %H:%M"),
    }


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            self._send(200, TEMPLATE)
        elif u.path == "/api/cockpit":
            t = parse_qs(u.query).get("t", ["daren_boss"])[0]
            if not re.fullmatch(r"[A-Za-z0-9_]+", t):
                self._send(400, json.dumps({"error": "bad tenant"}),
                           "application/json")
                return
            try:
                data = load_cockpit(t)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}),
                           "application/json")
                return
            if not data:
                self._send(404, json.dumps({"error": f"tenant {t} not found"}),
                           "application/json")
                return
            self._send(200, json.dumps(data, ensure_ascii=False),
                       "application/json; charset=utf-8")
        elif u.path == "/api/roster":
            try:
                data = load_roster()
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}),
                           "application/json")
                return
            self._send(200, json.dumps(data, ensure_ascii=False),
                       "application/json; charset=utf-8")
        else:
            self._send(404, "not found", "text/plain")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7100)
    args, _ = ap.parse_known_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    # 启动即后台预热默认租户缓存——第一个访问者也不吃冷启动
    threading.Thread(target=_warm_cache, daemon=True).start()
    print(f"daren-cockpit @ http://{args.host}:{args.port}/")
    srv.serve_forever()


def _warm_cache():
    for tk in ("daren_boss",):
        try:
            load_cockpit(tk)
            print(f"[warm] cache warmed for {tk}", flush=True)
        except Exception as e:
            print(f"[warm] failed for {tk}: {e}", flush=True)
    try:
        load_roster()
        print("[warm] cache warmed for roster", flush=True)
    except Exception as e:
        print(f"[warm] failed for roster: {e}", flush=True)


if __name__ == "__main__":
    main()
