"""① 达人聊天内换绑小号（§126）+ ② 运营后台一键创建飞书助理（tenant-create 端点）。

背景（2026-11-04 老大两个拍板）：
- 「如果达人需要换自己的小号怎么办」→ 聊天内 `换绑 <抖音号>`，零成本路由；
  ★ 必须排在 dy_in_text 之前——「换绑抖音号：xxx」会被「抖音号：」前缀抢走。
- 「把创建飞书助理弄在我们系统上，填表即创建」→ /admin/api/tenant-create。
"""
import importlib
import json
import os
import tempfile
import threading
from http.server import HTTPServer
from types import SimpleNamespace
from urllib import request as urlreq
from urllib.error import HTTPError

import pytest

TK = "daren_test2"
APP_ID = "cli_a" + "1" * 15          # 合规 app_id 形态
APP_ID_2 = "cli_b" + "2" * 15


@pytest.fixture()
def bot(monkeypatch):
    tmp = tempfile.mkdtemp(prefix='daren_rebind_')
    monkeypatch.setenv('DAREN_DATA_DIR', os.path.join(tmp, 'data'))
    monkeypatch.setenv('DAREN_DB', os.path.join(tmp, 'daren_bot.db'))
    import daren_bot.config as cfg
    import daren_bot.db as ddb
    import daren_bot.handler as hd
    for m in (cfg, ddb, hd):
        importlib.reload(m)
    ddb.init_db()
    ddb.add_tenant(APP_ID, TK, "enc-dummy", bind_douyin_id="old_id_1",
                   role=cfg.ROLE_DAREN)
    return {"db": ddb, "handler": hd, "config": cfg}


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


def _dispatch(router, text, tenant):
    data = SimpleNamespace(
        event=SimpleNamespace(
            message=SimpleNamespace(
                chat_id="chat_1", message_type="text",
                content=json.dumps({"text": text}, ensure_ascii=False)),
            sender=SimpleNamespace(sender_id=SimpleNamespace(open_id="ou_test")),
        ))
    router._dispatch(tenant, data)


class TestRebind:
    def test_plain_rebind(self, bot):
        hd, ddb = bot["handler"], bot["db"]
        router, s = _router(hd)
        tenant = {"tenant_key": TK, "bind_douyin_id": "old_id_1",
                  "role": "daren"}
        _dispatch(router, "换绑 newid_888", tenant)
        assert ddb.get_tenant(TK)["bind_douyin_id"] == "newid_888"
        assert "已换绑到 newid_888" in s.text
        assert "old_id_1" in s.text          # 告知原绑定号

    def test_rebind_with_douyin_prefix_not_collected(self, bot, monkeypatch):
        """★「换绑抖音号：abc123」绝不能被 dy_in_text 抢走走采集（扣 100 分）。"""
        hd, ddb = bot["handler"], bot["db"]
        router, s = _router(hd)
        spy = []
        monkeypatch.setattr(ddb, "enqueue_task_charged",
                            lambda *a, **kw: spy.append(a))
        tenant = {"tenant_key": TK, "bind_douyin_id": "old_id_1",
                  "role": "daren"}
        _dispatch(router, "换绑抖音号：abc123", tenant)
        assert ddb.get_tenant(TK)["bind_douyin_id"] == "abc123"
        assert spy == [], "换绑被当成采集入队了 —— 路由顺序错了！"
        assert "已换绑" in s.text

    def test_rebind_same_id_noop(self, bot):
        hd, ddb = bot["handler"], bot["db"]
        router, s = _router(hd)
        tenant = {"tenant_key": TK, "bind_douyin_id": "old_id_1",
                  "role": "daren"}
        _dispatch(router, "换绑 old_id_1", tenant)
        assert ddb.get_tenant(TK)["bind_douyin_id"] == "old_id_1"
        assert "不用换" in s.text

    def test_rebind_commerce_role_not_handled(self, bot, monkeypatch):
        """商务助理无绑定概念：「换绑 xxx」不触发换绑路由。"""
        hd, ddb = bot["handler"], bot["db"]
        ddb.add_tenant(APP_ID_2, "biz_1", "enc-dummy", role="commerce")
        router, s = _router(hd)
        tenant = {"tenant_key": "biz_1", "bind_douyin_id": "",
                  "role": "commerce"}
        _dispatch(router, "换绑 abc123", tenant)
        assert ddb.get_tenant("biz_1")["bind_douyin_id"] == ""


@pytest.fixture()
def admin_server(bot, monkeypatch):
    """真起一个 HTTP 服务打 tenant-create 端点（测的是完整请求链）。

    ⚠️ 测试基线 python（/usr/bin/python3）无 pycryptodome（生产 .venv-daren 有）。
    缺 Crypto 时给 sys.modules 注入替身：端点的 校验/查重/入库/积分 全部真测，
    AES 加解密本身不在这层验。"""
    try:
        import Crypto  # noqa: F401
    except ImportError:
        import sys
        fake = SimpleNamespace(
            load_or_create_master_key=lambda: b"k" * 32,
            encrypt_secret=lambda mk, s: ("enc:" + s).encode(),
        )
        monkeypatch.setitem(sys.modules, "daren_bot.crypto_util", fake)
    import daren_bot.admin as adm
    importlib.reload(adm)
    state = {"ws": None}
    handler = adm.make_handler(state)
    srv = HTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    token = (bot["config"].DATA_DIR / "admin_token.txt").read_text().strip()
    base = f'http://127.0.0.1:{srv.server_address[1]}'
    yield base, token, bot["db"]
    srv.shutdown()


