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

import sys
import io

# 强制 UTF-8 stdout/stderr，避免 Windows 控制台 GBK 编码报错
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import json
import os
import tempfile
from datetime import datetime, timedelta
import time
import argparse
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
# 线程锁：保护 history_cache 的读写（ThreadPoolExecutor 并行访问）
history_cache_lock = threading.Lock()
# 时区支持：优先使用 zoneinfo (Python 3.9+)，回退到 pytz
try:
    from zoneinfo import ZoneInfo
    _BEIJING_TZ = ZoneInfo("Asia/Shanghai")
except ImportError:
    try:
        import pytz
        _BEIJING_TZ = pytz.timezone("Asia/Shanghai")
    except ImportError:
        # 如果都没有，使用固定偏移量（UTC+8）
        from datetime import timezone
        _BEIJING_TZ = timezone(timedelta(hours=8))

def get_beijing_time():
    """获取当前北京时间"""
    return datetime.now(_BEIJING_TZ)

# 导入日志模块
try:
    from logger_config import log
except ImportError:
    # 如果 logger_config 不存在，使用简单的 log 函数
    def log(message, level='info'):
        print(message)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_CACHE_FILE = os.path.join(BASE_DIR, "data", "history_cache.json")


def load_fund_config():
    """加载基金配置文件（唯一数据源：fund_config.json）"""
    config_file = os.path.join(BASE_DIR, "fund_config.json")

    if not os.path.exists(config_file):
        log("[ERROR] 基金配置文件不存在: {}".format(config_file), "error")
        log("请创建 fund_config.json，格式参考项目文档", "error")
        return {}, set(), {}

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

        log("[OK] 成功加载基金配置: {} 只QDII基金, {} 只基金名称".format(len(qdii_codes), len(fund_names)), "info")

        return funds_dict, qdii_codes, fund_names

    except Exception as e:
        log("[ERROR] 加载基金配置失败: {}".format(e), "error")
        return {}, set(), {}


# (基金列表和QDII代码现在通过函数参数传递，不再使用全局变量)

# 天天基金API (使用HTTPS)
HISTORY_API = "https://api.fund.eastmoney.com/f10/lsjz"
REALTIME_API = "https://fundgz.1234567.com.cn/js/{}.js"


def load_history_cache():
    """加载历史数据缓存。返回 {fund_code: [entries]} 或 {} if not found."""
    if not os.path.exists(HISTORY_CACHE_FILE):
        return {}
    try:
        with open(HISTORY_CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        # 去掉 _meta 等元数据键
        return {k: v for k, v in cache.items() if not k.startswith("_")}
    except Exception as e:
        log(f"⚠️ 加载历史缓存失败，将全量获取: {e}", "warning")
        return {}


def save_history_cache(cache_data):
    """保存历史数据缓存到磁盘（原子写入，防止中断导致文件损坏）。"""
    os.makedirs(os.path.dirname(HISTORY_CACHE_FILE), exist_ok=True)
    output = {"_meta": {"version": 1}, **cache_data}
    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(HISTORY_CACHE_FILE), suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp_path, HISTORY_CACHE_FILE)  # 原子替换
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
    total_entries = sum(len(v) for v in cache_data.values())
    log(f"✓ 历史缓存已保存: {len(cache_data)} 只基金, {total_entries} 条记录")


def merge_history(existing, new_entries):
    """合并历史数据：新条目覆盖同日期旧条目，按日期升序排序。

    Args:
        existing: 缓存中的历史数据 list[dict]，按日期升序
        new_entries: API 新获取的数据 list[dict]，可能存在日期重叠

    Returns:
        合并后的 list[dict]，按日期升序
    """
    if not existing:
        return sorted([e for e in new_entries if e.get("date")], key=lambda x: x["date"])
    if not new_entries:
        return existing

    # 新条目优先（处理 NAV 纠正场景）；过滤空日期防止字典键冲突
    merged = {e["date"]: e for e in existing if e.get("date")}
    for entry in new_entries:
        if entry.get("date"):
            merged[entry["date"]] = entry

    return sorted(merged.values(), key=lambda x: x["date"])

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

            # 解析JSONP响应：找最外层括号 () 包裹的 JSON，避免匹配字符串值中的 {}
            text = response.text
            json_start = text.find('(') + 1
            json_end = text.rfind(')')
            if json_start <= 0 or json_end <= json_start:
                log(f"  ⚠ 基金 {fund_code} 实时估值JSONP格式异常，尝试回退到历史数据API...")
                return _fetch_latest_from_history(fund_code, qdii_codes, fund_names, session=session)
            json_str = text[json_start:json_end].strip()

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
                "nav_status": "estimated",  # 实时估算值，main()会根据历史数据最新日期修正
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
        today = get_beijing_time().strftime("%Y-%m-%d")
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

        # 净值状态：由 main() 根据历史数据最新日期统一修正
        # 此处先设为 confirmed，main() 会覆盖为 confirmed_today / delayed / confirmed
        nav_status = "confirmed"

        # 从配置文件获取基金名称（回退方案）
        fund_name = _get_fund_name(fund_code, fund_names)

        log(f"  ✓ 基金 {fund_code} 回退成功: 净值 {nav} ({nav_date}), 涨跌 {change_percent}%")
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

