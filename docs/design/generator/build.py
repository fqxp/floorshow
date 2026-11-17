import os, sys, json, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gen_lib import *
import gen_lib
from gen_main import *
from gen_cfg import *
from gen_cal import *


def sec(title, inner, gap=12):
    return (f'<section style="display:flex;flex-direction:column;gap:{gap}px"><div style="font-size:12.5px;font-weight:700;color:{K.t3};'
            f'text-transform:uppercase;letter-spacing:.07em">{title}</div>{inner}</section>')


def lab(inner, text, w=None):
    ws = f'width:{w}px;' if w else ''
    return (f'<div style="display:flex;flex-direction:column;gap:8px;align-items:flex-start;{ws}">{inner}'
            f'<span style="font-size:12.5px;color:{K.t2}">{text}</span></div>')


def comps_a():
    frames = ''
    for t in (0, 40, 100, 200, 320, 484):
        it = max(0.0, 1 - t / 484.0) ** 2
        frames += lab(beat_sq('live', it, 72), f'{t} ms · {round(it * 100)} %')
    s1 = sec('Beat indicator across one beat · 124 BPM = 484 ms · red on the beat, easing to the well colour',
             f'<div style="display:flex;gap:20px">{frames}</div>')
    pp = ''
    for i in range(1, 5):
        pp += lab(pips(i), 'Beat 1 · downbeat' if i == 1 else f'Beat {i}')
    modes = (lab(beat_sq('idle', 0, 72), 'Idle · renderer stopped') + lab(beat_sq('hold', 0, 72), 'Holding · MIDI clock lost'))
    s2 = sec('Pips and beat states · pips carry the beat without colour; downbeat has a heavier outline and ring',
             f'<div style="display:flex;gap:28px;align-items:flex-start">{pp}<div style="width:24px"></div>{modes}</div>')
    ss = (lab(start_btn('stopped'), 'Stopped · Start is primary') + lab(start_btn('running'), 'Running · neutral fill, stop glyph')
          + lab(start_btn('starting'), 'Starting… · disabled, spinner'))
    s3 = sec('Start / Stop · 200 × 92', f'<div style="display:flex;gap:24px;align-items:flex-start">{ss}</div>')
    cards = ''
    for st, tx in (('default', 'Default'), ('active', 'Active · Live chip'), ('next', 'Queued next (Random)'),
                   ('focus', 'Keyboard focus'), ('error', 'File error'), ('inert', 'Inert · calibration')):
        c = scene_card(1, 'default' if st == 'inert' else st, 176, 128)
        if st == 'inert':
            c = f'<div inert style="filter:grayscale(1);opacity:.5">{c}</div>'
        cards += lab(c, tx)
    s4 = sec('Scene card · name, tags, hotkey badge, reserved thumbnail area', f'<div style="display:flex;gap:20px;align-items:flex-start">{cards}</div>')
    return root(1280, 820, f'<div style="padding:36px 40px;display:flex;flex-direction:column;gap:30px">{s1}{s2}{s3}{s4}</div>')


def toast(text, action=None):
    a = f'<span style="color:{K.acc};font-weight:700;padding:0 10px">{action}</span>' if action else ''
    return (f'<div style="display:inline-flex;align-items:center;gap:8px;height:44px;padding:0 10px 0 20px;border-radius:22px;background:{K.bg4};'
            f'border:1px solid {K.line2};box-shadow:0 6px 18px {K.shadow};font-size:14px">{text}{a}</div>')


