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
import re
import time
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
                   ("最新快照", 3, {"options": [
                       {"name": "是", "color": 4},
                       {"name": "否", "color": 0}]}),
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


def _v3_conf_get(tok: str, app_token: str, tbl: str, view_id: str, sub: str):
    """base/v3 视图配置读取；无权限/不支持返回 None。"""
    try:
        r = _get(tok, f"/base/v3/bases/{app_token}/tables/{tbl}/views/{view_id}/{sub}")
        return r.get("data") if r.get("code") == 0 else None
    except Exception:
        return None


def _v3_conf_set(tok: str, app_token: str, tbl: str, view_id: str,
                 sub: str, want, name_of: dict = None) -> None:
    """读-改-写视图配置（幂等）。GET 返回裸数据（列表/dict），PUT 带包装键；
    服务端把字段 id 回显成名字，比较前统一归一成 (字段名, desc) 再比。"""
    cur = _v3_conf_get(tok, app_token, tbl, view_id, sub)
    if cur is None:
        return  # 权限未开通或视图不支持该配置，静默跳过
    norm = lambda x: (name_of or {}).get(x, x)
    key = next((k for k in ("sort_config", "group_config", "visible_fields", "cover_field")
                if isinstance(want, dict) and k in want), None)
    if key in ("sort_config", "group_config"):
        cur_n = sorted((norm(i.get("field")), bool(i.get("desc"))) for i in (cur or []))
        want_n = sorted((norm(i.get("field")), bool(i.get("desc"))) for i in want[key])
        if cur_n == want_n:
            return
    elif key == "visible_fields":
        if (cur or []) == want["visible_fields"]:
            return
    elif key == "cover_field":
        if norm((cur or {}).get("cover_field")) == norm(want["cover_field"]):
            return
    elif cur == want:
        return
    try:
        _put(tok, f"/base/v3/bases/{app_token}/tables/{tbl}/views/{view_id}/{sub}", want)
        time.sleep(0.3)  # 同表写接口要求串行
        log.info("[bitable] view conf: %s %s -> %s", view_id, sub, want)
    except Exception:
        log.info("[bitable] view conf failed (非致命): %s %s", view_id, sub)


def _view_conf_by_type(views, vtype):
    return next((v for v in views if v.get("view_type") == vtype), None)


def _configure_views(tok: str, app_token: str, ids: dict) -> None:
    """视图外观统一配置（需 base:view 权限；未开通整体跳过）：
    - 全部视图按日期/时间倒序（最新在前）
    - 账号画册封面=报告图、商品画册封面=商品图
    - 结论看板按「结论」单选分泳道
    - grid/gallery 隐藏长文本字段，打开即一目了然
    """
    for key, date_name, extra in (
            ("profile", "日期", _profile_view_conf),
            ("products", "日期", _products_view_conf),
            ("log", "时间", None)):
        tbl = ids.get(key)
        if not tbl:
            continue
        try:
            items = (_get(tok, f"/bitable/v1/apps/{app_token}/tables/{tbl}/fields")
                     .get("data") or {}).get("items") or []
            fmap = {f["field_name"]: f["field_id"] for f in items}
            rev = {v: k for k, v in fmap.items()}
            views = (_get(tok, f"/bitable/v1/apps/{app_token}/tables/{tbl}/views")
                     .get("data") or {}).get("items") or []
        except Exception:
            continue
        date_fid = fmap.get(date_name)
        for v in views:
            if date_fid and v.get("view_type") in ("grid", "gallery", "kanban"):
                _v3_conf_set(tok, app_token, tbl, v["view_id"], "sort",
                             {"sort_config": [{"field": date_fid, "desc": True}]}, rev)
        if extra:
            extra(tok, app_token, tbl, views, fmap, rev)


def _profile_view_conf(tok, app_token, tbl, views, fmap, rev) -> None:
    gal = _view_conf_by_type(views, "gallery")
    if gal and "报告图" in fmap:
        _v3_conf_set(tok, app_token, tbl, gal["view_id"], "card",
                     {"cover_field": fmap["报告图"]}, rev)
        _v3_conf_set(tok, app_token, tbl, gal["view_id"], "visible_fields",
                     {"visible_fields": ["日期", "昵称", "抖音号", "粉丝", "累计GMV", "评估结论"]})
    grid = _view_conf_by_type(views, "grid")
    if grid:
        _v3_conf_set(tok, app_token, tbl, grid["view_id"], "visible_fields",
                     {"visible_fields": ["日期", "抖音号", "昵称", "粉丝", "累计GMV",
                                         "视频占比", "评估结论", "报告图"]})


