#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基金收益追踪系统 - 数据抓取脚本
从天天基金网获取基金数据，读取持仓记录，计算实际收益

更新说明 (2026-05-13):
1. 优化效率:每只基金只获取一次历史数据，避免重复API调用
2. 改进错误处理:增加重试机制和详细日志
3. 自动创建模板文件:如果不存在持仓记录文件，自动创建模板
4. 支持手动输入净值:如果无法获取历史净值，允许手动输入
"""

import requests
import copy
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import json
import os
import tempfile
from datetime import datetime, timedelta
import time
import argparse
import re
import traceback
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from logger_config import get_beijing_time, safe_load_json, log, setup_encoding

# 线程锁:保护 history_cache 的读写（ThreadPoolExecutor 并行访问）
history_cache_lock = threading.Lock()

def safe_float(val, default=0.0):
    """
    安全转换为 float，处理 '--'、空值、None 等非数字字符串。
    API 返回 '--' 时 float() 会抛 ValueError，此函数统一兜底。
    """
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

# 初始化日志编码（import 已保证 logger_config 存在，无需 try/except）
setup_encoding()


def _parse_jsonpgz(text):
    """解析天天基金 JSONP 响应 (jsonpgz({...}) 格式)。

    兼容两种场景:
    - 精确模式: jsonpgz({"fundcode":"...",...}) 或 jsonpgz({"fundcode":...});
    - 回退模式: 手动取最外层 () 包裹的内容（兼容轻微格式变化）

    Returns:
        dict 或 None（解析失败）
    """
    if not text:
        return None
    # 方式1：正则匹配 jsonpgz(...) 精确格式
    match = re.search(r'jsonpgz\((.*)\);?\s*$', text, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    # 方式2：手动找最外层括号（兼容无 jsonpgz 前缀等格式）
    start = text.find('(') + 1
    end = text.rfind(')')
    if 0 < start < end:
        return json.loads(text[start:end].strip())
    return None


def _atomic_write_json(path, data):
    """原子写入 JSON 文件（tempfile.mkstemp + os.replace 防止中断损坏）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_CACHE_FILE = os.path.join(BASE_DIR, "data", "history_cache.json")

# 基准指数数据
INDEX_CACHE_FILE = os.path.join(BASE_DIR, "data", "benchmark_index_data.json")
BENCHMARK_INDICES = {
    "sh000688": {"name": "科创50", "secid": "1.000688"},
    "us.NDX": {"name": "纳斯达克100指数", "secid": "100.NDX"},
}


def load_fund_config():
    """加载基金配置文件（唯一数据源:fund_config.json）"""
    config_file = os.path.join(BASE_DIR, "fund_config.json")

    if not os.path.exists(config_file):
        log("[ERROR] 基金配置文件不存在: {}".format(config_file), "error")
        log("请创建 fund_config.json，格式参考项目文档", "error")
        return {}, set(), {}

    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)

        # 兼容两种格式:{"funds": {"支付宝": [...]}} 或 {"支付宝": [...]}
        funds_source = config.get("funds", config)
        if not isinstance(funds_source, dict):
            funds_source = {}
        qdii_codes = set()
        fund_names = {}
        funds_dict = {}  # 初始化平台->基金代码列表的字典

        for platform, fund_list in funds_source.items():
            # 防御校验：确保 fund_list 是列表且每项包含 "code" 字段
            if not isinstance(fund_list, list):
                log(f"[WARNING] 平台 {platform} 的配置不是列表，跳过", "warning")
                continue
            log(f"[DEBUG] 平台: {platform}, 基金数: {len(fund_list)}", "info")
            valid_funds = []
            for fund in fund_list:
                if not isinstance(fund, dict) or "code" not in fund:
                    log(f"[WARNING] 平台 {platform} 中存在无效基金条目（缺少 code 字段），已跳过: {fund}", "warning")
                    continue
                valid_funds.append(fund["code"])
                if fund.get("is_qdii", False):
                    qdii_codes.add(fund["code"])
                if fund.get("name"):
                    fund_names[fund["code"]] = fund["name"]
            funds_dict[platform] = valid_funds
            # QDII标记和基金名称已在第一次循环中一并提取

        log("[OK] 成功加载基金配置: {} 只QDII基金, {} 只基金名称".format(len(qdii_codes), len(fund_names)), "info")
        log(f"[DEBUG] funds_dict 平台数: {len(funds_dict)}, 平台列表: {list(funds_dict.keys())}", "info")

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
    cache = safe_load_json(HISTORY_CACHE_FILE, default={}, filter_keys=lambda k: k == "_meta")
    return cache if cache else {}


