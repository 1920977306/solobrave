"""Kimi 纯文本 fallback 的错误识别 —— **散文里的 error 不许当成错误**（2026-10-26）

起因（B 边第 2 轮实测反馈，A 边占用文件）：
  `solobrave-server.py::_extract_text_from_garbage()` 的关键词正则
  `(?:error|错误|exception|failed|timeout)\\s*[:：\\-]?\\s*(...)`
  **分隔符是可选的** ⇒ 散文里的普通单词会被误判成上游错误：

    'The model returned an error in the middle of the sentence.'
      → 旧: 'Kimi 服务提示: in the middle of the sentence.'   ← 误判
    'that is not an error but garbage data'
      → 旧: 'Kimi 服务提示: but garbage data'                   ← 误判

  ⇒ 误判代价：把**上游正常返回的内容**说成「Kimi 服务提示」
    ⇒ 达人看到的是「服务故障」，而上游其实好好的。

本文件锁住两件事：
1. **散文不许误判** —— 只有**结构化证据**（分隔符 / 行首锚定 / JSON 形态）才算错误。
2. **真错误仍要抓得到** —— 修了误判不能变成「什么错都抓不到」（漏判同样是缺陷）。

⚠️ 双向断言：每个用例都同时钉住「散文 → 非标准格式」和「真错误 → 服务提示」，
   防止有人为了消掉误判而把真错误也一起放过（那等于把闸门改成摆设）。
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(scope='module')
def srv():
    """加载 solobrave-server.py 模块（它不是包，只能按文件加载）。"""
    spec = importlib.util.spec_from_file_location('srv_garbage', ROOT / 'solobrave-server.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── 散文里的关键词 ⇒ 必须是「非标准格式」，不许说「服务提示」──────────────
@pytest.mark.parametrize('raw', [
    b'The model returned an error in the middle of the sentence.',
    b'The task failed to complete because of user input.',
    b'The request timeout value was set to 30 seconds.',
    b'that is not an error but garbage data',
    '这是一段散文，提到 error 这个词，但没有真的报错'.encode(),
    b'no exception was raised during the run',
])
def test_prose_keyword_is_not_treated_as_error(srv, raw):
    """散文里的 error/failed/timeout ⇒ 如实说「非标准格式」，不许编「服务提示」。"""
    out = srv._extract_text_from_garbage(raw)
    assert 'Kimi 服务提示' not in out, (
        f'散文里的关键词被误判成上游错误: {out!r}\n'
        f'⇒ 会让达人以为服务坏了，而上游其实正常返回了内容。')
    assert 'Kimi 返回非标准格式' in out


# ── 真错误 ⇒ 必须仍然是「服务提示」（防止为消误判而把真错误也放过）────────
@pytest.mark.parametrize('raw,expect', [
    (b'error: 502 Bad Gateway', '502 Bad Gateway'),
    (b'Exception: rate limit exceeded', 'rate limit exceeded'),
    ('错误: 请求超时'.encode(), '请求超时'),
    (b'failed to connect to upstream', 'to connect to upstream'),
    (b'{"error": {"message": "rate limited"}}', 'rate limited'),
    (b'<html><head><title>502 Bad Gateway</title></head></html>', 'HTTP 502'),
    (b'<html>503 Service Unavailable</html>', 'HTTP 503'),
])
def test_real_error_still_detected(srv, raw, expect):
    """真错误必须仍被识别（漏判和误判同样是缺陷）。"""
    out = srv._extract_text_from_garbage(raw)
    assert 'Kimi 服务提示' in out, f'真错误没被抓到: {out!r}'
    assert expect in out, f'错误内容丢了: {out!r}'


# ── 空响应 ────────────────────────────────────────────────────────────────
def test_empty_raw_gives_empty_notice(srv):
    assert '空响应' in srv._extract_text_from_garbage(b'')
    assert '空响应' in srv._extract_text_from_garbage(None)


def _code_only(src):
    """剥掉 docstring 与注释，**保留正则字面量**（判据形态就写在正则里）。

    ⚠️ 为什么不能直接扫全文：本函数的 docstring 里正写着
    「老写法 `\\s*[:：\\-]?\\s*`」用来解释历史坑 ⇒ 不剥离就会**误报**
    （静态扫描的经典假命中：注释里的示例被当成真代码）。
    ⚠️ 为什么不能用「清空所有字符串常量」：分隔符 `[:：\\-=]` 就在正则字符串里
    ⇒ 清空了连判据本身都扫不到 ⇒ 守卫变成「找不到就报错」的假闸门。
    """
    import re as _re
    lines = src.split('\n')
    code_lines = []
    in_doc = False
    doc_delim = None
    for line in lines:
        stripped = line.strip()
        if in_doc:
            if doc_delim in stripped:
                in_doc = False
            continue
        if stripped.startswith(('"""', "'''")):
            q = stripped[:3]
            if not (len(stripped) > 3 and stripped.endswith(q)):
                in_doc, doc_delim = True, q
            continue
        if stripped.startswith('#'):
            continue
        # 剥行尾注释（粗略：# 前是空白，且该行前半段没有引号）
        m_hash = _re.search(r"(?<!['\"])\s#", line)
        if m_hash and not _re.search(r"['\"]", line[:m_hash.start()]):
            line = line[:m_hash.start()]
        code_lines.append(line)
    return '\n'.join(code_lines)


# ── ★ 守门人自检：判据必须要求「结构化证据」，不能退回「关键词即错误」────
def test_guard_requires_structured_evidence_not_bare_keyword(srv):
    """★ 防回退：判据里不能出现「分隔符可选」的老写法。

    老写法：`\\s*[:：\\-]?\\s*` —— 分隔符带 `?` ⇒ 可选 ⇒ 散文误判。
    修法：分隔符**必需**（`[:：\\-=]`），另开行首锚定 + JSON 形态两条路。

    ⇒ 本守卫盯**源码里的正则形态**，不是盯行为（行为会被别的写法绕过）。
    """
    import inspect
    import re as _re
    code_only = _code_only(inspect.getsource(srv._extract_text_from_garbage))

    # ① 老写法（分隔符可选）不许回来
    assert not _re.search(r'\[:：\\-\]\?', code_only), (
        '关键词正则的分隔符又变回可选了（散文会误判）')
    # ② 必须存在「分隔符必需」的形态
    assert _re.search(r'\[:：\\-=\]', code_only), (
        '没找到「分隔符必需」的结构化判据 —— 判据被削弱了')
    # ③ 必须保留行首锚定（否则 'failed to connect' 这类无冒号的真错误会漏）
    assert '(?m)^' in code_only, (
        '行首锚定没了 ⇒ 无分隔符的真错误（如 "failed to connect"）会漏判')
    # ④ 必须保留 JSON 形态这条判据
    assert 'error|message|detail' in code_only, (
        'JSON 形态的错误判据没了 ⇒ {"error": {...}} 这类会漏判')
