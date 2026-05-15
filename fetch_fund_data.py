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
import subprocess
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

def fetch_fund_realtime(fund_code, max_retries=3):
    """获取基金实时数据（估算净值和涨跌幅），支持重试。
    如果实时估值API返回空（非交易时段），则回退到历史数据API获取最新净值。"""
    for attempt in range(1, max_retries + 1):
        try:
            url = REALTIME_API.format(fund_code)
            response = requests.get(url, headers=HEADERS, timeout=10)
            response.raise_for_status()

            # 解析JSONP响应
            text = response.text
            json_str = text[text.find('{'):text.rfind('}')+1]

            # 空JSON（如 jsonpgz(); 的情况）- 回退到历史数据
            if not json_str.strip():
                log(f"  ⚠ 基金 {fund_code} 实时估值API返回空数据，尝试回退到历史数据API...")
                return _fetch_latest_from_history(fund_code)

            data = json.loads(json_str)

            # 检查返回数据是否有效（有些基金不在交易时段会返回空内容）
            if not data.get("name") and not data.get("gsz"):
                log(f"  ⚠ 基金 {fund_code} 实时估值无数据，尝试回退到历史数据API...")
                return _fetch_latest_from_history(fund_code)

            return {
                "code": fund_code,
                "name": data.get("name", ""),
                "nav": float(data.get("gsz", 0)),  # 估算净值
                "nav_date": data.get("gztime", ""),  # 估值时间
                "change_percent": float(data.get("gszzl", 0)),  # 估算涨跌幅
            }
        except json.JSONDecodeError:
            # JSON解析失败 - 回退到历史数据
            log(f"  ⚠ 基金 {fund_code} 实时估值JSON解析失败，尝试回退到历史数据API...")
            return _fetch_latest_from_history(fund_code)
        except Exception as e:
            if attempt < max_retries:
                log(f"  ⚠ 获取基金 {fund_code} 实时数据失败 (第{attempt}次), {max_retries - attempt}次重试机会剩余: {e}")
                time.sleep(2)
            else:
                log(f"❌ 获取基金 {fund_code} 实时数据失败 (已重试{max_retries}次): {e}")
                # 最后一次也尝试回退
                return _fetch_latest_from_history(fund_code)

def _fetch_latest_from_history(fund_code):
    """从历史净值API获取最新一条记录，作为实时数据的回退方案"""
    try:
        params = {
            "fundCode": fund_code,
            "pageIndex": 1,
            "pageSize": 2,  # 取最近2条以计算涨跌幅
            "startDate": "2020-01-01",
            "endDate": datetime.now().strftime("%Y-%m-%d")
        }
        response = requests.get(HISTORY_API, params=params, headers=HEADERS, timeout=10)
        response.raise_for_status()
        result = response.json()

        items = result.get("Data", {}).get("LSJZList", [])
        if not items:
            log(f"  ❌ 基金 {fund_code} 历史数据也为空")
            return None

        latest = items[0]
        nav = float(latest.get("DWJZ", 0))
        nav_date = latest.get("FSRQ", "")
        change_percent = float(latest.get("JZZZL", 0)) if latest.get("JZZZL") else 0

        # 尝试获取基金名称（从历史API中无法直接获取，使用已知映射或留空）
        fund_name = _get_fund_name(fund_code)

        log(f"  ✓ 基金 {fund_code} 回退成功: 净值 {nav} ({nav_date}), 涨跌 {change_percent}%")
        return {
            "code": fund_code,
            "name": fund_name,
            "nav": nav,
            "nav_date": nav_date,
            "change_percent": change_percent,
        }
    except Exception as e:
        log(f"  ❌ 基金 {fund_code} 历史数据回退也失败: {e}")
        return None

def _get_fund_name(fund_code):
    """已知基金名称映射（回退方案使用）"""
    FUND_NAMES = {
        "016665": "天弘全球高端制造混合(QDII)C",
        "270023": "广发全球精选股票(QDII)人民币A",
        "001438": "易方达瑞享混合E",
        "002112": "德邦鑫星价值灵活配置混合C",
        "018230": "易方达全球优质企业混合(QDII)C(人民币份额)",
        "018147": "建信新兴市场混合(QDII)C",
        "012922": "易方达全球成长精选混合(QDII)人民币C",
        "019018": "易方达信息产业混合C",
        "021277": "广发全球精选股票(QDII)人民币C",
        "000390": "华商优势行业混合A",
        "020723": "国寿安保数字经济股票发起式C",
    }
    return FUND_NAMES.get(fund_code, fund_code)

