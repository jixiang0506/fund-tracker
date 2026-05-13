#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
获取基金真实名称
"""

import requests
import json

# 基金列表
FUNDS = {
    "支付宝": ["270023", "016665", "001438", "002112", "018230"],
    "理财通": ["018147", "012922", "019018"],
    "招商银行": ["021277", "000390", "020723"]
}

# 天天基金实时API
REALTIME_API = "http://fundgz.1234567.com.cn/js/{}.js"

def get_fund_name(fund_code):
    """获取基金名称"""
    try:
        url = REALTIME_API.format(fund_code)
        response = requests.get(url, timeout=10)
        response.raise_for_status()

        # 解析JSONP响应
        text = response.text
        json_str = text[text.find('{'):text.rfind('}')+1]
        data = json.loads(json_str)

        return data.get("name", "未知基金")
    except Exception as e:
        print(f"获取基金 {fund_code} 名称失败: {e}")
        return "未知基金"

def main():
    print("正在获取基金真实名称...")

    fund_info = {}

    for platform, codes in FUNDS.items():
        print(f"\n处理 {platform}...")
        fund_info[platform] = {}

        for code in codes:
            name = get_fund_name(code)
            fund_info[platform][code] = name
            print(f"  {code}: {name}")

    # 保存到文件
    with open("data/fund_info.json", "w", encoding="utf-8") as f:
        json.dump(fund_info, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 基金信息已保存到 data/fund_info.json")

if __name__ == "__main__":
    main()
