"""达人标识口径整改（daren_cockpit/tools/fix_talent_ids.py）的测试。

重点锁四件事：
1. **不靠格式判别** —— 平台达人 id 也可能是纯数字（372168571 / 69097875157），
   所以只能对着平台真值 join，不能按「纯数字=抖音号」猜。
2. **三路 join 的强度顺序**：平台达人id > 真抖音号 > 昵称精确匹配。
3. **`douyin_id` 一根手指不动** —— 它是对外契约（创建查重 / accountId / 飞书筛选）。
4. **只删「确认空壳」** —— 有任何一条真实数据就不删，只报告。
"""
import importlib.util
import sqlite3
from pathlib import Path

import pytest

MOD_PATH = (Path(__file__).resolve().parents[1] / "daren_cockpit" / "tools"
            / "fix_talent_ids.py")
spec = importlib.util.spec_from_file_location("fix_talent_ids", MOD_PATH)
ft = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ft)


@pytest.fixture()
def db(tmp_path):
    """造一个最小库：talents + platform_talent_stats + product_talent_match。"""
    path = tmp_path / "t.db"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE talents (
            id TEXT PRIMARY KEY, name TEXT, douyin_id TEXT DEFAULT '',
            followers INTEGER DEFAULT 0, total_gmv_text TEXT DEFAULT '',
            rating_score REAL DEFAULT 0, city TEXT DEFAULT '',
            main_category TEXT DEFAULT '',
            created_at INTEGER DEFAULT 0, updated_at INTEGER DEFAULT 0);
        CREATE TABLE platform_talent_stats (
            stat_date TEXT, platform_talent_id TEXT, douyin_id TEXT, name TEXT);
        CREATE TABLE product_talent_match (product_id TEXT, talent_id TEXT);
        CREATE TABLE talent_collects (
            id TEXT PRIMARY KEY, name TEXT, talent_id TEXT, status TEXT);
    """)
    conn.executemany(
        "INSERT INTO platform_talent_stats VALUES ('2026-10-23',?,?,?)",
        [("hlb725", "96800857093", "洁洁爱户外"),
         ("69097875157", "534782117427321", "小晴姑姑"),
         ("Jingyu0627", "62025284273", "鲸鱼姐姐")])
    conn.commit()
    ft.ensure_schema(conn)          # 建 platform_talent_id / douyin_uid 两列
    return conn


def _add_talent(conn, tid, name, douyin_id, **kw):
    conn.execute(
        "INSERT INTO talents (id,name,douyin_id,followers,total_gmv_text,rating_score,"
        "city,main_category,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (tid, name, douyin_id, kw.get("followers", 0),
         kw.get("total_gmv_text", ""), kw.get("rating_score", 0),
         kw.get("city", ""), kw.get("main_category", ""),
         kw.get("created_at", 100), kw.get("updated_at", 100)))
    conn.commit()


# ───────────── 三路 join ─────────────

def test_join_by_platform_id_is_strongest(db):
    _add_talent(db, "t1", "洁洁爱户外", "hlb725")
    j = ft.join_talents(db)
    m = j["matched"][0]
    assert m["how"].startswith("①")
    assert m["platform_talent_id"] == "hlb725"
    assert m["douyin_uid"] == "96800857093"
    assert m["name_agrees"] is True


def test_join_by_name_when_douyin_id_is_dirty(db):
    """`douyin_id` 是脏值（中文名）时，靠昵称精确匹配仍能定位到平台身份。"""
    _add_talent(db, "t1", "鲸鱼姐姐", "鲸鱼姐姐")
    j = ft.join_talents(db)
    m = j["matched"][0]
    assert m["how"].startswith("③")
    assert m["platform_talent_id"] == "Jingyu0627"
    assert m["douyin_uid"] == "62025284273"


def test_join_reports_unmatched_instead_of_guessing(db):
    """平台查不到 ⇒ 如实进 unmatched，**不许按「纯数字就是抖音号」猜**。"""
    _add_talent(db, "t1", "查无此人", "372168571")
    j = ft.join_talents(db)
    assert j["matched"] == []
    assert len(j["unmatched"]) == 1


def test_numeric_platform_id_is_not_mistaken_for_douyin(db):
    """★ `372168571` 是**平台达人 id**（纯数字），不是真抖音号。

    这是本轮推翻的错误结论的回归锁：不能按「纯数字 ⇒ 抖音号」分流。
    """
    db.execute("INSERT INTO platform_talent_stats VALUES ('2026-10-23','372168571','72557607792','小韩睡了吗')")
    db.commit()
    _add_talent(db, "t1", "小韩睡了吗", "372168571")
    j = ft.join_talents(db)
    m = j["matched"][0]
    assert m["platform_talent_id"] == "372168571"
    assert m["douyin_uid"] == "72557607792"


# ───────────── 重复档案：只删确认空壳 ─────────────

def test_duplicate_detected_and_shell_flagged(db):
    _add_talent(db, "keep", "小晴姑姑", "69097875157",
                followers=16000, total_gmv_text="¥100万-250万", rating_score=5.0,
                city="江西·南昌", main_category="生活家居",
                created_at=100, updated_at=999)
    _add_talent(db, "shell", "名小晴姑姑", "69097875157",
                created_at=200, updated_at=200)          # 空壳：一切为空 + 从未更新
    dups = ft.find_duplicates(db)
    assert len(dups) == 1
    assert dups[0]["keep"]["id"] == "keep"
    assert [m["id"] for m, _, _ in dups[0]["shells"]] == ["shell"]


def test_duplicate_not_deleted_when_it_has_real_data(db):
    """第二行**有真实数据** ⇒ 不是空壳 ⇒ 不该进 shells（只报告）。"""
    _add_talent(db, "keep", "小晴姑姑", "69097875157", followers=100)
    _add_talent(db, "other", "小晴姑姑2", "69097875157", followers=200)
    dups = ft.find_duplicates(db)
    assert len(dups) == 1
    assert dups[0]["shells"] == []          # 两边都有数据 → 一个都不能删
    assert dups[0]["keep"]["id"] == "keep"
    assert dups[0]["all_shell"] is False


def test_duplicate_with_matches_is_not_shell(db):
    """有商品匹配的档案 ⇒ 不是空壳。"""
    _add_talent(db, "keep", "小晴姑姑", "69097875157", followers=100)
    _add_talent(db, "shell", "名小晴姑姑", "69097875157")
    db.execute("INSERT INTO product_talent_match VALUES ('p1','shell')")
    db.commit()
    dups = ft.find_duplicates(db)
    assert dups[0]["shells"] == []


def test_all_shell_group_is_reported_but_not_actionable(db):
    """全是空壳 ⇒ 无从判断正主 ⇒ 只报告，不进 shells。"""
    _add_talent(db, "s1", "甲", "69097875157")
    _add_talent(db, "s2", "乙", "69097875157")
    dups = ft.find_duplicates(db)
    assert len(dups) == 1
    assert dups[0]["all_shell"] is True
    assert dups[0]["keep"] is None


# ───────────── schema ─────────────

def test_ensure_schema_adds_columns_idempotently(db):
    cols = {r[1] for r in db.execute("PRAGMA table_info(talents)")}
    assert {"platform_talent_id", "douyin_uid"} <= cols
    # 再跑一次不应重复加（幂等）
    assert ft.ensure_schema(db) == []


def test_ensure_schema_adds_columns_on_fresh_db(tmp_path):
    """新库上首次调用才加列。"""
    path = tmp_path / "fresh.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE talents (id TEXT PRIMARY KEY, name TEXT)")
    conn.commit()
    added = ft.ensure_schema(conn)
    assert added == ["platform_talent_id", "douyin_uid"]
    cols = {r[1] for r in conn.execute("PRAGMA table_info(talents)")}
    assert {"platform_talent_id", "douyin_uid"} <= cols