def _get_earliest_purchase_date(purchase_records):
    """从持仓记录中找出最早的交易日期，往前推7天作为历史数据起始日期"""
    earliest = None
    for fund_code, purchases in purchase_records.items():
        for p in purchases:
            d = p.get("date", "")
            if d and (earliest is None or d < earliest):
                earliest = d
    if earliest:
        # 往前推7天，确保能找到交易日前后的净值
        dt = datetime.strptime(earliest, "%Y-%m-%d") - timedelta(days=7)
        return dt.strftime("%Y-%m-%d")
    return "2020-01-01"  # 兜底默认值

def fetch_fund_history(fund_code, start_date="2020-01-01", max_pages=100):
    """获取基金历史净值数据（从start_date开始获取）"""
    try:
        log(f"  获取基金 {fund_code} 的历史净值数据...")
        all_history = []
        page_index = 1
        page_size = 20  # API每页最大返回20条

        while page_index <= max_pages:
            params = {
                "fundCode": fund_code,
                "pageIndex": page_index,
                "pageSize": page_size,
                "startDate": start_date,
                "endDate": datetime.now().strftime("%Y-%m-%d")
            }

            response = requests.get(HISTORY_API, params=params, headers=HEADERS, timeout=10)
            response.raise_for_status()

            result = response.json()
            if not result.get("Data") or not result["Data"].get("LSJZList"):
                break

            items = result["Data"]["LSJZList"]
            for item in items:
                all_history.append({
                    "date": item.get("FSRQ", ""),
                    "nav": float(item.get("DWJZ", 0)),
                    "change_percent": float(item.get("JZZZL", 0)) if item.get("JZZZL") else 0
                })

            # 如果返回的数据少于page_size，说明没有更多页了
            if len(items) < page_size:
                break

            page_index += 1
            time.sleep(0.3)  # 避免请求过快

        # 按日期排序（从旧到新）
        all_history = sorted(all_history, key=lambda x: x["date"])

        log(f"  ✓ 成功获取 {len(all_history)} 条历史记录")
        return all_history
    except Exception as e:
        log(f"❌ 获取基金 {fund_code} 历史数据失败: {e}")
        return []

