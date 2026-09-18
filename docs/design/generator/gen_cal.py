from gen_lib import *

CX, CY, R0, SC = 150, 107, 84, 84 / 387.0


def fx(px):
    return CX + (px - 960) * SC


def fy(py):
    return CY + (py - 540) * SC


def kbtn(icon, key, dis=False, w=76, label=None):
    op = 'opacity:.45;' if dis else ''
    d = ' disabled' if dis else ''
    lab = label or icon
    return (f'<button type="button" aria-label="{lab}"{d} style="width:{w}px;height:40px;border-radius:9px;background:{K.bg3};border:1px solid {K.line2};'
            f'display:flex;align-items:center;justify-content:center;gap:8px;flex:none;{op}">{ic(icon, 18)}{kbd(key)}</button>')


def stepper(active):
    items = [('Silhouette', False), ('Ball axis', True), ('Tile rows', True)]
    out = ''
    for i, (n, opt) in enumerate(items, 1):
        done, on = i < active, i == active
        if done:
            c = f'background:{K.acc};color:{K.on_acc};border:2px solid {K.acc}'
            inner = ic('check', 14, 2.4)
        elif on:
            c = f'background:{K.acc_bg};color:{K.t1};border:2px solid {K.acc}'
            inner = str(i)
        else:
            c = f'background:{K.bg3};color:{K.t3};border:2px solid {K.line2}'
            inner = str(i)
        sub = f'<span style="font-size:12px;color:{K.t3}">optional</span>' if opt else ''
        out += (f'<div style="display:flex;align-items:center;gap:8px"><span style="width:26px;height:26px;border-radius:13px;display:flex;align-items:center;'
                f'justify-content:center;font-size:13px;font-weight:700;{c}">{inner}</span><span style="font-size:14px;font-weight:{700 if on else 400};'
                f'color:{K.t1 if on else K.t2}">{n}</span>{sub}</div>')
        if i < 3:
            out += f'<div style="width:36px;height:2px;background:{K.line2};flex:none"></div>'
    return (f'<div style="height:44px;flex:none;display:flex;align-items:center;justify-content:center;gap:14px;background:{K.bg1};'
            f'border-bottom:1px solid {K.line}">{out}</div>')


def cal_header(dirty):
    ch = chip('Edited', 'acc', dot=True) if dirty else ''
    return (f'<div style="height:44px;flex:none;display:flex;align-items:center;padding:0 12px 0 16px;background:{K.bg1}">'
            f'<div style="flex:1">{ch}</div><div style="font-weight:700;font-size:15px">Calibrate output</div><div style="flex:1"></div></div>')


def footer(step, dis=False):
    cancel = btn('Cancel', 'sec', key='Esc', h=40)
    if step == 1:
        left = btn('Reset to defaults', 'amber', h=40, dis=dis)
        right = cancel + btn('Advanced…', 'sec', h=40, icon='chevr') + btn('Finish', 'pri', key='Enter', h=40, px=18, dis=dis)
    elif step == 2:
        left = btn('Back', 'ghost', icon='left', h=40)
        right = cancel + btn('Skip', 'sec', h=40) + btn('Next', 'pri', key='Enter', h=40, px=18)
    else:
        left = btn('Back', 'ghost', icon='left', h=40)
        right = cancel + btn('Finish', 'pri', key='Enter', h=40, px=18)
    return (f'<div style="height:56px;flex:none;display:flex;align-items:center;gap:8px;padding:0 16px;background:{K.bg1};border-top:1px solid {K.line}">'
            f'{left}<div style="flex:1"></div>{right}</div>')


