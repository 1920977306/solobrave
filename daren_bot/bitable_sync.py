"""多维表格联动（spec 第8条）：机器人用租户自己的飞书 app 凭证，
在其飞书云空间创建多维表格并双写分析结果（卡片 + 表格）。

三张表：
- 账号档案：日期 / 抖音号 / 昵称 / 粉丝 / 累计GMV / 视频占比 / 评估结论 / 报告摘要
- AI 互动记录：时间 / 类型(账号分析|问答|视频拆解) / 输入 / 回答或报告摘要
- 商品推荐记录：日期 / 商品名 / 价格 / 佣金率 / 销量 / 结论(单选带色) / 匹配理由 / 来源

app_token + table_id 存回 bot_tenants，复用不再建。
任何失败都只 log 不抛——绝不影响消息推送主链路。
"""
import datetime
import json
import logging
import os
import urllib.request
import uuid

from . import db, crypto_util

log = logging.getLogger(__name__)

API = "https://open.feishu.cn/open-apis"

APP_NAME = "我的带货助手"
FOLDER_TOKEN = ""  # 空 = 建在我的空间根目录

# 表定义：fields = [(字段名, 类型, 扩展property)]
# 类型: 1=文本 2=数字 3=单选 5=日期 17=附件
# 单选色: 0=红 3=黄 4=绿（飞书色板编号）
VERDICT_OPTIONS = [
    {"name": "潜力型", "color": 4}, {"name": "成长型", "color": 3},
    {"name": "稳健型", "color": 3}, {"name": "成熟型", "color": 4},
    {"name": "风险型", "color": 0},
]
TABLES = {
    "profile": {
        "name": "账号档案",
        "fields": [("日期", 5, {"date_formatter": "yyyy-MM-dd HH:mm"}),
                   ("抖音号", 1), ("昵称", 1),
                   ("粉丝", 2, {"formatter": "0"}),
                   ("累计GMV", 2, {"formatter": "0.0"}),
                   ("视频占比", 2, {"formatter": "0.00%"}),
                   ("评估结论", 3, {"options": VERDICT_OPTIONS}),
                   ("报告摘要", 1),
                   ("报告图", 17)],   # 附件：报告卡片图，画册视图封面
    },
    "log": {
        "name": "AI 互动记录",
        "fields": [("时间", 5, {"date_formatter": "yyyy-MM-dd HH:mm"}),
                   ("类型", 1), ("输入", 1), ("回答或报告摘要", 1)],
    },
    "products": {
        "name": "商品推荐记录",
        "fields": [("日期", 5, {"date_formatter": "yyyy-MM-dd HH:mm"}),
                   ("商品名", 1), ("价格", 1), ("佣金率", 1), ("销量", 1),
                   ("结论", 3, {"options": [
                       {"name": "🟢 能带", "color": 4},
                       {"name": "🟡 慎带", "color": 3},
                       {"name": "🔴 别带", "color": 0}]}),
                   ("匹配理由", 1), ("来源", 1), ("商品图", 17)],  # 附件：分析卡片图
    },
}


def _field_body(spec):
    name, ftype = spec[0], spec[1]
    body = {"field_name": name, "type": ftype}
    if len(spec) > 2 and spec[2]:
        body["property"] = spec[2]
    return body


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


def _create_table(tok: str, app_token: str, key: str) -> str:
    spec = TABLES[key]
    tbl = _post(tok, f"/bitable/v1/apps/{app_token}/tables",
                {"table": {"name": spec["name"],
                           "fields": [_field_body(f) for f in spec["fields"]],
                           "default_view_name": spec["name"]}})
    return tbl["table_id"]


