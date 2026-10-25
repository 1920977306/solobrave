"""运营告警出口 —— 让**运营（老大）**第一时间知道「采集通道掉登录了」。

★ 为什么单独一层（2026-10-25 老大原话）：
  「浏览器的掉了，我也不知道，就是需要我重新扫码登陆或者验证码登陆，我都不知道」
  ⇒ 达人那边看到的是一串技术异常，运营那边**什么都没收到**。
  ⇒ 这一层把「机器知道的事」翻译成「运营该做的一个动作」，推到运营看得到的通道。

★ 通道实测结论（2026-10-25 在这台机器上真跑过，不是推测）：
  | 通道 | 实测 | 说明 |
  |---|---|---|
  | macOS 本地通知 | ✅ 可用 | `osascript display notification`，老大本机直接弹窗 |
  | 日志 `[OPS-ALERT]` | ✅ 可用 | launchd 服务日志，`grep OPS-ALERT` 就能捞 |
  | 落盘 jsonl | ✅ 可用 | admin 后台读它展示待处理告警 |
  | 飞书（运营租户最近会话）| ✅ **启用** | 老大确认「我看得见」⇒ 走 `daren_boss` 那条（跟 weekly 运营告警同一条通道）|
  | 飞书（老大私聊 `oc_c086…`）| ❌ 不可用 | `code=230002 Bot/User can NOT be out of the chat` —— 老大私聊是**另一个 app** 的会话，达人 bot 进不去 ⇒ **别配成 OPS_CHAT_ID** |

★ ⇒ 飞书走「运营租户的最近会话」，**老大不用手填 chat id**；
  要固定到别的会话，在 `.env` 里配 `DAREN_OPS_CHAT_ID`。

★ 这一层**永不抛异常**：告警失败不能拖垮采集主链路。
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path

from . import config

log = logging.getLogger("daren-bot.ops-alert")

# 同一个 key 在这个秒数内只告警一次（防刷屏：一次掉登录可能连着好几个达人踩）
DEDUP_SECONDS = 900

_last_alert_at: dict = {}

# 绑定的 WsManager（main.py 启动时注入）—— 飞书通道要用它拿 sender
_ws = None
_sender_tenant = ""


def bind_ws(ws, sender_tenant: str = "") -> None:
    """注入 WsManager（供飞书通道用）。没有它 ⇒ 只有本机通道（弹窗/日志/落盘）。"""
    global _ws, _sender_tenant
    _ws = ws
    _sender_tenant = sender_tenant or config.OPS_TENANT_KEY


def _applescript_quote(s: str) -> str:
    """转义成 AppleScript 字符串字面量（换行压成空格，弹窗放不下多行）。"""
    s = str(s or "").replace("\\", "\\\\").replace('"', '\\"')
    s = s.replace("\r", " ").replace("\n", " ")
    return '"' + s + '"'


def _mac_notify(title: str, body: str) -> bool:
    """macOS 本地通知（老大本机弹窗）。"""
    if os.uname().sysname != "Darwin":
        return False
    script = (f"display notification {_applescript_quote(body)} "
              f"with title {_applescript_quote(title)}")
    try:
        p = subprocess.run(["osascript", "-e", script],
                           capture_output=True, timeout=8)
        return p.returncode == 0
    except Exception as e:
        log.warning("mac notify failed: %s", e)
        return False


def _append_file(title: str, body: str, kind: str) -> bool:
    """落盘（admin 后台读它）。"""
    try:
        path = Path(config.OPS_ALERT_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": time.time(), "time": time.strftime("%Y-%m-%d %H:%M:%S"),
               "kind": kind, "title": title, "body": body}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except Exception as e:
        log.warning("ops alert file append failed: %s", e)
        return False


def _feishu_notify(title: str, body: str) -> bool:
    """飞书（走**运营租户的最近会话** —— 跟 weekly 运营告警同一条通道）。

    ★ 会话来源有两级：
      ① `DAREN_OPS_CHAT_ID` 显式配置 → 用它（要固定到别的会话时才需要）
      ② 没配置 → 自动取 `db.get_latest_chat_id(OPS_TENANT_KEY)`
        ⇒ 老大不用手填 chat id，换了会话也不会失效。
    ★ 实测边界（2026-10-25）：
      老大自己的私聊 `oc_c086…` **发不进去**
      （`code=230002 Bot/User can NOT be out of the chat` —— 那是另一个 app 的会话，
      达人 bot 不在里面）⇒ 别把它配成 OPS_CHAT_ID。
    """
    chat_id = (config.OPS_CHAT_ID or "").strip()
    if not chat_id:
        try:
            from . import db
            chat_id = (db.get_latest_chat_id(config.OPS_TENANT_KEY) or "").strip()
        except Exception as e:
            log.warning("ops alert: resolve ops chat failed: %s", e)
            chat_id = ""
    if not chat_id:
        log.warning("ops alert: no ops chat resolved, skip feishu")
        return False
    if _ws is None:
        log.warning("ops alert: ws not bound, skip feishu")
        return False
    try:
        sender = _ws.get_sender(_sender_tenant)
        if sender is None:
            log.warning("ops alert: no sender for tenant=%s", _sender_tenant)
            return False
        sender.send_text(chat_id, f"⚠️ {title}\n{body}")
        return True
    except Exception as e:
        log.warning("ops alert feishu failed: %s", e)
        return False


def alert(title: str, body: str = "", *, kind: str = "ops",
          dedup_key: str = "") -> bool:
    """发一条运营告警。返回 True = 至少一个通道送达。

    ★ **永不抛异常** —— 告警失败不能拖垮采集主链路。
    """
    title = str(title or "运营告警")
    body = str(body or "")
    if dedup_key:
        last = _last_alert_at.get(dedup_key)
        now = time.time()
        if last and now - last < DEDUP_SECONDS:
            log.info("[OPS-ALERT-SUPPRESSED] %s (dedup=%s)", title, dedup_key)
            return False
        _last_alert_at[dedup_key] = now

    log.error("[OPS-ALERT] %s%s", title, ("\n" + body) if body else "")

    ok = False
    # ★ 每个通道单独兜底：任何一个通道挂了都不许冒到主链路
    #   （告警是**旁路**，它挂了，采集/问答链路必须照跑）。
    for name, fn in (("file", lambda: _append_file(title, body, kind)),
                     ("mac", lambda: _mac_notify(title, body)),
                     ("feishu", lambda: _feishu_notify(title, body))):
        try:
            ok = bool(fn()) or ok
        except Exception as e:
            log.warning("ops alert channel %s failed: %s", name, e)
    return ok


def recent(limit: int = 20) -> list:
    """最近告警（admin 后台展示用）。读文件失败返回空列表，不抛。"""
    try:
        path = Path(config.OPS_ALERT_FILE)
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        out = []
        for ln in lines[-int(limit):]:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
        return out
    except Exception as e:
        log.warning("ops alert read failed: %s", e)
        return []