def _post(base, token, path, body):
    req = urlreq.Request(f"{base}{path}?token={token}",
                         data=json.dumps(body).encode(),
                         headers={"Content-Type": "application/json"})
    try:
        with urlreq.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except HTTPError as e:
        # HTTP/1.0 短连接下读 body 可能撞上服务端 close（ConnectionResetError），
        # 本测试只断状态码，读不到 body 不算失败
        try:
            body = e.read() or b"{}"
        except OSError:
            body = b"{}"
        return e.code, json.loads(body)


_GOOD = {"tenant_key": "daren_jiejie", "app_id": APP_ID_2,
         "app_secret": "s" * 20, "role": "daren", "douyin_id": "hlb725",
         "credits": 500}


class TestTenantCreate:
    def test_create_ok(self, admin_server):
        base, token, ddb = admin_server
        code, j = _post(base, token, "/admin/api/tenant-create", _GOOD)
        assert code == 200 and j["ok"], j
        t = ddb.get_tenant("daren_jiejie")
        assert t["role"] == "daren" and t["bind_douyin_id"] == "hlb725"
        assert t["app_id"] == APP_ID_2
        assert ddb.get_balance("daren_jiejie") == 500

    def test_create_custom_credits(self, admin_server):
        base, token, ddb = admin_server
        body = {**_GOOD, "tenant_key": "daren_b", "app_id": APP_ID_2.replace(
            "cli_b", "cli_c")}
        # 非法 app_id？不—— replace 后仍是 cli_c222…（合规形态）
        code, j = _post(base, token, "/admin/api/tenant-create",
                        {**body, "credits": 800})
        assert code == 200, j
        assert ddb.get_balance("daren_b") == 800

    def test_create_low_credits_adjusts_down(self, admin_server):
        """初始积分低于默认 500 ⇒ 回冲差额（grant 只收正数，直接 grant(负数)
        会抛 ValueError 且租户已建完 ⇒ 响应写不回 —— 2026-11-04 线上实测）。"""
        base, token, ddb = admin_server
        body = {**_GOOD, "tenant_key": "daren_low",
                "app_id": APP_ID_2.replace("cli_b", "cli_d")}
        code, j = _post(base, token, "/admin/api/tenant-create",
                        {**body, "credits": 300})
        assert code == 200 and j["ok"], j
        assert ddb.get_balance("daren_low") == 300

    def test_empty_key_autogenerated_from_app_id(self, admin_server):
        """老大 2026-11-05：助理标识（内部主键，达人不可见）留空 ⇒ 按 App ID 自动生成。"""
        base, token, ddb = admin_server
        app_id = APP_ID_2.replace("cli_b", "cli_e")
        body = {**_GOOD, "tenant_key": "", "app_id": app_id}
        code, j = _post(base, token, "/admin/api/tenant-create", body)
        assert code == 200 and j["ok"], j
        want = "t_" + app_id[-8:].lower()
        assert j["tenant_key"] == want, f"应自动生成 {want}"
        assert ddb.get_tenant(want), "自动生成的标识必须真的落库"

    def test_duplicate_key_rejected(self, admin_server):
        base, token, _ = admin_server
        code, j = _post(base, token, "/admin/api/tenant-create",
                        {**_GOOD, "tenant_key": TK})
        assert code == 400 and "已存在" in j["error"]

    def test_duplicate_app_id_rejected(self, admin_server):
        """add_tenant 是 ON CONFLICT(app_id) DO UPDATE——不拦 = 静默劫持已有助理。"""
        base, token, _ = admin_server
        code, j = _post(base, token, "/admin/api/tenant-create",
                        {**_GOOD, "tenant_key": "daren_x", "app_id": APP_ID})
        assert code == 400 and "占用" in j["error"]

    def test_bad_app_id_format(self, admin_server):
        base, token, _ = admin_server
        code, j = _post(base, token, "/admin/api/tenant-create",
                        {**_GOOD, "tenant_key": "daren_y",
                         "app_id": "not_an_app_id"})
        assert code == 400 and "App ID" in j["error"]

    def test_merchant_role_not_open(self, admin_server):
        base, token, _ = admin_server
        code, j = _post(base, token, "/admin/api/tenant-create",
                        {**_GOOD, "tenant_key": "daren_z", "role": "merchant"})
        assert code == 400 and "商家助理暂未开放" in j["error"]

    def test_bad_tenant_key(self, admin_server):
        base, token, _ = admin_server
        code, j = _post(base, token, "/admin/api/tenant-create",
                        {**_GOOD, "tenant_key": "Bad Key!"})
        assert code == 400

    def test_no_token_forbidden(self, admin_server):
        base, _, _ = admin_server
        code, _ = _post(base, "wrong-token", "/admin/api/tenant-create", _GOOD)
        assert code == 403
