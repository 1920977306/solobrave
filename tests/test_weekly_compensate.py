# -*- coding: utf-8 -*-
"""周采窗后补偿（weekly.py `_maybe_compensate_collect`）+ 失败计数口径守卫。

背景（2026-10-28 老大拍板）：daren_test2 周一窗 07:47 采集、08:04 失败
（buyin worker 409 冲突），失败时刻已过窗 ⇒ 窗内重试够不着 ⇒ 缺一周快照。

同轮挖出的更深根因：`count_auto_fails_this_week` 用
`payload LIKE '%"auto":1%'`，而入队侧 `json.dumps({"auto": 1})` 带空格
⇒ 永远匹配不上 ⇒ 失败计数恒 0 ⇒ 窗内重试**从来没生效过**。
已改 json_extract 解析，两个用例锁死口径。
"""
import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from daren_bot import config, db  # noqa: E402

weekly = importlib.import_module("daren_bot.weekly")


@pytest.fixture()
def bot_db(tmp_path, monkeypatch):
    path = tmp_path / "daren_bot.db"
    monkeypatch.setattr(config, "DB_PATH", path)
    db.init_db()
    return path


def _add_task(path, tid, tk, dyid, status, payload, chat_id="oc_x", created_at=100):
    import sqlite3
    con = sqlite3.connect(str(path))
    con.execute(
        "INSERT INTO bot_collect_tasks (id,tenant_key,type,target,chat_id,status,"
        "cache_hit,credits_cost,payload,created_at) VALUES (?,?,?,?,?,?,0,0,?,?)",
        (tid, tk, "douyin", dyid, chat_id, status, json.dumps(payload), created_at))
    con.commit()
    con.close()


# ─────────────── 失败计数口径（json_extract） ───────────────

def test_count_auto_fails_matches_spaced_payload(bot_db):
    """入队侧 json.dumps 默认带空格（"auto": 1）——必须数得到（原 LIKE 口径数不到）。"""
    _add_task(bot_db, "f1", "t1", "dy1", "failed", {"auto": 1, "retry": 0})
    assert db.count_auto_fails_this_week("t1", "dy1", 0) == 1


def test_count_auto_fails_matches_compact_payload(bot_db):
    _add_task(bot_db, "f1", "t1", "dy1", "failed", {"auto": 1, "retry": 1},
              )
    # 手工紧凑写法的行也要数到
    import sqlite3
    con = sqlite3.connect(str(bot_db))
    con.execute(
        "INSERT INTO bot_collect_tasks (id,tenant_key,type,target,status,payload,"
        "created_at) VALUES ('f2','t1','douyin','dy1','failed','{\"auto\":1}',100)")
    con.commit()
    con.close()
    assert db.count_auto_fails_this_week("t1", "dy1", 0) == 2


def test_count_auto_fails_excludes_non_auto_and_done(bot_db):
    _add_task(bot_db, "f1", "t1", "dy1", "failed", {"auto": 1})
    _add_task(bot_db, "d1", "t1", "dy1", "done", {"auto": 1})      # 成功不算失败
    _add_task(bot_db, "m1", "t1", "dy1", "failed", {"manual": 1})  # 非自动采集不算
    _add_task(bot_db, "b1", "t1", "dy1", "failed", None)           # 坏 payload 不炸
    assert db.count_auto_fails_this_week("t1", "dy1", 0) == 1


# ─────────────── 窗后补偿编排 ───────────────

def _spy_enqueue(monkeypatch):
    calls = []

    def _fake(tk, task_type, target, action, **kw):
        calls.append((tk, task_type, target, action, kw.get("payload", "")))
        return "ok", "task_x", 100

    monkeypatch.setattr(weekly.credits, "charge_and_enqueue", _fake)
    return calls


def _sched():
    return weekly.WeeklyScheduler(wm=None)


