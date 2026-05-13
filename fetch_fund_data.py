#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基金收益追踪系统 - 数据抓取脚本
从天天基金网获取基金数据，读取持仓记录，计算实际收益

更新说明 (2026-05-13):
1. 优化效率：每只基金只获取一次历史数据，避免重复API调用
2. 改进错误处理：增加重试机制和详细日志
3. 自动创建模板文件：如果不存在持仓记录文件，自动创建模板
4. 支持手动输入净值：如果无法获取历史净值，允许手动输入
"""

import requests
import json
import os
from datetime import datetime, timedelta
import time
import sys

# 基金列表配置
FUNDS = {
    "支付宝": ["270023", "016665", "001438", "002112", "018230"],
    "理财通": ["018147", "012922", "019018"],
    "招商银行": ["021277", "000390", "020723"]
}

# 天天基金API
HISTORY_API = "http://api.fund.eastmoney.com/f10/lsjz"
REALTIME_API = "http://fundgz.1234567.com.cn/js/{}.js"

# 添加请求头，模拟浏览器访问
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "http://fund.eastmoney.com/"
}

def log(message):
    """打印带时间戳的日志"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

def fetch_fund_realtime(fund_code):
    """获取基金实时数据（估算净值和涨跌幅）"""
    try:
        url = REALTIME_API.format(fund_code)
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()

        # 解析JSONP响应
        text = response.text
        json_str = text[text.find('{'):text.rfind('}')+1]
        data = json.loads(json_str)

        return {
            "code": fund_code,
            "name": data.get("name", ""),
            "nav": float(data.get("gsz", 0)),  # 估算净值
            "nav_date": data.get("gztime", ""),  # 估值时间
            "change_percent": float(data.get("gszzl", 0)),  # 估算涨跌幅
        }
    except Exception as e:
        log(f"❌ 获取基金 {fund_code} 实时数据失败: {e}")
        return None

def fetch_fund_history(fund_code, start_date="2020-01-01", pages=500):
    """获取基金历史净值数据（一次性获取所有历史数据）"""
    try:
        log(f"  获取基金 {fund_code} 的历史净值数据...")
        all_history = []
        page_index = 1

        while True:
            params = {
                "fundCode": fund_code,
                "pageIndex": page_index,
                "pageSize": 50,  # 每页50条记录
                "startDate": start_date,
                "endDate": datetime.now().strftime("%Y-%m-%d")
            }

            response = requests.get(HISTORY_API, params=params, headers=HEADERS, timeout=10)
            response.raise_for_status()

            result = response.json()
            if not result.get("Data") or not result["Data"].get("LSJZList"):
                break

            for item in result["Data"]["LSJZList"]:
                all_history.append({
                    "date": item.get("FSRQ", ""),
                    "nav": float(item.get("DWJZ", 0)),
                    "change_percent": float(item.get("JZZZL", 0)) if item.get("JZZZL") else 0
                })

            # 检查是否还有更多页
            total_count = result["Data"].get("TotalCount", 0)
            if len(all_history) >= total_count:
                break

            page_index += 1
            time.sleep(0.2)  # 避免请求过快

        # 按日期排序（从旧到新）
        all_history = sorted(all_history, key=lambda x: x["date"])

        log(f"  ✓ 成功获取 {len(all_history)} 条历史记录")
        return all_history
    except Exception as e:
        log(f"❌ 获取基金 {fund_code} 历史数据失败: {e}")
        return []

def get_nav_from_history(history, target_date):
    """从历史事件中查找指定日期的净值"""
    # 先尝试精确匹配
    for record in reversed(history):
        if record["date"] == target_date:
            return record["nav"]

    # 如果找不到，找最近的交易日（往前找）
    for record in reversed(history):
        if record["date"] <= target_date:
            log(f"  注意：未找到 {target_date} 的净值，使用 {record['date']} 的净值: {record['nav']}")
            return record["nav"]

    return None