def shell(step, instr, left, right, dirty=False, banner_html='', dis=False):
    body = (f'<div style="flex:1;min-height:0;display:flex;flex-direction:column;padding:0 20px 10px">'
            f'<div style="height:34px;flex:none;display:flex;align-items:center;font-size:15px">{instr}</div>'
            f'<div style="flex:1;min-height:0;display:flex;gap:16px"><div style="width:300px;flex:none;display:flex;flex-direction:column;gap:8px">{left}</div>'
            f'<div style="flex:1;min-width:0;display:flex;flex-direction:column;gap:8px">{right}</div></div></div>')
    return root(720, 560, cal_header(dirty) + stepper(step) + banner_html + body + footer(step, dis),
                extra=f'border:1px solid {K.line2};border-radius:14px')


def diagram_card(svg):
    return (f'<div style="height:214px;flex:none;border-radius:12px;background:{K.bg1};border:1px solid {K.line};display:flex;align-items:center;justify-content:center">{svg}</div>')


def ball_defs(uid):
    return (f'<defs><pattern id="tl{uid}" width="11" height="11" patternUnits="userSpaceOnUse"><path d="M11 0H0V11" fill="none" stroke="{K.line2}" stroke-width=".7"/></pattern></defs>')


def diagram1(L, R, T, B, active='L', warn=False, uid='a'):
    xl, xr, yt, yb = fx(L), fx(R), fy(T), fy(B)
    ex = amb = K.amber if warn else K.t1
    lines = ''
    for key, kind, v in (('L', 'v', xl), ('R', 'v', xr), ('T', 'h', yt), ('B', 'h', yb)):
        on = key == active
        col, wd = (K.acc, 2.6) if on else (K.t2, 1.4)
        if kind == 'v':
            lines += f'<path d="M{v:.1f} 16V198" stroke="{col}" stroke-width="{wd}"/><text x="{v:.1f}" y="11" text-anchor="middle" font-size="11" font-weight="700" fill="{col}">{key}</text>'
        else:
            lines += f'<path d="M18 {v:.1f}H282" stroke="{col}" stroke-width="{wd}"/><text x="8" y="{v + 4:.1f}" text-anchor="middle" font-size="11" font-weight="700" fill="{col}">{key}</text>'
    ecx, ecy, erx, ery = (xl + xr) / 2, (yt + yb) / 2, (xr - xl) / 2, (yb - yt) / 2
    return (f'<svg width="300" height="214" viewBox="0 0 300 214" aria-label="Ball with four calibration lines and the fitted ellipse" style="font-family:\'Cantarell\',system-ui,sans-serif">'
            f'{ball_defs(uid)}<circle cx="{CX}" cy="{CY}" r="{R0}" fill="{K.bg3}" stroke="{K.line2}" stroke-width="1.5"/>'
            f'<circle cx="{CX}" cy="{CY}" r="{R0}" fill="url(#tl{uid})"/>{lines}'
            f'<ellipse cx="{ecx:.1f}" cy="{ecy:.1f}" rx="{erx:.1f}" ry="{ery:.1f}" fill="none" stroke="{ex}" stroke-width="1.6" stroke-dasharray="5 4"/>'
            f'<path d="M{ecx - 5:.1f} {ecy:.1f}h10M{ecx:.1f} {ecy - 5:.1f}v10" stroke="{ex}" stroke-width="1.6"/></svg>')


def line_card(name, orient, px, total, keys, active=False, dis=False):
    icons = ('left', 'right') if orient == 'h' else ('up', 'down')
    pct = px / total * 100
    ring = f'box-shadow:inset 0 0 0 2px {K.acc};' if active else ''
    return (f'<div style="display:flex;flex-direction:column;gap:6px;padding:8px;border-radius:11px;background:{K.bg2};border:1px solid {K.line};{ring}">'
            f'<div style="display:flex;justify-content:space-between;align-items:baseline"><span style="font-size:14px;font-weight:700">{name}</span>'
            f'<span style="font-size:12.5px;color:{K.t2}">{px} px · {pct:.1f} %</span></div>'
            f'<div style="display:flex;gap:6px">{kbtn(icons[0], keys[0], dis, 76, name + " line " + icons[0])}{kbtn(icons[1], keys[1], dis, 76, name + " line " + icons[1])}</div></div>')


