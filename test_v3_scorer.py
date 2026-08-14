# -*- coding: utf-8 -*-
"""v3_scorer 单元测试：用 V3_model_data.md 中的真实商品数据验证评分。"""

import json
import os
import tempfile
import unittest
from unittest import mock

import v3_scorer
from v3_scorer import (
    FeishuError,
    brand_score,
    category_score,
    commission_score,
    grade,
    heat_score,
    positive_rate_score,
    price_band_score,
    save_to_feishu,
    score_product,
    shop_score,
)


def make_product(**kw):
    base = {
        "商品名称": "测试品",
        "品类": "服装/T恤",
        "价格": 59.9,
        "页面佣金率": 10,
        "好评率": 93,
        "店铺评分": 95,
        "品牌类型": "新锐品牌",
        "月销量": 1500,
    }
    base.update(kw)
    return base


class TestCommissionScore(unittest.TestCase):
    """佣金意愿评分表（1.1）：>=20→95 / 15-19→85 / 10-14→70 / 5-9→55 / <5→35"""

    def test_table_values(self):
        self.assertEqual(commission_score(25), 95.0)   # >=20%
        self.assertEqual(commission_score(20), 95.0)   # 边界
        self.assertEqual(commission_score(18), 85.0)   # 15-19%
        self.assertEqual(commission_score(15), 85.0)   # 边界
        self.assertEqual(commission_score(12), 70.0)   # 10-14%
        self.assertEqual(commission_score(10), 70.0)   # 边界
        self.assertEqual(commission_score(7), 55.0)    # 5-9%
        self.assertEqual(commission_score(5), 55.0)    # 边界（5% 归 5-9 档）
        self.assertEqual(commission_score(3), 35.0)    # <5%

    def test_ratio_input(self):
        self.assertEqual(commission_score(0.20), 95.0)  # 小数写法等价
        self.assertEqual(commission_score(0.03), 35.0)


class TestPositiveRateScore(unittest.TestCase):
    """好评率意愿评分表（1.2）"""

    def test_table_values(self):
        self.assertEqual(positive_rate_score(98), 95.0)    # >=96%
        self.assertEqual(positive_rate_score(96), 95.0)    # 边界
        self.assertEqual(positive_rate_score(94.2), 80.0)  # 93-95%（七绿洗发皂）
        self.assertEqual(positive_rate_score(91.9), 65.0)  # 90-92%（芭克）
        self.assertEqual(positive_rate_score(88.2), 40.0)  # 85-89%（搓澡巾）
        self.assertEqual(positive_rate_score(83.3), 15.0)  # <85%（Tian甜睡衣）

    def test_ratio_input(self):
        self.assertEqual(positive_rate_score(0.98), 95.0)


class TestShopScore(unittest.TestCase):
    """店铺信誉评分表（1.3）"""

    def test_table_values(self):
        self.assertEqual(shop_score(100), 95.0)  # Rockfish 满分店铺
        self.assertEqual(shop_score(99), 95.0)
        self.assertEqual(shop_score(96), 80.0)
        self.assertEqual(shop_score(94), 65.0)   # MSQ 粉底刷店铺
        self.assertEqual(shop_score(88), 40.0)   # 芭克店铺（全场最低）


class TestBrandScore(unittest.TestCase):
    """品牌势能评分表（1.4）"""

    def test_table_values(self):
        self.assertEqual(brand_score("国际大牌（进口）"), 95.0)  # Rockfish 英国进口
        self.assertEqual(brand_score("海外品牌"), 95.0)          # “海外”并入国际大牌档
        self.assertEqual(brand_score("国内知名品牌"), 80.0)      # 蕉下
        self.assertEqual(brand_score("IP/明星联名"), 75.0)       # CANOTWAIT
        self.assertEqual(brand_score("新锐品牌"), 55.0)          # Kismet
        self.assertEqual(brand_score("白牌/无品牌"), 30.0)       # 探屿旅行包
        self.assertEqual(brand_score("没听过的牌子"), 30.0)      # 未知按白牌


class TestHeatScore(unittest.TestCase):
    """市场热度评分表（1.5）"""

    def test_table_values(self):
        self.assertEqual(heat_score(29500), 90.0)  # 月销>1万（Kismet 2.95万）
        self.assertEqual(heat_score(5000), 70.0)   # 3000-1万
        self.assertEqual(heat_score(1956), 55.0)   # 1000-3000（探屿旅行包）
        self.assertEqual(heat_score(89), 35.0)     # <1000（MSQ 粉底刷）


