# -*- coding: utf-8 -*-
"""
单元测试：calculate_cumulative_returns（重构后应复用 _process_fifo_transaction）

验证每日累计收益率的 FIFO 模拟：买入后随净值上涨为正收益，卖出后队列清空。
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


class TestCumulativeReturns(unittest.TestCase):

    def test_empty_history(self):
        self.assertEqual(
            ffd.calculate_cumulative_returns([], [{"date": "2024-01-01", "amount": 1000}]), []
        )

    def test_no_purchases_returns_none_list(self):
        rates = ffd.calculate_cumulative_returns(_history(), None, _history())
        self.assertEqual(rates, [None, None, None])

    def test_buy_and_appreciate(self):
        purchases = [{"date": "2024-01-01", "amount": 1000}]
        rates = ffd.calculate_cumulative_returns(_history(), purchases, _history())
        self.assertEqual(rates[0], 0.0)
        self.assertEqual(rates[1], 10.0)
        self.assertEqual(rates[2], 20.0)

    def test_sell_empties_queue(self):
        purchases = [
            {"date": "2024-01-01", "amount": 1000},
            {"date": "2024-01-02", "type": "sell", "shares": 1000, "amount": 1000},
        ]
        rates = ffd.calculate_cumulative_returns(_history(), purchases, _history())
        self.assertEqual(rates[0], 0.0)
        self.assertIsNone(rates[1])
        self.assertIsNone(rates[2])

    def test_consistency_with_calculate_holdings(self):
        purchases = [{"date": "2024-01-01", "amount": 1000}]
        rates = ffd.calculate_cumulative_returns(_history(), purchases, _history())
        holdings = ffd.calculate_holdings(purchases, 1.2, _history())
        self.assertEqual(rates[2], 20.0)
        self.assertEqual(holdings["profit_loss_percent"], 20.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
