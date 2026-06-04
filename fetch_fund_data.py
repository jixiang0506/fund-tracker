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

import sys
import io
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
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
# 线程锁:保护 history_cache 的读写（ThreadPoolExecutor 并行访问）
history_cache_lock = threading.Lock()
# 时区支持:优先使用 zoneinfo (Python 3.9+)，回退到 pytz
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

def safe_float(val, default=0.0):
    """
    安全转换为 float，处理 '--'、空值、None 等非数字字符串。
    API 返回 '--' 时 float() 会抛 ValueError，此函数统一兜底。
    """
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

# 导入日志模块
try:
    from logger_config import log, setup_encoding
    setup_encoding()
except ImportError:
    # 如果 logger_config 不存在，使用简单的 log 函数和编码设置
    def log(message, level='info'):
        print(message)
    def setup_encoding():
        if hasattr(sys.stdout, "buffer"):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "buffer"):
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    setup_encoding()

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
            log(f"[DEBUG] 平台: {platform}, 基金数: {len(fund_list)}", "info")
            funds_dict[platform] = [f["code"] for f in fund_list]
            for fund in fund_list:
                if fund.get("is_qdii", False):
                    qdii_codes.add(fund["code"])
                if fund.get("name"):
                    fund_names[fund["code"]] = fund["name"]

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
    if not os.path.exists(HISTORY_CACHE_FILE):
        return {}
    try:
        with open(HISTORY_CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        # 去掉 _meta 等元数据键
        return {k: v for k, v in cache.items() if not k.startswith("_")}
    except Exception as e:
        log(f"\u26a0\ufe0f 加载历史缓存失败，将全量获取: {e}", "warning")
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
    log(f"\u2713 历史缓存已保存: {len(cache_data)} 只基金, {total_entries} 条记录")


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

            # 解析JSONP响应:找最外层括号 () 包裹的 JSON，避免匹配字符串值中的 {}
            text = response.text
            json_start = text.find('(') + 1
            json_end = text.rfind(')')
            if json_start <= 0 or json_end <= json_start:
                log(f"  \u26a0 基金 {fund_code} 实时估值JSONP格式异常，尝试回退到历史数据API...")
                return _fallback()
            json_str = text[json_start:json_end].strip()

            # 空JSON（如 jsonpgz(); 的情况）- 回退到历史数据
            if not json_str.strip():
                log(f"  \u26a0 基金 {fund_code} 实时估值API返回空数据，尝试回退到历史数据API...")
                return _fallback()

            data = json.loads(json_str)

            # 检查返回数据是否有效（有些基金不在交易时段会返回空内容）
            if not data.get("name") and not data.get("gsz"):
                log(f"  \u26a0 基金 {fund_code} 实时估值无数据，尝试回退到历史数据API...")
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
            log(f"  \u26a0 基金 {fund_code} 实时估值JSON解析失败，尝试回退到历史数据API...")
            return _fallback()
        except Exception as e:
            if attempt < max_retries:
                log(f"  \u26a0 获取基金 {fund_code} 实时数据失败 (第{attempt}次), {max_retries - attempt}次重试机会剩余: {e}")
                time.sleep(2)
            else:
                log(f"\u274c 获取基金 {fund_code} 实时数据失败 (已重试{max_retries}次): {e}")
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

        data_obj = result.get("Data") or {}
        items = data_obj.get("LSJZList", []) if isinstance(data_obj, dict) else []
        if not items:
            log(f"  \u274c 基金 {fund_code} 历史数据也为空")
            raise RuntimeError(f"基金 {fund_code} 历史数据为空")

        latest = items[0]
        nav = safe_float(latest.get("DWJZ", 0))
        nav_date = latest.get("FSRQ", "")
        change_percent = safe_float(latest.get("JZZZL", 0)) if latest.get("JZZZL") else 0

        # 净值状态:由 main() 根据历史数据最新日期统一修正
        # 此处先设为 confirmed，main() 会覆盖为 confirmed_today / delayed / confirmed
        nav_status = "confirmed"

        # 从配置文件获取基金名称（回退方案）
        fund_name = _get_fund_name(fund_code, fund_names)

        log(f"  \u2713 基金 {fund_code} 回退成功: 净值 {nav} ({nav_date}), 涨跌 {change_percent}%")
        return {
            "code": fund_code,
            "name": fund_name,
            "nav": nav,
            "nav_date": nav_date,
            "change_percent": change_percent,
            "nav_status": nav_status,
        }
    except Exception as e:
        log(f"  \u274c 基金 {fund_code} 历史数据回退也失败: {e}")
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
    """从持仓记录中找出最早的交易日期，往前推7天作为历史数据起始日期。
    内部调用 _get_fund_earliest_purchase_date，避免逻辑重复。
    """
    earliest = None
    for platform, funds in nested_records.items():
        for fund_code in funds:
            fund_start = _get_fund_earliest_purchase_date(nested_records, platform, fund_code)
            if fund_start and (earliest is None or fund_start < earliest):
                earliest = fund_start
    return earliest if earliest else "2020-01-01"

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

        log(f"  \u2713 成功获取 {len(all_history)} 条历史记录")
        return all_history
    except Exception as e:
        log(f"\u274c 获取基金 {fund_code} 历史数据失败: {e}")
        return []

def get_nav_from_history(history, target_date, before_15=True):
    """从历史数据中查找指定日期的净值。

    基金交易规则:
    - 工作日15点前提交 \u2192 按当天净值确认 (before_15=True)
    - 工作日15点后或周末提交 \u2192 按下一个工作日净值确认 (before_15=False)

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
            "\u793a\u4f8b\u5e73\u53f0": {
                "000000": [
                    {"date": "2024-01-15", "amount": 1000.00}
                ]
            }
        }

    template_path = os.path.join(data_dir, "purchase_records.json")
    if not os.path.exists(template_path):
        with open(template_path, "w", encoding="utf-8") as f:
            json.dump(template_records, f, ensure_ascii=False, indent=2)
        log(f"\u2713 \u5df2\u521b\u5efa\u6a21\u677f\u6301\u4ed3\u8bb0\u5f55\u6587\u4ef6: {template_path}")
        log("  \u8bf7\u7f16\u8f91\u6b64\u6587\u4ef6\uff0c\u586b\u5165\u4f60\u7684\u5b9e\u9645\u4e70\u5165\u8bb0\u5f55")
        log("  \u5356\u51fa\u8bb0\u5f55\u683c\u5f0f: {\"date\": \"2024-06-15\", \"amount\": 500.00, \"type\": \"sell\"}")
        return False

    return True

def validate_purchase_records(records):
    """
    \u6821\u9a8c\u4ea4\u6613\u8bb0\u5f55 schema\uff0c\u63d0\u524d\u62e6\u622a\u810f\u6570\u636e
    \u7ed3\u6784: {\u5e73\u53f0: {\u57fa\u91d1\u4ee3\u7801: [\u4ea4\u6613\u8bb0\u5f55]}}
    \u8fd4\u56de: (is_valid, validated_records, errors)
    """
    if not isinstance(records, dict):
        return False, None, ["\u9876\u5c42\u7ed3\u6784\u5fc5\u987b\u662f\u5bf9\u8c61\uff08\u5e73\u53f0\u2192\u57fa\u91d1\u4ee3\u7801\u2192\u4ea4\u6613\u5217\u8868\uff09"]

    validated = {}
    all_errors = []

    for platform, funds in records.items():
        if not isinstance(funds, dict):
            all_errors.append(f"\u5e73\u53f0 '{platform}' \u7684\u503c\u5fc5\u987b\u662f\u5bf9\u8c61\uff08\u57fa\u91d1\u4ee3\u7801\u2192\u4ea4\u6613\u5217\u8868\uff09")
            continue

        validated[platform] = {}
        for fund_code, trans_list in funds.items():
            if not isinstance(trans_list, list):
                all_errors.append(f"\u5e73\u53f0 '{platform}' \u57fa\u91d1 '{fund_code}' \u7684\u4ea4\u6613\u8bb0\u5f55\u5fc5\u987b\u662f\u5217\u8868")
                continue

            validated[platform][fund_code] = []
            for i, rec in enumerate(trans_list):
                prefix = f"{platform}.{fund_code}[{i}]"
                if not isinstance(rec, dict):
                    all_errors.append(f"{prefix}: \u5fc5\u987b\u662f\u5bf9\u8c61")
                    continue

                # \u6821\u9a8c date
                if "date" not in rec:
                    all_errors.append(f"{prefix}: \u7f3a\u5c11 'date' \u5b57\u6bb5")
                    continue
                date_val = rec["date"]
                if not isinstance(date_val, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", date_val):
                    all_errors.append(f"{prefix}: 'date' \u683c\u5f0f\u9519\u8bef\uff0c\u5e94\u4e3a YYYY-MM-DD")
                    continue

                # \u6821\u9a8c amount
                if "amount" not in rec:
                    all_errors.append(f"{prefix}: \u7f3a\u5c11 'amount' \u5b57\u6bb5")
                    continue
                amount_val = rec["amount"]
                if not isinstance(amount_val, (int, float)) or amount_val <= 0:
                    all_errors.append(f"{prefix}: 'amount' \u5fc5\u987b\u662f\u6b63\u6570")
                    continue

                # \u6821\u9a8c type\uff08\u53ef\u9009\uff09
                if "type" in rec and not (isinstance(rec["type"], str) and rec["type"].lower() in ("buy", "sell")):
                    all_errors.append(f"{prefix}: 'type' \u5fc5\u987b\u662f 'buy' \u6216 'sell'\uff08\u4e0d\u533a\u5206\u5927\u5c0f\u5199\uff09")
                    continue

                validated[platform][fund_code].append(rec)

    return len(all_errors) == 0, validated, all_errors


def load_purchase_records():
    """\u52a0\u8f7d\u6301\u4ed3\u8bb0\u5f55\uff08\u4fdd\u7559\u5e73\u53f0\u5206\u5c42\u7ed3\u6784\uff0c\u4e0d\u505a\u6241\u5e73\u5316\uff09"""
    try:
        records_file = os.path.join(BASE_DIR, "data", "purchase_records.json")

        # \u5982\u679c\u6587\u4ef6\u4e0d\u5b58\u5728\uff0c\u521b\u5efa\u6a21\u677f
        if not os.path.exists(records_file):
            log("\u672a\u627e\u5230\u6301\u4ed3\u8bb0\u5f55\u6587\u4ef6\uff0c\u6b63\u5728\u521b\u5efa\u7a7a\u6a21\u677f...")
            create_template_files(None)
            return None

        with open(records_file, "r", encoding="utf-8") as f:
            raw_records = json.load(f)

        # \u6821\u9a8c\u4ea4\u6613\u8bb0\u5f55\u683c\u5f0f
        is_valid, validated, errors = validate_purchase_records(raw_records)
        if not is_valid:
            log("[ERROR] \u4ea4\u6613\u8bb0\u5f55\u683c\u5f0f\u9519\u8bef:")
            for err in errors:
                log("  - {}".format(err))
            return None

        # \u7edf\u8ba1\u603b\u57fa\u91d1\u6570
        fund_count = sum(len(funds) for funds in validated.values())
        log(f"\u2713 \u6210\u529f\u52a0\u8f7d\u6301\u4ed3\u8bb0\u5f55: {fund_count} \u53ea\u57fa\u91d1\uff08\u4fdd\u7559\u5e73\u53f0\u4fe1\u606f\uff09")
        return validated
    except Exception as e:
        log(f"\u274c \u52a0\u8f7d\u6301\u4ed3\u8bb0\u5f55\u5931\u8d25: {e}")
        return None

def calculate_holdings(purchases, current_nav, history, fund_code=""):
    """\u8ba1\u7b97\u6301\u4ed3\u4fe1\u606f\u548c\u5b9e\u9645\u6536\u76ca\uff08\u652f\u6301\u4e70\u5165\u548c\u5356\u51fa\u8bb0\u5f55\uff0cFIFO\u6cd5\u81ea\u52a8\u62b5\u6263\uff09
    \u53c2\u6570:
        purchases: \u8be5\u57fa\u91d1\u7684\u5168\u90e8\u4ea4\u6613\u8bb0\u5f55\u5217\u8868\uff08\u5df2\u6309\u5e73\u53f0\u533a\u5206\uff0c\u4e0d\u542b\u5176\u4ed6\u5e73\u53f0\u540c\u540d\u57fa\u91d1\uff09
        current_nav: \u5f53\u524d\u51c0\u503c
        history: \u5386\u53f2\u51c0\u503c\u5217\u8868
        fund_code: \u57fa\u91d1\u4ee3\u7801\uff08\u7528\u4e8e\u65e5\u5fd7\u8f93\u51fa\uff09
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

    # FIFO\u961f\u5217:{date, amount, shares, nav, remaining_shares}
    buy_queue = []
    realized_profit_loss = 0  # \u5df2\u5b9e\u73b0\u76c8\u4e8f\uff08\u5356\u51fa\u65f6\u786e\u8ba4\uff09
    purchase_details = []

    # \u6309\u65e5\u671f\u6392\u5e8f\uff08\u786e\u4fddFIFO\u987a\u5e8f\uff09
    sorted_purchases = sorted(purchases, key=lambda x: x["date"])

    for purchase in sorted_purchases:
        date = purchase["date"]
        amount = purchase["amount"]
        trans_type = purchase.get("type", "buy")

        # \u4ece\u5386\u53f2\u6570\u636e\u4e2d\u67e5\u627e\u4ea4\u6613\u65e5\u7684\u51c0\u503c
        # before_15: \u662f\u5426\u4e3a15\u70b9\u524d\u63d0\u4ea4\uff08\u9ed8\u8ba4True\uff0c\u5373\u6309\u5f53\u5929\u51c0\u503c\u786e\u8ba4\uff09
        before_15 = purchase.get("before_15", True)
        nav_result = get_nav_from_history(history, date, before_15)

        if not nav_result or nav_result["nav"] <= 0:
            log(f"  \u26a0 \u65e0\u6cd5\u83b7\u53d6 {date} \u7684\u51c0\u503c\uff0c\u8df3\u8fc7\u6b64\u7b14\u8bb0\u5f55")
            continue

        nav_on_date = nav_result["nav"]
        nav_source = nav_result.get("nav_source", "exact")

        if trans_type == "sell":
            # \u5356\u51fa\u8bb0\u5f55:\u6309FIFO\u6cd5\u4ece\u6700\u65e9\u4e70\u5165\u62b5\u6263
            sell_shares = amount / nav_on_date
            remaining_sell_shares = sell_shares
            sell_realized_profit = 0  # \u672c\u7b14\u5356\u51fa\u5b9e\u73b0\u7684\u76c8\u4e8f
            total_fifo_cost = 0  # \u672c\u7b14\u5356\u51fa\u7684FIFO\u603b\u6210\u672c

            log(f"  \u5356\u51fa\u8bb0\u5f55: {date}, \u91d1\u989d \u00a5{amount}, \u51c0\u503c {nav_on_date:.4f} (\u6765\u6e90: {nav_source}), \u4efd\u989d {sell_shares:.2f}")

            # FIFO\u62b5\u6263
            for buy in buy_queue:
                if remaining_sell_shares <= 0:
                    break

                if buy["remaining_shares"] <= 0:
                    continue

                # \u672c\u6b21\u62b5\u6263\u7684\u4efd\u989d
                deduct_shares = min(remaining_sell_shares, buy["remaining_shares"])
                deduct_amount = deduct_shares * buy["nav"]  # \u6210\u672c\u91d1\u989d
                total_fifo_cost += deduct_amount  # \u7d2f\u52a0FIFO\u6210\u672c
                sell_value = deduct_shares * nav_on_date  # \u5356\u51fa\u91d1\u989d
                profit = sell_value - deduct_amount

                # \u66f4\u65b0
                buy["remaining_shares"] -= deduct_shares
                remaining_sell_shares -= deduct_shares
                realized_profit_loss += profit
                sell_realized_profit += profit

                log(f"    FIFO\u62b5\u6263: \u4ece {buy['date']} \u4e70\u5165\u8bb0\u5f55\u62b5\u6263 {deduct_shares:.2f} \u4efd, \u6210\u672c \u00a5{deduct_amount:.2f}, \u5356\u51fa \u00a5{sell_value:.2f}, \u76c8\u4e8f \u00a5{profit:.2f}")

            # \u68c0\u67e5\u662f\u5426\u8d85\u5356\uff08\u5269\u4f59\u672a\u62b5\u6263\u4efd\u989d > 0\uff09
            if remaining_sell_shares > 0.0001:
                actual_sell_shares = sell_shares - remaining_sell_shares
                actual_amount = actual_sell_shares * nav_on_date
                log(f"  \u26a0 \u8b66\u544a: \u57fa\u91d1 {fund_code} \u5356\u51fa\u4efd\u989d\u8d85\u8fc7\u6301\u4ed3\uff01\u5c1d\u8bd5\u5356\u51fa {sell_shares:.2f} \u4efd\uff0c\u5b9e\u9645\u53ef\u5356\u51fa {actual_sell_shares:.2f} \u4efd")
                # \u4fee\u6b63\u4e3a\u5b9e\u9645\u53ef\u5356\u51fa\u7684\u4efd\u989d\u548c\u91d1\u989d
                sell_shares = actual_sell_shares
                amount = actual_amount

            # \u8bb0\u5f55\u5356\u51fa\u8be6\u60c5\uff08\u4f7f\u7528\u672c\u7b14\u5356\u51fa\u7684\u5df2\u5b9e\u73b0\u76c8\u4e8f\uff09
            purchase_details.append({
                "date": date,
                "amount": -round(amount, 2),
                "nav": round(nav_on_date, 4),
                "shares": -round(sell_shares, 2),
                "type": "sell",
                "realized_profit": round(sell_realized_profit, 2),  # \u672c\u7b14\u5356\u51fa\u7684\u76c8\u4e8f
                "fifo_cost": round(total_fifo_cost, 2),  # \u672c\u7b14\u5356\u51fa\u7684FIFO\u603b\u6210\u672c
                "nav_source": nav_source  # \u51c0\u503c\u6765\u6e90:exact / next_trading_day
            })

        else:
            # \u4e70\u5165\u8bb0\u5f55:\u52a0\u5165FIFO\u961f\u5217
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
                "nav_source": nav_source  # \u51c0\u503c\u6765\u6e90:exact / next_trading_day
            })

            log(f"  \u4e70\u5165\u8bb0\u5f55: {date}, \u91d1\u989d \u00a5{amount}, \u51c0\u503c {nav_on_date:.4f}, \u4efd\u989d {shares:.2f}")

    # \u8ba1\u7b97\u5269\u4f59\u6301\u4ed3
    remaining_shares = sum(b["remaining_shares"] for b in buy_queue)
    remaining_cost = sum(b["remaining_shares"] * b["nav"] for b in buy_queue)

    # \u8ba1\u7b97\u5e73\u5747\u6301\u4ed3\u6210\u672c
    avg_cost_nav = remaining_cost / remaining_shares if remaining_shares > 0 else 0

    # \u786e\u4fdd\u4efd\u989d\u548c\u6295\u5165\u4e0d\u4e3a\u8d1f\u6570
    remaining_shares = max(0, remaining_shares)
    remaining_cost = max(0, remaining_cost)

    # \u8ba1\u7b97\u5f53\u524d\u5e02\u503c\u548c\u6536\u76ca
    current_value = remaining_shares * current_nav if current_nav > 0 else 0
    unrealized_profit = current_value - remaining_cost
    profit_loss_percent = (unrealized_profit / remaining_cost * 100) if remaining_cost > 0 else 0

    log(f"  \u6301\u4ed3\u6c47\u603b: \u5269\u4f59\u4efd\u989d {remaining_shares:.2f}, \u5269\u4f59\u6210\u672c \u00a5{remaining_cost:.2f}, \u5df2\u5b9e\u73b0\u76c8\u4e8f \u00a5{realized_profit_loss:.2f}, \u5e73\u5747\u6210\u672c {avg_cost_nav:.4f}")

    return {
        "total_invested": round(remaining_cost, 2),
        "total_shares": round(remaining_shares, 2),
        "current_value": round(current_value, 2),
        "profit_loss": round(unrealized_profit, 2),
        "profit_loss_percent": round(profit_loss_percent, 2),
        "purchases": purchase_details,
        "realized_profit_loss": round(realized_profit_loss, 2),  # \u65b0\u589e:\u5df2\u5b9e\u73b0\u76c8\u4e8f
        "avg_cost_nav": round(avg_cost_nav, 4)  # \u65b0\u589e:\u5e73\u5747\u6301\u4ed3\u6210\u672c
    }

