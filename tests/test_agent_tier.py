# -*- coding: utf-8 -*-
"""P2 代理层级守卫（docs/AGENT-TIER-DESIGN.md，2026-11-02 拍板 D1b/D2a/D3a）。

锁四条（每条都有变异对照）：
1. **两列迁移幂等**：agent_of / agent_enabled 走 PRAGMA 检查 + ALTER（与 role/weekly_sub
   同模式）；旧库升级后存量行默认值零行为变化；重复 init_db 不炸。
2. **subtree_keys 是唯一可见范围入口**：自己 + 传递下级；含访问标记防环
   （拍板补充：脏数据成环时跳过 + 告警，绝不死循环）。
3. **transfer 单事务**：扣 from + 加 to + 两条流水一个事务；余额不足整体不落地
   （照 §8.11 两事务坑：分两次调用 ⇒ 重复请求净多扣）。
4. **人格/层级正交**：静态扫描 daren_bot/，agent_of/agent_enabled 不许与 role（人格）
   出现在同一个布尔判定表达式里（§8.3 拍板锁；代理可以是任何人格）。

铁律：零 prod 接触 —— 全部走 tmp DATA_DIR + init_db()。
"""
import ast
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from daren_bot import config, db  # noqa: E402

BOT_DIR = ROOT / "daren_bot"

# 旧库形态（2026-11-02 加列前）：两列不存在
DDL_OLD_TENANTS = """
CREATE TABLE bot_tenants (
  app_id          TEXT PRIMARY KEY,
  tenant_key      TEXT NOT NULL UNIQUE,
  app_secret_enc  TEXT NOT NULL,
  bind_douyin_id  TEXT NOT NULL DEFAULT '',
  status          TEXT NOT NULL DEFAULT 'active',
  created_at      INTEGER NOT NULL,
  updated_at      INTEGER NOT NULL
);
INSERT INTO bot_tenants (app_id, tenant_key, app_secret_enc, created_at, updated_at)
VALUES ('cli_old', 'legacy_tk', 'enc', 1, 1);
"""


@pytest.fixture()
def botdb(tmp_path, monkeypatch):
    """全新库：init_db() 跑全量 DDL + 轻量迁移。"""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "bot.db")
    monkeypatch.setattr(config, "MASTER_KEY_PATH", tmp_path / "master.key")
    db.init_db()
    db.add_tenant("cli_a", "agent_a", "enc_a")
    db.add_tenant("cli_b", "daren_b", "enc_b")
    db.add_tenant("cli_c", "daren_c", "enc_c")
    return tmp_path / "bot.db"


def _conn(path):
    c = sqlite3.connect(str(path))
    c.row_factory = sqlite3.Row
    return c


# ---------- 守卫 1：迁移幂等 ----------

class TestMigration:
    def test_fresh_db_has_columns(self, botdb):
        conn = _conn(botdb)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(bot_tenants)")}
        conn.close()
        assert "agent_of" in cols and "agent_enabled" in cols

    def test_defaults_are_neutral(self, botdb):
        t = db.get_tenant("daren_b")
        assert t["agent_of"] == ""
        assert int(t["agent_enabled"]) == 0, "存量租户默认不是代理（零行为变化）"

    def test_init_db_twice_no_error(self, botdb):
        db.init_db()  # PRAGMA 检查 ⇒ 重复 ALTER 不会发生，不炸即过
        t = db.get_tenant("daren_b")
        assert t["agent_of"] == ""

    def test_upgrade_from_old_shape(self, tmp_path, monkeypatch):
        """旧库（无两列）→ init_db 升级 ⇒ 列出现、存量行默认值、数据不丢。"""
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        monkeypatch.setattr(config, "IMAGE_DIR", tmp_path / "images")
        monkeypatch.setattr(config, "DB_PATH", tmp_path / "bot.db")
        monkeypatch.setattr(config, "MASTER_KEY_PATH", tmp_path / "master.key")
        tmp_path.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(tmp_path / "bot.db"))
        conn.executescript(DDL_OLD_TENANTS)
        conn.close()
        db.init_db()
        conn = _conn(tmp_path / "bot.db")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(bot_tenants)")}
        row = conn.execute("SELECT tenant_key, agent_of, agent_enabled "
                           "FROM bot_tenants WHERE tenant_key='legacy_tk'").fetchone()
        conn.close()
        assert "agent_of" in cols and "agent_enabled" in cols
        assert row["agent_of"] == "" and row["agent_enabled"] == 0
        assert row["tenant_key"] == "legacy_tk", "存量数据一条不许丢"


# ---------- 守卫 2：subtree_keys 唯一入口 + 防环 ----------

