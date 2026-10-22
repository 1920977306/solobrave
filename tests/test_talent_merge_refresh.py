"""达人 PUT merge 的「显式刷新派生字段」行为（2026-10-22）。

背景：飞书 Base「报告摘要」永远停在第一次采集的旧文本（写「累计GMV¥10万-25万」），
而同页卡片显示 37.5万 → 同页两个数字打架。

根因：`PUT /api/talents` 的 merge 是「老值非空 + 新值非空 → **保留老值**」
（老大硬约束：严禁覆盖核心数据）⇒ `notes`（画像摘要）第一次采集后**永远冻结**。

修法：body 里带 `__refresh_fields: ["notes", ...]` 时，**白名单内**的字段允许刷新。
**默认行为完全不变**，且有白名单双保险（调用方乱传也刷不动核心事实字段）。

⚠️ 本文件重点是**证明老大的硬约束没被削弱**：
  - 不传 `__refresh_fields` → 行为与修之前**逐位相同**（非空字段仍保留老值）
  - 传了但字段不在白名单 → 仍然刷不动（followers/GMV 等核心事实字段必须安全）
  - 新值为空 → 仍不覆盖（不拿空值冲掉好数据）
"""
import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "solobrave-server.py")

_spec = importlib.util.spec_from_file_location("solobrave_server_merge", SERVER)
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)

merge = srv._merge_talent_only_empty
WHITELIST = srv._TALENT_REFRESHABLE_DERIVED


class TestDefaultBehaviorUnchanged:
    """不声明刷新时，行为必须与修之前逐位一致。"""

    def test_non_empty_existing_is_not_overwritten(self):
        existing = {"followers": 12804, "total_gmv": 375000.0, "notes": "旧摘要"}
        body = {"followers": 99999, "total_gmv": 1.0, "notes": "新摘要"}
        out = merge(existing, body)
        assert out["followers"] == 12804, "核心数据被覆盖了 —— 硬约束被破坏！"
        assert out["total_gmv"] == 375000.0
        assert out["notes"] == "旧摘要", "没声明刷新就不该被覆盖"

    def test_empty_existing_is_filled(self):
        out = merge({"notes": "", "followers": 0}, {"notes": "新摘要", "followers": 12804})
        assert out["notes"] == "新摘要"
        assert out["followers"] == 12804

    def test_empty_new_value_never_overwrites(self):
        """新值为空（None/''/0/{}/[]）→ 一律不覆盖。"""
        for empty in (None, "", 0, {}, [], "{}", "[]"):
            out = merge({"notes": "旧摘要"}, {"notes": empty})
            assert out["notes"] == "旧摘要", f"空值 {empty!r} 覆盖了好数据"

    def test_id_and_updated_at_untouched(self):
        out = merge({"id": "tal_1", "updated_at": 111, "notes": "x"},
                    {"id": "tal_2", "updated_at": 222, "notes": "y"})
        assert out["id"] == "tal_1"
        assert out["updated_at"] == 111


class TestExplicitRefresh:
    """显式声明刷新：白名单内字段可覆盖，白名单外仍然刷不动。"""

    def test_whitelisted_field_is_refreshed(self):
        existing = {"notes": "旧摘要（10-16）", "followers": 12804}
        body = {"notes": "新摘要（10-22）", "__refresh_fields": ["notes"]}
        out = merge(existing, body)
        assert out["notes"] == "新摘要（10-22）"

    def test_non_whitelisted_field_cannot_be_refreshed(self):
        """⚠️ 双保险的核心：调用方就算传了 followers，也刷不动。"""
        existing = {"followers": 12804, "total_gmv": 375000.0}
        body = {"followers": 99999, "total_gmv": 1.0,
                "__refresh_fields": ["followers", "total_gmv"]}
        out = merge(existing, body)
        assert out["followers"] == 12804, "核心事实字段被绕过了 —— 硬约束被破坏！"
        assert out["total_gmv"] == 375000.0

    def test_empty_new_value_still_does_not_overwrite_even_when_refreshing(self):
        out = merge({"notes": "旧摘要"},
                    {"notes": "", "__refresh_fields": ["notes"]})
        assert out["notes"] == "旧摘要", "声明刷新也不能拿空值冲掉好数据"

    def test_refresh_flag_is_not_stored_as_data(self):
        out = merge({"notes": "x"}, {"notes": "y", "__refresh_fields": ["notes"]})
        assert "__refresh_fields" not in out, "控制字段不能混进数据里"

    def test_whitelist_excludes_core_fact_fields(self):
        """白名单里绝不能出现核心事实字段。"""
        for danger in ("followers", "total_gmv", "video_gpm", "live_gpm",
                       "rating_score", "average_price", "total_products"):
            assert danger not in WHITELIST, f"{danger} 是核心事实字段，不该在刷新白名单里"

    def test_whitelist_contains_the_summary_fields(self):
        for need in ("notes", "ai_summary", "ai_analysis"):
            assert need in WHITELIST, f"{need} 是派生字段，应该允许刷新"


class TestRefreshFieldNameIsAccepted:
    def test_string_form_is_accepted(self):
        out = merge({"notes": "旧"}, {"notes": "新", "__refresh_fields": "notes"})
        assert out["notes"] == "新"

    def test_unknown_field_name_is_ignored(self):
        out = merge({"notes": "旧"}, {"notes": "新", "__refresh_fields": ["不存在"]})
        assert out["notes"] == "旧"