def calculate_cumulative_returns(history, purchases, original_purchases=None, history_for_nav=None):
    """\u4e3a\u5386\u53f2\u6570\u636e\u4e2d\u6bcf\u4e00\u5929\u9884\u8ba1\u7b97\u7d2f\u8ba1\u6536\u76ca\u7387\uff0c\u4f7f\u7528\u4e0e calculate_holdings() \u4e00\u81f4\u7684 FIFO \u903b\u8f91\u3002\n
    \u4f18\u5316:\u589e\u91cf\u7ef4\u62a4 FIFO \u961f\u5217\uff0c\u65f6\u95f4\u590d\u6742\u5ea6\u4ece O(H\u00d7P) \u964d\u81f3 O(H+P)\uff0c
    \u5176\u4e2d H=\u5386\u53f2\u5929\u6570\uff0cP=\u4ea4\u6613\u7b14\u6570\u3002\n
    \u53c2\u6570:
        history: \u5386\u53f2\u51c0\u503c\u5217\u8868\uff08\u6309\u65e5\u671f\u5347\u5e8f\uff09
        purchases: calculate_holdings() \u8fd4\u56de\u7684 purchase_details\uff08\u542b fifo_cost\uff09
        original_purchases: \u539f\u59cb\u4ea4\u6613\u8bb0\u5f55\uff08\u542b before_15 \u5b57\u6bb5\uff09\uff0c\u7528\u4e8e\u7cbe\u786e FIFO \u6a21\u62df
        history_for_nav: \u4e0e original_purchases \u914d\u5408\u7684\u5386\u53f2\u6570\u636e\uff0c\u7528\u4e8e\u67e5\u51c0\u503c
    """
    if not history:
        return []

    # \u786e\u4fdd history \u6309\u65e5\u671f\u5347\u5e8f\u6392\u5217
    sorted_history = sorted(history, key=lambda h: h["date"])
    n = len(sorted_history)
    return_rates = [None] * n

    # \u2500\u2500 \u8def\u5f841:\u7cbe\u786e FIFO \u6a21\u62df\uff08\u63d0\u4f9b\u539f\u59cb\u4ea4\u6613\u8bb0\u5f55\u65f6\uff09\u2500\u2500
    if original_purchases and history_for_nav:
        sorted_purchases = sorted(original_purchases, key=lambda p: p["date"])
        buy_queue = []          # FIFO \u961f\u5217:[{date, nav, remaining_shares}]
        purchase_idx = 0
        num_purchases = len(sorted_purchases)

        # \u6eda\u52a8\u7ef4\u62a4\u6301\u4ed3\u4efd\u989d\u548c\u6210\u672c\uff08\u66ff\u4ee3\u5faa\u73af\u5185\u5168\u91cf sum\uff09
        queue_shares = 0.0
        queue_cost = 0.0

        for h_idx, h in enumerate(sorted_history):
            # \u589e\u91cf\u63a8\u8fdb:\u5904\u7406\u6240\u6709\u5728\u8be5\u5386\u53f2\u65f6\u70b9\u4e4b\u524d\uff08\u542b\u5f53\u65e5\uff09\u7684\u4ea4\u6613
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
                    buy_queue.append({
                        "date": p["date"],
                        "nav": nav_on_date,
                        "remaining_shares": shares
                    })
                    queue_shares += shares
                    queue_cost += shares * nav_on_date

            # \u4f7f\u7528\u6eda\u52a8\u53d8\u91cf\uff0cO(1) \u8ba1\u7b97\uff08\u4e0d\u518d\u5168\u91cf sum\uff09
            if queue_cost > 0 and queue_shares > 0:
                value = h["nav"] * queue_shares
                profit = value - queue_cost
                return_rates[h_idx] = round((profit / queue_cost) * 100, 2)

        return return_rates

    # \u2500\u2500 \u8def\u5f842:\u8fd1\u4f3c\u56de\u9000\uff08\u4ec5\u63d0\u4f9b purchase_details \u65f6\uff09\u2500\u2500
    # \u26a0\ufe0f \u6ce8\u610f\uff1apurchase_details \u7684 shares \u5df2\u88ab FIFO \u62b5\u6263\uff0c\u65e0\u6cd5\u7cbe\u786e\u6a21\u62df\u3002
    # \u6b64\u5904\u4f7f\u7528\u8fd1\u4f3c\u7b97\u6cd5\uff1a
    #   - \u7d2f\u8ba1\u4efd\u989d acc_shares\uff1a\u4e70\u5165+\uff0c\u5356\u51fa-\uff08\u4f7f\u7528 detail.shares\uff09
    #   - \u7d2f\u8ba1\u6295\u5165 acc_invested\uff1a\u4e70\u5165+amount\uff0c\u5356\u51fa-nav_p*sh\uff08\u8fd1\u4f3c\u6210\u672c\uff09
    #   - \u6536\u76ca\u7387 = (nav * acc_shares - acc_invested) / acc_invested
    # \u7cbe\u5ea6\u4f4e\u4e8e\u8def\u5f841\uff08\u65e0\u6cd5\u7cbe\u786e FIFO \u62b5\u6263\uff09\uff0c\u4f46\u6bd4\u5168 None \u66f4\u6709\u610f\u4e49\u3002
    if purchases:
        sorted_p = sorted(purchases, key=lambda p: p["date"])
        p_idx = 0
        num_p = len(sorted_p)
        acc_invested = 0.0   # \u622a\u6b62\u5f53\u5929\u7684\u7d2f\u8ba1\u6295\u5165
        acc_shares = 0.0     # \u622a\u6b62\u5f53\u5929\u7684\u7d2f\u8ba1\u4efd\u989d

        for h_idx, h in enumerate(sorted_history):
            # \u6eda\u52a8\u63a8\u8fdb:\u5904\u7406\u6240\u6709 <= \u5f53\u5929\u7684\u4ea4\u6613\uff0c\u66f4\u65b0\u7d2f\u8ba1\u503c
            while p_idx < num_p and sorted_p[p_idx]["date"] <= h["date"]:
                p = sorted_p[p_idx]
                p_idx += 1
                nav_p = p.get("nav", 0)
                if nav_p <= 0:
                    continue
                amt = p.get("amount", 0)
                sh = p.get("shares", 0)
                if p.get("type") == "sell":
                    # \u5356\u51fa:\u4efd\u989d\u51cf\u5c11\uff0c\u6295\u5165\u6309 nav * sh \u8fd1\u4f3c\u6263\u9664
                    acc_shares -= sh
                    acc_invested -= nav_p * sh
                else:
                    acc_shares += sh
                    acc_invested += amt

            if acc_invested > 0 and acc_shares > 0:
                value = h["nav"] * acc_shares
                profit = value - acc_invested
                return_rates[h_idx] = round((profit / acc_invested) * 100, 2)

    return return_rates


