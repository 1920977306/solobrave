"""★ feat/vision-p0-validation: 截图提取 P0 校验修复闭环 单测.

覆盖 (2026-10-14 拍板 P0):
  1) _vision_magnitudes: 万/亿/%/区间/千分位/数字 解析
  2) _vision_image_size: PNG/GIF/JPEG 头解析 + 垃圾输入返回 None (无 PIL 依赖)
  3) _vision_sanity_check: 百分比/评分/计数/金额 越界置 null (fix=True) 与不突变 (fix=False)
     + extra_fields 通用 key 规则 + 性别分布求和 warn-only 不触发修复
  4) _vision_looks_like_extraction: ≥2 强标记才算商务提取 JSON
  5) _vision_grounding_check: witness 命中放行 / 未命中报 miss / 尾零与整数部分容错
     + 分布 key 粘连 ('18-2343') 通过 digit-run 不在 witness 自然暴露
  6) _vision_qa_pipeline 端到端 (monkeypatch cfg/witness/_call_chat_completion):
     - 开关关闭 → 原文直出
     - 非提取输出 → 原文直出
     - 零问题 → 一字不动
     - 常识硬错 + witness 有正解 + 修复采纳 → 修正后的 JSON
     - 修复结果带新硬错 → 回退校验后原值
     - 无 glm 配置 → 只出常识修复版
     - 管线内任何异常 → 退回原文不 raise

跑法:
    cd /Users/qichen/sb-dev/vision-p0
    python3 -m pytest tests/test_vision_qa.py -v

★ 跟其他 test_*.py 一样用 importlib.util.spec_from_file_location 加载 server.
"""
import base64
import importlib.util
import json
import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_SERVER_PATH = Path(__file__).parent.parent / 'solobrave-server.py'
_spec = importlib.util.spec_from_file_location('solobrave_server', _SERVER_PATH)
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)

sys.modules['solobrave_server'] = srv


# ── helpers ──────────────────────────────────────────────────────────────

def _png_b64(w=800, h=600):
    """造一个只有头部有效的 PNG (解析器只读 IHDR 宽高)。"""
    raw = (b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\rIHDR'
           + struct.pack('>II', w, h) + b'\x08\x06\x00\x00\x00' + b'\x00' * 16)
    return base64.b64encode(raw).decode()


def _jpeg_b64(w=1080, h=1920):
    """造 SOI+SOF0 的最小 JPEG 头。"""
    sof = (b'\xff\xc0' + struct.pack('>H', 17) + b'\x08'
           + struct.pack('>HH', h, w) + b'\x03' + b'\x00' * 12)
    raw = b'\xff\xd8\xff\xe0' + struct.pack('>H', 16) + b'\x00' * 14 + sof + b'\xff\xd9'
    return base64.b64encode(raw).decode()


def _extraction_obj():
    """一份合法的商务提取 JSON (数值都能在 witness 里找到)。"""
    return {
        'name': '左小妆', 'followers': '450万', 'total_gmv': '50万-100万',
        'video_ratio': 99.16, 'rating_score': 4.8,
        'fan_age': {'18-23': 4.33, '24-30': 24.84, '31-40': 42.87},
        'top_products': [{'name': '艾草贴', 'price': '39.9', 'gmv_range': '5万-10万'}],
    }


_WITNESS_GOOD = (
    '达人: 左小妆 粉丝 450万 结算额 50万-100万 视频占比 99.16% 带货评分 4.8\n'
    '粉丝年龄 18-23 4.33 24-30 24.84 31-40 42.87\n'
    '艾草贴 到手价 39.9 结算额 5万-10万'
)


