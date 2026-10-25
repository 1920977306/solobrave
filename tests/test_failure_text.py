"""失败话术层 + 运营告警 + 登录态巡检（2026-10-25 老大反馈闭环）。

老大原话：
  「发现 bug，我发送商品链接，ai 无法从中识别出商品 id 进行查找商品数据，
    然后就是浏览器的掉了，我也不知道，就是需要我重新扫码登陆或者验证码登陆，我都不知道」

本文件锁住四件事：
1. **达人看到的是人话** —— 不是 `RuntimeError: 达人端采集账号登录态过期了，
   跟运营老师说一声扫码重登就好`。
   ⇒ 静态闸门：达人可见文案里不许出现异常类名 / 端口 / 内部路径 / 给运营看的指示 /
     我们的内部状态（登录态、扫码、采集器…）。
2. **登录态类错误不许回退** —— 回退链路查的是**团长端**（违反 AGENTS §8.1），
   更会把「达人端掉登录」掩盖掉。
3. **运营能被叫醒** —— 掉登录 ⇒ 告警（弹窗/日志/落盘），且**非**登录态失败不告警
   （恒真闸门自检：这条守卫必须能被反例打红）。
4. **巡检判定口径** —— 「浏览器没开」≠「掉登录」（两个浏览器互斥，§8.2）。
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from daren_bot import failure_text, login_watcher, ops_alert  # noqa: E402

TK = "tk_failtext_test"


# ─────────────────────────── 夹具 ───────────────────────────

@pytest.fixture()
def bot(monkeypatch):
    """daren_bot 的库指向 tmp（绝不碰 prod 库）。"""
    tmp = tempfile.mkdtemp(prefix='daren_failtext_bot_')
    from daren_bot import config, db
    monkeypatch.setattr(config, "DB_PATH", Path(tmp) / "daren_bot.db")
    monkeypatch.setattr(config, "OPS_ALERT_FILE", Path(tmp) / "ops_alerts.jsonl")
    db.init_db()
    db.grant(TK, 500)
    return {"db": db, "config": config}


class _FakeSender:
    def __init__(self):
        self.sent = []

    def send_text(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))

    @property
    def text(self):
        return "\n".join(t for _, t in self.sent)


def _capture_alerts(monkeypatch):
    """把 ops_alert.alert 换成记录器，返回记录列表。"""
    calls = []

    def fake_alert(title, body="", *, kind="ops", dedup_key=""):
        calls.append({"title": title, "body": body,
                      "kind": kind, "dedup_key": dedup_key})
        return True

    monkeypatch.setattr(ops_alert, "alert", fake_alert)
    return calls


# ─────────────────── 1. 达人话术静态闸门 ───────────────────

# ★ 达人可见文案里**绝对不许出现**的东西。每条都要能问「删了这行，哪个用例会红」。
BANNED_IN_USER_TEXT = [
    "RuntimeError", "NeedLogin", "ProductNotFound", "TimeoutError",
    "Exception", "Traceback",
    "127.0.0.1", "8095", "8092", "http://", "https://",
    "登录态", "扫码", "cookie", "浏览器",
    "采集器", "buyin", "巨量", "百应", "风控", "a_bogus",
    "跟运营老师说一声", "联系运营", "任务失败",
]


def _all_user_texts():
    out = []
    for table in (failure_text._USER_TEXT_PRODUCT,
                  failure_text._USER_TEXT_GENERIC):
        for key, txt in table.items():
            out.append((f"product_table:{key}" if table is failure_text._USER_TEXT_PRODUCT
                        else f"generic_table:{key}", txt))
    out.append(("fallback_product", failure_text._FALLBACK_PRODUCT))
    out.append(("fallback_generic", failure_text._FALLBACK_GENERIC))
    return out


@pytest.mark.parametrize("name,txt", _all_user_texts(),
                         ids=[n for n, _ in _all_user_texts()])
def test_user_text_has_no_internal_detail(name, txt):
    """达人可见文案不许泄漏内部细节 / 异常名 / 给运营看的指示。"""
    low = txt.lower()
    for bad in BANNED_IN_USER_TEXT:
        assert bad.lower() not in low, f"{name} 出现了内部细节 {bad!r}: {txt!r}"


@pytest.mark.parametrize("key,txt", list(failure_text._USER_TEXT_GENERIC.items()))
def test_generic_scene_does_not_mention_product(key, txt):
    """通用场景（采集对标号/周报/问答）不许出现「商品」「截图」——
    那是商品场景的话术，塞错场景就是驴唇不对马嘴。"""
    assert "商品" not in txt
    assert "截图" not in txt


def test_user_text_for_picks_the_right_scene():
    """★ `product=False` 必须真的选到通用话术（不只查词表内容，还得查**选择逻辑**）。

    ⚠️ 变异验证实测踩过两个坑：
      ① 光断言词表里有/没有「商品」是**不够**的 ——
         把 `table = _USER_TEXT_PRODUCT if product else _USER_TEXT_GENERIC`
         改成 `table = _USER_TEXT_PRODUCT`，词表断言照样全绿 ⇒ 恒真闸门。
      ② 用的异常必须**真的命中词表** —— 用一个归类成 'other' 的异常，
         `table.get('other', 兜底)` 根本走不到表里，改了选择逻辑也测不出来。
         ⇒ 这里逐个失败类型都验一遍。
    """
    cases = [
        RuntimeError("商品查询超时，过会儿再试"),          # → timeout
        RuntimeError("达人端采集账号登录态过期了"),        # → need_login
        RuntimeError("平台查不到这个商品（可能已下架）"),  # → not_found
        RuntimeError("采集器未启动或不可达"),              # → collector_down
    ]
    for exc in cases:
        generic = failure_text.user_text_for(exc, product=False)
        product = failure_text.user_text_for(exc, product=True)
        assert "商品" not in generic, f"通用场景混进商品话术：{generic}"
        assert "截图" not in generic, f"通用场景混进截图话术：{generic}"
        assert ("商品" in product or "截图" in product), \
            f"商品场景没有商品话术：{product}"
        assert generic != product


def test_product_scene_offers_screenshot_fallback():
    """商品场景必须给替代路径（发截图我照样能分析）—— 达人被卡住时还有路走。"""
    for key in ("need_login", "not_found", "collector_down", "timeout"):
        assert "截图" in failure_text._USER_TEXT_PRODUCT[key]


# ─────────────────── 2. 分类判定 ───────────────────

def test_classify_need_login():
    from daren_bot import executors
    exc = executors.NeedLogin("达人端采集账号登录态过期了，需要重新扫码登录",
                               side="达人端")
    assert failure_text.classify(exc) == "need_login"
    assert failure_text.is_login_failure(exc)
    # 消息特征也要认（不精确 import 也能判）
    assert failure_text.is_login_failure(
        RuntimeError("达人端采集账号登录态过期了，跟运营老师说一声扫码重登就好"))


def test_classify_other_kinds():
    from daren_bot import executors
    assert failure_text.classify(
        executors.ProductNotFound("平台查不到这个商品（可能已下架）")) == "not_found"
    assert failure_text.classify(
        RuntimeError("采集器未启动或不可达(http://127.0.0.1:8095)")) == "collector_down"
    assert failure_text.classify(
        RuntimeError("商品查询超时，过会儿再试")) == "timeout"
    assert failure_text.classify(ValueError("随便什么错")) == "other"
    assert not failure_text.is_login_failure(ValueError("随便什么错"))


def test_ops_text_keeps_side():
    """运营那条要能看出是达人端还是团长端掉登录（达人那条不泄漏）。"""
    from daren_bot import executors
    exc = executors.NeedLogin("x", side="团长端")
    assert "团长端" in failure_text.ops_text_for(exc)
    assert "团长端" not in failure_text.user_text_for(exc)


def test_ops_alert_body_tells_the_action():
    """★ 运营告警正文必须给出**一个动作**（老大要知道去干嘛）。"""
    from daren_bot import executors
    exc = executors.NeedLogin("达人端采集账号登录态过期了", side="达人端")
    body = failure_text.ops_alert_body(exc, context="任务 abc 失败")
    assert "扫码" in body
    assert "达人端" in body
    assert "abc" in body


# ─────────────────── 3. worker._fail 展示层 ───────────────────

def _worker_stub():
    class _Stub:
        def __init__(self):
            self.notified = []

        def _notify(self, tenant_key, text, chat_id=""):
            self.notified.append((tenant_key, text, chat_id))
    return _Stub()


def test_fail_sends_human_text_not_traceback(bot, monkeypatch):
    """达人收到的是人话，不是异常原文。"""
    from daren_bot import executors, worker
    alerts = _capture_alerts(monkeypatch)
    stub = _worker_stub()
    task = {"id": "t1", "tenant_key": TK, "type": "product",
            "credits_cost": 20, "chat_id": "oc_x"}
    exc = executors.NeedLogin("达人端采集账号登录态过期了，需要重新扫码登录",
                               side="达人端")
    worker.Worker._fail(stub, task, f"{type(exc).__name__}: {exc}", exc=exc)

    assert len(stub.notified) == 1
    text = stub.notified[0][1]
    assert "RuntimeError" not in text
    assert "NeedLogin" not in text
    assert "跟运营老师说一声" not in text
    assert "扫码" not in text          # 达人不需要知道要去扫码
    assert "20" in text and "积分" in text   # 退款说明还在
    # ★ 运营被叫醒了
    assert len(alerts) == 1
    assert "扫码" in alerts[0]["body"]


def test_fail_refunds_still_happens(bot, monkeypatch):
    """失败照样退积分（展示层改动不能把退款改没了）。"""
    from daren_bot import executors, worker
    _capture_alerts(monkeypatch)
    stub = _worker_stub()
    task = {"id": "t2", "tenant_key": TK, "type": "product",
            "credits_cost": 20, "chat_id": "oc_x"}
    before = bot["db"].get_balance(TK)
    worker.Worker._fail(stub, task, "boom", exc=executors.NeedLogin("x", side="达人端"))
    assert bot["db"].get_balance(TK) == before + 20
    row = bot["db"].get_task("t2") if hasattr(bot["db"], "get_task") else None
    assert row is None or row.get("status") == "failed"


def test_non_login_failure_does_not_alert_ops(bot, monkeypatch):
    """★ 恒真闸门自检：非登录态失败**不许**告警运营。
    （否则「有告警」这个守卫等于没有 —— 什么都报 = 什么都不是。）"""
    from daren_bot import worker
    alerts = _capture_alerts(monkeypatch)
    stub = _worker_stub()
    task = {"id": "t3", "tenant_key": TK, "type": "product",
            "credits_cost": 20, "chat_id": "oc_x"}
    worker.Worker._fail(stub, task, "RuntimeError: 商品查询超时", exc=RuntimeError("商品查询超时"))
    assert alerts == [], "非登录态失败不该惊动运营"


def test_fail_without_exception_still_human(bot, monkeypatch):
    """没有异常对象时（老路径）也不能把原始 reason 甩给达人。"""
    from daren_bot import worker
    _capture_alerts(monkeypatch)
    stub = _worker_stub()
    task = {"id": "t4", "tenant_key": TK, "type": "douyin",
            "credits_cost": 100, "chat_id": "oc_x"}
    worker.Worker._fail(stub, task, "RuntimeError: 达人端采集账号登录态过期了")
    text = stub.notified[0][1]
    assert "RuntimeError" not in text
    assert "扫码" not in text


def test_generic_scene_text_used_for_non_product_task(bot, monkeypatch):
    """采集对标号任务失败 ⇒ 不该出现「商品」「截图」。"""
    from daren_bot import worker
    _capture_alerts(monkeypatch)
    stub = _worker_stub()
    task = {"id": "t5", "tenant_key": TK, "type": "douyin",
            "credits_cost": 100, "chat_id": "oc_x"}
    worker.Worker._fail(stub, task, "RuntimeError: boom", exc=RuntimeError("boom"))
    text = stub.notified[0][1]
    assert "商品" not in text
    assert "截图" not in text


# ─────────────────── 4. 登录态巡检判定 + 边沿触发 ───────────────────

def test_classify_health_states():
    """判定口径（AGENTS §8：必须同时看 *_browser_up）。"""
    assert login_watcher.classify_health(None) == "unknown"
    assert login_watcher.classify_health({}) == "unknown"
    # 浏览器没开 = 切到团长端了（互斥），**不是**掉登录
    assert login_watcher.classify_health(
        {"ok": True, "daren_browser_up": False, "daren_logged_in": False}) == "unknown"
    assert login_watcher.classify_health(
        {"ok": True, "daren_browser_up": True, "daren_logged_in": False}) == "bad"
    assert login_watcher.classify_health(
        {"ok": True, "daren_browser_up": True, "daren_logged_in": True}) == "ok"


def test_watcher_thread_survives_startup(monkeypatch):
    """★ 巡检线程启动后不许秒退。

    ⚠️ 2026-10-25 真机重启实测踩到：`self._stop.is_set()` 写错了 ——
    `threading.Thread._stop` 是**方法**（老 API 残留），不是事件 ⇒
    线程一起来就 AttributeError 死掉 ⇒ **巡检能力静默消失**，
    而服务照跑、告警永不发生（只有日志里一条 traceback）。
    ⇒ 这条用真线程验「还活着」。
    """
    import time
    monkeypatch.setattr(login_watcher, "fetch_health",
                        lambda *a, **k: {"ok": True,
                                          "daren_browser_up": True,
                                          "daren_logged_in": True})
    w = login_watcher.LoginWatcher(interval=1)
    w.start()
    time.sleep(1.5)
    assert w.is_alive(), "巡检线程启动就死了（告警能力静默消失）"
    w.stop()
    w.join(timeout=3)


def test_watcher_alerts_only_on_edge_and_needs_two_samples(monkeypatch):
    """连续 2 次异常才认定；之后不重复报；恢复正常时报一声。"""
    alerts = _capture_alerts(monkeypatch)
    w = login_watcher.LoginWatcher(interval=999)
    bad = {"ok": True, "daren_browser_up": True, "daren_logged_in": False}
    good = {"ok": True, "daren_browser_up": True, "daren_logged_in": True}

    w.check_once(bad)                       # 第 1 次：还不认定
    assert alerts == []
    w.check_once(bad)                       # 第 2 次：认定 ⇒ 告警
    assert len(alerts) == 1
    assert alerts[0]["kind"] == "login_lost"
    w.check_once(bad)                       # 第 3 次：同一轮故障，不重复报
    assert len(alerts) == 1
    w.check_once(good)                      # 恢复 ⇒ 报一声
    assert len(alerts) == 2
    assert alerts[1]["kind"] == "login_recovered"


def test_watcher_never_alerts_when_browser_not_open(monkeypatch):
    """★ 浏览器没开（切到团长端）**不许**告警 —— 否则天天误报。"""
    alerts = _capture_alerts(monkeypatch)
    w = login_watcher.LoginWatcher(interval=999)
    idle = {"ok": True, "daren_browser_up": False, "daren_logged_in": False}
    for _ in range(5):
        w.check_once(idle)
    assert alerts == []


def test_watcher_unknown_does_not_reset_streak(monkeypatch):
    """unknown（采集器没起）不认定故障、不清零计数。"""
    alerts = _capture_alerts(monkeypatch)
    w = login_watcher.LoginWatcher(interval=999)
    bad = {"ok": True, "daren_browser_up": True, "daren_logged_in": False}
    w.check_once(bad)
    w.check_once(None)          # unknown
    assert alerts == []
    w.check_once(bad)           # 第 2 次异常 ⇒ 认定
    assert len(alerts) == 1


# ─────────────────── 5. 登录态类错误不许回退（§8.1） ───────────────────

def test_need_login_does_not_fallback_to_page_parsing(monkeypatch):
    """★ ID 通路掉登录 ⇒ **不许**回退到页面解析链路（那条查的是团长端）。

    2026-10-25 实测事故：达人端掉登录 → 抛错 → 被 `except RuntimeError` 吞掉
    回退 → 团长端也没登录 ⇒ 达人看到的却是「采集账号登录态过期了」，
    **真实原因（达人端掉登录）被掩盖**。
    """
    from daren_bot import executors
    called = []

    def _boom(pid):
        raise executors.NeedLogin("达人端采集账号登录态过期了，需要重新扫码登录",
                                   side="达人端")

    def _http_spy(method, url, payload=None, timeout=15):
        called.append((method, url))
        raise AssertionError(f"不该走到页面解析链路: {method} {url}")

    monkeypatch.setattr(executors, "collect_product_by_id", _boom)
    monkeypatch.setattr(executors, "_http_json", _http_spy)
    monkeypatch.setattr(executors, "_daren_collect_state", lambda: (True, True, False))

    with pytest.raises(executors.NeedLogin):
        executors.collect_product_by_url(
            "https://haohuo.jinritemai.com/ecommerce/trade/detail/index.html?id=3847842537083633879")
    assert called == [], f"登录态类错误回退了：{called}"


def test_product_not_found_does_not_fallback(monkeypatch):
    """平台查无此商品 ⇒ 也不许回退（回退也没用）。"""
    from daren_bot import executors
    called = []

    def _boom(pid):
        raise executors.ProductNotFound("平台查不到这个商品（可能已下架）")

    def _http_spy(method, url, payload=None, timeout=15):
        called.append((method, url))
        raise AssertionError("不该回退")

    monkeypatch.setattr(executors, "collect_product_by_id", _boom)
    monkeypatch.setattr(executors, "_http_json", _http_spy)
    monkeypatch.setattr(executors, "_daren_collect_state", lambda: (True, True, True))
    monkeypatch.setattr(executors, "_buyin_logged_in", lambda: (True, True, True))

    with pytest.raises(executors.ProductNotFound):
        executors.collect_product_by_url(
            "https://haohuo.jinritemai.com/ecommerce/trade/detail/index.html"
            "?id=3847842537083633879")
    assert called == []


def test_daren_channel_state_judgement(monkeypatch):
    """前置预检的判定口径：只有「浏览器开着 + 没登录」才是 logged_out。"""
    from daren_bot import executors

    def _health(payload):
        monkeypatch.setattr(executors, "_http_json",
                            lambda *a, **k: payload)

    _health({"ok": True, "daren_browser_up": True, "daren_logged_in": False})
    assert executors.daren_channel_state() == "logged_out"
    _health({"ok": True, "daren_browser_up": True, "daren_logged_in": True})
    assert executors.daren_channel_state() == "ready"
    _health({"ok": True, "daren_browser_up": False, "daren_logged_in": False})
    assert executors.daren_channel_state() == "unknown"

    def _boom(*a, **k):
        raise OSError("down")

    monkeypatch.setattr(executors, "_http_json", _boom)
    assert executors.daren_channel_state() == "unknown"


# ─────────────────── 6. handler 前置预检 ───────────────────

def test_product_route_precheck_blocks_when_logged_out(bot, monkeypatch):
    """达人端确实掉登录 ⇒ 直接回人话、**不入队不扣分**，并告警运营。"""
    from daren_bot import executors, handler
    alerts = _capture_alerts(monkeypatch)
    enqueued = []
    monkeypatch.setattr(executors, "daren_channel_state", lambda: "logged_out")
    monkeypatch.setattr(bot["db"], "enqueue_task_charged",
                        lambda *a, **k: enqueued.append(a))
    sender = _FakeSender()
    router = handler.Router(None)
    tenant = {"tenant_key": TK, "app_id": "x", "bind_douyin_id": ""}

    router._route_product(tenant, "oc_x",
                           "https://haohuo.jinritemai.com/x?id=3847842537083633879",
                           sender, "链接")

    assert enqueued == [], "掉登录时不该入队（更不该扣分）"
    assert sender.sent, "达人应该收到一条说明"
    text = sender.text
    assert "RuntimeError" not in text
    assert "扫码" not in text
    assert "没有扣积分" in text
    assert len(alerts) == 1


def test_product_route_precheck_passes_when_ready(bot, monkeypatch):
    """通道正常 ⇒ 照常入队（预检不能把正常链路堵死）。"""
    from daren_bot import executors, handler
    _capture_alerts(monkeypatch)
    seen = []
    real = bot["db"].enqueue_task_charged

    def _spy(*a, **k):
        seen.append(a)
        return real(*a, **k)

    monkeypatch.setattr(executors, "daren_channel_state", lambda: "ready")
    monkeypatch.setattr(bot["db"], "enqueue_task_charged", _spy)
    sender = _FakeSender()
    router = handler.Router(None)
    tenant = {"tenant_key": TK, "app_id": "x", "bind_douyin_id": ""}

    router._route_product(tenant, "oc_x",
                           "https://haohuo.jinritemai.com/x?id=3847842537083633879",
                           sender, "链接")
    assert seen, "正常通道应该照常入队"


# ─────────────────── 7. 告警出口本身 ───────────────────

def test_alert_writes_file_and_never_raises(bot, monkeypatch):
    """告警落盘 + 永不让异常冒到主链路。"""
    monkeypatch.setattr(ops_alert, "_mac_notify", lambda t, b: False)
    monkeypatch.setattr(ops_alert, "_feishu_notify", lambda t, b: False)
    ok = ops_alert.alert("测试标题", "测试正文", kind="login_lost")
    assert ok
    recs = ops_alert.recent(5)
    assert recs and recs[-1]["title"] == "测试标题"


def test_alert_survives_channel_failure(bot, monkeypatch):
    """★ 告警通道全挂也不能把异常冒到主链路（告警失败 ≠ 采集失败）。

    告警是**旁路**：它挂了，采集/问答链路必须照跑。
    """
    def _boom(*a, **k):
        raise OSError("channel down")

    monkeypatch.setattr(ops_alert, "_append_file", _boom)
    monkeypatch.setattr(ops_alert, "_mac_notify", _boom)
    monkeypatch.setattr(ops_alert, "_feishu_notify", _boom)
    ops_alert.alert("t", "b")          # 不许抛


def test_alert_dedup_suppresses_same_key(bot, monkeypatch):
    """同一个 key 短时间内不重复告警（一次掉登录可能连着好几个达人踩）。"""
    monkeypatch.setattr(ops_alert, "_mac_notify", lambda t, b: False)
    monkeypatch.setattr(ops_alert, "_feishu_notify", lambda t, b: False)
    ops_alert._last_alert_at.clear()
    assert ops_alert.alert("A", "b", dedup_key="k1")
    assert not ops_alert.alert("A", "b", dedup_key="k1")
    assert ops_alert.alert("B", "b", dedup_key="k2")