def comps_b():
    col1 = sec('Scene controls', (
        f'<div style="display:flex;flex-direction:column;gap:14px;width:300px">'
        f'<div>{ctl_head("Band width · slider with unit", "38 px")}{slider(38, "Band width")}</div>'
        f'<div style="display:flex;flex-direction:column;gap:4px"><span style="font-size:13.5px">Colour button</span>{colorbtn("#3fbfae")}</div>'
        f'<div style="display:flex;align-items:center;justify-content:space-between"><span style="font-size:13.5px">Switch on</span>{switch(True, "On")}</div>'
        f'<div style="display:flex;align-items:center;justify-content:space-between"><span style="font-size:13.5px">Switch off</span>{switch(False, "Off")}</div>'
        f'<div style="display:flex;flex-direction:column;gap:4px"><span style="font-size:13.5px">Dropdown</span>{dd("Sweep")}</div>'
        f'<div style="display:flex;flex-direction:column;gap:4px"><span style="font-size:13.5px">Segmented</span>{seg(["Manual", "Random"], 0)}</div>'
        f'<div style="display:flex;flex-direction:column;gap:4px"><span style="font-size:13.5px">Spin, disabled in Manual</span>{spinf("Every <b>8</b> bars", 164, dis=True)}</div>'
        f'</div>'))
    btns = ''.join(lab(b, t) for b, t in (
        (btn('Primary', 'pri', h=44, px=18, key='Enter'), 'Primary · 44 px'), (btn('Secondary', 'sec', icon='folder', h=36), 'Secondary · 36 px'),
        (btn('Ghost', 'ghost', h=36), 'Ghost'), (btn('Reset to defaults', 'amber', h=36), 'Caution · amber outline'),
        (btn('Disabled', 'sec', h=36, dis=True), 'Disabled')))
    chips = (f'<div style="display:flex;flex-wrap:wrap;gap:10px">{conn_chip("running")}{conn_chip("stopped")}{conn_chip("starting")}</div>'
             f'<div style="display:flex;flex-wrap:wrap;gap:8px">{chip("Internal · 120 BPM", "n", size=13)}{chip("MIDI · waiting for clock (holding 118 BPM)", "amber", icon="warn", size=13)}</div>'
             f'<div style="display:flex;gap:8px">{chip("bands")}{chip("rings")}{chip("Live", "solid")}{chip("Next", "dash")}{chip("File error", "amber", icon="warn")}</div>')
    bl = (f'<div style="display:flex;gap:16px;width:420px">{btn("Blackout", "sec", h=52, w="50%", key="B", size=17, extra="font-weight:700;")}'
          f'<button type="button" aria-pressed="true" style="width:50%;height:52px;border-radius:11px;background:{K.t1};color:{K.bg0};border:2px solid {K.t1};font-size:17px;font-weight:700;display:flex;align-items:center;justify-content:center;gap:10px">Blackout on{kbd("B")}</button></div>')
    col2 = (sec('Buttons', f'<div style="display:flex;flex-wrap:wrap;gap:16px 20px;width:400px">{btns}</div>')
            + sec('Blackout · off and on (inverted, not red)', bl) + sec('Chips', chips))
    col3 = (sec('Banners (AdwBanner)', f'<div style="display:flex;flex-direction:column;gap:12px;width:460px;border:1px solid {K.line};border-radius:8px;overflow:hidden">'
                + banner('amber', '<span style="' + MONO + ';font-weight:700">strobe-grid.scene</span> line 12: unknown field “widht”', 'Open in editor')
                + banner('amber', 'MIDI clock lost. Holding the last tempo (118 BPM).', 'Use internal clock')
                + banner('busy', 'Calibration in progress', 'Show window') + '</div>')
            + sec('Toasts (AdwToast)', f'<div style="display:flex;gap:12px">{toast("Scene reloaded")}{toast("Saved")}{toast("Scene removed", "Undo")}</div>')
            + sec('Status page · empty state', f'<div style="width:460px;height:200px;background:{K.bg2};border:1px solid {K.line};border-radius:12px;display:flex">'
                  + status_page('sliders', 'No parameters', 'This scene has no adjustable parameters yet.', tsize=16) + '</div>'))
    inner = (f'<div style="padding:36px 40px;display:flex;gap:44px;align-items:flex-start"><div style="display:flex;flex-direction:column;gap:26px">{col1}</div>'
             f'<div style="display:flex;flex-direction:column;gap:26px">{col2}</div><div style="display:flex;flex-direction:column;gap:26px">{col3}</div></div>')
    return root(1280, 820, inner)


