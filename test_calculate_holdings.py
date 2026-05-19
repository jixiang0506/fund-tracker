#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单元测试 - calculate_holdings() FIFO 买卖计算"""

import sys
import os
import pytest

# 确保能导入项目模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fetch_fund_data import calculate_holdings, get_nav_from_history


# ============================================================
# 辅助工具
# ============================================================

def make_history(entries):
    """快速构建历史净值列表

    Args:
        entries: [(date, nav), ...] 按日期升序
    Returns:
        list[dict] 标准格式
    """
    return [{"date": d, "nav": n, "change_percent": 0} for d, n in entries]


def make_purchase(date, amount, ptype="buy", before_15=True):
    """快速构建交易记录"""
    return {"date": date, "amount": amount, "type": ptype, "before_15": before_15}


# ============================================================
# 测试用例
# ============================================================

class TestCalculateHoldingsBasic:
    """基础场景测试"""

    def test_empty_purchases(self):
        """空交易记录返回零值"""
        result = calculate_holdings([], 1.0, [])
        assert result["total_invested"] == 0
        assert result["total_shares"] == 0
        assert result["current_value"] == 0
        assert result["profit_loss"] == 0
        assert result["profit_loss_percent"] == 0
        assert result["realized_profit_loss"] == 0

    def test_single_buy(self):
        """单笔买入"""
        history = make_history([("2024-01-10", 1.0)])
        purchases = [make_purchase("2024-01-10", 1000)]

        result = calculate_holdings(purchases, 1.2, history)

        assert result["total_shares"] == 1000.0   # 1000 / 1.0
        assert result["total_invested"] == 1000.0   # 成本 = 份额 * 买入净值
        assert result["current_value"] == 1200.0     # 1000 * 1.2
        assert result["profit_loss"] == 200.0        # 1200 - 1000
        assert result["profit_loss_percent"] == 20.0  # 200/1000 * 100

    def test_two_buys_same_nav(self):
        """两笔买入相同净值"""
        history = make_history([("2024-01-10", 1.0), ("2024-01-20", 1.0)])
        purchases = [
            make_purchase("2024-01-10", 1000),
            make_purchase("2024-01-20", 2000),
        ]

        result = calculate_holdings(purchases, 1.5, history)

        assert result["total_shares"] == 3000.0   # 3000 / 1.0
        assert result["total_invested"] == 3000.0
        assert result["current_value"] == 4500.0   # 3000 * 1.5
        assert result["profit_loss"] == 1500.0

    def test_two_buys_different_nav(self):
        """两笔买入不同净值"""
        history = make_history([("2024-01-10", 1.0), ("2024-01-20", 2.0)])
        purchases = [
            make_purchase("2024-01-10", 1000),   # 1000份
            make_purchase("2024-01-20", 2000),   # 1000份
        ]

        result = calculate_holdings(purchases, 3.0, history)

        assert result["total_shares"] == 2000.0    # 1000 + 1000
        assert result["current_value"] == 6000.0    # 2000 * 3.0
        # 成本: 1000*1.0 + 1000*2.0 = 3000
        assert result["total_invested"] == 3000.0
        assert result["profit_loss"] == 3000.0      # 6000 - 3000


