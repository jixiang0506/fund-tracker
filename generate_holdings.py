#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
根据 purchase_records.json 和 funds_data.json 生成持仓快照 holdings_snapshot.json

⚠️  重要依赖警告：
    此脚本直接复用 funds_data.json 中的 holdings 计算结果（由 fetch_fund_data.py 的完整 FIFO 逻辑生成）。
    如果 funds_data.json 没有通过 fetch_fund_data.py 重新生成，
    此脚本将输出过期数据！

✅ 正确执行顺序：
    1. python fetch_fund_data.py      （必须先执行！获取最新净值、计算 FIFO 持仓）
    2. python generate_holdings.py    （本脚本会自动校验交易记录完整性）

📋 数据新鲜度检查：
    脚本会自动检查 funds_data.json 的更新时间，如果超过 24 小时，
    会强制中断并提示用户先运行 fetch_fund_data.py
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from logger_config import log, get_beijing_time, safe_load_json

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
PURCHASE_FILE = os.path.join(DATA_DIR, 'purchase_records.json')
FUNDS_DATA_FILE = os.path.join(DATA_DIR, 'funds_data.json')
OUTPUT_FILE = os.path.join(DATA_DIR, 'holdings_snapshot.json')

# 数据新鲜度阈值（小时）
FRESHNESS_THRESHOLD_HOURS = 24

# 北京时间时区（UTC+8）
BEIJING_TZ = timezone(timedelta(hours=8))


def _check_funds_data_freshness():
    """
    检查 funds_data.json 是否足够新鲜（24小时内）
    
    返回：
        True  - 数据新鲜，可以继续执行
        False - 数据过期，应该中断执行
    """
    try:
        with open(FUNDS_DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        update_time = data.get('update_time', '')
        if not update_time:
            # 没有更新时间，给出警告但继续执行（兼容旧数据）
            log("[Warning] funds_data.json 没有 update_time 字段，无法检查新鲜度", "warning")
            return True
        
        # 解析时间戳（支持多种格式，并添加北京时间时区）
        data_time = None
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d %H:%M:%S"]:
            try:
                data_time = datetime.strptime(update_time, fmt).replace(tzinfo=BEIJING_TZ)
                break
            except ValueError:
                continue
        
        if not data_time:
            log(f"[Warning] 无法解析 funds_data.json 的时间戳: {update_time}", "warning")
            return True
        
        # 检查是否超过阈值
        now = get_beijing_time()
        hours_old = (now - data_time).total_seconds() / 3600
        
        if hours_old > FRESHNESS_THRESHOLD_HOURS:
            log("", "error")
            log("=" * 60, "error")
            log("[ERROR] ⚠️  数据过期警告！", "error")
            log(f"   funds_data.json 最后更新时间: {update_time}", "error")
            log(f"   距今已过去: {hours_old:.1f} 小时（阈值: {FRESHNESS_THRESHOLD_HOURS} 小时）", "error")
            log("", "error")
            log("⚠️  请先运行: python fetch_fund_data.py", "error")
            log("   然后再运行: python generate_holdings.py", "error")
            log("", "error")
            log("💡 建议: 先运行 python fetch_fund_data.py 再运行本脚本", "error")
            log("=" * 60, "error")
            log("", "error")
            return False
        
        # 数据新鲜，记录日志
        log(f"[OK] funds_data.json 数据新鲜（{hours_old:.1f} 小时前更新）", "info")
        return True
        
    except FileNotFoundError:
        log("[Error] funds_data.json 不存在，请先运行 fetch_fund_data.py", "error")
        return False
    except Exception as e:
        log(f"[Warning] 检查数据新鲜度失败: {e}，继续执行...", "warning")
        return True


def generate_holdings_snapshot():
    """生成持仓快照"""
    
    # ⚠️ 首先检查数据新鲜度
    if not _check_funds_data_freshness():
        log("[Error] 数据过期，中断执行", "error")
        sys.exit(1)

    # 校验交易记录完整性
    try:
        import validate_records
        validate_records.validate()
    except SystemExit as e:
        if e.code != 0:
            log("[Error] 交易记录校验失败，中断执行", "error")
            sys.exit(1)
    except Exception as e:
        log(f"[Error] 交易记录校验异常: {e}", "error")
        sys.exit(1)

    purchase_records = safe_load_json(PURCHASE_FILE)
    funds_data = safe_load_json(FUNDS_DATA_FILE)

    if purchase_records is None or funds_data is None:
        log("[Error] 无法读取必要数据，退出", "error")
        return

    # 📋 数据版本日志：记录依赖数据的更新时间
    data_update_time = funds_data.get('update_time', '未知')
    log(f"📋 基于 funds_data.json (更新时间: {data_update_time}) 生成快照", "info")

    # data_update_time 已在上方（L128）赋值，此处无需重复
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
        log(f"✓ 持仓快照已生成: {OUTPUT_FILE}", "info")
        log(f"✓ 持仓基金数: {funds_count}", "info")
        log(f"✓ 总市值: {total_holdings_value:,.2f}", "info")
        log(f"✓ 累计盈亏: {total_profit_loss:,.2f} ({snapshot['summary']['total_profit_loss_percent']:.2f}%)", "info")
        log(f"✓ 已实现盈亏: {total_realized_profit:,.2f}", "info")
    except Exception as e:
        log(f"❌ 保存持仓快照失败: {e}", "error")
        return


if __name__ == '__main__':
    generate_holdings_snapshot()