def _fetch_and_merge_history(code, fund_start_date, http_session,
                            history_cache, history_cache_lock):
    """
    \u83b7\u53d6\u57fa\u91d1\u5386\u53f2\u6570\u636e\uff0c\u4e0e\u7f13\u5b58\u5408\u5e76\uff0c\u66f4\u65b0\u5185\u5b58\u7f13\u5b58\u3002
    \u7ebf\u7a0b\u5b89\u5168:\u4f7f\u7528 history_cache_lock \u4fdd\u62a4\u7f13\u5b58\u8bfb\u5199\u3002\n
    \u8fd4\u56de: (history_list, error_message_or_None)
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
                return (None, "\u5386\u53f2\u6570\u636e\u4e3a\u7a7a\uff0c\u4e14\u65e0\u7f13\u5b58")

        with history_cache_lock:
            history_cache[code] = history

        return (history, None)
    except Exception as e:
        return (None, str(e))


def _apply_nav_correction(code, history, realtime, today, qdii_codes):
    """
    \u7528\u5386\u53f2\u786e\u8ba4\u51c0\u503c\u4fee\u6b63\u5b9e\u65f6\u4f30\u7b97\u503c\uff0c\u5e76\u4fee\u6b63 nav_status\u3002
    \u76f4\u63a5\u4fee\u6539 realtime dict\uff08in-place\uff09\uff0c\u65e0\u9700\u8fd4\u56de\u503c\u3002
    """
    if history and history[-1].get("date"):
        history_latest_date = history[-1].get("date", "")
        realtime_date = realtime.get("nav_date", "")[:10]
        should_use_history = (
            history_latest_date > realtime_date or
            (history_latest_date == realtime_date and realtime.get("nav_status") == "estimated")
        )
        if should_use_history:
            log(f"  \u57fa\u91d1 {code}: \u5386\u53f2\u786e\u8ba4\u51c0\u503c({history_latest_date} {history[-1]['nav']})\u4f18\u5148\u4e8e\u5b9e\u65f6\u4f30\u7b97({realtime_date} {realtime['nav']})")
            realtime["nav"] = history[-1].get("nav", 0)
            realtime["nav_date"] = history_latest_date
            realtime["change_percent"] = history[-1].get("change_percent", 0)

    # \u4fee\u6b63 nav_status
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


def _calculate_latest_trading_day_metrics(history, holdings, today):
    """
    \u6839\u636e\u5386\u53f2\u6570\u636e\u8ba1\u7b97\u6628\u65e5\u51c0\u503c\u3001\u6628\u65e5\u6536\u76ca\u7387\u3001\u6628\u65e5\u6536\u76ca\u3001
    \u524d\u65e5\u6536\u76ca\u7387\u3001\u524d\u65e5\u6536\u76ca\u3002\n
    \u8fd4\u56de: dict with keys:
        latest_trading_day_nav, latest_trading_day_nav_date, latest_trading_day_return,
        latest_trading_day_profit, day_before_latest_trading_day_return,
        day_before_latest_trading_day_profit
    """
    result = {
        "latest_trading_day_nav": 0,
        "latest_trading_day_nav_date": "",
        "latest_trading_day_return": 0,
        "latest_trading_day_profit": 0,
        "day_before_latest_trading_day_return": 0,
        "day_before_latest_trading_day_profit": 0,
    }

    if not history or len(history) < 2:
        return result

    if history[-1].get("date", "") == today:
        latest_trading_day_nav = history[-2].get("nav", 0)
        latest_trading_day_nav_date = history[-2].get("date", "")
        prev_nav = history[-3].get("nav", 0) if len(history) >= 3 else latest_trading_day_nav
        if len(history) >= 4:
            day_before_latest_trading_day_nav = history[-3].get("nav", 0)
            prev_trading_day_prev_nav = history[-4].get("nav", 0)
        else:
            day_before_latest_trading_day_nav = latest_trading_day_nav
            prev_trading_day_prev_nav = latest_trading_day_nav
    else:
        latest_trading_day_nav = history[-1].get("nav", 0)
        latest_trading_day_nav_date = history[-1].get("date", "")
        prev_nav = history[-2].get("nav", 0)
        if len(history) >= 3:
            day_before_latest_trading_day_nav = history[-2].get("nav", 0)
            prev_trading_day_prev_nav = history[-3].get("nav", 0)
        else:
            day_before_latest_trading_day_nav = latest_trading_day_nav
            prev_trading_day_prev_nav = latest_trading_day_nav

    if prev_nav > 0:
        latest_trading_day_return = (latest_trading_day_nav - prev_nav) / prev_nav * 100
    else:
        latest_trading_day_return = 0

    if prev_trading_day_prev_nav > 0:
        day_before_latest_trading_day_return = (day_before_latest_trading_day_nav - prev_trading_day_prev_nav) / prev_trading_day_prev_nav * 100
    else:
        day_before_latest_trading_day_return = 0

    total_shares = holdings.get("total_shares", 0)
    if total_shares > 0:
        latest_trading_day_profit = round(total_shares * (latest_trading_day_nav - prev_nav), 2)
        day_before_latest_trading_day_profit = round(total_shares * (day_before_latest_trading_day_nav - prev_trading_day_prev_nav), 2)
    else:
        latest_trading_day_profit = 0
        day_before_latest_trading_day_profit = 0

    result["latest_trading_day_nav"] = latest_trading_day_nav
    result["latest_trading_day_nav_date"] = latest_trading_day_nav_date
    result["latest_trading_day_return"] = latest_trading_day_return
    result["latest_trading_day_profit"] = latest_trading_day_profit
    result["day_before_latest_trading_day_return"] = day_before_latest_trading_day_return
    result["day_before_latest_trading_day_profit"] = day_before_latest_trading_day_profit
    return result


def _calculate_year_to_date_metrics(history, holdings, today):
    """
    \u8ba1\u7b97\u672c\u5e74(YTD)\u6570\u636e\uff1a\u672c\u5e74\u6536\u76ca\u7387\u3001\u672c\u5e74\u76c8\u4e8f\n
    \u4ee5\u672c\u5e74\u7b2c\u4e00\u6761\u5386\u53f2\u8bb0\u5f55\u7684\u51c0\u503c\u4f5c\u4e3a\u5e74\u521d\u57fa\u51c6\uff0c\u5f53\u524d\u51c0\u503c\u4e3a\u6700\u65b0\u503c\u3002
    \u5355\u57fa\u91d1\u672c\u5e74\u6536\u76ca\u7387 = (current_nav - year_start_nav) / year_start_nav * 100
    \u5355\u57fa\u91d1\u672c\u5e74\u76c8\u4e8f  = total_shares * (current_nav - year_start_nav)

    \u53c2\u6570:
        history: \u5386\u53f2\u51c0\u503c\u6570\u7ec4\uff08\u5df2\u6309\u65e5\u671f\u6392\u5e8f\uff09
        holdings: \u6301\u4ed3\u6570\u636e\uff08\u542b total_shares\uff09
        today: \u5f53\u524d\u65e5\u671f\u5b57\u7b26\u4e32 "YYYY-MM-DD"

    \u8fd4\u56de:
        dict: {ytd_return, ytd_profit, ytd_start_nav}
    """
    if not history:
        return {"ytd_return": 0, "ytd_profit": 0, "ytd_start_nav": 0}

    current_year = datetime.strptime(today, "%Y-%m-%d").year

    # \u627e\u5230\u672c\u5e74\u7b2c\u4e00\u6761\u5386\u53f2\u8bb0\u5f55\uff08\u82e5\u57fa\u91d1\u5e74\u4e2d\u624d\u4e70\u5165\uff0c\u5219\u4ece\u7b2c\u4e00\u6761\u8bb0\u5f55\u7b97\u8d77\uff09
    year_start_nav = None
    for h in history:
        h_date = h.get("date", "")
        if h_date.startswith(str(current_year)):
            year_start_nav = h.get("nav", 0)
            break

    # \u82e5 history \u4e2d\u65e0\u672c\u5e74\u6570\u636e\uff08\u7406\u8bba\u4e0a\u4e0d\u4f1a\u53d1\u751f\uff09\uff0c\u9000\u5230\u7b2c\u4e00\u6761\u8bb0\u5f55
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
    \u5904\u7406\u5355\u53ea\u57fa\u91d1:\u83b7\u53d6\u5386\u53f2\u6570\u636e\u3001\u5b9e\u65f6\u6570\u636e\uff0c\u8ba1\u7b97\u6301\u4ed3\u548c\u6536\u76ca\u3002
    \u7ebf\u7a0b\u5b89\u5168:\u4f7f\u7528 history_cache_lock \u4fdd\u62a4\u7f13\u5b58\u8bfb\u5199\u3002\n
    \u8fd4\u56de: (fund_data_dict, total_invested, total_value) \u6210\u529f
          (None, 0, 0, error_message) \u5931\u8d25
    """
    try:
        # --- \u7b2c1\u6b65:\u83b7\u53d6\u5386\u53f2\u6570\u636e\uff08\u4e0e\u5b9e\u65f6\u6570\u636e\u89e3\u8026\uff09 ---
        history, err = _fetch_and_merge_history(
            code, fund_start_date, http_session,
            history_cache, history_cache_lock
        )
        if err:
            return (None, 0, 0, err)

        # --- \u7b2c2\u6b65:\u83b7\u53d6\u5b9e\u65f6\u6570\u636e ---
        realtime = fetch_fund_realtime(code, qdii_codes, fund_names, session=http_session)
        if not realtime:
            if code in prev_fund_map:
                # \u7528\u6df1\u62f7\u8d1d\u907f\u514d\u4fee\u6539 prev_fund_map \u4e2d\u7684\u539f\u59cb\u6570\u636e
                old_fund = copy.deepcopy(prev_fund_map[code])
                old_nav = old_fund.get("current_nav", 0)
                # \u7528\u65b0\u7684 purchase_records \u91cd\u65b0\u8ba1\u7b97 holdings\uff0c\u4fdd\u7559\u65e7 NAV
                purchases = purchase_records.get(platform, {}).get(code, [])
                if history and purchases:
                    new_holdings = calculate_holdings(purchases, old_nav, history, fund_code=code)
                    old_fund["holdings"] = new_holdings
                    old_fund["holdings"]["current_value"] = new_holdings["total_shares"] * old_nav
                    # \u91cd\u65b0\u8ba1\u7b97\u7d2f\u8ba1\u6536\u76ca\u7387\uff08\u4f7f\u7528\u8def\u5f842\u8fd1\u4f3c\u8ba1\u7b97\uff09
                    old_fund["return_rates"] = calculate_cumulative_returns(
                        history, new_holdings["purchases"]
                    )
                    # \u66f4\u65b0\u6628\u65e5\u6536\u76ca\u6307\u6807\uff08\u4f7f\u7528\u65e7 NAV\uff09
                    m = _calculate_latest_trading_day_metrics(history, new_holdings, today)
                    old_fund["latest_trading_day_nav"] = round(m["latest_trading_day_nav"], 4)
                    old_fund["latest_trading_day_return"] = round(m["latest_trading_day_return"], 2)
                    old_fund["latest_trading_day_profit"] = m["latest_trading_day_profit"]
                return (old_fund, old_fund["holdings"]["total_invested"],
                        old_fund["holdings"]["current_value"], "\u4f7f\u7528\u7f13\u5b58\u6570\u636e(\u5df2\u7528\u65b0\u4ea4\u6613\u8bb0\u5f55\u91cd\u65b0\u8ba1\u7b97)")
            else:
                return (None, 0, 0, "\u65e0\u6cd5\u83b7\u53d6\u5b9e\u65f6\u6570\u636e\u4e14\u65e0\u7f13\u5b58")

        _apply_nav_correction(code, history, realtime, today, qdii_codes)

        # --- \u7b2c3\u6b65:\u8ba1\u7b97\u6301\u4ed3\u548c\u6536\u76ca ---
        purchases = purchase_records.get(platform, {}).get(code, [])
        if not history:
            return (None, 0, 0, "\u5386\u53f2\u6570\u636e\u4e3a\u7a7a\uff0c\u65e0\u6cd5\u8ba1\u7b97\u6301\u4ed3")

        holdings = calculate_holdings(purchases, realtime["nav"], history, fund_code=code)

        cumulative_returns = calculate_cumulative_returns(
            history, holdings["purchases"],
            original_purchases=purchases, history_for_nav=history
        )

        # \u8ba1\u7b97\u6628\u65e5\u51c0\u503c\u3001\u6628\u65e5\u6536\u76ca\u7387\u3001\u6628\u65e5\u6536\u76ca
        m = _calculate_latest_trading_day_metrics(history, holdings, today)

        # \u8ba1\u7b97\u672c\u5e74(YTD)\u6570\u636e
        ytd = _calculate_year_to_date_metrics(history, holdings, today)

        # \u7ec4\u7ec7\u6570\u636e
        latest_history_date = history[-1].get("date", "") if history else ""
        fund_data = {
            "code": code,
            "name": realtime["name"],
            "platform": platform,
            "current_nav": realtime["nav"],
            "nav_date": realtime["nav_date"],
            "daily_return": realtime["change_percent"],
            "nav_status": realtime.get("nav_status", "confirmed"),
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
        log("\u274c \u57fa\u91d1 {} \u5904\u7406\u5931\u8d25: {}".format(code, e))
        import traceback
        log(traceback.format_exc())
        return (None, 0, 0, str(e))


def main():
    """\u4e3b\u51fd\u6570"""
    log("="*60)
    log(f"\u57fa\u91d1\u6536\u76ca\u8ffd\u8e2a\u7cfb\u7edf - \u6570\u636e\u6293\u53d6")
    log(f"\u5f00\u59cb\u65f6\u95f4: {get_beijing_time().strftime('%Y-%m-%d %H:%M:%S')}")
    log("="*60)

    # \u89e3\u6790\u547d\u4ee4\u884c\u53c2\u6570
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-summary', action='store_true', help='\u8df3\u8fc7\u6c47\u603b\u66f4\u65b0\uff08\u4ec5\u66f4\u65b0\u57fa\u91d1\u6570\u636e\uff09')
    parser.add_argument('--force-refresh', action='store_true', help='\u5f3a\u5236\u5237\u65b0\u6a21\u5f0f:\u5ffd\u7565\u5386\u53f2\u7f13\u5b58\uff0c\u5168\u91cf\u83b7\u53d6')
    args = parser.parse_args()

    # \u81ea\u52a8\u5224\u65ad\u662f\u5426\u9700\u8981\u8df3\u8fc7\u6c47\u603b\u66f4\u65b0
    # \u5317\u4eac\u65f6\u95f4 20:00-23:59 \u7684\u66f4\u65b0\u4e0d\u66f4\u65b0\u6c47\u603b
    beijing_now = get_beijing_time()
    if 20 <= beijing_now.hour <= 23:
        if not args.skip_summary:
            args.skip_summary = True
            log(f"\u23ed \u5f53\u524d\u4e3a {beijing_now.strftime('%H:%M')} \u5317\u4eac\u65f6\u95f4\uff0c\u81ea\u52a8\u8df3\u8fc7\u6c47\u603b\u66f4\u65b0")

    # \u81ea\u52a8\u68c0\u6d4b\u65b0\u57fa\u91d1\uff08\u5728\u52a0\u8f7d\u914d\u7f6e\u4e4b\u524d\u6267\u884c\uff09
    auto_detect_new_funds()

    # \u52a0\u8f7d\u57fa\u91d1\u914d\u7f6e
    log("\n[0/4] \u52a0\u8f7d\u57fa\u91d1\u914d\u7f6e...")
    funds, qdii_codes, fund_names = load_fund_config()

    if not funds:
        log("\u274c \u65e0\u6cd5\u52a0\u8f7d\u57fa\u91d1\u914d\u7f6e\uff0c\u9000\u51fa")
        return
    
    # \u68c0\u67e5/\u521b\u5efa\u6a21\u677f\u6587\u4ef6
    log("\n[1/4] \u68c0\u67e5\u5fc5\u8981\u6587\u4ef6...")
    has_records = create_template_files(funds)
    if not has_records:
        log("\n\u26a0\ufe0f  \u8bf7\u5148\u7f16\u8f91 data/purchase_records.json \u6587\u4ef6\uff0c\u586b\u5165\u4f60\u7684\u5b9e\u9645\u4e70\u5165\u8bb0\u5f55")
        log("   \u6a21\u677f\u6587\u4ef6\u5df2\u521b\u5efa\uff0c\u4f60\u53ef\u4ee5\u53c2\u8003\u5176\u4e2d\u7684\u683c\u5f0f")
        return

    # \u52a0\u8f7d\u6301\u4ed3\u8bb0\u5f55
    log("\n[2/4] \u52a0\u8f7d\u6301\u4ed3\u8bb0\u5f55...")
    purchase_records = load_purchase_records()
    if purchase_records is None:
        return

    # \u521d\u59cb\u5316\u8f93\u51fa\u6570\u636e
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

    # \u52a0\u8f7d\u4e0a\u6b21\u6570\u636e\uff08\u7528\u4e8eAPI\u5931\u8d25\u65f6\u4fdd\u7559\u65e7\u6570\u636e\uff09
    previous_data = None
    previous_file = os.path.join(BASE_DIR, "data", "funds_data.json")
    if os.path.exists(previous_file):
        try:
            with open(previous_file, "r", encoding="utf-8") as f:
                previous_data = json.load(f)
            update_time_str = previous_data.get('update_time', '\u672a\u77e5')
            log(f"\u2713 \u5df2\u52a0\u8f7d\u4e0a\u6b21\u6570\u636e\u4f5c\u4e3a\u5907\u4efd\uff08\u66f4\u65b0\u65f6\u95f4: {update_time_str}")
        except Exception as e:
            log(f"\u26a0\ufe0f \u52a0\u8f7d\u4e0a\u6b21\u6570\u636e\u5931\u8d25: {e}")
            # \u7ee7\u7eed\u6267\u884c\uff0c\u4e0d\u5f71\u54cd\u4e3b\u6d41\u7a0b

    # \u6784\u5efa\u4e0a\u6b21\u6570\u636e\u7684\u5feb\u901f\u67e5\u627e\u8868: {fund_code: fund_data}
    prev_fund_map = {}
    if previous_data:
        for platform_name, fund_list in previous_data.get("funds", {}).items():
            for fund_item in fund_list:
                prev_fund_map[fund_item["code"]] = fund_item

    # \u5904\u7406\u6240\u6709\u57fa\u91d1
    log("\n[3/4] \u83b7\u53d6\u57fa\u91d1\u6570\u636e...")

    # \u521b\u5efa\u5171\u4eab HTTP Session\uff08\u7edf\u4e00\u91cd\u8bd5\u7b56\u7565\uff09
    http_session = _create_session()
    log("\u2713 HTTP Session \u5df2\u521b\u5efa\uff08\u81ea\u52a8\u91cd\u8bd5: 3\u6b21, \u9000\u907f: 1s\uff09")

    # \u8ba1\u7b97\u5386\u53f2\u6570\u636e\u8d77\u59cb\u65e5\u671f\uff08\u5168\u5c40\u515c\u5e95\uff0c\u6309\u57fa\u91d1\u7ef4\u5ea6\u4f18\u5148\uff09
    history_start_date = _get_earliest_purchase_date(purchase_records)
    log(f"\u5386\u53f2\u6570\u636e\u5168\u5c40\u8d77\u59cb\u65e5\u671f\uff08\u515c\u5e95\uff09: {history_start_date}")

    # \u52a0\u8f7d\u5386\u53f2\u6570\u636e\u7f13\u5b58\uff08\u589e\u91cf\u62c9\u53d6\uff09
    force_refresh = args.force_refresh or os.environ.get("FORCE_REFRESH", "").lower() == "true"
    if force_refresh:
        log("\u26a1 \u5f3a\u5236\u5237\u65b0\u6a21\u5f0f: \u5ffd\u7565\u5386\u53f2\u7f13\u5b58\uff0c\u5168\u91cf\u83b7\u53d6")
        history_cache = {}
    else:
        history_cache = load_history_cache()
        if history_cache:
            total_cached = sum(len(v) for v in history_cache.values())
            log(f"\u2713 \u5df2\u52a0\u8f7d\u5386\u53f2\u7f13\u5b58: {len(history_cache)} \u53ea\u57fa\u91d1, {total_cached} \u6761\u8bb0\u5f55")
        else:
            log("\u2139\ufe0f \u65e0\u5386\u53f2\u7f13\u5b58\uff0c\u5c06\u5168\u91cf\u83b7\u53d6")

    # \u5e76\u884c\u5904\u7406\u6240\u6709\u57fa\u91d1\uff08\u74f6\u98883\u4fee\u590d:ThreadPoolExecutor\uff09
    log(f"\n[3/4] \u5e76\u884c\u83b7\u53d6\u57fa\u91d1\u6570\u636e\uff08\u7ebf\u7a0b\u6c60 max_workers=3\uff09...")

    # \u6536\u96c6\u6240\u6709\u5f85\u5904\u7406\u57fa\u91d1
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
    stale_funds = []   # \u4f7f\u7528\u7f13\u5b58\u6570\u636e\u7684\u57fa\u91d1\uff08\u975e\u5931\u8d25\uff0c\u4f46\u6570\u636e\u53ef\u80fd\u8fc7\u671f\uff09
    today = get_beijing_time().strftime("%Y-%m-%d")

    # \u4f7f\u7528\u7ebf\u7a0b\u6c60\u5e76\u884c\u5904\u7406
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_to_fund = {}
        for platform, code, fund_start_date in fund_tasks:
            future = executor.submit(
                process_fund, platform, code, fund_start_date,
                http_session,  # \u4f20\u9012\u5171\u4eab session\uff0c\u907f\u514d\u6bcf\u53ea\u57fa\u91d1\u91cd\u590d\u521b\u5efa
                purchase_records, qdii_codes, fund_names,
                prev_fund_map, today, history_cache
            )
            future_to_fund[future] = (platform, code)

        # \u6309\u5b8c\u6210\u987a\u5e8f\u6536\u96c6\u7ed3\u679c
        for future in as_completed(future_to_fund):
            platform, code = future_to_fund[future]
            try:
                fund_data, invested, value, error_msg = future.result()
                if fund_data:
                    all_data["funds"][platform].append(fund_data)
                    all_data["summary"]["total_invested"] += invested
                    all_data["summary"]["total_value"] += value
                    # \u95ee\u98981\u4fee\u590d:\u7f13\u5b58\u6570\u636e\u4e5f\u8981\u63d0\u793a\u7528\u6237
                    if error_msg and "\u4f7f\u7528\u7f13\u5b58\u6570\u636e" in error_msg:
                        stale_funds.append(code)
                        log(f"  \u26a0 \u57fa\u91d1 {code} \u4f7f\u7528\u7f13\u5b58\u6570\u636e\uff08\u5b9e\u65f6\u6570\u636e\u83b7\u53d6\u5931\u8d25\uff09")
                else:
                    failed_funds.append(code)
                    if error_msg and "\u4f7f\u7528\u7f13\u5b58\u6570\u636e" in error_msg:
                        log(f"  \u26a0 \u57fa\u91d1 {code} \u4f7f\u7528\u7f13\u5b58\u6570\u636e")
                    elif error_msg:
                        log(f"  \u274c \u57fa\u91d1 {code} \u5904\u7406\u5931\u8d25: {error_msg}")
            except Exception as e:
                failed_funds.append(code)
                log(f"  \u274c \u57fa\u91d1 {code} \u5904\u7406\u5f02\u5e38: {e}")

    # \u7edf\u4e00\u4fdd\u5b58\u5386\u53f2\u7f13\u5b58\uff08\u6240\u6709\u57fa\u91d1\u5904\u7406\u5b8c\u6210\u540e\u53ea\u5199\u4e00\u6b21\uff0c\u907f\u514d\u9010\u57fa\u91d1\u5199\u76d8\uff09
    try:
        save_history_cache(history_cache)
    except Exception as cache_err:
        log("\u26a0\ufe0f \u4fdd\u5b58\u5386\u53f2\u7f13\u5b58\u5931\u8d25: {}".format(cache_err), "warning")

    # \u8ba1\u7b97\u603b\u8ba1\u6536\u76ca
    log("\n[4/4] \u8ba1\u7b97\u603b\u8ba1\u6536\u76ca...")
    summary = all_data["summary"]
    summary["total_profit_loss"] = round(summary["total_value"] - summary["total_invested"], 2)
    if summary["total_invested"] > 0:
        summary["total_profit_loss_percent"] = round(summary["total_profit_loss"] / summary["total_invested"] * 100, 2)
    
    # \u8ba1\u7b97\u6628\u65e5\u76c8\u4e8f\uff08\u7d2f\u52a0\u5404\u57fa\u91d1\u5df2\u8ba1\u7b97\u7684\u6628\u65e5\u6536\u76ca\uff09
    latest_trading_day_profit = 0
    day_before_latest_trading_day_profit = 0
    for platform_funds in all_data["funds"].values():
        for fund in platform_funds:
            latest_trading_day_profit += fund.get("latest_trading_day_profit", 0)
            day_before_latest_trading_day_profit += fund.get("day_before_latest_trading_day_profit", 0)
    
    summary["latest_trading_day_profit_loss"] = round(latest_trading_day_profit, 2)
    summary["latest_trading_day_profit_loss_percent"] = round((latest_trading_day_profit / summary["total_value"] * 100), 2) if summary["total_value"] > 0 else 0
    
    # \u8ba1\u7b97\u6628\u65e5\u53d8\u5316\u5dee\uff08\u4e0e\u524d\u4e00\u4ea4\u6613\u65e5\u5bf9\u6bd4\uff09
    summary["latest_trading_day_profit_loss_diff"] = round(latest_trading_day_profit - day_before_latest_trading_day_profit, 2)
    
    # \u8ba1\u7b97\u6628\u65e5\u6536\u76ca\u7387\u53d8\u5316\u5dee\uff08\u52a0\u6743\u5e73\u5747\uff09
    # \u4f7f\u7528\u5404\u57fa\u91d1\u6628\u65e5\u6536\u76ca\u7387\u548c\u524d\u65e5\u6536\u76ca\u7387\uff0c\u6309\u5e02\u503c\u52a0\u6743\u8ba1\u7b97
    latest_trading_day_return_weighted_sum = 0
    day_before_latest_trading_day_return_weighted_sum = 0
    total_weight = 0
    for platform_funds in all_data["funds"].values():
        for fund in platform_funds:
            weight = fund.get("holdings", {}).get("current_value", 0)
            if weight > 0:
                latest_trading_day_return_weighted_sum += fund.get("latest_trading_day_return", 0) * weight
                day_before_latest_trading_day_return_weighted_sum += fund.get("day_before_latest_trading_day_return", 0) * weight
                total_weight += weight
    
    if total_weight > 0:
        latest_trading_day_return_avg = latest_trading_day_return_weighted_sum / total_weight
        day_before_latest_trading_day_return_avg = day_before_latest_trading_day_return_weighted_sum / total_weight
        summary["latest_trading_day_profit_loss_percent_diff"] = round(latest_trading_day_return_avg - day_before_latest_trading_day_return_avg, 2)
    else:
        summary["latest_trading_day_profit_loss_percent_diff"] = 0

    # \u517c\u5bb9\u5b57\u6bb5\u522b\u540d\uff08\u524d\u7aef\u4f7f\u7528 yesterday_xxx\uff09
    summary["yesterday_profit_loss"] = summary["latest_trading_day_profit_loss"]
    summary["yesterday_profit_loss_percent"] = summary["latest_trading_day_profit_loss_percent"]
    summary["yesterday_profit_loss_diff"] = summary["latest_trading_day_profit_loss_diff"]
    summary["yesterday_profit_loss_percent_diff"] = summary["latest_trading_day_profit_loss_percent_diff"]

    # \u8ba1\u7b97\u672c\u5e74\u6570\u636e\uff08YTD\uff09
    ytd_profit_sum = 0
    ytd_return_weighted_sum = 0
    ytd_total_weight = 0
    for platform_funds in all_data["funds"].values():
        for fund in platform_funds:
            ytd_profit_sum += fund.get("ytd_profit", 0)
            weight = fund.get("holdings", {}).get("current_value", 0)
            if weight > 0:
                ytd_return_weighted_sum += fund.get("ytd_return", 0) * weight
                ytd_total_weight += weight

    summary["ytd_profit_loss"] = round(ytd_profit_sum, 2)
    summary["ytd_profit_loss_percent"] = round(ytd_return_weighted_sum / ytd_total_weight, 2) if ytd_total_weight > 0 else 0

    # \u7d2f\u8ba1\u7b97\u5df2\u5b9e\u73b0\u76c8\u4e8f
    total_realized_profit = 0
    for platform_funds in all_data["funds"].values():
        for fund in platform_funds:
            if fund.get("holdings") and fund["holdings"].get("realized_profit_loss"):
                total_realized_profit += fund["holdings"]["realized_profit_loss"]
    
    summary["total_realized_profit_loss"] = round(total_realized_profit, 2)
    
    # \u6253\u5370\u6c47\u603b\u4fe1\u606f
    log("\n" + "="*60)
    log("\u2713 \u6570\u636e\u6293\u53d6\u5b8c\u6210\uff01")
    log(f"  \u603b\u6295\u5165: \u00a5{summary['total_invested']:.2f}")
    log(f"  \u5f53\u524d\u5e02\u503c: \u00a5{summary['total_value']:.2f}")
    profit_sign = "+" if summary["total_profit_loss"] >= 0 else ""
    log(f"  \u672a\u5b9e\u73b0\u76c8\u4e8f: {profit_sign}\u00a5{summary['total_profit_loss']:.2f} ({profit_sign}{summary['total_profit_loss_percent']:.2f}%)")
    yesterday_sign = "+" if summary["latest_trading_day_profit_loss"] >= 0 else ""
    log(f"  \u6628\u65e5\u76c8\u4e8f: {yesterday_sign}\u00a5{summary['latest_trading_day_profit_loss']:.2f} ({yesterday_sign}{summary['latest_trading_day_profit_loss_percent']:.2f}%)")
    realized_sign = "+" if summary["total_realized_profit_loss"] >= 0 else ""
    log(f"  \u5df2\u5b9e\u73b0\u76c8\u4e8f: {realized_sign}\u00a5{summary['total_realized_profit_loss']:.2f}")
    log(f"  \u603b\u76c8\u4e8f: {profit_sign}\u00a5{summary['total_profit_loss']:.2f} ({profit_sign}{summary['total_profit_loss_percent']:.2f}%)")
    if stale_funds:
        log(f"\n  [Warning] \u4ee5\u4e0b\u57fa\u91d1\u4f7f\u7528\u7f13\u5b58\u6570\u636e\uff08\u975e\u5b9e\u65f6\uff09: {', '.join(stale_funds)}")
    if failed_funds:
        log(f"\n  [ERROR] \u4ee5\u4e0b\u57fa\u91d1\u5904\u7406\u5931\u8d25: {', '.join(failed_funds)}")
    log("="*60)

    # \u4fdd\u5b58\u6570\u636e
    output_file = os.path.join(BASE_DIR, "data", "funds_data.json")
    try:
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(all_data, f, ensure_ascii=False, indent=2)
        log(f"\n\u2713 \u6570\u636e\u5df2\u4fdd\u5b58\u5230 {output_file}")
        log(f"  \u66f4\u65b0\u65f6\u95f4: {all_data['update_time']}")
    except Exception as e:
        log(f"[ERROR] \u4fdd\u5b58\u6570\u636e\u5230 {output_file} \u5931\u8d25: {e}")

    # \u5386\u53f2\u7f13\u5b58\u5df2\u5728 main() \u672b\u5c3e\u7edf\u4e00\u4fdd\u5b58\uff08\u7b2c1115-1119\u884c\uff09\uff0c\u6b64\u5904\u65e0\u9700\u91cd\u590d

    # \u751f\u6210\u6301\u4ed3\u5feb\u7167
    log("\n\u751f\u6210\u6301\u4ed3\u5feb\u7167...")
    try:
        from generate_holdings import generate_holdings_snapshot
        generate_holdings_snapshot()
    except Exception as e:
        log("[Warning] \u6301\u4ed3\u5feb\u7167\u751f\u6210\u5931\u8d25: {}".format(e))

    # \u66f4\u65b0\u57fa\u51c6\u6307\u6570\u6570\u636e\uff08\u4e1c\u8d22 K\u7ebf API\uff09
    log("\n\u66f4\u65b0\u57fa\u51c6\u6307\u6570\u6570\u636e...")
    try:
        update_benchmark_index_data()
    except Exception as e:
        log("[Warning] \u57fa\u51c6\u6307\u6570\u6570\u636e\u66f4\u65b0\u5931\u8d25: {}".format(e))


def update_benchmark_index_data():
    """\u4ece\u4e1c\u65b9\u8d22\u5bcc API \u66f4\u65b0\u57fa\u51c6\u6307\u6570\u6570\u636e\uff08\u79d1\u521b50 sh000688, \u7eb3\u65af\u8fbe\u514b100 us.NDX\uff09"""
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
            log(f"\u2713 \u5df2\u52a0\u8f7d\u65e7\u57fa\u51c6\u6307\u6570\u6570\u636e: {list(existing.keys())}")
        except Exception as e:
            log(f"\u26a0\ufe0f \u52a0\u8f7d\u65e7\u57fa\u51c6\u6307\u6570\u6570\u636e\u5931\u8d25: {e}", "warning")
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
                log(f"  \u26a0 {code} ({info['name']}) \u672a\u83b7\u53d6\u5230\u6570\u636e")
                continue
            new_data = {}
            for kline in klines:
                parts = kline.split(",")
                if len(parts) >= 3:
                    new_data[parts[0]] = float(parts[2])
            if not new_data:
                log(f"  \u26a0 {code} ({info['name']}) \u89e3\u6790\u6570\u636e\u4e3a\u7a7a")
                continue
            old_data = existing.get(code, {}).get("data", {})
            old_data.update(new_data)
            existing[code] = {"name": info["name"], "data": old_data}
            log(f"  \u2713 {code} ({info['name']}): {len(klines)} \u6761K\u7ebf, \u6700\u65b0 {list(new_data.keys())[-1]} \u6536\u76d8 {list(new_data.values())[-1]}")
            updated = True
        except Exception as e:
            log(f"  \u26a0 {code} ({info['name']}) \u83b7\u53d6\u5931\u8d25: {e}", "warning")
    if updated:
        import tempfile
        fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(INDEX_CACHE_FILE), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp_path, INDEX_CACHE_FILE)
            total_entries = sum(len(v.get("data", {})) for v in existing.values())
            log(f"\u2713 \u57fa\u51c6\u6307\u6570\u6570\u636e\u5df2\u4fdd\u5b58: {len(existing)} \u4e2a\u6307\u6570, {total_entries} \u6761\u8bb0\u5f55")
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
    else:
        log("\u2139 \u57fa\u51c6\u6307\u6570\u6570\u636e\u65e0\u66f4\u65b0")


