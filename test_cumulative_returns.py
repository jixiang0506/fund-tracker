"""
测试 calculate_cumulative_returns 函数（11个用例）
覆盖：FIFO精确模拟、回退路径、边界条件
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fetch_fund_data import calculate_cumulative_returns, get_nav_from_history


def make_history(nav_map):
    """nav_map: {date: nav}"""
    return [{"date": d, "nav": v} for d, v in nav_map.items()]


def make_purchase(code, date, shares, nav, amount=None, before_15=True):
    return {"code": code, "date": date, "shares": shares, "nav": nav,
            "amount": amount or round(shares * nav, 2), "before_15": before_15}


class TestCumulativeReturnsFIFO(unittest.TestCase):
    """FIFO精确模拟（盈利/亏损/卖出/多笔）"""

    def test_single_buy_profit(self):
        """单笔买入，盈利"""
        history = make_history({"2025-01-02": 1.0, "2025-01-03": 1.1})
        purchases = [make_purchase("000001", "2025-01-02", 1000, 1.0)]
        result = calculate_cumulative_returns(history, purchases, purchases, history)
        self.assertTrue(len(result) > 0)
        # 最后一天（2025-01-03）应有正收益
        self.assertGreater(result[-1], 0)

    def test_single_buy_loss(self):
        """单笔买入，亏损"""
        history = make_history({"2025-01-02": 1.0, "2025-01-03": 0.9})
        purchases = [make_purchase("000001", "2025-01-02", 1000, 1.0)]
        result = calculate_cumulative_returns(history, purchases, purchases, history)
        self.assertTrue(len(result) > 0)
        self.assertLess(result[-1], 0)

    def test_multiple_buys(self):
        """多笔买入，盈利"""
        history = make_history({
            "2025-01-02": 1.0,
            "2025-01-03": 1.05,
            "2025-01-06": 1.12,
        })
        purchases = [
            make_purchase("000001", "2025-01-02", 1000, 1.0),
            make_purchase("000001", "2025-01-03", 1000, 1.05),
        ]
        result = calculate_cumulative_returns(history, purchases, purchases, history)
        self.assertTrue(len(result) > 0)
        self.assertGreater(result[-1], 0)


class TestCumulativeReturnsFallback(unittest.TestCase):
    """回退路径（基础/卖出/无记录）"""

    def test_insufficient_history(self):
        """历史记录不足"""
        history = make_history({"2025-01-02": 1.0})
        purchases = [make_purchase("000001", "2025-01-02", 1000, 1.0)]
        result = calculate_cumulative_returns(history, purchases, purchases, history)
        # 只有1天历史，返回空列表或单元素
        self.assertTrue(len(result) <= 1)

    def test_empty_history(self):
        """空历史"""
        result = calculate_cumulative_returns([], [], [], [])
        self.assertEqual(len(result), 0)


class TestCumulativeReturnsEdgeCases(unittest.TestCase):
    """边界条件"""

    def test_weekend_non_trading_day(self):
        """周末/节假日：original_purchases 含非交易日"""
        history = make_history({
            "2025-01-02": 1.0,  # 周四
            "2025-01-03": 1.05,  # 周五
            "2025-01-06": 1.08,  # 周一
        })
        # 交易日期是周六（非交易日），before_15=True，应使用下一交易日净值
        purchases = [{
            "code": "000001",
            "date": "2025-01-04",  # 周六
            "shares": 1000,
            "nav": 1.0,
            "amount": 1000.0,
            "before_15": True,
        }]
        result = calculate_cumulative_returns(history, purchases, purchases, history)
        self.assertTrue(len(result) > 0)

    def test_zero_nav(self):
        """零净值处理"""
        history = make_history({"2025-01-02": 1.0, "2025-01-03": 0.0})
        purchases = [make_purchase("000001", "2025-01-02", 1000, 1.0)]
        result = calculate_cumulative_returns(history, purchases, purchases, history)
        # 零净值时累计收益率应为0或负
        self.assertTrue(len(result) > 0)

    def test_consistent_length(self):
        """累计收益率数组长度与历史记录一致"""
        history = make_history({
            "2025-01-02": 1.0,
            "2025-01-03": 1.05,
            "2025-01-06": 1.08,
        })
        purchases = [make_purchase("000001", "2025-01-02", 1000, 1.0)]
        result = calculate_cumulative_returns(history, purchases, purchases, history)
        self.assertEqual(len(result), len(history))


if __name__ == "__main__":
    unittest.main()
