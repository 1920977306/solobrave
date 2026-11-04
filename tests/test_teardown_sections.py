"""爆款拆解一期：两列卖点结构化落库 + 反馈埋点 测试。

锁住四件事：
1. **报告 prompt 必须产出三节结构化内容**（产品露出 / 产品卖点(客观属性) / 卖点表达(达人话术)），
   且带正反例防两列混用（2026-11-04 一期约束②）。
2. **`_parse_teardown_sections` 纯函数**：三节齐全 / 缺节 / 无节（报告失败串）都不炸，
   缺列留空。
3. **`talent_videos` 幂等迁移**：老库（无新列）跑 `_ensure_video_tables` 自动补列，
   重复跑不报错。
4. **feedback 端点行为**：`_video_feedback` 写库 + 幂等补列；非法值 / 不存在 id 拒绝。
"""
import importlib
import inspect
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

buyin_worker = importlib.import_module("buyin_worker")
server = importlib.import_module("daren_cockpit.server")

# 老库形态（一期之前的 talent_videos，无新列）
OLD_VIDEO_DDL = """
CREATE TABLE talent_videos (
    id TEXT PRIMARY KEY,
    job_id TEXT DEFAULT '',
    talent_id TEXT DEFAULT '',
    talent_name TEXT DEFAULT '',
    rank INTEGER DEFAULT 0,
    title TEXT DEFAULT '',
    cover_url TEXT DEFAULT '',
    metrics TEXT DEFAULT '{}',
    source_url TEXT DEFAULT '',
    local_path TEXT DEFAULT '',
    transcript TEXT DEFAULT '',
    transcript_status TEXT DEFAULT '',
    visual_desc TEXT DEFAULT '',
    report TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    error TEXT DEFAULT '',
    created_at INTEGER DEFAULT 0,
    updated_at INTEGER DEFAULT 0
)
"""

NEW_COLS = ("product_name", "product_points", "selling_expr", "feedback")


def _cols(con):
    return {r[1] for r in con.execute("PRAGMA table_info(talent_videos)")}


# ---------- 1. prompt 守卫 ----------

class TestPromptGuard:
    def test_three_new_sections_present(self):
        src = inspect.getsource(buyin_worker._teardown_report)
        for kw in ("## 产品露出", "## 产品卖点(客观属性)", "## 卖点表达(达人话术)"):
            assert kw in src, f"prompt 缺节: {kw}"

    def test_positive_negative_examples_present(self):
        """正反例必须都在 —— 防模型把属性塞进话术列（一期约束②）。"""
        src = inspect.getsource(buyin_worker._teardown_report)
        assert "316不锈钢内胆" in src          # 卖点正例
        assert "办公室姐妹听我一句" in src       # 表达正例（场景+话术）
        assert "错误示例" in src                  # 反例区存在

    def test_product_points_and_expression_not_merged(self):
        """两列不许退化成一个「卖点呈现方式」节。"""
        src = inspect.getsource(buyin_worker._teardown_report)
        assert "## 卖点呈现方式" not in src


# ---------- 2. 报告解析纯函数 ----------

FULL_REPORT = """## 产品露出
复古毛绒热水袋（个人护理/热敷包）
## 产品卖点(客观属性)
- 316不锈钢内胆
- 500ml 大容量
## 卖点表达(达人话术)
- 办公室姐妹听我一句，这杯子放包里从来不漏
## 数据诊断
GPM 偏弱，直说。"""


class TestParseSections:
    def test_full_report(self):
        out = buyin_worker._parse_teardown_sections(FULL_REPORT)
        assert "热水袋" in out["product_name"]
        assert "316不锈钢" in out["product_points"]
        assert "办公室姐妹" in out["selling_expr"]

    def test_missing_section_leaves_empty(self):
        out = buyin_worker._parse_teardown_sections(
            "## 产品露出\n热水袋\n## 数据诊断\n弱")
        assert "热水袋" in out["product_name"]
        assert out["product_points"] == ""
        assert out["selling_expr"] == ""

    def test_failure_string_not_crash(self):
        out = buyin_worker._parse_teardown_sections("(拆解报告生成失败: timeout)")
        assert out == {"product_name": "", "product_points": "", "selling_expr": ""}

    def test_empty_and_garbage(self):
        assert buyin_worker._parse_teardown_sections("") == \
            {"product_name": "", "product_points": "", "selling_expr": ""}
        assert buyin_worker._parse_teardown_sections(None) == \
            {"product_name": "", "product_points": "", "selling_expr": ""}
        out = buyin_worker._parse_teardown_sections("没有任何标题的正文")
        assert out["product_points"] == ""

    def test_sections_do_not_bleed_into_next(self):
        """每节内容必须截在下一个 ## 之前。"""
        out = buyin_worker._parse_teardown_sections(FULL_REPORT)
        assert "数据诊断" not in out["selling_expr"]


