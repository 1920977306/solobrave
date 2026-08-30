# -*- coding: utf-8 -*-
"""单元测试：阶段4B-P0 —— 规律置信体系 / 三信号混合检索 / 事件重要度 / Deal 归因
1. 字段迁移：knowledge_patterns 5 个新列 + knowledge_events 2 个新列 + deals 3 个新列及默认值；
   存量 status=confirmed/rejected 规律重跑 init_db 幂等迁移 verification_level，再跑一次不被改
2. 规律等级注入：proven 带【高置信规律】、candidate 带【待更多验证】、hypothesis(draft)/deprecated
   不注入、按 confidence_score 降序只取 top2
3. _kp_can_promote 晋升门槛边界矩阵（恰好满足/差一点/跨级）
4. FTS/实体路召回：LIKE 路漏检的 query 通过实体路召回目标事件，干扰事件不进结果
5. RRF 融合：同一事件多路命中排在单路命中之前
6. _ke_compute_importance 启发式：首事件长文 8 / +predicted_match / +关联 deal 封顶 10 / 非首事件短文 4
7. Deal 归因：切 completed/failed 缺 win_loss_category -> 400；非法枚举 -> 400；
   合法写入返回三字段；key_moment 传了非空必须在枚举内
8. 降级：FTS 关闭仍返回其余路结果不抛异常；三路全失败返回 []；注入检索内部异常返回 ''
运行: python test_knowledge_4b_p0.py
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile
import time
import types

sys.stdout.reconfigure(encoding='utf-8')

spec = importlib.util.spec_from_file_location(
    'solobrave_server',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'solobrave-server.py'))
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)

tmpdir = tempfile.mkdtemp(prefix='sb_test_4bp0_')
srv.DB_PATH = os.path.join(tmpdir, 'test.db')
srv.get_embedding_config = lambda emp_id=None: {}
srv._load_agents = lambda: []

failures = 0


def check(label, ok, extra=''):
    global failures
    print(f'{label} {"OK" if ok else "FAIL " + extra}')
    failures += 0 if ok else 1


# 1. 字段迁移与默认值
srv.init_db()
conn = sqlite3.connect(srv.DB_PATH)
conn.row_factory = sqlite3.Row

kp_cols = {r['name'] for r in conn.execute('PRAGMA table_info(knowledge_patterns)').fetchall()}
check('1a. knowledge_patterns 5个新列存在',
      {'confidence_score', 'evidence_count', 'hit_count', 'miss_count', 'verification_level'} <= kp_cols,
      str(sorted(kp_cols)))
ke_cols = {r['name'] for r in conn.execute('PRAGMA table_info(knowledge_events)').fetchall()}
check('1b. knowledge_events 2个新列存在',
      {'importance_score', 'last_accessed_at'} <= ke_cols, str(sorted(ke_cols)))
deal_cols = {r['name'] for r in conn.execute('PRAGMA table_info(deals)').fetchall()}
check('1c. deals 3个新列存在',
      {'win_loss_category', 'key_moment', 'decision_maker_feedback'} <= deal_cols, str(sorted(deal_cols)))

fts_tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
check('1d. knowledge_events_fts 建表成功且标志可用',
      'knowledge_events_fts' in fts_tables and srv._KE_FTS_ENABLED is True)

conn.execute("INSERT INTO knowledge_patterns (id, category, pattern_text, created_at, updated_at) "
             "VALUES ('kp_default', '默认类', '默认值占位规律', 1, 1)")
conn.execute("INSERT INTO knowledge_events (id, content_full, created_at) VALUES ('ke_default', '默认占位', 1)")
conn.execute("INSERT INTO deals (id, talent_id) VALUES ('deal_default', 't_none')")
conn.commit()
r = conn.execute("SELECT * FROM knowledge_patterns WHERE id = 'kp_default'").fetchone()
check('1e. pattern 新列默认值正确',
      r['confidence_score'] == 50 and r['evidence_count'] == 0 and r['hit_count'] == 0
      and r['miss_count'] == 0 and r['verification_level'] == 'hypothesis',
      repr(dict(r)))
r = conn.execute("SELECT * FROM knowledge_events WHERE id = 'ke_default'").fetchone()
check('1f. event 新列默认值正确', r['importance_score'] == 5 and r['last_accessed_at'] == '', repr(dict(r)))
r = conn.execute("SELECT * FROM deals WHERE id = 'deal_default'").fetchone()
check('1g. deal 新列默认值正确',
      r['win_loss_category'] == '' and r['key_moment'] == '' and r['decision_maker_feedback'] == '',
      repr(dict(r)))

# 存量迁移：confirmed -> verified、rejected -> deprecated、draft 保持 hypothesis
conn.execute("INSERT INTO knowledge_patterns (id, category, entity_type, pattern_text, status, created_at, updated_at) "
             "VALUES ('kp_mig_c', 'zzz无关类目', '', '迁移用规律C', 'confirmed', 1, 1)")
conn.execute("INSERT INTO knowledge_patterns (id, category, entity_type, pattern_text, status, created_at, updated_at) "
             "VALUES ('kp_mig_r', 'zzz无关类目', '', '迁移用规律R', 'rejected', 1, 1)")
conn.execute("INSERT INTO knowledge_patterns (id, category, entity_type, pattern_text, status, created_at, updated_at) "
             "VALUES ('kp_mig_d', 'zzz无关类目', '', '迁移用规律D', 'draft', 1, 1)")
conn.commit()
conn.close()
srv.init_db()
conn = sqlite3.connect(srv.DB_PATH)
conn.row_factory = sqlite3.Row
levels = {r['id']: r['verification_level'] for r in conn.execute(
    "SELECT id, verification_level FROM knowledge_patterns WHERE id IN ('kp_mig_c','kp_mig_r','kp_mig_d')").fetchall()}
check('1h. 迁移映射 confirmed->verified / rejected->deprecated / draft 保持 hypothesis',
      levels.get('kp_mig_c') == 'verified' and levels.get('kp_mig_r') == 'deprecated'
      and levels.get('kp_mig_d') == 'hypothesis', repr(levels))
conn.execute("UPDATE knowledge_patterns SET evidence_count = 7 WHERE id = 'kp_mig_c'")
conn.commit()
conn.close()
srv.init_db()  # 再跑一次：已迁移的行不应被改动
conn = sqlite3.connect(srv.DB_PATH)
conn.row_factory = sqlite3.Row
r = conn.execute("SELECT verification_level, evidence_count FROM knowledge_patterns WHERE id = 'kp_mig_c'").fetchone()
check('1i. init_db 幂等：已迁移行不被二次修改',
      r['verification_level'] == 'verified' and r['evidence_count'] == 7, repr(dict(r)))

# 2. 规律等级映射与注入规则
now_ms = int(time.time() * 1000)
conn.execute("INSERT INTO talents (id, name, category, status, created_by, created_at, updated_at) "
             "VALUES ('t_xlm', '小鹿妈妈', '美妆', 'active', 'admin', ?, ?)", (now_ms, now_ms))
conn.execute("INSERT INTO products (id, name, status) VALUES ('p_jzb', '便携榨汁杯', 'active')")
patterns_seed = [
    ('kp_proven', '美妆', 'talent', 'PROVEN规律文本_高客单美妆需纯佣', 'confirmed', 92, 'proven'),
    ('kp_verified', '美妆', 'talent', 'VERIFIED规律文本_美妆短视频种草', 'confirmed', 80, 'verified'),
    ('kp_cand', '美妆', 'talent', 'CANDIDATE规律文本_新达人需先试播', 'confirmed', 60, 'candidate'),
    ('kp_hyp', '美妆', 'talent', 'HYPOTHESIS草稿规律不应注入', 'draft', 99, 'hypothesis'),
    ('kp_depr', '美妆', 'talent', 'DEPRECATED旧规律不应注入', 'deprecated', 95, 'deprecated'),
    ('kp_cand_p', '', 'product', '候选规律文本_低客单适合纯佣测试', 'confirmed', 60, 'candidate'),
]
for pid, cat, et, text, status, score, level in patterns_seed:
    conn.execute(
        "INSERT INTO knowledge_patterns (id, category, entity_type, pattern_text, status, "
        "confidence_score, verification_level, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1)", (pid, cat, et, text, status, score, level))
conn.commit()

ctx = srv._retrieve_knowledge_context('分析一下小鹿妈妈的合作价值', 'helen', None)
ctx = ctx or ''
check('2a. proven 规律注入且带【高置信规律】',
      '【历史规律参考】' in ctx and '【高置信规律】' in ctx and 'PROVEN规律文本' in ctx, repr(ctx[-300:]))
check('2b. verified 规律注入（无标注）', 'VERIFIED规律文本' in ctx)
check('2c. candidate 被 top2 截断不注入', 'CANDIDATE规律文本' not in ctx)
check('2d. hypothesis(draft)/deprecated 不注入',
      'HYPOTHESIS草稿规律' not in ctx and 'DEPRECATED旧规律' not in ctx)
check('2e. 注入顺序按 confidence_score 降序',
      ctx.index('PROVEN规律文本') < ctx.index('VERIFIED规律文本'))

ctx2 = srv._retrieve_knowledge_context('推荐便携榨汁杯的合作达人有哪些', 'helen', None) or ''
check('2f. candidate 规律注入且带【待更多验证】',
      '【待更多验证】' in ctx2 and '候选规律文本' in ctx2, repr(ctx2[-300:]))
check('2g. entity_type 不匹配的规律不注入', 'PROVEN规律文本' not in ctx2 and 'VERIFIED规律文本' not in ctx2)

# 3. _kp_can_promote 晋升门槛边界矩阵
cp = srv._kp_can_promote
check('3a. hypothesis->candidate 恰好满足(approved+证据3)', cp('hypothesis', 'candidate', 0, 3, approved=True) is True)
check('3b. hypothesis->candidate 未审核被拒', cp('hypothesis', 'candidate', 0, 3, approved=False) is False)
check('3c. hypothesis->candidate 证据差1被拒', cp('hypothesis', 'candidate', 0, 2, approved=True) is False)
check('3d. candidate->verified 恰好满足(70+10)', cp('candidate', 'verified', 70, 10) is True)
check('3e. candidate->verified 分数差一点被拒', cp('candidate', 'verified', 69.9, 10) is False)
check('3f. candidate->verified 证据差1被拒', cp('candidate', 'verified', 70, 9) is False)
check('3g. verified->proven 恰好满足(85+30)', cp('verified', 'proven', 85, 30) is True)
check('3h. verified->proven 分数差一点被拒', cp('verified', 'proven', 84.9, 30) is False)
check('3i. verified->proven 证据差1被拒', cp('verified', 'proven', 85, 29) is False)
check('3j. 跨级 hypothesis->verified 不允许', cp('hypothesis', 'verified', 100, 100, approved=True) is False)
check('3k. proven->deprecated 非晋升路径不允许', cp('proven', 'deprecated', 100, 100, approved=True) is False)
check('3l. candidate->verified 不要求 approved', cp('candidate', 'verified', 70, 10, approved=False) is True)

# 4. FTS/实体路召回（LIKE 路漏检场景）
fts_seed = [
    ('ke_tgt', 'talent', 't_xlm', '三月带货归档心得',
     '本场转化数据表现不错，佣金结构合理，复购情况良好，后续继续观察。'),
    ('ke_n1', 'talent', 't_other', '随手记录一', '今日无特别发现。'),
    ('ke_n2', '', '', '随手记录二', '常规巡检完成。'),
]
for eid, et, ei, title, content in fts_seed:
    conn.execute(
        "INSERT INTO knowledge_events (id, entity_type, entity_id, agent_id, event_type, title, "
        "content_full, created_at) VALUES (?, ?, ?, 'helen', 'analysis', ?, ?, ?)",
        (eid, et, ei, title, content, now_ms))
    srv._ke_fts_upsert(conn, eid, title, '', '{}')
conn.commit()

q4 = '小鹿妈妈推便携榨汁杯的效果怎么样'
like_ids = [it['id'] for it in srv._search_knowledge_events(q4)]
check('4a. 路1 LIKE 漏检目标事件（构造前提）', 'ke_tgt' not in like_ids, repr(like_ids))
res4 = srv._hybrid_retrieve_events(q4)
res4_ids = [it['id'] for it in res4]
check('4b. 实体/FTS 路召回目标事件', 'ke_tgt' in res4_ids, repr(res4_ids))
check('4c. 干扰事件未被召回', 'ke_n1' not in res4_ids and 'ke_n2' not in res4_ids, repr(res4_ids))

# 5. RRF 融合排序：多路命中 > 单路命中
conn.execute("INSERT INTO talents (id, name, category, status, created_by, created_at, updated_at) "
             "VALUES ('t_dxg', '大熊哥', '数码', 'active', 'admin', ?, ?)", (now_ms, now_ms))
rrf_seed = [
    ('keA', '复盘 ROI 数据分析', '归档内容甲'),   # FTS 路 + 实体路双命中
    ('keB', '每日进展同步', '例行跟进记录无亮点'),  # 仅实体路命中
]
for eid, title, content in rrf_seed:
    conn.execute(
        "INSERT INTO knowledge_events (id, entity_type, entity_id, agent_id, event_type, title, "
        "content_full, created_at) VALUES (?, 'talent', 't_dxg', 'helen', 'analysis', ?, ?, ?)",
        (eid, title, content, now_ms))
    srv._ke_fts_upsert(conn, eid, title, '', '{}')
conn.commit()
res5 = srv._hybrid_retrieve_events('大熊哥 ROI 复盘 分析')
res5_ids = [it['id'] for it in res5]
ok5 = len(res5) >= 2 and res5_ids[0] == 'keA' and 'keB' in res5_ids
if ok5:
    score_a = next(it['score'] for it in res5 if it['id'] == 'keA')
    score_b = next(it['score'] for it in res5 if it['id'] == 'keB')
    ok5 = score_a > score_b
check('5. RRF 融合：双路命中的 keA 排在单路命中的 keB 前',
      ok5, repr([(it['id'], it['score']) for it in res5]))

# 6. _ke_compute_importance 启发式
conn.execute("INSERT INTO deals (id, talent_id, status, created_at, updated_at) "
             "VALUES ('deal_imp', 't_imp2', 'live', 1, 1)")
conn.execute("INSERT INTO knowledge_events (id, entity_type, entity_id, content_full, created_at) "
             "VALUES ('ke_imp3', 'talent', 't_imp3', '首次分析', 1)")
conn.commit()
ci = srv._ke_compute_importance
check('6a. 首个事件+长文(>800) = 8', ci(conn, 'talent', 't_imp1', '长' * 900, {}) == 8.0)
check('6b. 首事件长文 + predicted_match = 9',
      ci(conn, 'talent', 't_imp1', '长' * 900, {'predicted_match': {'product_name': 'x'}}) == 9.0)
check('6c. 首事件长文 + predicted_match + 关联deal = 封顶10',
      ci(conn, 'talent', 't_imp2', '长' * 900, {'predicted_match': {'product_name': 'x'}}) == 10.0)
check('6d. 非首个事件+短文(<300) = 4', ci(conn, 'talent', 't_imp3', '短' * 100, {}) == 4.0)
check('6e. 非首个事件+普通长度 = 基准5', ci(conn, 'talent', 't_imp3', '中' * 400, {}) == 5.0)

# 7. deal 归因必填校验（MockHandler 直调）
fake_auth = types.SimpleNamespace(is_authenticated=True, user_id='admin', is_admin=True,
                                  user_info={'userId': 'admin'})
srv._authenticate = lambda *a, **k: fake_auth


class MockHandler:
    def __init__(self, path='', body=None):
        self.path = path
        self.headers = {}
        self.client_address = ('127.0.0.1', 0)
        self._body = body
        self.resp = None

    def _require_module_permission(self, auth, module):
        return True

    def _read_body(self):
        return self._body

    def _send_json(self, status, data):
        self.resp = (status, data)

    def _send_json_error(self, status, msg):
        self.resp = (status, {'error': msg})

    def _send_auth_error(self, error, status):
        self.resp = (status, {'error': error})


conn.execute("INSERT INTO talents (id, name, category, status, created_by, created_at, updated_at) "
             "VALUES ('t_att1', '归因达人甲', '美妆', 'active', 'admin', ?, ?)", (now_ms, now_ms))
conn.commit()


def _new_deal_to_live():
    h = MockHandler(body={'talent_id': 't_att1', 'product_name': '测试商品'})
    srv.SoloBraveHandler._handle_post_deal(h)
    did = h.resp[1]['id']
    for nxt in ('negotiating', 'sample_sent', 'approved', 'live'):
        hh = MockHandler(body={'status': nxt})
        srv.SoloBraveHandler._handle_put_deal(hh, did)
        assert hh.resp and hh.resp[0] == 200, f'状态链断裂: {nxt} {hh.resp!r}'
    return did


deal_a = _new_deal_to_live()
h = MockHandler(body={'status': 'completed', 'actual_gmv': 8000})
srv.SoloBraveHandler._handle_put_deal(h, deal_a)
check('7a. 切 completed 无 win_loss_category -> 400', h.resp and h.resp[0] == 400, repr(h.resp)[:150])
h = MockHandler(body={'status': 'completed', 'actual_gmv': 8000, 'win_loss_category': 'bogus_enum'})
srv.SoloBraveHandler._handle_put_deal(h, deal_a)
check('7b. 非法 win_loss_category 枚举 -> 400', h.resp and h.resp[0] == 400, repr(h.resp)[:150])
h = MockHandler(body={'status': 'completed', 'actual_gmv': 8000, 'win_loss_category': 'price_commission',
                      'key_moment': 'during_live', 'decision_maker_feedback': '达人临时要求加佣'})
srv.SoloBraveHandler._handle_put_deal(h, deal_a)
ok7c = h.resp and h.resp[0] == 200 and h.resp[1]['status'] == 'completed' \
    and h.resp[1]['win_loss_category'] == 'price_commission' \
    and h.resp[1]['key_moment'] == 'during_live' \
    and h.resp[1]['decision_maker_feedback'] == '达人临时要求加佣'
check('7c. completed+gmv+合法归因 -> 200 且返回三字段', ok7c, repr(h.resp)[:200])

deal_b = _new_deal_to_live()
h = MockHandler(body={'status': 'failed'})
srv.SoloBraveHandler._handle_put_deal(h, deal_b)
check('7d. 切 failed 无 win_loss_category -> 400', h.resp and h.resp[0] == 400, repr(h.resp)[:150])
h = MockHandler(body={'status': 'failed', 'win_loss_category': 'tone_mismatch', 'result_note': '达人口碑翻车'})
srv.SoloBraveHandler._handle_put_deal(h, deal_b)
check('7e. failed+合法归因 -> 200', h.resp and h.resp[0] == 200 and h.resp[1]['status'] == 'failed',
      repr(h.resp)[:150])

h = MockHandler(body={'talent_id': 't_att1', 'product_name': '测试商品'})
srv.SoloBraveHandler._handle_post_deal(h)
deal_c = h.resp[1]['id']
h = MockHandler(body={'key_moment': 'bogus_moment'})
srv.SoloBraveHandler._handle_put_deal(h, deal_c)
check('7f. key_moment 非法枚举 -> 400', h.resp and h.resp[0] == 400, repr(h.resp)[:150])
h = MockHandler(body={'key_moment': 'first_contact'})
srv.SoloBraveHandler._handle_put_deal(h, deal_c)
check('7g. key_moment 合法枚举 -> 200', h.resp and h.resp[0] == 200 and h.resp[1]['key_moment'] == 'first_contact',
      repr(h.resp)[:150])

# 8. 降级路径
srv._KE_FTS_ENABLED = False
try:
    res8a = srv._hybrid_retrieve_events('大熊哥 ROI 复盘 分析')
    ok8a = 'keA' in [it['id'] for it in res8a]
except Exception as e:
    ok8a = False
    res8a = repr(e)
srv._KE_FTS_ENABLED = True
check('8a. FTS 关闭后其余路仍正常返回', ok8a, repr(res8a)[:200])

_orig_search, _orig_escape, _orig_ent = srv._search_knowledge_events, srv._ke_fts_escape_query, srv._ke_entity_match_events


def _boom(*a, **k):
    raise RuntimeError('模拟故障')


srv._search_knowledge_events = _boom
srv._ke_fts_escape_query = _boom
srv._ke_entity_match_events = _boom
try:
    res8b = srv._hybrid_retrieve_events('大熊哥 ROI 复盘 分析')
    ok8b = res8b == []
except Exception as e:
    ok8b = False
    res8b = repr(e)
srv._search_knowledge_events, srv._ke_fts_escape_query, srv._ke_entity_match_events = \
    _orig_search, _orig_escape, _orig_ent
check('8b. 三路全部失败返回 [] 不抛异常', ok8b, repr(res8b)[:200])

_orig_extract = srv._extract_entities_from_text
srv._extract_entities_from_text = _boom
try:
    ctx8 = srv._retrieve_knowledge_context('分析一下小鹿妈妈的合作价值', 'helen', None)
    ok8c = ctx8 == ''
except Exception as e:
    ok8c = False
    ctx8 = repr(e)
srv._extract_entities_from_text = _orig_extract
check('8c. 注入检索内部异常返回空串不抛异常', ok8c, repr(ctx8)[:120])

conn.close()
import shutil
shutil.rmtree(tmpdir, ignore_errors=True)
print()
if failures:
    print(f'共 {failures} 项失败')
    sys.exit(1)
print('全部通过')