def save_history_cache(cache_data):
    """保存历史数据缓存到磁盘（原子写入，防止中断导致文件损坏）。"""
    output = {"_meta": {"version": 1}, **cache_data}
    _atomic_write_json(HISTORY_CACHE_FILE, output)
    total_entries = sum(len(v) for v in cache_data.values())
    log(f"✓ 历史缓存已保存: {len(cache_data)} 只基金, {total_entries} 条记录")


def merge_history(existing, new_entries):
    """合并历史数据:新条目覆盖同日期旧条目，按日期升序排序。

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

    # 统一回退函数，避免重复参数传递
    def _fallback():
        return _fetch_latest_from_history(fund_code, qdii_codes, fund_names, session=session)

    for attempt in range(1, max_retries + 1):
        try:
            url = REALTIME_API.format(fund_code)
            response = session.get(url, timeout=10)
            response.raise_for_status()

            # 解析JSONP响应
            data = _parse_jsonpgz(response.text)
            if data is None:
                log(f"  ⚠ 基金 {fund_code} 实时估值JSONP格式异常，尝试回退到历史数据API...")
                return _fallback()

            # 检查返回数据是否有效（有些基金不在交易时段会返回空内容）
            if not data.get("name") and not data.get("gsz"):
                log(f"  ⚠ 基金 {fund_code} 实时估值无数据，尝试回退到历史数据API...")
                return _fallback()

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
            return _fallback()
        except Exception as e:
            if attempt < max_retries:
                log(f"  ⚠ 获取基金 {fund_code} 实时数据失败 (第{attempt}次), {max_retries - attempt}次重试机会剩余: {e}")
                time.sleep(2)
            else:
                log(f"❌ 获取基金 {fund_code} 实时数据失败 (已重试{max_retries}次): {e}")
                # 最后一次也尝试回退
                return _fallback()

def _fetch_latest_from_history(fund_code, qdii_codes=None, fund_names=None, session=None):
    """从历史净值API获取最新记录，作为实时数据的回退方案。
    QDII基金（T+1更新）若当天无新净值，自动沿用上一交易日净值并标记延迟。
    """
    if session is None:
        session = _create_session()
    try:
        today = get_beijing_time().strftime("%Y-%m-%d")

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

        data_obj = result.get("Data") or {}
        items = data_obj.get("LSJZList", []) if isinstance(data_obj, dict) else []
        if not items:
            log(f"  ❌ 基金 {fund_code} 历史数据也为空")
            raise RuntimeError(f"基金 {fund_code} 历史数据为空")

        latest = items[0]
        nav = safe_float(latest.get("DWJZ", 0))
        nav_date = latest.get("FSRQ", "")
        change_percent = safe_float(latest.get("JZZZL"))

        # 净值状态:由 main() 根据历史数据最新日期统一修正
        # 此处先设为 confirmed，main() 会覆盖为 confirmed_today / delayed / confirmed
        nav_status = "confirmed"

        # 从配置文件获取基金名称（内联 _get_fund_name）
        fund_name = fund_names.get(fund_code, fund_code) if fund_names else fund_code

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


def fetch_fund_history(fund_code, start_date="2020-01-01", max_pages=200, session=None, incremental_from=None):
    """获取基金历史净值数据。

    Args:
        fund_code: 基金代码
        start_date: 历史数据起始日期
        max_pages: 最大翻页数（默认200页，覆盖约4000条记录）
        session: HTTP Session（统一重试策略）
        incremental_from: 增量获取模式，获取此日期附近的数据（含7天重叠以捕获NAV纠正）
    """
    if session is None:
        session = _create_session()
    try:
        # 增量模式:从缓存最新日期往前7天开始获取（重叠窗口捕获NAV纠正）
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
        # 2026-05-25: API限制pageSize>=300时返回空数据，且无论设多少固定每页20条
        page_size = 20

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
                    "nav": safe_float(item.get("DWJZ", 0)),
                    "change_percent": safe_float(item.get("JZZZL", 0)) if item.get("JZZZL") else 0
                })

            # 如果返回的数据少于page_size，说明没有更多页了
            if len(items) < page_size:
                break

            page_index += 1
            # 短暂停顿避免触发API速率限制
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

    基金交易规则:
    - 工作日15点前提交 → 按当天净值确认 (before_15=True)
    - 工作日15点后或周末提交 → 按下一个工作日净值确认 (before_15=False)

    参数:
        history: 历史净值列表（按日期升序）
        target_date: 交易日期（"YYYY-MM-DD"）
        before_15: 是否为15点前提交（默认True，即按当天净值）

    返回值:
        dict: {"nav": float, "nav_source": str} 或 None
        nav_source 取值:
        - "exact": 精确匹配到目标日期（before_15=True 且精确匹配）
        - "next_trading_day": 使用下一交易日净值（before_15=False 或周末/节假日）
    """
    if before_15:
        # 15点前:先尝试精确匹配
        for record in reversed(history):
            if record.get("date", "") == target_date:
                return {"nav": record.get("nav", 0), "nav_source": "exact"}

    # 15点后或精确匹配失败:找最近的后一个交易日
    for record in history:
        if record.get("date", "") > target_date:
            nav_val = record.get("nav", 0)
            log(f"  注意:{target_date} 非交易日或15点后提交，使用下一交易日 {record.get('date', '')} 的净值: {nav_val}")
            return {"nav": nav_val, "nav_source": "next_trading_day"}

    return None