# ---------- 3. 幂等迁移 ----------

class TestVideoTableMigration:
    def test_old_db_gets_new_cols(self, tmp_path, monkeypatch):
        db = tmp_path / "test.db"
        con = sqlite3.connect(str(db))
        con.execute(OLD_VIDEO_DDL)
        con.commit()
        con.close()
        monkeypatch.setattr(buyin_worker, "DB_PATH", db)
        buyin_worker._ensure_video_tables()
        con = sqlite3.connect(str(db))
        cols = _cols(con)
        con.close()
        for col in NEW_COLS:
            assert col in cols, f"迁移缺列: {col}"

    def test_migration_idempotent(self, tmp_path, monkeypatch):
        db = tmp_path / "test.db"
        con = sqlite3.connect(str(db))
        con.execute(OLD_VIDEO_DDL)
        con.commit()
        con.close()
        monkeypatch.setattr(buyin_worker, "DB_PATH", db)
        buyin_worker._ensure_video_tables()
        buyin_worker._ensure_video_tables()  # 重复跑不得报错
        con = sqlite3.connect(str(db))
        # 老库 18 列 + 新列 4 列，重复迁移不得多加
        assert len(_cols(con)) == 22
        con.close()

    def test_fresh_db_has_cols_directly(self, tmp_path, monkeypatch):
        db = tmp_path / "test.db"
        monkeypatch.setattr(buyin_worker, "DB_PATH", db)
        buyin_worker._ensure_video_tables()
        con = sqlite3.connect(str(db))
        cols = _cols(con)
        con.close()
        for col in NEW_COLS:
            assert col in cols


# ---------- 4. feedback 端点行为 ----------

class TestVideoFeedback:
    def _mk_db(self, tmp_path, monkeypatch):
        db = tmp_path / "test.db"
        con = sqlite3.connect(str(db))
        con.execute(OLD_VIDEO_DDL)  # 故意用老库形态，连迁移一起验
        con.execute("INSERT INTO talent_videos (id, title) VALUES ('vid_abc1', 't')")
        con.commit()
        con.close()
        monkeypatch.setattr("daren_bot.config.DB_PATH", db)
        return db

    def test_write_ok(self, tmp_path, monkeypatch):
        db = self._mk_db(tmp_path, monkeypatch)
        ok, err = server._video_feedback("vid_abc1", "对")
        assert ok and err == ""
        con = sqlite3.connect(str(db))
        row = con.execute("SELECT feedback FROM talent_videos WHERE id='vid_abc1'").fetchone()
        cols = _cols(con)
        con.close()
        assert row[0] == "对"
        assert "feedback" in cols  # 老库被端点顺手补了列

    def test_all_three_choices_accepted(self, tmp_path, monkeypatch):
        db = self._mk_db(tmp_path, monkeypatch)
        for choice in ("对", "不对", "说不清"):
            ok, _ = server._video_feedback("vid_abc1", choice)
            assert ok, choice

    def test_bad_choice_rejected(self, tmp_path, monkeypatch):
        self._mk_db(tmp_path, monkeypatch)
        ok, err = server._video_feedback("vid_abc1", "很好")
        assert not ok and "feedback 必须是" in err

    def test_bad_id_rejected(self, tmp_path, monkeypatch):
        self._mk_db(tmp_path, monkeypatch)
        ok, err = server._video_feedback("vid;drop", "对")
        assert not ok

    def test_unknown_id_rejected(self, tmp_path, monkeypatch):
        self._mk_db(tmp_path, monkeypatch)
        ok, err = server._video_feedback("vid_nope", "对")
        assert not ok and "not found" in err
