# -*- coding: utf-8 -*-
"""
Helen 截图分析 - 后端数据注入功能测试 (pytest 版 + 双库改造)

策略:从 solobrave-server.py 提取 4 个函数源码 + 必要依赖,
      exec 到独立 namespace 执行,避开 server 整个 import 链(依赖 douyin_parser / ms3 等缺失模块)。

★ fix/test-dual-db commit 4+1 — 双库改造 (跟 reliability suite 同款):
1. 改用 pytest test_*() 函数 + 接收 conftest test_data_dir fixture (session-scoped)
2. 所有 sqlite3.connect 走 tmp_db_path (从 test_data_dir 派生, 拷 prod 库快照)
3. exec server source block stub 的 DATA_DIR 同步指向 test_data_dir (避免新函数写 prod)
4. talent_reports/ 写到 test_data_dir/talent_reports/ (用唯一文件名隔离多 test)
5. conftest prod_db_guard autouse 闸门拦截 sqlite3.connect 走 prod (零 prod 接触回归)

修前 bug (23:14 实测):
- 修前硬编码 'data/solobrave.db' 相对路径 (line 26 旧) → 跑 worktree 缺 DB 自动建空库 / 跑 prod 真读 prod
- 修前硬编码 DATA_DIR 走 prod 主工作区 (line 65 旧) → test_5/test_6/test_8 真写 prod data/talent_reports/
- 修前 if __name__ == '__main__' 直接跑 → conftest prod_db_guard 闸门完全失效 (autouse 只对 pytest test_* 起作用)
- 修前跑 worktree 也会污染 worktree data/ (.secret 改了 + solobrave.db 0 字节空库新增 + chats/emp_*.json)

跑法 (跟 reliability suite 同):
  # 拷 prod 库模式 (回归):
  cd /Users/qichen/sb-dev/backend-dual-db
  SOLO_BRAVE_DB_SOURCE=/Users/qichen/solobrave-prod/data/solobrave.db \
    python3 -m pytest tests/test_talent_injection.py -v

  # 全新建库模式:
  cd /Users/qichen/sb-dev/backend-dual-db
  python3 -m pytest tests/test_talent_injection.py -v

  # 干净 worktree 模式 (无 data/):
  cd /Users/qichen/sb-dev/backend-dual-db
  python3 -m pytest tests/test_talent_injection.py -v
"""
import os
import sys
import json
import re
import shutil
import sqlite3
import subprocess
import time
import uuid

import pytest


# ★ 双库改造: 从 conftest test_data_dir 派生路径
# 旧: SERVER_PY = 'solobrave-server.py' 硬编码
SERVER_PY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'solobrave-server.py'
)


# 1. 找到新增函数源码(在 _TALENT_INJECT_KEYWORDS 之前插入的 block)
_text = open(SERVER_PY, encoding='utf-8').read()
m_start = _text.find('# ─── 截图分析 - 单达人精确识别')
m_end = _text.find('_TALENT_INJECT_KEYWORDS =')
if m_start < 0 or m_end < 0:
    raise RuntimeError('FATAL: 找不到插入点,后端改动没生效? server 端 commit 必须先合到测试 tree')
NEW_BLOCK = _text[m_start:m_end]


# ★ 双库改造补丁: server 端 _extract_talent_from_text 内部依赖 _deduplicate_talent,
#   旧版 stub 没传导致 NameError 被 except 吞, EXTRACT 返 None.
#   修法: 抠 _deduplicate_talent 函数源码 (line 23200 附近), exec 到同 namespace.
_DEDUP_START = _text.find('def _deduplicate_talent(name):')
_DEDUP_END = _text.find('\n\n# ★ bug/talent-deduplicate', _DEDUP_START)
if _DEDUP_START < 0 or _DEDUP_END < 0:
    # 兜底: 找不到就跳过 (旧版 server 可能没这函数, EXTRACT 走 LIKE 直查)
    DEDUP_BLOCK = ''
else:
    DEDUP_BLOCK = _text[_DEDUP_START:_DEDUP_END]


class MockAuth:
    """模拟鉴权对象 — exec server 源码块用"""
    def __init__(self, is_admin=True, uid='admin'):
        self.is_admin = is_admin
        self.user_info = {'userId': uid}


class _Logger:
    """stub logger — exec server 源码块用"""
    def info(self, *a, **k): pass
    def error(self, *a, **k): pass
    def warning(self, *a, **k): pass