def setup_data_files(funds=None):
    """设置数据文件：如果 purchase_records.json 不存在则创建模板，返回文件是否已存在"""
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
                if "type" in rec and not (isinstance(rec["type"], str) and rec["type"].lower() in ("buy", "sell")):
                    all_errors.append(f"{prefix}: 'type' 必须是 'buy' 或 'sell'（不区分大小写）")
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
            setup_data_files(None)
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
    参数:
        purchases: 该基金的全部交易记录列表（已按平台区分，不含其他平台同名基金）
        current_nav: 当前净值
        history: 历史净值列表
        fund_code: 基金代码（用于日志输出）
    """
    if not purchases:
        return {
            "total_invested": 0,
            "total_shares": 0,
            "current_value": 0,
            "profit_loss": 0,
            "profit_loss_percent": 0,
            "purchases": [],
            "realized_profit_loss": 0
        }

    # FIFO队列:{date, amount, shares, nav, remaining_shares}
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
            # 卖出记录:按FIFO法从最早买入抵扣
            # 优先使用 shares 字段（实际卖出份额），否则从 amount / nav 计算
            raw_shares = purchase.get("shares")
            if raw_shares is not None and raw_shares > 0:
                sell_shares = raw_shares
            else:
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
                "nav_source": nav_source  # 净值来源:exact / next_trading_day
            })

        else:
            # 买入记录:加入FIFO队列
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
                "nav_source": nav_source  # 净值来源:exact / next_trading_day
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
        "realized_profit_loss": round(realized_profit_loss, 2),  # 新增:已实现盈亏
        "avg_cost_nav": round(avg_cost_nav, 4)  # 新增:平均持仓成本
    }

def calculate_cumulative_returns(history, original_purchases=None, history_for_nav=None):
    """为历史数据中每一天预计算累计收益率，使用与 calculate_holdings() 一致的 FIFO 逻辑。
    优化:增量维护 FIFO 队列，时间复杂度从 O(H×P) 降至 O(H+P)。
    """
    if not history:
        return []

    sorted_history = sorted(history, key=lambda h: h["date"])
    n = len(sorted_history)
    return_rates = [None] * n

    # 仅支持精确 FIFO 模拟（必须提供原始交易记录）
    if not original_purchases or not history_for_nav:
        return return_rates

    sorted_purchases = sorted(original_purchases, key=lambda p: p["date"])
    buy_queue = []
    purchase_idx = 0
    num_purchases = len(sorted_purchases)
    queue_shares = 0.0
    queue_cost = 0.0

    for h_idx, h in enumerate(sorted_history):
        while purchase_idx < num_purchases and sorted_purchases[purchase_idx]["date"] <= h["date"]:
            p = sorted_purchases[purchase_idx]
            purchase_idx += 1

            trans_type = p.get("type", "buy")
            before_15 = p.get("before_15", True)
            # 使用 history（完整历史数据）作为净值查找源，避免 history_for_nav 数据窗口不足导致 NAV 错位
            nav_result = get_nav_from_history(history, p["date"], before_15)
            if not nav_result or nav_result["nav"] <= 0:
                continue
            nav_on_date = nav_result["nav"]

            if trans_type == "sell":
                # 与 calculate_holdings() 保持一致：优先使用 shares 字段（实际卖出份额），否则从 amount / nav 计算
                raw_shares = p.get("shares")
                if raw_shares is not None and raw_shares > 0:
                    sell_shares = raw_shares
                else:
                    sell_shares = p["amount"] / nav_on_date
                remaining_sell = sell_shares
                deducted_cost = 0.0
                for buy in buy_queue:
                    if remaining_sell <= 0:
                        break
                    if buy["remaining_shares"] <= 0:
                        continue
                    deduct = min(remaining_sell, buy["remaining_shares"])
                    deducted_cost += deduct * buy["nav"]
                    buy["remaining_shares"] -= deduct
                    remaining_sell -= deduct
                queue_shares -= (sell_shares - remaining_sell)
                queue_cost -= deducted_cost
            else:
                shares = p["amount"] / nav_on_date
                buy_queue.append({"date": p["date"], "nav": nav_on_date, "remaining_shares": shares})
                queue_shares += shares
                queue_cost += shares * nav_on_date

        if queue_cost > 0 and queue_shares > 0:
            value = h["nav"] * queue_shares
            profit = value - queue_cost
            return_rates[h_idx] = round((profit / queue_cost) * 100, 2)

    return return_rates


def _fetch_and_merge_history(code, fund_start_date, http_session,
                            history_cache, history_cache_lock):
    """
    获取基金历史数据，与缓存合并，更新内存缓存。
    线程安全:使用 history_cache_lock 保护缓存读写。

    返回: (history_list, error_message_or_None)
    """
    try:
        with history_cache_lock:
            cached_history = list(history_cache.get(code, []))

        if cached_history:
            last_cached_date = cached_history[-1].get("date", "")
            if cached_history[0]["date"] > fund_start_date:
                full_history = fetch_fund_history(code, start_date=fund_start_date, session=http_session)
                history = merge_history(cached_history, full_history)
            else:
                new_history = fetch_fund_history(code, start_date=fund_start_date,
                                                  session=http_session, incremental_from=last_cached_date)
                history = merge_history(cached_history, new_history)
        else:
            history = fetch_fund_history(code, start_date=fund_start_date, session=http_session)

        if not history:
            if cached_history:
                history = cached_history
            else:
                return (None, "历史数据为空，且无缓存")

        # 注意：不再按 fund_start_date 截断历史，后端存储全量历史；
        # 前端 renderFundChart() 负责按"首次买入日 - 7天"过滤展示范围。

        with history_cache_lock:
            history_cache[code] = history

        return (history, None)
    except Exception as e:
        return (None, str(e))


def _apply_nav_correction(code, history, realtime, today, qdii_codes):
    """
    用历史确认净值修正实时估算值，并修正 nav_status。
    直接修改 realtime dict（in-place），无需返回值。
    """
    if history and history[-1].get("date"):
        history_latest_date = history[-1].get("date", "")
        realtime_date = realtime.get("nav_date", "")[:10]
        should_use_history = (
            history_latest_date > realtime_date or
            (history_latest_date == realtime_date and realtime.get("nav_status") == "estimated")
        )
        if should_use_history:
            log(f"  基金 {code}: 历史确认净值({history_latest_date} {history[-1]['nav']})优先于实时估算({realtime_date} {realtime['nav']})")
            realtime["nav"] = history[-1].get("nav", 0)
            realtime["nav_date"] = history_latest_date
            realtime["change_percent"] = history[-1].get("change_percent", 0)

    # 修正 nav_status
    beijing_now = get_beijing_time()
    is_after_15 = 15 <= beijing_now.hour <= 23
    if history:
        latest_nav_date = history[-1].get("date", "")
        if latest_nav_date == today and is_after_15:
            realtime["nav_status"] = "confirmed_today"
        elif code in qdii_codes:
            realtime["nav_status"] = "delayed"
        else:
            realtime["nav_status"] = "confirmed"


def _calculate_latest_trading_day_metrics(history, holdings):
    if not history or len(history) < 2:
        return {"latest_trading_day_nav": 0, "latest_trading_day_nav_date": "",
                "latest_trading_day_return": 0, "latest_trading_day_profit": 0,
                "day_before_latest_trading_day_return": 0,
                "day_before_latest_trading_day_profit": 0}

    # 脚本在每日 20:00 之后运行，此时当日净值已确认并写入 history 最后一条，
    # 直接以最新一条（当日）作为“最新交易日”计算日涨跌/日盈亏。
    # 前端会依据当前北京时间(是否>=20:00)与 navDate 决定标注“已更新至今日”还是“昨日”。
    idx = -1

    latest_nav = history[idx].get("nav", 0)
    latest_date = history[idx].get("date", "")
    prev_nav = history[idx - 1].get("nav", 0) if len(history) >= -idx + 1 else latest_nav

    # day_before uses offset+1
    day_before_idx = idx - 1
    day_before_nav = history[day_before_idx].get("nav", 0) if len(history) >= -day_before_idx else latest_nav
    day_before_prev_nav = history[day_before_idx - 1].get("nav", 0) if len(history) >= -day_before_idx + 1 else latest_nav

    def _calc_return(current, previous):
        return (current - previous) / previous * 100 if previous > 0 else 0

    latest_return = _calc_return(latest_nav, prev_nav)
    day_before_return = _calc_return(day_before_nav, day_before_prev_nav)

    total_shares = holdings.get("total_shares", 0)
    if total_shares > 0:
        latest_profit = round(total_shares * (latest_nav - prev_nav), 2)
        day_before_profit = round(total_shares * (day_before_nav - day_before_prev_nav), 2)
    else:
        latest_profit = 0
        day_before_profit = 0

    return {
        "latest_trading_day_nav": latest_nav,
        "latest_trading_day_nav_date": latest_date,
        "latest_trading_day_return": round(latest_return, 2),
        "latest_trading_day_profit": latest_profit,
        "day_before_latest_trading_day_return": round(day_before_return, 2),
        "day_before_latest_trading_day_profit": day_before_profit,
    }


def _calculate_year_to_date_metrics(history, holdings, today):
    """
    计算本年(YTD)数据：本年收益率、本年盈亏

    以本年第一条历史记录的净值作为年初基准，当前净值为最新值。
    单基金本年收益率 = (current_nav - year_start_nav) / year_start_nav * 100
    单基金本年盈亏  = total_shares * (current_nav - year_start_nav)

    参数:
        history: 历史净值数组（已按日期排序）
        holdings: 持仓数据（含 total_shares）
        today: 当前日期字符串 "YYYY-MM-DD"

    返回:
        dict: {ytd_return, ytd_profit, ytd_start_nav}
    """
    if not history:
        return {"ytd_return": 0, "ytd_profit": 0, "ytd_start_nav": 0}

    current_year = datetime.strptime(today, "%Y-%m-%d").year

    # 找到本年第一条历史记录（若基金年中才买入，则从第一条记录算起）
    year_start_nav = None
    for h in history:
        h_date = h.get("date", "")
        if h_date.startswith(str(current_year)):
            year_start_nav = h.get("nav", 0)
            break

    # 若 history 中无本年数据（理论上不会发生），退到第一条记录
    if year_start_nav is None:
        year_start_nav = history[0].get("nav", 0)

    current_nav = history[-1].get("nav", 0)
    total_shares = holdings.get("total_shares", 0)

    if year_start_nav > 0:
        ytd_return = (current_nav - year_start_nav) / year_start_nav * 100
    else:
        ytd_return = 0

    ytd_profit = total_shares * (current_nav - year_start_nav) if total_shares > 0 else 0

    return {
        "ytd_return": round(ytd_return, 2),
        "ytd_profit": round(ytd_profit, 2),
        "ytd_start_nav": round(year_start_nav, 4)
    }


def process_fund(platform, code, fund_start_date, http_session,
                  purchase_records, qdii_codes, fund_names,
                  prev_fund_map, today, history_cache):
    """
    处理单只基金:获取历史数据、实时数据，计算持仓和收益。
    线程安全:使用 history_cache_lock 保护缓存读写。

    返回: (fund_data_dict, total_invested, total_value, message_or_error) 成功
           (None, 0, 0, error_message) 失败
    """
    try:
        # --- 第1步:获取历史数据（与实时数据解耦） ---
        history, err = _fetch_and_merge_history(
            code, fund_start_date, http_session,
            history_cache, history_cache_lock
        )
        if err:
            return (None, 0, 0, err)

        # --- 第2步:获取实时数据 ---
        realtime = fetch_fund_realtime(code, qdii_codes, fund_names, session=http_session)
        if not realtime:
            if code in prev_fund_map:
                # 用深拷贝避免修改 prev_fund_map 中的原始数据
                old_fund = copy.deepcopy(prev_fund_map[code])
                purchases = purchase_records.get(platform, {}).get(code, [])

                # 确定确认净值（优先用历史数据，兜底用缓存值）
                # history 来自确认净值 API，history[-1] 即最新已确认净值（当日已公布则为今日）
                if history:
                    confirmed_nav = history[-1]["nav"]
                    confirmed_nav_date = history[-1]["date"]
                else:
                    confirmed_nav = old_fund.get("current_nav", 0)
                    confirmed_nav_date = old_fund.get("nav_date", "")

                if history and purchases:
                    # 使用历史中的确认净值（而非缓存的实时估算）重新计算持仓
                    new_holdings = calculate_holdings(purchases, confirmed_nav, history, fund_code=code)
                    old_fund["holdings"] = new_holdings
                    old_fund["current_nav"] = confirmed_nav
                    old_fund["nav_date"] = confirmed_nav_date
                    old_fund["nav_status"] = "confirmed"
                    # 注意：calculate_holdings() 内部已计算 current_value，无需重复计算
                    # 重新计算累计收益率（使用路径2近似计算）
                    # 也传入原始交易记录和完整历史，使用精确FIFO模拟（路径1）
                    old_fund["return_rates"] = calculate_cumulative_returns(
                        history,
                        original_purchases=purchases, history_for_nav=history
                    )
                    # 更新昨日收益指标（使用旧 NAV）
                    m = _calculate_latest_trading_day_metrics(history, new_holdings)
                    old_fund["latest_trading_day_nav"] = round(m["latest_trading_day_nav"], 4)
                    old_fund["latest_trading_day_return"] = round(m["latest_trading_day_return"], 2)
                    old_fund["latest_trading_day_profit"] = m["latest_trading_day_profit"]
                else:
                    # 无历史数据或无交易记录时，仍更新 current_nav 保证字段一致性
                    old_fund["current_nav"] = confirmed_nav
                    old_fund["nav_date"] = confirmed_nav_date
                    old_fund["nav_status"] = "confirmed" if history else old_fund.get("nav_status", "unknown")

                old_fund["data_source"] = "cached"
                return (old_fund, old_fund["holdings"]["total_invested"],
                        old_fund["holdings"]["current_value"], "使用缓存数据(已用新交易记录重新计算)")
            else:
                return (None, 0, 0, "无法获取实时数据且无缓存")

        _apply_nav_correction(code, history, realtime, today, qdii_codes)

        # --- 第3步:计算持仓和收益 ---
        purchases = purchase_records.get(platform, {}).get(code, [])

        # 使用最新确认净值计算持仓收益（history 来自确认净值 API，history[-1] 即最新已确认净值）
        if history:
            confirmed_nav = history[-1]["nav"]
            confirmed_nav_date = history[-1]["date"]
        else:
            confirmed_nav = realtime["nav"]
            confirmed_nav_date = realtime.get("nav_date", "")
        holdings = calculate_holdings(purchases, confirmed_nav, history, fund_code=code)

        cumulative_returns = calculate_cumulative_returns(
            history,
            original_purchases=purchases, history_for_nav=history
        )

        # 计算昨日净值、昨日收益率、昨日收益
        m = _calculate_latest_trading_day_metrics(history, holdings)

        # 计算本年(YTD)数据
        ytd = _calculate_year_to_date_metrics(history, holdings, today)

        # 组织数据
        latest_history_date = history[-1].get("date", "") if history else ""
        fund_data = {
            "code": code,
            "name": realtime["name"],
            "platform": platform,
            "current_nav": confirmed_nav,
            "nav_date": confirmed_nav_date,
            "daily_return": m["latest_trading_day_return"],
            "nav_status": realtime.get("nav_status", "unknown"),
            "data_source": "live",
            "latest_history_date": latest_history_date,
            "latest_trading_day_nav": round(m["latest_trading_day_nav"], 4),
            "latest_trading_day_nav_date": m["latest_trading_day_nav_date"],
            "latest_trading_day_return": round(m["latest_trading_day_return"], 2),
            "latest_trading_day_profit": m["latest_trading_day_profit"],
            "day_before_latest_trading_day_return": round(m["day_before_latest_trading_day_return"], 2),
            "day_before_latest_trading_day_profit": m["day_before_latest_trading_day_profit"],
            "ytd_return": ytd["ytd_return"],
            "ytd_profit": ytd["ytd_profit"],
            "ytd_start_nav": ytd["ytd_start_nav"],
            "holdings": holdings,
            "history": [{"date": h["date"], "nav": h["nav"], "return_rate": cumulative_returns[i]} for i, h in enumerate(history)]
        }

        return (fund_data, holdings["total_invested"], holdings["current_value"], None)

    except Exception as e:
        log("❌ 基金 {} 处理失败: {}".format(code, e))
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
    parser.add_argument('--force-refresh', action='store_true', help='强制刷新模式:忽略历史缓存，全量获取')
    args = parser.parse_args()

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
    records_file_exists = setup_data_files(funds)
    if not records_file_exists:
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
    earliest = None
    for _pltf, _codes in purchase_records.items():
        for _code in _codes:
            _start = _get_fund_earliest_purchase_date(purchase_records, _pltf, _code)
            if _start and (earliest is None or _start < earliest):
                earliest = _start
    history_start_date = earliest if earliest else "2020-01-01"
    log(f"历史数据全局起始日期（兜底）: {history_start_date}")

    # 加载历史数据缓存（增量拉取）
    force_refresh = args.force_refresh or os.environ.get("FORCE_REFRESH", "").lower() == "true"
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

    # 并行处理所有基金（瓶颈3修复:ThreadPoolExecutor）
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
    stale_funds = []   # 使用缓存数据的基金（非失败，但数据可能过期）
    today = get_beijing_time().strftime("%Y-%m-%d")

    # 使用线程池并行处理
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_to_fund = {}
        for platform, code, fund_start_date in fund_tasks:
            future = executor.submit(
                process_fund, platform, code, fund_start_date,
                http_session,  # 传递共享 session，避免每只基金重复创建
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
                    # 问题1修复:缓存数据也要提示用户
                    if error_msg and "使用缓存数据" in error_msg:
                        stale_funds.append(code)
                        log(f"  ⚠ 基金 {code} 使用缓存数据（实时数据获取失败）")
                else:
                    failed_funds.append(code)
                    if error_msg:
                        log(f"  ❌ 基金 {code} 处理失败: {error_msg}")
            except Exception as e:
                failed_funds.append(code)
                log(f"  ❌ 基金 {code} 处理异常: {e}")

    # 统一保存历史缓存（所有基金处理完成后只写一次，避免逐基金写盘）
    try:
        save_history_cache(history_cache)
    except Exception as cache_err:
        log("⚠️ 保存历史缓存失败: {}".format(cache_err), "warning")

    # 计算所有汇总指标（单次遍历，代替原来4次独立遍历）
    log("\n[4/4] 计算总计收益...")
    summary = all_data["summary"]
    summary["total_profit_loss"] = round(summary["total_value"] - summary["total_invested"], 2)
    if summary["total_invested"] > 0:
        summary["total_profit_loss_percent"] = round(summary["total_profit_loss"] / summary["total_invested"] * 100, 2)

    # 汇总计算（用 sum() 替代简单累加）
    _latest_profit = sum(
        f.get("latest_trading_day_profit", 0)
        for fs in all_data["funds"].values() for f in fs
    )
    _day_before_profit = sum(
        f.get("day_before_latest_trading_day_profit", 0)
        for fs in all_data["funds"].values() for f in fs
    )
    _ytd_profit = sum(
        f.get("ytd_profit", 0)
        for fs in all_data["funds"].values() for f in fs
    )
    _realized = sum(
        f.get("holdings", {}).get("realized_profit_loss", 0)
        for fs in all_data["funds"].values() for f in fs
    )

    # 加权平均需要逐基金计算，保留循环
    _return_weighted = 0
    _return_day_before_weighted = 0
    _total_weight = 0
    _ytd_return_weighted = 0
    _ytd_weight = 0
    for platform_funds in all_data["funds"].values():
        for fund in platform_funds:
            weight = fund.get("holdings", {}).get("current_value", 0)
            if weight > 0:
                _return_weighted += fund.get("latest_trading_day_return", 0) * weight
                _return_day_before_weighted += fund.get("day_before_latest_trading_day_return", 0) * weight
                _ytd_return_weighted += fund.get("ytd_return", 0) * weight
                _ytd_weight += weight
            _total_weight += weight

    summary["latest_trading_day_profit_loss"] = round(_latest_profit, 2)
    summary["latest_trading_day_profit_loss_percent"] = round((_latest_profit / summary["total_value"] * 100), 2) if summary["total_value"] > 0 else 0
    summary["latest_trading_day_profit_loss_diff"] = round(_latest_profit - _day_before_profit, 2)

    if _total_weight > 0:
        diff = (_return_weighted - _return_day_before_weighted) / _total_weight
        summary["latest_trading_day_profit_loss_percent_diff"] = round(diff, 2)
    else:
        summary["latest_trading_day_profit_loss_percent_diff"] = 0

    # 兼容字段别名（前端使用 yesterday_xxx）
    for alias in ("yesterday_profit_loss", "yesterday_profit_loss_percent",
                  "yesterday_profit_loss_diff", "yesterday_profit_loss_percent_diff"):
        summary[alias] = summary[alias.replace("yesterday_", "latest_trading_day_")]

    summary["ytd_profit_loss"] = round(_ytd_profit, 2)
    summary["ytd_profit_loss_percent"] = round(_ytd_return_weighted / _ytd_weight, 2) if _ytd_weight > 0 else 0
    summary["total_realized_profit_loss"] = round(_realized, 2)
    
    # 打印汇总信息
    log("\n" + "="*60)
    log("✓ 数据抓取完成！")
    log(f"  总投入: ¥{summary['total_invested']:.2f}")
    log(f"  当前市值: ¥{summary['total_value']:.2f}")
    profit_sign = "+" if summary["total_profit_loss"] >= 0 else ""
    log(f"  未实现盈亏: {profit_sign}¥{summary['total_profit_loss']:.2f} ({profit_sign}{summary['total_profit_loss_percent']:.2f}%)")
    yesterday_sign = "+" if summary["latest_trading_day_profit_loss"] >= 0 else ""
    log(f"  昨日盈亏: {yesterday_sign}¥{summary['latest_trading_day_profit_loss']:.2f} ({yesterday_sign}{summary['latest_trading_day_profit_loss_percent']:.2f}%)")
    realized_sign = "+" if summary["total_realized_profit_loss"] >= 0 else ""
    log(f"  已实现盈亏: {realized_sign}¥{summary['total_realized_profit_loss']:.2f}")
    total_profit_value = summary["total_profit_loss"] + summary["total_realized_profit_loss"]
    total_profit_sign = "+" if total_profit_value >= 0 else ""
    total_profit_percent = round(total_profit_value / summary["total_invested"] * 100, 2) if summary["total_invested"] > 0 else 0
    total_profit_psign = "+" if total_profit_percent >= 0 else ""
    log(f"  总盈亏: {total_profit_sign}¥{total_profit_value:.2f} ({total_profit_psign}{total_profit_percent:.2f}%)")
    if stale_funds:
        log(f"\n  [Warning] 以下基金使用缓存数据（非实时）: {', '.join(stale_funds)}")
    if failed_funds:
        log(f"\n  [ERROR] 以下基金处理失败: {', '.join(failed_funds)}")
    log("="*60)

    # 保存数据
    output_file = os.path.join(BASE_DIR, "data", "funds_data.json")
    try:
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(all_data, f, ensure_ascii=False, indent=2)
        log(f"\n✓ 数据已保存到 {output_file}")
        log(f"  更新时间: {all_data['update_time']}")
    except Exception as e:
        log(f"[ERROR] 保存数据到 {output_file} 失败: {e}")

    # 历史缓存已在 main() 末尾统一保存（第1115-1119行），此处无需重复

    # 生成持仓快照
    log("\n生成持仓快照...")
    try:
        from generate_holdings import generate_holdings_snapshot
        generate_holdings_snapshot()
    except Exception as e:
        log("[Warning] 持仓快照生成失败: {}".format(e))

    # 更新基准指数数据（东财 K线 API）
    log("\n更新基准指数数据...")
    try:
        update_benchmark_index_data()
    except Exception as e:
        log("[Warning] 基准指数数据更新失败: {}".format(e))


def update_benchmark_index_data():
    """从东方财富 API 更新基准指数数据（科创50 sh000688, 纳斯达克100 us.NDX）"""
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params_template = {
        "fields1": "f1,f2,f3",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "klt": "101",
        "fqt": "1",
        "end": "20500101",
        "lmt": "2000",
    }
    existing = {}
    if os.path.exists(INDEX_CACHE_FILE):
        try:
            with open(INDEX_CACHE_FILE, "r", encoding="utf-8") as f:
                existing = json.load(f)
            log(f"✓ 已加载旧基准指数数据: {list(existing.keys())}")
        except Exception as e:
            log(f"⚠️ 加载旧基准指数数据失败: {e}", "warning")
            existing = {}
    session = _create_session()
    updated = False
    for code, info in BENCHMARK_INDICES.items():
        try:
            params = dict(params_template, secid=info["secid"])
            resp = session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            result = resp.json()
            klines = result.get("data", {}).get("klines", [])
            if not klines:
                log(f"  ⚠ {code} ({info['name']}) 未获取到数据")
                continue
            new_data = {}
            for kline in klines:
                parts = kline.split(",")
                if len(parts) >= 3:
                    new_data[parts[0]] = float(parts[2])
            if not new_data:
                log(f"  ⚠ {code} ({info['name']}) 解析数据为空")
                continue
            old_data = existing.get(code, {}).get("data", {})
            old_data.update(new_data)
            existing[code] = {"name": info["name"], "data": old_data}
            log(f"  ✓ {code} ({info['name']}): {len(klines)} 条K线, 最新 {list(new_data.keys())[-1]} 收盘 {list(new_data.values())[-1]}")
            updated = True
        except Exception as e:
            log(f"  ⚠ {code} ({info['name']}) 获取失败: {e}", "warning")
    if updated:
        _atomic_write_json(INDEX_CACHE_FILE, existing)
        total_entries = sum(len(v.get("data", {})) for v in existing.values())
        log(f"✓ 基准指数数据已保存: {len(existing)} 个指数, {total_entries} 条记录")
    else:
        log("ℹ 基准指数数据无更新")


def fetch_fund_info_from_web(code, session=None):
    """
    从天天基金网获取基金基本信息（基金名称、类型）
    使用搜索 API，不受交易时段限制
    返回: {"name": "基金名称", "is_qdii": True/False, "benchmark": "..."} 或 None
    """
    url = "https://searchapi.eastmoney.com/api/suggest/get?input={}&type=14&count=1".format(code)

    try:
        if session is None:
            session = _create_session()

        resp = session.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("QuotationCodeTable", {}).get("Data", [])
            if items and items[0].get("Code", "") == code:
                name = items[0].get("Name", "")

                # 启发式判断是否为 QDII
                qdii_keywords = ["纳斯达克", "标普", "美股", "QDII", "海外", "国际"]
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

    # 兼容两种格式:统一归一化为 {"funds": {"平台": [...]}}
    if "funds" not in config:
        # 旧格式:{"支付宝": [...], ...} → 转换为新格式
        log("[WARN] fund_config.json 是旧格式（无 'funds' 键），正在转换...", "warning")
        config = {"funds": {k: v for k, v in config.items() if isinstance(v, list)}}

    existing_codes = set()
    for platform, fund_list in config.get("funds", {}).items():
        if not isinstance(fund_list, list):
            continue
        for fund in fund_list:
            code = fund.get("code") if isinstance(fund, dict) else None
            if not code:
                log(f"[WARN] fund_config.json 平台 '{platform}' 存在缺 code 的基金条目，已跳过", "warning")
                continue
            existing_codes.add(code)

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
