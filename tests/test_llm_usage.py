"""llm_usage 守卫（优化菜单③ MiniMax 消耗统计）。

覆盖：
1. span contextmanager set/reset + 嵌套内层优先；
2. record_call 落行 + query 聚合正确（env LLM_USAGE_DB 指 tmp，零污染生产库）；
3. llm_channels 四个公开包装：成功/失败都记一行、site 来自 ContextVar、异常原样上抛；
4. ★ 旁路纪律：db 路径不可写时 record_call 不抛（统计挂了不影响主链路）。
"""

import sqlite3

import pytest

from daren_bot import llm_channels, llm_usage


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db = tmp_path / "llm_usage_test.db"
    monkeypatch.setenv("LLM_USAGE_DB", str(db))
    return db


def _rows(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT site, mode, vendor, pool, model, ok, err, prompt_chars,"
            " max_tokens FROM calls ORDER BY ts, rowid"
        ).fetchall()
    finally:
        conn.close()


def test_span_set_reset():
    assert llm_usage.current_site() == llm_usage.SITE_UNKNOWN
    with llm_usage.span("qa"):
        assert llm_usage.current_site() == "qa"
    assert llm_usage.current_site() == llm_usage.SITE_UNKNOWN


def test_span_nesting_inner_wins():
    with llm_usage.span("worker"):
        with llm_usage.span("worker_eval"):
            assert llm_usage.current_site() == "worker_eval"
        assert llm_usage.current_site() == "worker"


def test_span_reset_on_exception():
    with pytest.raises(ValueError):
        with llm_usage.span("qa"):
            raise ValueError("boom")
    assert llm_usage.current_site() == llm_usage.SITE_UNKNOWN


def test_record_and_query(tmp_db):
    ring = {"vendor": "minimax", "pool": "cp-subscription",
            "model": "MiniMax-M3", "max_tokens": 4096}
    llm_usage.record_call(site="qa", mode="prompt", ring=ring, ok=True,
                          latency_ms=123, prompt_chars=500, max_tokens=4096)
    llm_usage.record_call(site="qa", mode="prompt", ring=ring, ok=False,
                          latency_ms=50, err="http 429: too many", max_tokens=4096)
    llm_usage.record_call(site="worker_eval", mode="messages", ring=ring, ok=True,
                          latency_ms=999, prompt_chars=2000, max_tokens=8000)

    rows = _rows(tmp_db)
    assert len(rows) == 3
    assert rows[0][:6] == ("qa", "prompt", "minimax", "cp-subscription",
                           "MiniMax-M3", 1)
    assert rows[1][5] == 0 and "429" in rows[1][6]
    assert rows[2][0] == "worker_eval"

    data = llm_usage.query(days=7)
    assert data["totals"]["calls"] == 3
    assert data["totals"]["ok"] == 2
    assert data["totals"]["fail"] == 1
    assert data["totals"]["prompt_chars"] == 2500
    sites = {(r["day"], r["site"]): r["calls"] for r in data["by_day_site"]}
    assert sum(sites.values()) == 3
    models = {(m["vendor"], m["model"]): m["calls"] for m in data["by_model"]}
    assert models[("minimax", "MiniMax-M3")] == 3
    assert len(data["recent_failures"]) == 1
    assert data["recent_failures"][0]["site"] == "qa"


def test_query_days_clamped(tmp_db):
    data = llm_usage.query(days=999)
    assert data["totals"]["days"] == 30
    data = llm_usage.query(days=0)
    assert data["totals"]["days"] == 1


def test_wrapper_success_records(tmp_db, monkeypatch):
    monkeypatch.setattr(llm_channels, "_call_openai_raw", lambda *a, **k: "好的")
    ring = {"protocol": "openai", "vendor": "minimax", "pool": "p",
            "model": "M", "max_tokens": 100, "temp": 1.0, "base": "http://x",
            "key": "k"}
    with llm_usage.span("qa"):
        out = llm_channels.call_openai(ring, "你好", 0)
    assert out == "好的"
    rows = _rows(tmp_db)
    assert len(rows) == 1
    assert rows[0][0] == "qa" and rows[0][1] == "prompt" and rows[0][5] == 1
    assert rows[0][7] == 2  # prompt_chars