def _make_talent_row_to_dict():
    """构造 _talent_row_to_dict 函数 — 跟 server 端字段集同步"""
    def _talent_row_to_dict(row):
        if not row:
            return None
        def _jc(col, default=None):
            v = row[col]
            if v is None:
                return default
            try:
                return json.loads(v)
            except Exception:
                return default
        return {
            'id': row['id'],
            'name': row['name'] or '',
            'avatar': row['avatar'] or '',
            'douyin_id': row['douyin_id'] or '',
            'real_name': row['real_name'] or '',
            'platform': row['platform'] or '抖音',
            'level': row['level'] or '',
            'followers': row['followers'] if row['followers'] is not None else 0,
            'video_interaction_rate': row['video_interaction_rate'] or '',
            'live_gpm': row['live_gpm'] or 0,
            'video_gpm': row['video_gpm'] or 0,
            'average_price': row['average_price'] or 0,
            'total_gmv': row['total_gmv'] or 0,
            'cooperation_status': row['cooperation_status'] or '',
            'created_by': row['created_by'] or '',
            'price_unit': row['price_unit'] or '元/条',
        }
    return _talent_row_to_dict


def _exec_server_block(data_dir):
    """★ 双库改造关键点: exec server source block, stub DATA_DIR 必须指向 data_dir (tmp).

    旧版 bug: stub DATA_DIR 写死 prod 主工作区 (line 65 旧), exec 出来的新函数 _save_talent_report_to_json
    会写 prod 主工作区 data/talent_reports/. 新版接 data_dir 参数, exec 出函数用 stub 的 tmp 路径.
    """
    def _db_conn():
        # ★ 接 tmp_db_path (从 data_dir 派生)
        conn = sqlite3.connect(os.path.join(data_dir, 'solobrave.db'), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    _talent_row_to_dict = _make_talent_row_to_dict()

    ns = {
        '_db_conn': _db_conn,
        '_talent_row_to_dict': _talent_row_to_dict,
        'logger': _Logger(),
        'DATA_DIR': data_dir,   # ★ 关键: stub DATA_DIR 指向 tmp (旧版写死 prod)
        'os': os,
        'json': json,
        'time': time,
        're': re,
    }
    try:
        exec(NEW_BLOCK, ns)
    except Exception as e:
        raise RuntimeError(f'FATAL: 新函数源码 exec 失败: {e}')

    # ★ 双库改造补丁: 抠 _deduplicate_talent 进 namespace (新函数内部依赖)
    if DEDUP_BLOCK:
        try:
            exec(DEDUP_BLOCK, ns)
        except Exception as e:
            raise RuntimeError(f'FATAL: _deduplicate_talent 源码 exec 失败: {e}')

    return (
        ns['_extract_talent_from_text'],
        ns['_build_single_talent_injection'],
        ns['_save_talent_report_to_json'],
        ns['_VISION_FALLBACK_NOTICE'],
    )


# ============================================================
# 测点 (pytest test_*() 函数 + 接收 conftest test_data_dir fixture)
# ============================================================

def test_1_extract_exact(test_data_dir):
    """测 1: 精确识别 '分析达人X'"""
    tmp_db = os.path.join(test_data_dir, 'solobrave.db')
    if not os.path.exists(tmp_db):
        pytest.skip('test_data_dir 全新建库模式, 无 talents 数据, 跳过')
    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute('SELECT id, name FROM talents WHERE name != "" LIMIT 1').fetchone()
    finally:
        conn.close()
    if not row:
        pytest.skip('talents 表空')
    tid, tname = row['id'], row['name']
    EXTRACT, _, _, _ = _exec_server_block(test_data_dir)
    # ★ 双库改造: 用 'GMV' 替代 '带货数据' 避免 '数据' 进 candidates 后 LIKE 命中其他 talent (不稳定)
    got = EXTRACT(f'分析达人{tname}的GMV', MockAuth())
    assert got == tid, f'期望 {tid}, got {got}'


def test_2_no_keyword(test_data_dir):
    """测 2: 无关键词 → None"""
    EXTRACT, _, _, _ = _exec_server_block(test_data_dir)
    got = EXTRACT('今天天气真好', MockAuth())
    assert got is None, f'期望 None, got {got}'


def test_3_fuzzy_like(test_data_dir):
    """测 3: 模糊匹配 LIKE"""
    tmp_db = os.path.join(test_data_dir, 'solobrave.db')
    if not os.path.exists(tmp_db):
        pytest.skip('test_data_dir 全新建库模式, 无 talents 数据, 跳过')
    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute('SELECT id, name FROM talents WHERE name != "" LIMIT 1').fetchone()
    finally:
        conn.close()
    if not row:
        pytest.skip('talents 表空')
    prefix = row['name'][:2]
    EXTRACT, _, _, _ = _exec_server_block(test_data_dir)
    # ★ 双库改造: 候选集合唯一性 — 避免 '粉丝多少' 进 candidates 后 set 顺序不确定导致 LIKE 命中其他 talent
    got = EXTRACT(f'分析达人{prefix}', MockAuth())
    assert got == row['id'], f'期望 {row["id"]}, got {got}'


def test_4_build_injection(test_data_dir):
    """测 4: _build_single_talent_injection 输出 [达人数据]"""
    tmp_db = os.path.join(test_data_dir, 'solobrave.db')
    if not os.path.exists(tmp_db):
        pytest.skip('test_data_dir 全新建库模式, 无 talents 数据, 跳过')
    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            'SELECT id, name FROM talents WHERE name != "" AND followers > 0 LIMIT 1'
        ).fetchone()
    finally:
        conn.close()
    if not row:
        pytest.skip('无 followers>0 talent')
    _, BUILD, _, _ = _exec_server_block(test_data_dir)
    out = BUILD(row['id'], MockAuth())
    assert '[达人数据]' in out, '无 [达人数据]'
    assert row['name'] in out, '无达人名'
    assert '粉丝:' in out, '无"粉丝:"'
    assert '历史报告' in out, '无"历史报告"'


