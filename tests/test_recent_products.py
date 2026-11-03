"""backlog ⑥「近期带的品」承接 + ⑦缓存/实查口径合并（fix/豆鲨包承接质量 2026-11-03）。

⑥ 生产实锤（豆鲨包，2026-11-02 17:06）：达人问「我账号近期带的品 你知道有哪些吗」
   被当成普通重采集 —— 扣 100 分、等 5-8 分钟，收到的却是通用账号报告，
   没有答她的具体问题。修法：达人角色 + 自己绑定的号 + 问法命中
   RECENT_PRODUCTS_RE ⇒ `_answer_recent_products` 纯读库秒答
   （0 积分、不入队、不调 LLM）；档案 >7 天或没有明细才回落采集链路。

⑦ 口径打架：同一商品 7 天内结论缓存命中秒答🟢能带，过期后实查平台
   查无此品 → 退积分答「已下架」。修法：实查 ProductNotFound 时若库里有
   过期结论 ⇒ 合并回答（明示旧分析 + 截图兜底），任务算成功不退费；
   没有旧结论才维持原失败退费路径。

红线：
  - 秒答路径必须 0 积分、不调 LLM、不入队（无消耗算力）；
  - 合并口径必须向达人明示「这是旧分析」，不许伪装成新结论；
  - 没有旧结论时行为与修复前逐位一致（照样抛 ProductNotFound）。
"""
import json
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from daren_bot import executors as ex
from daren_bot import handler as hd


# ---------------------------------------------------------------- ⑥ 纯函数

class TestRecentProductItems:
    def test_product_details_normal_render(self):
        t = {"ocr_raw_fields": json.dumps({
            "product_details": [
                {"cells": ["商品", "销量", "结算额"]},                # 表头
                {"cells": ["2026/11/01", "凉拖", "1,234件", "¥5.6万"]},  # 日期列跳过
                {"cells": ["草帽", "890件", "¥3.2万"]},
            ]}, ensure_ascii=False)}
        items = ex.recent_product_items(t)
        assert [i["name"] for i in items] == ["凉拖", "草帽"]
        # 其余 cell（含统计日期）作摘要展示
        assert items[0]["extra"] == ["2026/11/01", "1,234件", "¥5.6万"]

    def test_filters_nav_garbage_rows(self):
        t = {"ocr_raw_fields": json.dumps({
            "product_details": [
                {"cells": ["概览", "场景分析", "直播分析", "视频分析"]},
                {"cells": ["概览"]},
                {"cells": ["凉鞋女夏外穿", "500件"]},
            ]}, ensure_ascii=False)}
        items = ex.recent_product_items(t)
        assert [i["name"] for i in items] == ["凉鞋女夏外穿"]

    def test_top_products_fallback(self):
        t = {"top_products": json.dumps(["海边度假人字拖  月销1.2万",
                                          "概览"], ensure_ascii=False)}
        items = ex.recent_product_items(t)
        assert len(items) == 1
        assert "人字拖" in items[0]["name"]

    def test_double_json_encoded(self):
        inner = json.dumps({"product_details": [{"cells": ["沙滩帽", "300件"]}]},
                           ensure_ascii=False)
        t = {"ocr_raw_fields": json.dumps(inner)}
        items = ex.recent_product_items(t)
        assert [i["name"] for i in items] == ["沙滩帽"]

    def test_empty_returns_empty(self):
        assert ex.recent_product_items({}) == []
        assert ex.recent_product_items({"ocr_raw_fields": "{}"}) == []
        assert ex.recent_product_items(None) == []


class TestRecentProductsRe:
    def test_hits_doushabao_original(self):
        assert hd.RECENT_PRODUCTS_RE.search("我账号近期带的品 你知道有哪些吗")
        assert hd.RECENT_PRODUCTS_RE.search("我最近带了哪些货")
        assert hd.RECENT_PRODUCTS_RE.search("我这段时间推过的品有哪些")

    def test_misses_normal_analysis_requests(self):
        assert not hd.RECENT_PRODUCTS_RE.search("帮我分析一下这个号")
        assert not hd.RECENT_PRODUCTS_RE.search("这个账号数据怎么样")
        assert not hd.RECENT_PRODUCTS_RE.search("近期涨粉情况如何")
        assert not hd.RECENT_PRODUCTS_RE.search("")


class FakeSender:
    def __init__(self):
        self.sent = []

    def send_text(self, chat_id, text):
        self.sent.append((chat_id, text))


