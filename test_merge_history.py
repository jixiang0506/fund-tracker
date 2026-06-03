"""
测试 merge_history 函数（12个用例）
覆盖：基础合并、空日期过滤、增量场景
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fetch_fund_data import merge_history


def make_entry(date, nav, change_percent=0.0):
    return {"date": date, "nav": nav, "change_percent": change_percent}


class TestMergeHistoryBasic(unittest.TestCase):
    """基础合并（空/无重叠/覆盖/排序）"""

    def test_merge_empty_existing(self):
        """existing 为空"""
        new = [make_entry("2025-01-02", 1.0)]
        result = merge_history([], new)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["date"], "2025-01-02")

    def test_merge_empty_new(self):
        """new 为空"""
        existing = [make_entry("2025-01-02", 1.0)]
        result = merge_history(existing, [])
        self.assertEqual(len(result), 1)

    def test_merge_no_overlap(self):
        """无重叠：new 追加到末尾"""
        existing = [make_entry("2025-01-02", 1.0)]
        new = [make_entry("2025-01-03", 1.05)]
        result = merge_history(existing, new)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["date"], "2025-01-02")
        self.assertEqual(result[1]["date"], "2025-01-03")

    def test_merge_overlap_newer_nav(self):
        """重叠：new 中日期相同但净值更新"""
        existing = [make_entry("2025-01-02", 1.0)]
        new = [make_entry("2025-01-02", 1.02)]  # 更新的净值
        result = merge_history(existing, new)
        # 应使用 new 的净值
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["nav"], 1.02)

    def test_merge_sorted_result(self):
        """合并后按日期排序"""
        existing = [make_entry("2025-01-03", 1.05), make_entry("2025-01-02", 1.0)]
        new = [make_entry("2025-01-04", 1.08)]
        result = merge_history(existing, new)
        dates = [r["date"] for r in result]
        self.assertEqual(dates, sorted(dates))


class TestMergeHistoryEmptyDate(unittest.TestCase):
    """空日期过滤（existing/new/双方/全空）"""

    def test_existing_empty_date_filtered(self):
        """existing 中含空日期条目，应被过滤"""
        existing = [make_entry("2025-01-02", 1.0), {"nav": 1.1}]  # 缺少 date
        new = [make_entry("2025-01-03", 1.05)]
        result = merge_history(existing, new)
        dates = [r["date"] for r in result if "date" in r]
        self.assertNotIn("", dates)

    def test_new_empty_date_filtered(self):
        """new 中含空日期条目，应被过滤"""
        existing = [make_entry("2025-01-02", 1.0)]
        new = [make_entry("2025-01-03", 1.05), {"nav": 1.1}]  # 缺少 date
        result = merge_history(existing, new)
        # 空日期条目应被过滤
        self.assertTrue(all("date" in r and r["date"] for r in result))

    def test_both_have_empty_date(self):
        """双方都有空日期条目"""
        existing = [{"nav": 1.0}]  # 缺少 date
        new = [{"nav": 1.1}]  # 缺少 date
        result = merge_history(existing, new)
        # 所有条目都应有 date
        self.assertTrue(all("date" in r and r["date"] for r in result))

    def test_all_empty_dates(self):
        """全为空日期"""
        existing = [{"nav": 1.0}]
        new = [{"nav": 1.1}]
        result = merge_history(existing, new)
        self.assertEqual(len(result), 0)


class TestMergeHistoryIncremental(unittest.TestCase):
    """增量场景（追加/NAV纠正/缺口修复）"""

    def test_append_new_days(self):
        """追加新天数"""
        existing = [make_entry("2025-01-02", 1.0)]
        new = [make_entry("2025-01-02", 1.0), make_entry("2025-01-03", 1.05)]
        result = merge_history(existing, new)
        self.assertEqual(len(result), 2)

    def test_nav_correction(self):
        """NAV纠正：同一天，new 的净值更准确"""
        existing = [make_entry("2025-01-02", 1.0)]
        new = [make_entry("2025-01-02", 1.01)]  # 纠正后的净值
        result = merge_history(existing, new)
        self.assertEqual(result[0]["nav"], 1.01)

    def test_gap_repair(self):
        """缺口修复：new 中包含缺失日期"""
        existing = [make_entry("2025-01-02", 1.0), make_entry("2025-01-06", 1.08)]
        new = [
            make_entry("2025-01-02", 1.0),
            make_entry("2025-01-03", 1.02),  # 缺失的日期
            make_entry("2025-01-06", 1.08),
        ]
        result = merge_history(existing, new)
        dates = [r["date"] for r in result]
        self.assertIn("2025-01-03", dates)


if __name__ == "__main__":
    unittest.main()