def swatch(name, hexv, use):
    return (f'<div style="width:108px;display:flex;flex-direction:column;gap:3px"><div style="height:44px;border-radius:8px;background:{hexv};border:1px solid {K.line2}"></div>'
            f'<div style="font-size:12.5px;font-weight:700;margin-top:3px">{name}</div><div style="font-size:12px;color:{K.t2}">{hexv}</div>'
            f'<div style="font-size:12px;color:{K.t3}">{use}</div></div>')


def tokens_board():
    use = [('bg0', 'Window'), ('bg1', 'Header, footer'), ('bg2', 'Panels'), ('bg3', 'Controls, cards'), ('bg4', 'Tracks, pressed'),
           ('well', 'Beat tile ground'), ('line', 'Hairlines'), ('line2', 'Control borders'), ('t1', 'Text'), ('t2', 'Secondary text'),
           ('t3', 'Hints'), ('acc', 'Primary, focus'), ('acc_bg', 'Selected fill'), ('acc_line', 'Selected border'), ('on_acc', 'Text on accent'),
           ('amber', 'Warning icon'), ('amber_bg', 'Banner fill'), ('amber_line', 'Banner border'), ('amber_t', 'Warning text'), ('beat', 'Beat only')]

    def pal(d):
        return f'<div style="display:flex;flex-wrap:wrap;gap:14px 12px;width:1180px">' + ''.join(swatch(n, d[n], u) for n, u in use) + '</div>'
    rules = (f'<div style="display:flex;flex-direction:column;gap:6px;font-size:14px;color:{K.t2};width:420px">'
             f'<div><b style="color:{K.t1}">Red is beat only.</b> Nothing else is ever red, so a red flash always means a beat.</div>'
             f'<div><b style="color:{K.t1}">Amber is warning and error.</b> Banners, file errors, aspect warning, lost clock, caution buttons.</div>'
             f'<div><b style="color:{K.t1}">One accent.</b> Teal for Start, selection, focus and progress.</div>'
             f'<div><b style="color:{K.t1}">Never colour alone.</b> Live chip, pips, warning icons and text repeat every colour cue.</div></div>')
    types = ''.join(
        f'<div style="display:flex;align-items:baseline;gap:14px"><span style="width:118px;font-size:12.5px;color:{K.t3}">{n}</span>'
        f'<span style="font-size:{s}px;font-weight:{wt}">{smp}</span></div>' for n, s, wt, smp in (
            ('26 bold · Start', 26, 700, 'Start Stop'), ('20 bold · Title', 20, 700, 'No scenes yet'), ('16 bold · Heading', 16, 700, 'Scene controls'),
            ('14 · Body', 14, 400, 'Manual · Every 8 bars · 124.0 BPM'), ('13 · Secondary', 13, 400, 'Renderer: running · 60 fps'),
            ('12 · Caption', 12, 400, 'bands  fast  calm')))
    sp = ''.join(f'<div style="display:flex;flex-direction:column;align-items:center;gap:4px"><div style="width:{n}px;height:{n}px;background:{K.acc}"></div><span style="font-size:12px;color:{K.t2}">{n}</span></div>'
                 for n in (4, 8, 12, 16, 24))
    rad = ''.join(f'<div style="display:flex;flex-direction:column;align-items:center;gap:4px"><div style="width:44px;height:44px;border:2px solid {K.t2};border-radius:{n}px"></div><span style="font-size:12px;color:{K.t2}">{n}</span></div>'
                  for n in (5, 6, 9, 12, 14))
    tg = ''.join(f'<div style="display:flex;flex-direction:column;align-items:center;gap:4px"><div style="width:{n}px;height:{min(n, 60)}px;background:{K.bg4};border:1px solid {K.line2};border-radius:9px"></div><span style="font-size:12px;color:{K.t2}">{n}</span></div>'
                 for n in (32, 36, 44))
    typebox = '<div style="display:flex;flex-direction:column;gap:8px">' + types + '</div>'
    spbox = '<div style="display:flex;gap:16px;align-items:flex-end">' + sp + '</div>'
    radbox = '<div style="display:flex;gap:16px;align-items:flex-end">' + rad + '</div>'
    tgbox = '<div style="display:flex;gap:16px;align-items:flex-end">' + tg + '</div>'
    inner = ('<div style="padding:36px 40px;display:flex;flex-direction:column;gap:26px">'
             + sec('Dark palette (primary)', pal(DARK)) + sec('Light palette (same roles)', pal(LIGHT))
             + '<div style="display:flex;gap:56px;align-items:flex-start">'
             + sec('Type · Cantarell (Adwaita fallback), tabular numerals for BPM and pixels', typebox) + sec('Rules', rules) + '</div>'
             + '<div style="display:flex;gap:56px;align-items:flex-end">'
             + sec('Spacing px', spbox) + sec('Radius px', radbox) + sec('Hit target px · primary at least 44, Start/Stop 92', tgbox) + '</div></div>')
    return root(1280, 920, inner)


