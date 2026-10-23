"""L0 记忆回填 assistant_content（solobrave-server.py `_backfill_l0_assistant`）的测试。

★ 为什么要有这个（2026-10-23 实测）：
  代理链在**转发请求之前**就把 L0 落库了（`save_conversation`），
  那时还没拿到 LLM 回复 ⇒ 只能写 `user_content`，
  `assistant_content` 用了默认值 `''`。
  ⇒ 实测 Ray 的 **316 条飞书对话 `assistant_content` 100% 为空**：
  **知识库 L0 只有「问」没有「答」**，
  而 L1 事实原子提取依赖问答对 ⇒ 提炼不出结论，记忆召回也喂不完整上下文。

⇒ 现在拿到上游响应后按 (agent_id, session_id, turn_id) 回填。
"""
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "solobrave_server", ROOT / "solobrave-server.py")
srv = importlib.util.module_from_spec(spec)
sys.modules["solobrave_server"] = srv
spec.loader.exec_module(srv)


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    """造一个最小 memory_conversations 表，并把 _db_conn 指向它。"""
    path = tmp_path / "t.db"
    c = sqlite3.connect(str(path))
    c.row_factory = sqlite3.Row
    c.execute("""
        CREATE TABLE memory_conversations (
            id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, session_id TEXT NOT NULL,
            turn_id INTEGER NOT NULL, user_content TEXT NOT NULL,
            assistant_content TEXT DEFAULT '', tool_calls TEXT DEFAULT '[]',
            created_at TEXT DEFAULT '', metadata TEXT DEFAULT '{}')
    """)
    c.execute(
        "INSERT INTO memory_conversations "
        "(id, agent_id, session_id, turn_id, user_content, assistant_content) "
        "VALUES ('c1','emp_a','emp_a',111,'问：哪个商品好','')")
    c.commit()

    monkeypatch.setattr(srv, "_db_conn", lambda: sqlite3.connect(str(path)))
    yield c
    c.close()


def _row(c, rowid="c1"):
    return c.execute(
        "SELECT assistant_content FROM memory_conversations WHERE id=?", (rowid,)).fetchone()[0]


# ───────────── 正常回填 ─────────────

def test_backfill_writes_assistant_content(conn):
    ok = srv._backfill_l0_assistant("emp_a", "emp_a", 111, "答：编织沙丘骑士靴，GMV 158 万")
    assert ok is True
    conn.commit()
    assert "编织沙丘骑士靴" in _row(conn)


def test_backfill_uses_agent_id_as_session_id_by_default(conn):
    """代理链落库时 session_id=agent_id ⇒ 回填必须用同一个键。"""
    srv._backfill_l0_assistant("emp_a", "emp_a", 111, "答")
    conn.commit()
    assert _row(conn) == "答"


# ───────────── 不能误伤已有值 ─────────────

def test_backfill_does_not_overwrite_existing_reply(conn):
    conn.execute("UPDATE memory_conversations SET assistant_content='原有回复' WHERE id='c1'")
    conn.commit()
    ok = srv._backfill_l0_assistant("emp_a", "emp_a", 111, "新回复")
    assert ok is False          # 没匹配到「空回复」的行
    conn.commit()
    assert _row(conn) == "原有回复"


def test_backfill_wrong_turn_id_does_not_hit(conn):
    ok = srv._backfill_l0_assistant("emp_a", "emp_a", 999, "答")
    assert ok is False
    conn.commit()
    assert _row(conn) == ""


def test_backfill_wrong_agent_does_not_hit(conn):
    ok = srv._backfill_l0_assistant("emp_zzz", "emp_zzz", 111, "答")
    assert ok is False
    conn.commit()
    assert _row(conn) == ""


# ───────────── 边界：不崩、不写空 ─────────────

@pytest.mark.parametrize("agent_id,session_id,turn_id,text", [
    ("", "emp_a", 111, "答"),
    ("emp_a", "", 111, "答"),
    ("emp_a", "emp_a", 0, "答"),
    ("emp_a", "emp_a", 111, ""),
    ("emp_a", "emp_a", 111, "   "),
    ("emp_a", "emp_a", 111, None),
])
def test_backfill_rejects_empty_inputs(conn, agent_id, session_id, turn_id, text):
    assert srv._backfill_l0_assistant(agent_id, session_id, turn_id, text) is False
    conn.commit()
    assert _row(conn) == ""


