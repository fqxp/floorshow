from gen_lib import *

TABS = ['Scenes', 'Clock', 'Output', 'MIDI', 'Director', 'Calibration', 'Ball']


def cfg(active, content, h=600):
    hd = (f'<div style="height:48px;flex:none;display:flex;align-items:center;padding:0 8px 0 16px;background:{K.bg1}">'
          f'<div style="flex:1"></div><div style="font-weight:700;font-size:15px">Configuration</div>'
          f'<div style="flex:1;display:flex;justify-content:flex-end">{ibtn("x", "Close", 36, "ghost")}</div></div>')
    tabs = ''
    for i, t in enumerate(TABS):
        on = i == active
        tabs += (f'<button type="button" aria-pressed="{"true" if on else "false"}" style="flex:1;height:44px;border:0;background:transparent;'
                 f'font-size:14px;color:{K.t1 if on else K.t2};font-weight:{700 if on else 400};'
                 f'border-bottom:3px solid {K.acc if on else "transparent"}">{t}</button>')
    tabs = f'<div style="flex:none;display:flex;background:{K.bg1};border-bottom:1px solid {K.line}">{tabs}</div>'
    body = f'<div style="flex:1;min-height:0;overflow:hidden;padding:18px 24px;display:flex;flex-direction:column;gap:18px">{content}</div>'
    return root(720, h, hd + tabs + body, extra=f'border:1px solid {K.line2};border-radius:14px')


def valid_icon(ok):
    if ok:
        return (f'<span style="width:24px;height:24px;border-radius:12px;background:{K.acc_bg};border:1px solid {K.acc_line};color:{K.acc};'
                f'display:flex;align-items:center;justify-content:center;flex:none">{ic("check", 14, 2)}</span>')
    return (f'<span style="width:24px;height:24px;border-radius:12px;background:{K.amber_bg};border:1px solid {K.amber_line};color:{K.amber};'
            f'display:flex;align-items:center;justify-content:center;flex:none">{ic("warn", 14, 1.8)}</span>')


def page_scenes():
    data = [('Aurora Bands', 'aurora-bands.scene', True, '1'), ('Halo Rings', 'halo-rings.scene', True, '2'),
            ('Spot Chase', 'spot-chase.scene', True, '3'), ('Strobe Grid', 'strobe-grid.scene', False, '4'),
            ('Slow Sweep', 'slow-sweep.scene', True, '5')]
    rows = []
    for n, f, ok, k in data:
        sel = n == 'Strobe Grid'
        suffix = chip('Valid', 'acc') if ok else chip('Error', 'amber')
        suffix += kbd(k)
        r = row(n, f'<span style="{MONO}">{f}</span>', suffix, lead=valid_icon(ok), sel=sel)
        if not ok:
            r += (f'<div style="background:{K.amber_bg};border-top:1px solid {K.amber_line};padding:10px 14px 12px 50px;color:{K.amber_t};'
                  f'display:flex;flex-direction:column;gap:8px"><div style="{MONO};font-size:13px"><b>strobe-grid.scene:12:9</b><br>unknown field “widht”, expected one of: width, height, speed</div>'
                  f'<div style="display:flex;gap:8px">{btn("Open at line 12", "amber", icon="edit", h=32, size=13)}{btn("Copy message", "amber", icon="copy", h=32, size=13)}</div></div>')
        rows.append(r)
    toolbar = (f'<div style="display:flex;gap:8px;flex:none">{btn("Add", "pri", icon="plus", h=40, px=14)}{btn("Duplicate", "sec", icon="copy", h=40)}'
               f'{btn("Rename", "sec", icon="edit", h=40)}{btn("Delete", "sec", icon="trash", h=40)}<div style="flex:1"></div>'
               f'{btn("Open in editor", "sec", icon="ext", h=40)}{btn("Reveal folder", "sec", icon="folder", h=40)}</div>')
    return cfg(0, toolbar + group('Scene files', 'Saving a file reloads its scene while the show runs.', rows))


def clock_common():
    return group('Timing', None, [
        row('Beats per bar', 'Sets the downbeat and the “every N bars” counter.', spinf('4', 132, aria='beats per bar')),
        row('Beat offset', 'Shift the beat to cancel projector latency. Positive means later.', spinf('<b>0</b> ms', 132, aria='beat offset')),
    ])


def page_clock_midi():
    src = seg(['MIDI clock', 'Internal BPM'], 0, h=44, w='100%')
    g = group('MIDI clock input', None, [
        row('Input port', 'Only ports that send clock are listed.', dd('Arturia BeatStep', 210, aria='MIDI clock port') + ibtn('refresh', 'Refresh ports')),
        row('Status', None, chip('Receiving · 124.0 BPM', 'acc', dot=True)),
    ])
    return cfg(1, src + g + clock_common())