class TestCategoryAndPrice(unittest.TestCase):
    """品类转化率基准表（2.1）与价格带效果规则（2.3）"""

    def test_category_benchmark(self):
        # 功能痛点品 15-40%（中位 27.5）应明显高于日常服装 5-10%（中位 7.5）
        self.assertGreater(category_score("洗护/浴花", 15.9),
                           category_score("服装/风衣", 139.99))
        # ¥200+ 按高端品 2.5-5% 档
        self.assertEqual(category_score("服装/外套", 319), 45.0)
        # 鞋类价格区间豁免：仅 ¥200-300 豁免，按日常服装档 5-10%（-> 60）
        self.assertEqual(category_score("女鞋", 239), 60.0)
        self.assertEqual(category_score("鞋/凉鞋", 200), 60.0)    # 区间下限，豁免
        self.assertEqual(category_score("鞋/休闲鞋", 300), 45.0)  # 区间上限，不豁免
        self.assertEqual(category_score("鞋/休闲鞋", 319), 45.0)  # ≥300 走高端品档

    def test_price_band(self):
        self.assertEqual(price_band_score(15.9), 90.0)    # ¥15-40
        self.assertEqual(price_band_score(39.9), 90.0)
        self.assertEqual(price_band_score(59.9), 75.0)    # ¥40-100
        self.assertEqual(price_band_score(139.99), 60.0)  # ¥100-200
        self.assertEqual(price_band_score(319), 45.0)     # ¥200+


class TestGrade(unittest.TestCase):
    """综合评级表（5.2）"""

    def test_thresholds(self):
        self.assertEqual(grade(80), "S")
        self.assertEqual(grade(79.9), "A")
        self.assertEqual(grade(70), "A")
        self.assertEqual(grade(69.9), "B")
        self.assertEqual(grade(60), "B")
        self.assertEqual(grade(59.9), "C")
        self.assertEqual(grade(50), "C")
        self.assertEqual(grade(49.9), "D")


class TestRealProducts(unittest.TestCase):
    """用 V3_model_data.md 中的真实商品端到端验证"""

    def test_kismet_windbreaker(self):
        """Kismet风衣：佣金20%、好评98%、月销2.95万 -> 应为 A 或 S 级"""
        r = score_product({
            "商品名称": "Kismet风衣",
            "品类": "服装/风衣",
            "价格": 139.99,
            "页面佣金率": 20,
            "好评率": 98,
            "店铺评分": 95,
            "品牌类型": "新锐品牌",
            "月销量": 29500,
        })
        # 意愿分 = 95*.35 + 95*.20 + 80*.15 + 55*.15 + 90*.15 = 86.0
        self.assertAlmostEqual(r["选品意愿分"], 86.0, places=2)
        self.assertGreaterEqual(r["选品意愿分"], 80)  # 意愿维度达 A 级
        self.assertIn(r["评级"], ("S", "A"))

    def test_sanli_scrub_towel(self):
        """三利搓澡巾：佣金15%、好评88.2%、白牌、月销3万+ -> B 级上下"""
        r = score_product({
            "商品名称": "三利搓澡巾",
            "品类": "洗护/浴花",
            "价格": 15.9,
            "页面佣金率": 15,
            "好评率": 88.2,
            "店铺评分": 95,
            "品牌类型": "白牌/无品牌",
            "月销量": 30000,
        })
        # 意愿分 = 85*.35 + 40*.20 + 80*.15 + 30*.15 + 90*.15 = 67.75
        self.assertAlmostEqual(r["选品意愿分"], 67.75, places=2)
        self.assertIn(r["评级"], ("A", "B", "C"))

    def test_johnson_contact_lens(self):
        """强生美瞳：佣金5% 但国际大牌+满分店铺，品牌力替代佣金 -> 不低于 B"""
        r = score_product({
            "商品名称": "强生美瞳",
            "品类": "美妆/美瞳",
            "价格": 189,
            "页面佣金率": 5,
            "好评率": 96.9,
            "店铺评分": 99,
            "品牌类型": "国际大牌（进口）",
            "月销量": 10700,
        })
        # 意愿分 = 55*.35 + 95*.20 + 95*.15 + 95*.15 + 90*.15 = 80.25（佣金5% -> 55）
        self.assertAlmostEqual(r["选品意愿分"], 80.25, places=2)
        self.assertIn(r["评级"], ("S", "A", "B"))

    def test_bad_product_gets_d(self):
        """白牌+低佣+低好评+低店铺+冷门 -> D 级"""
        r = score_product(make_product(
            品类="服装/外套", 价格=299, 页面佣金率=3,
            好评率=80, 店铺评分=85, 品牌类型="白牌/无品牌", 月销量=300,
        ))
        self.assertLess(r["选品意愿分"], 40)  # 意愿维度 D 级
        self.assertEqual(r["评级"], "D")

    def test_overall_formula(self):
        """验证综合分 = 意愿*0.45 + 效果*0.45 + 人群*0.10"""
        p = make_product(人群匹配度=80)
        r = score_product(p)
        expected = r["选品意愿分"] * 0.45 + r["带货效果分"] * 0.45 + 80 * 0.10
        self.assertAlmostEqual(r["综合分"], expected, places=1)

    def test_output_keys(self):
        r = score_product(make_product())
        for key in ("选品意愿分", "带货效果分", "综合分", "评级"):
            self.assertIn(key, r)


