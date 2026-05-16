#!/usr/bin/env python3
"""
根据 purchase_records.json 和 funds_data.json 生成持仓快照 holdings_snapshot.json
用法：python generate_holdings.py

逻辑：直接复用 funds_data.json 中的 holdings 计算结果（由 fetch_fund_data.py 的完整 FIFO 逻辑生成），
避免 generate_holdings.py 自行计算导致与主数据不一致。
"""

import json
import os
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
PURCHASE_FILE = os.path.join(DATA_DIR, 'purchase_records.json')
FUNDS_DATA_FILE = os.path.join(DATA_DIR, 'funds_data.json')
OUTPUT_FILE = os.path.join(DATA_DIR, 'holdings_snapshot.json')


def load_json(filepath):
    """安全加载 JSON 文件"""
    if not os.path.exists(filepath):
        print(" [Warning] 文件不存在: " + filepath)
        return None
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print("[Error] 读取失败 " + filepath + ": " + str(e))
        return None


def generate_holdings_snapshot():
    """生成持仓快照"""
    purchase_records = load_json(PURCHASE_FILE)
    funds_data = load_json(FUNDS_DATA_FILE)

    if purchase_records is None or funds_data is None:
        print("[Error] 无法读取必要数据，退出")
        return

    snapshot = {
        "format_version": "1.0",
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "funds": {},
        "summary": {
            "total_holdings_value": 0.0,
            "total_invested": 0.0,
            "total_profit_loss": 0.0,
            "total_profit_loss_percent": 0.0,
            "today_profit_loss": 0.0,
            "today_profit_loss_percent": 0.0,
            "funds_count": 0,
            "platforms": []
        }
    }

    platforms_set = set()
    total_holdings_value = 0.0
    total_invested = 0.0
    total_profit_loss = 0.0
    total_daily_profit_loss = 0.0
    funds_count = 0

    # 遍历 funds_data 中的基金，直接复用 holdings 数据
    for platform, fund_list in funds_data.get('funds', {}).items():
        if not fund_list:
            continue

        if platform not in snapshot['funds']:
            snapshot['funds'][platform] = {}

        platforms_set.add(platform)

        for fund in fund_list:
            fund_code = fund.get('code', '')
            fund_name = fund.get('name', fund_code)
            current_nav = fund.get('current_nav', 0)
            nav_date = fund.get('nav_date', '')
            daily_return = fund.get('daily_return', 0)
            holdings = fund.get('holdings')

            # 跳过无持仓的基金（无 holdings 或总份额为 0）
            if not holdings:
                continue
            total_shares = holdings.get('total_shares', 0)
            if total_shares <= 0.0001:
                continue

            # 从 purchase_records 获取交易统计信息（笔数、首笔日期）
            platform_records = purchase_records.get(platform, {})
            records = platform_records.get(fund_code, [])
            transactions_count = len(records)
            first_date = None
            if records:
                sorted_records = sorted(records, key=lambda x: x.get('date', ''))
                first_date = sorted_records[0].get('date', '')

            # 直接从 holdings 读取所有计算好的数据（由 fetch_fund_data.py 的 FIFO 逻辑生成）
            total_invested_fund = holdings.get('total_invested', 0)
            current_value = holdings.get('current_value', 0)
            profit_loss = holdings.get('profit_loss', 0)
            profit_loss_percent = holdings.get('profit_loss_percent', 0)
            realized_profit_loss = holdings.get('realized_profit_loss', 0)
            avg_cost_nav = holdings.get('avg_cost_nav', 0)

            # 计算今日盈亏
            daily_profit_loss = total_shares * current_nav * daily_return / 100 if current_nav > 0 else 0

            snapshot['funds'][platform][fund_code] = {
                "fund_name": fund_name,
                "current_nav": round(current_nav, 4),
                "nav_date": nav_date,
                "holdings": {
                    "total_shares": round(total_shares, 2),
                    "total_invested": round(total_invested_fund, 2),
                    "current_value": round(current_value, 2),
                    "profit_loss": round(profit_loss, 2),
                    "profit_loss_percent": round(profit_loss_percent, 2),
                    "realized_profit_loss": round(realized_profit_loss, 2),
                    "avg_cost_nav": round(avg_cost_nav, 4) if avg_cost_nav else 0,
                    "daily_return": round(daily_return, 2),
                    "daily_profit_loss": round(daily_profit_loss, 2)
                },
                "transactions_count": transactions_count,
                "first_purchase_date": first_date or '',
                "last_update": datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00")
            }

            total_holdings_value += current_value
            total_invested += total_invested_fund
            total_profit_loss += profit_loss
            total_daily_profit_loss += daily_profit_loss
            funds_count += 1

    # 计算汇总
    snapshot['summary'] = {
        "total_holdings_value": round(total_holdings_value, 2),
        "total_invested": round(total_invested, 2),
        "total_profit_loss": round(total_profit_loss, 2),
        "total_profit_loss_percent": round((total_profit_loss / total_invested * 100) if total_invested > 0 else 0, 2),
        "today_profit_loss": round(total_daily_profit_loss, 2),
        "today_profit_loss_percent": round((total_daily_profit_loss / total_holdings_value * 100) if total_holdings_value > 0 else 0, 2),
        "funds_count": funds_count,
        "platforms": sorted(list(platforms_set))
    }

    # 移除没有持仓的平台
    empty_platforms = [p for p, funds in snapshot['funds'].items() if len(funds) == 0]
    for p in empty_platforms:
        del snapshot['funds'][p]

    # 保存
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    print("[OK] 持仓快照已生成: " + OUTPUT_FILE)
    print("      持仓基金数: " + str(funds_count))
    print("      总市值: " + format(total_holdings_value, ',.2f'))
    print("      累计盈亏: " + format(total_profit_loss, ',.2f') + " (" + str(round(snapshot['summary']['total_profit_loss_percent'], 2)) + "%)")
    print("      已实现盈亏: " + format(sum(f.get('holdings', {}).get('realized_profit_loss', 0) for p in snapshot['funds'].values() for f in p.values()), ',.2f'))


if __name__ == '__main__':
    generate_holdings_snapshot()
