import os, json

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')

DARK = dict(
    bg0='#101114', bg1='#15171b', bg2='#1b1d22', bg3='#23262c', bg4='#2d3138',
    line='#2a2e35', line2='#3b404a', t1='#eceef1', t2='#b0b5bf', t3='#8b919c',
    acc='#3fbfae', acc_bg='#143630', acc_line='#2c8f82', on_acc='#04211d',
    amber='#f0b03c', amber_bg='#2b2210', amber_line='#6f5519', amber_t='#f6d58f',
    beat='#ff3b30', well='#0b0c0e', scrim='rgba(0,0,0,.55)', shadow='rgba(0,0,0,.5)',
    pip_on='#eceef1', pip_on_t='#101114', kbd_pri='rgba(0,0,0,.18)',
)
LIGHT = dict(
    bg0='#eceef1', bg1='#f7f8fa', bg2='#ffffff', bg3='#eef0f3', bg4='#dde1e6',
    line='#d8dce2', line2='#b9bec8', t1='#181a1f', t2='#444a54', t3='#5b626e',
    acc='#0d7c70', acc_bg='#d8f0ec', acc_line='#0d7c70', on_acc='#ffffff',
    amber='#8a5600', amber_bg='#fff0cc', amber_line='#d6a237', amber_t='#5a3900',
    beat='#e5271d', well='#dfe3e8', scrim='rgba(20,22,26,.35)', shadow='rgba(20,22,26,.25)',
    pip_on='#181a1f', pip_on_t='#ffffff', kbd_pri='rgba(255,255,255,.22)',
)


class _K:
    pass


K = _K()


def set_theme(d):
    K.__dict__.update(d)


set_theme(DARK)

FONT = "font-family:'Cantarell',system-ui,sans-serif"
MONO = "font-family:ui-monospace,'SFMono-Regular',Menlo,Consolas,monospace"


def hexrgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, t):
    ra, rb = hexrgb(a), hexrgb(b)
    return '#%02x%02x%02x' % tuple(round(x + (y - x) * t) for x, y in zip(ra, rb))


def css():
    return f'''body{{margin:0}}
*{{box-sizing:border-box}}
button{{font:inherit;color:inherit;cursor:pointer;margin:0;padding:0}}
button:disabled{{cursor:default}}
button:focus-visible,input:focus-visible,select:focus-visible{{outline:2px solid {K.acc};outline-offset:2px}}
@keyframes dcspin{{to{{transform:rotate(360deg)}}}}
.spin{{animation:dcspin .9s linear infinite;transform-origin:center}}
input.rng{{-webkit-appearance:none;appearance:none;display:block;width:100%;height:28px;margin:0;background:transparent}}
input.rng::-webkit-slider-runnable-track{{height:8px;border-radius:4px;background:linear-gradient(to right,{K.acc} var(--p),{K.bg4} var(--p))}}
input.rng::-webkit-slider-thumb{{-webkit-appearance:none;appearance:none;width:24px;height:24px;margin-top:-8px;border-radius:50%;background:{K.t1};border:0}}
input.rng::-moz-range-track{{height:8px;border-radius:4px;background:{K.bg4}}}
input.rng::-moz-range-progress{{height:8px;border-radius:4px;background:{K.acc}}}
input.rng::-moz-range-thumb{{width:24px;height:24px;border-radius:50%;background:{K.t1};border:0}}
input.sw{{-webkit-appearance:none;appearance:none;position:relative;width:46px;height:26px;margin:0;border-radius:13px;background:{K.bg4};border:1px solid {K.line2};flex:none;cursor:pointer}}
input.sw::after{{content:"";position:absolute;top:2px;left:2px;width:20px;height:20px;border-radius:50%;background:{K.t1}}}
input.sw:checked{{background:{K.acc};border-color:{K.acc}}}
input.sw:checked::after{{left:22px;background:{K.on_acc}}}
input.sw:disabled{{opacity:.45}}
select.dd{{-webkit-appearance:none;appearance:none;width:100%;background:{K.bg3};color:{K.t1};border:1px solid {K.line2};border-radius:8px;padding:0 32px 0 12px;font:inherit;font-size:14px}}
select.dd:disabled{{opacity:.45}}
input.tf{{background:{K.bg0};color:{K.t1};border:1px solid {K.line2};border-radius:8px;font:inherit;font-size:14px;padding:0 10px}}'''


