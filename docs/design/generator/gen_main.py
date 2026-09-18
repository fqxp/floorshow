from gen_lib import *

SCENES = [
    ('Aurora Bands', ['bands', 'slow']), ('Halo Rings', ['rings']), ('Spot Chase', ['spots', 'fast']),
    ('Strobe Grid', ['bands', 'fast']), ('Slow Sweep', ['bands', 'calm']), ('Pulse Ring', ['rings', 'beat']),
    ('Confetti Spots', ['spots']), ('Equator Line', ['bands', 'calm']),
]


def scene_card(i, st='default', w=None, h=None):
    name, tags = SCENES[i]
    shadow = ''
    if st == 'active':
        bd, bg = f'2px solid {K.acc}', K.acc_bg
    elif st == 'next':
        bd, bg = f'2px dashed {K.acc}', K.bg3
    elif st == 'error':
        bd, bg = f'2px solid {K.amber_line}', K.bg3
    elif st == 'focus':
        bd, bg = f'2px solid {K.line2}', K.bg3
        shadow = f'box-shadow:0 0 0 2px {K.bg2},0 0 0 4px {K.acc};'
    else:
        bd, bg = f'2px solid {K.line}', K.bg3
    status = {'active': chip('Live', 'solid'), 'next': chip('Next', 'dash'),
              'error': chip('File error', 'amber', icon='warn')}.get(st, '')
    tchips = ''.join(chip(t, 'n', size=12) for t in tags)
    tb = f'border:1px dashed {K.acc_line if st == "active" else K.line2}'
    size = ''
    if w:
        size = f'width:{w}px;height:{h}px;'
    return (f'<button type="button" aria-pressed="{"true" if st == "active" else "false"}" style="{size}min-width:0;display:flex;flex-direction:column;'
            f'gap:6px;text-align:left;padding:8px;border-radius:11px;background:{bg};border:{bd};{shadow}">'
            f'<span style="flex:1;min-height:28px;display:flex;align-items:flex-start;padding:5px;border-radius:6px;background:{K.bg1};{tb}">{status}</span>'
            f'<span style="display:flex;align-items:center;gap:6px"><span style="flex:1;min-width:0;font-size:14px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{name}</span>{kbd(i + 1)}</span>'
            f'<span style="display:flex;gap:4px">{tchips}</span></button>')


def scene_section(active=0, mode='manual', nxt=None, err=None):
    head = (f'<div style="display:flex;align-items:center;gap:8px"><div style="font-size:16px;font-weight:700;flex:1">Scenes</div>'
            f'{btn("Prev", "sec", icon="left", key="[", h=36)}{btn("Next", "sec", icon="right", key="]", h=36)}</div>')
    rnd = mode == 'random'
    row2 = (f'<div style="display:flex;align-items:center;gap:8px">{seg(["Manual", "Random"], 1 if rnd else 0, w=168)}'
            f'{spinf("Every <b>8</b> bars", 164, dis=not rnd, aria="bars")}'
            f'<div style="flex:1"></div>{dd("Transition: Crossfade", 176)}{dd("1 bar", 72)}</div>')
    cards = ''
    for i in range(8):
        st = 'active' if i == active else 'next' if i == nxt else 'error' if i == err else 'default'
        cards += scene_card(i, st)
    grid = (f'<div style="flex:1;min-height:0;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));'
            f'grid-template-rows:repeat(2,minmax(0,1fr));gap:8px">{cards}</div>')
    return panel(head + row2 + grid, 'flex:1;gap:10px')


def scenes_firstrun():
    sp = status_page('grid', 'No scenes yet', 'Add a scene to get started. Scenes are small files you can edit in any text editor.',
                     btn('Add scene…', 'pri', h=44, px=18) + btn('Reveal folder', 'sec', icon='folder', h=44, px=16))
    head = f'<div style="font-size:16px;font-weight:700">Scenes</div>'
    return panel(head + sp, 'flex:1;gap:0')


def conn_chip(renderer):
    if renderer == 'running':
        inner = f'<span style="width:9px;height:9px;border-radius:50%;background:{K.acc}"></span>Renderer: running · 60 fps'
    elif renderer == 'starting':
        inner = f'{spin(14)}Connecting…'
    else:
        inner = f'<span style="width:9px;height:9px;border-radius:50%;border:2px solid {K.t3}"></span>Not running'
    return (f'<span style="display:inline-flex;align-items:center;gap:8px;height:30px;padding:0 12px;border-radius:15px;'
            f'background:{K.bg3};border:1px solid {K.line2};font-size:13px;color:{K.t1}">{inner}</span>')


