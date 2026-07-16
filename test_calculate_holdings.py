# -*- coding: utf-8 -*-
"""
单元测试：calculate_holdings（重构后应复用 _process_fifo_transaction）

覆盖：空记录、纯买入、FIFO 卖出（部分/全部/超卖）、累计收益率与已实现盈亏。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fetch_fund_data as ffd


def _history():
    return [
        {"date": "2024-01-01", "nav": 1.0},
        {"date": "2024-01-02", "nav": 1.1},
        {"date": "2024-01-03", "nav": 1.2},
    ]


class TestCalculateHoldings(unittest.TestCase):

    def test_empty_purchases(self):
        r = ffd.calculate_holdings([], 1.0, _history())
        self.assertEqual(r["total_invested"], 0)
        self.assertEqual(r["total_shares"], 0)
        self.assertEqual(r["current_value"], 0)
        self.assertEqual(r["realized_profit_loss"], 0)

    def test_single_buy(self):
        purchases = [{"date": "2024-01-01", "amount": 1000}]
        r = ffd.calculate_holdings(purchases, 1.2, _history(), fund_code="TEST")
        self.assertEqual(r["total_invested"], 1000.0)
        self.assertEqual(r["total_shares"], 1000.0)
        self.assertEqual(r["current_value"], 1200.0)
        self.assertEqual(r["profit_loss"], 200.0)
        self.assertEqual(r["realized_profit_loss"], 0)
        self.assertEqual(len(r["purchases"]), 1)
        self.assertEqual(r["purchases"][0]["type"], "buy")

    def test_fifo_sell_partial(self):
        purchases = [
            {"date": "2024-01-01", "amount": 1000},
            {"date": "2024-01-02", "type": "sell", "amount": 550},
        ]
        r = ffd.calculate_holdings(purchases, 1.2, _history(), fund_code="TEST")
        self.assertEqual(r["total_shares"], 500.0)
        self.assertEqual(r["total_invested"], 500.0)
        self.assertEqual(r["current_value"], 600.0)
        self.assertEqual(r["realized_profit_loss"], 50.0)
        self.assertEqual(r["profit_loss"], 100.0)
        sell_detail = [p for p in r["purchases"] if p["type"] == "sell"][0]
        self.assertEqual(sell_detail["shares"], -500.0)
        self.assertEqual(sell_detail["realized_profit"], 50.0)
        self.assertEqual(sell_detail["fifo_cost"], 500.0)

    def test_fifo_sell_full(self):
        purchases = [
            {"date": "2024-01-01", "amount": 1000},
            {"date": "2024-01-02", "type": "sell", "amount": 1100},
        ]
        r = ffd.calculate_holdings(purchases, 1.2, _history(), fund_code="TEST")
        self.assertEqual(r["total_shares"], 0.0)
        self.assertEqual(r["total_invested"], 0.0)
        self.assertEqual(r["realized_profit_loss"], 100.0)

    def test_oversell_corrected_in_details(self):
        purchases = [
            {"date": "2024-01-01", "amount": 1000},
            {"date": "2024-01-02", "type": "sell", "shares": 2000, "amount": 2200},
        ]
        r = ffd.calculate_holdings(purchases, 1.2, _history(), fund_code="TEST")
        self.assertEqual(r["total_shares"], 0.0)
        self.assertEqual(r["realized_profit_loss"], 100.0)
        sell_detail = [p for p in r["purchases"] if p["type"] == "sell"][0]
        self.assertEqual(sell_detail["shares"], -1000.0)
        self.assertEqual(sell_detail["amount"], -1100.0)

    def test_avg_cost_nav(self):
        purchases = [{"date": "2024-01-01", "amount": 1000}]
        r = ffd.calculate_holdings(purchases, 1.0, _history(), fund_code="TEST")
        self.assertEqual(r["avg_cost_nav"], 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
