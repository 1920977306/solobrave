# -*- coding: utf-8 -*-
"""
达人 ID 命名空间契约单测 (fix/talent-id-prefix)

覆盖:
  1) _generate_talent_id (solobrave-server.py L4677 附近)
     - 默认 now=None → 生成 tal_<timestamp_ms>_<uuid6>
     - 指定 now → 用传入时间戳 (毫秒)
     - 多次调用 → uuid 不重复
  2) _migrate_inf_to_tal_prefix
     - inf_<digits>_<hex> (created_at >= 1780000000000) → tal_
     - inf_<slug> (短 slug,无数字时间戳) → 跳过 (demo)
     - inf_<digits>_<hex> (created_at < 1780000000000) → 跳过 (demo)
     - tal_ 前缀 → 不动 (idempotent)
     - 同一 created_at 多次迁移 → 不重复 (新 id 唯一)

策略:
  - 用 ast.extract + compile + exec 把函数注入到隔离 namespace
  - mock _db_conn (用 sqlite3 内存库,提供 conn.execute 简单协议)
  - 不 import 整个 solobrave-server (避免 socket 启动 + douyin_parser 等副作用)
"""
import ast
import os
import re
import sqlite3
import sys
import time
import unittest
import uuid as _uuid

# ============================================================
# 1. 从 solobrave-server.py 抽出 _generate_talent_id 和 _migrate_inf_to_tal_prefix
# ============================================================
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
    raise RuntimeError(f'Function {name} not found in solobrave-server.py')


_GEN_FN_SRC = _extract_func(_server_tree, '_generate_talent_id')
_MIGRATE_FN_SRC = _extract_func(_server_tree, '_migrate_inf_to_tal_prefix')


# ============================================================
# 2. _generate_talent_id 单测 (无依赖,直接 exec)
# ============================================================
# ★ 把两个 helper 一起 exec 到同一个 namespace:
#   _migrate_inf_to_tal_prefix 内部调用 _generate_talent_id,
#   如果 exec 到不同 globals 会 NameError。
# ★ 挂到 module-level 而非 TestCase class attribute:
#   class attribute 在 instance 访问时会被 Python 3 绑定成 method,导致
#   `self._gen()` 实际变成 `_gen(self)`,污染第一个参数 now=。
_GLOBALS_HELPERS = {'time': time, 'uuid': _uuid}
exec(_GEN_FN_SRC, _GLOBALS_HELPERS)
exec(_MIGRATE_FN_SRC, _GLOBALS_HELPERS)
_gen = _GLOBALS_HELPERS['_generate_talent_id']
_migrate_fn = _GLOBALS_HELPERS['_migrate_inf_to_tal_prefix']


class TestGenerateTalentId(unittest.TestCase):
    """_generate_talent_id 返回值契约"""

    def test_default_now_returns_tal_prefix(self):
        out = _gen()
        self.assertTrue(out.startswith('tal_'),
                        f'expected tal_ prefix, got {out}')

    def test_default_now_format_is_tal_tsms_uuid6(self):
        out = _gen()
        # tal_<13-digit-ms-timestamp>_<6-hex-chars>
        m = re.fullmatch(r'tal_(\d{13})_([0-9a-f]{6})', out)
        self.assertIsNotNone(m, f'format mismatch: {out}')
        ts = int(m.group(1))
        # 时间戳合理性: 距今 < 60s
        now_ms = int(time.time() * 1000)
        self.assertLess(abs(now_ms - ts), 60_000,
                        f'timestamp drift > 60s: ts={ts}, now={now_ms}')

    def test_explicit_now_used_verbatim(self):
        out = _gen(now=1234567890123)
        expected_prefix = 'tal_1234567890123_'
        self.assertTrue(out.startswith(expected_prefix),
                        f'expected {expected_prefix}, got {out}')
        self.assertRegex(out, r'^tal_1234567890123_[0-9a-f]{6}$')

    def test_uuid_unique_across_calls(self):
        seen = set()
        for _ in range(100):
            seen.add(_gen())
        self.assertEqual(len(seen), 100,
                         'UUID should be unique across 100 calls')


