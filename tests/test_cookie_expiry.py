# -*- coding: utf-8 -*-
"""登录 cookie 临期/失效预警守卫（2026-10-29 事故治本）。

事故：ecom_us_lt_buyin 10-28 到期被清，零预警，达人连撞 4 次采集失败才暴露。
本文件锁：
  ① classify_cookie_expiry 分档（missing/expired/warn/ok + 边界）
  ② read_cookie_expiry 读 sqlite（含库不存在 → 全 None）
  ③ worst_side_state 多端取最坏
  ④ LoginWatcher 接线：告警边沿触发 + 锁存（同状态不重复喊、恢复 ok 解除）

★ 变异对照（改实现 ⇒ 红）：
  - WARN 档常量改大 ⇒ warn 用例红
  - worst 取「最好」而非「最坏」 ⇒ 多端用例红
  - watcher 删锁存 ⇒ 二次采样不重复告警用例红
"""
import sqlite3
import time
from pathlib import Path

import pytest

from daren_bot import cookie_expiry as ce

NOW_US = ce.chrome_now_us()
HOUR_US = 3600 * 1_000_000


def _make_cookies_db(path: Path, rows: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, expires_utc INTEGER)")
    conn.executemany("INSERT INTO cookies VALUES (?,?,?)", rows)
    conn.commit()
    conn.close()


class TestClassify:
    def test_missing(self):
        assert ce.classify_cookie_expiry(None, NOW_US) == "missing"

    def test_expired(self):
        assert ce.classify_cookie_expiry(NOW_US - HOUR_US, NOW_US) == "expired"

    def test_warn_within_48h(self):
        assert ce.classify_cookie_expiry(NOW_US + 10 * HOUR_US, NOW_US) == "warn"

    def test_ok_beyond_48h(self):
        assert ce.classify_cookie_expiry(NOW_US + 100 * HOUR_US, NOW_US) == "ok"

    def test_warn_boundary(self):
        """恰在 48h 线上：剩余 == 阈值 → warn（< 才是 warn，== 视为安全余量内提醒）。"""
        assert ce.classify_cookie_expiry(
            NOW_US + ce.WARN_WINDOW_SECONDS * 1_000_000 - 1, NOW_US) == "warn"


class TestReadExpiry:
    def test_missing_db_all_none(self, tmp_path):
        out = ce.read_cookie_expiry(tmp_path / "nope", ("ecom_us_lt_buyin",))
        assert out == {"ecom_us_lt_buyin": None}

    def test_reads_latest_expiry(self, tmp_path):
        db = tmp_path / "Default" / "Cookies"
        _make_cookies_db(db, [
            ("buyin.jinritemai.com", "ecom_us_lt_buyin", NOW_US + 10 * HOUR_US),
            ("buyin.jinritemai.com", "ecom_us_lt_buyin", NOW_US + 100 * HOUR_US),
        ])
        out = ce.read_cookie_expiry(tmp_path, ("ecom_us_lt_buyin",))
        assert out["ecom_us_lt_buyin"] == NOW_US + 100 * HOUR_US


class TestWorstSideState:
    def _root(self, tmp_path, captain_exp, daren_exp):
        _make_cookies_db(tmp_path / "data/buyin_browser/profile/Default/Cookies",
                         [("buyin.jinritemai.com", "ecom_us_lt_buyin", captain_exp),
                          (".jinritemai.com", "sessionid", NOW_US + 100 * HOUR_US)])
        _make_cookies_db(tmp_path / "data/buyin_browser/profile_daren/Default/Cookies",
                         [(".buyin-daren-sso.jinritemai.com", "sid_ucp_sso_v1_daren", daren_exp)])
        return tmp_path

    def test_worst_of_multi_cookie(self, tmp_path):
        """一端多枚 cookie 取最坏（团长端主凭证临期、sessionid 充足 ⇒ 端判 warn）。
        （对照：worst 改取最好 ⇒ 本用例红）"""
        root = self._root(tmp_path, NOW_US + 10 * HOUR_US, NOW_US + 100 * HOUR_US)
        states = ce.worst_side_state(root)
        assert states["团长端"] == "warn"
        assert states["达人端"] == "ok"

    def test_expired_beats_warn(self, tmp_path):
        root = self._root(tmp_path, NOW_US - HOUR_US, NOW_US + 10 * HOUR_US)
        states = ce.worst_side_state(root)
        assert states["团长端"] == "expired"
        assert states["达人端"] == "warn"

    def test_all_ok(self, tmp_path):
        root = self._root(tmp_path, NOW_US + 100 * HOUR_US, NOW_US + 100 * HOUR_US)
        assert ce.worst_side_state(root) == {"团长端": "ok", "达人端": "ok"}

    def test_missing_db_is_missing(self, tmp_path):
        (tmp_path / "data").mkdir()
        states = ce.worst_side_state(tmp_path)
        assert states == {"团长端": "missing", "达人端": "missing"}


class TestWatcherLatch:
    """watcher 接线：边沿告警 + 锁存。monkeypatch 掉磁盘读取与告警发送。"""

    def _watcher(self, monkeypatch, states_seq):
        from daren_bot import login_watcher, ops_alert
        w = login_watcher.LoginWatcher(interval=60)
        alerts = []
        monkeypatch.setattr(ops_alert, "alert",
                            lambda *a, **kw: alerts.append((a, kw)))
        it = iter(states_seq)
        monkeypatch.setattr(ce, "worst_side_state", lambda root=None: next(it))
        return w, alerts

    def test_alert_once_and_latch(self, monkeypatch):
        states = [{"团长端": "warn", "达人端": "ok"}] * 3
        w, alerts = self._watcher(monkeypatch, states)
        for _ in range(3):
            w.check_once(health=None)  # health=None → unknown，cookie 巡检照跑
        kinds = [kw.get("kind") for _, kw in alerts]
        assert kinds.count("cookie_expiring") == 1, f"同状态重复告警: {alerts!r}"

    def test_recovery_clears_latch(self, monkeypatch):
        states = iter([
            {"团长端": "warn", "达人端": "ok"},
            {"团长端": "ok", "达人端": "ok"},
            {"团长端": "warn", "达人端": "ok"},
        ])
        from daren_bot import login_watcher, ops_alert
        w = login_watcher.LoginWatcher(interval=60)
        alerts = []
        monkeypatch.setattr(ops_alert, "alert",
                            lambda *a, **kw: alerts.append(kw.get("kind")))
        monkeypatch.setattr(ce, "worst_side_state",
                            lambda root=None: next(states))
        for _ in range(3):
            w.check_once(health=None)
        # warn → ok → warn：第二次 warn 必须重新告警（锁存已解除）
        assert alerts == ["cookie_expiring", "cookie_expiring"], alerts

    def test_dead_side_alerts_separately(self, monkeypatch):
        states = [{"团长端": "expired", "达人端": "missing"}]
        w, alerts = self._watcher(monkeypatch, states)
        w.check_once(health=None)
        kinds = sorted(kw.get("kind") for _, kw in alerts)
        assert kinds == ["cookie_dead", "cookie_dead"]
        sides = sorted(kw.get("dedup_key") for _, kw in alerts)
        assert sides == ["cookie_dead_团长端", "cookie_dead_达人端"]
