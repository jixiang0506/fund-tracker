"""
测试 calculate_holdings 函数（最终修正版）
"""
import sys, os, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fetch_fund_data import calculate_holdings, get_nav_from_history


def make_purchase(code, date, shares, nav, amount=None, before_15=True):
    a = amount if amount is not None else round(shares * nav, 2)
    return {"code": code, "date": date, "shares": shares,
            "nav": nav, "amount": a, "before_15": before_15}


def make_history(nav_map):
    return [{"date": d, "nav": v} for d, v in nav_map.items()]


class TestCalculateHoldingsBasic(unittest.TestCase):
    def test_empty_records(self):
        r = calculate_holdings([], 1.0, [{"date": "2025-01-02", "nav": 1.0}])
        self.assertEqual(r["total_shares"], 0)
        self.assertEqual(r["total_invested"], 0.0)

    def test_single_buy(self):
        records = [make_purchase("000001", "2025-01-02", 1000, 1.0)]
        history = make_history({"2025-01-02": 1.0, "2025-01-03": 1.1})
        r = calculate_holdings(records, 1.1, history)
        self.assertEqual(r["total_shares"], 1000)
        self.assertAlmostEqual(r["current_value"], 1100.0, places=2)

    def test_multiple_buys(self):
        records = [
            make_purchase("000001", "2025-01-02", 1000, 1.0),
            make_purchase("000001", "2025-01-03", 500, 1.1),
        ]
        history = make_history({"2025-01-02": 1.0, "2025-01-03": 1.1, "2025-01-06": 1.2})
        r = calculate_holdings(records, 1.2, history)
        self.assertEqual(r["total_shares"], 1500)
        self.assertAlmostEqual(r["total_invested"], 1550.0, places=2)


class TestCalculateHoldingsSell(unittest.TestCase):
    def test_sell_all(self):
        records = [
            make_purchase("000001", "2025-01-02", 1000, 1.0),
            {"code": "000001", "date": "2025-01-03", "type": "sell",
             "shares": 1000, "nav": 1.1, "amount": 1100.0, "before_15": True},
        ]
        history = make_history({"2025-01-02": 1.0, "2025-01-03": 1.1})
        r = calculate_holdings(records, 1.1, history)
        self.assertEqual(r["total_shares"], 0)

    def test_sell_partial(self):
        records = [
            make_purchase("000001", "2025-01-02", 1000, 1.0),
            {"code": "000001", "date": "2025-01-03", "type": "sell",
             "shares": 400, "nav": 1.1, "amount": 440.0, "before_15": True},
        ]
        history = make_history({"2025-01-02": 1.0, "2025-01-03": 1.1, "2025-01-06": 1.2})
        r = calculate_holdings(records, 1.2, history)
        self.assertEqual(r["total_shares"], 600)

    def test_sell_cross_batch(self):
        records = [
            make_purchase("000001", "2025-01-02", 500, 1.0),
            make_purchase("000001", "2025-01-03", 500, 1.2),
            {"code": "000001", "date": "2025-01-06", "type": "sell",
             "shares": 700, "nav": 1.1, "amount": 770.0, "before_15": True},
        ]
        history = make_history({"2025-01-02": 1.0, "2025-01-03": 1.2,
                                   "2025-01-06": 1.1, "2025-01-07": 1.3})
        r = calculate_holdings(records, 1.3, history)
        self.assertEqual(r["total_shares"], 300)

    def test_sell_with_loss(self):
        records = [
            make_purchase("000001", "2025-01-02", 1000, 1.5),
            {"code": "000001", "date": "2025-01-06", "type": "sell",
             "shares": 1000, "nav": 1.2, "amount": 1200.0, "before_15": True},
        ]
        history = make_history({"2025-01-02": 1.5, "2025-01-06": 1.2})
        r = calculate_holdings(records, 1.2, history)
        self.assertEqual(r["total_shares"], 0)
        self.assertLess(r["realized_profit_loss"], 0)


class TestCalculateHoldingsNavLookup(unittest.TestCase):
    def test_nav_lookup_non_trading_day(self):
        records = [make_purchase("000001", "2025-01-04", 1000, 1.0)]
        history = make_history({"2025-01-06": 1.0, "2025-01-07": 1.1})
        r = calculate_holdings(records, 1.1, history)
        self.assertEqual(r["total_shares"], 1000)

    def test_nav_lookup_zero_nav(self):
        records = [make_purchase("000001", "2025-01-02", 1000, 1.0)]
        history = make_history({"2025-01-02": 0.0, "2025-01-03": 1.1})
        r = calculate_holdings(records, 1.1, history)
        # get_nav_from_history returns None when nav<=0, record skipped
        self.assertEqual(r["total_shares"], 0)


class TestCalculateHoldingsPurchaseDetails(unittest.TestCase):
    def test_purchase_details_format(self):
        records = [make_purchase("000001", "2025-01-02", 1000, 1.0)]
        history = make_history({"2025-01-02": 1.0, "2025-01-03": 1.1})
        r = calculate_holdings(records, 1.1, history)
        self.assertIn("purchases", r)
        details = r["purchases"]
        self.assertEqual(len(details), 1)
        self.assertIn("shares", details[0])
        self.assertIn("amount", details[0])

    def test_purchase_details_after_sell(self):
        """卖出后，purchase_details 包含买入和卖出两条记录"""
        records = [
            make_purchase("000001", "2025-01-02", 1000, 1.0),
            {"code": "000001", "date": "2025-01-03", "type": "sell",
             "shares": 600, "nav": 1.1, "amount": 660.0, "before_15": True},
        ]
        history = make_history({"2025-01-02": 1.0, "2025-01-03": 1.1,
                                   "2025-01-06": 1.2})
        r = calculate_holdings(records, 1.2, history)
        # purchase_details 包含买入+卖出共2条
        self.assertEqual(len(r["purchases"]), 2)
        # 剩余份额400
        self.assertAlmostEqual(r["total_shares"], 400, places=2)


class TestCalculateHoldingsAvgCost(unittest.TestCase):
    def test_avg_cost_basic(self):
        records = [
            make_purchase("000001", "2025-01-02", 1000, 1.0),
            make_purchase("000001", "2025-01-03", 1000, 1.2),
        ]
        history = make_history({"2025-01-02": 1.0, "2025-01-03": 1.2,
                                   "2025-01-06": 1.3})
        r = calculate_holdings(records, 1.3, history)
        # 平均成本 = (1000*1.0 + 1000*1.2) / 2000 = 1.1
        self.assertAlmostEqual(r["avg_cost_nav"], 1.1, places=4)

    def test_avg_cost_after_sell(self):
        records = [
            make_purchase("000001", "2025-01-02", 1000, 1.0),
            make_purchase("000001", "2025-01-03", 1000, 1.4),
            {"code": "000001", "date": "2025-01-06", "type": "sell",
             "shares": 1000, "nav": 1.2, "amount": 1200.0, "before_15": True},
        ]
        history = make_history({"2025-01-02": 1.0, "2025-01-03": 1.4,
                                   "2025-01-06": 1.2, "2025-01-07": 1.3})
        r = calculate_holdings(records, 1.3, history)
        # 第一笔1000份全卖，剩余第二笔1000份，成本1.4
        self.assertAlmostEqual(r["avg_cost_nav"], 1.4, places=4)


if __name__ == "__main__":
    unittest.main()
