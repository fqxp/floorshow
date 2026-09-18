"""GTK smoke tests for the main window (need a display) and the client against a live renderer core.

The file name starts with "z" on purpose: it must run LAST. Once GTK has opened a Wayland display in a
process, creating a standalone EGL context (tests/test_gl.py) in that same process deadlocks. The
app avoids this by being two processes; the test suite avoids it by ordering."""

import time

import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, GLib, Gtk
except (ValueError, ImportError):  # pragma: no cover
    pytest.skip("GTK4/libadwaita not available", allow_module_level=True)

if not Gtk.init_check() or Gdk.Display.get_default() is None:  # pragma: no cover
    pytest.skip("no display", allow_module_level=True)

from mirrorballs.control import tokens
from mirrorballs.control.client import RendererClient
from mirrorballs.control.library import Library
from mirrorballs.control.main_window import MainWindow
from mirrorballs.control.store import ConfigStore

from test_render_io import Rig  # noqa: E402  (real IPC server + Core on a thread)


def pump(ms=100):
    ctx = GLib.MainContext.default()
    end = time.time() + ms / 1000
    while time.time() < end:
        ctx.iteration(False)


class FakeClient:
    def __init__(self):
        self.sent = []

    def send(self, cmd, **kw):
        self.sent.append((cmd, kw))
        return True


@pytest.fixture(scope="module")
def app():
    a = Adw.Application(application_id="de.fqxp.MirrorballsTest")
    a.register(None)
    prov = Gtk.CssProvider()
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    prov.load_from_string(tokens.css(True))
    return a


def scene_dir(tmp_path):
    d = tmp_path / "scenes"
    d.mkdir()
    (d / "alpha.yaml").write_text(
        "schema: 1\nname: Alpha\ntags: [x, y]\nparams:\n  w: {type: float, default: 5, min: 1, max: 10}\n"
        "  on: {type: bool, default: true}\n  m: {type: choice, default: a, choices: [a, b]}\n"
        "  c: {type: color, default: '#ff0000'}\nlayers:\n  - {type: fill}\n")
    (d / "beta.yaml").write_text("schema: 1\nname: Beta\nlayers:\n  - {type: fill}\n")
    return d


def st(scene="alpha", **kw):
    s = dict(event="state", scene=scene, scenes=["alpha", "beta"], running=True, mode="manual", every_beats=32, master=0.5,
             params={"w": 5.0, "on": True, "m": "a", "c": [1.0, 0.0, 0.0]}, calibrating=False, fps=60.0, scene_errors={},
             config_error=None, midi={},
             clock=dict(source="internal", port=None, bpm=120.0, beat=2.0, phase=0.0, bar=0.5, beats_per_bar=4, locked=True, status="internal"))
    s.update(kw)
    return s


@pytest.fixture
def win(app, tmp_path):
    fake = FakeClient()
    w = MainWindow(app, fake, Library([scene_dir(tmp_path)]), ConfigStore(tmp_path / "config.yaml"))
    w.present()
    pump(200)
    yield w, fake
    w.destroy()


def texts(widget, out=None):
    out = [] if out is None else out
    if isinstance(widget, Gtk.Label):
        out.append(widget.get_text())
    c = widget.get_first_child()
    while c:
        texts(c, out)
        c = c.get_next_sibling()
    return out


def test_offline_then_running_states(win):
    w, fake = win
    assert w.conn_chip["text"].get_text() == "Not running" and w.run_label.get_text() == "Start"
    assert w.run_btn.has_css_class("start") and not w.grid.get_sensitive()
    w.set_connection(True)
    w.apply_state(st())
    assert w.conn_chip["text"].get_text() == "Renderer: running · 60 fps"
    assert w.run_label.get_text() == "Stop" and w.run_btn.has_css_class("stop")
    assert w.source_chip.get_text() == "Internal · 120 BPM" and w.grid.get_sensitive()
    cards = []
    c = w.grid.get_first_child()
    while c:
        cards.append(c.get_child())
        c = c.get_next_sibling()
    assert len(cards) == 2
    assert cards[0].has_css_class("live") and not cards[1].has_css_class("live")
    assert "Alpha" in texts(w.grid) and "Beta" in texts(w.grid) and "1" in texts(w.grid) and "x" in texts(w.grid)
    assert w.brightness_label.get_text() == "50 %"
    # connection lost: dimmed again, state cleared
    w.set_connection(False)
    assert w.conn_chip["text"].get_text() == "Not running" and w.state is None and not w.grid.get_sensitive()


def test_commands_sent_by_the_ui(win):
    w, fake = win
    w.set_connection(True)
    w.apply_state(st())
    w.run_action("toggle-run")
    w.run_action("tap")
    w.run_action("scene-key", "2")
    w.run_action("step", +1)  # alpha -> beta, with the chosen transition
    w.run_action("mode", "random")
    w.run_action("brightness", +5)
    pump(120)  # brightness is debounced
    cmds = [c for c, _ in fake.sent]
    assert cmds[:2] == ["running.set", "tap"]
    assert fake.sent[0][1] == {"running": False}
    scene_cmds = [(c, kw) for c, kw in fake.sent if c == "scene.set"]
    assert scene_cmds and scene_cmds[0][1]["scene"] == "beta"
    assert scene_cmds[0][1]["transition"] == {"type": "crossfade", "beats": 1.0}  # ¼ bar of 4 beats, default UI selection
    assert ("mode.set", {"mode": "random", "every": {"bars": 8}}) in fake.sent
    assert ("master.set", {"value": 0.55}) in fake.sent
    # already on beta: selecting it again sends nothing
    w.apply_state(st(scene="beta"))
    n = len(fake.sent)
    w.run_action("scene-key", "2")
    assert len(fake.sent) == n


