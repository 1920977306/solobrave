"""采集通道登录态巡检 —— 掉登录了**主动**叫醒运营，别等达人投诉。

★ 起因（2026-10-25 老大原话）：
  「浏览器的掉了，我也不知道，就是需要我重新扫码登陆或者验证码登陆，我都不知道」
  ⇒ 之前只有「任务失败」这条路能暴露问题：达人踩到、任务失败、达人才看到报错。
    运营全程无感知 ⇒ 老大只能等达人来问。

⇒ 这一层**主动巡检**：定期看采集器健康，判定「达人端是不是真掉登录」，
  一旦掉了立刻告警运营（附**该做的动作**），登录恢复也报一声。

★★ 判定口径（严格照 AGENTS.md §8 的铁律，别拍脑袋）：
  | 采样 | 判定 | 动作 |
  |---|---|---|
  | 采集器不可达 | unknown | 不告警（不是掉登录，是采集器没起） |
  | `daren_browser_up=false` | unknown | **不告警** —— 两个浏览器互斥，切到团长端时达人端会被关掉（§8.2），这是设计不是故障 |
  | `daren_browser_up=true` + `daren_logged_in=false` | **bad** | 告警：真掉登录 |
  | `daren_browser_up=true` + `daren_logged_in=true` | ok | 恢复时告警一次 |

  ★ 只盯**达人端**：商品数据只走达人端（§8.1），团长端登录态与达人链路无关。

★ 边沿触发 + 防抖：
  - 只有「正常 → 异常」的**跳变**才告警（不是每次轮询都喊）。
  - 连续 2 次采样都异常才认定（浏览器切换瞬间健康值会抖）。
  - 告警去重在 `ops_alert`（同 key 15 分钟内只发一次）。
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.request

from . import config, ops_alert

log = logging.getLogger("daren-bot.login-watcher")

BUYIN_CONTROL = "http://127.0.0.1:8095"

# 连续几次采样都异常才认定掉登录（防抖）
BAD_STREAK_THRESHOLD = 2

# 每几轮打一次心跳（可观测性：巡检线程死没死，从日志一眼看得出）
HEARTBEAT_EVERY = 10

DEDUP_KEY = "daren_collector_need_login"

# 「没传 health」和「传了 None（=采不到）」必须区分开：
#   不区分的话，测试里传 None 会去**真**网络拉一次健康值，判定结果不可控。
_UNSET = object()


def classify_health(health: dict | None) -> str:
    """健康 JSON → 'ok' / 'bad' / 'unknown'。

    ★ 纯函数（不碰网络）—— 判定口径要有独立的回归用例钉死。
    """
    if not isinstance(health, dict):
        return "unknown"
    if not health.get("ok", True):
        return "unknown"
    up = bool(health.get("daren_browser_up"))
    logged = bool(health.get("daren_logged_in"))
    if not up:
        # 浏览器没开（切到团长端 / 还没启动）—— **不是掉登录**
        return "unknown"
    return "ok" if logged else "bad"


def fetch_health(timeout: int = 5) -> dict | None:
    """拉采集器健康（失败返回 None = unknown）。"""
    try:
        with urllib.request.urlopen(f"{BUYIN_CONTROL}/health", timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


class LoginWatcher(threading.Thread):
    """后台巡检线程：达人端掉登录 → 告警运营；恢复 → 再报一声。"""

    def __init__(self, interval: int | None = None):
        super().__init__(daemon=True, name="login-watcher")
        self.interval = int(interval or config.LOGIN_WATCH_INTERVAL)
        self._stop_event = threading.Event()
        self._state = "unknown"      # 上一次判定的状态
        self._bad_streak = 0
        self._bad_notified = False   # 本轮「已掉登录」是否已告警过
        self._cycles = 0
        self._last_logged_state = None

    def stop(self):
        self._stop_event.set()

    def run(self):
        log.info("login watcher started (interval=%ss)", self.interval)
        # ⚠️ 用 `self._stop_event`，**不是** `self._stop` ——
        #   `threading.Thread._stop` 是个**方法**（老 API 残留），
        #   写成 `self._stop.is_set()` 会让线程一起来就 AttributeError 死掉 ⇒
        #   巡检能力静默消失（日志里才看得到，服务照跑、告警永不发生）。
        #   2026-10-25 真机重启时实测踩到。
        while not self._stop_event.is_set():
            try:
                self.check_once()
            except Exception:
                # 单次巡检失败不能让线程猝死（否则告警能力静默消失）
                log.exception("login watcher check failed")
            self._stop_event.wait(self.interval)
        log.info("login watcher stopped")

    # ---- 单次采样：抽出来是为了能单测（不依赖线程/网络）----
    def check_once(self, health=_UNSET) -> str:
        """采样 + 判定 + 必要时告警。返回本次判定状态。

        ★ `health` 不传 = 自己去拉；显式传 None = 「采不到」（unknown）。
        """
        if health is _UNSET:
            health = fetch_health()
        state = classify_health(health)

        if state == "bad":
            self._bad_streak += 1
            if self._bad_streak >= BAD_STREAK_THRESHOLD and not self._bad_notified:
                self._bad_notified = True
                self._alert_bad()
        elif state == "ok":
            self._bad_streak = 0
            if self._bad_notified:
                # 恢复：老大刚扫完码，要告诉他「好了，可以继续查商品了」
                self._bad_notified = False
                ops_alert.alert(
                    "✅ 采集通道已恢复",
                    "达人端登录态正常，商品查询可以正常走了。",
                    kind="login_recovered", dedup_key="daren_collector_recovered")
                log.info("login watcher: daren collector recovered")
        else:
            # unknown：采集器没起 / 达人端浏览器没开 —— 不判定、不告警
            log.debug("login watcher: state=unknown")

        self._state = state
        self._cycles += 1
        # ★ 心跳：巡检是**哑的**就没有可观测性 —— 线程死了/卡住了都没人知道。
        #   每 10 轮（默认 10 分钟）打一行状态，状态变了立刻打。
        if state != self._last_logged_state or self._cycles % HEARTBEAT_EVERY == 0:
            log.info("login watcher: state=%s (cycle %d)", state, self._cycles)
            self._last_logged_state = state
        return state

    def _alert_bad(self) -> None:
        ops_alert.alert(
            "⚠️ 达人端采集通道掉登录了",
            "达人端登录态失效，商品查询会失败（达人只会看到「查不到数据」）。\n"
            "动作：打开采集器的浏览器窗口，扫码登录「达人工作台」；登录好之后自动恢复。",
            kind="login_lost", dedup_key=DEDUP_KEY)
        log.error("login watcher: daren collector logged out (alert sent)")
