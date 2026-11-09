# -*- coding: utf-8 -*-
"""buyin_worker 入队去重守卫（2026-11-09 认领 409 根因修复）。

事故：/collect 控制口 INSERT 后立即 submit_collect，queue_loop 每 POLL_INTERVAL
扫 db_pending()——任务在队列里排队期间 DB 行仍是 'pending'，被反复再入队。
浏览器线程串行：第一个实例正常采集到 done，孪生实例 claim 撞旧状态 →
服务端 409 → 孪生体把内存任务表覆盖成「认领失败: HTTP Error 409: Conflict」
（11-08 20:13 / 22:13 周报实锤）。

守卫目标：同一 collect_id 重复 submit_collect 只入队一次；
不同 id 不受影响。探针必须能失败——直接数 JOB_Q 深度。
"""
import uuid

import buyin_worker as bw


def _cid():
    return f'tcr_test_{uuid.uuid4().hex[:12]}'


def test_same_cid_enqueued_once():
    cid = _cid()
    before = bw.JOB_Q.qsize()
    bw.submit_collect({'id': cid, 'name': 'x', 'douyin_id': 'y'})
    bw.submit_collect({'id': cid, 'name': 'x', 'douyin_id': 'y'})
    bw.submit_collect({'id': cid, 'name': 'x', 'douyin_id': 'y'})  # 模拟 queue_loop 第三轮扫到同一 pending 行
    assert bw.JOB_Q.qsize() == before + 1, '同一 collect_id 重复入队 → 孪生 claim 409 复发'


def test_different_cids_all_enqueued():
    before = bw.JOB_Q.qsize()
    ids = [_cid() for _ in range(3)]
    for cid in ids:
        bw.submit_collect({'id': cid, 'name': 'x', 'douyin_id': 'y'})
    assert bw.JOB_Q.qsize() == before + 3, '不同 collect_id 被误去重 → 任务丢失'


def test_no_id_row_not_deduped():
    """不带 id 的内部指令行（如打开浏览器开关）不受去重影响。"""
    before = bw.JOB_Q.qsize()
    bw.submit_collect({'_open_browser': True})
    bw.submit_collect({'_open_browser': True})
    assert bw.JOB_Q.qsize() == before + 2