def test_wrapper_failure_records_and_reraises(tmp_db, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("http 402: insufficient balance")
    monkeypatch.setattr(llm_channels, "_call_openai_raw", _boom)
    ring = {"protocol": "openai", "vendor": "minimax", "pool": "p",
            "model": "M", "max_tokens": 100, "temp": 1.0, "base": "http://x",
            "key": "k"}
    with llm_usage.span("product_verdict"):
        with pytest.raises(RuntimeError, match="402"):
            llm_channels.call_openai(ring, "商品怎么样", 0)
    rows = _rows(tmp_db)
    assert len(rows) == 1
    assert rows[0][0] == "product_verdict" and rows[0][5] == 0
    assert "402" in rows[0][6]


def test_wrapper_no_span_records_unknown(tmp_db, monkeypatch):
    monkeypatch.setattr(llm_channels, "_call_openai_raw", lambda *a, **k: "x")
    ring = {"protocol": "openai", "vendor": "minimax", "pool": "p",
            "model": "M", "max_tokens": 100, "temp": 1.0, "base": "http://x",
            "key": "k"}
    llm_channels.call_openai(ring, "hi", 0)
    assert _rows(tmp_db)[0][0] == llm_usage.SITE_UNKNOWN


def test_messages_wrappers_record(tmp_db, monkeypatch):
    monkeypatch.setattr(llm_channels, "_call_openai_messages_raw",
                        lambda *a, **k: "ok1")
    monkeypatch.setattr(llm_channels, "_call_anthropic_messages_raw",
                        lambda *a, **k: "ok2")
    monkeypatch.setattr(llm_channels, "_call_anthropic_raw", lambda *a, **k: "ok3")
    ring = {"protocol": "openai", "vendor": "minimax", "pool": "p",
            "model": "M", "max_tokens": 100, "temp": 1.0, "base": "http://x",
            "key": "k"}
    msgs = [{"role": "system", "content": "系统"}, {"role": "user", "content": "问题"}]
    with llm_usage.span("worker_eval"):
        llm_channels.call_openai_messages(ring, msgs)
        llm_channels.call_anthropic_messages(ring, msgs)
    with llm_usage.span("qa"):
        llm_channels.call_anthropic(ring, "直接问")
    rows = _rows(tmp_db)
    assert [r[0] for r in rows] == ["worker_eval", "worker_eval", "qa"]
    assert rows[0][1] == "messages" and rows[0][7] == 4  # '系统'+'问题'
    assert rows[2][1] == "prompt" and rows[2][7] == 3


def test_executors_qa_llm_span_wiring(tmp_db, monkeypatch):
    """_qa_llm 环循环的 span 接线：默认 qa、显式 site 覆盖、落行 site 正确。"""
    from daren_bot import executors

    ring = {"protocol": "openai", "vendor": "minimax", "pool": "p",
            "model": "M", "max_tokens": 100, "temp": 1.0, "base": "http://x",
            "key": "k"}
    monkeypatch.setattr(executors, "_fallback_rings_daren", lambda *a, **k: [ring])
    # 只 mock 最底层 HTTP 实现（raw），让 llm_channels 包装层真实走埋点
    monkeypatch.setattr(llm_channels, "_call_openai_raw", lambda *a, **k: "回答")
    monkeypatch.setattr(llm_channels, "_call_anthropic_raw", lambda *a, **k: "回答")

    assert executors._qa_llm("你好") == "回答"
    assert executors._qa_llm("你好", site="product_verdict") == "回答"
    rows = _rows(tmp_db)
    assert [r[0] for r in rows] == ["qa", "product_verdict"]


def test_executors_verdict_passes_product_site(tmp_db, monkeypatch):
    """_llm_product_verdict 必须传 SITE_PRODUCT_VERDICT（商品结论单列统计）。"""
    from daren_bot import executors

    seen = {}

    def _spy(prompt, model="", temp=None, site=None):
        seen["site"] = site
        return "🟢 能带"

    monkeypatch.setattr(executors, "_talent_context", lambda tk: "画像")
    monkeypatch.setattr(executors, "_qa_llm", _spy)
    out = executors._llm_product_verdict("tk_x", {"name": "拖鞋", "price": "¥29.9"})
    assert "🟢 能带" in out
    assert seen["site"] == llm_usage.SITE_PRODUCT_VERDICT


def test_bypass_record_never_raises(tmp_path, monkeypatch):
    """★ 旁路纪律：db 不可写（路径是目录）时 record_call 不抛。"""
    blocker = tmp_path / "not_a_db"
    blocker.mkdir()
    monkeypatch.setenv("LLM_USAGE_DB", str(blocker / "x.db"))
    # 目录不可作为文件创建 ⇒ connect 必失败；record 必须吞掉
    llm_usage.record_call(site="qa", mode="prompt", ok=True, latency_ms=1)
    # 走到这里没抛即通过
    assert True


def test_bypass_query_raises_to_caller(tmp_path, monkeypatch):
    """query 不向调用方伪装成功：db 坏了要抛（server 层转 500，前端可见）。"""
    blocker = tmp_path / "blocker"
    blocker.write_text("我不是目录")
    monkeypatch.setenv("LLM_USAGE_DB", str(blocker / "x.db"))  # 父级是文件 ⇒ 必失败
    with pytest.raises(Exception):
        llm_usage.query(days=7)
