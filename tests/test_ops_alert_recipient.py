"""运营告警收件人守卫（老大红线 2026-10-31）。

「飞书提醒必须指定一个账号提醒，别给客户（达人）提醒了。」

ops_alert 的结构保证：alert() **没有租户参数**——飞书出口永远用
bind_ws 注入的 _sender_tenant（默认 config.OPS_TENANT_KEY = 运营账号），
chat_id 也只从 OPS_CHAT_ID 显式配置或 OPS_TENANT_KEY 的最近会话解析。
这两条守卫防止后人把出口改歪（比如按消息触发租户发、或把 chat_id
解析改成跟随业务租户）。
"""

import pytest

from daren_bot import config, ops_alert


class _FakeSender:
    def __init__(self):
        self.sent = []

    def send_text(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))


class _FakeWs:
    def __init__(self, sender):
        self._sender = sender
        self.get_sender_calls = []

    def get_sender(self, tenant_key):
        self.get_sender_calls.append(tenant_key)
        return self._sender


@pytest.fixture()
def bound(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "OPS_CHAT_ID", "oc_ops_fixed_chat")
    # 落盘通道指 tmp，零污染生产 ops_alerts.jsonl
    monkeypatch.setattr(config, "OPS_ALERT_FILE", str(tmp_path / "alerts.jsonl"))
    sender = _FakeSender()
    ws = _FakeWs(sender)
    ops_alert.bind_ws(ws)          # 默认 → config.OPS_TENANT_KEY
    return sender, ws


def test_feishu_goes_to_ops_tenant_only(bound):
    """★ 红线：飞书出口只能拿 OPS_TENANT_KEY 的 sender——
    无论哪个达人/客户租户触发了告警。"""
    sender, ws = bound
    ok = ops_alert.alert("测试告警", kind="test", dedup_key="t1")
    assert ok and sender.sent, "飞书通道应送达"
    assert ws.get_sender_calls == [config.OPS_TENANT_KEY], (
        f"sender 租户必须是运营账号 {config.OPS_TENANT_KEY!r}，"
        f"实际拿了 {ws.get_sender_calls!r} —— 可能把提醒发到达人了！")


def test_chat_id_resolves_via_ops_tenant(bound, monkeypatch):
    """★ 红线：chat_id 解析路径只用 OPS_TENANT_KEY 的最近会话，
    绝不向任何业务租户的会话解析。"""
    seen = []

    def _spy(tenant_key):
        seen.append(tenant_key)
        return "oc_from_ops_chat"

    monkeypatch.setattr(config, "OPS_CHAT_ID", "")   # 走自动解析分支
    from daren_bot import db
    monkeypatch.setattr(db, "get_latest_chat_id", _spy)
    sender, ws = bound
    ops_alert.alert("测试告警2", kind="test", dedup_key="t2")
    assert seen == [config.OPS_TENANT_KEY], (
        f"最近会话解析必须查运营账号，实际查了 {seen!r}")
    assert sender.sent and sender.sent[0][0] == "oc_from_ops_chat"


def test_bind_ws_default_is_ops_tenant(bound):
    """bind_ws 不带参数 ⇒ sender 租户回退到 OPS_TENANT_KEY（不是空、不是业务租户）。"""
    _, ws = bound
    ops_alert.alert("测试告警3", kind="test", dedup_key="t3")
    assert ws.get_sender_calls == [config.OPS_TENANT_KEY]
    assert config.OPS_TENANT_KEY != "mumu"  # 运营键绝不能是客户租户