class TestSubtree:
    def test_root_plus_children(self, botdb):
        assert db.set_agent_parent("daren_b", "agent_a")[0]
        assert db.set_agent_parent("daren_c", "agent_a")[0]
        assert sorted(db.subtree_keys("agent_a")) == ["agent_a", "daren_b", "daren_c"]

    def test_unrelated_not_included(self, botdb):
        db.add_tenant("cli_x", "outsider", "enc_x")
        db.set_agent_parent("daren_b", "agent_a")
        assert "outsider" not in db.subtree_keys("agent_a")

    def test_inactive_child_excluded(self, botdb):
        db.set_agent_parent("daren_b", "agent_a")
        conn = _conn(botdb)
        conn.execute("UPDATE bot_tenants SET status='disabled' WHERE tenant_key='daren_b'")
        conn.commit(); conn.close()
        assert db.subtree_keys("agent_a") == ["agent_a"]

    def test_transitive_closure(self, botdb):
        """拍板是单级树，但实现按传递闭包——手工造孙级也要能被看见。"""
        db.add_tenant("cli_g", "grand_g", "enc_g")
        db.set_agent_parent("daren_b", "agent_a")
        conn = _conn(botdb)
        conn.execute("UPDATE bot_tenants SET agent_of='daren_b' WHERE tenant_key='grand_g'")
        conn.commit(); conn.close()
        assert sorted(db.subtree_keys("agent_a")) == ["agent_a", "daren_b", "grand_g"]

    def test_cycle_does_not_hang(self, botdb):
        """脏数据成环（A↔B）：必须终止且不重不漏，不许死循环。"""
        conn = _conn(botdb)
        conn.execute("UPDATE bot_tenants SET agent_of='daren_b' WHERE tenant_key='agent_a'")
        conn.execute("UPDATE bot_tenants SET agent_of='agent_a' WHERE tenant_key='daren_b'")
        conn.commit(); conn.close()
        keys = db.subtree_keys("agent_a")  # 测试能跑完 = 没死循环
        assert sorted(keys) == ["agent_a", "daren_b"]

    def test_leaf_subtree_is_self(self, botdb):
        assert db.subtree_keys("daren_b") == ["daren_b"]


# ---------- 守卫 3：set_agent_parent 防环 ----------

class TestSetParent:
    def test_self_parent_rejected(self, botdb):
        ok, msg = db.set_agent_parent("agent_a", "agent_a")
        assert not ok and "自己" in msg

    def test_unknown_parent_rejected(self, botdb):
        ok, _ = db.set_agent_parent("daren_b", "ghost_agent")
        assert not ok

    def test_parent_cannot_attach_to_descendant(self, botdb):
        db.set_agent_parent("daren_b", "agent_a")
        ok, msg = db.set_agent_parent("agent_a", "daren_b")
        assert not ok and "环" in msg, "代理挂到自己下级名下必须被拒（防环）"

    def test_detach_with_empty(self, botdb):
        db.set_agent_parent("daren_b", "agent_a")
        ok, _ = db.set_agent_parent("daren_b", "")
        assert ok
        assert db.get_tenant("daren_b")["agent_of"] == ""


# ---------- 守卫 4：transfer 单事务 ----------

class TestTransfer:
    def test_happy_path_moves_balance_and_writes_two_ledgers(self, botdb):
        db.grant("agent_a", 100)  # 500(开户) + 100 = 600
        ok, from_new, msg = db.transfer("agent_a", "daren_b", 60)
        assert ok, msg
        assert from_new == 540
        assert db.get_balance("agent_a") == 540
        assert db.get_balance("daren_b") == 560, "收款方 = 开户 500 + 转入 60"
        conn = _conn(botdb)
        rows = conn.execute(
            "SELECT tenant_key, action, cost, balance_after FROM bot_credits_ledger "
            "WHERE action IN ('transfer_out','transfer_in') ORDER BY created_at").fetchall()
        conn.close()
        assert len(rows) == 2
        out = next(r for r in rows if r["action"] == "transfer_out")
        inn = next(r for r in rows if r["action"] == "transfer_in")
        assert out["tenant_key"] == "agent_a" and out["cost"] == 60
        assert inn["tenant_key"] == "daren_b" and inn["cost"] == -60

    def test_insufficient_balance_rolls_back(self, botdb):
        db.grant("agent_a", 10)  # 500(开户) + 10 = 510
        ok, bal, msg = db.transfer("agent_a", "daren_b", 999)
        assert not ok
        assert bal == 510
        assert db.get_balance("agent_a") == 510
        assert db.get_balance("daren_b") == 500, "余额不足时收款方一分不许动"
        conn = _conn(botdb)
        n = conn.execute("SELECT COUNT(*) FROM bot_credits_ledger "
                         "WHERE action LIKE 'transfer%'").fetchone()[0]
        conn.close()
        assert n == 0, "余额不足却落了流水（单事务被拆穿的证据）"

    def test_non_positive_and_self_rejected(self, botdb):
        assert not db.transfer("agent_a", "daren_b", 0)[0]
        assert not db.transfer("agent_a", "daren_b", -5)[0]
        assert not db.transfer("agent_a", "agent_a", 10)[0]


# ---------- 守卫 5：agent_enabled 授予权 ----------

class TestAgentEnabled:
    def test_round_trip(self, botdb):
        assert not db.is_agent("daren_b")
        db.set_agent_enabled("daren_b", True)
        assert db.is_agent("daren_b")
        assert db.get_tenant("daren_b")["role"] == "daren", "升降代理不许动人格"
        db.set_agent_enabled("daren_b", False)
        assert not db.is_agent("daren_b")


# ---------- 守卫 6：人格/层级正交静态扫描（§8.3 拍板锁） ----------

def _names_in(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} | \
           {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}


def test_agent_fields_never_mixed_with_role_in_boolean_expr():
    """agent_of/agent_enabled 与 role（人格）不许出现在同一个布尔判定里。
    变异对照：把「代理且人格为 daren」写成单条 if 判定 ⇒ 必红。"""
    AGENT = {"agent_of", "agent_enabled"}
    offenders = []
    for path in sorted(BOT_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.BoolOp):
                continue
            names = _names_in(node)
            if names & AGENT and "role" in names:
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, \
        "人格与层级混在同一判定（正交锁被破）：" + "; ".join(offenders)