VALS = {
    'default': (560, 1360, 140, 940),
    'edited': (578, 1352, 152, 930),
    'warn': (578, 1390, 152, 930),
}


def step_row(active_idx=1, dis=False, w='100%'):
    return seg(['Fine', 'Normal', 'Coarse'], active_idx, h=40, w=w, dis=dis)


def card(title, inner, cap='', extra=''):
    c = f'<span style="font-size:12.5px;color:{K.t3}">{cap}</span>' if cap else ''
    return (f'<div style="flex:1;min-width:0;display:flex;flex-direction:column;gap:6px;padding:8px;border-radius:11px;background:{K.bg2};border:1px solid {K.line};{extra}">'
            f'<div style="display:flex;justify-content:space-between;align-items:baseline"><span style="font-size:14px;font-weight:700">{title}</span>{c}</div>{inner}</div>')


def toggle(label, key, on=False, dis=False):
    st = (f'background:{K.acc_bg};border:1px solid {K.acc_line};color:{K.t1};font-weight:700' if on else f'background:{K.bg3};border:1px solid {K.line2};color:{K.t2}')
    return (f'<button type="button" aria-pressed="{"true" if on else "false"}"{" disabled" if dis else ""} style="flex:1;min-width:0;height:36px;border-radius:9px;{st};display:flex;align-items:center;'
            f'justify-content:center;gap:8px;font-size:13.5px;{"opacity:.45;" if dis else ""}">{label}{kbd(key)}</button>')


def cal1(state='default', banner_kind=None):
    L, R, T, B = VALS['edited' if state in ('edited', 'nofs', 'starting') else state]
    if state == 'default':
        L, R, T, B = VALS['default']
    warn = state == 'warn'
    dirty = state in ('edited', 'warn', 'nofs')
    dis = state == 'starting'
    active = 'R' if warn else 'L' if dirty else 'L'
    w, h = R - L, B - T
    cxp, cyp = (L + R) // 2, (T + B) // 2
    dev = abs(w / h - 1) * 100
    svg = diagram_card(diagram1(L, R, T, B, active, warn, state[:3]))
    toggles = f'<div style="display:flex;gap:8px;flex:none">{toggle("Blink", "B", False, dis)}{toggle("Verification grid", "G", state == "nofs", dis)}</div>'
    if warn:
        asp = (f'<div style="display:flex;gap:8px;align-items:flex-start;color:{K.amber_t}">{ic("warn", 16, 1.8, "margin-top:2px")}'
               f'<span style="font-size:13px">Aspect off by {dev:.1f} %. The ball should look round: re-check Left and Right.</span></div>')
        box_bd = K.amber_line
        box_bg = K.amber_bg
    else:
        asp = f'<div style="display:flex;justify-content:space-between;font-size:13.5px"><span style="color:{K.t2}">Aspect</span><span>{w / h:.3f}</span></div>'
        box_bd, box_bg = K.line, K.bg2
    read = (f'<div style="flex:1;min-height:0;display:flex;flex-direction:column;justify-content:center;gap:3px;padding:8px 12px;border-radius:11px;background:{box_bg};border:1px solid {box_bd}">'
            f'<div style="display:flex;justify-content:space-between;font-size:13.5px"><span style="color:{K.t2}">Centre</span><span>{cxp}, {cyp} px</span></div>'
            f'<div style="display:flex;justify-content:space-between;font-size:13.5px"><span style="color:{K.t2}">Size</span><span>{w} × {h} px</span></div>{asp}</div>')
    left = svg + toggles + read
    grid = (f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;flex:none">'
            f'{line_card("Left", "h", L, 1920, ("A", "D"), active == "L", dis)}{line_card("Right", "h", R, 1920, ("J", "L"), active == "R", dis)}'
            f'{line_card("Top", "v", T, 1080, ("W", "S"), False, dis)}{line_card("Bottom", "v", B, 1080, ("I", "K"), False, dis)}</div>')
    mv = (f'<div style="display:flex;gap:8px;flex:none">'
          + card('Move ball', '<div style="display:flex;gap:4px">' + ''.join(
              f'<button type="button" aria-label="Move {n}"{" disabled" if dis else ""} style="flex:1;height:40px;border-radius:9px;background:{K.bg3};border:1px solid {K.line2};display:flex;align-items:center;justify-content:center;{"opacity:.45;" if dis else ""}">{ic(i, 18)}</button>'
              for i, n in (('left', 'left'), ('up', 'up'), ('down', 'down'), ('right', 'right'))) + '</div>', 'arrow keys')
          + card('Grow / Shrink', f'<div style="display:flex;gap:6px">{kbtn("minus", "−", dis, 72, "Shrink")}{kbtn("plus", "+", dis, 72, "Grow")}</div>')
          + '</div>')
    step = f'<div style="display:flex;align-items:center;gap:10px;flex:none"><span style="font-size:13.5px;color:{K.t2}">Step</span>{step_row(1, dis, "100%")}</div>'
    right = grid + mv + step
    bn = ''
    if state == 'starting':
        bn = banner('busy', 'Starting the renderer. The lines appear on the projector in a moment.')
    elif state == 'nofs':
        bn = banner('amber', 'The output window is not fullscreen yet. Move it to the projector and press F11.')
    return shell(1, 'Move each line until it just touches the edge of the ball.', left, right, dirty, bn, dis)


