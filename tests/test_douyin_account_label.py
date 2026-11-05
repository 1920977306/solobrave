"""「抖音账号：xxx」路由修复测试 (2026-11-06)。

生产实锤（新租户 t_20359bf4 的真实聊天记录）：
    达人连发 6 次「抖音名：一百 抖音账号：Ayn11930 这是我的账号，帮我分析一下」
    ⇒ 全部掉进 qa 通用问答（1 分/次），AI 每次都回「没有数据，请发截图来」；
    ⇒ 真实账号采集（douyin，100 分/次）从未触发。

根因：DOUYIN_IN_TEXT_RE 只认字面「抖音号：」，客户写「抖音账号：」整句不匹配，
落到最后走 LLM 问答瞎答。

锁死：
    正：抖音号 / 抖音账号 / 抖音ID / 抖音id 显式标注 ⇒ 走 douyin 采集
    反：无标注的中文提问 ⇒ 仍走 qa，不能被新正则劫持
    反：「换绑抖音账号：xxx」⇈ 仍走零成本换绑（必须排在采集之前）
    反：裸商品ID（17-20 位数字）⇈ 仍走商品链路，不被「抖音号」前缀语义影响
"""
import importlib
import os
import tempfile
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TK = "tk_dy_account_label_test"


@pytest.fixture()
def bot(monkeypatch):
    tmp = tempfile.mkdtemp(prefix='daren_dyacct_bot_')
    monkeypatch.setenv('DAREN_DB', os.path.join(tmp, 'daren_bot.db'))
    import daren_bot.config as cfg
    import daren_bot.db as ddb
    import daren_bot.handler as hd
    import daren_bot.executors as ex
    importlib.reload(cfg)
    importlib.reload(ddb)
    importlib.reload(hd)
    importlib.reload(ex)
    ddb.init_db()
    ddb.grant(TK, 1000)
    return {"db": ddb, "handler": hd, "executors": ex, "config": cfg}


class _FakeSender:
    def __init__(self):
        self.sent = []

    def send_text(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))

    @property
    def text(self):
        return "\n".join(t for _, t in self.sent)


def _router(handler):
    class _WM:
        def __init__(self, s):
            self.s = s

        def get_sender(self, tenant_key):
            return self.s

    s = _FakeSender()
    return handler.Router(_WM(s)), s


def _dispatch(router, text, tenant=None):
    tenant = tenant or {"tenant_key": TK, "bind_douyin_id": ""}
    data = SimpleNamespace(
        event=SimpleNamespace(
            message=SimpleNamespace(
                chat_id="chat_1", message_type="text",
                content='{"text": %s}' % __import__('json').dumps(
                    text, ensure_ascii=False)),
            sender=SimpleNamespace(sender_id=SimpleNamespace(open_id="ou_test")),
        ))
    router._dispatch(tenant, data)
    return tenant


def _spy(bot, monkeypatch):
    db = bot["db"]
    enq = []
    orig = db.enqueue_task_charged

    def spy(*a, **kw):
        enq.append(a)
        return orig(*a, **kw)

    monkeypatch.setattr(db, "enqueue_task_charged", spy)
    return enq


# ---------------- 正：四种标注写法都走 douyin 采集 ----------------

def test_douyin_account_label_full_sentence(bot, monkeypatch):
    """生产原句一字不改：「抖音名：一百 抖音账号：Ayn11930 这是我的账号，帮我分析一下」。"""
    router, sender = _router(bot["handler"])
    enq = _spy(bot, monkeypatch)
    _dispatch(router, "抖音名：一百 抖音账号：Ayn11930 这是我的账号，帮我分析一下")
    kinds = [a[1] for a in enq]
    assert "douyin" in kinds, f"应走 douyin 采集，实际入队={kinds}；回复={sender.text!r}"
    assert any(a[2] == "Ayn11930" for a in enq if a[1] == "douyin")


def test_douyin_zhanghao_colon(bot, monkeypatch):
    router, _ = _router(bot["handler"])
    enq = _spy(bot, monkeypatch)
    _dispatch(router, "抖音账号：hlb725")
    assert any(a[1] == "douyin" and a[2] == "hlb725" for a in enq), \
        f"实际入队={[(a[1], a[2]) for a in enq]}"


def test_douyin_hao_unchanged(bot, monkeypatch):
    """旧写法「抖音号：」行为不变。"""
    router, _ = _router(bot["handler"])
    enq = _spy(bot, monkeypatch)
    _dispatch(router, "抖音号：hlb725")
    assert any(a[1] == "douyin" and a[2] == "hlb725" for a in enq)


def test_douyin_id_label(bot, monkeypatch):
    """「抖音ID：xxx」也是显式标注。"""
    router, _ = _router(bot["handler"])
    enq = _spy(bot, monkeypatch)
    _dispatch(router, "帮我看下这个达人 抖音ID:abc12345")
    assert any(a[1] == "douyin" and a[2] == "abc12345" for a in enq), \
        f"实际入队={[(a[1], a[2]) for a in enq]}"


# ---------------- 反：不能被新正则误劫持 ----------------

def test_plain_question_stays_qa(bot, monkeypatch):
    """无标注、无抖音号的中文提问 ⇒ 仍走 qa，不被「账号」二字误伤。"""
    router, _ = _router(bot["handler"])
    enq = _spy(bot, monkeypatch)
    _dispatch(router, "这是我的账号，帮我分析一下")
    kinds = [a[1] for a in enq]
    assert "douyin" not in kinds, f"无标注提问不应触发采集，实际={kinds}"


def test_rebind_douyin_zhanghao_not_hijacked(bot, monkeypatch):
    """「换绑抖音账号：xxx」⇒ 零成本换绑，绝不入队采集（分支必须在 dy_in_text 之前）。"""
    router, _ = _router(bot["handler"])
    enq = _spy(bot, monkeypatch)
    tenant = {"tenant_key": TK, "bind_douyin_id": "old_dy_1"}
    _dispatch(router, "换绑抖音账号：newdy123", tenant)
    assert not enq, f"换绑不应入队，实际={[(a[1], a[2]) for a in enq]}"
    assert tenant["bind_douyin_id"] == "newdy123", "换绑应改绑定号"


def test_bare_product_id_still_product(bot, monkeypatch):
    """裸商品ID（19 位）⇒ 仍走商品链路。回归 §8 铁律：不能被抖音号规则抢。"""
    router, _ = _router(bot["handler"])
    enq = _spy(bot, monkeypatch)
    pid = "3668788850777522321"
    _dispatch(router, pid)
    kinds = [a[1] for a in enq]
    assert "douyin" not in kinds, f"裸商品ID 不能被当成抖音号，实际={kinds}"
    assert any(k == "product" for k in kinds), f"应走商品链路，实际={kinds}"
