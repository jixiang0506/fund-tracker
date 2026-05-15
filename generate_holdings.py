#!/usr/bin/env python3
"""
根据 purchase_records.json 和 funds_data.json 生成持仓快照 holdings_snapshot.json
用法：python generate_holdings.py
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

    if purchase_records is None:
        print("[Error] 无法读取交易记录，退出")
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

    # 构建 funds_data 的快速查找索引：{platform: {code: fund_obj}}
    funds_index = {}
    if funds_data and 'funds' in funds_data:
        for platform, fund_list in funds_data['funds'].items():
            funds_index[platform] = {}
            for fund in fund_list:
                funds_index[platform][fund['code']] = fund

    # 遍历交易记录，计算每只基金的持仓
    for platform, funds in purchase_records.items():
        if platform not in snapshot['funds']:
            snapshot['funds'][platform] = {}

        platforms_set.add(platform)

        for fund_code, records in funds.items():
            # 计算净份额和累计投入
            total_shares = 0.0
            total_invested_fund = 0.0
            transactions_count = 0
            first_date = None

            # 按日期排序
            sorted_records = sorted(records, key=lambda x: x.get('date', ''))

            for record in sorted_records:
                amount = record.get('amount', 0)
                rtype = record.get('type', 'buy')
                shares = record.get('shares', None)
                transactions_count += 1

                if first_date is None:
                    first_date = record.get('date')

                if rtype == 'sell':
                    # 卖出：减少份额，减少投入成本
                    sell_amount = abs(amount) if amount > 0 else -amount
                    total_invested_fund -= sell_amount
                    if shares:
                        total_shares -= shares
                    # 如果没有份额信息，尝试从金额和净值估算
                    elif platform in funds_index and fund_code in funds_index[platform]:
                        nav = funds_index[platform][fund_code].get('current_nav', 0)
                        if nav > 0:
                            total_shares -= sell_amount / nav
                else:
                    # 买入：增加份额，增加投入
                    total_invested_fund += amount
                    if shares:
                        total_shares += shares
                    # 如果没有份额信息，尝试从金额和净值估算
                    elif platform in funds_index and fund_code in funds_index[platform]:
                        nav = funds_index[platform][fund_code].get('current_nav', 0)
                        if nav > 0:
                            total_shares += amount / nav

            # 从 funds_data 获取最新净值和市值
            fund_info = funds_index.get(platform, {}).get(fund_code, {})
            current_nav = fund_info.get('current_nav', 0)
            nav_date = fund_info.get('nav_date', '')
            daily_return = fund_info.get('daily_return', 0)
            holdings = fund_info.get('holdings', {})

            # 优先使用 funds_data 中的准确持仓数据
            if holdings:
                total_shares = holdings.get('total_shares', total_shares)
                total_invested_fund = holdings.get('total_invested', total_invested_fund)
                current_value = holdings.get('current_value', 0)
                profit_loss = holdings.get('profit_loss', 0)
                profit_loss_percent = holdings.get('profit_loss_percent', 0)
            else:
                # 自行计算
                current_value = total_shares * current_nav if current_nav > 0 else 0
                profit_loss = current_value - total_invested_fund
                profit_loss_percent = (profit_loss / total_invested_fund * 100) if total_invested_fund > 0 else 0

            daily_profit_loss = total_shares * current_nav * daily_return / 100 if current_nav > 0 else 0

            # 只记录有持仓的基金（净份额 > 0）
            if total_shares > 0.0001:
                snapshot['funds'][platform][fund_code] = {
                    "fund_name": fund_info.get('name', fund_code),
                    "current_nav": round(current_nav, 4),
                    "nav_date": nav_date,
                    "holdings": {
                        "total_shares": round(total_shares, 2),
                        "total_invested": round(total_invested_fund, 2),
                        "current_value": round(current_value, 2),
                        "profit_loss": round(profit_loss, 2),
                        "profit_loss_percent": round(profit_loss_percent, 2),
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


if __name__ == '__main__':
    generate_holdings_snapshot()