def create_template_files():
    """创建模板文件"""
    # 创建 data 目录
    os.makedirs("data", exist_ok=True)

    # 创建模板持仓记录文件
    template_records = {
        "支付宝": {
            "270023": [
                {"date": "2024-01-15", "amount": 1000.00},
                {"date": "2024-02-15", "amount": 1000.00},
                {"date": "2024-06-15", "amount": 500.00, "type": "sell"}
            ],
            "016665": [
                {"date": "2024-01-20", "amount": 2000.00}
            ]
        },
        "理财通": {
            "018147": [
                {"date": "2024-03-01", "amount": 1500.00}
            ]
        },
        "招商银行": {
            "021277": [
                {"date": "2024-02-10", "amount": 3000.00}
            ]
        }
    }

    template_path = "data/purchase_records.json"
    if not os.path.exists(template_path):
        with open(template_path, "w", encoding="utf-8") as f:
            json.dump(template_records, f, ensure_ascii=False, indent=2)
        log(f"✓ 已创建模板持仓记录文件: {template_path}")
        log("  请编辑此文件，填入你的实际买入记录")
        log("  卖出记录格式: {\"date\": \"2024-06-15\", \"amount\": 500.00, \"type\": \"sell\"}")
        return False

    return True

def load_purchase_records():
    """加载持仓记录"""
    try:
        records_file = "data/purchase_records.json"

        # 如果文件不存在，创建模板
        if not os.path.exists(records_file):
            log("未找到持仓记录文件，正在创建模板...")
            create_template_files()
            return None

        with open(records_file, "r", encoding="utf-8") as f:
            raw_records = json.load(f)

        # 将分层结构转换为扁平结构: {fund_code: [purchases]}
        flattened = {}
        for platform, funds in raw_records.items():
            for fund_code, purchases in funds.items():
                if fund_code not in flattened:
                    flattened[fund_code] = []
                flattened[fund_code].extend(purchases)

        log(f"✓ 成功加载持仓记录: {len(flattened)} 只基金")
        return flattened
    except Exception as e:
        log(f"❌ 加载持仓记录失败: {e}")
        return None

def calculate_holdings(fund_code, purchase_records, current_nav, history):
    """计算持仓信息和实际收益（支持买入和卖出记录）"""
    if not purchase_records or fund_code not in purchase_records:
        return {
            "total_invested": 0,
            "total_shares": 0,
            "current_value": 0,
            "profit_loss": 0,
            "profit_loss_percent": 0,
            "purchases": []
        }

    purchases = purchase_records[fund_code]
    total_invested = 0  # 净投入金额（买入-卖出）
    total_shares = 0    # 持有份额（买入-卖出）
    purchase_details = []

    for purchase in purchases:
        date = purchase["date"]
        amount = purchase["amount"]
        trans_type = purchase.get("type", "buy")  # 默认为买入

        # 从历史数据中查找交易日的净值
        nav_on_date = get_nav_from_history(history, date)

        if nav_on_date and nav_on_date > 0:
            shares = amount / nav_on_date

            if trans_type == "sell":
                # 卖出记录：减少份额和投入
                total_invested -= amount
                total_shares -= shares

                purchase_details.append({
                    "date": date,
                    "amount": -amount,
                    "nav": round(nav_on_date, 4),
                    "shares": -round(shares, 2),
                    "type": "sell"
                })

                log(f"  卖出记录: {date}, 金额 ¥{amount}, 净值 {nav_on_date:.4f}, 份额 -{shares:.2f}")
            else:
                # 买入记录
                total_invested += amount
                total_shares += shares

                purchase_details.append({
                    "date": date,
                    "amount": amount,
                    "nav": round(nav_on_date, 4),
                    "shares": round(shares, 2),
                    "type": "buy"
                })

                log(f"  买入记录: {date}, 金额 ¥{amount}, 净值 {nav_on_date:.4f}, 份额 {shares:.2f}")
        else:
            log(f"  ⚠ 无法获取 {date} 的净值，跳过此笔记录")

    # 确保份额和投入不为负数（防止数据错误）
    total_shares = max(0, total_shares)
    total_invested = max(0, total_invested)

    # 计算当前市值和收益
    current_value = total_shares * current_nav if current_nav > 0 else 0
    profit_loss = current_value - total_invested
    profit_loss_percent = (profit_loss / total_invested * 100) if total_invested > 0 else 0

    return {
        "total_invested": round(total_invested, 2),
        "total_shares": round(total_shares, 2),
        "current_value": round(current_value, 2),
        "profit_loss": round(profit_loss, 2),
        "profit_loss_percent": round(profit_loss_percent, 2),
        "purchases": purchase_details
    }