def page_clock_internal():
    src = seg(['MIDI clock', 'Internal BPM'], 1, h=44, w='100%')
    g = group('Internal tempo', None, [
        row('Tempo', 'Default 120 BPM. Tap tempo on the main window sets this value.', numfield('120', 'BPM', 96, 'Tempo')),
    ])
    return cfg(1, src + g + clock_common())


def page_output():
    g1 = group('Test pattern', None, [
        row('Test pattern', 'Shows a grid and a circle on the projector so you can position the output window.', btn('Show test pattern', 'sec', h=40)),
    ])
    g2 = group('Output window', None, [
        row('Window state', 'Monitor 2 · 1920 × 1080', chip('Fullscreen', 'acc', dot=True)),
    ])
    hint = (f'<div style="display:flex;gap:12px;padding:12px 14px;border-radius:12px;background:{K.bg2};border:1px solid {K.line};color:{K.t2}">'
            f'<span style="color:{K.acc};display:flex">{ic("info", 18)}</span><div style="font-size:13.5px">The output window is moved to the projector and made fullscreen with GNOME: '
            f'<b style="color:{K.t1}">Super + Shift + ← →</b> changes monitor, <b style="color:{K.t1}">F11</b> toggles fullscreen.</div></div>')
    return cfg(2, g1 + g2 + hint)


def page_midi():
    g1 = group('Control port', None, [
        row('MIDI control input', 'Notes and knobs that control the show.', dd('Arturia BeatStep', 210, aria='control port') + ibtn('refresh', 'Refresh ports')),
    ])

    def mrow(trig, action, learning=False):
        if learning:
            return (f'<div style="display:flex;align-items:center;gap:12px;min-height:52px;padding:6px 14px;background:{K.acc_bg}">'
                    f'<span style="width:10px;height:10px;border-radius:5px;border:2px solid {K.acc};flex:none"></span>'
                    f'<div style="flex:1;font-size:14px">Listening… press a key or turn a knob</div>{btn("Cancel", "sec", h=34, size=13)}</div>')
        return (f'<div style="display:flex;align-items:center;gap:12px;min-height:52px;padding:6px 14px"><span style="{MONO};font-size:13px;width:150px;flex:none">{trig}</span>'
                f'<div style="flex:1">{dd(action, aria="Action")}</div>{btn("Learn", "sec", icon="midi", h=34, size=13)}{ibtn("trash", "Remove mapping", 34, "ghost")}</div>')

    hdr = (f'<div style="display:flex;gap:12px;padding:8px 14px;font-size:12.5px;color:{K.t3}"><span style="width:150px">Trigger</span><span style="flex:1">Action</span><span style="width:120px"></span></div>')
    rows = [hdr, mrow('Note C3 (36)', 'Scene 1 · Aurora Bands'), mrow('Note D3 (38)', 'Scene 2 · Halo Rings'),
            mrow('CC 7', 'Master brightness'), mrow('Note G#2 (32)', 'Blackout'), mrow('', '', True)]
    g2 = group('Mappings', 'Each trigger runs one action.', rows)
    add = f'<div style="display:flex">{btn("Add mapping", "sec", icon="plus", h=40)}</div>'
    return cfg(3, g1 + g2 + add)


def page_director():
    g1 = group('Automatic scene changes', 'Used by Random mode on the main window.', [
        row('Change scene every', None, spinf('<b>8</b> bars', 132, aria='bars')),
        row('Avoid repeating the last scene', None, switch(True, 'Avoid repeats')),
        row('Only use scenes tagged', None,
            f'<div style="display:flex;gap:6px">{chip("bands", "acc", icon="check")}{chip("rings", "acc", icon="check")}{chip("spots")}{chip("calm", "acc", icon="check")}</div>'),
    ])
    seq = []
    for i, (n, b) in enumerate([('Aurora Bands', '8'), ('Halo Rings', '4'), ('Spot Chase', '8'), ('Slow Sweep', '16')]):
        lead = f'<span style="color:{K.t3};display:flex">{ic("grip", 16, 2.4)}</span><span style="width:18px;color:{K.t2}">{i + 1}</span>'
        seq.append(row(n, None, spinf(f'<b>{b}</b> bars', 132, aria='bars'), lead=lead))
    g2 = group('Sequence', 'Plays in order in Sequence mode.', seq)
    foot = (f'<div style="display:flex;align-items:center;gap:12px">{btn("Add scene", "sec", icon="plus", h=40)}<div style="flex:1"></div>'
            f'<span style="color:{K.t2}">Loop sequence</span>{switch(True, "Loop sequence")}</div>')
    return cfg(4, g1 + g2 + foot)


