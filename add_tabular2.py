with open("index.html", 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
modified = 0
i = 0
while i < len(lines):
    line = lines[i]
    new_lines.append(line)
    # Check if line contains "font-family: var(--font-mono)"
    if 'font-family: var(--font-mono)' in line:
        # 幂等性：检查下一行是否已有 font-variant-numeric 声明
        if i + 1 < len(lines) and 'font-variant-numeric' in lines[i + 1]:
            print(f"  Skipped line {i+1} (already present): {line.strip()[:60]}")
            i += 1
            continue
        # Get indentation
        indent = line[:len(line) - len(line.lstrip())]
        added_line = indent + '            font-variant-numeric: tabular-nums;\n'
        new_lines.append(added_line)
        modified += 1
        print(f"  Added tabular-nums after line {i+1}: {line.strip()[:60]}")
    i += 1

with open("index.html", 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print(f"\nDone: added tabular-nums to {modified} locations")
