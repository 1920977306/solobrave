# -*- coding: utf-8 -*-
"""§118 P0 聊天落库 单测（2026-11-01，飞书助理平台架构 P0）

背景：聊天文本此前哪里都不落库（日志只有元数据），「角色好不好/翻车率/达人需求」
无法自动化评估。本文件的 bot_chat_messages 是一切评估的地基。

守卫点（行为断言，变异对照：删 handler 入站落库调用 / 删 Sender 出站落库调用必红）：
1. save_chat_message：in/out 双向落库、direction 校验、content 截 2000
2. list_chat_messages：新→旧倒序、limit 封顶 500、before_ts 翻页
3. purge_old_chat_messages：90 天滚动删除，近期的保留，days 参数生效
4. handler._dispatch 入站统一落库（text 存原文 / 非 text 存原始 body JSON）
5. 入站落库失败被吞（绝不阻塞回复路径）
6. Sender.send_text 出站落库 + 落库失败不影响发送 + 无 tenant_key 不写

铁律：零 prod 接触 — bot 全套 DAREN_DB/DAREN_DATA_DIR 指 tmp；
ws_manager 走 test_image_intake 的假 lark_oapi 注入模式（本机无 SDK）。
"""
import importlib
import json
import threading
from types import SimpleNamespace

import pytest

TK = "tk_chatmsg"


@pytest.fixture()
def bot(tmp_path, monkeypatch):
    """隔离环境（照抄 tests/test_image_intake.py 口径）：DB/数据目录全落 tmp。"""
    monkeypatch.setenv("DAREN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DAREN_DB", str(tmp_path / "daren_bot.db"))
    monkeypatch.delenv("DAREN_USE_KIMI", raising=False)
    import daren_bot.config as cfg
    import daren_bot.db as ddb
    import daren_bot.handler as hd
    for m in (cfg, ddb, hd):
        importlib.reload(m)
    ddb.init_db()
    ddb.grant(TK, 1000)
    return {"db": ddb, "handler": hd, "config": cfg}


class _FakeSender:
    def __init__(self):
        self.sent = []

    def send_text(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))

    @property
    def text(self):
        return "\n".join(t for _, t in self.sent)


def _router(handler, sender):
    class _WM:
        def __init__(self, s):
            self.s = s

        def get_sender(self, tenant_key):
            return self.s
    return handler.Router(_WM(sender)), sender


def _add(db, tk):
    db.add_tenant(f"app_{tk}", tk, "enc", "", "daren")


# ---------- 守卫 1-3：db 层 ----------

class TestSaveChatMessage:
    def test_in_out_roundtrip(self, bot):
        db = bot["db"]
        _add(db, TK)
        db.save_chat_message(TK, "chat_1", "in", "text", "帮我分析这个账号")
        db.save_chat_message(TK, "chat_1", "out", "text", "好的，5-8分钟出报告")
        rows = db.list_chat_messages(TK)
        assert len(rows) == 2
        assert rows[0]["direction"] == "out", "倒序：最新在前"
        assert rows[1]["direction"] == "in"
        assert rows[1]["content"] == "帮我分析这个账号"

    def test_direction_validated(self, bot):
        with pytest.raises(ValueError):
            bot["db"].save_chat_message(TK, "c", "sideways", "text", "x")

    def test_content_truncated(self, bot):
        db = bot["db"]
        _add(db, TK)
        db.save_chat_message(TK, "c", "in", "text", "长" * 5000)
        row = db.list_chat_messages(TK)[0]
        assert len(row["content"]) == 2000


class TestListAndPurge:
    def test_limit_capped_and_before_ts(self, bot):
        db = bot["db"]
        _add(db, TK)
        ids = []
        for i in range(10):
            ids.append(db.save_chat_message(TK, "c", "in", "text", f"msg{i}"))
        # now_ts 秒级精度——同秒批量写入时间戳相同, 显式拨开再测翻页
        with db.db_ctx() as conn:
            for i, mid in enumerate(ids):
                conn.execute("UPDATE bot_chat_messages SET created_at=? WHERE id=?",
                             (db.now_ts() + i, mid))
        rows = db.list_chat_messages(TK, limit=3)
        assert len(rows) == 3 and rows[0]["content"] == "msg9"
        # limit 封顶 500：传 9999 也只回 10 条（库里就 10 条），不报错
        assert len(db.list_chat_messages(TK, limit=9999)) == 10
        mid = rows[0]["created_at"]
        older = db.list_chat_messages(TK, limit=100, before_ts=mid)
        assert all(r["created_at"] < mid for r in older)
        assert len(older) == 9

    def test_purge_90d(self, bot):
        db = bot["db"]
        _add(db, TK)
        db.save_chat_message(TK, "c", "in", "text", "recent")
        old_id = db.save_chat_message(TK, "c", "in", "text", "ancient")
        with db.db_ctx() as conn:
            conn.execute("UPDATE bot_chat_messages SET created_at=? WHERE id=?",
                         (db.now_ts() - 91 * 86400, old_id))
        n = db.purge_old_chat_messages()
        assert n == 1
        rows = db.list_chat_messages(TK)
        assert len(rows) == 1 and rows[0]["content"] == "recent"

    def test_purge_days_param(self, bot):
        db = bot["db"]
        _add(db, TK)
        mid_id = db.save_chat_message(TK, "c", "in", "text", "5d-old")
        with db.db_ctx() as conn:
            conn.execute("UPDATE bot_chat_messages SET created_at=? WHERE id=?",
                         (db.now_ts() - 5 * 86400, mid_id))
        assert db.purge_old_chat_messages(days=1) == 1
        assert db.list_chat_messages(TK) == []


