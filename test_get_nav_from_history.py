#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单元测试 - get_nav_from_history() 交易日映射逻辑"""

import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fetch_fund_data import get_nav_from_history


# ============================================================
# 辅助工具
# ============================================================

def make_history(entries):
    """快速构建历史净值列表

    Args:
        entries: [(date, nav), ...] 按日期升序
    """
    return [{"date": d, "nav": n, "change_percent": 0} for d, n in entries]


# ============================================================
# 测试用例
# ============================================================

class TestGetNavExactMatch:
    """精确匹配测试"""

    def test_exact_match_before_15(self):
        """15点前提交，精确匹配当天净值"""
        history = make_history([
            ("2024-01-08", 1.0),
            ("2024-01-09", 1.1),
            ("2024-01-10", 1.2),
        ])
        result = get_nav_from_history(history, "2024-01-10", before_15=True)

        assert result is not None
        assert result["nav"] == 1.2
        assert result["nav_source"] == "exact"

    def test_exact_match_first_day(self):
        """精确匹配历史首日"""
        history = make_history([
            ("2024-01-10", 1.0),
            ("2024-01-11", 1.1),
        ])
        result = get_nav_from_history(history, "2024-01-10", before_15=True)

        assert result is not None
        assert result["nav"] == 1.0
        assert result["nav_source"] == "exact"

    def test_exact_match_last_day(self):
        """精确匹配历史末日"""
        history = make_history([
            ("2024-01-10", 1.0),
            ("2024-01-11", 1.1),
        ])
        result = get_nav_from_history(history, "2024-01-11", before_15=True)

        assert result is not None
        assert result["nav"] == 1.1
        assert result["nav_source"] == "exact"


class TestGetNavNextTradingDay:
    """下一交易日映射测试"""

    def test_weekend_finds_next_monday(self):
        """周末日期自动映射到下周一"""
        # 2024-01-13 是周六, 2024-01-15 是周一
        history = make_history([
            ("2024-01-12", 1.0),  # 周五
            ("2024-01-15", 1.2),  # 周一
            ("2024-01-16", 1.3),  # 周二
        ])
        # before_15=True, 但1月13日不在history中 → 找下一个交易日
        result = get_nav_from_history(history, "2024-01-13", before_15=True)

        assert result is not None
        assert result["nav"] == 1.2
        assert result["nav_source"] == "next_trading_day"

    def test_holiday_finds_next_trading_day(self):
        """节假日映射到下一个交易日"""
        # 假设 2024-01-02 是元旦假期
        history = make_history([
            ("2024-01-01", 1.0),
            ("2024-01-03", 1.1),  # 假期后首个交易日
        ])
        result = get_nav_from_history(history, "2024-01-02", before_15=True)

        assert result is not None
        assert result["nav"] == 1.1
        assert result["nav_source"] == "next_trading_day"

    def test_before_15_false_uses_next_day(self):
        """15点后提交，使用下一交易日净值（即使当天有净值）"""
        history = make_history([
            ("2024-01-10", 1.0),
            ("2024-01-11", 1.2),
        ])
        # before_15=False → 跳过当天, 直接找下一个交易日
        result = get_nav_from_history(history, "2024-01-10", before_15=False)

        assert result is not None
        assert result["nav"] == 1.2
        assert result["nav_source"] == "next_trading_day"

    def test_before_15_false_non_trading_day(self):
        """15点后 + 非交易日 → 仍找下一交易日"""
        history = make_history([
            ("2024-01-12", 1.0),  # 周五
            ("2024-01-15", 1.2),  # 周一
        ])
        result = get_nav_from_history(history, "2024-01-13", before_15=False)

        assert result is not None
        assert result["nav"] == 1.2
        assert result["nav_source"] == "next_trading_day"


class TestGetNavEdgeCases:
    """边界情况测试"""

    def test_date_after_all_history(self):
        """目标日期在所有历史记录之后 → 返回None"""
        history = make_history([
            ("2024-01-10", 1.0),
            ("2024-01-11", 1.1),
        ])
        result = get_nav_from_history(history, "2024-01-20", before_15=True)

        assert result is None

    def test_date_after_all_history_before_15_false(self):
        """目标日期在所有历史记录之后(before_15=False) → 返回None"""
        history = make_history([
            ("2024-01-10", 1.0),
            ("2024-01-11", 1.1),
        ])
        result = get_nav_from_history(history, "2024-01-20", before_15=False)

        assert result is None

    def test_empty_history(self):
        """空历史记录 → 返回None"""
        result = get_nav_from_history([], "2024-01-10", before_15=True)

        assert result is None

    def test_exact_match_takes_priority_over_next_day(self):
        """before_15=True时，精确匹配优先于下一交易日"""
        history = make_history([
            ("2024-01-10", 1.0),
            ("2024-01-11", 1.2),
        ])
        result = get_nav_from_history(history, "2024-01-10", before_15=True)

        # 应该是精确匹配1.0, 而不是下一交易日1.2
        assert result["nav"] == 1.0
        assert result["nav_source"] == "exact"

    def test_gap_of_multiple_days(self):
        """多天间隔（如春节长假）映射到假期后首个交易日"""
        history = make_history([
            ("2024-02-08", 1.0),   # 假期前最后交易日
            ("2024-02-19", 1.2),   # 假期后首个交易日
        ])
        # 春节期间某天
        result = get_nav_from_history(history, "2024-02-12", before_15=True)

        assert result is not None
        assert result["nav"] == 1.2
        assert result["nav_source"] == "next_trading_day"

    def test_single_entry_history(self):
        """只有一条历史记录"""
        history = make_history([("2024-01-10", 1.5)])

        # 精确匹配
        result = get_nav_from_history(history, "2024-01-10", before_15=True)
        assert result is not None
        assert result["nav"] == 1.5
        assert result["nav_source"] == "exact"

        # 日期之前：1月9日非交易日，1月10日在历史中且在1月9日之后
        # → 找到下一交易日1月10日，返回next_trading_day
        result = get_nav_from_history(history, "2024-01-09", before_15=True)
        assert result is not None
        assert result["nav"] == 1.5
        assert result["nav_source"] == "next_trading_day"

        # 日期之后 → 无匹配
        result = get_nav_from_history(history, "2024-01-11", before_15=True)
        assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
