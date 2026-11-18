"""Builds the a-msg logo masters into design/logo/. Run from anywhere: python3 design/logo/source/build.py"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
OUT = os.path.dirname(HERE)

OLIVE, OLIVE_LIGHT, INK, PAPER, WHITE, BLACK = '#7B7135', '#B8AD62', '#24251F', '#E3DECC', '#FFFFFF', '#000000'

def f(v): return f'{v:.2f}'.rstrip('0').rstrip('.')
def svg(w, h, body, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {f(w)} {f(h)}" width="{f(w)}" height="{f(h)}" '
            f'role="img" aria-label="{title}"><title>{title}</title>{body}</svg>\n')
def rr(x, y, w, h, r):
    return (f'M{f(x+r)} {f(y)}H{f(x+w-r)}A{f(r)} {f(r)} 0 0 1 {f(x+w)} {f(y+r)}V{f(y+h-r)}A{f(r)} {f(r)} 0 0 1 {f(x+w-r)} {f(y+h)}'
            f'H{f(x+r)}A{f(r)} {f(r)} 0 0 1 {f(x)} {f(y+h-r)}V{f(y+r)}A{f(r)} {f(r)} 0 0 1 {f(x+r)} {f(y)}Z')

def bubble_a(R=90, wv=35, wh=32, k=7, cx=128, cy=127):
    """Lowercase a: outer shape and counter are both speech bubbles (round, one square corner at the stem foot).
    wv = vertical strokes, wh = horizontals (thinned for optical balance); left bowl overshoots 1.2 %."""
    rx, ry = R - wv, R - wh; ccx = cx + R - wv - rx; Ro = R * 1.012
    outer = (f'M{f(cx)} {f(cy-R)}A{f(R)} {f(R)} 0 0 1 {f(cx+R)} {f(cy)}V{f(cy+R-k)}A{f(k)} {f(k)} 0 0 1 {f(cx+R-k)} {f(cy+R)}'
             f'H{f(cx)}A{f(Ro)} {f(R)} 0 0 1 {f(cx-Ro)} {f(cy)}A{f(Ro)} {f(R)} 0 0 1 {f(cx)} {f(cy-R)}Z')
    counter = (f'M{f(ccx)} {f(cy-ry)}A{f(rx)} {f(ry)} 0 0 0 {f(ccx-rx)} {f(cy)}A{f(rx)} {f(ry)} 0 0 0 {f(ccx)} {f(cy+ry)}'
               f'H{f(ccx+rx)}V{f(cy)}A{f(rx)} {f(ry)} 0 0 0 {f(ccx)} {f(cy-ry)}Z')
    return outer + counter

def write(rel, content):
    p = os.path.join(OUT, rel); os.makedirs(os.path.dirname(p), exist_ok=True); open(p, 'w').write(content)

SYM = bubble_a()
SYM_SMALL = bubble_a(wv=42, wh=39, k=8)        # heavier cut for 32 px and below
SYM_REV = bubble_a(wv=34, wh=31)               # thinned for light-on-dark (irradiation)
SYM_SMALL_REV = bubble_a(wv=41, wh=38, k=8)

def sym_file(d, colour, title='a-msg'):
    return svg(256, 256, f'<path fill="{colour}" fill-rule="evenodd" d="{d}"/>', title)

for name, d, colour in [('colour', SYM, OLIVE), ('black', SYM, BLACK), ('ink', SYM, INK),
                        ('white', SYM_REV, WHITE), ('colour-reversed', SYM_REV, OLIVE_LIGHT)]:
    write(f'symbol/amsg-symbol-{name}.svg', sym_file(d, colour))
for name, d, colour in [('colour', SYM_SMALL, OLIVE), ('black', SYM_SMALL, BLACK), ('white', SYM_SMALL_REV, WHITE)]:
    write(f'symbol/amsg-symbol-small-{name}.svg', sym_file(d, colour))

# App icons on the macOS grid: 1024 canvas, 824 body, radius 185, mark 52 % of the body height.
def app_icon(bg, fg, small=False, full_bleed=False):
    d = SYM_SMALL if small else SYM
    if full_bleed: x0, size, r = 0, 1024, 0
    else: x0, size, r = 100, 824, 185.4
    sc = size * 0.52 / 180; ox = x0 + size / 2 - 128 * sc; oy = x0 + size / 2 - 127 * sc
    tile = f'<rect width="1024" height="1024" fill="{bg}"/>' if full_bleed else f'<path fill="{bg}" d="{rr(x0, x0, size, size, r)}"/>'
    return svg(1024, 1024, tile + f'<path fill="{fg}" fill-rule="evenodd" transform="translate({f(ox)} {f(oy)}) scale({f(sc)})" d="{d}"/>', 'a-msg')
for name, bg, fg in [('colour', OLIVE, PAPER), ('dark', INK, PAPER), ('light', PAPER, OLIVE)]:
    write(f'app-icon/amsg-app-icon-{name}.svg', app_icon(bg, fg))
    write(f'app-icon/amsg-app-icon-{name}-square.svg', app_icon(bg, fg, full_bleed=True))
print('written to', os.path.relpath(OUT))
