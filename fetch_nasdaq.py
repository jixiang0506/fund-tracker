"""
获取纳斯达克100指数(^NDX)历史数据，写入 data/benchmark_index_data.json
由 fetch_fund_data.py 自动调用，也可单独运行

数据源: yfinance (替代已失效的 Yahoo Finance 直接 CSV 下载)
"""
import json
import os
import sys
import io
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 强制 UTF-8 stdout，避免 Windows 控制台 GBK 编码报错
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def main():
    script_dir = Path(__file__).parent
    data_file = script_dir / "data" / "benchmark_index_data.json"

    # 尝试导入 yfinance
    try:
        import yfinance as yf
    except ImportError:
        print("[ERROR] 缺少 yfinance 库，请运行: pip install yfinance")
        sys.exit(1)

    print("开始获取纳斯达克100指数(^NDX)数据...")

    end = datetime.now(timezone.utc)
    ticker = yf.Ticker("^NDX")
    hist = ticker.history(start="2022-03-01", end=end, interval="1d")

    if hist.empty:
        print("[WARNING] 获取到空数据，跳过保存")
        return

    data = {}
    for date, row in hist.iterrows():
        date_str = date.strftime("%Y-%m-%d")
        close = round(float(row["Close"]), 2)
        data[date_str] = close

    print("[OK] 获取到 {} 条数据（{} ~ {})".format(
        len(data), min(data.keys()), max(data.keys())
    ))

    # 增量更新 benchmark_index_data.json
    if data_file.exists():
        with open(data_file, "r", encoding="utf-8") as f:
            existing = json.load(f)
    else:
        existing = {}

    existing["usNDX"] = {
        "name": "纳斯达克100指数",
        "data": data
    }

    data_file.parent.mkdir(exist_ok=True)
    with open(data_file, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    print("[OK] 纳斯达克100指数数据已写入 {}".format(data_file))


if __name__ == "__main__":
    main()