def page(title, w, h, body):
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<script src="./support.js"></script>
</head>
<body>
<x-dc>
<helmet>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Cantarell:wght@400;700&amp;display=swap">
<style>
{css()}
</style>
</helmet>
{body}
</x-dc>
<script type="text/x-dc" data-dc-script data-props='{{"$preview":{{"width":{w},"height":{h}}}}}'>
class Component extends DCLogic {{
renderVals() {{ return {{}}; }}
}}
</script>
</body>
</html>
'''


def root(w, h, inner, bg=None, extra=''):
    return (f'<div style="width:{w}px;height:{h}px;background:{bg or K.bg0};color:{K.t1};{FONT};font-size:14px;'
            f'line-height:1.35;font-variant-numeric:tabular-nums;display:flex;flex-direction:column;position:relative;'
            f'overflow:hidden;{extra}">{inner}</div>')


# ---------- icons ----------
P = {
    'left': '<path d="M10 3 5 8l5 5"/>',
    'right': '<path d="m6 3 5 5-5 5"/>',
    'up': '<path d="m3 10 5-5 5 5"/>',
    'down': '<path d="m3 6 5 5 5-5"/>',
    'menu': '<path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11"/>',
    'plus': '<path d="M8 3v10M3 8h10"/>',
    'minus': '<path d="M3 8h10"/>',
    'check': '<path d="m3 8.5 3.5 3.5L13 4.5"/>',
    'warn': '<path d="M8 2.5 14 13H2z"/><path d="M8 6.5v3M8 11.3v.2"/>',
    'x': '<path d="m4 4 8 8M12 4l-8 8"/>',
    'refresh': '<path d="M13 8a5 5 0 1 1-1.6-3.7"/><path d="M13 2.5v3h-3"/>',
    'folder': '<path d="M2 4.5h4l1.5 1.5H14v6.5H2z"/>',
    'edit': '<path d="m3 13 .7-3L11 2.7 13.3 5 6 12.3z"/>',
    'trash': '<path d="M3 4.5h10M6 4.5V3h4v1.5M4.5 4.5l.5 8.5h6l.5-8.5"/>',
    'copy': '<path d="M5.5 5.5h7v8h-7z"/><path d="M3.5 10.5v-8h7"/>',
    'ext': '<path d="M9 3h4v4M13 3 7 9M11 9.5V13H3V5h3.5"/>',
    'chev': '<path d="m4 6 4 4 4-4"/>',
    'chevr': '<path d="m6 4 4 4-4 4"/>',
    'grid': '<path d="M2.5 2.5h4.5v4.5H2.5zM9 2.5h4.5v4.5H9zM2.5 9h4.5v4.5H2.5zM9 9h4.5v4.5H9z"/>',
    'sliders': '<path d="M3 5h6M12 5h1M3 11h1M7 11h6"/><circle cx="10.5" cy="5" r="1.5"/><circle cx="5.5" cy="11" r="1.5"/>',
    'grip': '<path d="M6 4v.1M10 4v.1M6 8v.1M10 8v.1M6 12v.1M10 12v.1"/>',
    'info': '<circle cx="8" cy="8" r="6"/><path d="M8 7.3v3.7M8 5v.1"/>',
    'midi': '<circle cx="8" cy="8" r="6"/><path d="M5.2 6.3v.1M8 4.9v.1M10.8 6.3v.1M6 10.5v.1M10 10.5v.1"/>',
}
FILLED = {
    'play': '<path d="M4.5 2.8 13 8l-8.5 5.2z"/>',
    'stop': '<rect x="3.5" y="3.5" width="9" height="9" rx="1.6"/>',
}


def ic(n, s=16, sw=1.6, extra=''):
    if n in FILLED:
        return (f'<svg width="{s}" height="{s}" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true" '
                f'style="flex:none;{extra}">{FILLED[n]}</svg>')
    return (f'<svg width="{s}" height="{s}" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="{sw}" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="flex:none;{extra}">{P[n]}</svg>')


def spin(s=16):
    return (f'<svg class="spin" width="{s}" height="{s}" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" '
            f'stroke-linecap="round" aria-hidden="true" style="flex:none"><circle cx="8" cy="8" r="6" stroke-opacity=".25"/>'
            f'<path d="M8 2a6 6 0 0 1 6 6"/></svg>')


# ---------- atoms ----------
def kbd(t, tone='n'):
    if tone == 'pri':
        st = f'background:{K.kbd_pri};color:{K.on_acc};border:1px solid transparent'
    else:
        st = f'background:{K.bg4};color:{K.t2};border:1px solid {K.line2}'
    return (f'<span style="display:inline-flex;align-items:center;justify-content:center;min-width:20px;height:20px;'
            f'padding:0 6px;border-radius:5px;font-size:12px;font-weight:700;line-height:1;flex:none;{st}">{t}</span>')


def btn(label='', kind='sec', icon=None, key=None, h=36, w=None, dis=False, size=14, px=14, aria=None, extra=''):
    styles = {
        'pri': f'background:{K.acc};color:{K.on_acc};border:1px solid {K.acc};font-weight:700',
        'sec': f'background:{K.bg3};color:{K.t1};border:1px solid {K.line2}',
        'ghost': f'background:transparent;color:{K.t2};border:1px solid transparent',
        'amber': f'background:transparent;color:{K.amber_t};border:1px solid {K.amber_line}',
        'acc': f'background:{K.acc_bg};color:{K.t1};border:1px solid {K.acc_line}',
    }
    wd = ''
    if w is not None:
        wd = f'width:{w};' if isinstance(w, str) else f'width:{w}px;'
    a = f' aria-label="{aria}"' if aria else ''
    d = ' disabled' if dis else ''
    op = 'opacity:.45;' if dis else ''
    return (f'<button type="button"{a}{d} style="height:{h}px;{wd}padding:0 {px}px;border-radius:9px;{styles[kind]};'
            f'font-size:{size}px;display:inline-flex;align-items:center;justify-content:center;gap:8px;flex:none;'
            f'white-space:nowrap;{op}{extra}">'
            f'{ic(icon, 16) if icon else ""}{label}{kbd(key, "pri" if kind == "pri" else "n") if key else ""}</button>')


def ibtn(icon, aria, s=36, kind='sec', dis=False, isz=16):
    bg = {'sec': f'background:{K.bg3};border:1px solid {K.line2};color:{K.t1}',
          'ghost': f'background:transparent;border:1px solid transparent;color:{K.t2}'}[kind]
    op = 'opacity:.45;' if dis else ''
    d = ' disabled' if dis else ''
    return (f'<button type="button" aria-label="{aria}"{d} style="width:{s}px;height:{s}px;border-radius:9px;{bg};'
            f'display:inline-flex;align-items:center;justify-content:center;flex:none;{op}">{ic(icon, isz)}</button>')


def chip(text, tone='n', icon=None, dot=False, size=12, ring=False):
    st = {
        'n': f'background:{K.bg3};border:1px solid {K.line2};color:{K.t2}',
        'amber': f'background:{K.amber_bg};border:1px solid {K.amber_line};color:{K.amber_t}',
        'acc': f'background:{K.acc_bg};border:1px solid {K.acc_line};color:{K.t1}',
        'solid': f'background:{K.acc};border:1px solid {K.acc};color:{K.on_acc};font-weight:700',
        'dash': f'background:transparent;border:1px dashed {K.acc};color:{K.t1}',
    }[tone]
    lead = ''
    if icon:
        lead = ic(icon, 13)
    elif dot:
        lead = (f'<span style="width:8px;height:8px;border-radius:50%;background:{K.acc};flex:none"></span>' if not ring else
                f'<span style="width:8px;height:8px;border-radius:50%;border:1.5px solid {K.t3};flex:none"></span>')
    return (f'<span style="display:inline-flex;align-items:center;gap:6px;height:22px;padding:0 8px;border-radius:6px;'
            f'font-size:{size}px;white-space:nowrap;flex:none;{st}">{lead}{text}</span>')


def seg(opts, active, h=36, w=None, dis=False):
    inner = ''
    for i, o in enumerate(opts):
        on = i == active
        st = (f'background:{K.acc_bg};color:{K.t1};border:1px solid {K.acc_line};font-weight:700' if on else
              f'background:transparent;color:{K.t2};border:1px solid transparent')
        inner += (f'<button type="button" aria-pressed="{"true" if on else "false"}"{" disabled" if dis else ""} '
                  f'style="flex:1;height:{h - 8}px;border-radius:7px;font-size:14px;padding:0 14px;display:flex;'
                  f'align-items:center;justify-content:center;white-space:nowrap;{st}">{o}</button>')
    wd = ''
    if w is not None:
        wd = f'width:{w};' if isinstance(w, str) else f'width:{w}px;'
    op = 'opacity:.5;' if dis else ''
    return (f'<div style="display:flex;gap:2px;padding:3px;border-radius:10px;background:{K.bg0};border:1px solid {K.line2};'
            f'height:{h}px;flex:none;{wd}{op}">{inner}</div>')


def slider(pct, aria, dis=False):
    return (f'<input class="rng" type="range" min="0" max="100" value="{pct}" aria-label="{aria}"'
            f'{" disabled" if dis else ""} style="--p:{pct}%;{"opacity:.45;" if dis else ""}">')


def switch(on, aria, dis=False):
    return f'<input class="sw" type="checkbox" aria-label="{aria}"{" checked" if on else ""}{" disabled" if dis else ""}>'


def dd(text, w=None, h=36, dis=False, aria=None):
    wd = 'width:100%;' if w is None else (f'width:{w}px;' if not isinstance(w, str) else f'width:{w};')
    return (f'<div style="position:relative;flex:none;{wd}height:{h}px">'
            f'<select class="dd" aria-label="{aria or text}"{" disabled" if dis else ""} style="height:{h}px"><option>{text}</option></select>'
            f'<span style="position:absolute;right:10px;top:50%;margin-top:-8px;pointer-events:none;color:{K.t2};display:flex">{ic("chev", 16)}</span></div>')


def spinf(text, w=150, dis=False, h=36, aria='Value'):
    op = 'opacity:.45;' if dis else ''
    d = ' disabled' if dis else ''
    return (f'<div style="display:flex;align-items:center;height:{h}px;width:{w}px;flex:none;border-radius:9px;background:{K.bg3};'
            f'border:1px solid {K.line2};overflow:hidden;{op}">'
            f'<button type="button" aria-label="Decrease {aria}"{d} style="width:34px;height:100%;border:0;background:transparent;display:flex;align-items:center;justify-content:center">{ic("minus", 14)}</button>'
            f'<span style="flex:1;text-align:center;white-space:nowrap;font-size:14px">{text}</span>'
            f'<button type="button" aria-label="Increase {aria}"{d} style="width:34px;height:100%;border:0;background:transparent;display:flex;align-items:center;justify-content:center">{ic("plus", 14)}</button></div>')


def numfield(val, unit, w=150, aria='Value', h=40):
    return (f'<div style="display:flex;align-items:center;gap:8px;flex:none">'
            f'<input class="tf" type="text" inputmode="decimal" value="{val}" aria-label="{aria}" style="width:{w}px;height:{h}px;text-align:right;font-size:18px;font-weight:700">'
            f'<span style="color:{K.t2};font-size:14px">{unit}</span></div>')


def colorbtn(hexv, w='100%'):
    wd = f'width:{w};' if isinstance(w, str) else f'width:{w}px;'
    return (f'<button type="button" aria-label="Colour {hexv}" style="{wd}height:40px;border-radius:9px;background:{K.bg3};border:1px solid {K.line2};'
            f'display:flex;align-items:center;gap:10px;padding:0 10px;flex:none">'
            f'<span style="width:24px;height:24px;border-radius:6px;background:{hexv};border:1px solid {K.line2};flex:none"></span>'
            f'<span style="flex:1;text-align:left;font-size:14px">{hexv.upper()}</span>{ic("chev", 16)}</button>')


def banner(kind, text, btn_label=None, w=None):
    if kind == 'amber':
        bg, bd, col, lead = K.amber_bg, K.amber_line, K.amber_t, ic('warn', 18)
        bk = 'amber'
    else:
        bg, bd, col, lead = K.acc_bg, K.acc_line, K.t1, spin(18) if kind == 'busy' else ic('info', 18)
        bk = 'acc'
    b = btn(btn_label, bk, h=32, size=13) if btn_label else ''
    return (f'<div style="flex:none;min-height:44px;display:flex;align-items:center;gap:10px;padding:6px 12px 6px 16px;'
            f'background:{bg};border-bottom:1px solid {bd};color:{col}">{lead}'
            f'<div style="flex:1;font-size:14px">{text}</div>{b}</div>')


# ---------- boxed lists (AdwPreferencesGroup) ----------
def group(title, desc, rows, gap=8):
    head = f'<div style="display:flex;flex-direction:column;gap:2px"><div style="font-size:15px;font-weight:700">{title}</div>'
    if desc:
        head += f'<div style="font-size:13px;color:{K.t2}">{desc}</div>'
    head += '</div>'
    body = ''
    for i, r in enumerate(rows):
        bt = f'border-top:1px solid {K.line};' if i else ''
        body += f'<div style="{bt}">{r}</div>'
    return (f'<section style="display:flex;flex-direction:column;gap:{gap}px;flex:none">{head}'
            f'<div style="background:{K.bg2};border:1px solid {K.line};border-radius:12px;overflow:hidden">{body}</div></section>')


def row(title, sub=None, suffix='', lead='', h=56, sel=False):
    s = f'<div style="font-size:12.5px;color:{K.t3};margin-top:1px">{sub}</div>' if sub else ''
    ring = f'box-shadow:inset 0 0 0 2px {K.acc};' if sel else ''
    return (f'<div style="display:flex;align-items:center;gap:12px;min-height:{h}px;padding:8px 14px;{ring}">{lead}'
            f'<div style="flex:1;min-width:0"><div style="font-size:14px">{title}</div>{s}</div>'
            f'<div style="display:flex;align-items:center;gap:8px;flex:none">{suffix}</div></div>')


def status_page(icon, title, text, buttons='', tsize=20):
    return (f'<div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;text-align:center;padding:12px 20px">'
            f'<div style="width:52px;height:52px;border-radius:26px;background:{K.bg3};border:1px solid {K.line2};display:flex;align-items:center;justify-content:center;color:{K.t2}">{ic(icon, 24)}</div>'
            f'<div style="font-size:{tsize}px;font-weight:700">{title}</div>'
            f'<div style="font-size:14px;color:{K.t2};max-width:300px">{text}</div>'
            f'{"<div style=\"display:flex;gap:8px;margin-top:6px\">" + buttons + "</div>" if buttons else ""}</div>')


def panel(inner, extra=''):
    return (f'<div style="background:{K.bg2};border:1px solid {K.line};border-radius:12px;padding:14px;display:flex;'
            f'flex-direction:column;gap:12px;min-height:0;{extra}">{inner}</div>')


def write(name, w, h, title, body):
    os.makedirs(OUT, exist_ok=True)
    with open(f'{OUT}/{name}', 'w') as f:
        f.write(page(title, w, h, body))