def _products_view_conf(tok, app_token, tbl, views, fmap, rev) -> None:
    kanban = _view_conf_by_type(views, "kanban")
    if kanban and "结论" in fmap:
        _v3_conf_set(tok, app_token, tbl, kanban["view_id"], "group",
                     {"group_config": [{"field": fmap["结论"], "desc": False}]}, rev)
    gal = _view_conf_by_type(views, "gallery")
    if gal and "商品图" in fmap:
        _v3_conf_set(tok, app_token, tbl, gal["view_id"], "card",
                     {"cover_field": fmap["商品图"]}, rev)
        _v3_conf_set(tok, app_token, tbl, gal["view_id"], "visible_fields",
                     {"visible_fields": ["日期", "商品名", "价格", "佣金率", "结论"]})


def _upgrade_profile_table(tok: str, app_token: str, profile_tbl: str) -> None:
    """老账号档案表升级：补「报告图」附件字段 + 字段类型化 + 「账号画册」gallery 视图。
    视图外观（封面/排序/显隐）统一由 _configure_views 处理。"""
    try:
        fields = (_get(tok, f"/bitable/v1/apps/{app_token}/tables/{profile_tbl}/fields")
                  .get("data") or {}).get("items") or []
        by_name = {f.get("field_name"): f for f in fields}
        # 1) 补缺失字段
        if "报告图" not in by_name:
            _post(tok, f"/bitable/v1/apps/{app_token}/tables/{profile_tbl}/fields",
                  {"field_name": "报告图", "type": 17})
            log.info("[bitable] upgraded: profile +报告图 attachment field")
        if "最新快照" not in by_name:
            _post(tok, f"/bitable/v1/apps/{app_token}/tables/{profile_tbl}/fields",
                  {"field_name": "最新快照", "type": 3,
                   "property": {"options": [{"name": "是", "color": 4},
                                            {"name": "否", "color": 0}]}})
            log.info("[bitable] upgraded: profile +最新快照 select field")
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
            _configure_views(tok, saved, ids)
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
    _configure_views(tok, app_token, table_ids)
    if t.get("owner_open_id"):
        _grant_owner(tok, app_token, t["owner_open_id"])
    return {"app": app_token, **table_ids}


def add_record(tenant_key: str, table: str, values: dict) -> str:
    """往租户多维表格的某张表追加一条记录。values: 字段名 -> 值；
    list 原样透传（附件字段），None 置空，其余转字符串截断。
    返回新记录 record_id（拿不到返回空串）。"""
    refs = ensure_bitable(tenant_key)
    app_id, secret = _tenant_creds(tenant_key)
    tok = _token(app_id, secret)
    fields = {k: (v if isinstance(v, list) or v is None
                  else (v if isinstance(v, (int, float))
                        else str(v)[:40000]))
              for k, v in values.items()}
    data = _post(tok, f"/bitable/v1/apps/{refs['app']}/tables/{refs[table]}/records",
                 {"fields": fields})
    return ((data.get("record") or {}).get("record_id")) or ""


def _unset_old_snapshots(tenant_key: str, dyid: str, keep_id: str) -> None:
    """账号档案表：把同抖音号其他「最新快照=是」的旧行置否（看板指标卡按此筛选）。
    任何失败只 log 不抛——绝不影响主链路。"""
    if not dyid or not keep_id:
        return
    try:
        refs = ensure_bitable(tenant_key)
        app_id, secret = _tenant_creds(tenant_key)
        tok = _token(app_id, secret)
        path = f"/bitable/v1/apps/{refs['app']}/tables/{refs['profile']}/records/search"
        body = {"filter": {"conjunction": "and", "conditions": [
                    {"field_name": "抖音号", "operator": "is", "value": [dyid]},
                    {"field_name": "最新快照", "operator": "is", "value": ["是"]}]},
                "field_names": ["最新快照"], "page_size": 200}
        stale = []
        while True:
            data = _post(tok, path, body)
            items = (data.get("items") or [])
            stale += [r["record_id"] for r in items
                      if r.get("record_id") and r["record_id"] != keep_id]
            if not data.get("has_more"):
                break
            body["page_token"] = data.get("page_token")
        if not stale:
            return
        _post(tok, f"/bitable/v1/apps/{refs['app']}/tables/{refs['profile']}/records/batch_update",
              {"records": [{"record_id": rid, "fields": {"最新快照": "否"}}
                           for rid in stale]})
        log.info("[bitable] snapshots unset: tenant=%s dyid=%s count=%s",
                 tenant_key, dyid, len(stale))
    except Exception:
        log.exception("[bitable] unset old snapshots failed (非致命) tenant=%s", tenant_key)


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