def cal2():
    svg = (f'<svg width="300" height="214" viewBox="0 0 300 214" aria-label="Ball with equator and aiming crosshair" style="font-family:\'Cantarell\',system-ui,sans-serif">'
           f'{ball_defs("b")}<circle cx="150" cy="118" r="80" fill="{K.bg3}" stroke="{K.line2}" stroke-width="1.5"/><circle cx="150" cy="118" r="80" fill="url(#tlb)"/>'
           f'<ellipse cx="150" cy="118" rx="80" ry="20" fill="none" stroke="{K.t2}" stroke-width="1.6"/>'
           f'<ellipse cx="150" cy="118" rx="34" ry="80" fill="none" stroke="{K.line2}" stroke-width="1"/>'
           f'<path d="M150 20v196" stroke="{K.line2}" stroke-width="1" stroke-dasharray="3 4"/>'
           f'<circle cx="172" cy="94" r="13" fill="none" stroke="{K.acc}" stroke-width="2.4"/><path d="M172 74v40M152 94h40" stroke="{K.acc}" stroke-width="2"/>'
           f'<path d="M172 10v12" stroke="{K.t2}" stroke-width="1.4" stroke-dasharray="3 3"/><rect x="152" y="2" width="40" height="10" rx="3" fill="{K.t2}"/>'
           f'<text x="30" y="122" font-size="11" fill="{K.t2}">equator</text></svg>')
    left = (diagram_card(svg) +
            f'<div style="flex:1;display:flex;flex-direction:column;justify-content:center;gap:3px;padding:8px 12px;border-radius:11px;background:{K.bg2};border:1px solid {K.line}">'
            f'<div style="display:flex;justify-content:space-between;font-size:13.5px"><span style="color:{K.t2}">Beam hits</span><span>24° above equator</span></div>'
            f'<div style="display:flex;justify-content:space-between;font-size:13.5px"><span style="color:{K.t2}">Left / right of centre</span><span>8° right</span></div></div>')
    arrows = ''.join(
        f'<button type="button" aria-label="Move crosshair {n}" style="flex:1;height:44px;border-radius:9px;background:{K.bg3};border:1px solid {K.line2};display:flex;align-items:center;justify-content:center;gap:8px">{ic(i, 18)}{kbd(k)}</button>'
        for i, n, k in (('left', 'left', '←'), ('up', 'up', '↑'), ('down', 'down', '↓'), ('right', 'right', '→')))
    right = (card('Crosshair', f'<div style="display:flex;gap:4px">{arrows}</div>', 'arrow keys')
             + f'<div style="display:flex;align-items:center;gap:10px;flex:none"><span style="font-size:13.5px;color:{K.t2}">Step</span>{step_row(1)}</div>'
             + card('Projector position', seg(['Above the equator', 'Below the equator'], 0, h=40, w='100%'))
             + card('Numeric fallback',
                    f'<div style="display:flex;align-items:center;justify-content:space-between;font-size:13.5px"><span>Height above equator</span>{spinf("<b>35</b> cm", 130, aria="height")}</div>'
                    f'<div style="display:flex;align-items:center;justify-content:space-between;font-size:13.5px"><span>Distance to ball</span>{spinf("<b>2.40</b> m", 130, aria="distance")}</div>', 'if the crosshair is hard to judge'))
    return shell(2, 'Move the crosshair to where the projector beam meets the ball.', left, right, True)