class TestCalculateHoldingsSell:
    """卖出场景测试"""

    def test_full_sell(self):
        """全部卖出：份额归零"""
        history = make_history([
            ("2024-01-10", 1.0),  # 买入
            ("2024-02-10", 2.0),  # 卖出
        ])
        purchases = [
            make_purchase("2024-01-10", 1000),              # 买1000份
            make_purchase("2024-02-10", 2000, "sell"),      # 卖出金额=2000, 份额=1000
        ]

        result = calculate_holdings(purchases, 2.5, history)

        assert result["total_shares"] == 0
        assert result["total_invested"] == 0
        assert result["current_value"] == 0
        assert result["profit_loss"] == 0
        # 已实现盈亏: 卖出1000份 * 2.0 - 成本1000份 * 1.0 = 1000
        assert result["realized_profit_loss"] == 1000.0

    def test_partial_sell(self):
        """部分卖出"""
        history = make_history([
            ("2024-01-10", 1.0),  # 买入
            ("2024-02-10", 2.0),  # 卖出
        ])
        purchases = [
            make_purchase("2024-01-10", 1000),              # 买1000份
            make_purchase("2024-02-10", 1000, "sell"),      # 卖出金额=1000, 份额=500
        ]

        result = calculate_holdings(purchases, 3.0, history)

        # 剩余500份, 成本=500*1.0=500
        assert result["total_shares"] == 500.0
        assert result["total_invested"] == 500.0
        assert result["current_value"] == 1500.0   # 500 * 3.0
        # 已实现盈亏: 卖出500份 * 2.0 - 成本500份 * 1.0 = 500
        assert result["realized_profit_loss"] == 500.0

    def test_fifo_multi_buy_then_sell(self):
        """多笔买入后卖出（FIFO先抵扣最早的买入）"""
        history = make_history([
            ("2024-01-10", 1.0),  # 买入A
            ("2024-01-20", 2.0),  # 买入B
            ("2024-02-10", 3.0),  # 卖出
        ])
        purchases = [
            make_purchase("2024-01-10", 1000),   # 买A: 1000份@1.0
            make_purchase("2024-01-20", 2000),   # 买B: 1000份@2.0
            make_purchase("2024-02-10", 3000, "sell"),  # 卖出: 1000份@3.0
        ]

        result = calculate_holdings(purchases, 4.0, history)

        # FIFO: 卖出1000份先抵扣A(1000份@1.0), A全部抵扣完
        # 剩余: B的1000份@2.0
        assert result["total_shares"] == 1000.0
        assert result["total_invested"] == 2000.0   # 1000 * 2.0
        assert result["current_value"] == 4000.0     # 1000 * 4.0
        # 已实现盈亏: 1000份 * 3.0 - 1000份 * 1.0 = 2000
        assert result["realized_profit_loss"] == 2000.0

    def test_fifo_partial_deduction_from_two_buys(self):
        """FIFO：卖出份额跨越两笔买入"""
        history = make_history([
            ("2024-01-10", 1.0),  # 买入A
            ("2024-01-20", 2.0),  # 买入B
            ("2024-02-10", 3.0),  # 卖出
        ])
        purchases = [
            make_purchase("2024-01-10", 500),    # 买A: 500份@1.0
            make_purchase("2024-01-20", 2000),   # 买B: 1000份@2.0
            make_purchase("2024-02-10", 3000, "sell"),  # 卖出: 1000份@3.0
        ]

        result = calculate_holdings(purchases, 4.0, history)

        # FIFO: 先抵扣A的500份@1.0, 再抵扣B的500份@2.0
        # B剩余500份@2.0
        assert result["total_shares"] == 500.0
        assert result["total_invested"] == 1000.0   # 500 * 2.0
        # 已实现盈亏: (500*3.0 - 500*1.0) + (500*3.0 - 500*2.0) = 1000 + 500 = 1500
        assert result["realized_profit_loss"] == 1500.0

    def test_sell_at_loss(self):
        """亏损卖出"""
        history = make_history([
            ("2024-01-10", 2.0),  # 买入
            ("2024-02-10", 1.0),  # 卖出
        ])
        purchases = [
            make_purchase("2024-01-10", 2000),              # 买1000份@2.0
            make_purchase("2024-02-10", 500, "sell"),       # 卖出500份@1.0
        ]

        result = calculate_holdings(purchases, 1.5, history)

        # 剩余500份@2.0, 成本=1000
        assert result["total_shares"] == 500.0
        assert result["total_invested"] == 1000.0
        # 已实现盈亏: 500份*1.0 - 500份*2.0 = -500
        assert result["realized_profit_loss"] == -500.0


