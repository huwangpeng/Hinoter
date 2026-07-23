import pathlib, re
for pn in ['page-2', 'page-3', 'page-22']:
    p = pathlib.Path(f'vector-export/1/svg/{pn}.svg').read_text('utf-8')
    colors = set(re.findall(r'fill="#([^"]+)"', p))
    ops = set(re.findall(r'fill-opacity="([^"]+)"', p))
    hl = [c for c,l in zip(re.findall(r'fill="#([^"]+)"', p), re.findall(r'fill-opacity="([^"]+)"', p)) if l != '1']
    print(f'{pn}: colors={sorted(colors)}, opacities={sorted(ops)}, hl={hl}')