class TestAnswerRecentProducts:
    def _router(self):
        return hd.Router(ws_manager=None)

    def test_fresh_archive_answers_without_charge(self, monkeypatch):
        now_ms = int(time.time() * 1000)
        t = {"updated_at": now_ms,
             "ocr_raw_fields": json.dumps({
                 "product_details": [{"cells": ["遮阳帽", "2,000件", "¥8万"]},
                                      {"cells": ["凉拖", "1,000件"]}]},
                 ensure_ascii=False)}
        monkeypatch.setattr(ex, "_read_talent", lambda _tid, _dy: t)
        sender = FakeSender()
        ok = self._router()._answer_recent_products(
            {"tenant_key": "tk1"}, "chat1", "62224890070", sender)
        assert ok is True
        assert len(sender.sent) == 1
        text = sender.sent[0][1]
        assert "你近期带的品" in text
        assert "遮阳帽" in text and "凉拖" in text
        assert "不扣积分" in text

    def test_stale_archive_falls_back(self, monkeypatch):
        old_ms = int(time.time() * 1000) - 8 * 86400 * 1000
        t = {"updated_at": old_ms,
             "ocr_raw_fields": json.dumps({
                 "product_details": [{"cells": ["遮阳帽", "2,000件"]}]},
                 ensure_ascii=False)}
        monkeypatch.setattr(ex, "_read_talent", lambda _tid, _dy: t)
        sender = FakeSender()
        ok = self._router()._answer_recent_products(
            {"tenant_key": "tk1"}, "chat1", "dy", sender)
        assert ok is False
        assert sender.sent == []

    def test_no_detail_falls_back(self, monkeypatch):
        t = {"updated_at": int(time.time() * 1000), "ocr_raw_fields": "{}"}
        monkeypatch.setattr(ex, "_read_talent", lambda _tid, _dy: t)
        sender = FakeSender()
        assert self._router()._answer_recent_products(
            {"tenant_key": "tk1"}, "chat1", "dy", sender) is False

    def test_no_talent_row_falls_back(self, monkeypatch):
        monkeypatch.setattr(ex, "_read_talent", lambda _tid, _dy: None)
        sender = FakeSender()
        assert self._router()._answer_recent_products(
            {"tenant_key": "tk1"}, "chat1", "dy", sender) is False


# ---------------------------------------------------------------- ⑦ 合并口径

class TestDelistedWithReference:
    def test_contains_old_verdict_reason_and_age(self):
        created = int(time.time()) - 3 * 86400
        old = {"created_at": created,
               "md": "## 结论\n🟢 能带\n符合你的粉丝画像",
               "why": "佣金率 20% 高于均值"}
        text = ex._delisted_with_reference(old)
        assert "平台查无此品" in text
        assert "3 天前" in text
        assert "能带" in text
        assert "佣金率 20%" in text
        assert "截图" in text

    def test_no_md_verdict_label_path_does_not_crash(self):
        old = {"created_at": 0, "md": "", "verdict": "🟡 慎带", "why": ""}
        text = ex._delisted_with_reference(old)
        assert "平台查无此品" in text
        assert "🟡 慎带" in text


class TestExecuteProductCollectDelisted:
    TASK = {"tenant_key": "tk1", "target": "https://v.douyin.com/abc/",
            "payload": "{}"}

    def test_merged_answer_when_old_verdict_exists(self, monkeypatch):
        def _raise(_url, timeout=240):
            raise ex.ProductNotFound("平台查无此商品")
        monkeypatch.setattr(ex, "collect_product_by_url", _raise)
        old = {"name": "测试商品", "created_at": int(time.time()) - 86400,
               "md": "## 结论\n🟢 能带", "verdict": "🟢 能带", "why": "佣金高"}
        monkeypatch.setattr("daren_bot.db.verdict_cache_get_any",
                            lambda _tk, _pk: old)
        out = json.loads(ex.execute_product_collect(dict(self.TASK)))
        assert out["title"] == "测试商品"
        assert out["stats"] == {}
        assert "平台查无此品" in out["md"]
        assert "能带" in out["md"]

    def test_raises_when_no_old_verdict(self, monkeypatch):
        def _raise(_url, timeout=240):
            raise ex.ProductNotFound("平台查无此商品")
        monkeypatch.setattr(ex, "collect_product_by_url", _raise)
        monkeypatch.setattr("daren_bot.db.verdict_cache_get_any",
                            lambda _tk, _pk: None)
        with pytest.raises(ex.ProductNotFound):
            ex.execute_product_collect(dict(self.TASK))


# ---------------------------------------------------------------- 短链分流

class TestShortLinkRouting:
    """v.douyin.com 短链商品/视频分流（fix/短链商品误判视频 2026-11-03）。

    生产实锤：商品短链（能解出商品ID）被 VIDEO_LINK_RE 抢先判成视频拆解，
    扣 30 分还 failed；视频短链（解析无商品参数）视频链 done。
    """

    def _spy_router(self, monkeypatch, resolved_pid):
        monkeypatch.setattr(hd.product_link, "extract_product_id",
                            lambda _u: resolved_pid)
        router = hd.Router(ws_manager=None)
        calls = {}
        router._route_link = lambda *a: calls.setdefault("link", a)
        router._route_video = lambda *a: calls.setdefault("video", a)
        return router, calls

    def test_product_short_link_goes_to_product(self, monkeypatch):
        router, calls = self._spy_router(monkeypatch, "3848235384622416590")
        router._route_text({"tenant_key": "tk1", "role": "daren"}, "chat1",
                           "https://v.douyin.com/3HXXhXyDS8s/",
                           FakeSender())
        assert "link" in calls and "video" not in calls

    def test_video_short_link_stays_on_video(self, monkeypatch):
        router, calls = self._spy_router(monkeypatch, "")
        router._route_text({"tenant_key": "tk1", "role": "daren"}, "chat1",
                           "https://v.douyin.com/F6DKq9HC_yI/",
                           FakeSender())
        assert "video" in calls and "link" not in calls

    def test_full_video_url_untouched(self, monkeypatch):
        """全链视频分享页（iesdouyin.com/share/video/）不经短链解析，直接视频链。"""
        router, calls = self._spy_router(monkeypatch, "")
        router._route_text(
            {"tenant_key": "tk1", "role": "daren"}, "chat1",
            "https://www.iesdouyin.com/share/video/7702486399988367542/",
            FakeSender())
        assert "video" in calls and "link" not in calls