def test_compensate_enqueues_after_window_fail(bot_db, monkeypatch):
    """窗内失败 1 次、无快照、无在跑任务 ⇒ 补采入队（payload 带 retry=99 标记）。"""
    _add_task(bot_db, "f1", "t1", "dy1", "failed", {"auto": 1, "retry": 0})
    calls = _spy_enqueue(monkeypatch)
    _sched()._maybe_compensate_collect({"tenant_key": "t1"}, "dy1", 0)
    assert len(calls) == 1
    assert calls[0][1] == "douyin" and calls[0][3] == "weekly_collect"
    assert json.loads(calls[0][4])["retry"] == 99


def test_compensate_skips_when_no_fail(bot_db, monkeypatch):
    calls = _spy_enqueue(monkeypatch)
    _sched()._maybe_compensate_collect({"tenant_key": "t1"}, "dy1", 0)
    assert calls == []


def test_compensate_skips_when_fails_exhausted(bot_db, monkeypatch):
    _add_task(bot_db, "f1", "t1", "dy1", "failed", {"auto": 1, "retry": 0})
    _add_task(bot_db, "f2", "t1", "dy1", "failed", {"auto": 1, "retry": 1})
    calls = _spy_enqueue(monkeypatch)
    _sched()._maybe_compensate_collect({"tenant_key": "t1"}, "dy1", 0)
    assert calls == []  # 两次都败：防无限补，等下周一


def test_compensate_skips_when_collected(bot_db, monkeypatch):
    """任务在跑（pending）时不重复补。"""
    _add_task(bot_db, "f1", "t1", "dy1", "failed", {"auto": 1, "retry": 0})
    _add_task(bot_db, "p1", "t1", "dy1", "pending", {"auto": 1, "retry": 99})
    calls = _spy_enqueue(monkeypatch)
    _sched()._maybe_compensate_collect({"tenant_key": "t1"}, "dy1", 0)
    assert calls == []


def test_compensate_skips_when_snapshot_exists(bot_db, monkeypatch):
    _add_task(bot_db, "f1", "t1", "dy1", "failed", {"auto": 1, "retry": 0})
    db.save_snapshot("t1", "dy1", weekly.report_week_no(), {"followers": 1})
    calls = _spy_enqueue(monkeypatch)
    _sched()._maybe_compensate_collect({"tenant_key": "t1"}, "dy1", 0)
    assert calls == []


# ─────────────── 窗内重试（计数修复后应真正可达） ───────────────

def test_in_window_retry_branch_reachable(bot_db, monkeypatch):
    """失败 1 次 + 距失败满 2h + 未到推送 cutoff ⇒ 触发 retry=1 重采。

    用**构造的窗内时刻**（周一 06:00）而不是真实现在——周末/窗外跑也要可达。
    """
    import datetime
    # 构造窗内时刻：最近一次周一 06:00（在 retry cutoff 08:52 之前、距失败满 2h）
    real_now = datetime.datetime.now()
    now = real_now.replace(hour=6, minute=0, second=0, microsecond=0) \
              - datetime.timedelta(days=real_now.weekday())
    if now >= real_now:
        now -= datetime.timedelta(days=7)
    # 失败时刻 = 构造 now 的 3 小时前（周一 03:00，窗内）⇒ 距失败已满 2h 重试间隔
    _add_task(bot_db, "f1", "t1", "dy1", "failed", {"auto": 1, "retry": 0},
              created_at=int(now.timestamp()) - 3 * 3600)
    calls = _spy_enqueue(monkeypatch)
    days_since_sun = (now.weekday() + 1) % 7
    sun = datetime.datetime.combine(
        (now - datetime.timedelta(days=days_since_sun)).date(),
        datetime.time(20, 0))
    _sched()._maybe_enqueue_collect({"tenant_key": "t1"}, "dy1",
                                    int(sun.timestamp()), now, sun)
    assert len(calls) == 1
    assert json.loads(calls[0][4])["retry"] == 1
