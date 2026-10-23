"""
知识库入库分档（老大 2026-10-23 口径）单测

覆盖：
  1. 档位判定（分析类 / 平常类）
  2. 档 A：分析类 ⇒ 完全提取（原文一字不改，不截断、不摘要）
  3. 档 B：平常类 ⇒ 整理后入库（碎片归并 → 抽要素 → 结构化）
  4. 「只丢不造」：整理不产生原文没有的信息；纯碎片不入库
  5. tier/source 落库 + API 透出（声明端 ≠ 使用端的老坑：不落库就不可查）
  6. 回滚开关：daily_kb_intake=false ⇒ 档 B 退回旧行为

运行: python3 -m pytest tests/test_knowledge_intake.py -q
"""
import os
import sys
import sqlite3
import importlib.util

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import knowledge_intake as ki


# ═══════════════════════════════════════════════════════════════════════
# 1. 档位判定
# ═══════════════════════════════════════════════════════════════════════
class TestClassifyTier:
    def test_analysis_structural_signal(self):
        """成型结论（评级/核心结论结构）⇒ 档 A"""
        text = '【达人评级】：C\n【一句话核心结论】：粉丝基础薄弱，带货能力有限，建议谨慎合作。'
        tier, reason = ki.classify_tier(text)
        assert tier == ki.TIER_ANALYSIS
        assert '成型结论' in reason

    def test_analysis_conclusion_phrase(self):
        """长文本 + 指标 + 判断 ⇒ 档 A"""
        text = ('该达人近30天场均结算额 12.5 万，粉丝价格带集中在 50-100 元，'
                '建议优先推家清类目，转化率表现稳定。' * 3)
        tier, _ = ki.classify_tier(text)
        assert tier == ki.TIER_ANALYSIS

    def test_daily_fragments(self):
        """碎片素材 ⇒ 档 B"""
        for text in ('名称：大丸子', '达人ID：tal_1787213095215_841db3',
                     '现状：这是一个专注于服装品类的达人，粉丝画像数据完整。'):
            tier, _ = ki.classify_tier(text)
            assert tier == ki.TIER_DAILY, f'应判为平常类: {text}'

    def test_empty_is_legacy(self):
        tier, _ = ki.classify_tier('')
        assert tier == ki.TIER_LEGACY

    def test_reason_is_explainable(self):
        """判定必须可解释 —— reason 随内容变化，便于事后审计"""
        _, r1 = ki.classify_tier('名称：大丸子')
        _, r2 = ki.classify_tier('【达人评级】：C')
        assert r1 != r2


# ═══════════════════════════════════════════════════════════════════════
# 2. 档 B 整理
# ═══════════════════════════════════════════════════════════════════════
class TestOrganizeDaily:
    def test_kv_becomes_elements(self):
        r = ki.organize_daily('名称：大丸子\n粉丝数：222000\n品类：服装')
        assert r is not None
        assert '粉丝数' in r['content']
        assert '222000' in r['content']
        assert r['facts']

    def test_identity_alone_not_entry(self):
        """只有身份标识没有实质事实 ⇒ 不入库（宁可漏，不可造垃圾条目）"""
        assert ki.organize_daily('名称：大丸子') is None

    def test_pure_id_dropped(self):
        """达人ID 是不可读 ID ⇒ 剔除；但留痕不静默丢"""
        r = ki.organize_daily('达人ID：tal_1787213095215_841db3\n粉丝数：222000')
        assert r is not None
        assert 'tal_1787213095215_841db3' not in r['content']
        assert any('tal_' in d for d in r['dropped'])

    def test_all_noise_returns_none(self):
        """全是噪音 ⇒ 整理失败（不硬凑空壳）"""
        assert ki.organize_daily('达人ID：tal_xxx') is None
        assert ki.organize_daily('') is None
        assert ki.organize_daily('   \n  \n') is None

    def test_subject_in_title(self):
        """有主体 ⇒ 标题带主体（不是退化成品类名）"""
        r = ki.organize_daily('名称：大丸子\n粉丝数：222000\n品类：服装')
        assert '大丸子' in r['title']

    def test_no_fabrication(self):
        """只丢不造：整理结果里的每个值都必须在原文出现过"""
        src = '名称：大丸子\n粉丝数：222000\n品类：服装\n合作等级：L4'
        r = ki.organize_daily(src)
        assert r is not None
        for _k, v in r['facts']:
            assert v in src, f'造出了原文没有的值: {v}'

    def test_long_text_returns_none(self):
        """超长 ⇒ 不是碎片 ⇒ 交回完整提取路径（不由整理层处理）"""
        r = ki.organize_daily('要点：' + '内容' * 3000)
        assert r is None

    def test_dedupe_identical_lines(self):
        r = ki.organize_daily('粉丝数：222000\n粉丝数：222000')
        assert r is not None
        assert len(r['facts']) == 1