# ---------- 守卫 4-5：handler 入站 ----------

def _dispatch_data(chat_id, msg_type, content_json):
    return SimpleNamespace(event=SimpleNamespace(
        message=SimpleNamespace(chat_id=chat_id, message_type=msg_type,
                                content=content_json),
        sender=SimpleNamespace(sender_id=SimpleNamespace(open_id="ou_x"))))


class TestDispatchInboundLog:
    def test_text_inbound_logged(self, bot):
        db = bot["db"]
        _add(db, TK)
        router, s = _router(bot["handler"], _FakeSender())
        tenant = db.get_tenant(TK)
        router._dispatch(tenant, _dispatch_data(
            "chat_1", "text", json.dumps({"text": "dytest123"})))
        ins = [r for r in db.list_chat_messages(TK) if r["direction"] == "in"]
        assert len(ins) == 1, "入站消息必须落一条 in"
        assert ins[0]["msg_type"] == "text"
        assert ins[0]["content"] == "dytest123"

    def test_image_inbound_logged_raw_body(self, bot):
        db = bot["db"]
        _add(db, TK)
        router, s = _router(bot["handler"], _FakeSender())
        tenant = db.get_tenant(TK)
        body = json.dumps({"image_key": "img_v3_x"})
        router._dispatch(tenant, _dispatch_data("chat_1", "image", body))
        ins = [r for r in db.list_chat_messages(TK) if r["direction"] == "in"]
        assert ins and ins[0]["msg_type"] == "image"
        assert "img_v3_x" in ins[0]["content"]

    def test_inbound_log_failure_swallowed(self, bot, monkeypatch):
        """落库挂了聊天必须照走（best-effort 硬约束）。"""
        db = bot["db"]
        _add(db, TK)
        router, s = _router(bot["handler"], _FakeSender())

        def _boom(*a, **kw):
            raise RuntimeError("db down")
        monkeypatch.setattr(db, "save_chat_message", _boom)
        tenant = db.get_tenant(TK)
        router._dispatch(tenant, _dispatch_data(
            "chat_1", "sticker", json.dumps({"key": "v"})))
        assert s.sent, "回复路径不能被落库失败阻断"


# ---------- 守卫 6：Sender 出站 ----------

def _install_fake_lark(monkeypatch):
    """照 tests/test_image_intake.py：注入最小假 lark_oapi 让 ws_manager 可导入。"""
    import sys
    import types
    lark = types.ModuleType("lark_oapi")
    ws_mod = types.ModuleType("lark_oapi.ws")
    client_mod = types.ModuleType("lark_oapi.ws.client")
    ws_mod.client = client_mod
    lark.ws = ws_mod
    lark.LogLevel = types.SimpleNamespace(DEBUG=0)
    lark.Client = types.SimpleNamespace(builder=lambda: None)
    monkeypatch.setitem(sys.modules, "lark_oapi", lark)
    monkeypatch.setitem(sys.modules, "lark_oapi.ws", ws_mod)
    monkeypatch.setitem(sys.modules, "lark_oapi.ws.client", client_mod)


class TestSenderOutboundLog:
    def _sender(self, monkeypatch, tenant_key=TK):
        _install_fake_lark(monkeypatch)
        from daren_bot.ws_manager import Sender
        s = object.__new__(Sender)   # 绕过 __init__（不需要真 ws/SDK client）
        s._tenant_key = tenant_key
        s._cv = threading.Condition()
        s._queue = []
        return s

    def test_send_text_logs_out(self, bot, monkeypatch):
        db = bot["db"]
        _add(db, TK)
        s = self._sender(monkeypatch)
        s.send_text("chat_1", "收到，我想一下～")
        outs = [r for r in db.list_chat_messages(TK) if r["direction"] == "out"]
        assert len(outs) == 1, "出站文本必须落一条 out"
        assert outs[0]["content"] == "收到，我想一下～"
        assert outs[0]["chat_id"] == "chat_1"
        assert s._queue, "落库不能影响真实发送队列"

    def test_outbound_log_failure_still_sends(self, bot, monkeypatch):
        db = bot["db"]
        _add(db, TK)

        def _boom(*a, **kw):
            raise RuntimeError("db down")
        monkeypatch.setattr(db, "save_chat_message", _boom)
        s = self._sender(monkeypatch)
        s.send_text("chat_1", "回复内容")
        assert len(s._queue) == 1, "发送不能被落库失败阻断"

    def test_no_tenant_key_no_write(self, bot, monkeypatch):
        db = bot["db"]
        _add(db, TK)
        s = self._sender(monkeypatch, tenant_key="")
        s.send_text("chat_1", "x")
        assert db.list_chat_messages(TK) == []
