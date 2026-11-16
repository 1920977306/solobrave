# -*- coding: utf-8 -*-
"""P2-① 守卫：空壳 canonical 口径（tools/talent_shell.py）行为钉死。

锁三件事：
① is_shell 七信号逻辑（构造行逐信号翻转，必须全中才算空壳）；
② SQL 谓词与 Python 判定对 prod 全表一致（口径分叉即红——§8.6 铁律）；
③ keeper_ops 与 server 都消费 canonical（旧三信号口径不得复活）。
"""
import importlib.util
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent.parent

spec = importlib.util.spec_from_file_location('talent_shell', ROOT / 'tools' / 'talent_shell.py')
ts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ts)


def _shell_row():
    return {'douyin_id': '', 'platform_talent_id': '', 'followers': 0,
            'total_gmv': 0.0, 'monthly_settlement': '', 'contact_wechat': '',
            'contact_phone': '', 'wechat': '', 'phone': ''}


def test_all_signals_blank_is_shell():
    assert ts.is_shell(_shell_row()) is True


def test_each_signal_rescues_row():
    variants = [
        {'douyin_id': 'Wgg292'}, {'platform_talent_id': '372168571'},
        {'followers': 39000}, {'followers': '528000'},
        {'total_gmv': 1000.0}, {'monthly_settlement': '1万-2.5万'},
        {'contact_wechat': 'wx123'}, {'contact_phone': '138'},
        {'wechat': 'wx'}, {'phone': '139'},
    ]
    for v in variants:
        row = _shell_row()
        row.update(v)
        assert ts.is_shell(row) is False, f'信号 {v} 有值却被判空壳'


def test_null_and_text_zero_both_shell():
    row = _shell_row()
    row['followers'] = None
    row['total_gmv'] = '0'
    assert ts.is_shell(row) is True


def test_sql_matches_python_on_db_snapshot(test_data_dir):
    """SQL 谓词 vs Python 判定全表对拍——用 conftest 的 prod 快照副本（测试零 prod 接触铁律）。"""
    db = sqlite3.connect(os.path.join(test_data_dir, 'solobrave.db'))
    db.row_factory = sqlite3.Row
    cols = {r[1] for r in db.execute('PRAGMA table_info(talents)')}
    if 'platform_talent_id' not in cols:
        db.close()
        import pytest
        pytest.skip('干净模式 init_db 的 talents schema 无 platform_talent_id（老 schema），对拍跳过')
    sql_ids = {r[0] for r in db.execute(f'SELECT id FROM talents WHERE {ts.SHELL_SQL}')}
    py_ids = {r['id'] for r in db.execute('SELECT * FROM talents') if ts.is_shell(r)}
    db.close()
    assert sql_ids == py_ids, (f'SQL/Python 口径分叉: 仅SQL={sql_ids - py_ids} 仅PY={py_ids - sql_ids}')
    if not sql_ids:
        import pytest
        pytest.skip('快照无达人数据（干净 checkout 模式），对拍空转不判')


def test_keeper_uses_canonical():
    src = (ROOT / 'tools' / 'keeper_ops.py').read_text(encoding='utf-8')
    assert 'talent_shell.is_shell' in src, 'keeper 退回自写口径'
    assert 'monthly_settlement\')):\n            shells.append' not in src  # 旧三信号块不得复活


def test_server_consumes_canonical():
    src = (ROOT / 'solobrave-server.py').read_text(encoding='utf-8')
    assert "_load_talent_shell" in src and "SHELL_SQL" in src
    assert "shells = query.get('shells'" in src, '列表缺 shells 参数'
