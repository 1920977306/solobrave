"""商品链接 → 商品ID 提取（2026-11-03 补短链解析）。

老大口径：「商品id就是链接里面连在一起的数字」。
- 直连链接（haohuo/?id=、/product/<id>、全文 17-20 位数字）——既有行为锁死；
- 短链 v.douyin.com/<code>/ 链接本体**没有数字**，单次 HEAD 解析 302 Location
  （实测 aweme://webcast_webview?url=<多层编码的商品推广H5>）。
红线：
  - 只认 product_id / commodity_id / promotion_id 三个显式商品参数，且按此优先级；
  - 短链指向视频（Location 无商品参数）→ 返回空，**绝不把视频ID当商品ID**；
  - 网络异常/超时 → 返回空，不炸主链路；
  - 非短链且抽不到 → 不发起任何网络请求。
"""
import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from daren_bot import product_link as pl

# 实测真实 Location 样本（2026-11-03 curl -sI 拿到）
REAL_LOCATION_PROMOTION_ONLY = (
    'aweme://webcast_webview?url=https%3A%2F%2Flf-ecom-gr-sourcecdn.bytegecko.com%2F'
    'obj%2Fbyte-gurd-source-gr%2FmerchPromoting%2Fgecko%2Fh5Resource%2Ffe_merch_promoting_h5%2F'
    'cn%2Fhtml%2Fmerch-promoting%2Findex.html%3Fhide_nav_bar%3D1%26'
    'promotion_id%3D3848235384622416590%26ab_version%3D2'
)
REAL_LOCATION_MULTI_PARAM = (
    'aweme://webcast_webview?url=https%253A%252F%252Fcdn.example.com%252Fh5.html%253F'
    'product_id%253D3721152102001672246%2526commodity_id%253D3721153398628865149%2526'
    'promotion_id%253D3721153398628865149'
)
VIDEO_LOCATION = 'aweme://aweme/detail/7100000000000000000?refer=web'


class TestDirectLinks:
    """直连链接：既有行为，逐位锁死。"""

    @pytest.mark.parametrize('url,expect', [
        ('https://haohuo.jinritemai.com/ecommerce/trade/detail/index.html?id=3818866857771925914'
         '&ins_activity_param=Ef1LWy8mKXM&origin_type=pc_buyin_group', '3818866857771925914'),
        ('https://haohuo.jinritemai.com/views/product/detail?id=3446348913060228570',
         '3446348913060228570'),
        ('https://haohuo.jinritemai.com/views/product/item2?id=3502057979786706281',
         '3502057979786706281'),
        ('https://www.douyin.com/product/3502057979786706281', '3502057979786706281'),
    ])
    def test_known_formats(self, url, expect):
        assert pl.extract_product_id(url) == expect

    def test_plain_no_id_returns_empty(self):
        assert pl.extract_product_id('https://www.douyin.com/discover') == ''

    def test_full_text_fallback_longest_number(self):
        u = '看看这个 https://haohuo.jinritemai.com/x?a=1 商品 3446348913060228570 挺好'
        assert pl.extract_product_id(u) == '3446348913060228570'


class TestShortLinkResolution:
    def test_promotion_only(self, monkeypatch):
        monkeypatch.setattr(pl, '_resolve_short_link_location',
                            lambda u, timeout=5.0: REAL_LOCATION_PROMOTION_ONLY)
        assert pl.extract_product_id('https://v.douyin.com/3HXXhXyDS8s/') == '3848235384622416590'

    def test_multi_param_prefers_product_id(self, monkeypatch):
        """product_id / commodity_id / promotion_id 同时存在时，必须取 product_id。"""
        monkeypatch.setattr(pl, '_resolve_short_link_location',
                            lambda u, timeout=5.0: REAL_LOCATION_MULTI_PARAM)
        assert pl.extract_product_id('https://v.douyin.com/aovVC37WH7k/') == '3721152102001672246'

    def test_video_redirect_returns_empty(self, monkeypatch):
        """短链指向视频：没有商品参数 → 返回空，绝不能拿视频ID顶替。"""
        monkeypatch.setattr(pl, '_resolve_short_link_location',
                            lambda u, timeout=5.0: VIDEO_LOCATION)
        assert pl.extract_product_id('https://v.douyin.com/abcd123/') == ''

    def test_network_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr(pl, '_resolve_short_link_location', lambda u, timeout=5.0: '')
        assert pl.extract_product_id('https://v.douyin.com/abcd123/') == ''

    def test_non_short_link_never_calls_network(self, monkeypatch):
        called = []

        def _boom(u, timeout=5.0):
            called.append(u)
            return REAL_LOCATION_PROMOTION_ONLY

        monkeypatch.setattr(pl, '_resolve_short_link_location', _boom)
        assert pl.extract_product_id('https://www.douyin.com/discover') == ''
        assert called == [], '直连链接抽不到时也不许发起网络请求'

    def test_subdomain_matches(self, monkeypatch):
        monkeypatch.setattr(pl, '_resolve_short_link_location',
                            lambda u, timeout=5.0: REAL_LOCATION_PROMOTION_ONLY)
        assert pl.extract_product_id('https://sub.v.douyin.com/xyz/') == '3848235384622416590'


class TestResolvedParser:
    def test_multilayer_encoding(self):
        """实测 Location 有 3 层编码，必须全部解开。"""
        loc = ('aweme://webcast_webview?url='
               'https%253A%252F%252Fh5.example.com%252Fp.html%253Fproduct_id%253D1234567890123456789')
        assert pl._product_id_from_resolved(loc) == '1234567890123456789'

    def test_bare_id_param_ignored(self):
        """视频形态的裸 id 参数不认。"""
        loc = 'https://www.douyin.com/video/7100000000000000000?id=7100000000000000000'
        assert pl._product_id_from_resolved(loc) == ''

    def test_priority_order(self):
        loc = ('https://h5.example.com/p.html?promotion_id=1111111111111111111'
               '&commodity_id=2222222222222222222&product_id=3333333333333333333')
        assert pl._product_id_from_resolved(loc) == '3333333333333333333'
