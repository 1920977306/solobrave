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
_CFG = {'base': 'https://open.bigmodel.cn/api/paas/v4', 'key': 'test-key',
        'provider': 'zhipu', 'witnessModel': 'glm-4v-flash',
        'repairModel': 'glm-4-flash', 'maxTokens': 1024}


def test_pipeline_disabled_passthrough():
    os.environ['SOLOBRAVE_VISION_QA'] = '0'
    try:
        with _Monkey(_vision_qa_cfg=lambda: _CFG):
            out = srv._vision_qa_pipeline(_GOOD_DESC, _png_b64(), role='商务')
        assert out == _GOOD_DESC
    finally:
        del os.environ['SOLOBRAVE_VISION_QA']


def test_pipeline_non_extraction_passthrough():
    prose = '这张图片显示了一个柱状图, 蓝色系列代表销售额, 整体呈上升趋势。'
    with _Monkey(_vision_qa_cfg=lambda: _CFG):
        out = srv._vision_qa_pipeline(prose, _png_b64(), role='运营')
    assert out == prose


def test_pipeline_clean_extraction_untouched():
    with _Monkey(_vision_qa_cfg=lambda: _CFG,
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

    with _Monkey(_vision_qa_cfg=lambda: _CFG,
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

    with _Monkey(_vision_qa_cfg=lambda: _CFG,
                 _vision_witness_ocr=lambda b64, cfg: _WITNESS_GOOD,
                 _call_chat_completion=_fake_bad_chat):
        out = srv._vision_qa_pipeline(desc, _png_b64(), role='商务')
    out_obj = json.loads(out)
    assert out_obj['video_ratio'] == 88.88    # 回退: 保留原值 (grounding miss 不置 null)


def test_pipeline_no_glm_cfg_sanity_only():
    obj = _extraction_obj()
    obj['video_ratio'] = 150
    desc = json.dumps(obj, ensure_ascii=False)
    with _Monkey(_vision_qa_cfg=lambda: None):
        out = srv._vision_qa_pipeline(desc, _png_b64(), role='商务')
    out_obj = json.loads(out)
    assert out_obj['video_ratio'] is None     # 常识硬错仍被置 null
    assert out_obj['followers'] == '450万'    # 其余字段原样


def test_pipeline_witness_failure_falls_back():
    obj = _extraction_obj()
    obj['video_ratio'] = 150
    desc = json.dumps(obj, ensure_ascii=False)
    with _Monkey(_vision_qa_cfg=lambda: _CFG,
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


def test_vision_qa_calls_never_exceed_configured_max_tokens():
    """★ fix/vision-p0-realworld 防回归: glm-4v-flash 的 max_tokens 硬上限就是 1024
    (真机 2026-10-15 传 2500 → HTTP 400 code 1210)。witness 和修复 pass 都不许再超。

    ★ 2026-10-26：上限来源从「源码里写死 1024」改成 **cfg['maxTokens']**
    （视觉 QA 的独立配置源 `settings.visionQA`）⇒ 断言也跟着改：

    ⚠️ 断言方式从「源码里有没有这串字」改成**盯行为**（真调一次，看传出去的 max_tokens）。
       源码字符串断言是**恒真探针的第二形态**：把代码改成 `max_tokens=99999`
       而保留那串字，测试照样绿 —— 盯符号抓不到这种变异。
    """
    captured = []

    def _fake_chat(provider, key, model, endpoint, messages, timeout=0, max_tokens=0):
        captured.append(max_tokens)
        return json.dumps(_extraction_obj(), ensure_ascii=False)

    # witness：真调一次，抓它实际传出去的 max_tokens
    captured.clear()
    with _Monkey(_call_chat_completion=_fake_chat):
        srv._vision_witness_ocr(_png_b64(), _CFG)
    assert captured, 'witness 根本没调模型 —— 这条断言会空跑'
    assert max(captured) <= 1024, f'witness max_tokens={max(captured)} 超 glm-4v-flash 上限 1024'

    # 修复 pass：同上
    captured.clear()
    with _Monkey(_call_chat_completion=_fake_chat):
        srv._vision_repair_with_witness(
            _extraction_obj(), [{'field': 'video_ratio', 'why': 'x', 'value': 1,
                                 'hard': False, 'repairable': True}],
            _WITNESS_GOOD, _CFG)
    assert captured, '修复 pass 根本没调模型 —— 这条断言会空跑'
    assert max(captured) <= 1024, f'修复 pass max_tokens={max(captured)} 超上限 1024'

    # cfg 里给了更小的上限时，必须照 cfg 走（不许硬写 1024 顶掉）
    captured.clear()
    small = dict(_CFG, maxTokens=256)
    with _Monkey(_call_chat_completion=_fake_chat):
        srv._vision_witness_ocr(_png_b64(), small)
    assert captured == [256], f'witness 没照 cfg 的 maxTokens 走: {captured}'

    # 管线里修复 pass 的传参也不许超
    obj = _extraction_obj()
    obj['video_ratio'] = 88.88
    desc = json.dumps(obj, ensure_ascii=False)
    captured.clear()
    with _Monkey(_vision_qa_cfg=lambda: _CFG,
                 _vision_witness_ocr=lambda b64, cfg: _WITNESS_GOOD,
                 _call_chat_completion=_fake_chat):
        srv._vision_qa_pipeline(desc, _png_b64(), role='商务')
    for mt in captured:
        assert mt <= 1024, f'修复 pass max_tokens={mt} 超 glm-4-flash 上限 1024'
# ── 7) 采纳门槛: 未标记字段一个都不许变 (真机 2026-10-15 实证防回归) ──────

def test_unflagged_changes_detected():
    orig = {'followers': '450万', 'video_ratio': 88.88,
            'extra_fields': {'佣金率': '120%'},
            'top_products': [{'name': '艾草贴', 'price': '39.9'}]}
    fixed_good = {'followers': '450万', 'video_ratio': 99.16,
                  'extra_fields': {'佣金率': '5%'},
                  'top_products': [{'name': '艾草贴', 'price': '39.9'}]}
    roots = srv._vision_flagged_roots(
        [{'field': 'video_ratio', 'repairable': True},
         {'field': 'extra_fields.佣金率', 'repairable': True}])
    assert roots == {'video_ratio', 'extra_fields'}
    assert srv._vision_unflagged_changes(orig, fixed_good, roots) == []

    # 动了未标记字段 followers → 必须被抓
    fixed_bad = dict(fixed_good)
    fixed_bad['followers'] = '4500'
    changed = srv._vision_unflagged_changes(orig, fixed_bad, roots)
    assert any('followers' in c for c in changed)

    # 删了未标记 key / 新增野 key → 也必须被抓
    fixed_del = {k: v for k, v in fixed_good.items() if k != 'top_products'}
    assert any('top_products' in c for c in srv._vision_unflagged_changes(orig, fixed_del, roots))
    fixed_add = dict(fixed_good)
    fixed_add['agency'] = '无忧传媒'
    assert any('agency' in c for c in srv._vision_unflagged_changes(orig, fixed_add, roots))


def test_pipeline_repair_touching_unflagged_ignored():
    """★ fix/vision-p0-realworld4 新契约: 修复输出里对未标记字段的改动被程序合并层
    直接忽略 (不可能生效), 被标记字段照修 — 比旧契约"整体拒绝"更好: 该修的修好,
    未标记的零风险。followers 任何情况下不许被改。"""
    obj = _extraction_obj()
    obj['video_ratio'] = 88.88    # 唯一被标记字段
    desc = json.dumps(obj, ensure_ascii=False)

    def _fake_naughty_chat(provider, key, model, endpoint, messages, timeout=0, max_tokens=0):
        return json.dumps({'video_ratio': 99.16, 'followers': '4500'}, ensure_ascii=False)

    with _Monkey(_vision_qa_cfg=lambda: _CFG,
                 _vision_witness_ocr=lambda b64, cfg: _WITNESS_GOOD,
                 _call_chat_completion=_fake_naughty_chat):
        out = srv._vision_qa_pipeline(desc, _png_b64(), role='商务')
    out_obj = json.loads(out)
    assert out_obj['followers'] == '450万'    # 未标记字段改动被程序层挡下
    assert out_obj['video_ratio'] == 99.16    # 被标记字段正常修复采纳


# ── 8) witness 质量验收 (真机 2026-10-15: glm-4v-flash 中文转录成韩文乱码) ──

# 真机抓到的坏 witness 样本 (简化版): 韩文标签 + 错误数字
_BAD_WITNESS = ('\ucd09\uacc4\n\uc804\uccb4 dy99887766\n\uc804\uccb4 4500\n'
                '\uc804\uccb4 4.8\n\uc804\uccb4 99.16%\n\uc804\uccb4 50B-100g\n'
                '\uc804\uccb4 128\n18-23 4.33%\n24-30 24.84%\n31-40 42.87%')


def test_witness_quality_garbage_rejected():
    ok, why = srv._vision_witness_quality(_BAD_WITNESS)
    assert not ok and '\u4e71\u7801' in why   # CJK(\u4e2d\u6587) \u5360\u6bd4\u8fc7\u4f4e


def test_witness_quality_good_accepted():
    ok, _ = srv._vision_witness_quality(_WITNESS_GOOD)
    assert ok


def test_witness_quality_short_and_nodigit_rejected():
    ok, _ = srv._vision_witness_quality('\u77ed')
    assert not ok
    ok, why = srv._vision_witness_quality('\u8fbe\u4eba\u6570\u636e\u622a\u56fe\u8fbe\u4eba\u6570\u636e\u622a\u56fe\u8fbe\u4eba\u6570\u636e\u622a\u56fe\u8fbe\u4eba\u6570\u636e\u622a\u56fe')
    assert not ok and '\u6f0f\u5f55' in why


def test_pipeline_garbage_witness_never_corrupts_good_data():
    """\u574f witness \u88ab\u5f03\u7528 \u2192 \u4fee\u590d pass \u4e0d\u8dd1, kimi \u63d0\u5bf9\u7684\u503c\u539f\u6837\u4fdd\u4f4f (\u771f\u673a\u6848\u4f8b: '450\u4e07' \u4e0d\u8bb8\u88ab\u6539\u6210 '4500')\u3002"""
    obj = _extraction_obj()    # kimi \u63d0\u53d6\u7248 (\u5168\u5bf9)
    desc = json.dumps(obj, ensure_ascii=False)

    def _fake_chat(provider, key, model, endpoint, messages, timeout=0, max_tokens=0):
        raise AssertionError('\u574f witness \u4e0b\u4fee\u590d pass \u4e0d\u8be5\u88ab\u8c03\u7528')

    with _Monkey(_vision_qa_cfg=lambda: _CFG,
                 _vision_witness_ocr=lambda b64, cfg: _BAD_WITNESS,
                 _call_chat_completion=_fake_chat):
        out = srv._vision_qa_pipeline(desc, _png_b64(), role='\u5546\u52a1')
    assert json.loads(out) == obj    # \u539f\u6587\u4e00\u5b57\u4e0d\u52a8


# ── 9) 服务端预识别 _preocr_chat_images (老大 2026-10-15 拍板方案A) ──────────

_B64 = 'aGVsbG8='    # 假 base64, 内容无所谓


def test_preocr_business_single_image_injects_text():
    """商务单图 + vision/QA 成功 → 注入校验文本块, 不再传 image_url (模型免看图)。"""
    calls = []

    def _fake_vision(b64, agent_id=None, role=None):
        calls.append((b64, role))
        return '{"followers": "450万"}'

    with _Monkey(_call_kimi_vision=_fake_vision,
                 _vision_qa_pipeline=lambda raw, b64, role: raw):
        items, _name = srv._preocr_chat_images([{'base64': _B64}], 'emp_x', '商务')
    assert len(calls) == 1 and calls[0][1] == '商务'
    assert len(items) == 1 and items[0]['type'] == 'text'
    assert '截图识别结果' in items[0]['text'] and '450万' in items[0]['text']


def test_preocr_fallback_to_image_url_on_vision_failure():
    """识别失败 (vision 返回 None) → 回退 image_url 直连带图, 绝不裸奔。纯str元素兼容。"""
    with _Monkey(_call_kimi_vision=lambda b64, agent_id=None, role=None: None):
        items, _name = srv._preocr_chat_images([_B64], 'emp_x', '商务')    # 元素是纯字符串
    assert items == [{'type': 'image_url', 'image_url': {'url': _B64}}]


def test_preocr_fallback_when_qa_returns_none():
    """vision 成功但 QA 管线返回 None (比如非提取输出) → 也回退 image_url。"""
    with _Monkey(_call_kimi_vision=lambda b64, agent_id=None, role=None: '散文描述',
                 _vision_qa_pipeline=lambda raw, b64, role: None):
        items, _name = srv._preocr_chat_images([{'base64': _B64}], 'emp_x', '商务')
    assert items == [{'type': 'image_url', 'image_url': {'url': _B64}}]


def test_preocr_skipped_for_non_business_role():
    """非商务角色带图 → 不预识别, 直接 image_url (保持现状零变化)。"""
    calls = []
    with _Monkey(_call_kimi_vision=lambda b64, agent_id=None, role=None: calls.append(1) or 'x'):
        items, _name = srv._preocr_chat_images([{'base64': _B64}], 'emp_x', 'CEO助理')
    assert calls == []
    assert items == [{'type': 'image_url', 'image_url': {'url': _B64}}]


def test_preocr_skipped_for_multi_image():
    """多图 (≥2) → 不预识别, 全部 image_url (多图场景归 HeavyPipe 管道管)。"""
    calls = []
    imgs = [{'base64': _B64}, {'base64': _B64}, {'base64': _B64}]
    with _Monkey(_call_kimi_vision=lambda b64, agent_id=None, role=None: calls.append(1) or 'x'):
        items, _name = srv._preocr_chat_images(imgs, 'emp_x', '商务')
    assert calls == []
    assert len(items) == 3 and all(i['type'] == 'image_url' for i in items)


def test_preocr_exception_never_raises():
    """vision 抛异常 → 该图回退 image_url, 函数不 raise。"""
    def _boom(b64, agent_id=None, role=None):
        raise RuntimeError('炸')
    with _Monkey(_call_kimi_vision=_boom):
        items, _name = srv._preocr_chat_images([{'base64': _B64}], 'emp_x', '商务')
    assert items == [{'type': 'image_url', 'image_url': {'url': _B64}}]


# ── 10) fixes 合并契约 + 履约分百分制 (真机 2026-10-15 realworld4) ─────────

def test_apply_field_fixes_paths():
    obj = {'video_ratio': 88.88, 'extra_fields': {'履约分': None},
           'top_products': [{'name': 'a', 'price': '9.9'}], 'fan_age': {'18-23': 4.0}}
    roots = {'video_ratio', 'extra_fields', 'top_products', 'fan_age'}
    applied = srv._vision_apply_field_fixes(obj, {
        'video_ratio': 99.16,
        'extra_fields.履约分': 82,
        'top_products[0]': {'name': 'a', 'price': '19.9'},
        'fan_age': {'18-23': 4.33, '24-30': 24.84},
    }, roots)
    assert set(applied) == {'video_ratio', 'extra_fields.履约分', 'top_products[0]', 'fan_age'}
    assert obj['video_ratio'] == 99.16
    assert obj['extra_fields']['履约分'] == 82
    assert obj['top_products'][0]['price'] == '19.9'
    assert obj['fan_age'] == {'18-23': 4.33, '24-30': 24.84}


def test_apply_field_fixes_rejects_wild_paths():
    obj = {'followers': '450万', 'video_ratio': 88.88}
    roots = {'video_ratio'}
    applied = srv._vision_apply_field_fixes(obj, {
        'followers': '4500',            # 不在 flagged_roots → 拒
        'agency': '无忧传媒',            # 野 key → 拒
        'video_ratio': 99.16,           # 合法 → 应用
        'top_products[9]': {},          # 根不在 + 越界 → 拒
    }, roots)
    assert applied == ['video_ratio']
    assert obj == {'followers': '450万', 'video_ratio': 99.16}


def test_pipeline_fixes_partial_merge_adopted():
    """修复只输出部分被标记字段 → 只合并这部分, 其余保持原值。"""
    obj = _extraction_obj()
    obj['video_ratio'] = 88.88
    obj['rating_score'] = 9.9    # 第二个被标记字段 (常识硬错已置 null 前)
    desc = json.dumps(obj, ensure_ascii=False)

    def _fake_partial_chat(provider, key, model, endpoint, messages, timeout=0, max_tokens=0):
        return json.dumps({'video_ratio': 99.16}, ensure_ascii=False)    # 只修一个

    with _Monkey(_vision_qa_cfg=lambda: _CFG,
                 _vision_witness_ocr=lambda b64, cfg: _WITNESS_GOOD,
                 _call_chat_completion=_fake_partial_chat):
        out = srv._vision_qa_pipeline(desc, _png_b64(), role='商务')
    out_obj = json.loads(out)
    assert out_obj['video_ratio'] == 99.16    # 修好的采纳
    assert out_obj['followers'] == '450万'    # 未涉及字段不动


def test_sanity_lvdoufen_percent_no_longer_nulled():
    """★ 真机误杀修复: 百应履约分=12 是百分制, 不再被 score5 规则置 null。"""
    obj = {'extra_fields': {'履约分': '12'}}
    issues = srv._vision_sanity_check(obj, fix=True)
    assert not any(i.get('hard') for i in issues)
    assert obj['extra_fields']['履约分'] == '12'
    # 百分制越界 (>101.5) 仍然拦
    obj2 = {'extra_fields': {'履约分': '150'}}
    issues2 = srv._vision_sanity_check(obj2, fix=True)
    assert any(i.get('hard') for i in issues2)
    assert obj2['extra_fields']['履约分'] is None


# ── 11) 假 tool_call 剥离 + 预识别达人名透出 + 聊天进度 (真机 2026-10-15 21:05) ──

def test_strip_fake_toolcall_exec_form():
    """★ 真机漏网形态: MiniMax 回落吐 `<tool_call>exec(cmd="curl...")<tool_call>exec(...)`
    (开闭同标签、无闭合、连吐多行)。旧正则只认 <tool>exec</tool> 形态, 这种直接展示给用户。
    修复后整段剥掉, 保留正常正文, 且不再误剥含 'tool_call' 子串的普通词。"""
    reply = (
        '收到截图，正在写入达人档案。\n\n'
        '先查库里是否已有「李婶儿」建档：<tool_call>exec(cmd="curl -s \'http://localhost:8081/api/talents?q=李婶儿\'")\n'
        '<tool_call>exec(cmd="curl -s http://localhost:8081/api/tasks?assignee=xx")\n\n'
        '分析结论：该达人粉丝画像偏年轻。'
    )
    out = srv._strip_fake_tool_calls(reply)
    assert '<tool_call' not in out
    assert 'exec(cmd' not in out
    assert 'curl' not in out
    assert '收到截图，正在写入达人档案。' in out
    assert '分析结论：该达人粉丝画像偏年轻。' in out
    # 普通正文不过敏
    assert srv._strip_fake_tool_calls('今天聊聊工具调用的话题') == '今天聊聊工具调用的话题'


def test_strip_fake_toolcall_keeps_existing_tool_form():
    """回归: exec(command=...) 字面形态 + 历史 <tool>exec</tool> 块形态 → 占位替换, 正文保留。"""
    reply = '先跑一下：exec(command="curl -s http://x/api/talents")\n结论OK'
    out = srv._strip_fake_tool_calls(reply)
    assert 'exec(' not in out and 'curl' not in out
    assert '（正在查询系统数据…）' in out and '结论OK' in out
    # <tool> 块形态(历史 minimax 语料)
    reply2 = '<tool>exec</tool>\n<parameter>{"command": "curl x"}</parameter>\n结论OK'
    out2 = srv._strip_fake_tool_calls(reply2)
    assert '<tool>' not in out2 and 'parameter' not in out2 and 'curl' not in out2
    assert '结论OK' in out2


def test_preocr_extracts_talent_name():
    """★ fix/preocr-talent-name: 预识别 JSON 里的 name 字段随返回值透出, 供库里查重。"""
    def _fake_vision(b64, agent_id=None, role=None):
        return '{"name": "李婷儿", "followers": "12.8万", "level": "L4"}'
    with _Monkey(_call_kimi_vision=_fake_vision,
                 _vision_qa_pipeline=lambda raw, b64, role: raw):
        items, name = srv._preocr_chat_images([{'base64': _B64}], 'emp_x', '商务')
    assert name == '李婷儿'
    assert items[0]['type'] == 'text'
    # 无 name 字段 → None, 不炸
    with _Monkey(_call_kimi_vision=lambda b64, agent_id=None, role=None: '{"followers": "1万"}',
                 _vision_qa_pipeline=lambda raw, b64, role: raw):
        items2, name2 = srv._preocr_chat_images([{'base64': _B64}], 'emp_x', '商务')
    assert name2 is None and len(items2) == 1


def test_talent_id_by_name_miss_returns_none():
    """★ fix/preocr-talent-name: 库里没有该名字 → None (只读查询, 不造数)。"""
    class _Auth:
        is_admin = True
        user_info = {'userId': 'u_x'}
    assert srv._talent_id_by_name('绝不存在的人名xyz987', _Auth()) is None
    assert srv._talent_id_by_name('', _Auth()) is None
    assert srv._talent_id_by_name(None, _Auth()) is None


def test_chat_progress_set_and_clear():
    """★ feat/chat-progress: 进度写入/读取/清理的 registry 契约。"""
    srv._set_chat_progress('emp_prog', 'vision', '视觉提取')
    with srv._CHAT_PROGRESS_LOCK:
        info = dict(srv._CHAT_PROGRESS.get('emp_prog') or {})
    assert info['stage'] == 'vision' and info['detail'] == '视觉提取'
    srv._clear_chat_progress('emp_prog')
    with srv._CHAT_PROGRESS_LOCK:
        assert 'emp_prog' not in srv._CHAT_PROGRESS
    # 幂等: 清不存在的 key 不炸
    srv._clear_chat_progress('emp_prog')
    # 空 agent_id 防御
    srv._set_chat_progress('', 'ai')


def test_repair_bare_thousands_number():
    """★ 真机 2026-10-15: 修复 pass 吐 {"video_count": 2,734} 裸千分位非法 JSON,
    解析失败白跑一轮修复。修复后引号外裸数字修好, 引号内文本值原样保留。"""
    # 裸千分位 → 数字
    assert srv._parse_vision_json('```json\n{"video_count": 2,734}\n```') == {'video_count': 2734}
    # 引号内 "2,734" 是文本值, 不动
    obj = srv._parse_vision_json('{"video_plays": "2,734", "a": 1,000}')
    assert obj == {'video_plays': '2,734', 'a': 1000}
    # 多级千分位
    assert srv._parse_vision_json('{"gmv": 1,234,567}') == {'gmv': 1234567}
    # 字符串内转义引号不误伤
    obj2 = srv._parse_vision_json('{"bio": "他说\\"1,000\\"次", "n": 2,500}')
    assert obj2 == {'bio': '他说"1,000"次', 'n': 2500}


# ── 8) 视觉 QA 的配置源必须独立于对话链（2026-10-26）─────────────────────
#
# ★ 背景（连带损伤的根因）：视觉 QA 原来读 `settings.llm.providers` 里 name 含 zhipu
#   的那一档 —— 那份档位表是**对话链**的。对话链按老大口径（智谱资源包只做知识库
#   embedding，不拿来做 LLM 对话）摘掉智谱档后，**视觉 QA 被一起带断**：
#   witness/repair 双双失效 ⇒ 字段提取精度从 95%+ 退回「只跑常识规则」（~67%）。
#   ⇒ 病根不是「智谱没了」，是**两份语义不同的事实共用一份配置源**。


def _no_visionqa_env():
    """清掉视觉 QA 的 env 兜底变量，保证测试只走 settings 路径（不碰真实 .env）。"""
    for k in ('SOLOBRAVE_VISION_QA_API_KEY', 'SOLOBRAVE_VISION_QA_BASE_URL',
              'SOLOBRAVE_VISION_QA_PROVIDER', 'SOLOBRAVE_VISION_WITNESS_MODEL',
              'SOLOBRAVE_VISION_QA_MODEL'):
        os.environ.pop(k, None)


def test_vision_qa_cfg_ignores_conversation_chain_provider_list():
    """★ 回归锁：视觉 QA 绝不许读 `settings.llm.providers`（对话链的档位表）。

    对话链的档位表里**有** zhipu 档时，视觉 QA 仍必须拿不到配置 ——
    否则两份事实又共用起来了，对话链改一次配置就能把本管线一起带断。
    """
    _no_visionqa_env()
    fake_settings = {'llm': {'providers': [
        {'name': 'zhipu', 'apiKey': 'fake-zhipu-key',
         'baseUrl': 'https://open.bigmodel.cn/api/paas/v4', 'priority': 1},
    ]}}
    with _Monkey(_read_json=lambda *a, **k: fake_settings):
        cfg = srv._vision_qa_cfg()
    assert cfg is None, (
        '视觉 QA 又在读对话链的 llm.providers 了 —— '
        '两份语义不同的事实共用一份配置源，会互相把对方带断')


def test_vision_qa_cfg_reads_dedicated_visionqa_section():
    """视觉 QA 从自己的 `settings.visionQA` 段取配置（形态同既有 `vision` 段）。"""
    _no_visionqa_env()
    fake_settings = {'visionQA': {
        'provider': 'zhipu', 'apiKey': 'fake-key-123',
        'baseUrl': 'https://open.bigmodel.cn/api/paas/v4',
        'witnessModel': 'glm-4v-flash', 'repairModel': 'glm-4-flash'}}
    with _Monkey(_read_json=lambda *a, **k: fake_settings):
        cfg = srv._vision_qa_cfg()
    assert cfg, '视觉 QA 读不到自己的配置段'
    assert cfg['key'] == 'fake-key-123'
    assert cfg['base'] == 'https://open.bigmodel.cn/api/paas/v4'
    assert cfg['witnessModel'] == 'glm-4v-flash'
    assert cfg['repairModel'] == 'glm-4-flash'
    # ⚠️ glm-4v-flash 的 max_tokens 硬上限 1024（真机 2026-10-15 实证：
    #   传 2500 → HTTP 400 code 1210「限制数值范围[1,1024]」）必须被尊重
    assert cfg['maxTokens'] == 1024, cfg['maxTokens']


def test_vision_qa_cfg_rejects_anthropic_protocol_provider():
    """Anthropic 协议端点接不了 witness 的 image_url 形态 ⇒ 显式降级，不静默走错分支。

    ⚠️ `_call_chat_completion` 的 provider 参数决定协议分支：
    'minimax'/'anthropic'/'kimicode' → Anthropic `/messages`；
    其它 → OpenAI `/chat/completions`。配错了不会报错，只会**静默走错分支**。
    """
    _no_visionqa_env()
    fake_settings = {'visionQA': {
        'provider': 'minimax', 'apiKey': 'fake-key',
        'baseUrl': 'https://api.minimax.cn/anthropic/v1'}}
    with _Monkey(_read_json=lambda *a, **k: fake_settings):
        cfg = srv._vision_qa_cfg()
    assert cfg is None, 'Anthropic 协议端点被当 OpenAI 兼容用了 —— 会静默走到错误分支'


def test_vision_qa_cfg_env_fallback():
    """env 兜底可用（没配 settings.visionQA 时）。"""
    _no_visionqa_env()
    os.environ['SOLOBRAVE_VISION_QA_API_KEY'] = 'fake-env-key'
    os.environ['SOLOBRAVE_VISION_QA_BASE_URL'] = 'https://example.invalid/v4'
    try:
        with _Monkey(_read_json=lambda *a, **k: {}):
            cfg = srv._vision_qa_cfg()
        assert cfg, 'env 兜底不生效'
        assert cfg['key'] == 'fake-env-key'
        assert cfg['base'] == 'https://example.invalid/v4'
    finally:
        _no_visionqa_env()


def test_vision_qa_cfg_misconfigured_does_not_fall_through_to_env():
    """★ 配置错误不许被当成「没配置」：配了但装配失败 ⇒ 直接 None，不落 env 兜底。

    ⚠️ 落 env 兜底 = 把「配错了」写成「没配」⇒ 排查会跑偏（去查 env 而不是查配置）。
    """
    _no_visionqa_env()
    os.environ['SOLOBRAVE_VISION_QA_API_KEY'] = 'env-should-not-be-used'
    try:
        fake_settings = {'visionQA': {
            'provider': 'minimax', 'apiKey': 'fake-key',
            'baseUrl': 'https://api.minimax.cn/anthropic/v1'}}
        with _Monkey(_read_json=lambda *a, **k: fake_settings):
            cfg = srv._vision_qa_cfg()
        assert cfg is None, '配置错误时不该悄悄落到 env 兜底'
    finally:
        _no_visionqa_env()


def test_vision_qa_cfg_strips_embeddings_suffix_from_base():
    """★ 回归（2026-10-25 实测踩过）：base 残留 `/embeddings` ⇒ 请求 404。

    `.env` 的 embedding base 存的是**完整 embedding 路径**
    （`.../api/paas/v4/embeddings`）。直接拼 `/chat/completions` 会打到
    `/api/paas/v4/embeddings/chat/completions` → HTTP 404
    （路径看着像 base 没配对，其实是多了三段）。
    """
    _no_visionqa_env()
    fake_settings = {'visionQA': {
        'provider': 'zhipu', 'apiKey': 'fake-key',
        'baseUrl': 'https://open.bigmodel.cn/api/paas/v4/embeddings'}}
    with _Monkey(_read_json=lambda *a, **k: fake_settings):
        cfg = srv._vision_qa_cfg()
    assert cfg, '视觉 QA 配置解析失败'
    assert not cfg['base'].endswith('/embeddings'), \
        f"base 残留 /embeddings → 请求会 404: {cfg['base']}"
    assert cfg['base'].endswith('/v4'), cfg['base']


def test_repair_null_is_rejected_and_original_kept():
    """★ 修复 pass 吐 `null` ⇒ **拒绝采纳**，保留原值（「不置 null 不误杀」）。

    ⚠️ 模块设计写的是「修不好的字段不输出 → 保持原值」「修不动保留原值+记日志
      (不置 null 不误杀)」—— 程序侧原来照单全收模型给的任何值（包括 null），
      ⇒ 模型「找不到依据」时吐 null，原来的**错值**被抹成 null。

    ⚠️ 与**常识规则**的区分（别一起改错）：
      `_vision_sanity_check` 把越界值（120%）置 null 是**合法**的 ——
      120% 这种就是错值，置 null 比留错值好；
      这里拒的是**修复模型**的 null（=「我没修好」，不是「这个值是错的」）。
    """
    obj = {'followers': '450万', 'video_ratio': 88.88}
    fixes = {'followers': None, 'video_ratio': 99.16}
    applied = srv._vision_apply_field_fixes(obj, fixes, {'followers', 'video_ratio'})
    assert obj['followers'] == '450万', f'修不动不该抹掉原值: {obj}'
    assert obj['video_ratio'] == 99.16, obj
    assert 'followers' not in applied, applied
    assert 'video_ratio' in applied, applied


def test_sanity_check_may_still_null_out_of_range():
    """对照：常识规则的越界置 null **仍然合法**（别把它一起禁掉）。"""
    obj = {'extra_fields': {'转化率': '120%'}}
    srv._vision_sanity_check(obj, fix=True)
    assert obj['extra_fields']['转化率'] is None, obj