# ---------------- build ----------------
boards = []  # (file, w, h, title, body)


THEME_OF = {}


def add(file, w, h, title, body):
    boards.append((file, w, h, title, body))
    THEME_OF[file] = 'light' if K.bg0 == LIGHT['bg0'] else 'dark'


set_theme(DARK)
add('Main.dc.html', 960, 620, 'Main · Running (MIDI clock)', main_window('running', 'midi', 'empty', None, .7, 1, 0))
add('Main_Stopped.dc.html', 960, 620, 'Main · Stopped', main_window('stopped', 'internal', 'empty', None, 0, 0, 0))
add('Main_Starting.dc.html', 960, 620, 'Main · Starting', main_window('starting', 'internal', 'empty', None, 0, 0, 0))
add('Main_Controls.dc.html', 960, 620, 'Main · Scene controls, Random mode', main_window('running', 'internal', 'populated', None, .35, 3, 0, 'random', nxt=2))
add('Main_MidiLost.dc.html', 960, 620, 'Main · MIDI clock lost', main_window('running', 'lost', 'empty', 'midi', 0, 0, 0))
add('Main_SceneError.dc.html', 960, 620, 'Main · Scene file error', main_window('running', 'midi', 'empty', 'error', .5, 2, 0, err=3))
add('Main_Calibrating.dc.html', 960, 620, 'Main · Calibration in progress', main_window('running', 'midi', 'empty', 'calib', .5, 2, 0, calib=True))
add('Main_FirstRun.dc.html', 960, 620, 'Main · First run', main_window('stopped', 'internal', 'empty', None, 0, 0, 0, firstrun=True))
set_theme(LIGHT)
add('Main_Light.dc.html', 960, 620, 'Main · Light theme', main_window('running', 'midi', 'populated', None, .7, 1, 0))
set_theme(DARK)
add('Main_Menu.dc.html', 280, 232, 'Main menu', menu_popover())

add('Config_Scenes.dc.html', 720, 600, 'Config · Scenes', page_scenes())
add('Config_ClockMidi.dc.html', 720, 600, 'Config · Clock (MIDI)', page_clock_midi())
add('Config_ClockInternal.dc.html', 720, 600, 'Config · Clock (Internal)', page_clock_internal())
add('Config_Output.dc.html', 720, 600, 'Config · Output', page_output())
add('Config_Midi.dc.html', 720, 600, 'Config · MIDI mappings', page_midi())
add('Config_Director.dc.html', 720, 600, 'Config · Director', page_director())
add('Config_Calibration.dc.html', 720, 600, 'Config · Calibration', page_calibration())
add('Config_Ball.dc.html', 720, 600, 'Config · Ball', page_ball())
add('Config_Reset.dc.html', 460, 260, 'Confirm reset', confirm_dialog())
add('Shortcuts.dc.html', 640, 470, 'Keyboard shortcuts', shortcuts_overlay())