def test_backfill_never_raises_on_db_error(monkeypatch):
    """记忆沉淀是 best-effort ⇒ DB 挂了也**不许**把主链路带崩。"""
    def _boom():
        raise sqlite3.OperationalError("db gone")
    monkeypatch.setattr(srv, "_db_conn", _boom)
    assert srv._backfill_l0_assistant("emp_a", "emp_a", 111, "答") is False


# ───────────── 响应文本提取：兼容 OpenAI / Anthropic 两种形态 ─────────────

def test_extract_openai_shape():
    d = {"choices": [{"message": {"role": "assistant", "content": "答：编织沙丘骑士靴"}}]}
    assert srv._extract_assistant_text_any(d) == "答：编织沙丘骑士靴"


def test_extract_openai_shape_text_fallback():
    d = {"choices": [{"message": {"role": "assistant", "text": "旧字段"}}]}
    assert srv._extract_assistant_text_any(d) == "旧字段"


def test_extract_anthropic_shape():
    """★ 降级到 MiniMax 后返回的就是 Anthropic 形态 —— 必须能抠出文本。"""
    d = {"type": "message", "role": "assistant",
         "content": [{"type": "text", "text": "好"}]}
    assert srv._extract_assistant_text_any(d) == "好"


def test_extract_anthropic_shape_multiple_blocks():
    d = {"type": "message",
         "content": [{"type": "text", "text": "前半"}, {"type": "text", "text": "后半"}]}
    assert srv._extract_assistant_text_any(d) == "前半后半"


def test_extract_anthropic_content_string():
    d = {"type": "message", "content": "纯字符串"}
    assert srv._extract_assistant_text_any(d) == "纯字符串"


def test_extract_ignores_non_text_blocks():
    d = {"type": "message", "content": [{"type": "tool_use", "id": "x"}]}
    assert srv._extract_assistant_text_any(d) == ""


def test_extract_empty_and_garbage():
    assert srv._extract_assistant_text_any({}) == ""
    assert srv._extract_assistant_text_any(None) == ""
    assert srv._extract_assistant_text_any("不是 dict") == ""
    assert srv._extract_assistant_text_any({"choices": []}) == ""


def test_backfill_with_anthropic_response_end_to_end(conn):
    """端到端：Anthropic 形态的响应 ⇒ 真能写进 L0。"""
    resp = {"type": "message", "role": "assistant",
            "content": [{"type": "text", "text": "答：编织沙丘骑士靴 GMV 158 万"}]}
    txt = srv._extract_assistant_text_any(resp)
    assert srv._backfill_l0_assistant("emp_a", "emp_a", 111, txt) is True
    conn.commit()
    assert "158 万" in _row(conn)


# ───────────── SSE parser：流式也要能攒出 assistant 文本 ─────────────

def test_anthropic_sse_parser_accumulates_text():
    """流式路径的知识库回填靠 parser 攒文本 —— 之前 parser 只抽 usage 不抽文本。"""
    import provider_adapters as pa
    sse = pa.anthropic_sse_from_text('你好，这是流式回复', 'm', input_tokens=1, output_tokens=2)
    p = pa.AnthropicSSEParser()
    third = len(sse) // 3
    p.feed(sse[:third])
    p.feed(sse[third:third * 2])
    p.feed(sse[third * 2:])
    assert p.text == '你好，这是流式回复'


def test_openai_sse_parser_accumulates_text():
    import provider_adapters as pa
    sse = pa.openai_sse_from_text('openai 流式回复', 'm', input_tokens=1, output_tokens=2)
    op = pa.OpenAISSEParser()
    op.feed(sse)
    assert op.text == 'openai 流式回复'


def test_sse_parser_text_is_empty_without_events():
    import provider_adapters as pa
    p = pa.AnthropicSSEParser()
    p.feed(b'data: {broken json\n\n')
    assert p.text == ''
    assert p.parse_errors == 1
