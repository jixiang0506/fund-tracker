import re

filepath = "index.html"
with open(filepath, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Build a map: line_index -> new_transition_line
# First, parse CSS to find which selector owns each "transition: all" line
replacements = {}  # line_index (0-based) -> new_line_string

i = 0
while i < len(lines):
    line = lines[i]
    stripped = line.strip()
    
    # Detect start of a CSS rule: line contains { 
    if '{' in stripped and '}' not in stripped.split('{')[0]:
        # Extract selector (everything before {)
        selector_part = stripped.split('{')[0].strip()
        current_selectors = [s.strip() for s in selector_part.split(',')]
        
        # Now scan forward within this rule block to find transition: all
        brace_depth = stripped.count('{') - stripped.count('}')
        j = i + 1
        while j < len(lines) and brace_depth > 0:
            inner = lines[j]
            brace_depth += inner.count('{') - inner.count('}')
            # Check if this line has "transition: all"
            m = re.search(r'transition:\s*all\s*([\d.]+s(?:\s+ease)?)?', inner)
            if m:
                sel_name = current_selectors[0].split('.')[-1].split(':')[0].strip()
                # Determine replacement based on selector
                new_trans = None
                full_match = m.group(0)
                indent = inner[:len(inner) - len(inner.lstrip())]
                
                if '.btn' in selector_part and '.btn:hover' not in selector_part and '.btn.' not in selector_part:
                    new_trans = indent + 'transition: background 0.2s, color 0.2s;\n'
                elif '.sell-mode-btn' in selector_part and ':hover' not in selector_part and '.active' not in selector_part.split(',')[0]:
                    new_trans = indent + 'transition: border-color 0.2s, background 0.2s, color 0.2s, font-weight 0.2s;\n'
                elif '.stat-card' in selector_part and ':hover' not in selector_part:
                    new_trans = indent + 'transition: border-color 0.2s, box-shadow 0.2s;\n'
                elif '.fund-card' in selector_part and ':hover' not in selector_part:
                    new_trans = indent + 'transition: border-color 0.2s, box-shadow 0.2s;\n'
                elif '.sell-quick-btn' in selector_part and ':hover' not in selector_part:
                    new_trans = indent + 'transition: background 0.2s, color 0.2s;\n'
                elif '.filter-btn' in selector_part and ':hover' not in selector_part and '.active' not in selector_part.split(',')[0]:
                    new_trans = indent + 'transition: border-color 0.2s, color 0.2s, background 0.2s;\n'
                elif '.period-btn' in selector_part and ':hover' not in selector_part and '.active' not in selector_part.split(',')[0]:
                    new_trans = indent + 'transition: background 0.3s, color 0.3s, border-color 0.3s;\n'
                elif '.modal-tab' in selector_part and ':hover' not in selector_part and '.active' not in selector_part.split(',')[0]:
                    new_trans = indent + 'transition: color 0.3s ease, background 0.3s ease, border-bottom-color 0.3s ease;\n'
                elif '.records-filter-btn' in selector_part and ':hover' not in selector_part and '.active' not in selector_part.split(',')[0]:
                    new_trans = indent + 'transition: border-color 0.2s, color 0.2s, background 0.2s;\n'
                elif '.more-btn' in selector_part and ':hover' not in selector_part:
                    new_trans = indent + 'transition: border-color 0.2s, background 0.2s, color 0.2s;\n'
                
                if new_trans:
                    # Replace the whole line
                    lines[j] = new_trans
                    replacements[j] = new_trans.strip()
                    print(f"  Line {j+1}: {selector_part[:60]} -> {new_trans.strip()}")
                else:
                    print(f"  Line {j+1}: UNMATCHED - {selector_part[:60]}")
            j += 1
    i += 1

print(f"\nTotal replacements: {len(replacements)}")

with open(filepath, 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("File written successfully.")
