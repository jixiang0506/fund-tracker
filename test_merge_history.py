#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单元测试 - merge_history() 历史数据合并逻辑"""

import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fetch_fund_data import merge_history


# ============================================================
# 辅助工具
# ============================================================

def make_entry(date, nav=1.0):
    """快速构建历史条目"""
    return {"date": date, "nav": nav, "change_percent": 0}


# ============================================================
# 测试用例
# ============================================================

class TestMergeHistoryBasic:
    """基础合并测试"""

    def test_empty_existing(self):
        """existing为空，返回排序后的new_entries"""
        new = [make_entry("2024-01-15", 1.5), make_entry("2024-01-10", 1.0)]
        result = merge_history([], new)

        assert len(result) == 2
        assert result[0]["date"] == "2024-01-10"
        assert result[1]["date"] == "2024-01-15"

    def test_empty_new(self):
        """new_entries为空，返回existing不变"""
        existing = [make_entry("2024-01-10", 1.0)]
        result = merge_history(existing, [])

        assert result is existing  # 同一对象引用
        assert len(result) == 1

    def test_both_empty(self):
        """两者都为空"""
        result = merge_history([], [])
        assert result == []

    def test_simple_merge_no_overlap(self):
        """无重叠合并"""
        existing = [make_entry("2024-01-10", 1.0)]
        new = [make_entry("2024-01-15", 1.5)]
        result = merge_history(existing, new)

        assert len(result) == 2
        assert result[0]["date"] == "2024-01-10"
        assert result[1]["date"] == "2024-01-15"

    def test_merge_with_overlap_new_overwrites(self):
        """重叠日期：new_entries覆盖existing（NAV纠正场景）"""
        existing = [make_entry("2024-01-10", 1.0), make_entry("2024-01-15", 1.5)]
        new = [make_entry("2024-01-15", 1.55)]  # NAV纠正
        result = merge_history(existing, new)

        assert len(result) == 2
        assert result[0]["nav"] == 1.0
        assert result[1]["nav"] == 1.55  # 被new覆盖

    def test_merge_preserves_order(self):
        """合并结果按日期升序排列"""
        existing = [make_entry("2024-01-15", 1.5), make_entry("2024-01-20", 2.0)]
        new = [make_entry("2024-01-10", 1.0), make_entry("2024-01-18", 1.8)]
        result = merge_history(existing, new)

        dates = [r["date"] for r in result]
        assert dates == ["2024-01-10", "2024-01-15", "2024-01-18", "2024-01-20"]


class TestMergeHistoryEmptyDate:
    """空日期过滤测试"""

    def test_empty_date_in_existing(self):
        """existing中含空日期条目，过滤掉"""
        existing = [
            make_entry("2024-01-10", 1.0),
            {"nav": 0.5, "change_percent": 0},  # 无date字段
        ]
        new = [make_entry("2024-01-15", 1.5)]
        result = merge_history(existing, new)

        # 空日期条目被过滤
        assert len(result) == 2
        assert all(r.get("date") for r in result)

    def test_empty_date_in_new(self):
        """new_entries中含空日期条目，过滤掉"""
        existing = [make_entry("2024-01-10", 1.0)]
        new = [
            make_entry("2024-01-15", 1.5),
            {"nav": 0.5, "change_percent": 0},  # 无date字段
            make_entry("", 0.0),  # 空字符串date
        ]
        result = merge_history(existing, new)

        # 空日期和空字符串日期都被过滤
        assert len(result) == 2

    def test_empty_date_in_both(self):
        """existing和new都含空日期"""
        existing = [
            make_entry("2024-01-10", 1.0),
            {"nav": 0.5},  # 无date
        ]
        new = [
            make_entry("2024-01-15", 1.5),
            {"date": "", "nav": 0.0},  # 空date
        ]
        result = merge_history(existing, new)

        assert len(result) == 2
        assert all(r.get("date") for r in result)

    def test_all_entries_empty_date_in_new(self):
        """new_entries全部为空日期"""
        existing = [make_entry("2024-01-10", 1.0)]
        new = [
            {"nav": 0.5},
            {"date": "", "nav": 0.0},
        ]
        result = merge_history(existing, new)

        # 只有existing的有效条目
        assert len(result) == 1
        assert result[0]["date"] == "2024-01-10"


class TestMergeHistoryIncremental:
    """增量拉取合并测试（模拟实际使用场景）"""

    def test_incremental_append(self):
        """增量数据追加到缓存末尾"""
        cached = [
            make_entry("2024-01-10", 1.0),
            make_entry("2024-01-11", 1.1),
        ]
        incremental = [
            make_entry("2024-01-12", 1.2),
            make_entry("2024-01-15", 1.3),
        ]
        result = merge_history(cached, incremental)

        assert len(result) == 4
        assert result[-1]["date"] == "2024-01-15"

    def test_incremental_with_nav_correction(self):
        """增量拉取中的7天重叠窗口捕获NAV纠正"""
        cached = [
            make_entry("2024-01-10", 1.0),
            make_entry("2024-01-11", 1.1),
            make_entry("2024-01-12", 1.2),  # 这个值会被纠正
        ]
        # 增量拉取从1月6日开始（7天重叠），包含纠正后的1月12日
        incremental = [
            make_entry("2024-01-12", 1.22),  # NAV纠正
            make_entry("2024-01-15", 1.3),
        ]
        result = merge_history(cached, incremental)

        # 1月12日的值被更新为1.22
        jan12 = [r for r in result if r["date"] == "2024-01-12"][0]
        assert jan12["nav"] == 1.22
        # 总条目数不变（1月12日是覆盖，不是新增）
        assert len(result) == 4

    def test_full_merge_after_gap(self):
        """缺口修复：全量拉取与缓存合并"""
        cached = [
            make_entry("2024-03-01", 1.0),
            make_entry("2024-03-05", 1.1),
        ]
        # 发现早期缺口，全量拉取
        full_fetch = [
            make_entry("2024-01-10", 0.9),
            make_entry("2024-01-20", 0.95),
            make_entry("2024-03-01", 1.01),  # NAV纠正
            make_entry("2024-03-05", 1.11),  # NAV纠正
        ]
        result = merge_history(cached, full_fetch)

        assert len(result) == 4
        # cached中的3月1日和5日被覆盖
        assert [r for r in result if r["date"] == "2024-03-01"][0]["nav"] == 1.01
        assert [r for r in result if r["date"] == "2024-03-05"][0]["nav"] == 1.11


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