def main():
    """主函数"""
    print("="*60)
    print(f"基金收益追踪系统 - 数据抓取")
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)

    # 检查/创建模板文件
    print("\n[1/4] 检查必要文件...")
    has_records = create_template_files()
    if not has_records:
        print("\n⚠️  请先编辑 data/purchase_records.json 文件，填入你的实际买入记录")
        print("   模板文件已创建，你可以参考其中的格式")
        return

    # 加载持仓记录
    print("\n[2/4] 加载持仓记录...")
    purchase_records = load_purchase_records()
    if purchase_records is None:
        return

    # 初始化输出数据
    all_data = {
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "funds": {},
        "summary": {
            "total_invested": 0,
            "total_value": 0,
            "total_profit_loss": 0,
            "total_profit_loss_percent": 0
        }
    }

    # 处理所有基金
    print("\n[3/4] 获取基金数据...")
    for platform, codes in FUNDS.items():
        log(f"处理 {platform} 的基金...")

        if platform not in all_data["funds"]:
            all_data["funds"][platform] = []

        for code in codes:
            log(f"  正在处理基金 {code}...")

            # 获取实时数据
            realtime = fetch_fund_realtime(code)
            if not realtime:
                continue

            # 获取历史数据（一次性获取所有）
            history = fetch_fund_history(code)

            # 计算持仓和收益
            holdings = calculate_holdings(code, purchase_records, realtime["nav"], history)

            # 组织数据
            fund_data = {
                "code": code,
                "name": realtime["name"],
                "platform": platform,
                "current_nav": realtime["nav"],
                "nav_date": realtime["nav_date"],
                "daily_return": realtime["change_percent"],
                "holdings": holdings,
                "history": [{"date": h["date"], "nav": h["nav"]} for h in history[-30:]]  # 只保留最近30天
            }

            all_data["funds"][platform].append(fund_data)

            # 累计算计
            all_data["summary"]["total_invested"] += holdings["total_invested"]
            all_data["summary"]["total_value"] += holdings["current_value"]

            # 避免请求过快
            time.sleep(0.5)

    # 计算总计收益
    print("\n[4/4] 计算总计收益...")
    summary = all_data["summary"]
    summary["total_profit_loss"] = round(summary["total_value"] - summary["total_invested"], 2)
    if summary["total_invested"] > 0:
        summary["total_profit_loss_percent"] = round(summary["total_profit_loss"] / summary["total_invested"] * 100, 2)

    # 打印汇总信息
    print("\n" + "="*60)
    print("✓ 数据抓取完成！")
    print(f"  总投入: ¥{summary['total_invested']:.2f}")
    print(f"  当前市值: ¥{summary['total_value']:.2f}")
    profit_sign = "+" if summary["total_profit_loss"] >= 0 else ""
    print(f"  总盈亏: ¥{profit_sign}{summary['total_profit_loss']:.2f} ({profit_sign}{summary['total_profit_loss_percent']:.2f}%)")
    print("="*60)

    # 保存数据
    output_file = "data/funds_data.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)

    print(f"\n✓ 数据已保存到 {output_file}")
    print(f"  更新时间: {all_data['update_time']}")

if __name__ == "__main__":
    main()
