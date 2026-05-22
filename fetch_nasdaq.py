import os, json, calendar, time as t, sys
os.chdir(r"D:\Program Files (x86)\WorkBuddy\111\2026-05-12-task-2")
out = open("_nasdaq_output.txt", "w", encoding="utf-8")

try:
    import requests
    out.write("requests OK\n")
    out.flush()

    # Yahoo Finance CSV download
    from datetime import datetime
    start = int(calendar.timegm(datetime(2022, 3, 1).timetuple()))
    end = int(calendar.timegm(datetime.now().timetuple()))
    
    url = f"https://query1.finance.yahoo.com/v7/finance/download/%5ENDX?period1={start}&period2={end}&interval=1d&events=history"
    out.write(f"URL: {url[:80]}...\n")
    out.flush()
    
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    resp = requests.get(url, headers=headers, timeout=30)
    out.write(f"Status: {resp.status_code}\n")
    out.flush()
    
    if resp.status_code == 200:
        lines = resp.text.strip().split("\n")
        out.write(f"Lines: {len(lines)}\n")
        out.write(f"Header: {lines[0]}\n")
        
        data = {}
        for line in lines[1:]:
            parts = line.split(",")
            if len(parts) >= 5:
                date_str = parts[0]
                close = round(float(parts[4]), 2)
                data[date_str] = close
        
        if data:
            out.write(f"Records: {len(data)}, first={list(data.keys())[0]}, last={list(data.keys())[-1]}\n")
            out.flush()
            
            with open("data/benchmark_index_data.json", "r", encoding="utf-8") as f:
                existing = json.load(f)
            existing["usNDX"] = {"name": "纳斯达克100", "data": data}
            with open("data/benchmark_index_data.json", "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
            out.write("SAVED\n")
        else:
            out.write("WARNING: 获取到空数据，跳过保存以避免覆盖历史数据\n")
    else:
        out.write(f"FAILED: {resp.status_code}\n{resp.text[:200]}\n")

except Exception as e:
    out.write(f"ERROR: {e}\n")
finally:
    out.close()