add('Cal1_Default.dc.html', 720, 560, 'Calibration ① · Default values', cal1('default'))
add('Cal1_Edited.dc.html', 720, 560, 'Calibration ① · Edited', cal1('edited'))
add('Cal1_Warning.dc.html', 720, 560, 'Calibration ① · Aspect warning', cal1('warn'))
add('Cal1_Starting.dc.html', 720, 560, 'Calibration ① · Renderer starting', cal1('starting'))
add('Cal1_NotFullscreen.dc.html', 720, 560, 'Calibration ① · Output not fullscreen', cal1('nofs'))
add('Cal2_Axis.dc.html', 720, 560, 'Calibration ② · Ball axis', cal2())
add('Cal3_Rows.dc.html', 720, 560, 'Calibration ③ · Tile rows', cal3())

add('Components_A.dc.html', 1280, 820, 'Components · Beat, Start/Stop, scenes', comps_a())
add('Components_B.dc.html', 1280, 820, 'Components · Controls, feedback', comps_b())
add('Tokens.dc.html', 1280, 920, 'Tokens · Colour, type, spacing', tokens_board())

for f, w, h, t, body in boards:
    set_theme(LIGHT if THEME_OF[f] == 'light' else DARK)
    write(f, w, h, t, body)
set_theme(DARK)

# layout
sections = [
    ('Main control window', 3, ['Main.dc.html', 'Main_Stopped.dc.html', 'Main_Starting.dc.html', 'Main_Controls.dc.html', 'Main_MidiLost.dc.html',
                                'Main_SceneError.dc.html', 'Main_Calibrating.dc.html', 'Main_FirstRun.dc.html', 'Main_Light.dc.html', 'Main_Menu.dc.html']),
    ('Configuration dialog', 3, ['Config_Scenes.dc.html', 'Config_ClockMidi.dc.html', 'Config_ClockInternal.dc.html', 'Config_Output.dc.html', 'Config_Midi.dc.html',
                                 'Config_Director.dc.html', 'Config_Calibration.dc.html', 'Config_Ball.dc.html', 'Config_Reset.dc.html', 'Shortcuts.dc.html']),
    ('Calibration window', 3, ['Cal1_Default.dc.html', 'Cal1_Edited.dc.html', 'Cal1_Warning.dc.html', 'Cal1_Starting.dc.html', 'Cal1_NotFullscreen.dc.html',
                               'Cal2_Axis.dc.html', 'Cal3_Rows.dc.html']),
    ('Components and tokens', 1, ['Components_A.dc.html', 'Components_B.dc.html', 'Tokens.dc.html']),
]
info = {f: (w, h, t) for f, w, h, t, _ in boards}
bd, order, notes = {}, [], {}
cursor = 0
for si, (name, cols, files) in enumerate(sections):
    note_y = cursor
    y = cursor + 260
    maxw = 0
    for r in range(0, len(files), cols):
        row_files = files[r:r + cols]
        x = 0
        rh = 0
        for f in row_files:
            w, h, t = info[f]
            bd[f] = {'x': x, 'y': y, 'w': w, 'h': h, 'title': t}
            order.append(f)
            x += w + 120
            rh = max(rh, h)
        maxw = max(maxw, x - 120)
        y += rh + 120
    notes[f'sec{si + 1}'] = {'x': 0, 'y': note_y, 'text': name, 'kind': 'title1', 'maxW': maxw}
    cursor = y + 140

canvas = {
    'v': 3,
    'createdOnFiles': {'v': 1, 'at': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')},
    'title': 'Floorshow Control UI',
    'launch': {'view': 'canvas'},
    'pages': [],
    'boards': bd,
    'order': order,
    'notes': notes,
    'designSystems': [],
}
with open(OUT + '/canvas.json', 'w') as fh:
    json.dump(canvas, fh, indent=1, ensure_ascii=False)
print(len(boards), 'boards')
print(json.dumps({f: 'project/' + f for f, *_ in boards}))
