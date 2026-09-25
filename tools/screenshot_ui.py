"""Render the main window in each state to PNGs (no renderer needed): a visual check against the
design boards.   uv run --extra control python tools/screenshot_ui.py OUTDIR"""

import sys
import tempfile
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Graphene, GLib, Gtk  # noqa: E402

from mirrorballs.control import tokens  # noqa: E402
from mirrorballs.control.library import Library  # noqa: E402
from mirrorballs.control.main_window import MainWindow  # noqa: E402
from mirrorballs.control.store import ConfigStore  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "ui-shots")
OUT.mkdir(parents=True, exist_ok=True)


def clock(**kw):
    c = dict(source="midi", port="Arturia BeatStep", bpm=124.0, beat=6.0, phase=0.0, bar=1.5, beats_per_bar=4, locked=True, status="locked")
    c.update(kw)
    return c


def state(scene, ids, **kw):
    s = dict(event="state", clock=clock(), scene=scene, scenes=ids, running=True, mode="manual", every_beats=32, master=0.85,
             params={}, calibrating=False, fps=60.0, scene_errors={}, config_error=None, midi={})
    s.update(kw)
    return s


def pump(ms=500):
    ctx = GLib.MainContext.default()
    end = time.time() + ms / 1000
    while time.time() < end:
        ctx.iteration(False)


def shot(win, name):
    pump(400)
    win.state_time = time.monotonic()  # freeze the beat at the sampled position (the pump above took real time)
    pump(30)
    w, h = win.get_width(), win.get_height()
    print("size", w, h, "min", win.get_preferred_size().minimum_size.width, win.get_preferred_size().minimum_size.height)
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(win).snapshot(snap, w, h)
    tex = win.get_native().get_renderer().render_texture(snap.to_node(), Graphene.Rect().init(0, 0, w, h))
    tex.save_to_png(str(OUT / f"{name}.png"))
    print("saved", name, w, h)


def on_activate(app):
    provider = Gtk.CssProvider()
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    tokens.pin_base_font()  # shots are of the design, at the size it was drawn
    provider.load_from_string(tokens.css(True))

    tmp = Path(tempfile.mkdtemp())
    scenes_dir = tmp / "scenes"
    scenes_dir.mkdir()
    for f in (ROOT / "scenes").glob("*.yaml"):
        (scenes_dir / f.name).write_text(f.read_text().replace("../assets/", str(ROOT / "assets") + "/"))
    (scenes_dir / "aurora.yaml").write_text(
        "schema: 1\nname: Aurora Bands\ntags: [bands, slow]\nparams:\n"
        "  width: {type: float, default: 38, min: 4, max: 90, unit: px, label: Band width}\n"
        "  tint: {type: color, default: '#3fbfae', label: Colour}\n  mirror: {type: bool, default: true, label: Mirror halves}\n"
        "  motion: {type: choice, default: Sweep, choices: [Sweep, Pulse, Drift], label: Motion}\n"
        "layers:\n  - {type: band, lat: 0, width: 10}\n")
    cfg = tmp / "config.yaml"
    lib = Library([scenes_dir])
    win = MainWindow(app, None, lib, ConfigStore(cfg))
    win.set_dark(True)
    win.present()
    ids = lib.ids()
    cur = "aurora"

    # running, MIDI clock, on a beat (tile red), scene without params
    win.set_connection(True)
    win.apply_state(state("kick-spot", ids, clock=clock(beat=6.02)))
    shot(win, "1_running")
    win.apply_state(state(cur, ids, clock=clock(beat=6.02), params={"width": 38.0, "tint": [0.25, 0.75, 0.68], "mirror": True, "motion": "Sweep"},
                          mode="random", every_beats=32))
    win.state_time = time.monotonic()
    shot(win, "2_controls_random")
    win.apply_state(state("kick-spot", ids, clock=clock(source="internal", bpm=120.0, port=None, beat=5.5)))
    win.state_time = time.monotonic()
    shot(win, "3_internal_halfway")
    win.apply_state(state("kick-spot", ids, clock=clock(locked=False, bpm=118.0, status="waiting for clock", beat=5.5)))
    shot(win, "4_midi_lost")
    win.apply_state(state("kick-spot", ids, scene_errors={"strobe-grid": ["line 12: unknown field “widht”"]}))
    shot(win, "5_scene_error")
    win.apply_state(state("kick-spot", ids, calibrating=True))
    shot(win, "6_calibrating")
    win.set_connection(False)
    shot(win, "7_not_running")
    win.starting = True
    win._start_deadline = time.monotonic() + 30
    win.refresh()
    shot(win, "8_starting")
    win.starting = False
    empty = tmp / "empty"
    empty.mkdir()
    win.library = Library([empty])
    win._cards_key = None
    win.refresh()
    shot(win, "9_first_run")
    app.quit()


app = Adw.Application(application_id="de.fqxp.MirrorballsShots")
app.connect("activate", on_activate)
app.run([])
