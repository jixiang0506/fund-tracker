import re

filepath = "index.html"
with open(filepath, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Line numbers (1-based) and their replacement transition properties
# Based on earlier cat -n output and hover/active analysis
fixes = {
    # Line 147: .btn (hover changes: background, color)
    147: "            transition: background 0.2s, color 0.2s;\n",
    # Line 261: .sell-mode-btn (active changes: border-color, background, color, font-weight)
    261: "            transition: border-color 0.2s, background 0.2s, color 0.2s, font-weight 0.2s;\n",
    # Line 665: .stat-card (hover changes: border-color, box-shadow)
    665: "            transition: border-color 0.2s, box-shadow 0.2s;\n",
    # Line 751: .fund-card (hover changes: border-color, box-shadow)
    751: "            transition: border-color 0.2s, box-shadow 0.2s;\n",
    # Line 1422: .sell-quick-btn (hover changes: background, color)
    1422: "            transition: background 0.2s, color 0.2s;\n",
    # Line 1696: .filter-btn (hover/active: border-color, color, background)
    1696: "            transition: border-color 0.2s, color 0.2s, background 0.2s;\n",
    # Line 1712: .period-btn (hover/active: background, color, border-color)
    1712: "            transition: background 0.3s, color 0.3s, border-color 0.3s;\n",
    # Line 1892: .modal-tab (hover/active: color, background, border-bottom-color)
    1892: "            transition: color 0.3s ease, background 0.3s ease, border-bottom-color 0.3s ease;\n",
    # Line 1982: .records-filter-btn (hover/active: border-color, color, background)
    1982: "            transition: border-color 0.2s, color 0.2s, background 0.2s;\n",
    # Line 2099: .more-btn (hover changes: border-color, background, color)
    2099: "            transition: border-color 0.2s, background 0.2s, color 0.2s;\n",
}

updated = 0
for ln, new_line in fixes.items():
    idx = ln - 1  # convert to 0-based
    if idx < len(lines) and 'transition' in lines[idx]:
        old = lines[idx]
        # 保留原始缩进，替换 transition 属性值部分
        # new_line 格式: "            transition: ...;\n"
        stripped = new_line.strip()
        indent = old[:len(old) - len(old.lstrip())]
        lines[idx] = indent + stripped + "\n"
        print(f"  OK Line {ln}: {old.strip()} -> {new_line.strip()}")
        updated += 1
    else:
        actual = lines[idx].strip() if idx < len(lines) else 'OUT_OF_RANGE'
        print(f"  FAIL Line {ln}: expected transition, got: {actual[:60]}")

with open(filepath, 'w', encoding='utf-8') as f:
    f.writelines(lines)

print(f"\nDone: {updated}/{len(fixes)} replaced")