def _get(tok: str, path: str) -> dict:
    req = urllib.request.Request(
        API + path, headers={"Authorization": f"Bearer {tok}"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def _convert_fields_to_spec(tok: str, app_token: str, tbl: str, key: str) -> None:
    """老表字段类型化：按 TABLES spec 把文本字段转 日期/数字/单选。"""
    fields = (_get(tok, f"/bitable/v1/apps/{app_token}/tables/{tbl}/fields")
              .get("data") or {}).get("items") or []
    by_name = {f.get("field_name"): f for f in fields}
    for spec in TABLES[key]["fields"]:
        name, want_type = spec[0], spec[1]
        f = by_name.get(name)
        if not f or f.get("type") == want_type:
            continue
        body = {"field_name": name, "type": want_type}
        if len(spec) > 2 and spec[2]:
            body["property"] = spec[2]
        _put(tok, f"/bitable/v1/apps/{app_token}/tables/{tbl}"
                  f"/fields/{f['field_id']}", body)
        log.info("[bitable] upgraded: %s %s text->type %s", key, name, want_type)


def _upgrade_profile_table(tok: str, app_token: str, profile_tbl: str) -> None:
    """老账号档案表升级：补「报告图」附件字段 + 字段类型化 + 「账号画册」gallery 视图
    （封面字段需在飞书里手选一次，API 不支持写 property）。"""
    try:
        fields = (_get(tok, f"/bitable/v1/apps/{app_token}/tables/{profile_tbl}/fields")
                  .get("data") or {}).get("items") or []
        by_name = {f.get("field_name"): f for f in fields}
        # 1) 补缺失字段
        if "报告图" not in by_name:
            _post(tok, f"/bitable/v1/apps/{app_token}/tables/{profile_tbl}/fields",
                  {"field_name": "报告图", "type": 17})
            log.info("[bitable] upgraded: profile +报告图 attachment field")
        # 2) 字段类型化：老文本字段 → 日期/数字/单选（按 TABLES spec）
        _convert_fields_to_spec(tok, app_token, profile_tbl, "profile")
        # 3) gallery 视图
        views = (_get(tok, f"/bitable/v1/apps/{app_token}/tables/{profile_tbl}/views")
                 .get("data") or {}).get("items") or []
        if not any(v.get("view_type") == "gallery" for v in views):
            _post(tok, f"/bitable/v1/apps/{app_token}/tables/{profile_tbl}/views",
                  {"view_name": "账号画册", "view_type": "gallery"})
            log.info("[bitable] upgraded: profile +账号画册 gallery view")
    except Exception:
        log.exception("[bitable] profile table upgrade failed (非致命)")


def _upgrade_products_table(tok: str, app_token: str, products_tbl: str) -> None:
    """商品推荐记录升级：补「商品图」附件字段 + 字段类型化 +
    「结论看板」kanban 视图 + 「商品画册」gallery 视图。"""
    try:
        fields = (_get(tok, f"/bitable/v1/apps/{app_token}/tables/{products_tbl}/fields")
                  .get("data") or {}).get("items") or []
        if not any(f.get("field_name") == "商品图" for f in fields):
            _post(tok, f"/bitable/v1/apps/{app_token}/tables/{products_tbl}/fields",
                  {"field_name": "商品图", "type": 17})
            log.info("[bitable] upgraded: products +商品图 attachment field")
        _convert_fields_to_spec(tok, app_token, products_tbl, "products")
        views = (_get(tok, f"/bitable/v1/apps/{app_token}/tables/{products_tbl}/views")
                 .get("data") or {}).get("items") or []
        vtypes = {v.get("view_type") for v in views}
        if "kanban" not in vtypes:
            _post(tok, f"/bitable/v1/apps/{app_token}/tables/{products_tbl}/views",
                  {"view_name": "结论看板", "view_type": "kanban"})
            log.info("[bitable] upgraded: products +结论看板 kanban view")
        if "gallery" not in vtypes:
            _post(tok, f"/bitable/v1/apps/{app_token}/tables/{products_tbl}/views",
                  {"view_name": "商品画册", "view_type": "gallery"})
            log.info("[bitable] upgraded: products +商品画册 gallery view")
    except Exception:
        log.exception("[bitable] products table upgrade failed (非致命)")


def _upgrade_log_table(tok: str, app_token: str, log_tbl: str) -> None:
    """AI 互动记录升级：字段类型化（时间→日期）。"""
    try:
        _convert_fields_to_spec(tok, app_token, log_tbl, "log")
    except Exception:
        log.exception("[bitable] log table upgrade failed (非致命)")


def _put(tok: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(
        API + path, data=json.dumps(body).encode(), method="PUT",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {tok}"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def _upload_bitable_image(tenant_key: str, app_token: str, png_path: str) -> str:
    """上传图片到租户多维表格，返回 file_token（供附件字段引用）。"""
    app_id, secret = _tenant_creds(tenant_key)
    tok = _token(app_id, secret)
    boundary = uuid.uuid4().hex
    size = os.path.getsize(png_path)
    fields = {"file_name": os.path.basename(png_path),
              "parent_type": "bitable_image",
              "parent_node": app_token,
              "size": str(size)}
    body = b""
    for k, v in fields.items():
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"'
                 f"\r\n\r\n{v}\r\n").encode()
    with open(png_path, "rb") as f:
        data = f.read()
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
             f'filename="{os.path.basename(png_path)}"\r\n'
             f"Content-Type: image/png\r\n\r\n").encode() + data + \
        f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        API + "/drive/v1/medias/upload_all", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "Authorization": f"Bearer {tok}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        r = json.loads(resp.read().decode())
    ft = (r.get("data") or {}).get("file_token")
    if not ft:
        raise RuntimeError(f"图片上传无 file_token: {str(r)[:200]}")
    return ft


def ensure_bitable(tenant_key: str) -> dict:
    """确保租户的多维表格存在（含升级：老表补建缺失的表）；
    返回 {"app": token, "profile": tbl, "log": tbl, "products": tbl}。
    表是应用身份创建的，每次确保存在后同步把达人本人授权为协作者。"""
    t = db.get_tenant(tenant_key)
    if not t:
        raise RuntimeError(f"租户不存在: {tenant_key}")
    app_id, secret = _tenant_creds(tenant_key)
    tok = _token(app_id, secret)
    saved = (t.get("bitable_app_token") or "").strip()
    if saved and (t.get("bitable_table_ids") or "").strip():
        ids = json.loads(t["bitable_table_ids"])
        missing = [k for k in TABLES if not ids.get(k)]
        if not missing:
            _upgrade_profile_table(tok, saved, ids["profile"])
            _upgrade_products_table(tok, saved, ids["products"])
            _upgrade_log_table(tok, saved, ids["log"])
            if t.get("owner_open_id"):
                _grant_owner(tok, saved, t["owner_open_id"])
            return {"app": saved, **ids}
        # 老 app 升级：补建缺失的表（如商品推荐记录）
        for k in missing:
            ids[k] = _create_table(tok, saved, k)
            log.info("[bitable] upgraded: created table %s(%s) tenant=%s", TABLES[k]["name"], k, tenant_key)
        db.save_bitable_refs(tenant_key, saved, ids)
        if t.get("owner_open_id"):
            _grant_owner(tok, saved, t["owner_open_id"])
        return {"app": saved, **ids}

    body = {"name": APP_NAME}
    if FOLDER_TOKEN:
        body["folder_token"] = FOLDER_TOKEN
    app_data = _post(tok, "/bitable/v1/apps", body)
    app_token = app_data["app"]["app_token"]
    table_ids = {k: _create_table(tok, app_token, k) for k in TABLES}
    db.save_bitable_refs(tenant_key, app_token, table_ids)
    log.info("[bitable] created app=%s tables=%s tenant=%s", app_token, table_ids, tenant_key)
    _upgrade_profile_table(tok, app_token, table_ids["profile"])
    _upgrade_products_table(tok, app_token, table_ids["products"])
    _upgrade_log_table(tok, app_token, table_ids["log"])
    if t.get("owner_open_id"):
        _grant_owner(tok, app_token, t["owner_open_id"])
    return {"app": app_token, **table_ids}


def add_record(tenant_key: str, table: str, values: dict) -> None:
    """往租户多维表格的某张表追加一条记录。values: 字段名 -> 值；
    list 原样透传（附件字段），None 置空，其余转字符串截断。"""
    refs = ensure_bitable(tenant_key)
    app_id, secret = _tenant_creds(tenant_key)
    tok = _token(app_id, secret)
    fields = {k: (v if isinstance(v, list) or v is None
                  else (v if isinstance(v, (int, float))
                        else str(v)[:40000]))
              for k, v in values.items()}
    _post(tok, f"/bitable/v1/apps/{refs['app']}/tables/{refs[table]}/records",
          {"fields": fields})


def _now_ms() -> int:
    """当前时间的毫秒时间戳（日期/时间字段写入用）。"""
    return int(datetime.datetime.now().timestamp() * 1000)


def _to_int(v):
    try:
        return int(float(str(v).replace(",", "").strip()))
    except Exception:
        return None


def _to_float(v):
    try:
        return float(str(v).replace(",", "").replace("万", "")
                     .replace("¥", "").strip())
    except Exception:
        return None


def _to_ratio(v):
    """百分比(0-100) → 小数(0-1)，配 formatter '0.00%' 显示为百分比。"""
    f = _to_float(v)
    return round(f / 100, 4) if f is not None else None


def _s(v) -> str:
    """数值转字符串，保留 0，只把 None/空串归一为 ''。"""
    return "" if v is None or v == "" else str(v)


def sync_profile(tenant_key: str, talent: dict, verdict: str, summary: str,
                 img_path: str = "") -> None:
    """账号报告 -> 账号档案表。img_path: 报告卡片图，上传后挂到「报告图」附件字段。"""
    record = {
        "日期": _now_ms(),
        "抖音号": talent.get("douyin_id") or talent.get("dyid") or "",
        "昵称": talent.get("name") or "",
        "粉丝": _to_int(talent.get("followers")),
        "累计GMV": _to_float(talent.get("total_gmv") if talent.get("total_gmv") is not None
                             else talent.get("gmv")),
        "视频占比": _to_ratio(talent.get("video_gmv_ratio") or talent.get("video_ratio")),
        "评估结论": verdict or "",
        "报告摘要": summary or "",
    }
    if img_path and os.path.isfile(img_path):
        try:
            refs = ensure_bitable(tenant_key)
            ft = _upload_bitable_image(tenant_key, refs["app"], img_path)
            record["报告图"] = [{"file_token": ft}]
        except Exception:
            log.exception("[bitable] 报告图上传失败（记录仍写入，仅无图）")
    add_record(tenant_key, "profile", record)


def sync_log(tenant_key: str, kind: str, inp: str, answer: str) -> None:
    """问答/视频拆解 -> AI 互动记录表。"""
    add_record(tenant_key, "log", {
        "时间": _now_ms(),
        "类型": kind,
        "输入": (inp or "")[:2000],
        "回答或报告摘要": (answer or "")[:40000],
    })


def _verdict_option(verdict_md: str) -> str:
    """从结论 Markdown 里解析单选值：🟢→🟢 能带，🟡→🟡 慎带，🔴→🔴 别带。"""
    md = verdict_md or ""
    if "🔴" in md:
        return "🔴 别带"
    if "🟡" in md:
        return "🟡 慎带"
    if "🟢" in md:
        return "🟢 能带"
    return "🟡 慎带"   # 兜底（AI 没按格式输出时）


def sync_product(tenant_key: str, stats: dict, verdict_md: str,
                 source: str = "", img_path: str = "") -> None:
    """商品分析 -> 商品推荐记录表（结论列单选带色，打开表一眼看清）。
    img_path: 商品分析卡片图，上传后挂到「商品图」附件字段（画册封面）。"""
    record = {
        "日期": _now_ms(),
        "商品名": (stats.get("name") or "")[:200],
        "价格": _s(stats.get("price")),
        "佣金率": _s(stats.get("commission_rate") or stats.get("commission")),
        "销量": _s(stats.get("sales")),
        "结论": _verdict_option(verdict_md),
        "匹配理由": (verdict_md or "")[:40000],
        "来源": (source or stats.get("resolved_url") or "")[:500],
    }
    if img_path and os.path.isfile(img_path):
        try:
            refs = ensure_bitable(tenant_key)
            ft = _upload_bitable_image(tenant_key, refs["app"], img_path)
            record["商品图"] = [{"file_token": ft}]
        except Exception:
            log.exception("[bitable] 商品图上传失败（记录仍写入，仅无图）")
    add_record(tenant_key, "products", record)
