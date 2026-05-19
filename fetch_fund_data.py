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
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import json
import os
from datetime import datetime, timedelta
import time
import sys

# 导入日志模块
try:
    from logger_config import log
except ImportError:
    # 如果 logger_config 不存在，使用简单的 log 函数
    def log(message, level='info'):
        print(message)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_fund_config():
    """加载基金配置文件"""
    config_file = os.path.join(BASE_DIR, "fund_config.json")
    
    if not os.path.exists(config_file):
        log("⚠️ 基金配置文件不存在: " + config_file, "warning")
        log("将使用默认基金列表", "warning")
        # 返回默认配置
        return {
            "支付宝": ["270023", "016665", "001438", "002112", "018230"],
            "理财通": ["018147", "012922", "019018"],
            "招商银行": ["021277", "000390", "020723"]
        }, {
            "270023", "016665", "018230", "018147", "012922", "021277"
        }, {}
    
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        # 转换为原有格式 (兼容代码)
        funds_dict = {}
        qdii_codes = set()
        fund_names = {}

        for platform, fund_list in config.get("funds", {}).items():
            funds_dict[platform] = [f["code"] for f in fund_list]
            for fund in fund_list:
                if fund.get("is_qdii", False):
                    qdii_codes.add(fund["code"])
                if fund.get("name"):
                    fund_names[fund["code"]] = fund["name"]

        log(f"✓ 成功加载基金配置: {len(qdii_codes)} 只QDII基金, {len(fund_names)} 只基金名称", "info")

        return funds_dict, qdii_codes, fund_names
        
    except Exception as e:
        log(f"❌ 加载基金配置失败: {e}", "error")
        return None, None, None


# (基金列表和QDII代码现在通过函数参数传递，不再使用全局变量)

# 天天基金API (使用HTTPS)
HISTORY_API = "https://api.fund.eastmoney.com/f10/lsjz"
REALTIME_API = "https://fundgz.1234567.com.cn/js/{}.js"

# 添加请求头，模拟浏览器访问
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://fund.eastmoney.com/"
}

# 创建带统一重试策略的 HTTP Session
def _create_session():
    """创建带自动重试的 requests.Session"""
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(HEADERS)
    return session

def fetch_fund_realtime(fund_code, qdii_codes=None, fund_names=None, max_retries=3, session=None):
    """获取基金实时数据（估算净值和涨跌幅），支持重试。
    如果实时估值API返回空（非交易时段），则回退到历史数据API获取最新净值。"""
    if session is None:
        session = _create_session()
    for attempt in range(1, max_retries + 1):
        try:
            url = REALTIME_API.format(fund_code)
            response = session.get(url, timeout=10)
            response.raise_for_status()

            # 解析JSONP响应
            text = response.text
            json_str = text[text.find('{'):text.rfind('}')+1]

            # 空JSON（如 jsonpgz(); 的情况）- 回退到历史数据
            if not json_str.strip():
                log(f"  ⚠ 基金 {fund_code} 实时估值API返回空数据，尝试回退到历史数据API...")
                return _fetch_latest_from_history(fund_code, qdii_codes, fund_names, session=session)

            data = json.loads(json_str)

            # 检查返回数据是否有效（有些基金不在交易时段会返回空内容）
            if not data.get("name") and not data.get("gsz"):
                log(f"  ⚠ 基金 {fund_code} 实时估值无数据，尝试回退到历史数据API...")
                return _fetch_latest_from_history(fund_code, qdii_codes, fund_names, session=session)

            return {
                "code": fund_code,
                "name": data.get("name", ""),
                "nav": float(data.get("gsz", 0)),  # 估算净值
                "nav_date": data.get("gztime", ""),  # 估值时间
                "change_percent": float(data.get("gszzl", 0)),  # 估算涨跌幅
                "nav_status": "estimated",  # 实时估算值
            }
        except json.JSONDecodeError:
            # JSON解析失败 - 回退到历史数据
            log(f"  ⚠ 基金 {fund_code} 实时估值JSON解析失败，尝试回退到历史数据API...")
            return _fetch_latest_from_history(fund_code, qdii_codes, fund_names, session=session)
        except Exception as e:
            if attempt < max_retries:
                log(f"  ⚠ 获取基金 {fund_code} 实时数据失败 (第{attempt}次), {max_retries - attempt}次重试机会剩余: {e}")
                time.sleep(2)
            else:
                log(f"❌ 获取基金 {fund_code} 实时数据失败 (已重试{max_retries}次): {e}")
                # 最后一次也尝试回退
                return _fetch_latest_from_history(fund_code, qdii_codes, fund_names, session=session)

