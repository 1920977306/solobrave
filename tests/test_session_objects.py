# -*- coding: utf-8 -*-
"""多对象会话记忆（bot_session_objects，2026-11-02 豆鲨包实锤驱动）。

锁四条（每条都有行为断言，不是符号断言）：
1. **登记幂等**：同租户同类型同 key 重复登记 = 更新时间与摘要，不堆重复行。
2. **滚动修剪**：每租户每类型只留最近 6 个（老对象被挤掉，不无限涨）。
3. **注入形态三态**：
   - 对比型问题（区别/对比/哪个）+ 同类型 ≥2 对象 ⇒ 强制并列对比块；
   - 对比型问题但同类型 <2 ⇒ 「缺对象」块（明说缺谁，禁编造）；
   - 模糊指代（上面/那两个/分析过）⇈ 对象簿块，禁止重复索要。
   - 没有问题形态信号 ⇒ 空串（宁漏勿抢，不打扰普通问答）。
4. **execute_qa 真实注入**：monkeypatch LLM 捕获 prompt，断言对象簿文本在内。

铁律：零 prod 接触 —— 全部走 tmp DATA_DIR + init_db()。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from daren_bot import config, db  # noqa: E402


@pytest.fixture()
def botdb(tmp_path, monkeypatch):
    """隔离 bot 库到 tmp（与 test_agent_tier.py 同款夹具）。"""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "bot.db")
    monkeypatch.setattr(config, "MASTER_KEY_PATH", tmp_path / "master.key")
    tmp_path.mkdir(parents=True, exist_ok=True)
    db.init_db()
    return tmp_path / "bot.db"


TK = "daren_test2"


class TestRecordAndGet:
    def test_record_and_get_roundtrip(self, botdb):
        db.record_session_object(TK, "talent", "douyin_A", "豆鲨包", "账号评估 潜力型")
        objs = db.get_session_objects(TK)
        assert len(objs) == 1
        assert objs[0]["obj_type"] == "talent"
        assert objs[0]["label"] == "豆鲨包"
        assert "潜力型" in objs[0]["oneline"]

    def test_upsert_same_key_updates_not_duplicates(self, botdb):
        db.record_session_object(TK, "talent", "douyin_A", "豆鲨包", "旧摘要")
        db.record_session_object(TK, "talent", "douyin_A", "豆鲨包", "新摘要")
        objs = db.get_session_objects(TK, "talent")
        assert len(objs) == 1
        assert objs[0]["oneline"] == "新摘要"

    def test_rolling_prune_keeps_latest_six(self, botdb):
        for i in range(9):
            db.record_session_object(TK, "product", f"pid_{i}", f"商品{i}", "")
        objs = db.get_session_objects(TK, "product", within_hours=24, limit=10)
        assert len(objs) == 6
        # 留下的是最新的（时间升序，最后一个是最新登记的）
        assert objs[-1]["obj_key"] == "pid_8"
        assert objs[0]["obj_key"] == "pid_3"

    def test_time_window_filters_old(self, botdb):
        db.record_session_object(TK, "talent", "old_one", "老公", "")
        # 手改 created_at 到 25 小时前
        import time as _time
        with db.db_ctx() as conn:
            conn.execute(
                "UPDATE bot_session_objects SET created_at=? WHERE obj_key='old_one'",
                (int(_time.time()) - 25 * 3600,))
        assert db.get_session_objects(TK, within_hours=24) == []
        assert len(db.get_session_objects(TK, within_hours=48)) == 1

    def test_tenant_isolation(self, botdb):
        db.record_session_object(TK, "talent", "a", "A", "")
        db.record_session_object("other_tenant", "talent", "b", "B", "")
        assert len(db.get_session_objects(TK)) == 1
        assert len(db.get_session_objects("other_tenant")) == 1


class TestBlockBuilder:
    """_session_objects_block 三态触发 + 宁漏勿抢（直接打内部函数）。"""

    def _seed(self, n_talent=2, n_video=0):
        for i in range(n_talent):
            db.record_session_object(
                TK, "talent", f"dy_{i}", f"达人{i}", f"账号评估 潜力型")
        for i in range(n_video):
            db.record_session_object(
                TK, "video", f"url_{i}", f"视频标题{i}", "视频拆解报告已出")

    def test_compare_with_two_same_type_forces_compare(self, botdb):
        from daren_bot import executors
        self._seed(n_talent=2)
        blk = executors._session_objects_block(TK, "这两个达人有什么区别")
        assert blk
        assert "并列对比" in blk
        assert "达人0" in blk and "达人1" in blk

    def test_compare_with_one_says_missing_not_fabricate(self, botdb):
        from daren_bot import executors
        self._seed(n_talent=1)
        blk = executors._session_objects_block(TK, "这两个达人有什么区别")
        assert "还没分析过" in blk
        assert "严禁编造" in blk

    def test_vague_reference_injects_book(self, botdb):
        from daren_bot import executors
        self._seed(n_talent=1, n_video=2)
        blk = executors._session_objects_block(TK, "这是两个账号 上面你分析过")
        assert "会话对象簿" in blk
        assert "不要重复索要" in blk

    def test_video_compare_question_uses_video_objects(self, botdb):
        from daren_bot import executors
        self._seed(n_talent=1, n_video=2)
        blk = executors._session_objects_block(TK, "怎么没有两个视频对比")
        assert "视频标题0" in blk and "视频标题1" in blk
        assert "并列对比" in blk

    def test_plain_question_no_trigger(self, botdb):
        from daren_bot import executors
        self._seed(n_talent=2)
        assert executors._session_objects_block(TK, "给我推荐几个品") == ""
        assert executors._session_objects_block(TK, "今天天气怎么样") == ""

    def test_label_mention_injects_that_object(self, botdb):
        from daren_bot import executors
        self._seed(n_talent=2)
        blk = executors._session_objects_block(TK, "达人1最近数据怎么样")
        assert "达人1" in blk
        assert "达人0" not in blk

    def test_empty_book_returns_empty(self, botdb):
        from daren_bot import executors
        assert executors._session_objects_block(TK, "这两个达人有什么区别") == ""


class TestQaInjection:
    """execute_qa 真实注入：monkeypatch LLM 捕获 prompt（行为断言）。"""

    def test_qa_prompt_contains_book_on_vague_ref(self, botdb, monkeypatch):
        from daren_bot import executors
        db.record_session_object(TK, "talent", "dy_1", "豆鲨包", "账号评估 潜力型")
        db.record_session_object(TK, "talent", "dy_2", "小陈爱吃吃", "账号评估 稳健型")

        captured = {}

        def _fake_llm(prompt):
            captured["prompt"] = prompt
            return "对比结论"

        monkeypatch.setattr(executors, "_qa_llm", _fake_llm)
        monkeypatch.setattr(executors, "_talent_context", lambda tk: "")
        # 不触发 recollect / 第三方识别分支
        task = {"tenant_key": TK, "target": "这是两个账号 上面你分析过",
                "payload": "{}"}
        ans = executors.execute_qa(task)
        assert ans == "对比结论"
        assert "会话对象簿" in captured["prompt"]
        assert "豆鲨包" in captured["prompt"]

    def test_qa_prompt_without_signal_no_book(self, botdb, monkeypatch):
        from daren_bot import executors
        db.record_session_object(TK, "talent", "dy_1", "豆鲨包", "账号评估 潜力型")

        captured = {}

        def _fake_llm(prompt):
            captured["prompt"] = prompt
            return "ok"

        monkeypatch.setattr(executors, "_qa_llm", _fake_llm)
        monkeypatch.setattr(executors, "_talent_context", lambda tk: "")
        task = {"tenant_key": TK, "target": "给我推荐几个品", "payload": "{}"}
        executors.execute_qa(task)
        assert "会话对象簿" not in captured["prompt"]


class TestWorkerRecording:
    """worker 记录钩子：成功任务按类型登记对象（直接打内部函数）。"""

    def _task(self, ttype, target):
        return {"id": "t1", "tenant_key": TK, "type": ttype, "target": target}

    def test_douyin_records_talent(self, botdb):
        from daren_bot import worker
        report = "📊 达人报告：豆鲨包（抖音号 62224890070）\n粉丝 1.3万\n🎯 账号评估：潜力型\n画像：xxx"
        worker._record_session_object(self._task("douyin", "62224890070"), report)
        objs = db.get_session_objects(TK, "talent")
        assert len(objs) == 1
        assert objs[0]["label"] == "豆鲨包"
        assert "潜力型" in objs[0]["oneline"]

    def test_product_records_verdict(self, botdb):
        from daren_bot import worker
        env = {"title": "商品分析", "stats": {"name": "软支撑内衣"},
               "md": "## 结论\n🟡 慎带\n理由句\n"}
        worker._record_session_object(
            self._task("product", "3668788850777522321"), json.dumps(env),
            {"name": "软支撑内衣"})
        objs = db.get_session_objects(TK, "product")
        assert objs[0]["label"] == "软支撑内衣"
        assert "慎带" in objs[0]["oneline"]

    def test_video_records_title(self, botdb):
        from daren_bot import worker
        worker._record_session_object(
            self._task("video", "https://v.douyin.com/abc"),
            "穿搭好物开箱\n## 视频拆解报告")
        objs = db.get_session_objects(TK, "video")
        assert objs[0]["label"] == "穿搭好物开箱"

    def test_broken_summary_never_raises(self, botdb):
        from daren_bot import worker
        worker._record_session_object(self._task("douyin", "x"), None)
        worker._record_session_object({"tenant_key": TK, "type": "?"},
                                      "whatever")
        # 不抛异常；douyin 无报告文本时退化成 target 当 label 也登记（有目标就有上下文）
        objs = db.get_session_objects(TK)
        assert len(objs) == 1
        assert objs[0]["obj_key"] == "x"


class TestInflightNudge:
    """碎片催促词承接（2026-11-02 豆鲨包「给你啦」「你分析啊」实锤）。"""

    def _mk_task(self, status, age_s=0):
        import time as _time
        with db.db_ctx() as conn:
            conn.execute(
                "INSERT INTO bot_collect_tasks (id, tenant_key, type, target,"
                " status, created_at) VALUES (?,?,?,?,?,?)",
                (f"nudge_{status}_{age_s}", TK, "image", "/tmp/x.png",
                 status, int(_time.time()) - age_s))

    def test_pending_task_counts_as_inflight(self, botdb):
        self._mk_task("pending")
        assert db.has_inflight_task(TK) is True

    def test_collecting_task_counts(self, botdb):
        self._mk_task("collecting")
        assert db.has_inflight_task(TK) is True

    def test_done_task_not_inflight(self, botdb):
        self._mk_task("done")
        assert db.has_inflight_task(TK) is False

    def test_outside_window_not_inflight(self, botdb):
        self._mk_task("pending", age_s=3600)
        assert db.has_inflight_task(TK) is False

    def test_tenant_isolation(self, botdb):
        self._mk_task("pending")
        assert db.has_inflight_task("nobody") is False


class TestSelfFacts:
    """达人自述事实沉淀（2026-11-02 老大拍板「聊天即训练知识库」落地）。"""

    def test_add_and_get_roundtrip(self, botdb):
        assert db.add_talent_fact(TK, "我出单的都是发圈牙膏牙刷这类的") is True
        facts = db.get_talent_facts(TK)
        assert facts == ["我出单的都是发圈牙膏牙刷这类的"]

    def test_exact_dup_not_recorded(self, botdb):
        db.add_talent_fact(TK, "我出单的都是发圈牙膏牙刷这类的")
        assert db.add_talent_fact(TK, "我出单的都是发圈牙膏牙刷这类的") is False
        assert len(db.get_talent_facts(TK)) == 1

    def test_rolling_keep_ten(self, botdb):
        for i in range(13):
            db.add_talent_fact(TK, f"我主要带第{i}类商品测试")
        facts = db.get_talent_facts(TK)
        assert len(facts) == 10
        assert facts[-1] == "我主要带第12类商品测试"

    def test_short_fact_rejected(self, botdb):
        assert db.add_talent_fact(TK, "我带") is False
        assert db.get_talent_facts(TK) == []

    def test_extract_from_real_daren_sentences(self, botdb):
        from daren_bot import executors
        assert "出单的都是" in executors._extract_self_fact(
            "我本身出单的都是 发圈 牙膏 牙刷这类的")
        assert "没有带过" in executors._extract_self_fact(
            "我没有带过厨房收纳类")
        assert "本来也是带" in executors._extract_self_fact(
            "我本来也是带裤子多一些")
        assert executors._extract_self_fact("这个品能带吗") == ""
        assert executors._extract_self_fact("我还有多少积分") == ""

    def test_qa_records_fact_and_injects_on_later_question(self, botdb, monkeypatch):
        from daren_bot import executors
        monkeypatch.setattr(executors, "_qa_llm", lambda p: "ok")
        monkeypatch.setattr(executors, "_talent_context", lambda tk: "")
        # 第一句：自述事实 → 入库
        executors.execute_qa({"tenant_key": TK,
                              "target": "我本身出单的都是发圈牙膏牙刷这类的",
                              "payload": "{}"})
        assert len(db.get_talent_facts(TK)) == 1
        # 第二句：选品类问题 → prompt 里要带上自述
        captured = {}
        def _cap(p):
            captured["prompt"] = p
            return "ok"
        monkeypatch.setattr(executors, "_qa_llm", _cap)
        executors.execute_qa({"tenant_key": TK,
                              "target": "给我分析几个适合我带货的商品",
                              "payload": "{}"})
        assert "达人自述" in captured["prompt"]
        assert "发圈牙膏牙刷" in captured["prompt"]

    def test_qa_no_inject_on_offtopic_question(self, botdb, monkeypatch):
        from daren_bot import executors
        db.add_talent_fact(TK, "我出单的都是发圈牙膏牙刷这类的")
        captured = {}
        monkeypatch.setattr(executors, "_qa_llm",
                            lambda p: captured.setdefault("prompt", p) or "ok")
        monkeypatch.setattr(executors, "_talent_context", lambda tk: "")
        executors.execute_qa({"tenant_key": TK, "target": "你分析啊",
                              "payload": "{}"})
        assert "达人自述" not in captured["prompt"]

    def test_commerce_role_no_inject(self, botdb, monkeypatch):
        from daren_bot import executors
        db.add_talent_fact(TK, "我出单的都是发圈牙膏牙刷这类的")
        monkeypatch.setattr(executors, "_qa_llm", lambda p: "ok")
        monkeypatch.setattr(executors, "_talent_context", lambda tk: "")
        captured = {}
        def _cap(p):
            captured["prompt"] = p
            return "ok"
        monkeypatch.setattr(executors, "_qa_llm", _cap)
        # 角色切到商务：db.get_tenant 返回 None ⇒ role=''（达人缺省），
        # 这里直接验证 _self_facts_block 的 commerce 门。
        assert executors._self_facts_block(
            TK, "给我推荐几个品", role="commerce") == ""