def test_blackout_remembers_brightness(win):
    w, fake = win
    w.set_connection(True)
    w.apply_state(st(master=0.7))
    w.run_action("blackout")
    assert fake.sent[-1] == ("master.set", {"value": 0.0})
    w.apply_state(st(master=0.0))
    assert w.blackout_btn.get_active()
    w.run_action("blackout")
    assert fake.sent[-1] == ("master.set", {"value": 0.7})


def test_scene_controls_follow_declared_params(win):
    w, fake = win
    w.set_connection(True)
    w.apply_state(st())
    assert set(w._param_widgets) == {"w", "on", "m", "c"}
    assert w._param_widgets["w"][1].get_value() == 5.0
    fake.sent.clear()
    w._param_widgets["w"][1].set_value(8.0)
    w._param_widgets["on"][1].set_active(False)
    w._param_widgets["m"][1].set_selected(1)
    assert ("param.set", {"name": "w", "value": 8.0}) in fake.sent
    assert ("param.set", {"name": "on", "value": False}) in fake.sent
    assert ("param.set", {"name": "m", "value": "b"}) in fake.sent
    fake.sent.clear()
    w.apply_state(st(params={"w": 3.0, "on": True, "m": "a", "c": [0.0, 1.0, 0.0]}))  # state updates never echo commands
    assert fake.sent == [] and w._param_widgets["w"][1].get_value() == 3.0
    w.apply_state(st(scene="beta", params={}))
    assert w._param_widgets == {} and "This scene has no adjustable parameters yet." in texts(w.controls_body)


def test_banners_and_calibration_lockout(win):
    w, fake = win
    w.set_connection(True)
    w.apply_state(st(clock=dict(st()["clock"], source="midi", locked=False, bpm=118.0, port="X", status="waiting for clock")))
    assert any("MIDI clock lost" in t for t in texts(w.banner_box))
    assert w.source_chip.has_css_class("warn")
    w.apply_state(st(calibrating=True))
    assert not w.grid.get_sensitive() and not w.scenes_panel.get_sensitive()
    fake.sent.clear()
    w.run_action("toggle-run")
    w.run_action("scene-key", "1")
    assert fake.sent == []  # inert while calibrating
    w.apply_state(st(scene_errors={"beta": ["layers[0].x: unknown key"]}))
    assert any("beta.yaml" in t for t in texts(w.banner_box))


def test_use_internal_clock_banner_writes_config(win, tmp_path):
    w, fake = win
    (tmp_path / "config.yaml").write_text("clock: {source: midi, midi_port: X}\n")
    w.set_connection(True)
    w.apply_state(st(clock=dict(st()["clock"], source="midi", locked=False, port="X")))
    from mirrorballs.control.viewmodel import banners

    w.act_banner(banners(True, w.state)[0])
    assert w.store.load().clock.source == "internal" and ("reload", {}) in fake.sent


def test_beat_tile_and_pips_follow_the_clock(win):
    w, _ = win
    w.set_connection(True)
    w.apply_state(st(clock=dict(st()["clock"], beat=5.0)))
    w.state_time = time.monotonic()
    w._on_tick(None, None)  # the per-frame handler, called directly so the test does not depend on frame timing
    assert w._pip_current == 1  # beat 5 = second beat of the bar
    assert w.beat_tile.intensity > 0.9  # on the beat: red
    w.state_time = time.monotonic() - 0.45  # 0.9 beat later at 120 BPM: nearly faded
    w._on_tick(None, None)
    assert w.beat_tile.intensity < 0.05
    w.state_time = time.monotonic() - 1.6  # 3.2 beats later: wrapped into the next bar (beat 8.2 -> pip 1)
    w._on_tick(None, None)
    assert w._pip_current == 0


def test_client_talks_to_a_live_renderer(app, tmp_path):
    rig = Rig(tmp_path)
    got = {"state": [], "conn": [], "err": []}
    client = RendererClient(rig.sock, lambda s, t: got["state"].append(s), got["conn"].append, got["err"].append)
    client.start()
    try:
        deadline = time.time() + 5
        while not got["state"] and time.time() < deadline:
            pump(20)
        assert got["conn"] == [True] and got["state"][-1]["scene"] == "a" and client.connected
        assert client.send("scene.set", scene="b", transition={"type": "cut", "beats": 0})
        assert client.send("scene.set", scene="nope")
        deadline = time.time() + 5
        while (not got["err"] or got["state"][-1]["scene"] != "b") and time.time() < deadline:
            pump(20)
        assert got["state"][-1]["scene"] == "b" and "unknown scene" in got["err"][0]
        rig.close()  # renderer goes away: the client reports it and keeps retrying
        deadline = time.time() + 5
        while got["conn"][-1] is not False and time.time() < deadline:
            pump(20)
        assert got["conn"][-1] is False and not client.send("tap")
    finally:
        client.close()
