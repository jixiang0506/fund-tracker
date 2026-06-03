"""
测试 get_nav_from_history 函数（修正版）
"""
import sys, os, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fetch_fund_data import get_nav_from_history


class TestGetNavFromHistory(unittest.TestCase):

    def test_exact_match_first_day(self):
        history = [{"date": "2025-01-02", "nav": 1.0},
                    {"date": "2025-01-03", "nav": 1.1}]
        r = get_nav_from_history(history, "2025-01-02", before_15=True)
        self.assertEqual(r["nav"], 1.0)
        self.assertEqual(r["nav_source"], "exact")

    def test_exact_match_last_day(self):
        history = [{"date": "2025-01-02", "nav": 1.0},
                    {"date": "2025-01-03", "nav": 1.1}]
        r = get_nav_from_history(history, "2025-01-03", before_15=True)
        self.assertEqual(r["nav"], 1.1)

    def test_next_trading_day_weekend(self):
        history = [{"date": "2025-01-02", "nav": 1.0},
                    {"date": "2025-01-03", "nav": 1.1},
                    {"date": "2025-01-06", "nav": 1.2}]
        r = get_nav_from_history(history, "2025-01-04", before_15=False)
        self.assertEqual(r["nav"], 1.2)
        self.assertEqual(r["nav_source"], "next_trading_day")

    def test_next_trading_day_holiday(self):
        history = [{"date": "2025-01-02", "nav": 1.0},
                    {"date": "2025-01-03", "nav": 1.1},
                    {"date": "2025-01-06", "nav": 1.2}]
        r = get_nav_from_history(history, "2025-01-05", before_15=False)
        self.assertEqual(r["nav"], 1.2)

    def test_before_15_false_next_trading_day(self):
        history = [{"date": "2025-01-02", "nav": 1.0},
                    {"date": "2025-01-03", "nav": 1.1}]
        r = get_nav_from_history(history, "2025-01-02", before_15=False)
        # before_15=False: skip exact match, use next day
        self.assertEqual(r["nav"], 1.1)
        self.assertEqual(r["nav_source"], "next_trading_day")

    def test_out_of_range_target_after_history(self):
        history = [{"date": "2025-01-02", "nav": 1.0},
                    {"date": "2025-01-03", "nav": 1.1}]
        # When target > all dates and before_15=True:
        # exact match fails, then loop finds no date > target, returns None
        r = get_nav_from_history(history, "2025-01-10", before_15=True)
        self.assertIsNone(r)

    def test_out_of_range_target_before_history(self):
        history = [{"date": "2025-01-02", "nav": 1.0},
                    {"date": "2025-01-03", "nav": 1.1}]
        r = get_nav_from_history(history, "2024-12-30", before_15=True)
        # All history dates > target, first date is used as next_trading_day
        self.assertEqual(r["nav"], 1.0)
        self.assertEqual(r["nav_source"], "next_trading_day")

    def test_empty_history(self):
        r = get_nav_from_history([], "2025-01-02", before_15=True)
        self.assertIsNone(r)

    def test_multi_day_interval(self):
        history = [{"date": "2025-01-02", "nav": 1.0},
                    {"date": "2025-01-06", "nav": 1.2}]
        r = get_nav_from_history(history, "2025-01-04", before_15=False)
        self.assertEqual(r["nav"], 1.2)

    def test_defensive_get_date(self):
        history = [{"nav": 1.0},  # missing date
                    {"date": "2025-01-03", "nav": 1.1}]
        r = get_nav_from_history(history, "2025-01-03", before_15=True)
        self.assertEqual(r["nav"], 1.1)

    def test_defensive_get_nav(self):
        history = [{"date": "2025-01-02", "nav": 1.0},
                    {"date": "2025-01-03"}]  # missing nav
        r = get_nav_from_history(history, "2025-01-03", before_15=True)
        # safe_float(None) -> 0.0, so nav=0, which means this record
        # may be skipped; but .get("nav", 0) returns 0
        self.assertEqual(r["nav"], 0)

    def test_history_with_empty_date_string(self):
        history = [{"date": "", "nav": 1.0},
                    {"date": "2025-01-03", "nav": 1.1}]
        r = get_nav_from_history(history, "2025-01-03", before_15=True)
        self.assertEqual(r["nav"], 1.1)


if __name__ == "__main__":
    unittest.main()