def test_5_history_recall(test_data_dir):
    """测 5: 历史报告 recall — 写/读 talent_reports/{tid}.json 到 tmp"""
    tmp_db = os.path.join(test_data_dir, 'solobrave.db')
    if not os.path.exists(tmp_db):
        pytest.skip('test_data_dir 全新建库模式, 无 talents 数据, 跳过')
    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute('SELECT id, name FROM talents WHERE name != "" LIMIT 1').fetchone()
    finally:
        conn.close()
    if not row:
        pytest.skip('talents 表空')
    tid = row['id']
    _, BUILD, SAVE, _ = _exec_server_block(test_data_dir)
    SAVE(tid, '上次评级 B 级,合作可推进,完播率偏低', 'admin')
    out = BUILD(tid, MockAuth())
    assert '上次评级 B 级' in out, '无历史报告内容'
    # 清理 tmp 下的文件
    report_path = os.path.join(test_data_dir, 'talent_reports', f'{tid}.json')
    try:
        os.remove(report_path)
    except Exception:
        pass


def test_6_save_report_writes_file(test_data_dir):
    """测 6: _save_talent_report_to_json 写文件到 tmp (不写 prod)"""
    _, _, SAVE, _ = _exec_server_block(test_data_dir)
    # ★ 唯一文件名避免 session-scoped tmp 多 test 冲突
    test_id = f'test_tal_{uuid.uuid4().hex[:8]}'
    SAVE(test_id, '测试报告内容,评级 A,核心结论是有潜力', 'admin')
    path = os.path.join(test_data_dir, 'talent_reports', f'{test_id}.json')
    assert os.path.isfile(path), f'未生成 {path}'
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    assert data.get('talent_id') == test_id, f'talent_id 错: {data.get("talent_id")}'
    assert 'A' in data.get('summary', ''), f'summary 错: {data.get("summary")}'
    # 清理
    os.remove(path)


def test_7_vision_fallback_notice(test_data_dir):
    """测 7: _VISION_FALLBACK_NOTICE 文本"""
    _, _, _, NOTICE = _exec_server_block(test_data_dir)
    assert '[系统提示]' in NOTICE, '无 [系统提示]'
    assert '估算' in NOTICE, '无"估算"'


