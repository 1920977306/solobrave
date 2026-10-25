"""登录态判定（`buyin_auth.py`）—— 单一真源的回归用例。

★ 起因（2026-10-25 老大事件）：
  老大扫码登录后 `/health` 仍报 `logged_in=false` ⇒ 被反复叫去扫码，
  而**登录态其实一直是好的**（cookie 有效期到 10-28 / 12-24）。
  根因：两处判定口径分叉（health 与 `_check_login_state` 各自一套），
  且 health 把「URL 含 login/passport」放在 cookie **之前** ⇒ 假阴性。

本文件锁住：
1. **URL 判据是路径段匹配**，不是裸子串（`/dashboard/passport-center` 是业务页，不是登录页）
2. **cookie 判据**：团长端要两个 cookie 都在；达人端认自己的 SSO cookie
3. **两种用途两种优先级**（分层，不是分叉）：
   - 健康检查（`prefer_page=False`）⇒ cookie 为准 ⇒ **不许误报掉登录**
   - 采集前（`prefer_page=True`）⇒ 页面为准 ⇒ 页面在登录页必须拦住
4. ★ 2026-10-25 那次假阴性**本身**作为回归用例钉死
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import buyin_auth  # noqa: E402

CAPTAIN_OK = {"ecom_us_lt_buyin", "sessionid"}
DAREN_OK = {"sid_ucp_sso_v1_daren"}


# ── URL 判据 ──

@pytest.mark.parametrize("url", [
    "https://buyin.jinritemai.com/login?redirect=xx",
    "https://buyin.jinritemai.com/passport",
    "https://passport.jinritemai.com/uc/entry",
    "https://login.jinritemai.com/",
])
def test_login_urls_detected(url):
    assert buyin_auth.is_login_url(url)


@pytest.mark.parametrize("url", [
    "https://buyin.jinritemai.com/dashboard",
    "https://buyin.jinritemai.com/dashboard/passport-center",   # ★ 业务页，不是登录页
    "https://buyin.jinritemai.com/dashboard?tab=login-history",  # ★ query 里带 login
    "https://buyin.jinritemai.com/mpa/account/institution-role-select",
    "",
    None,
])
def test_business_urls_not_mistaken_for_login(url):
    assert not buyin_auth.is_login_url(url)


# ── cookie 判据 ──

def test_captain_needs_both_cookies():
    assert buyin_auth.cookies_logged_in(CAPTAIN_OK, "团长端")
    assert not buyin_auth.cookies_logged_in({"ecom_us_lt_buyin"}, "团长端")
    assert not buyin_auth.cookies_logged_in({"sessionid"}, "团长端")
    assert not buyin_auth.cookies_logged_in(set(), "团长端")


def test_daren_uses_its_own_sso_cookie():
    assert buyin_auth.cookies_logged_in(DAREN_OK, "达人端")
    assert not buyin_auth.cookies_logged_in(CAPTAIN_OK, "达人端")
    assert not buyin_auth.cookies_logged_in(set(), "达人端")


# ── 两种用途：分层，不是分叉 ──

def test_health_check_prefers_cookie():
    """健康检查：cookie 在就是登录态在（哪怕 URL 还带 login）。"""
    logged, _ = buyin_auth.judge_login_state(
        "https://buyin.jinritemai.com/login?redirect=xx", CAPTAIN_OK, "团长端")
    assert logged


def test_collect_gate_prefers_page():
    """采集前：页面真在登录页 ⇒ 必须拦住（不然会把登录页正文当数据抽）。"""
    logged, reason = buyin_auth.judge_login_state(
        "https://buyin.jinritemai.com/login?redirect=xx", CAPTAIN_OK, "团长端",
        prefer_page=True)
    assert not logged
    assert "登录页" in reason


def test_collect_gate_passes_when_page_normal_and_cookie_ok():
    logged, _ = buyin_auth.judge_login_state(
        "https://buyin.jinritemai.com/dashboard", CAPTAIN_OK, "团长端",
        prefer_page=True)
    assert logged


def test_no_cookie_means_not_logged_in_both_modes():
    for prefer in (False, True):
        logged, _ = buyin_auth.judge_login_state(
            "https://buyin.jinritemai.com/dashboard", set(), "团长端",
            prefer_page=prefer)
        assert not logged


# ── ★ 2026-10-25 事故本身的回归用例 ──

def test_regression_20261025_no_false_alarm_on_health_check():
    """★ 老大扫码后 /health 仍报 logged_in=false ⇒ 白扫码一次。

    现场：登录态有效（cookie 到 10-28 / 12-24），
    但页面刚打开时 URL 停在带 login 的地址。
    ⇒ 健康检查必须报 true（不许误报掉登录）。
    """
    url = "https://buyin.jinritemai.com/login?redirect=xx"
    logged, reason = buyin_auth.judge_login_state(url, CAPTAIN_OK, "团长端")
    assert logged, f"又误报掉登录了（reason={reason}）"


def test_regression_20261025_daren_health_not_false_negative():
    """达人端同理：SSO cookie 在就别报掉登录。"""
    url = "https://buyin.jinritemai.com/passport/login"
    logged, _ = buyin_auth.judge_login_state(url, DAREN_OK, "达人端")
    assert logged
