# -*- coding: utf-8 -*-
"""P1-⑥ 症状1守卫：聊天正文泄漏 tool-call JSON / provider 协议标记（fix/chat-leak-provider-switch-json）。

现行证据 notif_37b075fdf2c7（2026-11-13 21:49 老大实机）正文形态：
    ]<]minimax[>[（正在查询系统数据…）
    {"name": "keeper_ops", "arguments": {"agent": "keeper"}}
    （正在查询系统数据…）

两类泄漏：①provider 切换协议标记（网关产物，消费端剥成空串）；
②JSON 形态 tool-call 块（旧 sanitizer 只认 exec(command=/tool_call( 字面形态）。

另锁历史坑：_strip_fake_tool_calls 在 solobrave-server.py 有两个同名 def
（死版本 + 活跃版本，后者遮蔽前者）——守卫断言「活跃版本必须带新形态
处理」，防止有人改到死版本上自以为修了（§8.6 同名遮蔽同族事故）。
"""
import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).parent.parent
SERVER = ROOT / 'solobrave-server.py'

_spec = importlib.util.spec_from_file_location('solobrave_server_chatleak', SERVER)
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)

# notif_37b075fdf2c7 原文（证据锚点，勿改写）
_LEAK_SAMPLE = (
    ']<]minimax[>[（正在查询系统数据…）\n'
    '{"name": "keeper_ops", "arguments": {"agent": "keeper"}}\n'
    '（正在查询系统数据…）'
)


def test_leak_sample_fully_stripped():
    out = srv._strip_fake_tool_calls(_LEAK_SAMPLE)
    assert ']<]' not in out, f'协议标记残留: {out!r}'
    assert '{"name"' not in out, f'JSON tool-call 块残留: {out!r}'
    # 所有演戏文本收敛为单一占位，不留空白壳
    assert out == '（正在查询系统数据…）', f'占位合并异常: {out!r}'


def test_protocol_marker_alone_stripped():
    out = srv._strip_fake_tool_calls(']<]minimax[>[好的，查到了')
    assert ']<]' not in out
    assert '好的' in out


def test_json_block_inline_nested():
    out = srv._strip_fake_tool_calls(
        '前缀 {"name": "keeper_ops", "arguments": {"agent": "keeper", "x": {"y": 1}}} 后缀')
    assert '{"name"' not in out
    assert '前缀' in out and '后缀' in out


def test_normal_reply_untouched():
    normal = '今天广场扫了 4 个新达人，深看 5 人，明细如上。'
    assert srv._strip_fake_tool_calls(normal) == normal


def test_legacy_exec_form_still_stripped():
    """旧形态回归保护：exec(command= 与 <tool_call> 形态仍被剥（不能越修越漏）。"""
    out = srv._strip_fake_tool_calls('exec(command="curl http://x")')
    assert 'exec(command' not in out
    out2 = srv._strip_fake_tool_calls('<tool_call>exec(cmd="ls")')
    assert '<tool_call>' not in out2


def test_active_def_has_new_handlers():
    """同名遮蔽锁：AST 取最后一个（=活跃）def，其源码必须含新形态处理；
    若有人在死版本上加守卫而活跃版没带，此断言立即红。"""
    tree = ast.parse(SERVER.read_text(encoding='utf-8'))
    defs = [n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == '_strip_fake_tool_calls']
    assert len(defs) >= 1, 'sanitizer 函数不存在'
    active = max(defs, key=lambda n: n.lineno)
    src = ast.get_source_segment(SERVER.read_text(encoding='utf-8'), active)
    assert '_FAKE_PROVIDER_SWITCH_RE' in src, '活跃版 sanitizer 缺协议标记处理（改到死版本了？）'
    assert '_FAKE_TOOLCALL_JSON_INLINE_RE' in src, '活跃版 sanitizer 缺 JSON 块处理（改到死版本了？）'


# ── P1-⑩ 守卫（2026-11-18）：整条=占位 → 纠错重试 → 明示话术，问题不悬空 ──

def test_p1x_retry_logic_present():
    """守卫①：ChatPOST API 代理路径含 FakeToolRetry 纠错链（源码断言）。"""
    src = SERVER.read_text(encoding='utf-8')
    assert '[FakeToolRetry]' in src
    assert '整条回复=占位' in src
    # CollectTakeover 同款口径（31314 行模式：工具调用是错的，答不了就明说）
    assert '上一条输出工具调用是错的' in src
    assert '没有工具运行时' in src
    # 重试仍只有占位 → 明示话术落库（不悬空）
    assert '该查询需要我在工作区跑体检脚本' in src
    # 重试结果再过一遍 sanitizer（防重试回泄漏形态）
    seg = src.split("[FakeToolRetry] {agent_id} 整条回复=占位", 1)[1]
    assert seg.count('_strip_fake_tool_calls(_ft_cleaned)') == 1


def test_p1x_sanitizer_untouched():
    """守卫②（回归）：P1-⑩ 不动 sanitizer 本体——症状1 全部既有断言保持。"""
    out = srv._strip_fake_tool_calls(_LEAK_SAMPLE)
    assert out == '（正在查询系统数据…）'
    # 281 字级正常长答不受任何影响（正常回答路径不误伤的 sanitizer 层基线）
    long_answer = ('今天广场扫了 4 个新达人，深看 5 人，排除 18 人。' * 8)[:281]
    assert srv._strip_fake_tool_calls(long_answer) == long_answer
