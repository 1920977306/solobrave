"""多维表格联动（spec 第8条）：机器人用租户自己的飞书 app 凭证，
在其飞书云空间创建多维表格并双写分析结果（卡片 + 表格）。

内测只做两张表（账号档案 / AI 互动记录），表结构尽量简（全文本字段，可靠优先）：
- 账号档案：日期 / 抖音号 / 昵称 / 粉丝 / 累计GMV / 视频占比 / 评估结论 / 报告摘要
- AI 互动记录：时间 / 类型(账号分析|问答|视频拆解) / 输入 / 回答或报告摘要

app_token + table_id 存回 bot_tenants，复用不再建。
任何失败都只 log 不抛——绝不影响消息推送主链路。
"""
import datetime
import json
import logging
import urllib.request

from . import db, crypto_util

log = logging.getLogger(__name__)

API = "https://open.feishu.cn/open-apis"

APP_NAME = "我的带货助手"
FOLDER_TOKEN = ""  # 空 = 建在我的空间根目录

# 两张表定义：字段名 -> 飞书字段类型（1 = 文本）
TABLES = {
    "profile": {
        "name": "账号档案",
        "fields": ["日期", "抖音号", "昵称", "粉丝", "累计GMV",
                   "视频占比", "评估结论", "报告摘要"],
    },
    "log": {
        "name": "AI 互动记录",
        "fields": ["时间", "类型", "输入", "回答或报告摘要"],
    },
}


def _token(app_id: str, app_secret: str) -> str:
    req = urllib.request.Request(
        f"{API}/auth/v3/tenant_access_token/internal",
        data=json.dumps({"app_id": app_id, "app_secret": app_secret}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    tok = data.get("tenant_access_token")
    if not tok:
        raise RuntimeError(f"tenant_access_token 获取失败: {data.get('code')} {data.get('msg')}")
    return tok


def _post(tok: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json; charset=utf-8",
                 "Authorization": f"Bearer {tok}"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode())
    if data.get("code") != 0:
        raise RuntimeError(f"feishu api {path} 失败: {data.get('code')} {data.get('msg')}")
    return data.get("data") or {}


def _tenant_creds(tenant_key: str):
    t = db.get_tenant(tenant_key)
    if not t:
        raise RuntimeError(f"租户不存在: {tenant_key}")
    mk = crypto_util.load_or_create_master_key()
    return t["app_id"], crypto_util.decrypt_secret(mk, t["app_secret_enc"])


def _grant_owner(tok: str, app_token: str, open_id: str) -> None:
    """把达人本人加为多维表格协作者（应用身份建的表默认只有应用可见）。"""
    if not open_id:
        return
    try:
        _post(tok, f"/drive/v1/permissions/{app_token}/members?type=bitable",
              {"member_type": "openid", "perm": "full_access",
               "member_id": open_id})
    except Exception as e:
        # 授权失败不阻塞主链路（记录日志，达人看不到表时运营可手动分享）
        log.warning("[bitable] grant owner failed: %s", e)


def ensure_bitable(tenant_key: str) -> dict:
    """确保租户的多维表格存在；返回 {"app": token, "profile": tbl, "log": tbl}。
    表是应用身份创建的，每次确保存在后同步把达人本人授权为协作者。"""
    t = db.get_tenant(tenant_key)
    if not t:
        raise RuntimeError(f"租户不存在: {tenant_key}")
    app_id, secret = _tenant_creds(tenant_key)
    saved = (t.get("bitable_app_token") or "").strip()
    if saved and (t.get("bitable_table_ids") or "").strip():
        ids = json.loads(t["bitable_table_ids"])
        if ids.get("profile") and ids.get("log"):
            # 已存在：补授权（幂等），保证达人可见
            if t.get("owner_open_id"):
                _grant_owner(_token(app_id, secret), saved, t["owner_open_id"])
            return {"app": saved, **ids}

    tok = _token(app_id, secret)
    body = {"name": APP_NAME}
    if FOLDER_TOKEN:
        body["folder_token"] = FOLDER_TOKEN
    app_data = _post(tok, "/bitable/v1/apps", body)
    app_token = app_data["app"]["app_token"]
    table_ids = {}
    for key, spec in TABLES.items():
        tbl = _post(tok, f"/bitable/v1/apps/{app_token}/tables",
                    {"table": {"name": spec["name"],
                               "fields": [{"field_name": f, "type": 1} for f in spec["fields"]],
                               "default_view_name": spec["name"]}})
        table_ids[key] = tbl["table_id"]
    db.save_bitable_refs(tenant_key, app_token, table_ids)
    log.info("[bitable] created app=%s tables=%s tenant=%s", app_token, table_ids, tenant_key)
    if t.get("owner_open_id"):
        _grant_owner(tok, app_token, t["owner_open_id"])
    return {"app": app_token, **table_ids}


def add_record(tenant_key: str, table: str, values: dict) -> None:
    """往租户多维表格的某张表追加一条记录。values: 字段名 -> 文本。"""
    refs = ensure_bitable(tenant_key)
    app_id, secret = _tenant_creds(tenant_key)
    tok = _token(app_id, secret)
    fields = {k: ("" if v is None else str(v))[:40000] for k, v in values.items()}
    _post(tok, f"/bitable/v1/apps/{refs['app']}/tables/{refs[table]}/records",
          {"fields": fields})


def _s(v) -> str:
    """数值转字符串，保留 0，只把 None/空串归一为 ''。"""
    return "" if v is None or v == "" else str(v)


def sync_profile(tenant_key: str, talent: dict, verdict: str, summary: str) -> None:
    """账号报告 -> 账号档案表。"""
    add_record(tenant_key, "profile", {
        "日期": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "抖音号": talent.get("douyin_id") or talent.get("dyid") or "",
        "昵称": talent.get("name") or "",
        "粉丝": _s(talent.get("followers")),
        "累计GMV": _s(talent.get("total_gmv") if talent.get("total_gmv") is not None
                      else talent.get("gmv")),
        "视频占比": _s(talent.get("video_gmv_ratio") or talent.get("video_ratio")),
        "评估结论": verdict or "",
        "报告摘要": summary or "",
    })


def sync_log(tenant_key: str, kind: str, inp: str, answer: str) -> None:
    """问答/视频拆解 -> AI 互动记录表。"""
    add_record(tenant_key, "log", {
        "时间": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "类型": kind,
        "输入": (inp or "")[:2000],
        "回答或报告摘要": (answer or "")[:40000],
    })