# ============================================================
# 3. _migrate_inf_to_tal_prefix 单测 (mock _db_conn 用内存 SQLite)
# ============================================================
def _make_fake_conn(real_db_path):
    """Mock _db_conn: 返回一个真实 SQLite 连接,但所有 UPDATE 都会同步到测试 DB。"""
    def _fake_conn():
        c = sqlite3.connect(real_db_path)
        c.row_factory = sqlite3.Row
        return c
    return _fake_conn


class TestMigrateInfToTalPrefix(unittest.TestCase):
    """_migrate_inf_to_tal_prefix 行为契约"""

    @classmethod
    def setUpClass(cls):
        # 1. 创建测试 in-memory DB
        cls._tmp_db = '/tmp/test_migrate_inf_tal_' + str(int(time.time() * 1000)) + '.db'
        con = sqlite3.connect(cls._tmp_db)
        con.execute('''
            CREATE TABLE talents (
                id TEXT PRIMARY KEY,
                name TEXT,
                created_at INTEGER DEFAULT 0,
                created_by TEXT DEFAULT ''
            )
        ''')
        con.commit()
        con.close()

        # 2. 把 _generate_talent_id 注入到 _migrate_fn 的 globals
        #    (因为 exec 时 _migrate_fn 的 __globals__ 是它自己被 exec 时的 namespace,
        #     而我们 exec 时只注入了 _db_conn + logger,缺 _generate_talent_id)
        _migrate_fn.__globals__['_generate_talent_id'] = _gen
        _migrate_fn.__globals__['_db_conn'] = _make_fake_conn(cls._tmp_db)

        class _FakeLogger:
            def info(self, msg):
                pass

            def warning(self, msg):
                pass

        _migrate_fn.__globals__['logger'] = _FakeLogger()

    @classmethod
    def tearDownClass(cls):
        try:
            os.remove(cls._tmp_db)
        except Exception:
            pass

    def setUp(self):
        # 每个 case 前清空 DB
        con = sqlite3.connect(self._tmp_db)
        con.execute('DELETE FROM talents')
        con.commit()
        con.close()

    def _insert(self, tid, name='test', created_at=1787814190200):
        con = sqlite3.connect(self._tmp_db)
        con.execute(
            'INSERT INTO talents (id, name, created_at) VALUES (?,?,?)',
            (tid, name, created_at)
        )
        con.commit()
        con.close()

    def _ids(self):
        con = sqlite3.connect(self._tmp_db)
        rows = con.execute('SELECT id FROM talents ORDER BY id').fetchall()
        con.close()
        return [r[0] for r in rows]

    # ---- 真达人场景 ----

    def test_inf_real_talent_with_timestamp_migrated_to_tal(self):
        # 1787814190200 = 2026-09-25 之后 → 真达人
        self._insert('inf_1787814190200_6f57f0', '小晴姑姑', 1787814190200)
        migrated, skipped = _migrate_fn()
        self.assertEqual(migrated, 1)
        self.assertEqual(skipped, 0)
        ids = self._ids()
        self.assertEqual(len(ids), 1)
        self.assertTrue(ids[0].startswith('tal_'),
                        f'inf_ 真达人未迁移: {ids[0]}')
        # 迁移后保留原 created_at,新 id 形如 tal_<原时间戳>_<uuid6>
        self.assertRegex(ids[0], r'^tal_1787814190200_[0-9a-f]{6}$')

    def test_inf_real_talents_all_migrated(self):
        # 9 条真实录入 (created_at 1787... 系列)
        real_ids = [
            ('inf_1787816094249_6d979f', 1787816094249),
            ('inf_1787815157898_298b13', 1787815157898),
            ('inf_1787814190200_6f57f0', 1787814190200),
            ('inf_1787812620147_39495f', 1787812620147),
            ('inf_1787811322496_15423e', 1787811322496),
            ('inf_1787744125445_895446', 1787744125445),
            ('inf_1787744116171_4f8e2f', 1787744116171),
        ]
        for tid, ts in real_ids:
            self._insert(tid, 'real', ts)
        migrated, skipped = _migrate_fn()
        self.assertEqual(migrated, 7)
        self.assertEqual(skipped, 0)
        ids = self._ids()
        for old_id, ts in real_ids:
            new_id = f'tal_{ts}_'
            matches = [i for i in ids if i.startswith(new_id)]
            self.assertEqual(len(matches), 1,
                             f'原 {old_id} 应该被改成 {new_id}*, got {ids}')

    # ---- demo 场景 ----

    def test_inf_demo_short_slug_not_migrated(self):
        # inf_huahuac (短 slug,无数字时间戳) → demo,跳过
        self._insert('inf_huahuac', '花花穿搭', 1780243200000)
        migrated, skipped = _migrate_fn()
        self.assertEqual(migrated, 0)
        self.assertEqual(skipped, 1)
        self.assertEqual(self._ids(), ['inf_huahuac'])

    def test_inf_demo_old_timestamp_not_migrated(self):
        # inf_xxx 但 created_at 早于 1780000000000 → 视为 demo,跳过
        # 这是兜底: 即便有短 slug 时间戳格式,旧时间戳也保留
        self._insert('inf_1780000000000_aaaaaa', 'old', 1777564800000)
        migrated, skipped = _migrate_fn()
        self.assertEqual(migrated, 0)
        self.assertEqual(skipped, 1)
        self.assertEqual(self._ids(), ['inf_1780000000000_aaaaaa'])

    def test_all_demo_short_slugs_skipped(self):
        demos = ['inf_huahuac', 'inf_xiaomei', 'inf_akai', 'inf_sunny',
                 'inf_max', 'inf_limantou', 'inf_lisa', 'inf_laowang',
                 'inf_kev', 'inf_jane', 'inf_xiaoquexing']
        for d in demos:
            self._insert(d, 'demo', 0)
        migrated, skipped = _migrate_fn()
        self.assertEqual(migrated, 0)
        self.assertEqual(skipped, 11)
        self.assertEqual(sorted(self._ids()), sorted(demos))

    # ---- tal_ 不动 ----

    def test_existing_tal_unchanged(self):
        # tal_ 前缀 → 不动 (幂等)
        self._insert('tal_1786077969375_3c0d3f', 'already-tal', 1786077969375)
        migrated, skipped = _migrate_fn()
        self.assertEqual(migrated, 0)
        self.assertEqual(skipped, 0)
        self.assertEqual(self._ids(), ['tal_1786077969375_3c0d3f'])

    # ---- 混合场景 ----

    def test_mixed_real_and_demo_partial_migration(self):
        # 2 真达人 + 2 demo + 1 tal_ → 期望迁移 2 条
        self._insert('inf_1787814190200_a', 'real-A', 1787814190200)
        self._insert('inf_1787815157898_b', 'real-B', 1787815157898)
        self._insert('inf_huahuac', 'demo-A', 1780243200000)
        self._insert('inf_xiaomei', 'demo-B', 1780243200000)
        self._insert('tal_1786077969375_c', 'tal-C', 1786077969375)
        migrated, skipped = _migrate_fn()
        self.assertEqual(migrated, 2)
        self.assertEqual(skipped, 2)
        ids = self._ids()
        # 2 真达人 → tal_
        self.assertEqual(len([i for i in ids if i.startswith('tal_')]), 3)
        # 2 demo → 保留
        self.assertIn('inf_huahuac', ids)
        self.assertIn('inf_xiaomei', ids)

    def test_idempotent_second_call_no_op(self):
        # 第 1 次:迁移,第 2 次:无事可做
        self._insert('inf_1787814190200_a', 'real-A', 1787814190200)
        m1, s1 = _migrate_fn()
        self.assertEqual((m1, s1), (1, 0))
        m2, s2 = _migrate_fn()
        self.assertEqual((m2, s2), (0, 0))


if __name__ == '__main__':
    unittest.main(verbosity=2)
