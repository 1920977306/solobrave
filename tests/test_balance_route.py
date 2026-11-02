"""BALANCE_RE 余额查询句式锁（2026-11-03 mumu「账户积分还有多少」漏配实锤）。

零成本路由的正则：该命中的必须命中（否则会掉进商品话术答非所问），
不该命中的（价格询问/带其他内容）必须放行到问答链。
"""
import re

from daren_bot import handler


def _matches(text: str) -> bool:
    return bool(handler.BALANCE_RE.match(text.strip()))


class TestBalanceMatches:
    def test_plain_balance_words(self):
        for t in ["余额", "积分", "我的积分", "积分余额", "查积分",
                  "还有多少积分", "我还有多少积分", "我有多少积分",
                  "我的积分还有多少", "积分还有多少"]:
            assert _matches(t), t

    def test_account_prefix_variant(self):
        # 2026-11-03 实锤：这条之前漏配
        assert _matches("账户积分还有多少")

    def test_prefix_variants(self):
        for t in ["我的积分还有多少", "账户积分还有多少", "账号积分还有多少",
                  "帐号积分还有多少", "积分还有多少", "积分还剩多少",
                  "积分剩多少", "积分多少", "积分多少分", "积分还能用多少",
                  "积分余额还有多少", "我的积分余额多少"]:
            assert _matches(t), t

    def test_with_punctuation_and_spaces(self):
        assert _matches("账户积分还有多少？")
        assert _matches("我还有多少积分！")
        assert _matches("  积分多少  ")


class TestBalanceNotMatches:
    def test_cost_questions_go_to_qa(self):
        # 价格/规则询问不是余额查询，必须放行
        for t in ["分析一次要多少积分", "查商品要多少积分",
                  "积分怎么充值", "积分有什么用", "积分规则是什么",
                  "100积分能干什么"]:
            assert not _matches(t), t

    def test_sentences_with_other_content(self):
        for t in ["我的积分还有多少帮我查一下这个商品",
                  "账户积分还有多少顺便分析下这个达人"]:
            assert not _matches(t), t
