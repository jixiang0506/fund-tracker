#!/usr/bin/env python3
"""
交易记录完整性校验脚本
比较 purchase_records.json 和 funds_data.json，
确保所有交易记录都被正确处理。
如果有记录丢失，返回非0退出码（阻断 GitHub Actions 推送）。
"""
import sys
import os
from logger_config import safe_load_json

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
PURCHASE_FILE = os.path.join(DATA_DIR, 'purchase_records.json')
FUNDS_FILE = os.path.join(DATA_DIR, 'funds_data.json')

def validate():
    pr = safe_load_json(PURCHASE_FILE)
    fd = safe_load_json(FUNDS_FILE)

    if pr is None or fd is None:
        print("❌ 数据文件读取失败，校验中止")
        sys.exit(1)

    errors = []
    warnings = []

    # 遍历 purchase_records.json 中的所有记录
    for platform, funds in pr.items():
        for code, records in funds.items():
            # 在 funds_data.json 中定位该基金
            target_fund = None
            for arr in fd.get('funds', {}).values():
                if isinstance(arr, list):
                    for f in arr:
                        if f.get('code') == code:
                            target_fund = f
                            break
                    if target_fund:
                        break

            if not target_fund:
                errors.append(f"❌ {platform}/{code}: funds_data.json 中完全缺失该基金")
                continue

            # 获取 funds_data.json 中的 purchases 日期集合
            fd_purchases = target_fund.get('holdings', {}).get('purchases', [])
            fd_dates = set(p['date'] for p in fd_purchases)

            # 检查每条 purchase_records 中的记录是否都在 purchases 中
            for r in records:
                r_date = r.get('date', '')
                r_type = r.get('type', 'buy')
                if r_date not in fd_dates:
                    errors.append(
                        f"❌ {platform}/{code} {r_date} [{r_type}] "
                        f"金额={r.get('amount', '?')}: "
                        f"在 funds_data.json 的 purchases 中缺失"
                    )

            # 反向检查：funds_data.json 中有但 purchase_records 中没有的（疑似脏数据）
            pr_dates = set(r['date'] for r in records)
            extra_in_fd = fd_dates - pr_dates
            if extra_in_fd:
                for extra_date in sorted(extra_in_fd):
                    warnings.append(
                        f"⚠️  {platform}/{code} {extra_date}: "
                        f"在 funds_data.json 中存在但 purchase_records.json 中无此记录"
                    )

    # 输出结果
    print("=" * 60)
    print("  交易记录完整性校验报告")
    print("=" * 60)

    if warnings:
        print(f"\n⚠️  发现 {len(warnings)} 个警告：")
        for w in warnings:
            print(f"  {w}")

    if errors:
        print(f"\n❌ 发现 {len(errors)} 个错误：")
        for e in errors:
            print(f"  {e}")
        print(f"\n✋ 校验失败！请先运行 fetch_fund_data.py 重新生成数据。")
        print(f"   命令: python fetch_fund_data.py")
        sys.exit(1)
    else:
        total = sum(len(records) for funds in pr.values() for records in funds.values())
        print(f"\n✅ 校验通过！共 {total} 条交易记录，全部匹配。")
        sys.exit(0)

if __name__ == '__main__':
    validate()
