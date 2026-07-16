# -*- coding: utf-8 -*-
"""
单元测试：main() 拆分后的子函数（_init_data / _process_funds / _compute_summary / _save_outputs）

_init_data / _process_funds 涉及文件与网络，仅校验函数存在性与签名；
_compute_summary 用合成数据验证汇总计算逻辑与重构前一致。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fetch_fund_data as ffd


class TestMainSplit(unittest.TestCase):

    def test_subfunctions_exist(self):
        for name in ("_init_data", "_process_funds", "_compute_summary", "_save_outputs", "main"):
            self.assertTrue(hasattr(ffd, name), "缺少函数 %s" % name)
            self.assertTrue(callable(getattr(ffd, name)))

    def test_compute_summary_basic(self):
        all_data = {
            "update_time": "2024-01-03 15:00:00",
            "funds": {
                "p": [{
                    "latest_trading_day_profit": 10.0,
                    "day_before_latest_trading_day_profit": 5.0,
                    "ytd_profit": 30.0,
                    "latest_trading_day_return": 1.0,
                    "day_before_latest_trading_day_return": 0.5,
                    "ytd_return": 3.0,
                    "holdings": {
                        "current_value": 1000.0,
                        "realized_profit_loss": 50.0,
                    },
                }]
            },
            "summary": {
                "total_invested": 0,
                "total_value": 0,
                "total_profit_loss": 0,
                "total_profit_loss_percent": 0,
            },
        }
        ffd._compute_summary(all_data, stale_funds=["X"], failed_funds=["Y"])
        s = all_data["summary"]
        # total_value / total_invested 由 _process_funds 累加，这里模拟已累加
        s["total_value"] = 1100.0
        s["total_invested"] = 1000.0
        ffd._compute_summary(all_data, stale_funds=["X"], failed_funds=["Y"])
        s = all_data["summary"]
        self.assertEqual(s["total_invested"], 1000.0)
        self.assertEqual(s["total_value"], 1100.0)
        self.assertEqual(s["total_profit_loss"], 100.0)
        self.assertEqual(s["total_profit_loss_percent"], 10.0)
        self.assertEqual(s["latest_trading_day_profit_loss"], 10.0)
        self.assertEqual(s["total_realized_profit_loss"], 50.0)
        self.assertEqual(s["yesterday_profit_loss"], s["latest_trading_day_profit_loss"])
        self.assertEqual(s["yesterday_profit_loss_percent"], s["latest_trading_day_profit_loss_percent"])

    def test_compute_summary_empty_funds(self):
        all_data = {
            "update_time": "2024-01-03 15:00:00",
            "funds": {},
            "summary": {
                "total_invested": 0,
                "total_value": 0,
                "total_profit_loss": 0,
                "total_profit_loss_percent": 0,
            },
        }
        ffd._compute_summary(all_data, stale_funds=[], failed_funds=[])
        s = all_data["summary"]
        self.assertEqual(s["total_profit_loss"], 0)
        self.assertEqual(s["total_profit_loss_percent"], 0)
        self.assertEqual(s["ytd_profit_loss_percent"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