class TestSaveToFeishu(unittest.TestCase):
    """save_to_feishu：mock 飞书 API，验证查重与写入逻辑"""

    def _run(self, search_items):
        """mock _feishu_request，返回 (save_result, 调用记录)"""
        calls = []

        def fake_request(method, path, token=None, body=None, params=None):
            calls.append((method, path, body))
            if path.endswith("/tenant_access_token/internal"):
                return {"tenant_access_token": "fake-token"}
            if path.endswith("/records/search"):
                return {"items": search_items}
            if method == "PUT":
                return {"record": {"record_id": search_items[0]["record_id"]}}
            return {"record": {"record_id": "rec_new_1"}}

        with mock.patch.object(v3_scorer, "_feishu_request", side_effect=fake_request):
            result = save_to_feishu(make_product(商品名称="Kismet风衣"),
                                    app_token="app_tok_1",
                                    app_id="app_id_1", app_secret="app_secret_1")
        return result, calls

    def test_create_when_not_exists(self):
        """表中无同名商品 -> 新建记录"""
        result, calls = self._run(search_items=[])
        self.assertEqual(result, {"action": "created", "record_id": "rec_new_1"})
        methods = [m for m, _, _ in calls]
        self.assertIn("POST", methods)
        # 最后一次调用是创建记录，且字段带评分结果
        method, path, body = calls[-1]
        self.assertTrue(path.endswith("/records"))
        self.assertEqual(body["fields"]["product_name"], "Kismet风衣")
        self.assertEqual(body["fields"]["grade"], "B")  # make_product 默认参数综合分 65.4 -> B
        self.assertIn("total_score", body["fields"])
        self.assertIn("willingness_score", body["fields"])
        self.assertIn("effect_score", body["fields"])

    def test_update_when_exists(self):
        """表中已有同名商品 -> 更新原记录，不新建"""
        result, calls = self._run(search_items=[{"record_id": "rec_exist_1"}])
        self.assertEqual(result, {"action": "updated", "record_id": "rec_exist_1"})
        method, path, body = calls[-1]
        self.assertEqual(method, "PUT")
        self.assertIn("rec_exist_1", path)
        # 全流程只有一次查重 + 一次更新，没有创建记录
        self.assertFalse(any(m == "POST" and p.endswith("/records") and "search" not in p
                             for m, p, _ in calls))

    def test_missing_credentials(self):
        """缺 app_id/app_secret 环境变量时报错"""
        env = {k: v for k, v in __import__("os").environ.items()
               if k not in ("FEISHU_APP_ID", "FEISHU_APP_SECRET")}
        with mock.patch.dict(__import__("os").environ, env, clear=True):
            with self.assertRaises(FeishuError):
                save_to_feishu(make_product(), app_token="app_tok_1")

    def test_default_app_token(self):
        """app_token 缺省时使用内置默认值 NmYLbFbnJa3y1TsEujCcBoNEnzb"""
        env = {k: v for k, v in __import__("os").environ.items()
               if k != "FEISHU_APP_TOKEN"}
        with mock.patch.dict(__import__("os").environ, env, clear=True):
            result, calls = self._run_with_token(search_items=[], app_token=None)
        self.assertEqual(result["action"], "created")
        # 所有 bitable 请求路径都应包含默认 app_token
        for method, path, _ in calls:
            if "/bitable/" in path:
                self.assertIn(v3_scorer.FEISHU_APP_TOKEN_DEFAULT, path)

    def _run_with_token(self, search_items, app_token):
        calls = []

        def fake_request(method, path, token=None, body=None, params=None):
            calls.append((method, path, body))
            if path.endswith("/tenant_access_token/internal"):
                return {"tenant_access_token": "fake-token"}
            if path.endswith("/records/search"):
                return {"items": search_items}
            return {"record": {"record_id": "rec_new_1"}}

        with mock.patch.object(v3_scorer, "_feishu_request", side_effect=fake_request):
            result = save_to_feishu(make_product(商品名称="Kismet风衣"),
                                    app_token=app_token,
                                    app_id="app_id_1", app_secret="app_secret_1")
        return result, calls

    def test_missing_name(self):
        with self.assertRaises(ValueError):
            save_to_feishu({"品类": "服装"}, app_token="app_tok_1")


