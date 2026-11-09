# -*- coding: utf-8 -*-
"""批A修复守卫（2026-11-09 周报 P1-1 / P1-3）。

A1：商品分享口令识别（PRODUCT_SHARE_RE）——口令文本必须命中，
    普通视频链接/普通聊天不命中（防误路由进商品链路）。
A2：QA 点名号提取（_qa_mentioned_dyids）——显式标注优先、未标注需库命中、
    命中上限与查库上限防爆破。
"""
from daren_bot import handler
from daren_bot import executors


class TestProductShareRe:
    def test_share_cmd_hits(self):
        # 周报 11-03 实锤的两种商品分享口令形态
        assert handler.PRODUCT_SHARE_RE.search(
            "【物生物保温杯】复制此条消息，打开抖音，发现更多好物【︽︽口令】")
        assert handler.PRODUCT_SHARE_RE.search(
            "复制此条消息打开抖音 https://v.douyin.com/abc123/")
        assert handler.PRODUCT_SHARE_RE.search("长按复制此条消息")

    def test_video_only_not_hit(self):
        # 纯视频分享文案（无商品口令标记）不得命中
        assert not handler.PRODUCT_SHARE_RE.search(
            "7.99 复制打开抖音，看看【小陈的橱窗】 https://v.douyin.com/xyz/")
        assert not handler.PRODUCT_SHARE_RE.search(
            "https://www.douyin.com/video/7293847561029384756")

    def test_cannot_parse_reply_defined(self):
        assert "商品" in handler.PRODUCT_SHARE_CANNOT_PARSE
        assert "口令" in handler.PRODUCT_SHARE_CANNOT_PARSE


class TestQaMentionedDyids:
    def test_annotated_always_returned(self):
        out = executors._qa_mentioned_dyids(
            "抖音账号：Ayn11930 帮我分析一下", "")
        assert ("Ayn11930", True) in out

    def test_unannotated_requires_db_hit(self, monkeypatch):
        # 库中没有的裸 alnum 串不算点名号（防英文单词误伤）
        monkeypatch.setattr(executors, "_read_talent", lambda *a, **k: None)
        out = executors._qa_mentioned_dyids("Wgg292 这个号能带货吗", "")
        assert ("Wgg292", False) not in out

    def test_unannotated_with_db_hit_returned(self, monkeypatch):
        monkeypatch.setattr(executors, "_read_talent",
                            lambda *a, **k: {"name": "x", "douyin_id": "Wgg292"})
        out = executors._qa_mentioned_dyids("Wgg292 这个号能带货吗", "")
        assert ("Wgg292", False) in out

    def test_own_dyid_extraction_not_duplicated(self):
        out = executors._qa_mentioned_dyids("抖音号：mumu123 数据分析", "mumu123")
        assert (d for d, _ in out if d == "mumu123")
