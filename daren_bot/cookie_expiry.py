"""登录 cookie 临期/失效预警 —— 治「cookie 悄悄到期，达人撞墙才发现」（2026-10-29 事故）。

★ 起因：ecom_us_lt_buyin（团长端登录 cookie）10-28 到期被清，**没有任何预警**，
  10-29 下午达人连撞 4 次采集失败才暴露。复盘：盘上 cookie 有效期一查便知
  （§8.14 的正确排查姿势），这件事应该**机器每天盯**，不是人撞墙后取证。

判定口径（纯函数 classify_cookie_expiry，独立回归钉死）：
  缺失            → 'missing'（要扫码）
  已过期          → 'expired'（要扫码）
  剩余 < 48h      → 'warn'（临期，方便时提前扫码）
  其余            → 'ok'

每端只看「最关键的一枚」登录 cookie（判据与 buyin_auth.py 同源口径）：
  团长端 profile        → ecom_us_lt_buyin
  达人端 profile_daren  → sid_ucp_sso_v1_daren（达人端会话主凭证；缺失则退化看 passport_auth_status_daren）
"""
from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

log = logging.getLogger("daren-bot.cookie-expiry")

# Chrome cookie 时间戳：1601-01-01 起的微秒
_CHROME_EPOCH_US = 11644473600 * 1_000_000

# 临期阈值：剩余不足 48h 就提醒（提前量要给老大留出「想起来去扫」的窗口）
WARN_WINDOW_SECONDS = 48 * 3600

# 两端的 profile 目录与关键 cookie 名（与 buyin_auth.py 同源口径）
SIDES = {
    "团长端": {
        "profile": Path("data/buyin_browser/profile"),
        "cookies": ("ecom_us_lt_buyin", "sessionid"),
    },
    "达人端": {
        "profile": Path("data/buyin_browser/profile_daren"),
        "cookies": ("sid_ucp_sso_v1_daren", "passport_auth_status_daren"),
    },
}


def chrome_now_us() -> int:
    return int(time.time() * 1_000_000) + _CHROME_EPOCH_US


def classify_cookie_expiry(expires_us, now_us: int) -> str:
    """单枚 cookie 的到期状态（纯函数）。

    expires_us=None → 'missing'；过期/临期/正常按 WARN_WINDOW_SECONDS 分档。
    """
    if expires_us is None:
        return "missing"
    remain = int(expires_us) - int(now_us)
    if remain <= 0:
        return "expired"
    if remain < WARN_WINDOW_SECONDS * 1_000_000:
        return "warn"
    return "ok"


def read_cookie_expiry(profile_dir: Path, names: tuple) -> dict:
    """读 profile 的 Cookies sqlite，返回 {cookie名: expires_utc 或 None}。

    库不存在 / 读失败 → 全部 None（= missing，由调用方决定要不要报）。
    只读打开，不碰浏览器写入（Chromium Cookies 库支持并发读）。
    """
    out = {n: None for n in names}
    db_path = Path(profile_dir) / "Default" / "Cookies"
    if not db_path.is_file():
        return out
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            for name in names:
                row = conn.execute(
                    "SELECT expires_utc FROM cookies WHERE name=? "
                    "ORDER BY expires_utc DESC LIMIT 1", (name,)).fetchone()
                if row:
                    out[name] = int(row[0])
        finally:
            conn.close()
    except Exception:
        log.warning("read cookie expiry failed: %s", db_path, exc_info=True)
    return out


def worst_side_state(root: Path | None = None) -> dict:
    """两端合并判定（root=None → 当前工作目录下的 data/）。

    返回 {"团长端": state, "达人端": state}，state ∈ ok/warn/expired/missing。
    每端的多枚 cookie 是「主凭证 + 备用」关系：取第一枚存在的来判定
    （主凭证缺失但备用还在 = 有效登录态，按备用的到期日算；全缺才是 missing）。
    """
    root = Path(root) if root else Path(".")
    result = {}
    now_us = chrome_now_us()
    for side, spec in SIDES.items():
        profile = root / spec["profile"]
        exps = read_cookie_expiry(profile, spec["cookies"])
        # 多端 cookie 是「主凭证 + 备用」关系：取**第一枚存在的**来判定
        # （主凭证缺失但备用在 ≠ missing——那也是有效登录态，只是到期日按备用来）
        picked = next((v for n in spec["cookies"]
                       for v in (exps.get(n),) if v is not None), None)
        result[side] = classify_cookie_expiry(picked, now_us)
    return result