class TestMergeDailyFragments:
    def test_same_subject_merged(self):
        """同主题碎片 ⇒ 归并成一条"""
        items = [
            {'content': '名称：大丸子\n粉丝数：222000', 'mem_id': 'm1'},
            {'content': '名称：大丸子\n品类：服装', 'mem_id': 'm2'},
        ]
        out = ki.merge_daily_fragments(items)
        assert len(out) == 1
        assert '大丸子' in out[0]['title']
        assert '粉丝数' in out[0]['content']
        assert '品类' in out[0]['content']
        assert sorted(out[0]['mem_ids']) == ['m1', 'm2']

    def test_different_subject_not_merged(self):
        """不同主题 ⇒ 分开（宁可多条，不硬凑一大坨）"""
        items = [
            {'content': '名称：大丸子\n粉丝数：222000', 'mem_id': 'm1'},
            {'content': '名称：七月姐姐\n粉丝数：17000', 'mem_id': 'm2'},
        ]
        out = ki.merge_daily_fragments(items)
        assert len(out) == 2

    def test_empty_input(self):
        assert ki.merge_daily_fragments([]) == []
        assert ki.merge_daily_fragments([{'content': '达人ID：tal_x'}]) == []


# ═══════════════════════════════════════════════════════════════════════
# 3. server 侧接线（tier/source 落库 + 档位分流）
# ═══════════════════════════════════════════════════════════════════════
@pytest.fixture(scope='module')
def srv():
    """加载 solobrave-server 模块（DB 指向临时库，绝不碰生产库）"""
    tmp_db = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_tmp_kb_intake.db')
    if os.path.exists(tmp_db):
        os.remove(tmp_db)
    spec = importlib.util.spec_from_file_location('solobrave_server_kb', os.path.join(ROOT, 'solobrave-server.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def _conn():
        c = sqlite3.connect(tmp_db)
        c.row_factory = sqlite3.Row
        return c

    mod.DB_PATH = tmp_db
    mod._db_conn = _conn
    yield mod
    if os.path.exists(tmp_db):
        os.remove(tmp_db)


class TestServerIntake:
    def test_upsert_persists_tier_and_source(self, srv):
        """★ 声明端 ≠ 使用端的老坑：tier/source 以前只在调用方字典里写、从不落库。
        本次必须真落库，否则老大两档口径不可查、不可验。"""
        conn = srv._db_conn()
        try:
            conn.execute('''CREATE TABLE IF NOT EXISTS kb_entries (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
                category TEXT DEFAULT '', category_id INTEGER, project_id TEXT DEFAULT '',
                scope TEXT DEFAULT 'global', team_id TEXT DEFAULT '', group_ids TEXT DEFAULT '[]',
                emp_id TEXT DEFAULT '', status TEXT DEFAULT 'ok', chunk_count INTEGER DEFAULT 0,
                created_by TEXT DEFAULT '', created_at INTEGER, updated_at INTEGER,
                domain TEXT NOT NULL DEFAULT 'tuangzhang',
                tier TEXT DEFAULT 'legacy', source TEXT DEFAULT 'legacy')''')
            conn.commit()
        finally:
            conn.close()

        kb_id = srv._upsert_knowledge_base({
            'title': '测试条目',
            'content': '测试内容',
            'tier': ki.TIER_ANALYSIS,
            'source': ki.SOURCE_ANALYSIS,
        }, user_id='auto:test')

        conn = srv._db_conn()
        try:
            row = conn.execute('SELECT tier, source FROM kb_entries WHERE id=?', (kb_id,)).fetchone()
        finally:
            conn.close()
        assert row['tier'] == ki.TIER_ANALYSIS
        assert row['source'] == ki.SOURCE_ANALYSIS

    def test_upsert_legacy_default(self, srv):
        """不传 tier/source ⇒ 落 legacy（不假装是某一档）"""
        conn = srv._db_conn()
        try:
            conn.execute('''CREATE TABLE IF NOT EXISTS kb_entries (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
                category TEXT DEFAULT '', category_id INTEGER, project_id TEXT DEFAULT '',
                scope TEXT DEFAULT 'global', team_id TEXT DEFAULT '', group_ids TEXT DEFAULT '[]',
                emp_id TEXT DEFAULT '', status TEXT DEFAULT 'ok', chunk_count INTEGER DEFAULT 0,
                created_by TEXT DEFAULT '', created_at INTEGER, updated_at INTEGER,
                domain TEXT NOT NULL DEFAULT 'tuangzhang',
                tier TEXT DEFAULT 'legacy', source TEXT DEFAULT 'legacy')''')
            conn.commit()
        finally:
            conn.close()
        kb_id = srv._upsert_knowledge_base({'title': 'x', 'content': 'y'}, user_id='auto:test')
        conn = srv._db_conn()
        try:
            row = conn.execute('SELECT tier, source FROM kb_entries WHERE id=?', (kb_id,)).fetchone()
        finally:
            conn.close()
        assert row['tier'] == 'legacy'
        assert row['source'] == 'legacy'

    def test_auto_check_analysis_full_extraction(self, srv):
        """档 A：分析类 ⇒ 正文一字不改（完全提取，不截断不摘要）"""
        calls = []
        orig = srv._upsert_knowledge_base
        srv._upsert_knowledge_base = lambda kb, user_id='': (calls.append(kb) or 'kb_test_1')
        orig_low = srv._is_low_quality_knowledge
        orig_recent = srv._is_recent_saved_analysis
        srv._is_low_quality_knowledge = lambda *a, **k: False
        srv._is_recent_saved_analysis = lambda *a, **k: False
        try:
            analysis = ('【达人评级】：B\n【一句话核心结论】：粉丝 52.8 万，转化率 3.1%。\n'
                        '合作建议：优先推家清类目。\n' + '细节行：' + 'x' * 500)
            srv._auto_check_knowledge('emp_test', 'mem_1', analysis)
        finally:
            srv._upsert_knowledge_base = orig
            srv._is_low_quality_knowledge = orig_low
            srv._is_recent_saved_analysis = orig_recent

        assert len(calls) == 1
        assert calls[0]['content'] == analysis, '分析类正文必须与原文完全一致'
        assert calls[0]['tier'] == ki.TIER_ANALYSIS

    def test_auto_check_daily_organized(self, srv):
        """档 B：平常类 ⇒ 整理后入库（不是照抄原文）"""
        calls = []
        orig = srv._upsert_knowledge_base
        srv._upsert_knowledge_base = lambda kb, user_id='': (calls.append(kb) or 'kb_test_2')
        orig_low = srv._is_low_quality_knowledge
        orig_recent = srv._is_recent_saved_analysis
        srv._is_low_quality_knowledge = lambda *a, **k: False
        srv._is_recent_saved_analysis = lambda *a, **k: False
        try:
            srv._auto_check_knowledge('emp_test', 'mem_2',
                                      '名称：大丸子\n粉丝数：222000\n品类：服装')
        finally:
            srv._upsert_knowledge_base = orig
            srv._is_low_quality_knowledge = orig_low
            srv._is_recent_saved_analysis = orig_recent

        assert len(calls) == 1
        assert calls[0]['tier'] == ki.TIER_DAILY
        assert calls[0]['source'] == ki.SOURCE_DAILY
        assert '粉丝数' in calls[0]['content']
        assert '大丸子' in calls[0]['title']

    def test_auto_check_daily_pure_noise_skipped(self, srv):
        """档 B：整理不出结构 ⇒ 不入库（不硬凑垃圾条目）"""
        calls = []
        orig = srv._upsert_knowledge_base
        srv._upsert_knowledge_base = lambda kb, user_id='': (calls.append(kb) or 'kb_test_3')
        orig_low = srv._is_low_quality_knowledge
        orig_recent = srv._is_recent_saved_analysis
        srv._is_low_quality_knowledge = lambda *a, **k: False
        srv._is_recent_saved_analysis = lambda *a, **k: False
        try:
            srv._auto_check_knowledge('emp_test', 'mem_3', '达人ID：tal_1787213095215_841db3')
        finally:
            srv._upsert_knowledge_base = orig
            srv._is_low_quality_knowledge = orig_low
            srv._is_recent_saved_analysis = orig_recent
        assert calls == [], '纯 ID 碎片不应入库'

    def test_rollback_switch_keeps_old_behavior(self, srv):
        """回滚点：daily_kb_intake=false ⇒ 档 B 退回旧行为（照抄原文 + 知识点：标题）"""
        calls = []
        orig = srv._upsert_knowledge_base
        srv._upsert_knowledge_base = lambda kb, user_id='': (calls.append(kb) or 'kb_test_4')
        orig_low = srv._is_low_quality_knowledge
        orig_recent = srv._is_recent_saved_analysis
        orig_enable = srv._daily_kb_intake_enabled
        srv._is_low_quality_knowledge = lambda *a, **k: False
        srv._is_recent_saved_analysis = lambda *a, **k: False
        srv._daily_kb_intake_enabled = lambda: False
        try:
            srv._auto_check_knowledge('emp_test', 'mem_4', '名称：大丸子\n粉丝数：222000')
        finally:
            srv._upsert_knowledge_base = orig
            srv._is_low_quality_knowledge = orig_low
            srv._is_recent_saved_analysis = orig_recent
            srv._daily_kb_intake_enabled = orig_enable

        assert len(calls) == 1
        assert calls[0]['title'].startswith('知识点：')
        assert '名称：大丸子' in calls[0]['content']


class TestRowSerializer:
    def test_tier_source_exposed(self):
        """API 层必须透出 tier/source，否则前端/验收看不到两档"""
        import knowledge_service as ks
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT 'kb_1' id, 'T' title, 'C' content, '' category, "
                           "NULL category_id, '' project_id, 'global' scope, '' team_id, "
                           "'[]' group_ids, '' emp_id, 'ok' status, 0 chunk_count, "
                           "'' created_by, 1 created_at, 2 updated_at, "
                           "'analysis' tier, 'auto_analysis' source").fetchone()
        d = ks._kb_entry_row_to_dict(row)
        assert d['tier'] == 'analysis'
        assert d['source'] == 'auto_analysis'