def fetch_fund_info_from_web(code, session=None):
    """
    \u4ece\u5929\u5929\u57fa\u91d1\u7f51\u83b7\u53d6\u57fa\u91d1\u57fa\u672c\u4fe1\u606f\uff08\u57fa\u91d1\u540d\u79f0\u3001\u7c7b\u578b\uff09
    \u8fd4\u56de: {"name": "\u57fa\u91d1\u540d\u79f0", "is_qdii": True/False, "benchmark": "..."} \u6216 None
    """

    url = "https://fundgz.1234567.com.cn/js/{}.js".format(code)

    try:
        if session is None:
            session = _create_session()

        resp = session.get(url, timeout=10)
        if resp.status_code == 200:
            # \u89e3\u6790 JSONP: jsonpgz({...});
            match = re.search(r'jsonpgz\((.*?)\);?\s*$', resp.text, re.DOTALL)
            if match:
                data = json.loads(match.group(1))
                name = data.get("name", "")

                # \u542f\u53d1\u5f0f\u5224\u65ad\u662f\u5426\u4e3a QDII
                qdii_keywords = ["\u7eb3\u65af\u8fbe\u514b", "\u6807\u666e", "\u7f8e\u80a1", "QDII", "\u6d77\u5916", "\u56fd\u9645"]
                is_qdii = any(kw in name for kw in qdii_keywords)

                benchmark = "\u7eb3\u65af\u8fbe\u514b100\u6307\u6570" if is_qdii else "\u79d1\u521b50\u6307\u6570"

                log("  \u2713 \u57fa\u91d1 {}: {} ({}\uff0c\u57fa\u51c6: {})".format(code, name, "QDII" if is_qdii else "\u56fd\u5185", benchmark))

                return {
                    "name": name,
                    "is_qdii": is_qdii,
                    "benchmark": benchmark
                }
    except Exception as e:
        log("\u26a0\ufe0f \u83b7\u53d6\u57fa\u91d1 {} \u4fe1\u606f\u5931\u8d25: {}".format(code, e), "warning")

    return None