def _get_fund_earliest_purchase_date(purchase_records, platform, fund_code):
    """获取指定基金的最早交易日期，往前推7天作为历史数据起始日期"""
    purchases = purchase_records.get(platform, {}).get(fund_code, [])
    if not purchases:
        return None
    earliest = None
    for p in purchases:
        d = p.get("date", "")
        if d and (earliest is None or d < earliest):
            earliest = d
    if earliest:
        dt = datetime.strptime(earliest, "%Y-%m-%d") - timedelta(days=7)
        return dt.strftime("%Y-%m-%d")
    return None


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

def fetch_fund_history(fund_code, start_date="2020-01-01", max_pages=100, session=None, incremental_from=None):
    """获取基金历史净值数据。

    Args:
        fund_code: 基金代码
        start_date: 历史数据起始日期
        max_pages: 最大翻页数
        session: HTTP Session（统一重试策略）
        incremental_from: 增量获取模式，获取此日期附近的数据（含7天重叠以捕获NAV纠正）
    """
    if session is None:
        session = _create_session()
    try:
        # 增量模式：从缓存最新日期往前7天开始获取（重叠窗口捕获NAV纠正）
        effective_start = start_date
        if incremental_from:
            # 往前7天重叠，确保近期的NAV纠正能被捕获（约5个交易日，1个API页）
            overlap_start = (datetime.strptime(incremental_from, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
            effective_start = overlap_start
            log(f"  增量获取基金 {fund_code}，从 {effective_start} 开始（含7天重叠窗口）...")
        else:
            log(f"  获取基金 {fund_code} 的历史净值数据...")
        all_history = []
        page_index = 1
        page_size = 500  # API支持最大500条/页，减少API调用次数

        while page_index <= max_pages:
            params = {
                "fundCode": fund_code,
                "pageIndex": page_index,
                "pageSize": page_size,
                "startDate": effective_start,
                "endDate": get_beijing_time().strftime("%Y-%m-%d")
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
            # 短暂停顿避免触发API速率限制（0.1s足够，页面已改为500条/页）
            time.sleep(0.1)

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

def create_template_files(funds=None):
    """创建模板文件（从配置动态生成示例数据）"""
    # 创建 data 目录
    data_dir = os.path.join(BASE_DIR, "data")
    os.makedirs(data_dir, exist_ok=True)

    # 从配置动态生成模板持仓记录（每个平台取第一只基金作为示例）
    template_records = {}
    if funds:
        for platform, codes in funds.items():
            if codes:
                first_code = codes[0]
                template_records[platform] = {
                    first_code: [
                        {"date": "2024-01-15", "amount": 1000.00},
                        {"date": "2024-02-15", "amount": 1000.00},
                        {"date": "2024-06-15", "amount": 500.00, "type": "sell"}
                    ]
                }
    else:
        # 无配置时创建空模板
        template_records = {
            "示例平台": {
                "000000": [
                    {"date": "2024-01-15", "amount": 1000.00}
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

def validate_purchase_records(records):
    """
    校验交易记录 schema，提前拦截脏数据
    结构: {平台: {基金代码: [交易记录]}}
    返回: (is_valid, validated_records, errors)
    """
    if not isinstance(records, dict):
        return False, None, ["顶层结构必须是对象（平台→基金代码→交易列表）"]

    validated = {}
    all_errors = []

    for platform, funds in records.items():
        if not isinstance(funds, dict):
            all_errors.append(f"平台 '{platform}' 的值必须是对象（基金代码→交易列表）")
            continue

        validated[platform] = {}
        for fund_code, trans_list in funds.items():
            if not isinstance(trans_list, list):
                all_errors.append(f"平台 '{platform}' 基金 '{fund_code}' 的交易记录必须是列表")
                continue

            validated[platform][fund_code] = []
            for i, rec in enumerate(trans_list):
                prefix = f"{platform}.{fund_code}[{i}]"
                if not isinstance(rec, dict):
                    all_errors.append(f"{prefix}: 必须是对象")
                    continue

                # 校验 date
                if "date" not in rec:
                    all_errors.append(f"{prefix}: 缺少 'date' 字段")
                    continue
                date_val = rec["date"]
                if not isinstance(date_val, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", date_val):
                    all_errors.append(f"{prefix}: 'date' 格式错误，应为 YYYY-MM-DD")
                    continue

                # 校验 amount
                if "amount" not in rec:
                    all_errors.append(f"{prefix}: 缺少 'amount' 字段")
                    continue
                amount_val = rec["amount"]
                if not isinstance(amount_val, (int, float)) or amount_val <= 0:
                    all_errors.append(f"{prefix}: 'amount' 必须是正数")
                    continue

                # 校验 type（可选）
                if "type" in rec and rec["type"] not in ("buy", "sell"):
                    all_errors.append(f"{prefix}: 'type' 必须是 'buy' 或 'sell'")
                    continue

                validated[platform][fund_code].append(rec)

    return len(all_errors) == 0, validated, all_errors


def load_purchase_records():
    """加载持仓记录（保留平台分层结构，不做扁平化）"""
    try:
        records_file = os.path.join(BASE_DIR, "data", "purchase_records.json")

        # 如果文件不存在，创建模板
        if not os.path.exists(records_file):
            log("未找到持仓记录文件，正在创建空模板...")
            create_template_files(None)
            return None

        with open(records_file, "r", encoding="utf-8") as f:
            raw_records = json.load(f)

        # 校验交易记录格式
        is_valid, validated, errors = validate_purchase_records(raw_records)
        if not is_valid:
            log("[ERROR] 交易记录格式错误:")
            for err in errors:
                log("  - {}".format(err))
            return None

        # 统计总基金数
        fund_count = sum(len(funds) for funds in validated.values())
        log(f"✓ 成功加载持仓记录: {fund_count} 只基金（保留平台信息）")
        return validated
    except Exception as e:
        log(f"❌ 加载持仓记录失败: {e}")
        return None

def calculate_holdings(purchases, current_nav, history, fund_code=""):
    """计算持仓信息和实际收益（支持买入和卖出记录，FIFO法自动抵扣）
    参数：
        purchases: 该基金的全部交易记录列表（已按平台区分，不含其他平台同名基金）
        current_nav: 当前净值
        history: 历史净值列表
        fund_code: 基金代码（用于日志输出）
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

            # 检查是否超卖（剩余未抵扣份额 > 0）
            if remaining_sell_shares > 0.0001:
                actual_sell_shares = sell_shares - remaining_sell_shares
                actual_amount = actual_sell_shares * nav_on_date
                log(f"  ⚠ 警告: 基金 {fund_code} 卖出份额超过持仓！尝试卖出 {sell_shares:.2f} 份，实际可卖出 {actual_sell_shares:.2f} 份")
                # 修正为实际可卖出的份额和金额
                sell_shares = actual_sell_shares
                amount = actual_amount

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

    优化：增量维护 FIFO 队列，时间复杂度从 O(H×P) 降至 O(H+P)，
    其中 H=历史天数，P=交易笔数。

    参数：
        history: 历史净值列表（按日期升序）
        purchases: calculate_holdings() 返回的 purchase_details（含 fifo_cost）
        original_purchases: 原始交易记录（含 before_15 字段），用于精确 FIFO 模拟
        history_for_nav: 与 original_purchases 配合的历史数据，用于查净值
    """
    if not history:
        return []

    # 确保 history 按日期升序排列
    sorted_history = sorted(history, key=lambda h: h["date"])
    n = len(sorted_history)
    return_rates = [None] * n

    # ── 路径1：精确 FIFO 模拟（提供原始交易记录时）──
    if original_purchases and history_for_nav:
        sorted_purchases = sorted(original_purchases, key=lambda p: p["date"])
        buy_queue = []          # FIFO 队列：[{date, nav, remaining_shares}]
        purchase_idx = 0
        num_purchases = len(sorted_purchases)

        for h_idx, h in enumerate(sorted_history):
            # 增量推进：处理所有在该历史时点之前（含当日）的交易
            while purchase_idx < num_purchases and sorted_purchases[purchase_idx]["date"] <= h["date"]:
                p = sorted_purchases[purchase_idx]
                purchase_idx += 1

                trans_type = p.get("type", "buy")
                before_15 = p.get("before_15", True)

                nav_result = get_nav_from_history(history_for_nav, p["date"], before_15)
                if not nav_result or nav_result["nav"] <= 0:
                    continue
                nav_on_date = nav_result["nav"]

                if trans_type == "sell":
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
                    shares = p["amount"] / nav_on_date
                    buy_queue.append({
                        "date": p["date"],
                        "nav": nav_on_date,
                        "remaining_shares": shares
                    })

            # 计算该时点的累计收益率
            total_shares = sum(b["remaining_shares"] for b in buy_queue)
            total_cost = sum(b["remaining_shares"] * b["nav"] for b in buy_queue)

            if total_cost > 0 and total_shares > 0:
                value = h["nav"] * total_shares
                profit = value - total_cost
                return_rates[h_idx] = round((profit / total_cost) * 100, 2)

        return return_rates

    # ── 路径2：回退（使用 purchase_details 中的 fifo_cost）──
    # 同样改为增量计算，从 O(H×P) 降至 O(H+P)
    sorted_purchases = sorted(purchases, key=lambda p: p["date"])
    purchase_idx = 0
    num_purchases = len(sorted_purchases)
    total_shares = 0
    total_invested = 0

    for h_idx, h in enumerate(sorted_history):
        while purchase_idx < num_purchases and sorted_purchases[purchase_idx]["date"] <= h["date"]:
            p = sorted_purchases[purchase_idx]
            purchase_idx += 1

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
            return_rates[h_idx] = round((profit / total_invested) * 100, 2)

    return return_rates
    

# ── 瓶颈3：提取单基金处理函数，供 ThreadPoolExecutor 并行调用 ─────────────────────

def process_fund(platform, code, fund_start_date, http_session,
                  purchase_records, qdii_codes, fund_names,
                  prev_fund_map, today, history_cache):
    """
    处理单只基金：获取历史数据、实时数据，计算持仓和收益。
    线程安全：使用 history_cache_lock 保护缓存读写。

    返回: (fund_data_dict, total_invested, total_value) 成功
           (None, 0, 0, error_message) 失败
    """
    try:
        # --- 第1步：获取历史数据（与实时数据解耦） ---
        cached_history = history_cache.get(code, [])
        original_cache_count = len(cached_history)
        if cached_history:
            last_cached_date = cached_history[-1]["date"]
            if cached_history[0]["date"] > fund_start_date:
                full_history = fetch_fund_history(code, start_date=fund_start_date, session=http_session)
                history = merge_history(cached_history, full_history)
            else:
                new_history = fetch_fund_history(code, start_date=fund_start_date,
                                                  session=http_session, incremental_from=last_cached_date)
                history = merge_history(cached_history, new_history)
        else:
            history = fetch_fund_history(code, start_date=fund_start_date, session=http_session)

        # 线程安全：更新缓存并立即保存
        with history_cache_lock:
            history_cache[code] = history
            try:
                save_history_cache(history_cache)
            except Exception as cache_err:
                log("⚠️ 保存历史缓存失败: {}".format(cache_err), "warning")

        if not history:
            if cached_history:
                history = cached_history
            else:
                return (None, 0, 0, "历史数据为空，且无缓存")

        # --- 第2步：获取实时数据 ---
        realtime = fetch_fund_realtime(code, qdii_codes, fund_names, session=http_session)
        if not realtime:
            if code in prev_fund_map:
                old_fund = prev_fund_map[code]
                return (old_fund, old_fund["holdings"]["total_invested"],
                        old_fund["holdings"]["current_value"], "使用缓存数据")
            else:
                return (None, 0, 0, "无法获取实时数据且无缓存")

        # 修正 nav_status
        beijing_now = get_beijing_time()
        is_after_15 = 15 <= beijing_now.hour <= 23
        if history:
            latest_nav_date = history[-1]["date"]
            if latest_nav_date == today and is_after_15:
                realtime["nav_status"] = "confirmed_today"
            elif code in qdii_codes:
                realtime["nav_status"] = "delayed"
            else:
                realtime["nav_status"] = "confirmed"

        # --- 第3步：计算持仓和收益 ---
        purchases = purchase_records.get(platform, {}).get(code, [])
        if not history:
            return (None, 0, 0, "历史数据为空，无法计算持仓")

        holdings = calculate_holdings(purchases, realtime["nav"], history, fund_code=code)

        cumulative_returns = calculate_cumulative_returns(
            history, holdings["purchases"],
            original_purchases=purchases, history_for_nav=history
        )

        # 计算昨日净值、昨日收益率、昨日收益
        yesterday_nav = realtime["nav"]
        yesterday_return = 0
        yesterday_profit = 0
        yesterday_nav_date = ""
        day_before_yesterday_return = 0
        day_before_yesterday_profit = 0
        if history and len(history) >= 2:
            latest_entry = history[-1]
            if latest_entry["date"] == today:
                yesterday_nav = history[-2]["nav"]
                yesterday_nav_date = history[-2]["date"]
                prev_nav = history[-3]["nav"] if len(history) >= 3 else yesterday_nav
                if len(history) >= 4:
                    day_before_yesterday_nav = history[-3]["nav"]
                    day_before_yesterday_prev_nav = history[-4]["nav"]
                else:
                    day_before_yesterday_nav = yesterday_nav
                    day_before_yesterday_prev_nav = yesterday_nav
            else:
                yesterday_nav = history[-1]["nav"]
                yesterday_nav_date = history[-1]["date"]
                prev_nav = history[-2]["nav"]
                if len(history) >= 3:
                    day_before_yesterday_nav = history[-2]["nav"]
                    day_before_yesterday_prev_nav = history[-3]["nav"]
                else:
                    day_before_yesterday_nav = yesterday_nav
                    day_before_yesterday_prev_nav = yesterday_nav

            if prev_nav > 0:
                yesterday_return = (yesterday_nav - prev_nav) / prev_nav * 100
            if day_before_yesterday_prev_nav > 0:
                day_before_yesterday_return = (day_before_yesterday_nav - day_before_yesterday_prev_nav) / day_before_yesterday_prev_nav * 100
            if holdings["total_shares"] > 0:
                yesterday_profit = round(holdings["total_shares"] * (yesterday_nav - prev_nav), 2)
                day_before_yesterday_profit = round(holdings["total_shares"] * (day_before_yesterday_nav - day_before_yesterday_prev_nav), 2)

        # 组织数据
        latest_history_date = history[-1]["date"] if history else ""
        fund_data = {
            "code": code,
            "name": realtime["name"],
            "platform": platform,
            "current_nav": realtime["nav"],
            "nav_date": realtime["nav_date"],
            "daily_return": realtime["change_percent"],
            "nav_status": realtime.get("nav_status", "confirmed"),
            "latest_history_date": latest_history_date,
            "yesterday_nav": round(yesterday_nav, 4),
            "yesterday_nav_date": yesterday_nav_date,
            "yesterday_return": round(yesterday_return, 2),
            "yesterday_profit": yesterday_profit,
            "day_before_yesterday_return": round(day_before_yesterday_return, 2),
            "day_before_yesterday_profit": day_before_yesterday_profit,
            "holdings": holdings,
            "history": [{"date": h["date"], "nav": h["nav"], "return_rate": cumulative_returns[i]} for i, h in enumerate(history)]
        }

        return (fund_data, holdings["total_invested"], holdings["current_value"], None)

    except Exception as e:
        log("❌ 基金 {} 处理失败: {}".format(code, e))
        import traceback
        log(traceback.format_exc())
        return (None, 0, 0, str(e))


def main():
    """主函数"""
    log("="*60)
    log(f"基金收益追踪系统 - 数据抓取")
    log(f"开始时间: {get_beijing_time().strftime('%Y-%m-%d %H:%M:%S')}")
    log("="*60)

    # 解析命令行参数
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-summary', action='store_true', help='跳过汇总更新（仅更新基金数据）')
    args = parser.parse_args()

    # 自动判断是否需要跳过汇总更新
    # 北京时间 20:00-23:59 的更新不更新汇总
    beijing_now = get_beijing_time()
    if 20 <= beijing_now.hour <= 23:
        if not args.skip_summary:
            args.skip_summary = True
            log(f"⏭ 当前为 {beijing_now.strftime('%H:%M')} 北京时间，自动跳过汇总更新")

    # 备份旧汇总（用于 --skip-summary 模式）
    old_summary = None
    output_file = os.path.join(BASE_DIR, "data", "funds_data.json")
    if os.path.exists(output_file):
        try:
            with open(output_file, "r", encoding="utf-8") as f:
                old_data = json.load(f)
                old_summary = old_data.get("summary")
            log(f"✓ 已备份旧汇总数据")
        except Exception as e:
            log(f"⚠️ 备份旧汇总失败: {e}", "warning")

    # 自动检测新基金（在加载配置之前执行）
    auto_detect_new_funds()

    # 加载基金配置
    log("\n[0/4] 加载基金配置...")
    funds, qdii_codes, fund_names = load_fund_config()

    if not funds:
        log("❌ 无法加载基金配置，退出")
        return
    
    # 检查/创建模板文件
    log("\n[1/4] 检查必要文件...")
    has_records = create_template_files(funds)
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
        "update_time": get_beijing_time().strftime("%Y-%m-%d %H:%M:%S"),
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

    # 计算历史数据起始日期（全局兜底，按基金维度优先）
    history_start_date = _get_earliest_purchase_date(purchase_records)
    log(f"历史数据全局起始日期（兜底）: {history_start_date}")

    # 加载历史数据缓存（增量拉取）
    force_refresh = "--force-refresh" in sys.argv or os.environ.get("FORCE_REFRESH", "").lower() == "true"
    if force_refresh:
        log("⚡ 强制刷新模式: 忽略历史缓存，全量获取")
        history_cache = {}
    else:
        history_cache = load_history_cache()
        if history_cache:
            total_cached = sum(len(v) for v in history_cache.values())
            log(f"✓ 已加载历史缓存: {len(history_cache)} 只基金, {total_cached} 条记录")
        else:
            log("ℹ️ 无历史缓存，将全量获取")

    # 并行处理所有基金（瓶颈3修复：ThreadPoolExecutor）
    log(f"\n[3/4] 并行获取基金数据（线程池 max_workers=3）...")

    # 收集所有待处理基金
    fund_tasks = []
    for platform, codes in funds.items():
        if platform not in all_data["funds"]:
            all_data["funds"][platform] = []
        for code in codes:
            fund_start_date = _get_fund_earliest_purchase_date(purchase_records, platform, code)
            if not fund_start_date:
                fund_start_date = history_start_date
            fund_tasks.append((platform, code, fund_start_date))

    failed_funds = []
    today = get_beijing_time().strftime("%Y-%m-%d")

    # 使用线程池并行处理
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_to_fund = {}
        for platform, code, fund_start_date in fund_tasks:
            future = executor.submit(
                process_fund, platform, code, fund_start_date,
                None,  # process_fund 内部创建自己的 HTTP session
                purchase_records, qdii_codes, fund_names,
                prev_fund_map, today, history_cache
            )
            future_to_fund[future] = (platform, code)

        # 按完成顺序收集结果
        for future in as_completed(future_to_fund):
            platform, code = future_to_fund[future]
            try:
                fund_data, invested, value, error_msg = future.result()
                if fund_data:
                    all_data["funds"][platform].append(fund_data)
                    all_data["summary"]["total_invested"] += invested
                    all_data["summary"]["total_value"] += value
                else:
                    failed_funds.append(code)
                    if error_msg and "使用缓存数据" in error_msg:
                        log(f"  ⚠ 基金 {code} 使用缓存数据")
                    elif error_msg:
                        log(f"  ❌ 基金 {code} 处理失败: {error_msg}")
            except Exception as e:
                failed_funds.append(code)
                log(f"  ❌ 基金 {code} 处理异常: {e}")

    # 计算总计收益
    log("\n[4/4] 计算总计收益...")
    summary = all_data["summary"]
    summary["total_profit_loss"] = round(summary["total_value"] - summary["total_invested"], 2)
    if summary["total_invested"] > 0:
        summary["total_profit_loss_percent"] = round(summary["total_profit_loss"] / summary["total_invested"] * 100, 2)
    
    # 计算昨日盈亏（累加各基金已计算的昨日收益）
    yesterday_profit = 0
    day_before_yesterday_profit = 0
    for platform_funds in all_data["funds"].values():
        for fund in platform_funds:
            yesterday_profit += fund.get("yesterday_profit", 0)
            day_before_yesterday_profit += fund.get("day_before_yesterday_profit", 0)
    
    summary["yesterday_profit_loss"] = round(yesterday_profit, 2)
    summary["yesterday_profit_loss_percent"] = round((yesterday_profit / summary["total_value"] * 100), 2) if summary["total_value"] > 0 else 0
    
    # 计算昨日变化差（与前一交易日对比）
    summary["yesterday_profit_loss_diff"] = round(yesterday_profit - day_before_yesterday_profit, 2)
    
    # 计算昨日收益率变化差（加权平均）
    # 使用各基金昨日收益率和前日收益率，按市值加权计算
    yesterday_return_weighted_sum = 0
    day_before_yesterday_return_weighted_sum = 0
    total_weight = 0
    for platform_funds in all_data["funds"].values():
        for fund in platform_funds:
            weight = fund.get("holdings", {}).get("current_value", 0)
            if weight > 0:
                yesterday_return_weighted_sum += fund.get("yesterday_return", 0) * weight
                day_before_yesterday_return_weighted_sum += fund.get("day_before_yesterday_return", 0) * weight
                total_weight += weight
    
    if total_weight > 0:
        yesterday_return_avg = yesterday_return_weighted_sum / total_weight
        day_before_yesterday_return_avg = day_before_yesterday_return_weighted_sum / total_weight
        summary["yesterday_profit_loss_percent_diff"] = round(yesterday_return_avg - day_before_yesterday_return_avg, 2)
    else:
        summary["yesterday_profit_loss_percent_diff"] = 0
    
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
    yesterday_sign = "+" if summary["yesterday_profit_loss"] >= 0 else ""
    log(f"  昨日盈亏: {yesterday_sign}¥{summary['yesterday_profit_loss']:.2f} ({yesterday_sign}{summary['yesterday_profit_loss_percent']:.2f}%)")
    realized_sign = "+" if summary["total_realized_profit_loss"] >= 0 else ""
    log(f"  已实现盈亏: {realized_sign}¥{summary['total_realized_profit_loss']:.2f}")
    log(f"  总盈亏: ¥{profit_sign}{summary['total_profit_loss']:.2f} ({profit_sign}{summary['total_profit_loss_percent']:.2f}%)")
    if failed_funds:
        log(f"\n  [Warning] 以下基金使用缓存数据或跳过: {', '.join(failed_funds)}")
    log("="*60)

    # 若指定 --skip-summary，恢复旧汇总
    if args.skip_summary and old_summary is not None:
        all_data["summary"] = old_summary
        log("⏭ 跳过汇总更新（使用上次数据）")
    elif args.skip_summary:
        log("⏭ 无旧汇总数据，仍更新汇总")

    # 保存数据
    output_file = os.path.join(BASE_DIR, "data", "funds_data.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)

    log(f"\n✓ 数据已保存到 {output_file}")
    log(f"  更新时间: {all_data['update_time']}")

    # 历史缓存已在处理每只基金时逐个保存，此处无需重复保存

    # 生成持仓快照
    log("\n生成持仓快照...")
    try:
        from generate_holdings import generate_holdings_snapshot
        generate_holdings_snapshot()
    except Exception as e:
        log("[Warning] 持仓快照生成失败: {}".format(e))

    # 自动更新业绩基准指数数据
    log("\n[5/5] 更新业绩基准指数数据...")
    _update_benchmark_data(BASE_DIR, log)


def _update_benchmark_data(base_dir, log):
    """
    基金净值更新完成后，自动更新业绩基准指数数据
    依次调用 fetch_benchmark_data.py 和 fetch_nasdaq.py
    """
    import subprocess, sys

    scripts = [
        ("fetch_benchmark_data.py", "业绩基准（A股/港股指数）"),
        ("fetch_nasdaq.py",       "纳斯达克100指数"),
    ]

    for script_name, desc in scripts:
        script_path = os.path.join(base_dir, script_name)
        if not os.path.exists(script_path):
            log("[Warning] {} 不存在，跳过 {}".format(script_name, desc))
            continue
        try:
            log("  正在更新{}（{}）...".format(desc, script_name))
            result = subprocess.run(
                [sys.executable, script_path],
                cwd=base_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=300,
            )
            if result.returncode == 0:
                lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
                tail = (": " + lines[-1]) if lines else ""
                log("[OK] {}更新完成{}".format(desc, tail))
            else:
                err = result.stderr.strip().splitlines()
                log("[Warning] {}更新失败（退出码 {}）".format(desc, result.returncode))
                if err:
                    log("    错误: {}".format(err[-1][:200]))
        except subprocess.TimeoutExpired:
            log("[Warning] {}更新超时（>300s），跳过".format(desc))
        except Exception as e:
            log("[Warning] {}更新异常: {}".format(desc, e))


def fetch_fund_info_from_web(code, session=None):
    """
    从天天基金网获取基金基本信息（基金名称、类型）
    返回: {"name": "基金名称", "is_qdii": True/False, "benchmark": "..."} 或 None
    """
    import re

    url = "https://fundgz.1234567.com.cn/js/{}.js".format(code)

    try:
        if session is None:
            session = _create_session()

        resp = session.get(url, timeout=10)
        if resp.status_code == 200:
            # 解析 JSONP: jsonpgz({...});
            match = re.search(r'jsonpgz\((.*)\);?\s*$', resp.text, re.DOTALL)
            if match:
                data = json.loads(match.group(1))
                name = data.get("name", "")

                # 启发式判断是否为 QDII
                qdii_keywords = ["全球", "纳斯达克", "美股", "QDII", "海外", "国际"]
                is_qdii = any(kw in name for kw in qdii_keywords)

                benchmark = "纳斯达克100指数" if is_qdii else "科创50指数"

                log("  ✓ 基金 {}: {} ({}，基准: {})".format(code, name, "QDII" if is_qdii else "国内", benchmark))

                return {
                    "name": name,
                    "is_qdii": is_qdii,
                    "benchmark": benchmark
                }
    except Exception as e:
        log("⚠️ 获取基金 {} 信息失败: {}".format(code, e), "warning")

    return None


def auto_detect_new_funds():
    """
    自动检测 purchase_records.json 中的新基金，并添加到 fund_config.json
    """
    log("\n[0.5/4] 自动检测新基金...")

    records_file = os.path.join(BASE_DIR, "data", "purchase_records.json")
    if not os.path.exists(records_file):
        log("ℹ️ 交易记录文件不存在，跳过新基金检测")
        return

    try:
        with open(records_file, "r", encoding="utf-8") as f:
            purchase_records = json.load(f)
    except Exception as e:
        log("⚠️ 加载交易记录失败: {}".format(e), "warning")
        return

    all_fund_codes = set()
    for platform, funds in purchase_records.items():
        if isinstance(funds, dict):
            for code in funds.keys():
                all_fund_codes.add(code)

    if not all_fund_codes:
        log("ℹ️ 交易记录中没有基金代码")
        return

    config_file = os.path.join(BASE_DIR, "fund_config.json")
    if os.path.exists(config_file):
        with open(config_file, "r", encoding="utf-8") as f:
            config = json.load(f)
    else:
        config = {"funds": {}}

    existing_codes = set()
    for platform, fund_list in config.get("funds", {}).items():
        for fund in fund_list:
            existing_codes.add(fund["code"])

    new_codes = all_fund_codes - existing_codes
    if not new_codes:
        log("✓ 无新基金需要添加")
        return

    log("🔍 发现 {} 只新基金: {}".format(len(new_codes), ", ".join(sorted(new_codes))))

    session = _create_session()
    for code in sorted(new_codes):
        log("  正在获取基金 {} 的信息...".format(code))
        fund_info = fetch_fund_info_from_web(code, session)

        if fund_info is None:
            log("  ⚠️ 无法获取基金 {} 的信息，跳过".format(code), "warning")
            continue

        platform = None
        for p, funds in purchase_records.items():
            if isinstance(funds, dict) and code in funds:
                platform = p
                break

        if platform is None:
            log("  ⚠️ 无法确定基金 {} 的平台，跳过".format(code), "warning")
            continue

        if platform not in config["funds"]:
            config["funds"][platform] = []

        config["funds"][platform].append({
            "code": code,
            "name": fund_info["name"],
            "is_qdii": fund_info["is_qdii"],
            "benchmark": fund_info["benchmark"]
        })

        log("  ✓ 已添加基金 {} ({}) 到 {}".format(code, fund_info["name"], platform))

    try:
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        log("✓ 已更新 fund_config.json")
    except Exception as e:
        log("❌ 保存 fund_config.json 失败: {}".format(e), "error")


if __name__ == "__main__":
    main()
