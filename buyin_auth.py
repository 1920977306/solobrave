"""登录态判定 —— **单一真源**（2026-10-25 新增）。

★ 为什么要抽出来：
  之前判定逻辑散在两处，且**两处口径不一致**：
    - `buyin_worker.Browser.health()`：URL 优先 → `ecom_us_lt_buyin` **且** `sessionid`
    - `buyin_worker._check_login_state()`：URL 优先 → 两个 cookie **任一**即可
  ⇒ 2026-10-25 实测**假阴性**：老大的登录态一直有效
    （`ecom_us_lt_buyin` 有效期到 10-28、`sessionid` 到 12-24），
    但页面刚打开时 URL 还停在带 login/passport 的地址
    ⇒ `/health` 报 `logged_in=false` ⇒ **老大被叫去白扫一次码**，
      而实际登录态一直是好的（后来浏览器重启、页面重新加载后就正常了）。

⇒ 这一层把「cookie 名 + URL 判据 + 两种用途的优先级」收到一处，
  改一处就够，不会再出现「两处口径分叉」。

★★ 两处用途不同，**优先级就该不同**（不是分叉，是分层）：
  | 用途 | 场景 | 判据优先级 | 理由 |
  |---|---|---|---|
  | `prefer_page=False` | 健康检查 / 前置预检（`/health`、登录态巡检）| **cookie 为准** | 只回答「登录态还在不在」⇒ 宁可宽松，也不能把好状态误报成掉登录（否则老大白扫码、达人白等）|
  | `prefer_page=True` | 真正发起采集前（`_check_login_state`）| **页面为准** | 只回答「这次访问通不通」⇒ 服务端会话已死时页面会跳登录页，**必须**拦住（否则会把登录页正文当数据抽）|
"""
from __future__ import annotations

import re

# ── 登录 cookie 名（唯一真源）──
# 团长端：主 cookie + 会话 cookie，两个都在才算登录态完整
CAPTAIN_COOKIE = "ecom_us_lt_buyin"
CAPTAIN_COOKIE_SESSION = "sessionid"
# 达人端：SSO cookie 挂在兄弟域 .buyin-daren-sso.jinritemai.com 上
DAREN_COOKIE = "sid_ucp_sso_v1_daren"

# 登录页 URL 判据：**路径段**匹配，不做裸子串匹配
# （`/dashboard/passport-center` 这种正常业务路径不该被判成登录页）
_LOGIN_PATH_RE = re.compile(r"/(login|passport)([/?#]|$)", re.I)
_LOGIN_HOSTS = ("passport.jinritemai.com", "login.jinritemai.com")


def is_login_url(url: str) -> bool:
    """这个 URL 是不是登录页 / 通行证页。"""
    u = (url or "").strip().lower()
    if not u:
        return False
    if any(h in u for h in _LOGIN_HOSTS):
        return True
    # 只取 path 部分做段匹配（避免 query 里的 login= 参数误伤）
    path = u.split("://", 1)[-1]
    path = path.split("/", 1)[-1] if "/" in path else ""
    path = "/" + path
    path = path.split("?", 1)[0].split("#", 1)[0]
    return bool(_LOGIN_PATH_RE.search(path))


def cookies_logged_in(cookie_names, label: str = "团长端") -> bool:
    """只看 cookie 名判登录态。"""
    names = set(cookie_names or ())
    if label == "达人端":
        return DAREN_COOKIE in names
    return CAPTAIN_COOKIE in names and CAPTAIN_COOKIE_SESSION in names


def judge_login_state(url: str, cookie_names, label: str = "团长端",
                      *, prefer_page: bool = False) -> tuple:
    """判定登录态 → `(logged: bool, reason: str)`。

    ★ `prefer_page=False`（默认，健康检查用）：**cookie 为准**
      —— cookie 在就是登录了，哪怕 URL 还带 login（页面没跳转完/标签页没跟上）。
    ★ `prefer_page=True`（采集前用）：**页面为准**
      —— 页面真的停在登录页 ⇒ 服务端会话已死 ⇒ 拦住。
    """
    names = set(cookie_names or ())
    page_says_login = is_login_url(url)

    if prefer_page:
        if page_says_login:
            return False, "页面跳转到了登录页"
        if cookies_logged_in(names, label):
            return True, "登录 cookie 有效"
        return False, "登录 cookie 缺失"

    if cookies_logged_in(names, label):
        return True, "登录 cookie 有效"
    if page_says_login:
        return False, "页面在登录页且无登录 cookie"
    return False, "登录 cookie 缺失"