def auto_detect_new_funds():
    """
    \u81ea\u52a8\u68c0\u6d4b purchase_records.json \u4e2d\u7684\u65b0\u57fa\u91d1\uff0c\u5e76\u6dfb\u52a0\u5230 fund_config.json
    """
    log("\n[0.5/4] \u81ea\u52a8\u68c0\u6d4b\u65b0\u57fa\u91d1...")

    records_file = os.path.join(BASE_DIR, "data", "purchase_records.json")
    if not os.path.exists(records_file):
        log("\u2139\ufe0f \u4ea4\u6613\u8bb0\u5f55\u6587\u4ef6\u4e0d\u5b58\u5728\uff0c\u8df3\u8fc7\u65b0\u57fa\u91d1\u68c0\u6d4b")
        return

    try:
        with open(records_file, "r", encoding="utf-8") as f:
            purchase_records = json.load(f)
    except Exception as e:
        log("\u26a0\ufe0f \u52a0\u8f7d\u4ea4\u6613\u8bb0\u5f55\u5931\u8d25: {}".format(e), "warning")
        return

    all_fund_codes = set()
    for platform, funds in purchase_records.items():
        if isinstance(funds, dict):
            for code in funds.keys():
                all_fund_codes.add(code)

    if not all_fund_codes:
        log("\u2139\ufe0f \u4ea4\u6613\u8bb0\u5f55\u4e2d\u6ca1\u6709\u57fa\u91d1\u4ee3\u7801")
        return

    config_file = os.path.join(BASE_DIR, "fund_config.json")
    if os.path.exists(config_file):
        with open(config_file, "r", encoding="utf-8") as f:
            config = json.load(f)
    else:
        config = {"funds": {}}

    # \u517c\u5bb9\u4e24\u79cd\u683c\u5f0f:\u7edf\u4e00\u5f52\u4e00\u5316\u4e3a {"funds": {"\u5e73\u53f0": [...]}}
    if "funds" not in config:
        # \u65e7\u683c\u5f0f:{"\u652f\u4ed8\u5b9d": [...], ...} \u2192 \u8f6c\u6362\u4e3a\u65b0\u683c\u5f0f
        log("[WARN] fund_config.json \u662f\u65e7\u683c\u5f0f\uff08\u65e0 'funds' \u952e\uff09\uff0c\u6b63\u5728\u8f6c\u6362...", "warning")
        config = {"funds": {k: v for k, v in config.items() if isinstance(v, list)}}

    existing_codes = set()
    for platform, fund_list in config.get("funds", {}).items():
        for fund in fund_list:
            existing_codes.add(fund["code"])

    new_codes = all_fund_codes - existing_codes
    if not new_codes:
        log("\u2713 \u65e0\u65b0\u57fa\u91d1\u9700\u8981\u6dfb\u52a0")
        return

    log("\ud83d\udd0d \u53d1\u73b0 {} \u53ea\u65b0\u57fa\u91d1: {}".format(len(new_codes), ", ".join(sorted(new_codes))))

    session = _create_session()
    for code in sorted(new_codes):
        log("  \u6b63\u5728\u83b7\u53d6\u57fa\u91d1 {} \u7684\u4fe1\u606f...".format(code))
        fund_info = fetch_fund_info_from_web(code, session)

        if fund_info is None:
            log("  \u26a0\ufe0f \u65e0\u6cd5\u83b7\u53d6\u57fa\u91d1 {} \u7684\u4fe1\u606f\uff0c\u8df3\u8fc7".format(code), "warning")
            continue

        platform = None
        for p, funds in purchase_records.items():
            if isinstance(funds, dict) and code in funds:
                platform = p
                break

        if platform is None:
            log("  \u26a0\ufe0f \u65e0\u6cd5\u786e\u5b9a\u57fa\u91d1 {} \u7684\u5e73\u53f0\uff0c\u8df3\u8fc7".format(code), "warning")
            continue

        if platform not in config["funds"]:
            config["funds"][platform] = []

        config["funds"][platform].append({
            "code": code,
            "name": fund_info["name"],
            "is_qdii": fund_info["is_qdii"],
            "benchmark": fund_info["benchmark"]
        })

        log("  \u2713 \u5df2\u6dfb\u52a0\u57fa\u91d1 {} ({}) \u5230 {}".format(code, fund_info["name"], platform))

    try:
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        log("\u2713 \u5df2\u66f4\u65b0 fund_config.json")
    except Exception as e:
        log("\u274c \u4fdd\u5b58 fund_config.json \u5931\u8d25: {}".format(e), "error")


if __name__ == "__main__":
    main()