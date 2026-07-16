# -*- coding: utf-8 -*-
"""
单元测试：共享 FIFO 交易处理函数 _process_fifo_transaction

验证买入/卖出/超卖/净值缺失等核心分支，确保 calculate_holdings 与
calculate_cumulative_returns 复用同一套 FIFO 逻辑时行为一致。
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
    ]


class TestFifo(unittest.TestCase):

    def test_buy_basic(self):
        queue = []
        res = ffd._process_fifo_transaction(
            {"date": "2024-01-01", "amount": 1000}, _history(), queue
        )
        self.assertEqual(res["trans_type"], "buy")
        self.assertEqual(res["nav_on_date"], 1.0)
        self.assertEqual(res["shares"], 1000.0)
        self.assertEqual(res["cost"], 1000.0)
        self.assertEqual(res["amount"], 1000.0)
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["remaining_shares"], 1000.0)

    def test_sell_exact_by_amount(self):
        queue = [{"date": "2024-01-01", "nav": 1.0, "remaining_shares": 1000.0}]
        res = ffd._process_fifo_transaction(
            {"date": "2024-01-02", "type": "sell", "amount": 1100}, _history(), queue
        )
        self.assertEqual(res["trans_type"], "sell")
        self.assertAlmostEqual(res["requested_shares"], 1000.0, places=6)
        self.assertAlmostEqual(res["shares"], 1000.0, places=6)
        self.assertAlmostEqual(res["cost"], 1000.0, places=6)
        self.assertAlmostEqual(res["amount"], 1100.0, places=6)
        self.assertFalse(res["is_oversell"])
        self.assertAlmostEqual(queue[0]["remaining_shares"], 0.0, places=9)

    def test_sell_using_shares_field(self):
        queue = [{"date": "2024-01-01", "nav": 1.0, "remaining_shares": 1000.0}]
        res = ffd._process_fifo_transaction(
            {"date": "2024-01-02", "type": "sell", "shares": 500, "amount": 550}, _history(), queue
        )
        self.assertEqual(res["shares"], 500.0)
        self.assertEqual(res["cost"], 500.0)
        self.assertEqual(res["amount"], 550.0)
        self.assertEqual(queue[0]["remaining_shares"], 500.0)

    def test_sell_oversell_corrected(self):
        queue = [{"date": "2024-01-01", "nav": 1.0, "remaining_shares": 1000.0}]
        res = ffd._process_fifo_transaction(
            {"date": "2024-01-02", "type": "sell", "shares": 2000, "amount": 2200}, _history(), queue
        )
        self.assertTrue(res["is_oversell"])
        self.assertEqual(res["requested_shares"], 2000.0)
        self.assertEqual(res["shares"], 1000.0)
        self.assertEqual(res["cost"], 1000.0)
        self.assertEqual(res["amount"], 1100.0)
        self.assertEqual(queue[0]["remaining_shares"], 0.0)

    def test_invalid_nav_returns_none(self):
        queue = []
        res = ffd._process_fifo_transaction(
            {"date": "2024-01-03", "amount": 1000}, _history(), queue
        )
        self.assertIsNone(res)

    def test_fifo_order_oldest_first(self):
        queue = [
            {"date": "2024-01-01", "nav": 1.0, "remaining_shares": 500.0},
            {"date": "2024-01-01", "nav": 2.0, "remaining_shares": 500.0},
        ]
        res = ffd._process_fifo_transaction(
            {"date": "2024-01-02", "type": "sell", "shares": 300, "amount": 330}, _history(), queue
        )
        self.assertEqual(res["cost"], 300.0)
        self.assertEqual(queue[0]["remaining_shares"], 200.0)
        self.assertEqual(queue[1]["remaining_shares"], 500.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
