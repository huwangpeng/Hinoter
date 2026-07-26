import re
with open('web/infinite-viewer.html', 'r', encoding='utf-8') as f:
    content = f.read()

m = re.search(r'<script>(.*?)</script>', content, re.DOTALL)
code = m.group(1)
lines = code.split('\n')

braces = parens = brackets = 0
in_template = in_string = False
string_char = None

for li, line in enumerate(lines):
    i = 0
    while i < len(line):
        c = line[i]
        nc = line[i+1] if i+1 < len(line) else ''
        
        if c == '/' and nc == '/':
            break
        if c == '/' and nc == '*':
            end = line.find('*/', i+2)
            i = end + 2 if end >= 0 else len(line)
            continue
        
        if c in ('"', "'") and not in_template:
            if not in_string:
                in_string = True
                string_char = c
            elif c == string_char and (i == 0 or line[i-1] != '\\'):
                in_string = False
                string_char = None
            i += 1
            continue
        
        if c == '`':
            in_template = not in_template
            i += 1
            continue
        
        if in_string or in_template:
            i += 1
            continue
        
        if c == '{': braces += 1
        elif c == '}': braces -= 1
        elif c == '(': parens += 1
        elif c == ')': parens -= 1
        elif c == '[': brackets += 1
        elif c == ']': brackets -= 1
        i += 1
    
    if parens != 0 or braces != 0 or brackets != 0:
        print(f'L{li+1:4d}: br={braces:+d} pa={parens:+d} bk={brackets:+d}  {line.strip()[:80]}')

print(f'\nFinal: braces={braces}, parens={parens}, brackets={brackets}')