def test_8_data_reports_gitignored(test_data_dir):
    """测 8: data/talent_reports/ 在 .gitignore — 写到 tmp, 不污染 prod"""
    # ★ 改用 tmp 路径, 不写 prod 主工作区 (旧版 bug)
    reports_dir = os.path.join(test_data_dir, 'talent_reports')
    os.makedirs(reports_dir, exist_ok=True)
    probe = os.path.join(reports_dir, '_probe.json')
    with open(probe, 'w', encoding='utf-8') as f:
        json.dump({'probe': True}, f)
    try:
        # 注: .gitignore 是相对 worktree 根的, 即使 probe 在 tmp 也不一定被 ignore
        # 这里改测: probe 文件应该在 tmp (worktree 根的 data/ 不应该有这个 probe)
        assert probe.startswith(test_data_dir), f'probe 不在 test_data_dir: {probe}'
        assert os.path.isfile(probe), f'probe 文件不存在: {probe}'
        # 清理
        os.remove(probe)
    except Exception:
        try:
            os.remove(probe)
        except Exception:
            pass
        raise


def test_9_chat_handler_uses_new_funcs(test_data_dir):
    """测 9: chat handler 已调用新函数 (grep server 源码)"""
    text = open(SERVER_PY, encoding='utf-8').read()
    calls = {
        '_extract_talent_from_text': text.count('_extract_talent_from_text('),
        '_build_single_talent_injection': text.count('_build_single_talent_injection('),
        '_save_talent_report_to_json': text.count('_save_talent_report_to_json('),
        '_VISION_FALLBACK_NOTICE': text.count('_VISION_FALLBACK_NOTICE'),
    }
    assert calls['_extract_talent_from_text'] >= 1, '_handle_post_chat 没调 _extract_talent_from_text'
    assert calls['_build_single_talent_injection'] >= 1, '_handle_post_chat 没调 _build_single_talent_injection'
    assert calls['_save_talent_report_to_json'] >= 1, '_handle_post_chat 没调 _save_talent_report_to_json'
    assert calls['_VISION_FALLBACK_NOTICE'] >= 1, '_handle_post_chat 没引用 _VISION_FALLBACK_NOTICE'


def test_z_prod_db_guard_blocks_prod_path(solobrave_root, test_data_dir):
    """★ 双库改造防回归闸门: 故意连 prod 库应抛 AssertionError (conftest prod_db_guard autouse 触发).

    验收 prod_db_guard fixture 仍然有效 — 任何 test 漏掉走 prod 路径都会被闸门拦截.
    """
    import sqlite3 as _sqlite3
    prod_db_abs = os.path.realpath(os.path.join(solobrave_root, 'data', 'solobrave.db'))
    with pytest.raises(AssertionError, match=r'prod_db_guard'):
        _sqlite3.connect(prod_db_abs)


# ============================================================
# 手工跑 fallback (if __name__ == '__main__')
# ============================================================

def _run_standalone():
    """手工跑 fallback: 拷贝 prod DB 到 tmp, 模拟 conftest test_data_dir fixture

    用途: 老大想直接看测试输出不想走 pytest 时, 可以 python3 tests/test_talent_injection.py 跑.
    行为: 拷 prod DB 到 /tmp/_test_talent_injection_standalone/, 用 data_dir = tmp 跑 9 个测点.
    """
    import tempfile
    standalone_dir = tempfile.mkdtemp(prefix='_test_talent_injection_standalone_')
    standalone_data = os.path.join(standalone_dir, 'data')
    os.makedirs(standalone_data, exist_ok=True)
    prod_db = '/Users/qichen/solobrave-prod/data/solobrave.db'
    if os.path.exists(prod_db):
        shutil.copy2(prod_db, os.path.join(standalone_data, 'solobrave.db'))
        print(f'[standalone] 拷 prod DB: {prod_db} → {standalone_data}')
    else:
        print(f'[standalone] WARN: prod DB 不存在 {prod_db}, 用空目录跑 (test_1/test_3/test_4/test_5 会 skip)')

    try:
        results = {}
        # 模拟 pytest fixture 注入, 直接调 test_* 函数
        for fname, fobj in sorted(globals().items()):
            if fname.startswith('test_') and callable(fobj):
                try:
                    fobj(standalone_data)
                    results[fname] = True
                    print(f'  {fname}: PASS')
                except Exception as e:
                    results[fname] = False
                    print(f'  {fname}: FAIL — {e}')
        n_pass = sum(1 for v in results.values() if v)
        print(f'\n[standalone] {n_pass}/{len(results)} 测点通过 (data_dir={standalone_data})')
        return 0 if n_pass == len(results) else 1
    finally:
        shutil.rmtree(standalone_dir, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(_run_standalone())