def get_nav_from_history(history, target_date):
    """从历史数据中查找指定日期的净值。
    
    基金交易规则：
    - 工作日15点前提交 → 按当天净值确认
    - 工作日15点后或周末提交 → 按下一个工作日净值确认
    
    由于 purchase_records.json 只记录日期不含时间，无法区分15点前后。
    当日期不在history中（周末/节假日）时，映射到最近的后一个交易日。
    """
    # 先尝试精确匹配
    for record in reversed(history):
        if record["date"] == target_date:
            return record["nav"]

    # 如果找不到（周末/节假日），找最近的后一个交易日
    for record in history:
        if record["date"] > target_date:
            log(f"  注意：{target_date} 非交易日，使用下一交易日 {record['date']} 的净值: {record['nav']}")
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
    """计算持仓信息和实际收益（支持买入和卖出记录，FIFO法自动抵扣）"""
    if not purchase_records or fund_code not in purchase_records:
        return {
            "total_invested": 0,
            "total_shares": 0,
            "current_value": 0,
            "profit_loss": 0,
            "profit_loss_percent": 0,
            "purchases": [],
            "realized_profit_loss": 0  # 已实现盈亏
        }

    purchases = purchase_records[fund_code]

    # FIFO队列：{date, amount, shares, nav, remaining_shares}
    buy_queue = []
    realized_profit_loss = 0  # 已实现盈亏（卖出时确认）
    purchase_details = []

    # 按日期排序（确保FIFO顺序）
    sorted_purchases = sorted(purchases, key=lambda x: x["date"])

    for purchase in sorted_purchases:
        date = purchase["date"]
        amount = purchase["amount"]
        trans_type = purchase.get("type", "buy")

        # 从历史数据中查找交易日的净值
        nav_on_date = get_nav_from_history(history, date)

        if not nav_on_date or nav_on_date <= 0:
            log(f"  ⚠ 无法获取 {date} 的净值，跳过此笔记录")
            continue

        if trans_type == "sell":
            # 卖出记录：按FIFO法从最早买入抵扣
            sell_shares = amount / nav_on_date
            remaining_sell_shares = sell_shares
            sell_realized_profit = 0  # 本笔卖出实现的盈亏

            log(f"  卖出记录: {date}, 金额 ¥{amount}, 净值 {nav_on_date:.4f}, 份额 {sell_shares:.2f}")

            # FIFO抵扣
            for buy in buy_queue:
                if remaining_sell_shares <= 0:
                    break

                if buy["remaining_shares"] <= 0:
                    continue

                # 本次抵扣的份额
                deduct_shares = min(remaining_sell_shares, buy["remaining_shares"])
                deduct_amount = deduct_shares * buy["nav"]  # 成本金额
                sell_value = deduct_shares * nav_on_date  # 卖出金额
                profit = sell_value - deduct_amount

                # 更新
                buy["remaining_shares"] -= deduct_shares
                remaining_sell_shares -= deduct_shares
                realized_profit_loss += profit
                sell_realized_profit += profit

                log(f"    FIFO抵扣: 从 {buy['date']} 买入记录抵扣 {deduct_shares:.2f} 份, 成本 ¥{deduct_amount:.2f}, 卖出 ¥{sell_value:.2f}, 盈亏 ¥{profit:.2f}")

            # 记录卖出详情（使用本笔卖出的已实现盈亏）
            purchase_details.append({
                "date": date,
                "amount": -round(amount, 2),
                "nav": round(nav_on_date, 4),
                "shares": -round(sell_shares, 2),
                "type": "sell",
                "realized_profit": round(sell_realized_profit, 2)  # 本笔卖出的盈亏
            })

        else:
            # 买入记录：加入FIFO队列
            shares = amount / nav_on_date
            buy_queue.append({
                "date": date,
                "amount": amount,
                "shares": shares,
                "nav": nav_on_date,
                "remaining_shares": shares
            })

            purchase_details.append({
                "date": date,
                "amount": amount,
                "nav": round(nav_on_date, 4),
                "shares": round(shares, 2),
                "type": "buy"
            })

            log(f"  买入记录: {date}, 金额 ¥{amount}, 净值 {nav_on_date:.4f}, 份额 {shares:.2f}")

    # 计算剩余持仓
    remaining_shares = sum(b["remaining_shares"] for b in buy_queue)
    remaining_cost = sum(b["remaining_shares"] * b["nav"] for b in buy_queue)

    # 计算平均持仓成本
    avg_cost_nav = remaining_cost / remaining_shares if remaining_shares > 0 else 0

    # 确保份额和投入不为负数
    remaining_shares = max(0, remaining_shares)
    remaining_cost = max(0, remaining_cost)

    # 计算当前市值和收益
    current_value = remaining_shares * current_nav if current_nav > 0 else 0
    unrealized_profit = current_value - remaining_cost
    profit_loss_percent = (unrealized_profit / remaining_cost * 100) if remaining_cost > 0 else 0

    log(f"  持仓汇总: 剩余份额 {remaining_shares:.2f}, 剩余成本 ¥{remaining_cost:.2f}, 已实现盈亏 ¥{realized_profit_loss:.2f}, 平均成本 {avg_cost_nav:.4f}")

    return {
        "total_invested": round(remaining_cost, 2),
        "total_shares": round(remaining_shares, 2),
        "current_value": round(current_value, 2),
        "profit_loss": round(unrealized_profit, 2),
        "profit_loss_percent": round(profit_loss_percent, 2),
        "purchases": purchase_details,
        "realized_profit_loss": round(realized_profit_loss, 2),  # 新增：已实现盈亏
        "avg_cost_nav": round(avg_cost_nav, 4)  # 新增：平均持仓成本
    }

