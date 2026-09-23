"""Render the configuration dialog (every tab) and the calibration window (its states) to PNGs.
   uv run --extra control python tools/screenshot_dialogs.py OUTDIR"""

import os
import sys
import tempfile
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ["MIRRORBALLS_CONFIG_DIR"] = str(TMP)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GLib, Graphene, Gtk  # noqa: E402

from mirrorballs.control import tokens  # noqa: E402
from mirrorballs.control.library import Library  # noqa: E402
from mirrorballs.control.main_window import MainWindow  # noqa: E402
from mirrorballs.control.store import ConfigStore  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "ui-shots")
OUT.mkdir(parents=True, exist_ok=True)


class Fake:
    def __init__(self):
        self.sent = []

    def send(self, cmd, **kw):
        self.sent.append((cmd, kw))
        return True


def pump(ms=400):
    ctx = GLib.MainContext.default()
    end = time.time() + ms / 1000
    while time.time() < end:
        ctx.iteration(False)


def shot(win, name):
    """Snapshot ``win`` to a PNG. A window that has not produced a frame yet gives an empty render node,
    so wait and retry."""
    for _ in range(10):
        pump(400)
        w, h = win.get_width(), win.get_height()
        snap = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(win).snapshot(snap, w, h)
        node = snap.to_node()
        if node is not None:
            tex = win.get_native().get_renderer().render_texture(node, Graphene.Rect().init(0, 0, w, h))
            tex.save_to_png(str(OUT / f"{name}.png"))
            print("saved", name, w, h)
            return
    raise RuntimeError(f"no frame for {name}")


def clock(**kw):
    c = dict(source="midi", port="Arturia BeatStep", bpm=124.0, beat=6.0, phase=0.0, bar=1.5, beats_per_bar=4, locked=True, status="locked")
    c.update(kw)
    return c


def state(ids, **kw):
    s = dict(event="state", clock=clock(), scene=ids[0], scenes=ids, running=True, mode="manual", every_beats=32, master=0.85,
             params={}, calibrating=False, test_pattern=False, learning=False, fps=60.0, scene_errors={},
             config_error=None, midi={}, window=dict(width=1920, height=1080, fullscreen=True, monitor="HDMI-A-1"))
    s.update(kw)
    return s


def on_activate(app):
    provider = Gtk.CssProvider()
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    provider.load_from_string(tokens.css(True))

    scenes = TMP / "scenes"
    scenes.mkdir()
    for f in (ROOT / "scenes").glob("*.yaml"):
        (scenes / f.name).write_text(f.read_text().replace("../assets/", str(ROOT / "assets") + "/"))
    (scenes / "strobe-grid.yaml").write_text("schema: 1\nname: Strobe Grid\nlayers:\n  - {type: band, lat: 0, widht: 12}\n")
    (TMP / "config.yaml").write_text(
        "schema: 1\nclock: {source: midi, midi_port: Arturia BeatStep, beats_per_bar: 4}\n"
        "calibration: {left: 0.3, right: 0.7, top: 0.14, bottom: 0.86, calibrated_at: '" + time.strftime("%Y-%m-%dT%H:%M") + "'}\n"
        "ball: {diameter_cm: 30, tile_mm: 10, row_pitch_deg: 3.83}\n"
        "midi:\n  control_port: Arturia BeatStep\n  map:\n    - {on: {note: 36}, do: {scene: bars-to-poles}}\n"
        "    - {on: {note: 38}, do: {scene: ripples}}\n    - {on: {cc: 7}, do: master}\n    - {on: {note: 32}, do: blackout}\n")
    fake = Fake()
    lib = Library([scenes])
    win = MainWindow(app, fake, lib, ConfigStore(TMP / "config.yaml"))
    win.set_dark(True)
    win.present()
    ids = lib.ids()
    win.set_connection(True)
    win.apply_state(state(ids))
    pump(300)

    win.open_scenes()
    shot(win.scenes_dialog, "scenes")
    win.scenes_dialog.close()
    pump(200)

    win.open_preferences()
    dlg = win.preferences_dialog
    for tab in ("Clock", "MIDI"):
        dlg.show_tab(tab)
        shot(dlg, f"prefs_{tab.lower()}")
    dlg.show_tab("Clock")
    dlg.clock_seg.select("internal")
    dlg._on_clock_source("internal")
    shot(dlg, "prefs_clock_internal")
    dlg.show_tab("MIDI")
    dlg._start_learning(2)
    shot(dlg, "prefs_midi_learning")
    dlg._flush()
    dlg.close()
    pump(200)

    # calibration window
    win.apply_state(state(ids))
    win.open_calibration()
    cal = win.calibration_window
    pump(300)
    shot(cal, "cal0_ball")
    cal._go(2)
    shot(cal, "cal1_default")
    cal.session.apply("line", ("left", -1)); cal.session.step = "coarse"
    for _ in range(3):
        cal.session.apply("line", ("left", -1))
    cal._update()
    shot(cal, "cal1_edited")
    for _ in range(12):
        cal.session.apply("line", ("right", +1))
    cal._update()
    shot(cal, "cal1_warning")
    cal.session.reset(cal._default_calibration())
    cal._go(3)
    cal._axis_act("step", "coarse")
    for _ in range(6):
        cal._axis_act("cross", (0, 1))
    for _ in range(2):
        cal._axis_act("cross", (-1, 0))
    shot(cal, "cal2_axis")
    cal._go(4)
    cal._rows_act("step", "coarse")
    cal._rows_act("offset", +1)
    shot(cal, "cal3_rows")
    print("sent:", [c for c, _ in fake.sent][-6:])
    app.quit()


app = Adw.Application(application_id="de.fqxp.MirrorballsShots2")
app.connect("activate", on_activate)
app.run([])
