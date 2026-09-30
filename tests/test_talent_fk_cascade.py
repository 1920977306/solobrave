# -*- coding: utf-8 -*-
"""
talents.id 外键约束单测 (fix/talent-id-prefix-r2)

覆盖:
  - _add_talent_foreign_keys (solobrave-server.py L4737 附近)
    1) idempotent: 第二次调用 no-op
    2) 3 个表全部加 FK (product_talent_match / talent_follow_ups / deals)
    3) FK 引用 talents.id ON DELETE CASCADE
    4) PRAGMA foreign_keys=ON 后 DELETE talent 真的 cascade 删从属
    5) 孤儿引用 (INSERT talent_id 引用不存在的 id) 被拒

策略:
  - 用 ast.extract + compile + exec 把函数注入到隔离 namespace
  - mock _db_conn (用 sqlite3 内存库 + PRAGMA foreign_keys=ON)
  - 不 import 整个 solobrave-server (避免 socket 启动 + douyin_parser 等副作用)
"""
import ast
import os
import sqlite3
import sys
import unittest

_SERVER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'solobrave-server.py'
)
with open(_SERVER_PATH, 'r', encoding='utf-8') as f:
    _server_src = f.read()
_server_tree = ast.parse(_server_src)


def _extract_func(tree, name):
    """在 AST 里找函数定义,返回源码片段。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.unparse(node)
    raise RuntimeError(f'Function {name} not found')


_ADD_FK_SRC = _extract_func(_server_tree, '_add_talent_foreign_keys')


# module-level 注入到 globals,避免 class attribute 被 bound
_GLOBALS = {'sqlite3': sqlite3}
exec(_ADD_FK_SRC, _GLOBALS)
_add_fk = _GLOBALS['_add_talent_foreign_keys']


def _make_db_path():
    """每个 test 独立的临时 db 文件 (memory 不支持 PRAGMA 跨 conn 验证 cascade)."""
    import time
    return '/tmp/test_fk_' + str(int(time.time() * 1000000)) + '.db'


def _setup_schema(conn):
    """创建 talents + 3 个引用表的初始 schema (无 FK) — 列名/列数跟生产一致,
    因为 _add_fk() recreate 后会用生产 schema, 测试 INSERT 必须配套。"""
    conn.execute('''
        CREATE TABLE talents (
            id TEXT PRIMARY KEY,
            name TEXT DEFAULT '',
            created_at INTEGER DEFAULT 0
        )
    ''')
    conn.execute('''
        CREATE TABLE product_talent_match (
            id TEXT PRIMARY KEY,
            product_id TEXT NOT NULL,
            talent_id TEXT NOT NULL,
            match_score REAL DEFAULT 0,
            match_reason TEXT DEFAULT '',
            sales_volume INTEGER DEFAULT 0,
            conversion_rate REAL DEFAULT 0,
            is_ai_recommended INTEGER DEFAULT 0,
            created_at INTEGER,
            updated_at INTEGER,
            UNIQUE(product_id, talent_id)
        )
    ''')
    conn.execute('''
        CREATE TABLE talent_follow_ups (
            id TEXT PRIMARY KEY,
            talent_id TEXT NOT NULL,
            follow_up_by TEXT DEFAULT '',
            follow_up_at INTEGER DEFAULT 0,
            next_follow_up_at INTEGER DEFAULT 0,
            content TEXT DEFAULT '',
            result TEXT DEFAULT '',
            status TEXT DEFAULT 'completed',
            created_at INTEGER,
            updated_at INTEGER
        )
    ''')
    conn.execute('''
        CREATE TABLE deals (
            id TEXT PRIMARY KEY,
            talent_id TEXT NOT NULL,
            product_id TEXT DEFAULT '',
            product_name TEXT DEFAULT '',
            deal_type TEXT DEFAULT '',
            commission_rate REAL DEFAULT 0,
            status TEXT DEFAULT 'pending',
            scheduled_at INTEGER DEFAULT 0,
            actual_gmv REAL DEFAULT 0,
            actual_roi REAL DEFAULT 0,
            actual_units INTEGER DEFAULT 0,
            result_note TEXT DEFAULT '',
            predicted_conclusion TEXT DEFAULT '',
            predicted_event_id TEXT DEFAULT '',
            verification TEXT DEFAULT '',
            created_by TEXT DEFAULT '',
            created_at INTEGER,
            updated_at INTEGER,
            win_loss_category TEXT DEFAULT '',
            key_moment TEXT DEFAULT '',
            decision_maker_feedback TEXT DEFAULT ''
        )
    ''')
    conn.commit()


class _FakeLogger:
    """absorbing logger: 不抛异常"""
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass
    def debug(self, msg): pass


class TestAddTalentForeignKeys(unittest.TestCase):
    """_add_talent_foreign_keys 行为契约"""

    @classmethod
    def setUpClass(cls):
        # 注入 logger 到函数 globals
        _add_fk.__globals__['logger'] = _FakeLogger()

    def setUp(self):
        self._db = _make_db_path()
        conn = sqlite3.connect(self._db)
        conn.execute('PRAGMA foreign_keys=ON')
        _setup_schema(conn)
        conn.close()
        # 给 _add_fk 注入 db_conn closure
        _add_fk.__globals__['_db_conn'] = lambda: self._open_conn()

    def tearDown(self):
        try:
            os.remove(self._db)
        except Exception:
            pass

    def _open_conn(self):
        c = sqlite3.connect(self._db)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        return c

    def _has_fk(self, table, ref_table='talents', ref_col='talent_id'):
        fks = self._open_conn().execute(f'PRAGMA foreign_key_list({table})').fetchall()
        # (id, seq, table, from, to, on_update, on_delete, match)
        return any(fk[2] == ref_table and fk[3] == ref_col for fk in fks)

    # ---- happy path ----

    def test_adds_fk_to_all_three_tables(self):
        m, s = _add_fk()
        self.assertEqual(m, 3)
        self.assertEqual(s, 0)
        self.assertTrue(self._has_fk('product_talent_match'))
        self.assertTrue(self._has_fk('talent_follow_ups'))
        self.assertTrue(self._has_fk('deals'))

    def test_fk_is_on_delete_cascade(self):
        _add_fk()
        for tbl in ['product_talent_match', 'talent_follow_ups', 'deals']:
            fks = self._open_conn().execute(f'PRAGMA foreign_key_list({tbl})').fetchall()
            talent_fks = [f for f in fks if f[2] == 'talents']
            self.assertEqual(len(talent_fks), 1,
                             f'{tbl} 应该有 1 个 talent FK, got {len(talent_fks)}')
            self.assertEqual(talent_fks[0][6], 'CASCADE',
                             f'{tbl}.talent_id FK 应该是 ON DELETE CASCADE, got {talent_fks[0][6]}')

    def test_idempotent_second_run_is_noop(self):
        _add_fk()
        m2, s2 = _add_fk()
        self.assertEqual(m2, 0)
        self.assertEqual(s2, 3)  # 3 张表已有 FK,跳过

    # ---- data preservation ----

    def test_existing_data_preserved_after_recreate(self):
        # 插入测试数据 — 用显式列名匹配生产 schema
        con = self._open_conn()
        con.execute("INSERT INTO talents (id, name) VALUES ('tal_a', 'A')")
        con.execute("INSERT INTO product_talent_match (id, product_id, talent_id) VALUES ('ptm_1', 'p_a', 'tal_a')")
        con.execute("INSERT INTO talent_follow_ups (id, talent_id) VALUES ('tfu_1', 'tal_a')")
        con.execute("INSERT INTO deals (id, talent_id) VALUES ('d_1', 'tal_a')")
        con.commit()
        con.close()

        _add_fk()

        con = self._open_conn()
        self.assertEqual(con.execute('SELECT COUNT(*) FROM talents').fetchone()[0], 1)
        self.assertEqual(con.execute('SELECT COUNT(*) FROM product_talent_match').fetchone()[0], 1)
        self.assertEqual(con.execute('SELECT COUNT(*) FROM talent_follow_ups').fetchone()[0], 1)
        self.assertEqual(con.execute('SELECT COUNT(*) FROM deals').fetchone()[0], 1)
        con.close()

    # ---- cascade behavior ----

    def test_delete_talent_cascades_to_follow_ups(self):
        _add_fk()
        con = self._open_conn()
        con.execute("INSERT INTO talents (id, name) VALUES ('tal_a', 'A')")
        con.execute("INSERT INTO talent_follow_ups (id, talent_id) VALUES ('tfu_1', 'tal_a')")
        con.commit()
        con.close()

        con = self._open_conn()
        con.execute('DELETE FROM talents WHERE id = ?', ('tal_a',))
        con.commit()
        self.assertEqual(con.execute('SELECT COUNT(*) FROM talent_follow_ups').fetchone()[0], 0,
                         'talent_follow_ups 应该 cascade 删')
        con.close()

    def test_delete_talent_cascades_to_deals(self):
        _add_fk()
        con = self._open_conn()
        con.execute("INSERT INTO talents (id, name) VALUES ('tal_a', 'A')")
        con.execute("INSERT INTO deals (id, talent_id) VALUES ('d_1', 'tal_a')")
        con.commit()
        con.close()

        con = self._open_conn()
        con.execute('DELETE FROM talents WHERE id = ?', ('tal_a',))
        con.commit()
        self.assertEqual(con.execute('SELECT COUNT(*) FROM deals').fetchone()[0], 0,
                         'deals 应该 cascade 删')
        con.close()

    def test_delete_talent_cascades_to_product_talent_match(self):
        _add_fk()
        con = self._open_conn()
        con.execute("INSERT INTO talents (id, name) VALUES ('tal_a', 'A')")
        con.execute("INSERT INTO product_talent_match (id, product_id, talent_id) VALUES ('ptm_1', 'p_a', 'tal_a')")
        con.commit()
        con.close()

        con = self._open_conn()
        con.execute('DELETE FROM talents WHERE id = ?', ('tal_a',))
        con.commit()
        self.assertEqual(con.execute('SELECT COUNT(*) FROM product_talent_match').fetchone()[0], 0,
                         'product_talent_match 应该 cascade 删')
        con.close()

    def test_orphan_reference_rejected_when_fk_on(self):
        _add_fk()
        con = self._open_conn()
        # FK 已加,PRAGMA foreign_keys=ON → 插入引用不存在 id 应该失败
        with self.assertRaises(sqlite3.IntegrityError):
            con.execute("INSERT INTO talent_follow_ups (id, talent_id) VALUES ('tfu_orphan', 'tal_nonexist')")
        con.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)