def header(renderer):
    return (f'<div style="height:48px;flex:none;display:flex;align-items:center;padding:0 8px 0 12px;background:{K.bg1};border-bottom:1px solid {K.line}">'
            f'<div style="flex:1;display:flex">{conn_chip(renderer)}</div><div style="font-weight:700;font-size:15px">Mirrorballs</div>'
            f'<div style="flex:1;display:flex;justify-content:flex-end">{ibtn("menu", "Main menu", 36, "ghost", isz=20)}</div></div>')


def start_btn(renderer, w=200, h=92):
    common = f'width:{w}px;height:{h}px;flex:none;border-radius:14px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;'
    if renderer == 'stopped':
        return (f'<button type="button" style="{common}background:{K.acc};color:{K.on_acc};border:2px solid {K.acc}">'
                f'<span style="display:flex;align-items:center;gap:10px;font-size:26px;font-weight:700">{ic("play", 26)}Start</span>{kbd("Space", "pri")}</button>')
    if renderer == 'running':
        return (f'<button type="button" style="{common}background:{K.bg3};color:{K.t1};border:2px solid {K.t3}">'
                f'<span style="display:flex;align-items:center;gap:10px;font-size:26px;font-weight:700">{ic("stop", 24)}Stop</span>{kbd("Space")}</button>')
    return (f'<button type="button" disabled aria-busy="true" style="{common}background:{K.acc_bg};color:{K.t1};border:2px solid {K.acc}">'
            f'<span style="display:flex;align-items:center;gap:10px;font-size:24px;font-weight:700">{spin(24)}Starting…</span>'
            f'<span style="font-size:12px;color:{K.t2}">Waiting for the renderer</span></button>')


def beat_sq(mode, beat, s=68):
    if mode == 'live':
        st = f'background:{mix(K.well, K.beat, beat)};border:1px solid {K.line2}'
    elif mode == 'hold':
        st = f'background:{K.well};border:2px dashed {K.amber_line}'
    else:
        st = f'background:{K.well};border:1px solid {K.line}'
    return f'<div style="width:{s}px;height:{s}px;flex:none;border-radius:14px;{st}"></div>'


def pips(active, faded=False):
    out = ''
    for i in range(1, 5):
        on = i == active
        if on:
            st = f'background:{K.pip_on};color:{K.pip_on_t};border:{"2px" if i == 1 else "1px"} solid {K.pip_on}'
        else:
            st = f'background:{K.bg3};color:{K.t3};border:{"2px solid " + K.t3 if i == 1 else "1px solid " + K.line2}'
        ring = f'box-shadow:0 0 0 2px {K.bg2},0 0 0 4px {K.pip_on};' if on and i == 1 else ''
        out += (f'<span style="width:26px;height:26px;border-radius:13px;display:flex;align-items:center;justify-content:center;font-size:13px;'
                f'font-weight:700;flex:none;{st};{ring}">{i}</span>')
    return f'<div style="display:flex;gap:8px;align-items:center;padding:0 3px;{"opacity:.6;" if faded else ""}">{out}</div>'


def clock_card(clock, live, beat, pip):
    if clock == 'midi':
        src = chip('MIDI · Arturia BeatStep · 124.0 BPM', 'n', size=13)
    elif clock == 'internal':
        src = chip('Internal · 120 BPM', 'n', size=13)
    else:
        src = chip('MIDI · waiting for clock (holding 118 BPM)', 'amber', icon='warn', size=13)
    if clock == 'lost':
        bs, pp = beat_sq('hold', 0), pips(0, True)
    elif live:
        bs, pp = beat_sq('live', beat), pips(pip)
    else:
        bs, pp = beat_sq('idle', 0), pips(0, True)
    tap = btn('Tap tempo', 'sec', key='T', h=34, size=13, px=12, dis=clock != 'internal')
    return (f'<div style="flex:1;min-width:0;display:flex;align-items:center;gap:16px;padding:12px 16px;background:{K.bg2};'
            f'border:1px solid {K.line};border-radius:12px">{bs}<div style="flex:1;min-width:0;display:flex;flex-direction:column;gap:10px">'
            f'<div style="display:flex">{src}</div><div style="display:flex;align-items:center;gap:12px">{pp}<div style="flex:1"></div>{tap}</div></div></div>')


def ctl_head(name, val):
    return (f'<div style="display:flex;justify-content:space-between;align-items:baseline"><span style="font-size:13.5px">{name}</span>'
            f'<span style="font-size:13.5px;color:{K.t2}">{val}</span></div>')


