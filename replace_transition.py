import re

filepath = "index.html"
with open(filepath, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Parse CSS structure: track current selector by scanning backwards from each {
# For each "transition: all" line, find the owning selector
replacements = {}  # line_index -> new_line

i = 0
while i < len(lines):
    line = lines[i]
    # Find "transition: all" lines
    m = re.search(r'^\s*transition:\s*all\s*[\d.]+s', line)
    if m:
        # Scan backwards to find the selector (line ending with { )
        j = i - 1
        current_selector = None
        while j >= 0:
            prev = lines[j]
            stripped = prev.strip()
            if not stripped:
                j -= 1
                continue
            if '{' in stripped:
                # Found a rule block opening
                sel = stripped.split('{')[0].strip()
                current_selector = sel
                break
            j -= 1
        
        if current_selector:
            # Determine which properties actually change on hover/active
            # by scanning forward to find :hover and .active rules for this selector
            changes = set()
            sel_clean = current_selector.rstrip('{').strip()
            # Scan forward from current position to find hover/active for this selector
            k = i + 1
            depth = 0
            found_block = False
            while k < len(lines) and k < i + 200:
                lk = lines[k]
                if '{' in lk:
                    depth += 1
                if '}' in lk:
                    if depth == 0:
                        break
                    depth -= 1
                # Look for selector:hover or selector.active
                if re.search(r'\.' + re.escape(sel_clean.split('.')[-1].split(',')[0].strip()) + r'(?:\.| |,|\{)', lk):
                    pass  # hard to parse reliably
                k += 1
            
            print(f"Line {i+1}: selector='{current_selector}', line='{line.strip()}'")
            # We'll use a conservative approach: replace with common properties
            # Actually, let's just note the selector and decide manually
    i += 1

print("\nScan complete.")
