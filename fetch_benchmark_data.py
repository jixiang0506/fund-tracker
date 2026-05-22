"""
获取基金业绩比较基准的指数历史数据
支持A股指数、港股指数等
"""
import json
import re
import requests
import sys
import io

# 强制 UTF-8 stdout，避免 Windows 控制台 GBK 编码报错
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from datetime import datetime, timedelta
from pathlib import Path

# 指数名称到代码的映射
INDEX_MAPPING = {
    # A股指数
    '上证指数': 'sh000001',
    '沪深300': 'sh000300',
    '中证500': 'sh000905',
    '中证800': 'sh000906',
    '创业板指': 'sz399006',
    '科创50指数': 'sh000688',
    '上证国债指数': 'sh000012',
    '中证全债指数': 'sh000013',
    '中证TMT产业主题指数': 'sz399610',
    '中证数字经济主题指数': 'sz931471',
    '中证高端装备制造指数': 'sh931680',
    # 注：中证港股通综合指数(H11165)在腾讯财经接口中无数据，暂不提供映射
    # 港股指数
    '恒生指数': 'hkHSI',
    # 美股指数（由 fetch_nasdaq.py 单独获取，此处仅作映射标识）
    '纳斯达克100指数': 'usNDX',
}

def fetch_index_history(index_code, start_date=None, days=500):
    """
    获取指数历史数据
    index_code: 指数代码，如 sh000001, sz399006, hkHSI
    """
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?_var=kline_dayqfq&param={index_code},day,,,{days},qfq"
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        # 解析JSONP响应
        text = response.text
        json_start = text.find('{')
        json_end = text.rfind('}') + 1
        if json_start == -1 or json_end == 0:
            return None
            
        data = json.loads(text[json_start:json_end])
        
        if data.get('code') != 0:
            return None
            
        index_data = data.get('data', {}).get(index_code, {})
        day_data = index_data.get('day', [])
        
        # 转换为 {date: close_price} 格式
        result = {}
        for item in day_data:
            if len(item) >= 2:
                date_str = item[0]
                close_price = float(item[1])
                result[date_str] = close_price
                
        return result
        
    except Exception as e:
        print(f"获取指数 {index_code} 数据失败: {e}")
        return None

def calculate_index_return(index_data, base_date=None):
    """
    计算指数收益率
    index_data: {date: close_price}
    返回: {date: return_rate%}
    """
    if not index_data:
        return {}
    
    # 找到基准日期（第一笔买入日期或数据起始日期）
    dates = sorted(index_data.keys())
    if not dates:
        return {}
    
    if base_date and base_date in index_data:
        base_price = index_data[base_date]
    else:
        base_price = index_data[dates[0]]
    
    result = {}
    for date in dates:
        price = index_data[date]
        return_rate = (price / base_price - 1) * 100
        result[date] = return_rate
    
    return result

def parse_benchmark(benchmark_str):
    """
    解析业绩比较基准字符串
    返回: [(index_name, weight), ...]
    例如: "中证500指数收益率*85%+中债总指数收益率*15%"
    """
    if not benchmark_str:
        return []
    
    # 去除"收益率"等字样
    benchmark_str = benchmark_str.replace('收益率', '').replace('(税后)', '')
    
    # 分割复合基准
    parts = re.split(r'[+]', benchmark_str)
    
    result = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
            
        # 提取权重
        weight_match = re.search(r'\*(\d+)%', part)
        if weight_match:
            weight = int(weight_match.group(1)) / 100
            index_name = re.sub(r'\*\d+%', '', part).strip()
        else:
            weight = 1.0
            index_name = part.strip()
        
        # 去除多余字符
        index_name = re.sub(r'[\(\)]', '', index_name)
        index_name = index_name.replace('人民币计价的', '').strip()
        
        result.append((index_name, weight))
    
    return result

def get_index_code(index_name):
    """
    根据指数名称获取指数代码
    """
    # 直接匹配
    if index_name in INDEX_MAPPING:
        return INDEX_MAPPING[index_name]
    
    # 模糊匹配
    for key, value in INDEX_MAPPING.items():
        if key in index_name or index_name in key:
            return value
    
    return None

def main():
    print("开始执行 fetch_benchmark_data.py...")

    # 读取基金配置
    config_file = Path(__file__).parent / 'fund_config.json'
    print(f"读取配置文件: {config_file}")
    with open(config_file, 'r', encoding='utf-8') as f:
        config = json.load(f)

    # 获取所有基准指数
    benchmarks = set()
    skipped = []
    for platform, fund_list in config['funds'].items():
        for fund in fund_list:
            benchmark = fund.get('benchmark', '')
            if benchmark:
                parsed = parse_benchmark(benchmark)
                for index_name, weight in parsed:
                    code = get_index_code(index_name)
                    if code:
                        benchmarks.add((index_name, code))
                    else:
                        skipped.append(index_name)

    if skipped:
        print(f"⚠ 以下基准指数无映射，将被跳过: {', '.join(set(skipped))}")

    print(f"需要获取 {len(benchmarks)} 个指数的数据:")
    for name, code in benchmarks:
        print(f"  {name} ({code})")

    # 读取已有数据（增量更新，不覆盖）
    output_file = Path(__file__).parent / 'data' / 'benchmark_index_data.json'
    all_index_data = {}
    if output_file.exists():
        try:
            with open(output_file, 'r', encoding='utf-8') as f:
                all_index_data = json.load(f)
            print(f"✓ 已加载已有数据: {len(all_index_data)} 个指数")
        except Exception as e:
            print(f"⚠ 读取已有数据失败: {e}，将创建新文件")

    # 获取指数历史数据（跳过 usNDX，由 fetch_nasdaq.py 维护）
    for index_name, index_code in benchmarks:
        if index_code == 'usNDX':
            print(f"\n跳过 {index_name} ({index_code})，由 fetch_nasdaq.py 维护")
            continue

        print(f"\n正在获取 {index_name} ({index_code})...")
        index_data = fetch_index_history(index_code, days=1000)
        if index_data:
            # 增量更新：合并新数据到已有数据
            if index_code in all_index_data:
                # 合并数据（新数据覆盖旧数据）
                all_index_data[index_code]['data'].update(index_data)
                print(f"  成功更新 {len(index_data)} 条数据（增量）")
            else:
                all_index_data[index_code] = {
                    'name': index_name,
                    'data': index_data
                }
                print(f"  成功获取 {len(index_data)} 条数据")
        else:
            print(f"  获取失败")

    # 保存到文件（增量更新）
    output_file.parent.mkdir(exist_ok=True)
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(all_index_data, f, ensure_ascii=False, indent=2)

    print(f"\n基准指数数据已保存到: {output_file}")
    print(f"总计: {len(all_index_data)} 个指数")

if __name__ == '__main__':
    main()
