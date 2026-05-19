#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单元测试 - calculate_cumulative_returns() 累计收益率计算"""

import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fetch_fund_data import calculate_cumulative_returns


# ============================================================
# 辅助工具
# ============================================================

def make_history(entries):
    """快速构建历史净值列表

    Args:
        entries: [(date, nav), ...] 按日期升序
    """
    return [{"date": d, "nav": n, "change_percent": 0} for d, n in entries]


def make_purchase(date, amount, ptype="buy", before_15=True):
    """快速构建原始交易记录"""
    return {"date": date, "amount": amount, "type": ptype, "before_15": before_15}


# ============================================================
# 测试用例
# ============================================================

class TestCumulativeReturnsFIFO:
    """精确 FIFO 模拟路径测试（传入 original_purchases + history_for_nav）"""

    def test_empty_history(self):
        """空历史数据返回空列表"""
        result = calculate_cumulative_returns([], [])
        assert result == []

    def test_single_buy_profit(self):
        """单笔买入盈利场景"""
        history = make_history([
            ("2024-01-10", 1.0),  # 买入日
            ("2024-01-11", 1.1),  # 涨10%
            ("2024-01-12", 1.2),  # 涨20%
        ])
        original_purchases = [make_purchase("2024-01-10", 1000)]
        history_for_nav = history

        result = calculate_cumulative_returns(
            history, [],
            original_purchases=original_purchases,
            history_for_nav=history_for_nav
        )

        # 1月10日: 成本1000, 市值1000, 收益率0%
        assert result[0] == 0.0
        # 1月11日: 成本1000, 市值1100, 收益率10%
        assert result[1] == 10.0
        # 1月12日: 成本1000, 市值1200, 收益率20%
        assert result[2] == 20.0

    def test_single_buy_loss(self):
        """单笔买入亏损场景"""
        history = make_history([
            ("2024-01-10", 2.0),  # 买入日
            ("2024-01-11", 1.8),  # 跌10%
        ])
        original_purchases = [make_purchase("2024-01-10", 2000)]
        history_for_nav = history

        result = calculate_cumulative_returns(
            history, [],
            original_purchases=original_purchases,
            history_for_nav=history_for_nav
        )

        # 1月10日: 2000/2.0=1000份, 成本2000, 市值2000, 收益率0%
        assert result[0] == 0.0
        # 1月11日: 1000份*1.8=1800, 成本2000, 收益率(1800-2000)/2000*100=-10%
        assert result[1] == -10.0

    def test_no_purchase_before_date(self):
        """买入前的日期收益率为None"""
        history = make_history([
            ("2024-01-09", 0.9),  # 买入前
            ("2024-01-10", 1.0),  # 买入日
            ("2024-01-11", 1.1),  # 涨10%
        ])
        original_purchases = [make_purchase("2024-01-10", 1000)]

        result = calculate_cumulative_returns(
            history, [],
            original_purchases=original_purchases,
            history_for_nav=history
        )

        # 买入前无持仓 → None
        assert result[0] is None
        # 买入日收益率0%
        assert result[1] == 0.0
        # 涨10%
        assert result[2] == 10.0

    def test_fifo_with_sell(self):
        """FIFO卖出后收益率变化"""
        history = make_history([
            ("2024-01-10", 1.0),  # 买入A: 1000份@1.0
            ("2024-01-15", 2.0),  # 卖出: 500份@2.0
            ("2024-01-20", 3.0),  # 后续
        ])
        original_purchases = [
            make_purchase("2024-01-10", 1000),           # 买1000份@1.0
            make_purchase("2024-01-15", 1000, "sell"),   # 卖出金额1000=500份@2.0
        ]

        result = calculate_cumulative_returns(
            history, [],
            original_purchases=original_purchases,
            history_for_nav=history
        )

        # 1月10日: 1000份, 成本1000, 市值1000, 收益率0%
        assert result[0] == 0.0

        # 1月15日: 卖出500份后剩余500份@1.0
        # 成本=500, 市值=500*2.0=1000, 收益率=(1000-500)/500*100=100%
        assert result[1] == 100.0

        # 1月20日: 500份, 成本500, 市值=500*3.0=1500, 收益率=(1500-500)/500*100=200%
        assert result[2] == 200.0

    def test_two_buys_different_nav(self):
        """两笔不同净值买入"""
        history = make_history([
            ("2024-01-10", 1.0),  # 买入A: 1000份@1.0
            ("2024-01-15", 2.0),  # 买入B: 500份@2.0
            ("2024-01-20", 3.0),  # 后续
        ])
        original_purchases = [
            make_purchase("2024-01-10", 1000),   # 1000份@1.0
            make_purchase("2024-01-15", 1000),   # 500份@2.0
        ]

        result = calculate_cumulative_returns(
            history, [],
            original_purchases=original_purchases,
            history_for_nav=history
        )

        # 1月10日: 1000份@1.0, 成本1000, 市值1000, 收益率0%
        assert result[0] == 0.0

        # 1月15日: 1500份, 成本=1000+1000=2000, 市值=1500*2.0=3000, 收益率=50%
        assert result[1] == 50.0

        # 1月20日: 1500份, 成本2000, 市值=1500*3.0=4500, 收益率=125%
        assert result[2] == 125.0


