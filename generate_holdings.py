#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
根据 purchase_records.json 和 funds_data.json 生成持仓快照 holdings_snapshot.json
用法：python generate_holdings.py

逻辑：直接复用 funds_data.json 中的 holdings 计算结果（由 fetch_fund_data.py 的完整 FIFO 逻辑生成），
避免 generate_holdings.py 自行计算导致与主数据不一致。
"""

import json
import os
from datetime import datetime
from logger_config import log, get_beijing_time

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
PURCHASE_FILE = os.path.join(DATA_DIR, 'purchase_records.json')
FUNDS_DATA_FILE = os.path.join(DATA_DIR, 'funds_data.json')
OUTPUT_FILE = os.path.join(DATA_DIR, 'holdings_snapshot.json')


def load_json(filepath):
    """安全加载 JSON 文件"""
    if not os.path.exists(filepath):
        log(f"[Warning] 文件不存在: {filepath}", "warning")
        return None
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        log(f"[Error] 读取失败 {filepath}: {e}", "error")
        return None


def generate_holdings_snapshot():
    """生成持仓快照"""
    purchase_records = load_json(PURCHASE_FILE)
    funds_data = load_json(FUNDS_DATA_FILE)

    if purchase_records is None or funds_data is None:
        log("[Error] 无法读取必要数据，退出", "error")
        return

    # 继承主数据的时间戳，避免单独运行时产生误导性时间戳
    data_update_time = funds_data.get('update_time', '')
    # 格式转换：funds_data.json 是 "%Y-%m-%d %H:%M:%S"，快照需要 ISO 8601 格式
    # 支持多种常见格式，任一失败则回退到当前时间
    snapshot_time = None
    if data_update_time:
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d %H:%M:%S"]:
            try:
                dt = datetime.strptime(data_update_time, fmt)
                snapshot_time = dt.strftime("%Y-%m-%dT%H:%M:%S+08:00")
                break
            except ValueError:
                continue
        if not snapshot_time:
            log(f"[Warning] 无法解析时间戳格式: {data_update_time}，使用当前时间", "warning")
    if not snapshot_time:
        snapshot_time = get_beijing_time().strftime("%Y-%m-%dT%H:%M:%S+08:00")

    snapshot = {
        "format_version": "1.0",
        "generated_at": snapshot_time,
        "funds": {},
        "summary": {
            "total_holdings_value": 0.0,
            "total_invested": 0.0,
            "total_profit_loss": 0.0,
            "total_profit_loss_percent": 0.0,
            "latest_trading_day_profit_loss": 0.0,
            "latest_trading_day_profit_loss_percent": 0.0,
            "funds_count": 0,
            "platforms": []
        }
    }

    total_holdings_value = 0.0
    total_invested = 0.0
    total_profit_loss = 0.0
    total_latest_trading_day_profit_loss = 0.0
    total_realized_profit = 0.0
    funds_count = 0

    # 遍历 funds_data 中的基金，直接复用 holdings 数据
    for platform, fund_list in funds_data.get('funds', {}).items():
        if not fund_list:
            continue

        if platform not in snapshot['funds']:
            snapshot['funds'][platform] = {}

        for fund in fund_list:
            fund_code = fund.get('code', '')
            fund_name = fund.get('name', fund_code)
            current_nav = fund.get('current_nav', 0)
            nav_date = fund.get('nav_date', '')
            daily_return = fund.get('daily_return', 0)
            holdings = fund.get('holdings')

            if not holdings:
                continue
            total_shares = holdings.get('total_shares', 0)
            if total_shares <= 0.0001:
                continue

            platform_records = purchase_records.get(platform, {})
            records = platform_records.get(fund_code, [])
            transactions_count = len(records)
            first_date = None
            if records:
                sorted_records = sorted(records, key=lambda x: x.get('date', ''))
                first_date = sorted_records[0].get('date', '')

            total_invested_fund = holdings.get('total_invested', 0)
            current_value = holdings.get('current_value', 0)
            profit_loss = holdings.get('profit_loss', 0)
            profit_loss_percent = holdings.get('profit_loss_percent', 0)
            realized_profit_loss = holdings.get('realized_profit_loss', 0)
            avg_cost_nav = holdings.get('avg_cost_nav', 0)
            daily_profit_loss = fund.get('latest_trading_day_profit', 0)

            snapshot['funds'][platform][fund_code] = {
                "fund_name": fund_name,
                "current_nav": current_nav,
                "nav_date": nav_date,
                "data_source": fund.get("data_source", "live"),
                "holdings": {
                    "total_shares": total_shares,
                    "total_invested": total_invested_fund,
                    "current_value": current_value,
                    "profit_loss": profit_loss,
                    "profit_loss_percent": profit_loss_percent,
                    "realized_profit_loss": realized_profit_loss,
                    "avg_cost_nav": avg_cost_nav if avg_cost_nav else 0,
                    "daily_return": daily_return,
                    "daily_profit_loss": round(daily_profit_loss, 2)
                },
                "transactions_count": transactions_count,
                "first_purchase_date": first_date or '',
                "last_update": snapshot_time
            }

            total_holdings_value += current_value
            total_invested += total_invested_fund
            total_profit_loss += profit_loss
            total_latest_trading_day_profit_loss += daily_profit_loss
            total_realized_profit += realized_profit_loss
            funds_count += 1

    snapshot['summary'] = {
        "total_holdings_value": round(total_holdings_value, 2),
        "total_invested": round(total_invested, 2),
        "total_profit_loss": round(total_profit_loss, 2),
        "total_profit_loss_percent": round(total_profit_loss / total_invested * 100, 2) if total_invested > 0 else 0,
        "latest_trading_day_profit_loss": round(total_latest_trading_day_profit_loss, 2),
        "latest_trading_day_profit_loss_percent": round(total_latest_trading_day_profit_loss / total_holdings_value * 100, 2) if total_holdings_value > 0 else 0,
        "funds_count": funds_count,
        "platforms": sorted(snapshot['funds'].keys())
    }

    # 移除没有持仓的平台
    empty_platforms = [p for p, funds in snapshot['funds'].items() if len(funds) == 0]
    for p in empty_platforms:
        del snapshot['funds'][p]

    # 保存（添加异常处理）
    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        
        # 使用 log() 替代 print()，保持一致性
        log(f"[OK] 持仓快照已生成: {OUTPUT_FILE}", "info")
        log(f"[OK] 持仓基金数: {funds_count}", "info")
        log(f"[OK] 总市值: {total_holdings_value:,.2f}", "info")
        
        log(f"[OK] 累计盈亏: {total_profit_loss:,.2f} ({snapshot['summary']['total_profit_loss_percent']:.2f}%)", "info")
        log(f"[OK] 已实现盈亏: {total_realized_profit:,.2f}", "info")
    except Exception as e:
        log(f"[Error] 保存持仓快照失败: {e}", "error")
        return


if __name__ == '__main__':
    generate_holdings_snapshot()
