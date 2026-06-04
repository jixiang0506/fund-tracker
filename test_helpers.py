"""测试辅助函数，供 test_calculate_holdings.py 和 test_cumulative_returns.py 复用"""


def make_purchase(code, date, shares, nav, amount=None, before_15=True):
    a = amount if amount is not None else round(shares * nav, 2)
    return {"code": code, "date": date, "amount": a, "before_15": before_15}


def make_history(nav_map):
    return [{"date": d, "nav": v} for d, v in nav_map.items()]
