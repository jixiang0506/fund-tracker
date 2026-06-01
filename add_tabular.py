import re

filepath = "index.html"
with open(filepath, 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
modified = 0
for i, line in enumerate(lines):
    new_lines.append(line)
    # If this line has "font-family: var(--font-mono)", add tabular-nums after it
    if re.search(r'font-family:\s*var\(--font-mono\)', line):
        # Check if next non-empty line already has font-variant-numeric
        j = i + 1
        has_tabular = False
        while j < len(lines) and lines[j].strip() != '' and '}' not in lines[j]:
            if 'font-variant-numeric' in lines[j]:
                has_tabular = True
                break
            j += 1
        if not has_tabular:
            # Get indentation of current line
            indent = line[:len(line) - len(line.lstrip())]
            new_line = indent + '            font-variant-numeric: tabular-nums;\n'
            new_lines.append(new_line)
            modified += 1
            print(f"  Line {i+1}: added tabular-nums")

with open(filepath, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print(f"\nDone: added tabular-nums to {modified} locations")