class TestCalculateHoldingsNavLookup:
    """净值查找相关测试"""

    def test_non_trading_day_uses_next_trading_day(self):
        """非交易日（周末）自动使用下一交易日净值"""
        history = make_history([
            ("2024-01-12", 1.0),  # 周五
            ("2024-01-15", 1.1),  # 周一
        ])
        # 周六买入, before_15默认True, 但1月13日不在history中
        # → 应自动找到下一交易日(1月15日)净值1.1
        purchases = [make_purchase("2024-01-13", 1100, before_15=True)]

        result = calculate_holdings(purchases, 1.2, history)

        # 份额 = 1100 / 1.1 = 1000
        assert result["total_shares"] == 1000.0
        assert result["total_invested"] == 1100.0

    def test_before_15_false_uses_next_day(self):
        """15点后提交使用下一交易日净值"""
        history = make_history([
            ("2024-01-10", 1.0),  # 当天
            ("2024-01-11", 1.2),  # 下一交易日
        ])
        # 15点后提交, 应使用1月11日净值1.2
        purchases = [make_purchase("2024-01-10", 1200, before_15=False)]

        result = calculate_holdings(purchases, 1.5, history)

        # 份额 = 1200 / 1.2 = 1000
        assert result["total_shares"] == 1000.0
        assert result["total_invested"] == 1200.0

    def test_nav_zero_skips_record(self):
        """净值为0时跳过该笔记录"""
        history = make_history([("2024-01-10", 0.0)])
        purchases = [make_purchase("2024-01-10", 1000)]

        result = calculate_holdings(purchases, 1.0, history)

        # 净值0 → 跳过, 无持仓
        assert result["total_shares"] == 0
        assert result["total_invested"] == 0


class TestCalculateHoldingsPurchaseDetails:
    """交易详情记录测试"""

    def test_buy_purchase_details(self):
        """买入详情格式正确"""
        history = make_history([("2024-01-10", 1.5)])
        purchases = [make_purchase("2024-01-10", 1500)]

        result = calculate_holdings(purchases, 2.0, history)

        assert len(result["purchases"]) == 1
        detail = result["purchases"][0]
        assert detail["date"] == "2024-01-10"
        assert detail["amount"] == 1500
        assert detail["nav"] == 1.5
        assert detail["shares"] == 1000.0
        assert detail["type"] == "buy"

    def test_sell_purchase_details(self):
        """卖出详情格式正确"""
        history = make_history([
            ("2024-01-10", 1.0),
            ("2024-02-10", 2.0),
        ])
        purchases = [
            make_purchase("2024-01-10", 1000),
            make_purchase("2024-02-10", 1000, "sell"),  # 卖500份
        ]

        result = calculate_holdings(purchases, 2.5, history)

        sell_detail = result["purchases"][1]
        assert sell_detail["type"] == "sell"
        assert sell_detail["amount"] == -1000       # 金额为负
        assert sell_detail["shares"] == -500.0       # 份额为负
        assert "realized_profit" in sell_detail
        assert "fifo_cost" in sell_detail

    def test_purchase_details_sorted_by_date(self):
        """交易详情按日期排序"""
        history = make_history([
            ("2024-01-20", 2.0),
            ("2024-01-10", 1.0),
        ])
        purchases = [
            make_purchase("2024-01-20", 2000),   # 后买但排前面
            make_purchase("2024-01-10", 1000),   # 先买
        ]

        result = calculate_holdings(purchases, 3.0, history)

        # 应按日期排序: 1月10日在前
        assert result["purchases"][0]["date"] == "2024-01-10"
        assert result["purchases"][1]["date"] == "2024-01-20"


class TestCalculateHoldingsAvgCost:
    """平均持仓成本测试"""

    def test_avg_cost_single_buy(self):
        """单笔买入的平均成本等于买入净值"""
        history = make_history([("2024-01-10", 1.5)])
        purchases = [make_purchase("2024-01-10", 1500)]

        result = calculate_holdings(purchases, 2.0, history)

        assert result["avg_cost_nav"] == 1.5

    def test_avg_cost_two_buys(self):
        """两笔买入的加权平均成本"""
        history = make_history([
            ("2024-01-10", 1.0),   # 买1000份
            ("2024-01-20", 2.0),   # 买500份
        ])
        purchases = [
            make_purchase("2024-01-10", 1000),
            make_purchase("2024-01-20", 1000),
        ]

        result = calculate_holdings(purchases, 3.0, history)

        # 加权平均: (1000*1.0 + 500*2.0) / (1000+500) = 2000/1500 ≈ 1.3333
        assert result["avg_cost_nav"] == round(2000 / 1500, 4)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