def cal3():
    bands = ''
    for k in range(17):
        y = 27 + k * 10
        bands += f'<rect x="60" y="{y}" width="180" height="10" fill="{K.bg4 if k % 2 else K.bg2}"/>'
    svg = (f'<svg width="300" height="214" viewBox="0 0 300 214" aria-label="Ball with alternating tile rows" style="font-family:\'Cantarell\',system-ui,sans-serif">'
           f'<defs><clipPath id="clc"><circle cx="150" cy="107" r="84"/></clipPath></defs><g clip-path="url(#clc)">{bands}</g>'
           f'<circle cx="150" cy="107" r="84" fill="none" stroke="{K.line2}" stroke-width="1.5"/>'
           f'<path d="M58 107h184" stroke="{K.acc}" stroke-width="2"/><text x="248" y="111" font-size="11" fill="{K.acc}">equator</text></svg>')
    left = (diagram_card(svg) +
            f'<div style="flex:1;display:flex;flex-direction:column;justify-content:center;gap:3px;padding:8px 12px;border-radius:11px;background:{K.bg2};border:1px solid {K.line}">'
            f'<div style="display:flex;justify-content:space-between;font-size:13.5px"><span style="color:{K.t2}">Result</span><span style="font-weight:700">rows ≈ 47.3 · tile 3.8°</span></div>'
            f'<div style="font-size:12.5px;color:{K.t3}">Read only. Calculated from offset and pitch.</div></div>')

    def nud(title, pair, cap):
        return card(title, f'<div style="display:flex;gap:6px">{kbtn(pair[0][0], pair[0][1], False, 90, title + " " + pair[0][0])}{kbtn(pair[1][0], pair[1][1], False, 90, title + " " + pair[1][0])}</div>', cap)
    right = (f'<div style="display:flex;gap:8px;flex:none">{nud("Offset", (("up", "↑"), ("down", "↓")), "up / down")}{nud("Pitch", (("left", "←"), ("right", "→")), "narrower / wider")}</div>'
             + f'<div style="display:flex;align-items:center;gap:10px;flex:none"><span style="font-size:13.5px;color:{K.t2}">Step</span>{step_row(1)}</div>'
             + card('Pattern', seg(['Zebra', 'Single row'], 0, h=40, w='100%'), 'shown on the ball')
             + f'<div style="display:flex;align-items:center;gap:10px;flex:none;padding:8px 12px;border-radius:11px;background:{K.bg2};border:1px solid {K.line}">'
               f'<span style="font-size:13.5px;color:{K.t2}">Rows / tile size</span><div style="flex:1"></div><span style="font-size:15px;font-weight:700">rows ≈ 47.3 · tile 3.8°</span></div>')
    return shell(3, 'Nudge the rows until the projected stripes line up with the mirror tiles.', left, right, True)
