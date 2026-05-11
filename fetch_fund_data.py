#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基金收益追踪系统 - 数据抓取脚本
从天天基金网获取基金数据，生成JSON文件供前端展示
"""

import requests
import json
import os
from datetime import datetime, timedelta
import time

# 基金列表配置
FUNDS = {
    "支付宝": ["270023", "016665", "001438", "002112", "018230"],
    "理财通": ["018147", "012922", "019018"],
    "招商银行": ["021277", "000390", "020723"]
}

# 天天基金API
API_BASE = "http://api.fund.eastmoney.com/f10/lsjz"
REALTIME_API = "http://fundgz.1234567.com.cn/js/{}.js"

def fetch_fund_realtime(fund_code):
    """获取基金实时数据"""
    try:
        url = REALTIME_API.format(fund_code)
        response = requests.get(url, timeout=10)
        response.raise_for_status()

        # 解析JSONP响应
        text = response.text
        json_str = text[text.find('{'):text.rfind('}')+1]
        data = json.loads(json_str)

        return {
            "code": fund_code,
            "name": data.get("name", ""),
            "nav": float(data.get("gsz", 0)),  # 估算净值
            "nav_date": data.get("gztime", ""),
            "change_percent": float(data.get("gszzl", 0)),  # 估算涨跌幅
        }
    except Exception as e:
        print(f"获取基金 {fund_code} 实时数据失败: {e}")
        return None

def fetch_fund_history(fund_code, pages=30):
    """获取基金历史净值数据"""
    try:
        params = {
            "fundCode": fund_code,
            "pageIndex": 1,
            "pageSize": pages,
            "startDate": "",
            "endDate": ""
        }

        response = requests.get(API_BASE, params=params, timeout=10)
        response.raise_for_status()

        result = response.json()
        if result.get("Data") and result["Data"].get("LSJZList"):
            history = []
            for item in result["Data"]["LSJZList"]:
                history.append({
                    "date": item.get("FSRQ", ""),
                    "nav": float(item.get("DWJZ", 0)),
                    "change_percent": float(item.get("JZZZL", 0)) if item.get("JZZZL") else 0
                })
            return sorted(history, key=lambda x: x["date"])
        return []
    except Exception as e:
        print(f"获取基金 {fund_code} 历史数据失败: {e}")
        return []

def calculate_earnings(history, purchase_nav=None):
    """计算收益情况"""
    if not history:
        return {
            "daily_earning": 0,
            "daily_return": 0,
            "cumulative_earning": 0,
            "cumulative_return": 0,
            "current_nav": 0
        }

    latest = history[-1]
    current_nav = latest["nav"]

    # 如果没有提供购买净值，使用最早的历史净值
    if purchase_nav is None and len(history) > 0:
        purchase_nav = history[0]["nav"]

    # 每日收益（使用昨日净值计算）
    if len(history) >= 2:
        yesterday_nav = history[-2]["nav"]
        daily_return = ((current_nav - yesterday_nav) / yesterday_nav * 100) if yesterday_nav > 0 else 0
    else:
        daily_return = latest.get("change_percent", 0)

    # 累计收益
    cumulative_return = ((current_nav - purchase_nav) / purchase_nav * 100) if purchase_nav > 0 else 0

    return {
        "daily_earning": round(current_nav - (history[-2]["nav"] if len(history) >= 2 else current_nav), 4),
        "daily_return": round(daily_return, 2),
        "cumulative_earning": round(current_nav - purchase_nav, 4) if purchase_nav else 0,
        "cumulative_return": round(cumulative_return, 2),
        "current_nav": current_nav,
        "purchase_nav": purchase_nav
    }

def main():
    """主函数"""
    print(f"开始抓取基金数据 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    all_data = {
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "funds": {}
    }

    #  flatten fund list
    all_funds = []
    for platform, codes in FUNDS.items():
        for code in codes:
            all_funds.append({"code": code, "platform": platform})

    for fund in all_funds:
        code = fund["code"]
        platform = fund["platform"]

        print(f"正在处理基金 {code}...")

        # 获取实时数据
        realtime = fetch_fund_realtime(code)
        if not realtime:
            continue

        # 获取历史数据
        history = fetch_fund_history(code, pages=30)

        # 计算收益
        earnings = calculate_earnings(history)

        # 组织数据
        if platform not in all_data["funds"]:
            all_data["funds"][platform] = []

        fund_data = {
            "code": code,
            "name": realtime["name"],
            "platform": platform,
            "current_nav": realtime["nav"],
            "nav_date": realtime["nav_date"],
            "daily_return": realtime["change_percent"],
            "cumulative_return": earnings["cumulative_return"],
            "history": [{"date": h["date"], "nav": h["nav"]} for h in history[-30:]]  # 最近30天
        }

        all_data["funds"][platform].append(fund_data)

        # 避免请求过快
        time.sleep(0.5)

    # 保存数据
    output_dir = "data"
    os.makedirs(output_dir, exist_ok=True)

    output_file = os.path.join(output_dir, "funds_data.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)

    print(f"数据已保存到 {output_file}")
    print(f"共处理 {len(all_funds)} 只基金")

if __name__ == "__main__":
    main()