def page_calibration():
    g = group('Current calibration', None, [
        row('Centre', None, '<b>965, 541</b> px'),
        row('Size', None, '<b>774 × 778</b> px'),
        row('Ball axis and tile rows', 'Optional advanced steps.', chip('Not set')),
        row('Last calibrated', None, 'Today, 21:14'),
    ])
    acts = (f'<div style="display:flex;gap:10px;align-items:center">{btn("Calibrate…", "pri", h=44, px=20, size=15)}'
            f'{btn("Reset to defaults", "amber", h=44, px=16)}<div style="flex:1"></div>'
            f'<span style="font-size:13px;color:{K.t3}">Reset asks for confirmation.</span></div>')
    return cfg(5, g + acts)


def page_ball():
    g1 = group('Ball', 'Starting values for calibration. Calibration refines them.', [
        row('Ball diameter', None, spinf('<b>30</b> cm', 132, aria='diameter')),
        row('Tile size', None, spinf('<b>10</b> mm', 132, aria='tile size')),
        row('Number of tile rows', 'Alternative to tile size.', spinf('<b>47</b>', 132, aria='rows')),
    ])
    g2 = group('Motor', None, [
        row('Rotation motor', 'Not connected.', chip('Coming later') + switch(False, 'Motor', dis=True)),
    ])
    link = group('Advanced', None, [
        row('Advanced calibration…', 'Ball axis and tile rows.', ic('ext', 16), h=56)])
    return cfg(6, g1 + g2 + link)


def confirm_dialog():
    inner = (f'<div style="margin:auto;width:380px;padding:20px;background:{K.bg2};border:1px solid {K.line2};border-radius:14px;box-shadow:0 12px 32px {K.shadow};'
             f'display:flex;flex-direction:column;gap:12px;text-align:center"><div style="font-size:18px;font-weight:700">Reset calibration to defaults?</div>'
             f'<div style="font-size:14px;color:{K.t2}">Centre, size, ball axis and tile rows go back to their factory values. This cannot be undone.</div>'
             f'<div style="display:flex;gap:8px;margin-top:6px">{btn("Cancel", "sec", h=44, w="50%")}{btn("Reset to defaults", "amber", h=44, w="50%")}</div></div>')
    return root(460, 260, inner, bg=K.bg0)


def shortcuts_overlay():
    def sc(title, rows):
        out = ''
        for a, keys in rows:
            ks = ''.join(kbd(k) for k in keys)
            out += (f'<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;min-height:32px"><span style="font-size:14px">{a}</span>'
                    f'<span style="display:flex;gap:4px">{ks}</span></div>')
        return (f'<div style="flex:1;min-width:0;display:flex;flex-direction:column;gap:6px"><div style="font-size:12.5px;font-weight:700;color:{K.t3};text-transform:uppercase;letter-spacing:.06em;margin-bottom:2px">{title}</div>{out}</div>')
    main = sc('Main window', [('Start / Stop', ['Space']), ('Tap tempo', ['T']), ('Blackout', ['B']), ('Scene 1–8', ['1', '…', '8']),
                              ('Previous / next scene', ['[', ']']), ('Manual / Random', ['M', 'R']), ('Brightness', ['Ctrl', '↑↓']),
                              ('Configuration', ['Ctrl', ',']), ('Shortcuts', ['Ctrl', '?'])])
    cal = sc('Calibration', [('Left line', ['A', 'D']), ('Right line', ['J', 'L']), ('Top line', ['W', 'S']), ('Bottom line', ['I', 'K']),
                             ('Move ball', ['←', '↑', '↓', '→']), ('Grow / shrink', ['+', '−']), ('Step', ['1', '2', '3']),
                             ('Blink / grid', ['B', 'G']), ('Cancel', ['Esc']), ('Finish', ['Enter'])])
    hd = (f'<div style="height:48px;flex:none;display:flex;align-items:center;padding:0 8px 0 16px;background:{K.bg1};border-bottom:1px solid {K.line}">'
          f'<div style="flex:1"></div><div style="font-weight:700;font-size:15px">Keyboard shortcuts</div><div style="flex:1;display:flex;justify-content:flex-end">{ibtn("x", "Close", 36, "ghost")}</div></div>')
    return root(640, 470, hd + f'<div style="flex:1;display:flex;gap:32px;padding:18px 24px">{main}{cal}</div>',
                extra=f'border:1px solid {K.line2};border-radius:14px')