def controls_panel(kind, name='Aurora Bands'):
    head = (f'<div style="display:flex;flex-direction:column;gap:2px"><div style="font-size:16px;font-weight:700">Scene controls</div>'
            f'<div style="font-size:13px;color:{K.t2}">{name}</div></div>')
    if kind == 'empty':
        body = status_page('sliders', 'No parameters', 'This scene has no adjustable parameters yet.', tsize=16)
    elif kind == 'none':
        body = status_page('sliders', 'No scene selected', 'Select a scene to adjust it.', tsize=16)
    else:
        def field(inner):
            return f'<div style="display:flex;flex-direction:column;gap:4px">{inner}</div>'
        body = (f'<div style="display:flex;flex-direction:column;gap:12px">'
                + field(ctl_head('Band width', '38 px') + slider(38, 'Band width'))
                + field(f'<span style="font-size:13.5px">Colour</span>' + colorbtn('#3fbfae'))
                + f'<div style="display:flex;align-items:center;justify-content:space-between;min-height:36px"><span style="font-size:13.5px">Mirror halves</span>{switch(True, "Mirror halves")}</div>'
                + field(f'<span style="font-size:13.5px">Motion</span>' + dd('Sweep', aria='Motion'))
                + '</div>')
    return panel(head + body, 'flex:1')


def master_panel(dis=False, blackout=False):
    bl = (f'<button type="button" aria-pressed="true" style="width:100%;height:52px;border-radius:11px;background:{K.t1};color:{K.bg0};border:2px solid {K.t1};font-size:17px;font-weight:700;display:flex;align-items:center;justify-content:center;gap:10px">Blackout on{kbd("B")}</button>'
          if blackout else btn('Blackout', 'sec', h=52, w='100%', key='B', size=17, dis=dis, extra='font-weight:700;'))
    return panel(f'<div style="font-size:16px;font-weight:700">Master</div>'
                 f'<div style="display:flex;flex-direction:column;gap:4px">{ctl_head("Brightness", "85 %")}{slider(85, "Brightness", dis)}</div>{bl}',
                 'gap:10px;flex:none')


MENU_ITEMS = [('Configuration…', 'Ctrl+,'), ('Quit renderer', ''), None, ('Keyboard shortcuts', 'Ctrl+?'), ('About Mirrorballs', '')]


def main_window(renderer='running', clock='midi', controls='empty', bnr=None, beat=.7, pip=1, active=0,
                mode='manual', firstrun=False, calib=False, err=None, nxt=None, name=None):
    live = renderer == 'running'
    filt = f'filter:grayscale(1);opacity:.5;' if calib else ''
    inert = ' inert' if calib else ''
    b = ''
    if bnr == 'error':
        b = banner('amber', f'<span style="{MONO};font-weight:700">strobe-grid.scene</span> line 12: unknown field “widht”', 'Open in editor')
    elif bnr == 'midi':
        b = banner('amber', 'MIDI clock lost. Holding the last tempo (118 BPM).', 'Use internal clock')
    elif bnr == 'calib':
        b = banner('busy', 'Calibration in progress', 'Show window')
    transport = f'<div style="display:flex;gap:12px;flex:none;height:92px">{start_btn(renderer)}{clock_card(clock, live, beat, pip)}</div>'
    scenes = scenes_firstrun() if firstrun else scene_section(active, mode, nxt, err)
    left = f'<div style="flex:1;min-width:0;display:flex;flex-direction:column;gap:12px">{transport}{scenes}</div>'
    nm = name or ('' if firstrun else SCENES[active][0])
    right = (f'<div style="width:272px;flex:none;display:flex;flex-direction:column;gap:12px;min-height:0">'
             f'{controls_panel("none" if firstrun else controls, nm)}{master_panel(dis=not live)}</div>')
    body = f'<div{inert} style="flex:1;min-height:0;display:flex;gap:12px;padding:16px;{filt}">{left}{right}</div>'
    hd = f'<div{inert} style="{filt}display:flex;flex-direction:column">{header(renderer)}</div>'
    return root(960, 620, hd + b + body, extra=f'border:1px solid {K.line};border-radius:12px')


def menu_popover():
    items = ''
    for it in MENU_ITEMS:
        if it is None:
            items += f'<div style="height:1px;background:{K.line};margin:6px 0"></div>'
        else:
            items += (f'<button type="button" style="display:flex;align-items:center;gap:12px;width:100%;height:40px;padding:0 12px;border:0;'
                      f'background:transparent;border-radius:8px;text-align:left;font-size:14px"><span style="flex:1">{it[0]}</span>'
                      f'{kbd(it[1]) if it[1] else ""}</button>')
    return root(280, 232, f'<div style="margin:12px;padding:6px;background:{K.bg2};border:1px solid {K.line2};border-radius:12px;'
                          f'box-shadow:0 8px 24px {K.shadow}">{items}</div>', bg=K.bg0)