class TestValidateModel(unittest.TestCase):
    """validate_model：用 3 个模拟商品验证偏差计算与报告生成"""

    PRODUCTS = [
        {"product_name": "商品A", "category": "服装/风衣", "price": 139.99,
         "commission_rate": 20, "review_rate": 98, "store_score": 95,
         "brand_type": "新锐品牌", "monthly_sales": 29500},
        {"product_name": "商品B", "category": "洗护/浴花", "price": 15.9,
         "commission_rate": 15, "review_rate": 88.2, "store_score": 95,
         "brand_type": "白牌/无品牌", "monthly_sales": 30000},
        {"product_name": "商品C", "category": "美妆/美瞳", "price": 189,
         "commission_rate": 5, "review_rate": 96.9, "store_score": 99,
         "brand_type": "国际大牌（进口）", "monthly_sales": 10700},
    ]

    def _pred(self, en_product):
        """英文键商品 -> score_product 预测结果"""
        cn = {v3_scorer.EN_TO_CN_KEYS[k]: v for k, v in en_product.items()}
        return score_product(cn)

    def _make_actuals(self):
        """商品A 偏差0%、商品B 偏差10%、商品C 偏差50%
        （actual = pred / d 时，偏差率恰为 (d-1)*100%）"""
        divisors = {"商品A": 1.0, "商品B": 1.1, "商品C": 1.5}
        actuals = []
        for p in self.PRODUCTS:
            pred = self._pred(p)
            d = divisors[p["product_name"]]
            actuals.append({
                "product_name": p["product_name"],
                "actual_willingness": pred["选品意愿分"] / d,
                "actual_effect": pred["带货效果分"] / d,
                "actual_overall": pred["综合分"] / d,
            })
        return actuals

    def test_deviation_level(self):
        """偏差等级阈值：<=10 正常 / <=20 需关注 / <=30 较大偏差 / >30 严重偏差"""
        self.assertEqual(v3_scorer.deviation_level(5), "正常")
        self.assertEqual(v3_scorer.deviation_level(10), "正常")
        self.assertEqual(v3_scorer.deviation_level(15), "需关注")
        self.assertEqual(v3_scorer.deviation_level(20), "需关注")
        self.assertEqual(v3_scorer.deviation_level(25), "较大偏差")
        self.assertEqual(v3_scorer.deviation_level(30), "较大偏差")
        self.assertEqual(v3_scorer.deviation_level(31), "严重偏差")

    def test_mae_mape_calculation(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = v3_scorer.validate_model(self.PRODUCTS, self._make_actuals(),
                                         report_dir=tmp)
        self.assertEqual(r["样本量"], 3)
        # 三个商品偏差 0% / 10% / 50% -> 每个维度 MAPE 都是 (0+10+50)/3 = 20%
        for dim in ("选品意愿", "带货效果", "综合得分"):
            self.assertAlmostEqual(r["dimensions"][dim]["MAPE"], 20.0, places=2)
            self.assertEqual(r["dimensions"][dim]["偏差等级"], "需关注")
        # 意愿维度 MAE = (0 + |pB - pB/1.1| + |pC - pC/1.5|) / 3 = (pB/11 + pC/3) / 3
        pred_b = self._pred(self.PRODUCTS[1])["选品意愿分"]
        pred_c = self._pred(self.PRODUCTS[2])["选品意愿分"]
        expected_mae = (pred_b / 11 + pred_c / 3) / 3
        self.assertAlmostEqual(r["dimensions"]["选品意愿"]["MAE"],
                               expected_mae, places=2)
        # 逐商品明细：商品C 偏差 50% -> 严重偏差
        detail_c = next(d for d in r["details"] if d["product_name"] == "商品C")
        self.assertAlmostEqual(detail_c["dimensions"]["综合得分"]["pct_error"],
                               50.0, places=2)
        self.assertEqual(detail_c["level"], "严重偏差")
        # 严重偏差清单只有商品C
        self.assertEqual([s["product_name"] for s in r["severe_products"]], ["商品C"])

    def test_report_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = v3_scorer.validate_model(self.PRODUCTS, self._make_actuals(),
                                         report_dir=tmp)
            path = r["report_path"]
            # 文件名格式 iteration_YYYYMMDD.md
            self.assertRegex(os.path.basename(path), r"^iteration_\d{8}\.md$")
            self.assertTrue(os.path.dirname(path) == tmp or
                            os.path.abspath(os.path.dirname(path)) == os.path.abspath(tmp))
            with open(path, encoding="utf-8") as f:
                content = f.read()
        self.assertIn("验证时间", content)
        self.assertIn("样本量：3", content)
        self.assertIn("各维度偏差汇总", content)
        self.assertIn("严重偏差商品清单", content)
        self.assertIn("商品C", content)          # 严重偏差清单
        self.assertIn("商品A", content)          # 逐商品明细
        self.assertIn("20.00%", content)         # 各维度 MAPE

    def test_unmatched_actual_skipped(self):
        """实际结果里没有的商品不参与验证"""
        with tempfile.TemporaryDirectory() as tmp:
            actuals = self._make_actuals()[:1]  # 只保留商品A
            r = v3_scorer.validate_model(self.PRODUCTS, actuals, report_dir=tmp)
        self.assertEqual(r["样本量"], 1)
        self.assertAlmostEqual(r["dimensions"]["综合得分"]["MAPE"], 0.0, places=2)
        self.assertEqual(r["dimensions"]["综合得分"]["偏差等级"], "正常")


class TestAutoUpdateModel(unittest.TestCase):
    """auto_update_model：三种场景——不调整/单维度调整/全部超阈值不调整"""

    def _validation(self, mapes, report_dir=None):
        """构造 validate_model 返回结构的 dict，mapes 为各维度 MAPE"""
        dims = {}
        for dim, mape in zip(("选品意愿", "带货效果", "综合得分"), mapes):
            dims[dim] = {"MAE": mape / 2, "MAPE": mape,
                         "偏差等级": v3_scorer.deviation_level(mape)}
        result = {"dimensions": dims, "样本量": 3}
        if report_dir:
            result["report_path"] = v3_scorer._write_iteration_report(
                dims, [], [], report_dir)
        return result

    def test_no_dimension_over_threshold(self):
        """场景一：无维度超阈值 -> 不调整，不生成权重文件"""
        with tempfile.TemporaryDirectory() as tmp:
            r = v3_scorer.auto_update_model(self._validation([5, 10, 20]),
                                            weights_dir=tmp)
            self.assertFalse(r["updated"])
            self.assertEqual(r["adjusted_dimensions"], [])
            self.assertEqual(r["new_weights"], r["old_weights"])
            self.assertIsNone(r["new_weights_path"])
            self.assertEqual(os.listdir(tmp), [])

    def test_single_dimension_over_threshold(self):
        """场景二：带货效果 MAPE 25% -> 触发调整，权重归一化，版本递增，报告追加"""
        with tempfile.TemporaryDirectory() as tmp:
            r = v3_scorer.auto_update_model(
                self._validation([10, 25, 10], report_dir=tmp), weights_dir=tmp)

            self.assertTrue(r["updated"])
            self.assertEqual(r["adjusted_dimensions"], ["带货效果"])
            self.assertEqual(r["old_weights"],
                             {"选品意愿": 0.45, "带货效果": 0.45, "综合得分": 0.10})
            # 带货效果下调 5%：0.45 -> 0.4275；释放量按比例分给另两个维度
            self.assertAlmostEqual(r["new_weights"]["带货效果"], 0.4275, places=4)
            self.assertGreater(r["new_weights"]["选品意愿"], 0.45)
            self.assertGreater(r["new_weights"]["综合得分"], 0.10)
            # 归一化：总和为 1
            self.assertAlmostEqual(sum(r["new_weights"].values()), 1.0, places=6)

            # 权重文件 model_weights_v1.json
            self.assertTrue(r["new_weights_path"].endswith("model_weights_v1.json"))
            with open(r["new_weights_path"], encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(saved["version"], 1)
            self.assertEqual(saved["dimension_weights"], r["new_weights"])
            self.assertIn("带货效果", saved["reason"])

            # 再次调整 -> 版本递增为 v2，且基于 v1 的权重继续下调
            r2 = v3_scorer.auto_update_model(self._validation([10, 25, 10]),
                                             weights_dir=tmp)
            self.assertTrue(r2["new_weights_path"].endswith("model_weights_v2.json"))
            self.assertAlmostEqual(r2["old_weights"]["带货效果"], 0.4275, places=4)
            self.assertLess(r2["new_weights"]["带货效果"], 0.4275)
            self.assertAlmostEqual(sum(r2["new_weights"].values()), 1.0, places=6)

        # 用独立目录验证报告追加内容
        with tempfile.TemporaryDirectory() as tmp:
            vr = self._validation([10, 25, 10], report_dir=tmp)
            v3_scorer.auto_update_model(vr, weights_dir=tmp)
            with open(vr["report_path"], encoding="utf-8") as f:
                content = f.read()
            self.assertIn("权重变更记录", content)
            self.assertIn("调整前权重", content)
            self.assertIn("调整后权重", content)
            self.assertIn("调整原因", content)
            self.assertIn("带货效果", content)

    def test_all_dimensions_over_threshold(self):
        """场景三：所有维度都超阈值 -> 不调整，保持原权重"""
        with tempfile.TemporaryDirectory() as tmp:
            r = v3_scorer.auto_update_model(self._validation([25, 30, 40]),
                                            weights_dir=tmp)
            self.assertFalse(r["updated"])
            self.assertEqual(r["adjusted_dimensions"], [])
            self.assertEqual(r["new_weights"], r["old_weights"])
            self.assertIsNone(r["new_weights_path"])
            self.assertIn("保持原权重", r["reason"])
            self.assertEqual(os.listdir(tmp), [])


class TestLoadLatestWeights(unittest.TestCase):
    """score_product 自动加载最新权重文件 / 无文件回退默认值"""

    def test_load_latest_weights_fallback(self):
        """无权重文件 -> 返回内置默认值"""
        with tempfile.TemporaryDirectory() as tmp:
            w = v3_scorer.load_latest_weights(tmp)
        self.assertEqual(w, {"选品意愿": 0.45, "带货效果": 0.45, "综合得分": 0.10})

    def test_load_latest_weights_picks_max_version(self):
        """多个版本文件 -> 取版本号最大的"""
        with tempfile.TemporaryDirectory() as tmp:
            for n, will_w in ((1, 0.50), (3, 0.60), (2, 0.55)):
                with open(os.path.join(tmp, "model_weights_v%d.json" % n), "w",
                          encoding="utf-8") as f:
                    json.dump({"version": n, "dimension_weights":
                               {"选品意愿": will_w, "带货效果": 0.30,
                                "综合得分": 0.10}}, f)
            w = v3_scorer.load_latest_weights(tmp)
        self.assertEqual(w["选品意愿"], 0.60)  # v3 最大

    def test_score_product_uses_default_without_file(self):
        """无权重文件 -> 综合分按默认权重 0.45/0.45/0.10"""
        with tempfile.TemporaryDirectory() as tmp:
            p = make_product(人群匹配度=80)
            r = score_product(p, weights_dir=tmp)
        expected = r["选品意愿分"] * 0.45 + r["带货效果分"] * 0.45 + 80 * 0.10
        self.assertAlmostEqual(r["综合分"], expected, places=1)

    def test_score_product_uses_file_weights(self):
        """有权重文件 -> 综合分改用文件中的维度权重"""
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "model_weights_v1.json"), "w",
                      encoding="utf-8") as f:
                json.dump({"version": 1, "dimension_weights":
                           {"选品意愿": 0.60, "带货效果": 0.30,
                            "综合得分": 0.10}}, f)
            p = make_product(人群匹配度=80)
            r = score_product(p, weights_dir=tmp)
        expected = r["选品意愿分"] * 0.60 + r["带货效果分"] * 0.30 + 80 * 0.10
        self.assertAlmostEqual(r["综合分"], expected, places=1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
