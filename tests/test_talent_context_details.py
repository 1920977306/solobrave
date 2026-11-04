"""_talent_context 的「近期视频」两态行结构（2026-11-04 百应改版历史内容卡）。

背景：采集链路明细回退到「历史内容」卡后，video_details 的行结构从
auxo-table（cells=[...]）变成平铺字段（title/plays/likes/comments/gmv、cells=[]）。
消费端只认 cells ⇒ 新行静默读不出 ⇒ 画像注入缺「近期视频」。
"""
import json

import daren_bot.db as db_mod
from daren_bot import executors as ex


def _mk_talent(monkeypatch, video_details):
    monkeypatch.setattr(db_mod, "get_tenant",
                        lambda tk: {"bind_douyin_id": "hlb725"})
    monkeypatch.setattr(ex, "_read_talent", lambda _tid, _dy: {
        "name": "洁洁爱户外", "douyin_id": "hlb725",
        "ocr_raw_fields": json.dumps({"video_details": video_details},
                                     ensure_ascii=False),
    })


def test_cells_row_auxo_table(monkeypatch):
    """旧 auxo-table 行（cells 非空）照旧消费。"""
    _mk_talent(monkeypatch, [{
        "title": "", "cells": ["2026/11/01\n视频标题A", "1.2万", "300"],
    }])
    ctx = ex._talent_context("t1")
    assert "近期视频" in ctx
    # 旧结构语义 = 首行（日期）+ 前两列指标（既有行为，勿改）
    assert "2026/11/01" in ctx and "1.2万" in ctx


def test_history_card_row_flat_fields(monkeypatch):
    """新「历史内容」卡行：cells 为空，title+平铺指标 ⇒ 照样进画像。"""
    _mk_talent(monkeypatch, [{
        "title": "格雷系加厚摇粒绒卫裤", "cells": [],
        "plays": "1.2万", "likes": "300", "comments": "20", "gmv": "-",
    }])
    ctx = ex._talent_context("t1")
    assert "近期视频" in ctx
    assert "格雷系加厚摇粒绒卫裤" in ctx
    assert "观看1.2万" in ctx
    assert "点赞300" in ctx
    # gmv 为 '-'（无数据）不得进文本
    assert "结算-" not in ctx


def test_history_card_row_empty_all_skipped(monkeypatch):
    """新行连 title 都没有 ⇒ 跳过该行，不造空壳。"""
    _mk_talent(monkeypatch, [{"title": "", "cells": []}])
    ctx = ex._talent_context("t1")
    assert "近期视频" not in ctx