def _fetch_latest_from_history(fund_code, qdii_codes=None, fund_names=None, session=None):
    """从历史净值API获取最新记录，作为实时数据的回退方案。
    QDII基金（T+1更新）若当天无新净值，自动沿用上一交易日净值并标记延迟。
    """
    if session is None:
        session = _create_session()
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        is_qdii = qdii_codes and fund_code in qdii_codes

        params = {
            "fundCode": fund_code,
            "pageIndex": 1,
            "pageSize": 2,  # 取最近2条以计算涨跌幅
            "startDate": "2020-01-01",
            "endDate": today
        }
        response = session.get(HISTORY_API, params=params, timeout=10)
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

        # QDII延迟检测：若最新记录日期不是今天，说明净值未更新
        nav_status = "confirmed"
        if is_qdii and nav_date != today:
            nav_status = "delayed"
            log(f"  ⚠ QDII基金 {fund_code} 净值延迟：最新净值日期 {nav_date}，非今日 {today}")

        # 从配置文件获取基金名称（回退方案）
        fund_name = _get_fund_name(fund_code, fund_names)

        status_text = "延迟" if nav_status == "delayed" else "确认"
        log(f"  ✓ 基金 {fund_code} 回退成功 [{status_text}]: 净值 {nav} ({nav_date}), 涨跌 {change_percent}%")
        return {
            "code": fund_code,
            "name": fund_name,
            "nav": nav,
            "nav_date": nav_date,
            "change_percent": change_percent,
            "nav_status": nav_status,
        }
    except Exception as e:
        log(f"  ❌ 基金 {fund_code} 历史数据回退也失败: {e}")
        return None

def _get_fund_name(fund_code, fund_names=None):
    """从配置文件获取基金名称（回退方案使用），不再硬编码"""
    if fund_names and fund_code in fund_names:
        return fund_names[fund_code]
    return fund_code

def _get_earliest_purchase_date(nested_records):
    """从持仓记录中找出最早的交易日期，往前推7天作为历史数据起始日期"""
    earliest = None
    for platform, funds in nested_records.items():
        for fund_code, purchases in funds.items():
            for p in purchases:
                d = p.get("date", "")
                if d and (earliest is None or d < earliest):
                    earliest = d
    if earliest:
        # 往前推7天，确保能找到交易日前后的净值
        dt = datetime.strptime(earliest, "%Y-%m-%d") - timedelta(days=7)
        return dt.strftime("%Y-%m-%d")
    return "2020-01-01"  # 兜底默认值

def fetch_fund_history(fund_code, start_date="2020-01-01", max_pages=100, session=None):
    """获取基金历史净值数据（从start_date开始获取）"""
    if session is None:
        session = _create_session()
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

            response = session.get(HISTORY_API, params=params, timeout=10)
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

def get_nav_from_history(history, target_date, before_15=True):
    """从历史数据中查找指定日期的净值。

    基金交易规则：
    - 工作日15点前提交 → 按当天净值确认 (before_15=True)
    - 工作日15点后或周末提交 → 按下一个工作日净值确认 (before_15=False)

    参数：
        history: 历史净值列表（按日期升序）
        target_date: 交易日期（"YYYY-MM-DD"）
        before_15: 是否为15点前提交（默认True，即按当天净值）

    返回值：
        dict: {"nav": float, "nav_source": str} 或 None
        nav_source 取值：
        - "exact": 精确匹配到目标日期（before_15=True 且精确匹配）
        - "next_trading_day": 使用下一交易日净值（before_15=False 或周末/节假日）
    """
    if before_15:
        # 15点前：先尝试精确匹配
        for record in reversed(history):
            if record["date"] == target_date:
                return {"nav": record["nav"], "nav_source": "exact"}

    # 15点后或精确匹配失败：找最近的后一个交易日
    for record in history:
        if record["date"] > target_date:
            log(f"  注意：{target_date} 非交易日或15点后提交，使用下一交易日 {record['date']} 的净值: {record['nav']}")
            return {"nav": record["nav"], "nav_source": "next_trading_day"}

    return None