_GMVTEXT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[万kK]?\s*[-~—～]\s*[¥￥]?\s*(\d+(?:\.\d+)?)\s*([万kK]?)")

def _gmv_from_text(text: str):
    """档位文本（如 '¥25万-50万'、'100-250万'）→ 中值（元）。

    百应只给 GMV 档位区间，数值字段为空/0 时按档位中值兜底，
    与 dashboard_card 的「档位中值」口径一致。解析失败返回 None。

    ★ 2026-10-22：实现已搬到 `daren_bot/fmt.gmv_mid_from_text`（轻量、无重依赖）。
      原因：本模块 import `crypto_util` → 需要 pycryptodome → **系统 python 下
      import 就炸**（ModuleNotFoundError: No module named 'Crypto'），
      依赖此解析的工具会静默跳过（异常被 except 吞掉）→ GMV 口径丢失。
      这里保留薄壳，只为不破坏既有调用点。
    """
    from .fmt import gmv_mid_from_text
    return gmv_mid_from_text(text)


def _gmv_value(talent: dict):
    """优先精确 total_gmv；为空/0 时用档位文本中值兜底。"""
    v = _to_float(talent.get("total_gmv") if talent.get("total_gmv") is not None
                  else talent.get("gmv"))
    if v:
        return v
    return _gmv_from_text(talent.get("total_gmv_text") or talent.get("gmv_text"))


def _wan(v) -> float | None:
    """原始数值 → 「万」单位数值（1 位小数）。**薄壳**，实现在 `daren_bot.fmt.wan_value`。

    ★ 2026-10-22 实测（飞书 BaseApp 经营总览页）：
      **指标卡（statistics）对数值一律取整，且不看字段 style** ——
      实测三组：公式 `粉丝(万)`=1.3 → 卡上 **1**；number+precision=1 的
      `粉丝(万)·卡`=1.3 → 卡上 **1**；number+precision=2 的 12.5 → 卡上 **13**
      （不是 12.50）。**只有千分位生效**（原始 `粉丝`=12804 → "12,804"）。
      ⇒ 「字段 style 决定卡上精度」是**错判**，已更正。
      **指标卡的小数精度只能在飞书 UI 里改**（块菜单 → 配置 → 自定义配置 →
      小数精度 = 保留 1 位小数），**API 改不了**（`data_config` 不接受 format/precision，
      服务端也不给 formula 字段加 style）。
      ⇒ 因此这里写「带 style.precision=1 的 number 副本字段」：
        ① 列表/表格视图按字段自身格式显示 **1.3 / 37.5**（与 H5 一致）；
        ② 指标卡指过去，配合 UI 的「小数精度=1 位小数」显示 1.3 / 37.5。
      公式字段保留不动（人类在表里看到的仍是公式，不破坏既有结构）。
    ★ 口径必须与 `fmt.fmt_wan` / H5 逐位一致 ⇒ 实现在 fmt.wan_value，
      舍入用 `_round1`（远离零），**不用内置 round()**（银行家舍入会差 0.1）。
    """
    from .fmt import wan_value
    return wan_value(v)


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
        "累计GMV": _gmv_value(talent),
        # ★ 2026-10-22：飞书卡的「同源副本」—— 指标卡取整、列表/表格按字段格式显示，
        #   所以万单位小数必须落在**独立 number 字段**上（口径见 `fmt.wan_value`）。
        #   字段名不带「(万)」：<1万 的达人沿用 H5 口径存**原值**（0.5万≈5000≠5251，
        #   H5 显示原值正是为了不丢精度）⇒ 写死「(万)」反而误导。
        "粉丝·卡": _wan(talent.get("followers")),
        "累计GMV·卡": _wan(_gmv_value(talent)),
        "视频占比": _to_ratio(talent.get("video_gmv_ratio") or talent.get("video_ratio")),
        "评估结论": verdict or "",
        "最新快照": "是",
        "报告摘要": summary or "",
    }
    if img_path and os.path.isfile(img_path):
        try:
            refs = ensure_bitable(tenant_key)
            ft = _upload_bitable_image(tenant_key, refs["app"], img_path)
            record["报告图"] = [{"file_token": ft}]
        except Exception:
            log.exception("[bitable] 报告图上传失败（记录仍写入，仅无图）")
    rid = add_record(tenant_key, "profile", record)
    _unset_old_snapshots(tenant_key, record["抖音号"], rid)


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
