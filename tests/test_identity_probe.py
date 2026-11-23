# -*- coding: utf-8 -*-
"""身份探针守卫（dq_bbcb24792baa，2026-11-23）：登录态≠身份态。

矩阵：
1. classify_identity 纯函数：罗盘 URL/达人版角标→daren，团长工作台→captain，判不出→unknown
2. worker 源码锚：/health 带 identity+ready；_refresh_browser_state 刷新 captain_identity
3. preflight ⑨ 消费真值：daren 红 / 浏览器未起免验（互斥常态不误报）
"""
import importlib.util
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    'buyin_auth', os.path.join(ROOT, 'buyin_auth.py'))
ba = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ba)


def test_classify_daren_by_compass_url():
    assert ba.classify_identity(
        'https://buyin.jinritemai.com/dashboard/compass/home', '随便什么文本') == 'daren'
    assert ba.classify_identity(
        'https://buyin.jinritemai.com/api/authorStatData/x', '') == 'daren'


def test_classify_daren_by_badge():
    assert ba.classify_identity(
        'https://buyin.jinritemai.com/dashboard',
        '首页\n达人版（一百）\n罗盘数据') == 'daren'


def test_classify_captain_by_workbench():
    assert ba.classify_identity(
        'https://buyin.jinritemai.com/dashboard',
        '团长工作台\n机构数据\n达人管理') == 'captain'


def test_classify_unknown_when_blind():
    assert ba.classify_identity('', '') == 'unknown'
    assert ba.classify_identity('https://buyin.jinritemai.com/', '加载中…') == 'unknown'
    # 空 URL 罗盘词不炸
    assert ba.classify_identity(None, None) == 'unknown'


def test_classify_url_beats_text():
    """URL 是强信号优先于 DOM 文本（漂移后机构页被重定向到罗盘）。"""
    assert ba.classify_identity(
        'https://buyin.jinritemai.com/compass_api/author/list',
        '团长工作台') == 'daren'


def test_worker_health_exposes_identity():
    src = open(os.path.join(ROOT, 'buyin_worker.py'), encoding='utf-8').read()
    assert "'identity'" in src and "'ready'" in src, '/health 必须带 identity+ready'
    assert 'captain_identity' in src, 'STATE 必须维护 captain_identity'
    assert 'def identity' in src, 'Browser.identity() 必须存在'
    assert 'classify_identity' in src, 'Browser.identity 必须接 buyin_auth 单一真源'


def test_preflight_consumes_identity():
    src = open(os.path.join(ROOT, 'scripts', 'preflight_925.py'),
               encoding='utf-8').read()
    assert 'identity' in src, 'preflight 必须改认身份真值'
    assert 'br_up' in src, '浏览器未起必须免验（互斥常态不误报）'
    assert "'daren'" in src, '漂成达人版必须报红'


def test_health_contract_json_shape():
    """/health 响应契约文档化（键集合锁定，防漂移）。"""
    src = open(os.path.join(ROOT, 'buyin_worker.py'), encoding='utf-8').read()
    seg = src.split("if self._path == '/health'", 1)[1].split('self._json(200, {', 1)[1]
    body = seg.split('})', 1)[0]
    for key in ('browser_up', 'logged_in', 'identity', 'ready'):
        assert f"'{key}'" in body, f'/health 缺键 {key}'
