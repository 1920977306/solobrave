#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单4 feat/shell-quarantine 守卫：空壳隔离区入口三件套（前端按钮/切换函数/API 语义）。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def test_button_present():
    assert 'sb2TalentsShellsBtn' in _read('index.html'), '空壳区按钮缺失'


def test_toggle_function_present():
    js = _read('js/inline-07.js')
    assert 'function sb2TalentsToggleShells()' in js
    assert "params.push('shells=only')" in js, 'load 没带 shells=only 参数'
    assert 'shell_count' in js, '响应里的空壳计数没接'


def test_mutual_exclusion_with_archive():
    js = _read('js/inline-07.js')
    i = js.find('function sb2TalentsToggleShells()')
    body = js[i:i + 900]
    assert '_sb2TalentsShowArchived = false' in body, '空壳模式未与回收站互斥'


def test_api_shells_semantics_still_there():
    server = _read('solobrave-server.py')
    assert "shells == 'only'" in server
    assert 'shell_count' in server, 'API 空壳计数响应字段被移除'