def calculate_cumulative_returns(history, purchases):
    """为历史数据中每一天预计算累计收益率，避免前端重复计算。"""
    if not history:
        return []

    sorted_purchases = sorted(purchases, key=lambda p: p["date"])
    return_rates = []

    for h in history:
        total_shares = 0
        total_invested = 0

        for p in sorted_purchases:
            if p["date"] <= h["date"]:
                if p.get("type") == "sell":
                    total_shares -= abs(p["shares"])
                    total_invested -= abs(p["amount"])
                else:
                    total_shares += p["shares"]
                    total_invested += p["amount"]

        if total_invested > 0:
            value = h["nav"] * total_shares
            profit = value - total_invested
            return_rate = (profit / total_invested) * 100
            return_rates.append(round(return_rate, 2))
        else:
            return_rates.append(None)

    return return_rates


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

    # 加载上次数据（用于API失败时保留旧数据）
    previous_data = None
    previous_file = "data/funds_data.json"
    if os.path.exists(previous_file):
        try:
            with open(previous_file, "r", encoding="utf-8") as f:
                previous_data = json.load(f)
            log(f"✓ 已加载上次数据作为备份 (更新时间: {previous_data.get('update_time', '未知')})")
        except Exception:
            pass

    # 构建上次数据的快速查找表: {fund_code: fund_data}
    prev_fund_map = {}
    if previous_data:
        for platform_name, fund_list in previous_data.get("funds", {}).items():
            for fund_item in fund_list:
                prev_fund_map[fund_item["code"]] = fund_item

    # 处理所有基金
    print("\n[3/4] 获取基金数据...")

    # 计算历史数据起始日期（所有基金共享，只需计算一次）
    history_start_date = _get_earliest_purchase_date(purchase_records)
    log(f"历史数据起始日期: {history_start_date}")

    failed_funds = []
    for platform, codes in FUNDS.items():
        log(f"处理 {platform} 的基金...")

        if platform not in all_data["funds"]:
            all_data["funds"][platform] = []

        for code in codes:
            log(f"  正在处理基金 {code}...")

            # 获取实时数据
            realtime = fetch_fund_realtime(code)
            if not realtime:
                # API失败：尝试使用上次数据
                if code in prev_fund_map:
                    old_fund = prev_fund_map[code]
                    log(f"  ⚠ 使用上次缓存数据: {old_fund.get('name', code)} (净值 {old_fund.get('current_nav', '?')} @{old_fund.get('nav_date', '?')})")
                    all_data["funds"][platform].append(old_fund)
                    all_data["summary"]["total_invested"] += old_fund["holdings"]["total_invested"]
                    all_data["summary"]["total_value"] += old_fund["holdings"]["current_value"]
                    failed_funds.append(code)
                else:
                    log(f"  ❌ 基金 {code} 无上次缓存数据，本次跳过！")
                    failed_funds.append(code)
                continue

            # 获取历史数据（从最早交易记录前7天开始获取，避免拉取大量无用数据）
            history = fetch_fund_history(code, start_date=history_start_date)

            # 计算持仓和收益
            holdings = calculate_holdings(code, purchase_records, realtime["nav"], history)

            # 预计算累计收益率（避免前端重复计算）
            cumulative_returns = calculate_cumulative_returns(history, holdings["purchases"])

            # 组织数据
            fund_data = {
                "code": code,
                "name": realtime["name"],
                "platform": platform,
                "current_nav": realtime["nav"],
                "nav_date": realtime["nav_date"],
                "daily_return": realtime["change_percent"],
                "holdings": holdings,
                "history": [{"date": h["date"], "nav": h["nav"], "return_rate": cumulative_returns[i]} for i, h in enumerate(history)]
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
    
    # 累计算已实现盈亏
    total_realized_profit = 0
    for platform_funds in all_data["funds"].values():
        for fund in platform_funds:
            if fund.get("holdings") and fund["holdings"].get("realized_profit_loss"):
                total_realized_profit += fund["holdings"]["realized_profit_loss"]
    
    summary["total_realized_profit_loss"] = round(total_realized_profit, 2)
    
    # 打印汇总信息
    print("\n" + "="*60)
    print("✓ 数据抓取完成！")
    print(f"  总投入: ¥{summary['total_invested']:.2f}")
    print(f"  当前市值: ¥{summary['total_value']:.2f}")
    profit_sign = "+" if summary["total_profit_loss"] >= 0 else ""
    print(f"  未实现盈亏: {profit_sign}¥{summary['total_profit_loss']:.2f} ({profit_sign}{summary['total_profit_loss_percent']:.2f}%)")
    realized_sign = "+" if summary["total_realized_profit_loss"] >= 0 else ""
    print(f"  已实现盈亏: {realized_sign}¥{summary['total_realized_profit_loss']:.2f}")
    print(f"  总盈亏: ¥{profit_sign}{summary['total_profit_loss']:.2f} ({profit_sign}{summary['total_profit_loss_percent']:.2f}%)")
    if failed_funds:
        print(f"\n  [Warning] 以下基金使用缓存数据或跳过: {', '.join(failed_funds)}")
    print("="*60)

    # 保存数据
    output_file = "data/funds_data.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)

    print(f"\n✓ 数据已保存到 {output_file}")
    print(f"  更新时间: {all_data['update_time']}")

    # 生成持仓快照
    print("\n生成持仓快照...")
    try:
        subprocess.run([sys.executable, "generate_holdings.py"], check=True)
    except Exception as e:
        print(f"  ⚠️ 持仓快照生成失败: {e}")

if __name__ == "__main__":
    main()