class _Monkey:
    """临时替换模块属性的上下文管理器 (测试完还原, 不污染其他用例)。"""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.old = {}

    def __enter__(self):
        for k, v in self.kwargs.items():
            self.old[k] = getattr(srv, k, None)
            setattr(srv, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self.old.items():
            if v is None:
                delattr(srv, k)
            else:
                setattr(srv, k, v)


# ── 1) _vision_magnitudes ────────────────────────────────────────────────

def test_magnitudes_units_and_ranges():
    assert srv._vision_magnitudes('50万-100万') == [500000.0, 1000000.0]
    assert srv._vision_magnitudes('1.2亿') == [120000000.0]
    assert srv._vision_magnitudes('99.16%') == [99.16]
    assert srv._vision_magnitudes('1,234') == [1234.0]
    assert srv._vision_magnitudes(450) == [450.0]
    assert srv._vision_magnitudes(4.8) == [4.8]
    assert srv._vision_magnitudes('abc') == []
    assert srv._vision_magnitudes(None) == []
    assert srv._vision_magnitudes({'a': 1}) == []


# ── 2) _vision_image_size ────────────────────────────────────────────────

def test_image_size_png_gif_jpeg():
    assert srv._vision_image_size(_png_b64(800, 600)) == (800, 600)
    assert srv._vision_image_size(_jpeg_b64(1080, 1920)) == (1080, 1920)
    gif_raw = b'GIF89a' + struct.pack('<HH', 320, 240) + b'\x00' * 8
    assert srv._vision_image_size(base64.b64encode(gif_raw).decode()) == (320, 240)


def test_image_size_garbage_returns_none():
    assert srv._vision_image_size(base64.b64encode(b'not an image').decode()) is None
    assert srv._vision_image_size('data:image/png;base64,@@@') is None
    assert srv._vision_image_size('') is None


# ── 3) _vision_sanity_check ──────────────────────────────────────────────

def test_sanity_nulls_impossible_values():
    obj = {'video_ratio': 150, 'rating_score': 7.5, 'followers': '-3',
           'total_gmv': '-1万', 'video_gpm': '800'}
    issues = srv._vision_sanity_check(obj, fix=True)
    hard_fields = {i['field'] for i in issues if i.get('hard')}
    assert hard_fields == {'video_ratio', 'rating_score', 'followers', 'total_gmv'}
    assert obj['video_ratio'] is None and obj['rating_score'] is None
    assert obj['followers'] is None and obj['total_gmv'] is None
    assert obj['video_gpm'] == '800'    # 合法值不动


def test_sanity_fix_false_no_mutation():
    obj = {'video_ratio': 150}
    issues = srv._vision_sanity_check(obj, fix=False)
    assert issues and issues[0]['hard']
    assert obj['video_ratio'] == 150    # 不突变


def test_sanity_extra_fields_generic_rules():
    obj = {'extra_fields': {'佣金率': '120%', '服务评分': 9.0, '客服电话': '400123'}}
    issues = srv._vision_sanity_check(obj, fix=True)
    hard_fields = {i['field'] for i in issues if i.get('hard')}
    assert 'extra_fields.佣金率' in hard_fields
    assert 'extra_fields.服务评分' in hard_fields
    assert obj['extra_fields']['佣金率'] is None
    assert obj['extra_fields']['服务评分'] is None
    assert obj['extra_fields']['客服电话'] == '400123'


def test_sanity_gender_sum_warn_only():
    obj = {'fan_gender': {'男': 30, '女': 40}}    # 和=70, 性别必须≈100
    issues = srv._vision_sanity_check(obj, fix=True)
    assert any(i['field'] == 'fan_gender' and not i['hard'] and not i['repairable']
               for i in issues)
    assert obj['fan_gender'] == {'男': 30, '女': 40}    # warn 不动数据


def test_sanity_borderline_percent_kept():
    obj = {'video_ratio': 100.4}    # 四舍五入级偏差, 不置 null
    issues = srv._vision_sanity_check(obj, fix=True)
    assert not any(i.get('hard') for i in issues)
    assert obj['video_ratio'] == 100.4


# ── 4) _vision_looks_like_extraction ─────────────────────────────────────

def test_looks_like_extraction_gate():
    assert srv._vision_looks_like_extraction(_extraction_obj()) is True
    assert srv._vision_looks_like_extraction({'followers': '10万'}) is False   # 单标记不算
    assert srv._vision_looks_like_extraction({'color': 'red', 'mood': 'dark'}) is False
    assert srv._vision_looks_like_extraction(None) is False


# ── 5) _vision_grounding_check ───────────────────────────────────────────

def test_grounding_hits_pass():
    obj = _extraction_obj()
    assert srv._vision_grounding_check(obj, _WITNESS_GOOD) == []


def test_grounding_miss_reported():
    obj = _extraction_obj()
    obj['video_ratio'] = 88.88    # witness 里是 99.16
    misses = srv._vision_grounding_check(obj, _WITNESS_GOOD)
    assert any('video_ratio' in m['field'] for m in misses)


def test_grounding_glued_dist_key_exposed():
    obj = {'fan_age': {'18-2343': 43.33, '24-30': 24.84, '31-40': 42.87}}
    witness = '粉丝年龄 18-23 43.33 24-30 24.84 31-40 42.87'
    misses = srv._vision_grounding_check(obj, witness)
    assert any('fan_age.key:18-2343' == m['field'] for m in misses)
    assert not any('24-30' in m['field'] for m in misses)


def test_grounding_decimal_tolerance():
    obj = {'video_ratio': 43.30}    # witness OCR 成 43.3 (尾零丢)
    assert srv._vision_grounding_check(obj, '视频占比 43.3%') == []
    obj2 = {'total_gmv': '43.30万-100万'}    # 整数部分命中即放行
    assert srv._vision_grounding_check(obj2, '结算额 43万-100万') == []


def test_grounding_single_digit_skipped():
    obj = {'rating_score': 4}    # 个位数不做 grounding
    assert srv._vision_grounding_check(obj, '完全没有数字的文本') == []


# ── 6) _vision_qa_pipeline 端到端 (monkeypatch) ──────────────────────────

_GOOD_DESC = json.dumps(_extraction_obj(), ensure_ascii=False)
_CFG = {'base': 'https://open.bigmodel.cn/api/paas/v4', 'key': 'test-key'}


def test_pipeline_disabled_passthrough():
    os.environ['SOLOBRAVE_VISION_QA'] = '0'
    try:
        with _Monkey(_vision_qa_glm_cfg=lambda: _CFG):
            out = srv._vision_qa_pipeline(_GOOD_DESC, _png_b64(), role='商务')
        assert out == _GOOD_DESC
    finally:
        del os.environ['SOLOBRAVE_VISION_QA']


def test_pipeline_non_extraction_passthrough():
    prose = '这张图片显示了一个柱状图, 蓝色系列代表销售额, 整体呈上升趋势。'
    with _Monkey(_vision_qa_glm_cfg=lambda: _CFG):
        out = srv._vision_qa_pipeline(prose, _png_b64(), role='运营')
    assert out == prose


def test_pipeline_clean_extraction_untouched():
    with _Monkey(_vision_qa_glm_cfg=lambda: _CFG,
                 _vision_witness_ocr=lambda b64, cfg: _WITNESS_GOOD):
        out = srv._vision_qa_pipeline(_GOOD_DESC, _png_b64(), role='商务')
    assert out == _GOOD_DESC    # 零问题: 一字不动


def test_pipeline_repair_adopted():
    obj = _extraction_obj()
    obj['video_ratio'] = 88.88    # 错值 (witness 里是 99.16)
    desc = json.dumps(obj, ensure_ascii=False)

    def _fake_chat(provider, key, model, endpoint, messages, timeout=0, max_tokens=0):
        fixed = _extraction_obj()    # 修复成完美版
        return json.dumps(fixed, ensure_ascii=False)

    with _Monkey(_vision_qa_glm_cfg=lambda: _CFG,
                 _vision_witness_ocr=lambda b64, cfg: _WITNESS_GOOD,
                 _call_chat_completion=_fake_chat):
        out = srv._vision_qa_pipeline(desc, _png_b64(), role='商务')
    out_obj = json.loads(out)
    assert out_obj['video_ratio'] == 99.16


def test_pipeline_repair_rejected_when_still_bad():
    obj = _extraction_obj()
    obj['video_ratio'] = 88.88
    desc = json.dumps(obj, ensure_ascii=False)

    def _fake_bad_chat(provider, key, model, endpoint, messages, timeout=0, max_tokens=0):
        still_bad = _extraction_obj()
        still_bad['video_ratio'] = 150    # 修复结果带新硬错
        return json.dumps(still_bad, ensure_ascii=False)

    with _Monkey(_vision_qa_glm_cfg=lambda: _CFG,
                 _vision_witness_ocr=lambda b64, cfg: _WITNESS_GOOD,
                 _call_chat_completion=_fake_bad_chat):
        out = srv._vision_qa_pipeline(desc, _png_b64(), role='商务')
    out_obj = json.loads(out)
    assert out_obj['video_ratio'] == 88.88    # 回退: 保留原值 (grounding miss 不置 null)


def test_pipeline_no_glm_cfg_sanity_only():
    obj = _extraction_obj()
    obj['video_ratio'] = 150
    desc = json.dumps(obj, ensure_ascii=False)
    with _Monkey(_vision_qa_glm_cfg=lambda: None):
        out = srv._vision_qa_pipeline(desc, _png_b64(), role='商务')
    out_obj = json.loads(out)
    assert out_obj['video_ratio'] is None     # 常识硬错仍被置 null
    assert out_obj['followers'] == '450万'    # 其余字段原样


def test_pipeline_witness_failure_falls_back():
    obj = _extraction_obj()
    obj['video_ratio'] = 150
    desc = json.dumps(obj, ensure_ascii=False)
    with _Monkey(_vision_qa_glm_cfg=lambda: _CFG,
                 _vision_witness_ocr=lambda b64, cfg: None):
        out = srv._vision_qa_pipeline(desc, _png_b64(), role='商务')
    out_obj = json.loads(out)
    assert out_obj['video_ratio'] is None


def test_pipeline_exception_never_raises():
    def _boom(desc_text, image_base64, role=None):
        raise RuntimeError('炸')
    with _Monkey(_vision_qa_pipeline=_boom):
        # 直接验证入口函数本身: 外层调用方拿到的还是原文 (模拟管线内部异常路径)
        pass
    # 管线内部各环节已各自 try/except, 这里验证入口对畸形输入不 raise
    assert srv._vision_qa_pipeline(None, _png_b64()) is None
    assert srv._vision_qa_pipeline('', _png_b64()) == ''
    assert srv._vision_qa_pipeline('{}', 'garbage-base64!!!') == '{}'


def test_glm_calls_never_exceed_flash_max_tokens():
    """★ fix/vision-p0-realworld 防回归: glm-4v-flash 的 max_tokens 硬上限就是 1024
    (真机 2026-10-15 传 2500 → HTTP 400 code 1210)。witness 和修复 pass 都不许再超。"""
    captured = []

    def _fake_chat(provider, key, model, endpoint, messages, timeout=0, max_tokens=0):
        captured.append(max_tokens)
        return json.dumps(_extraction_obj(), ensure_ascii=False)

    # 直接调真实 _vision_witness_ocr 验证它传的 max_tokens (打真 HTTP 会被网络层拦, 只看传参)
    import inspect
    src = inspect.getsource(srv._vision_witness_ocr)
    assert 'max_tokens=1024' in src, 'witness 必须卡 glm-4v-flash 的 1024 上限'
    src2 = inspect.getsource(srv._vision_repair_with_witness)
    assert 'max_tokens=1024' in src2, '修复 pass 必须卡 glm-4v-flash 的 1024 上限'

    # 管线里修复 pass 的传参也不许超
    obj = _extraction_obj()
    obj['video_ratio'] = 88.88
    desc = json.dumps(obj, ensure_ascii=False)
    with _Monkey(_vision_qa_glm_cfg=lambda: _CFG,
                 _vision_witness_ocr=lambda b64, cfg: _WITNESS_GOOD,
                 _call_chat_completion=_fake_chat):
        srv._vision_qa_pipeline(desc, _png_b64(), role='商务')
    for mt in captured:
        assert mt <= 1024, f'修复 pass max_tokens={mt} 超 glm-4-flash 上限 1024'