def create_template_files():
    """创建模板文件"""
    # 创建 data 目录
    data_dir = os.path.join(BASE_DIR, "data")
    os.makedirs(data_dir, exist_ok=True)

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

    template_path = os.path.join(data_dir, "purchase_records.json")
    if not os.path.exists(template_path):
        with open(template_path, "w", encoding="utf-8") as f:
            json.dump(template_records, f, ensure_ascii=False, indent=2)
        log(f"✓ 已创建模板持仓记录文件: {template_path}")
        log("  请编辑此文件，填入你的实际买入记录")
        log("  卖出记录格式: {\"date\": \"2024-06-15\", \"amount\": 500.00, \"type\": \"sell\"}")
        return False

    return True

def load_purchase_records():
    """加载持仓记录（保留平台分层结构，不做扁平化）"""
    try:
        records_file = os.path.join(BASE_DIR, "data", "purchase_records.json")

        # 如果文件不存在，创建模板
        if not os.path.exists(records_file):
            log("未找到持仓记录文件，正在创建模板...")
            create_template_files()
            return None

        with open(records_file, "r", encoding="utf-8") as f:
            raw_records = json.load(f)

        # 统计总基金数
        fund_count = sum(len(funds) for funds in raw_records.values())
        log(f"✓ 成功加载持仓记录: {fund_count} 只基金（保留平台信息）")
        return raw_records
    except Exception as e:
        log(f"❌ 加载持仓记录失败: {e}")
        return None

def calculate_holdings(purchases, current_nav, history):
    """计算持仓信息和实际收益（支持买入和卖出记录，FIFO法自动抵扣）
    参数：
        purchases: 该基金的全部交易记录列表（已按平台区分，不含其他平台同名基金）
        current_nav: 当前净值
        history: 历史净值列表
    """
    if not purchases or len(purchases) == 0:
        return {
            "total_invested": 0,
            "total_shares": 0,
            "current_value": 0,
            "profit_loss": 0,
            "profit_loss_percent": 0,
            "purchases": [],
            "realized_profit_loss": 0
        }

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
        # before_15: 是否为15点前提交（默认True，即按当天净值确认）
        before_15 = purchase.get("before_15", True)
        nav_result = get_nav_from_history(history, date, before_15)

        if not nav_result or nav_result["nav"] <= 0:
            log(f"  ⚠ 无法获取 {date} 的净值，跳过此笔记录")
            continue

        nav_on_date = nav_result["nav"]
        nav_source = nav_result.get("nav_source", "exact")

        if trans_type == "sell":
            # 卖出记录：按FIFO法从最早买入抵扣
            sell_shares = amount / nav_on_date
            remaining_sell_shares = sell_shares
            sell_realized_profit = 0  # 本笔卖出实现的盈亏
            total_fifo_cost = 0  # 本笔卖出的FIFO总成本

            log(f"  卖出记录: {date}, 金额 ¥{amount}, 净值 {nav_on_date:.4f} (来源: {nav_source}), 份额 {sell_shares:.2f}")

            # FIFO抵扣
            for buy in buy_queue:
                if remaining_sell_shares <= 0:
                    break

                if buy["remaining_shares"] <= 0:
                    continue

                # 本次抵扣的份额
                deduct_shares = min(remaining_sell_shares, buy["remaining_shares"])
                deduct_amount = deduct_shares * buy["nav"]  # 成本金额
                total_fifo_cost += deduct_amount  # 累加FIFO成本
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
                "realized_profit": round(sell_realized_profit, 2),  # 本笔卖出的盈亏
                "fifo_cost": round(total_fifo_cost, 2),  # 本笔卖出的FIFO总成本
                "nav_source": nav_source  # 净值来源：exact / next_trading_day
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
                "type": "buy",
                "nav_source": nav_source  # 净值来源：exact / next_trading_day
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