class TestCumulativeReturnsFallback:
    """简化计算回退路径测试（不传 original_purchases）"""

    def test_fallback_basic(self):
        """回退路径：基于 purchase_details 简化计算"""
        history = make_history([
            ("2024-01-10", 1.0),
            ("2024-01-11", 1.1),
        ])
        # purchase_details 格式（来自 calculate_holdings 的输出）
        purchase_details = [
            {"date": "2024-01-10", "amount": 1000, "shares": 1000.0, "type": "buy"}
        ]

        result = calculate_cumulative_returns(history, purchase_details)

        # 1月10日: 成本1000, 市值1000, 收益率0%
        assert result[0] == 0.0
        # 1月11日: 成本1000, 市值1100, 收益率10%
        assert result[1] == 10.0

    def test_fallback_with_sell(self):
        """回退路径：含卖出记录"""
        history = make_history([
            ("2024-01-10", 1.0),
            ("2024-01-15", 2.0),
            ("2024-01-20", 3.0),
        ])
        purchase_details = [
            {"date": "2024-01-10", "amount": 1000, "shares": 1000.0, "type": "buy"},
            {"date": "2024-01-15", "amount": -1000, "shares": -500.0, "type": "sell", "fifo_cost": 500},
        ]

        result = calculate_cumulative_returns(history, purchase_details)

        # 1月10日: 成本1000, 市值1000, 收益率0%
        assert result[0] == 0.0

        # 1月15日: 扣除后 shares=500, invested=1000-500=500, 市值=500*2.0=1000
        # 收益率=(1000-500)/500*100=100%
        assert result[1] == 100.0

        # 1月20日: 500份, 成本500, 市值=500*3.0=1500, 收益率=200%
        assert result[2] == 200.0

    def test_fallback_no_purchases(self):
        """回退路径：无交易记录"""
        history = make_history([("2024-01-10", 1.0)])
        result = calculate_cumulative_returns(history, [])

        # 无交易 → 全部None
        assert result == [None]


class TestCumulativeReturnsEdgeCases:
    """边界情况测试"""

    def test_all_none_before_first_buy(self):
        """买入前所有时点为None"""
        history = make_history([
            ("2024-01-08", 0.9),
            ("2024-01-09", 0.95),
            ("2024-01-10", 1.0),  # 买入日
        ])
        original_purchases = [make_purchase("2024-01-10", 1000)]

        result = calculate_cumulative_returns(
            history, [],
            original_purchases=original_purchases,
            history_for_nav=history
        )

        assert result[0] is None
        assert result[1] is None
        assert result[2] == 0.0

    def test_zero_nav_gives_none(self):
        """净值为0的时点返回None（无法计算）"""
        history = make_history([
            ("2024-01-10", 0.0),  # 零净值
            ("2024-01-11", 1.1),
        ])
        original_purchases = [make_purchase("2024-01-11", 1000)]

        result = calculate_cumulative_returns(
            history, [],
            original_purchases=original_purchases,
            history_for_nav=history
        )

        # 1月10日: 买入前 → None
        assert result[0] is None
        # 1月11日: 买入日, 收益率0%
        assert result[1] == 0.0

    def test_result_length_matches_history(self):
        """返回结果长度与历史数据一致"""
        history = make_history([
            ("2024-01-10", 1.0),
            ("2024-01-11", 1.1),
            ("2024-01-12", 1.2),
            ("2024-01-15", 1.3),
            ("2024-01-16", 1.4),
        ])
        original_purchases = [make_purchase("2024-01-10", 1000)]

        result = calculate_cumulative_returns(
            history, [],
            original_purchases=original_purchases,
            history_for_nav=history
        )

        assert len(result) == len(history)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