def calculate_cumulative_returns(history, purchases, original_purchases=None, history_for_nav=None):
    """为历史数据中每一天预计算累计收益率，使用与 calculate_holdings() 一致的 FIFO 逻辑。

    参数：
        history: 历史净值列表（按日期升序）
        purchases: calculate_holdings() 返回的 purchase_details（含 fifo_cost）
        original_purchases: 原始交易记录（含 before_15 字段），用于精确 FIFO 模拟
        history_for_nav: 与 original_purchases 配合的历史数据，用于查净值
    """
    if not history:
        return []

    return_rates = []

    # 使用原始交易记录进行精确 FIFO 模拟（如果提供的话）
    if original_purchases and history_for_nav:
        sorted_purchases = sorted(original_purchases, key=lambda p: p["date"])

        for h in history:
            # 对每个历史时点，模拟 FIFO 队列到该日期为止的状态
            buy_queue = []  # [{date, nav, remaining_shares}]

            for p in sorted_purchases:
                if p["date"] > h["date"]:
                    break  # 后续交易不参与

                trans_type = p.get("type", "buy")
                before_15 = p.get("before_15", True)

                # 查找交易日净值
                nav_result = get_nav_from_history(history_for_nav, p["date"], before_15)
                if not nav_result or nav_result["nav"] <= 0:
                    continue
                nav_on_date = nav_result["nav"]

                if trans_type == "sell":
                    # FIFO 抵扣
                    sell_shares = p["amount"] / nav_on_date
                    remaining_sell = sell_shares

                    for buy in buy_queue:
                        if remaining_sell <= 0:
                            break
                        if buy["remaining_shares"] <= 0:
                            continue
                        deduct = min(remaining_sell, buy["remaining_shares"])
                        buy["remaining_shares"] -= deduct
                        remaining_sell -= deduct
                else:
                    # 买入：加入 FIFO 队列
                    shares = p["amount"] / nav_on_date
                    buy_queue.append({
                        "date": p["date"],
                        "nav": nav_on_date,
                        "remaining_shares": shares
                    })

            # 计算该时点的持仓
            total_shares = sum(b["remaining_shares"] for b in buy_queue)
            total_cost = sum(b["remaining_shares"] * b["nav"] for b in buy_queue)

            if total_cost > 0 and total_shares > 0:
                value = h["nav"] * total_shares
                profit = value - total_cost
                return_rate = (profit / total_cost) * 100
                return_rates.append(round(return_rate, 2))
            else:
                return_rates.append(None)
    else:
        # 回退：使用 purchase_details 中的 fifo_cost 进行简化计算
        sorted_purchases = sorted(purchases, key=lambda p: p["date"])

        for h in history:
            total_shares = 0
            total_invested = 0

            for p in sorted_purchases:
                if p["date"] <= h["date"]:
                    if p.get("type") == "sell":
                        total_shares -= abs(p["shares"])
                        fifo_cost = p.get("fifo_cost", abs(p["amount"]))
                        total_invested -= fifo_cost
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
    log("="*60)
    log(f"基金收益追踪系统 - 数据抓取")
    log(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log("="*60)

    # 加载基金配置
    log("\n[0/4] 加载基金配置...")
    funds, qdii_codes, fund_names = load_fund_config()

    if funds is None:
        log("❌ 无法加载基金配置，退出")
        return
    
    # 检查/创建模板文件
    log("\n[1/4] 检查必要文件...")
    has_records = create_template_files()
    if not has_records:
        log("\n⚠️  请先编辑 data/purchase_records.json 文件，填入你的实际买入记录")
        log("   模板文件已创建，你可以参考其中的格式")
        return

    # 加载持仓记录
    log("\n[2/4] 加载持仓记录...")
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
    previous_file = os.path.join(BASE_DIR, "data", "funds_data.json")
    if os.path.exists(previous_file):
        try:
            with open(previous_file, "r", encoding="utf-8") as f:
                previous_data = json.load(f)
            update_time_str = previous_data.get('update_time', '未知')
            log(f"✓ 已加载上次数据作为备份（更新时间: {update_time_str}）")
        except Exception as e:
            log(f"⚠️ 加载上次数据失败: {e}")
            # 继续执行，不影响主流程

    # 构建上次数据的快速查找表: {fund_code: fund_data}
    prev_fund_map = {}
    if previous_data:
        for platform_name, fund_list in previous_data.get("funds", {}).items():
            for fund_item in fund_list:
                prev_fund_map[fund_item["code"]] = fund_item

    # 处理所有基金
    log("\n[3/4] 获取基金数据...")

    # 创建共享 HTTP Session（统一重试策略）
    http_session = _create_session()
    log("✓ HTTP Session 已创建（自动重试: 3次, 退避: 1s）")

    # 计算历史数据起始日期（所有基金共享，只需计算一次）
    history_start_date = _get_earliest_purchase_date(purchase_records)
    log(f"历史数据起始日期: {history_start_date}")

    failed_funds = []
    for platform, codes in funds.items():
        log(f"处理 {platform} 的基金...")

        if platform not in all_data["funds"]:
            all_data["funds"][platform] = []

        for code in codes:
            log(f"  正在处理基金 {code}...")

            # 获取实时数据
            realtime = fetch_fund_realtime(code, qdii_codes, fund_names, session=http_session)
            if not realtime:
                # API失败：尝试使用上次数据
                if code in prev_fund_map:
                    old_fund = prev_fund_map[code]
                    log(f"  ⚠ 使用上次缓存数据: {old_fund.get('name', code)}（净值 {old_fund.get('current_nav', '?')} @{old_fund.get('nav_date', '?')}）")
                    all_data["funds"][platform].append(old_fund)
                    all_data["summary"]["total_invested"] += old_fund["holdings"]["total_invested"]
                    all_data["summary"]["total_value"] += old_fund["holdings"]["current_value"]
                    failed_funds.append(code)
                else:
                    log(f"  ❌ 基金 {code} 无上次缓存数据，本次跳过！")
                    failed_funds.append(code)
                continue

            # 获取历史数据（从最早交易记录前7天开始获取，避免拉取大量无用数据）
            history = fetch_fund_history(code, start_date=history_start_date, session=http_session)

            # 获取该 (platform, code) 对应的交易记录（不与其他平台同名基金混淆）
            purchases = purchase_records.get(platform, {}).get(code, [])
            # 计算持仓和收益
            holdings = calculate_holdings(purchases, realtime["nav"], history)

            # 预计算累计收益率（使用 FIFO 一致逻辑，避免前端重复计算）
            cumulative_returns = calculate_cumulative_returns(
                history, holdings["purchases"],
                original_purchases=purchases, history_for_nav=history
            )

            # 组织数据
            fund_data = {
                "code": code,
                "name": realtime["name"],
                "platform": platform,
                "current_nav": realtime["nav"],
                "nav_date": realtime["nav_date"],
                "daily_return": realtime["change_percent"],
                "nav_status": realtime.get("nav_status", "confirmed"),
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
    log("\n[4/4] 计算总计收益...")
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
    log("\n" + "="*60)
    log("✓ 数据抓取完成！")
    log(f"  总投入: ¥{summary['total_invested']:.2f}")
    log(f"  当前市值: ¥{summary['total_value']:.2f}")
    profit_sign = "+" if summary["total_profit_loss"] >= 0 else ""
    log(f"  未实现盈亏: {profit_sign}¥{summary['total_profit_loss']:.2f} ({profit_sign}{summary['total_profit_loss_percent']:.2f}%)")
    realized_sign = "+" if summary["total_realized_profit_loss"] >= 0 else ""
    log(f"  已实现盈亏: {realized_sign}¥{summary['total_realized_profit_loss']:.2f}")
    log(f"  总盈亏: ¥{profit_sign}{summary['total_profit_loss']:.2f} ({profit_sign}{summary['total_profit_loss_percent']:.2f}%)")
    if failed_funds:
        log(f"\n  [Warning] 以下基金使用缓存数据或跳过: {', '.join(failed_funds)}")
    log("="*60)

    # 保存数据
    output_file = os.path.join(BASE_DIR, "data", "funds_data.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)

    log(f"\n✓ 数据已保存到 {output_file}")
    log(f"  更新时间: {all_data['update_time']}")

    # 生成持仓快照
    log("\n生成持仓快照...")
    try:
        from generate_holdings import generate_holdings_snapshot
        generate_holdings_snapshot()
    except Exception as e:
        log(f"  ⚠️ 持仓快照生成失败: {e}")

if __name__ == "__main__":
    main()
