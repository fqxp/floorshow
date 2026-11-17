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

from mirrorballs.control import tokens as _tokens  # noqa: E402

_tokens.pin_base_font()  # the sizes below are the design's, so the design's base font is what they need

from mirrorballs.control import tokens, viewmodel as vm
from mirrorballs.common.config import load_config
from mirrorballs.control.client import RendererClient
from mirrorballs.control.library import Library
from mirrorballs.control.main_window import MainWindow
from mirrorballs.control.calibration import DIAMETERS_CM
from mirrorballs.control.store import ConfigStore

from test_render_io import Rig  # noqa: E402  (real IPC server + Core on a thread)


def pump(ms=100):
    ctx = GLib.MainContext.default()
    end = time.time() + ms / 1000
    while time.time() < end:
        ctx.iteration(False)


def pump_until(cond, ms=2000):
    """Pump until ``cond()`` holds. GTK can be busy for longer than a debounce, so waiting for the
    result keeps the test from depending on how long a fixed pump happens to take."""
    ctx = GLib.MainContext.default()
    end = time.time() + ms / 1000
    while time.time() < end and not cond():
        ctx.iteration(False)
    return cond()


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
    s = dict(event="state", rev=vm.REV, scene=scene, scenes=["alpha", "beta"], running=True, mode="manual", every_beats=32, master=0.5,
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
    assert pump_until(lambda: any(c == "master.set" for c, _ in fake.sent))  # brightness is debounced
    assert fake.sent[0] == ("audio.preview", {"on": True})  # on connecting: the audio picker's meters
    ui = [(c, kw) for c, kw in fake.sent if not c.endswith(".preview")]
    cmds = [c for c, _ in ui]
    assert cmds[:2] == ["running.set", "tap"]
    assert ui[0][1] == {"running": False}
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


def test_four_escapes_black_the_show_out(win):
    w, fake = win
    w.set_connection(True)
    w.apply_state(st())
    esc = lambda: w._on_key(None, Gdk.KEY_Escape, 0, Gdk.ModifierType(0))
    assert [esc() for _ in range(3)] == [False] * 3  # a stray Escape does nothing at all
    assert not w.blackout_btn.get_active()
    assert esc() is True
    assert w.blackout_btn.get_active()
    # and Ctrl+B does the same in one press
    w.blackout_btn.set_active(False)
    assert w._on_key(None, Gdk.KEY_b, 0, Gdk.ModifierType.CONTROL_MASK) is True
    assert w.blackout_btn.get_active()
    assert w._on_key(None, Gdk.KEY_b, 0, Gdk.ModifierType(0)) is False  # b alone is no longer a shortcut


def test_tap_tempo_belongs_to_the_internal_clock_only(win, tmp_path):
    w, fake = win
    w.set_connection(True)
    w.apply_state(st())
    assert w.tap_btn.get_visible()
    w.run_action("tap")
    assert ("tap", {}) in fake.sent
    fake.sent.clear()
    w.apply_state(st(clock=dict(st()["clock"], source="midi", port="X")))
    assert not w.tap_btn.get_visible()
    w.run_action("tap")
    assert ("tap", {}) not in fake.sent  # the MIDI clock owns the tempo


def test_the_clock_and_sequence_shortcuts(win, tmp_path):
    w, fake = win
    w.set_connection(True)
    w.apply_state(st())
    w.run_action("mode", "sequence")
    assert ("mode.set", {"mode": "sequence"}) in fake.sent
    # C goes round the clocks; without a port MIDI is still chosen, and the picker below asks for one
    w.run_action("clock-source")
    assert w.store.load().clock.source == "midi" and w.midi_picker.get_visible()
    w.apply_state(st(clock=dict(st()["clock"], source="midi", port=None)))
    w._clock_hold = 0.0
    w.run_action("clock-source")
    assert w.store.load().clock.source == "audio" and w.audio_picker.get_visible()
    w.apply_state(st(clock=dict(st()["clock"], source="audio")))
    w._clock_hold = 0.0
    w.run_action("clock-source")
    assert w.store.load().clock.source == "internal"


def test_the_menu_actions_open_their_dialogs(win):
    w, _ = win
    for name, attr in (("scenes", "scenes_dialog"), ("preferences", "preferences_dialog")):
        w.win_action(name)
        assert getattr(w, attr) is not None
        getattr(w, attr).close()
    assert w.lookup_action("quit") is not None  # Ctrl+Q only: quitting is not worth a menu item
    w.show_shortcuts()  # the calibration keys moved out: that window shows them on its own buttons


def test_the_trail_slider_applies_live_and_is_remembered(win, tmp_path):
    w, fake = win
    w.set_connection(True)
    w.apply_state(st())
    assert w.trail_label.get_text() == "Off"  # off unless the config says otherwise
    w.trail.set_value(25)
    assert w.trail_label.get_text() == "0.25 s"
    assert pump_until(lambda: any(c == "trail.set" for c, _ in fake.sent))
    assert ("trail.set", {"seconds": 0.25}) in fake.sent
    assert load_config(tmp_path / "config.yaml").trail.seconds == 0.25  # kept for the next start
    # the renderer owns the live value, and following its state sends nothing back
    fake.sent.clear()
    w.apply_state(st(trail_s=0.5))
    assert w.trail.get_value() == 50 and w.trail_label.get_text() == "0.50 s"
    assert not fake.sent


def test_quit_renderer_is_disabled_while_no_renderer_runs(win):
    w, _ = win
    assert not w.lookup_action("quit-renderer").get_enabled()
    w.set_connection(True)
    w.apply_state(st())
    assert w.lookup_action("quit-renderer").get_enabled()
    w.set_connection(False)
    assert not w.lookup_action("quit-renderer").get_enabled()


def test_every_stepper_works_in_sequence_mode_and_survives_state_updates(win):
    w, fake = win
    w.set_connection(True)
    w.apply_state(st(mode="sequence", every_beats=32))
    assert w.every_bars == 8
    w._set_every(9)
    assert fake.sent[-1] == ("mode.set", {"mode": "sequence", "every": {"bars": 9}})
    w.apply_state(st(mode="sequence", every_beats=32))  # a state from before the renderer applied the change
    assert w.every_bars == 9 and "<b>9</b>" in w.every_label.get_label()
    w._every_hold = 0.0
    w.apply_state(st(mode="sequence", every_beats=36))
    assert w.every_bars == 9


def test_the_every_stepper_is_remembered_for_the_next_start(win, tmp_path):
    w, _ = win
    w.set_connection(True)
    w.apply_state(st(mode="random"))
    w._set_every(12)
    assert load_config(tmp_path / "config.yaml").director.every.bars == 12


def test_window_and_column_sizes_do_not_depend_on_the_scene(win):
    w, _ = win
    w.set_connection(True)
    sizes = []
    for scene, params in (("beta", {}), ("alpha", st()["params"]), ("beta", {})):
        w.apply_state(st(scene=scene, params=params))
        pump(150)
        sizes.append([(x.get_width(), x.get_height()) for x in (w, w.scenes_panel, w.controls_panel, w.master_panel)])
    assert sizes[0] == sizes[1] == sizes[2]


def test_scrolling_the_bpm_chip_sets_the_internal_tempo_only(win):
    w, fake = win
    w.set_connection(True)
    w.apply_state(st())
    assert "Scroll" in w.source_box.get_tooltip_text()
    assert w._on_bpm_scroll(None, 0, -1.0)  # wheel up: one BPM faster
    assert fake.sent[-1] == ("bpm.set", {"bpm": 121.0}) and w.source_chip.get_text() == "Internal · 121 BPM"
    w.apply_state(st())  # a stale state does not undo it
    assert w.source_chip.get_text() == "Internal · 121 BPM"
    w._on_bpm_scroll(None, 0, 1.0)
    w._on_bpm_scroll(None, 0, 1.0)
    assert fake.sent[-1] == ("bpm.set", {"bpm": 119.0})
    fake.sent.clear()
    w._on_bpm_scroll(None, 0, -0.3)  # a touchpad's small deltas add up
    assert fake.sent == []
    w.apply_state(st(clock=dict(st()["clock"], source="midi", bpm=124.0)))
    w._bpm_hold = 0.0
    fake.sent.clear()  # (showing the MIDI picker asked for the port previews)
    assert not w._on_bpm_scroll(None, 0, -1.0) and fake.sent == []
    assert "MIDI" in w.source_box.get_tooltip_text()


def test_the_beat_dot_sits_in_the_clock_chip(win):
    w, _ = win
    assert w.beat_tile.get_parent() is w.source_box and w.source_chip.get_parent() is w.source_box
    assert w.beat_tile.get_height() <= w.source_chip.get_height() + 2  # about the text's size, not a tile


def cards(w):
    out = []
    c = w.grid.get_first_child()
    while c:
        out.append(c.get_child())
        c = c.get_next_sibling()
    return out


def test_tag_filter_all_and_tags(win):
    w, _ = win
    w.set_connection(True)
    w.apply_state(st())
    assert list(w._tag_buttons) == [None, "x", "y"] and w._tag_buttons[None].get_active()
    w._tag_buttons["x"].set_active(True)
    assert not w._tag_buttons[None].get_active() and len(cards(w)) == 1 and "Alpha" in texts(w.grid)
    assert w.visible_ids() == ["alpha"]
    w._tag_buttons["x"].set_active(False)  # the last tag off: back to All
    assert w._tag_buttons[None].get_active() and len(cards(w)) == 2
    w._tag_buttons["y"].set_active(True)
    w._tag_buttons[None].set_active(True)
    assert not w._tag_buttons["y"].get_active() and len(cards(w)) == 2
    w._tag_buttons[None].set_active(False)  # All cannot be switched off by itself
    assert w._tag_buttons[None].get_active()


def test_only_accented_shows_the_scenes_that_use_the_accent(win, tmp_path):
    w, _ = win
    (tmp_path / "scenes" / "gamma.yaml").write_text("schema: 1\nname: Gamma\nlayers:\n  - {type: fill, color: accent}\n")
    w.library.refresh()
    w.set_connection(True)
    w.apply_state(st())
    w.refresh()
    w.accented_check.set_active(True)
    assert w.visible_ids() == ["gamma"] and "Gamma" in texts(w.grid) and "Alpha" not in texts(w.grid)


def test_the_accent_is_picked_from_the_offered_colours(win):
    w, fake = win
    w.set_connection(True)
    w.apply_state(st(accent=[1.0, 1.0, 1.0]))
    assert list(w.accent_choices) == [c for _, c in vm.ACCENT_COLORS] and len(w.accent_choices) == 26
    assert w.accent_label.get_text() == "White" and w.accent_choices["#ffffff"].has_css_class("chosen")
    w.accent_choices["#00ffff"].emit("clicked")
    assert fake.sent[-1] == ("accent.set", {"color": [0.0, 1.0, 1.0]}) and w.store.load().accent.color == "#00ffff"
    assert w.accent_label.get_text() == "Cyan" and w.accent_swatch.color == "#00ffff"
    assert w.accent_choices["#00ffff"].has_css_class("chosen") and not w.accent_choices["#ffffff"].has_css_class("chosen")
    w.apply_state(st(accent=[1.0, 1.0, 1.0]))  # a stale state does not snap it back
    assert w.accent_label.get_text() == "Cyan"
    assert w.lookup_action("accent-colors") is None


def test_countdown_bar_shrinks_towards_the_next_automatic_change(win):
    w, _ = win
    w.set_connection(True)
    w.apply_state(st(mode="manual", next_change_beat=None, change_interval_beats=None))
    w._update_countdown(w.state)
    assert w.countdown.fraction is None
    w.apply_state(st(mode="random", next_change_beat=34.0, change_interval_beats=32.0))  # beat 2.0: 32 of 32 left
    w._update_countdown(w.state)
    assert w.countdown.fraction == pytest.approx(1.0, abs=0.01)
    w.apply_state(st(mode="random", next_change_beat=18.0, change_interval_beats=32.0))
    w._update_countdown(w.state)
    assert w.countdown.fraction == pytest.approx(0.5, abs=0.01)


def test_outdated_renderer_banner_restarts_the_renderer(win):
    w, fake = win
    w.set_connection(True)
    old = {k: v for k, v in st().items() if k != "rev"}  # an old renderer reports no "rev"
    w.apply_state(old)
    w.act_banner(next(b for b in vm.banners(True, old) if b.kind == "renderer_outdated"))
    assert fake.sent[-1][0] == "quit" and w._restart_pending
    started = []
    w.launch_renderer = lambda: started.append(1)
    w.set_connection(False)
    pump(900)
    assert started == [1] and not w._restart_pending


def test_unchanged_states_do_not_refresh_the_window(win):
    w, _ = win
    w.set_connection(True)
    w.apply_state(st())
    calls = []
    real = w.refresh
    w.refresh = lambda: (calls.append(1), real())[1]
    moving = st()
    moving["clock"].update(beat=9.5, phase=0.5, bar=2.4)  # only the beat position moved
    w.apply_state(moving)
    assert calls == []
    w.apply_state(st(mode="random"))
    assert calls == [1]


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
    assert w.source_box.has_css_class("warn")
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


def test_the_clock_can_be_switched_from_the_main_window(win, tmp_path):
    w, fake = win
    w.set_connection(True)
    w.apply_state(st())
    # every clock can be chosen: without a port or source the picker below the strip asks for one
    assert w.clock_seg.selected() == "internal"
    assert all(b.get_sensitive() for b in w.clock_seg.buttons.values())
    w.store.update({"clock": {"midi_port": "VirMIDI 1-0"}})
    w.cfg = w.store.load()
    w.refresh()
    fake.sent.clear()
    w.clock_seg.buttons["midi"].set_active(True)  # a click during the show
    assert w.store.load().clock.source == "midi" and ("reload", {}) in fake.sent
    w.apply_state(st())  # a state from before the renderer reloaded must not snap the switch back
    assert w.clock_seg.selected() == "midi"
    # afterwards the renderer is the authority again, and following it sends nothing
    w._clock_hold = 0.0
    fake.sent.clear()
    w.apply_state(st(clock=dict(st()["clock"], source="midi", port="VirMIDI 1-0")))
    assert w.clock_seg.selected() == "midi"
    w.apply_state(st())
    assert w.clock_seg.selected() == "internal"
    # nothing but the MIDI port previews, on while the MIDI picker showed and off again
    assert fake.sent == [("midi.preview", {"on": False})]


def test_closing_while_the_show_runs_asks_about_the_renderer(win):
    w, fake = win
    assert w._on_close_request() is False  # not connected: nothing to ask about
    w.set_connection(True)
    w.apply_state(st(running=False))
    assert w._on_close_request() is False  # stopped: nothing on the projector
    w.apply_state(st(running=True))
    assert w._on_close_request() is True  # live: the window stays until the question is answered
    w._quit_asked = False
    w._on_quit_response(None, "keep")
    assert "quit" not in [c for c, _ in fake.sent]
    w._on_quit_response(None, "quit")
    assert ("quit", {}) in fake.sent


def test_beat_tile_and_pips_follow_the_clock(win):
    w, _ = win
    w.set_connection(True)
    w.apply_state(st(clock=dict(st()["clock"], beat=5.0)))
    w.state_time = time.monotonic()
    w._on_tick(None, None)  # the per-frame handler, called directly so the test does not depend on frame timing
    assert w._pip_current == 1  # beat 5 = second beat of the bar
    assert w.beat_tile.intensity > 0.9  # on the beat: red
    w.beat_tile._painted_at = 0.0  # the tile repaints at most 30 times a second; ignore that here
    w.state_time = time.monotonic() - 0.45  # 0.9 beat later at 120 BPM: nearly faded
    w._on_tick(None, None)
    assert w.beat_tile.intensity < 0.05
    w.state_time = time.monotonic() - 1.6  # 3.2 beats later: wrapped into the next bar (beat 8.2 -> pip 1)
    w._on_tick(None, None)
    assert w._pip_current == 0


def test_beat_tile_repaints_at_most_30_times_a_second(win):
    tile = win[0].beat_tile
    painted = []
    tile.queue_draw = lambda: painted.append(1)
    tile.intensity, tile._painted_at = 0.0, 0.0
    for i in range(1, 40):  # 39 changes within a few microseconds
        tile.set_intensity(i / 40)
    assert len(painted) == 1
    pump(40)
    tile.set_intensity(0.0)
    assert len(painted) == 2


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


# --- configuration dialog and calibration window ---------------------------------------------

from mirrorballs.common import paths  # noqa: E402
from mirrorballs.control.preferences import TABS  # noqa: E402


@pytest.fixture
def cfgwin(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MIRRORBALLS_CONFIG_DIR", str(tmp_path / "cfg"))
    bundled = scene_dir(tmp_path)
    fake = FakeClient()
    (tmp_path / "config.yaml").write_text("schema: 1\nmidi: {control_port: Pad}\n")
    w = MainWindow(app, fake, Library([bundled, paths.user_scenes_dir()]), ConfigStore(tmp_path / "config.yaml"))
    w.present()
    w.set_connection(True)
    w.apply_state(st(window=dict(width=1920, height=1080, fullscreen=True, monitor="HDMI-A-1")))
    pump(150)
    yield w, fake, tmp_path
    for x in (w.preferences_dialog, w.scenes_dialog, w.calibration_window):
        if x:
            x.destroy()
    w.destroy()


def test_clock_and_timing_settings_are_saved_and_the_renderer_reloads(cfgwin):
    w, fake, tmp = cfgwin
    w.open_preferences()
    d = w.preferences_dialog
    d.clock_seg.select("internal")
    d._on_clock_source("internal")
    d.bpm_entry.set_text("128")
    d.bpb.set_value(3, notify=True)
    d.offset.set_value(20, notify=True)
    d.offset.set_value(25, notify=True)
    d._flush()
    c = load_config(tmp / "config.yaml").clock
    assert (c.source, c.bpm, c.beats_per_bar, c.offset_ms) == ("internal", 128.0, 3, 25.0)
    assert ("reload", {}) in fake.sent
    d.bpm_entry.set_text("999")  # invalid values are flagged and never written
    d._flush()
    assert load_config(tmp / "config.yaml").clock.bpm == 128.0 and d.bpm_entry.has_css_class("error")


def test_beat_note_settings_default_to_mixxx_and_are_saved(cfgwin):
    w, fake, tmp = cfgwin
    w.open_preferences()
    d = w.preferences_dialog
    assert not d.beat_group.get_visible()  # only in beat-notes mode
    assert (d.beat_note.value, d.beat_velocity.value, d.midi_channel.value, d.tempo_note.value, d.tempo_offset.value) == (50, 100, 0, 52, 50)
    d.midi_mode_seg.select("beats")
    d._on_midi_mode("beats")
    assert d.beat_group.get_visible()
    d.beat_note.set_value(60, notify=True)
    d.beat_velocity.set_value(0, notify=True)
    d.midi_channel.set_value(3, notify=True)
    d.tempo_note.set_value(-1, notify=True)
    d._flush()
    c = load_config(tmp / "config.yaml").clock
    assert (c.midi_mode, c.beat_note, c.beat_velocity, c.midi_channel, c.tempo_note) == ("beats", 60, 0, 3, -1)
    assert ("reload", {}) in fake.sent


def test_downbeat_button_and_key_send_the_sync(win):
    w, fake = win
    assert not w.downbeat_btn.get_sensitive()
    w.set_connection(True)
    w.apply_state(st())
    assert w.downbeat_btn.get_sensitive()
    w.run_action("downbeat")
    assert fake.sent[-1] == ("sync_downbeat", {})


def test_detect_fills_in_the_beat_note_from_the_first_note(cfgwin):
    w, fake, tmp = cfgwin
    w.set_connection(True)
    w.apply_state(st())
    w.open_preferences()
    d = w.preferences_dialog
    d._toggle_detect()
    assert fake.sent[-1] == ("midi.learn", {"on": True, "role": "clock"}) and d.detecting
    d.on_event({"event": "midi_learned", "role": "control", "on": {"note": 1, "ch": 1}})  # not for us
    assert d.detecting
    d.on_event({"event": "midi_learned", "role": "clock", "on": {"note": 60, "ch": 1, "velocity": 100}})
    d._flush()
    c = load_config(tmp / "config.yaml").clock
    assert (c.beat_note, c.beat_velocity, c.midi_channel) == (60, 100, 1) and not d.detecting
    assert (d.beat_note.value, d.midi_channel.value) == (60, 1)


def test_the_port_list_starts_on_a_placeholder_while_no_port_is_saved(cfgwin):
    w, _, _ = cfgwin
    w.open_preferences()
    d = w.preferences_dialog
    d._ports = ["Midi Through 14:0", "Mixxx 130:0"]
    d._fill_ports()
    assert d.port_dd.get_model().get_string(0) == "Select a port…" and d.port_dd.get_selected() == 0
    d._on_port(0)  # the placeholder is not a port
    assert load_config(w.store.path).clock.midi_port is None
    d._on_port(2)
    d._flush()
    assert load_config(w.store.path).clock.midi_port == "Mixxx 130:0"


def test_midi_mapping_learn_flow(cfgwin):
    w, fake, tmp = cfgwin
    w.open_preferences()
    d = w.preferences_dialog
    d._add_mapping()  # starts listening because a control port is configured and the renderer is connected
    assert d.learn_row == 0 and d.learn_bar.get_visible() and ("midi.learn", {"on": True}) in fake.sent
    w.apply_event({"event": "midi_learned", "on": {"note": 36, "ch": 10}})
    assert d.learn_row is None and not d.learn_bar.get_visible()
    d._set_action(0, {"scene": "beta"})
    d._flush()
    m = load_config(tmp / "config.yaml").midi.map
    assert len(m) == 1 and (m[0].on.note, m[0].on.ch, m[0].do) == (36, 10, {"scene": "beta"})
    d._remove_mapping(0)
    d._flush()
    assert load_config(tmp / "config.yaml").midi.map == []


def test_only_the_settings_that_stay_put_are_tabs(cfgwin):
    # scenes and calibration are dialogs of their own, the window is positioned with GNOME,
    # and what is left of the director is set on the main window
    assert TABS == ["Clock", "MIDI"]


def test_the_calibration_menu_item_opens_the_wizard_itself(cfgwin):
    w, fake, tmp = cfgwin
    w.win_action("calibration")  # the menu item, above Configuration: no summary dialog in between
    assert w.calibration_window is not None and w.calibration_window.step_no == 1
    w.calibration_window.cancel()


def test_scene_file_operations(cfgwin):
    w, fake, tmp = cfgwin
    w.open_scenes()
    d = w.scenes_dialog
    d._do_add("Fresh One")
    assert d.selected_scene == "fresh-one" and "fresh-one" in w.library.scenes and d._is_user("fresh-one")
    assert d.rename_btn.get_sensitive() and d.delete_btn.get_sensitive()
    d._select("alpha")  # a bundled example: can be duplicated but not renamed or deleted
    assert d.dup_btn.get_sensitive() and not d.rename_btn.get_sensitive() and not d.delete_btn.get_sensitive()
    src = d._scene_path("alpha")
    from mirrorballs.control import scene_files as sf

    d._after_file_change(sf.duplicate_scene(src, paths.user_scenes_dir(), "Alpha copy", set(w.library.scenes)))
    assert "alpha-copy" in w.library.scenes
    sf.rename_scene(d._scene_path("alpha-copy"), "Renamed")
    w.library.refresh()
    assert w.library.scenes["alpha-copy"].name == "Renamed"
    sf.delete_scene(d._scene_path("fresh-one"))
    d._after_file_change(None)
    assert "fresh-one" not in w.library.scenes
    # a broken file shows up as an error row with its message
    (paths.user_scenes_dir() / "broken.yaml").write_text("schema: 1\nname: x\nlayers:\n  - {type: band, lat: 0, widht: 3}\n")
    d._after_file_change(None)
    assert "broken" in w.library.errors and any("broken.yaml" in t for t in texts(d.scene_group))


def test_calibration_window_flow_with_a_fake_renderer(cfgwin):
    w, fake, tmp = cfgwin
    w.open_calibration()
    cal = w.calibration_window
    assert cal.begun and ("calibration.begin", {}) in fake.sent
    assert fake.sent[-1][0] == "calibration.preview" and fake.sent[-1][1]["left"] == pytest.approx(0.5 - 0.7 / 2 * 9 / 16)
    s = cal.session
    assert (s.width, s.height) == (1920, 1080) and not s.dirty
    assert cal.read_centre.get_text() == "960, 540 px" and cal.read_size.get_text() == "756 × 756 px"
    for key in ("a", "a", "3", "d"):
        from mirrorballs.control.calibration_model import key_to_action

        cal._act(*key_to_action(key))
    assert s.dirty
    assert fake.sent[-1][0] == "calibration.preview" and fake.sent[-1][1]["active"] == "left"
    assert s.step == "coarse"
    cal._act("grid", None)
    assert fake.sent[-1][1]["grid"] is True
    fake.sent.clear()
    cal.finish()
    saved = load_config(tmp / "config.yaml").calibration
    assert saved is not None and saved.left == pytest.approx(s.cal.left) and saved.calibrated_at
    assert [c for c, _ in fake.sent] == ["calibration.end", "reload"] and fake.sent[0][1] == {"commit": True}
    assert w.calibration_window is None


def test_calibration_cancel_leaves_the_saved_values_alone(cfgwin):
    w, fake, tmp = cfgwin
    w.open_calibration()
    cal = w.calibration_window
    cal._act("line", ("top", +1))
    fake.sent.clear()
    cal.cancel()
    assert fake.sent == [("calibration.end", {"commit": False})]
    assert load_config(tmp / "config.yaml").calibration is None and w.calibration_window is None


def test_going_fullscreen_mid_calibration_keeps_the_measured_ball(cfgwin):
    w, fake, tmp = cfgwin
    (tmp / "config.yaml").write_text("calibration: {left: 0.303, right: 0.697, top: 0.15, bottom: 0.85}\n")
    w.apply_state(st(window=dict(width=1200, height=800, fullscreen=False, monitor=None)))
    w.open_calibration()
    cal = w.calibration_window
    assert cal.session.width == 1200 and not cal.session.fullscreen
    w.apply_state(st(window=dict(width=1920, height=1080, fullscreen=True, monitor="HDMI-A-1")))
    # a different window, not a resized one: the saved lines are fractions of it and stay as they are
    assert (cal.session.cal.left, cal.session.cal.bottom) == (0.303, 0.85)
    assert cal.read_size.get_text().startswith("756")  # round again, at the size it was measured
    w.apply_state(st(window=dict(width=1920, height=1200, fullscreen=True, monitor="HDMI-A-1")))
    assert cal.read_size.get_text().startswith("756")  # the same window resized: the ball keeps its pixels
    cal.cancel()


def test_calibration_banners_for_starting_and_windowed_output(cfgwin):
    w, fake, _ = cfgwin
    w.apply_state(st(window=dict(width=1280, height=720, fullscreen=False, monitor=None)))
    w.open_calibration()
    cal = w.calibration_window
    assert any("not fullscreen yet" in t for t in texts(cal.banner_box))
    w.set_connection(False)
    assert any("Starting the renderer" in t for t in texts(cal.banner_box)) and cal.session is None
    assert not cal.finish_btn.get_sensitive()
    cal.destroy()
    w.calibration_window = None


def test_calibration_against_a_live_renderer_core(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MIRRORBALLS_CONFIG_DIR", str(tmp_path / "cfg"))
    rig = Rig(tmp_path)
    got = {}
    win = MainWindow(app, None, Library([tmp_path / "scenes"]), ConfigStore(tmp_path / "config.yaml"))
    client = RendererClient(rig.sock, win.apply_state, win.set_connection, win.toast, win.apply_event)
    win.client = client
    client.start()
    win.present()
    try:
        deadline = time.time() + 5
        while not win.connected and time.time() < deadline:
            pump(20)
        rig.core.window = {"width": 1600, "height": 900, "fullscreen": True, "monitor": "X"}  # what the renderer app reports
        deadline = time.time() + 5
        while not (win.state and win.state.get("window")) and time.time() < deadline:
            pump(20)
        win.open_calibration()
        cal = win.calibration_window
        deadline = time.time() + 5
        while not rig.core.state(time.monotonic())["calibrating"] and time.time() < deadline:
            pump(20)
        assert rig.core.state(time.monotonic())["calibrating"] and (cal.session.width, cal.session.height) == (1600, 900)
        cal._act("step", "coarse")
        for _ in range(5):
            cal._act("move", (1, 0))
        deadline = time.time() + 5
        while rig.core._cal_preview is None or abs(rig.core._cal_preview["left"] - cal.session.cal.left) > 1e-9:
            pump(20)
            assert time.time() < deadline
        f = rig.core.frame(time.monotonic())  # the renderer is showing the lines the window sent
        assert f.overlay.lines[0] == pytest.approx(cal.session.cal.left)
        deadline = time.time() + 5
        while "Calibration in progress" not in "".join(texts(win.banner_box)) and time.time() < deadline:
            pump(20)  # the state that says "calibrating" travels over the socket
        assert "Calibration in progress" in "".join(texts(win.banner_box))
        cal.finish()
        deadline = time.time() + 5
        while rig.core.state(time.monotonic())["calibrating"] and time.time() < deadline:
            pump(20)
        c = rig.core.calibration(None)
        assert not rig.core.state(time.monotonic())["calibrating"]
        assert c.left * 1600 == pytest.approx((0.5 - 0.7 / 2 * 9 / 16) * 1600 + 50, abs=0.01)  # moved 5 x 10 px
        assert load_config(tmp_path / "config.yaml").calibration.left == pytest.approx(c.left)
    finally:
        client.close()
        win.destroy()
        rig.close()


def test_config_error_banner_has_an_editor_button(cfgwin):
    w, fake, tmp = cfgwin
    w.apply_state(st(config_error="config.yaml: clock.bpm: too small"))
    assert any("last good settings" in t for t in texts(w.banner_box)) and any("Open in editor" in t for t in texts(w.banner_box))


def test_tapped_tempo_is_saved_once_tapping_stops(cfgwin):
    w, fake, tmp = cfgwin
    w.apply_state(st(clock=dict(st()["clock"], source="internal", bpm=84.136)))
    w.act_tap()
    w.act_tap()
    assert [c for c, _ in fake.sent].count("tap") == 2 and w._tap_timer  # nothing is written while tapping
    assert load_config(tmp / "config.yaml").clock.bpm == 120
    w._persist_tempo()  # the settle timer firing
    assert load_config(tmp / "config.yaml").clock.bpm == 84.1
    # a MIDI clock owns the tempo: tapping never overwrites the configured internal BPM
    w.apply_state(st(clock=dict(st()["clock"], source="midi", bpm=128.0, port="X")))
    w._persist_tempo()
    assert load_config(tmp / "config.yaml").clock.bpm == 84.1


# --- calibration steps 1 (ball), 3 (ball axis) and 4 (tile rows) -------------------------------------------


def last_preview(fake):
    return next(kw for c, kw in reversed(fake.sent) if c == "calibration.preview")


def test_axis_step_drives_the_renderer_overlay_and_saves_roll_and_tilt(cfgwin):
    w, fake, tmp = cfgwin
    w.open_calibration()
    cal = w.calibration_window
    cal._go(3)
    p = last_preview(fake)
    assert cal.step_no == 3 and cal.pages.get_visible_child_name() == "3"
    assert p["lines"] is False and p["grid"] is True and p.get("rows") is None and len(p["crosshair"]) == 2
    assert cal.instruction.get_text().startswith("Move the crosshair onto the hanger eyelet")
    assert cal.read_tilt.get_text().startswith("0°") and "upright" in cal.read_roll.get_text()
    cal._axis_act("step", "coarse")
    for _ in range(8):
        cal._axis_act("cross", (0, 1))
    assert cal.axis.dirty and last_preview(fake)["tilt_deg"] > 5 and "above" in cal.read_tilt.get_text()
    cal._axis_set_above(False)
    assert last_preview(fake)["tilt_deg"] < -5 and "below" in cal.read_tilt.get_text()
    cal._axis_set_above(True)
    tilt = last_preview(fake)["tilt_deg"]
    cal.finish()  # finishing straight from step 2 keeps the angles
    saved = load_config(tmp / "config.yaml")
    assert saved.calibration.tilt_deg == pytest.approx(tilt, abs=1e-6)
    assert saved.ball.row_pitch_deg is None  # step 3 was never visited


def test_axis_numeric_fallback_and_reset(cfgwin):
    w, fake, tmp = cfgwin
    w.open_calibration()
    cal = w.calibration_window
    cal._go(3)
    cal.height_step.set_value(35, notify=True)
    cal.distance_step.set_value(2.4, notify=True)
    assert last_preview(fake)["tilt_deg"] == pytest.approx(8.3, abs=0.05)
    cal._go(4)  # Next applies the angles
    assert cal.session.cal.tilt_deg == pytest.approx(8.3, abs=0.05)
    cal._go(3)
    cal._do_reset(3)  # the page's own Reset to defaults: upright again
    assert last_preview(fake)["tilt_deg"] == 0.0 and cal.above_seg.selected() == "above"
    assert not cal.axis.dirty and cal.height_step.value == 0
    cal._go(4)
    assert cal.session.cal.tilt_deg == 0.0


def test_rows_step_previews_stripes_and_saves_only_what_was_changed(cfgwin):
    w, fake, tmp = cfgwin
    w.open_calibration()
    cal = w.calibration_window
    cal._go(4)
    p = last_preview(fake)
    assert p["lines"] is False and p["rows"] == {"mode": "all", "pitch_deg": 4.0, "offset_deg": 0.0} and p.get("crosshair") is None
    assert cal.read_rows.get_text() == "≈ 45 rows · 4.00° each" and cal.read_shift.get_text() == "+0.00 rows"
    cal.finish()  # nothing touched: the rows are not saved
    assert load_config(tmp / "config.yaml").ball.row_pitch_deg is None


def test_rows_step_nudges_and_persists(cfgwin):
    w, fake, tmp = cfgwin
    w.open_calibration()
    cal = w.calibration_window
    cal._go(4)
    cal._rows_act("step", "coarse")
    cal._rows_act("offset", +1)
    cal._rows_act("pitch", +1)
    cal._rows_set_pattern("equator")
    p = last_preview(fake)
    # coarse shift = a quarter of a row, and a row is now 4.1 degrees
    assert p["rows"] == {"mode": "equator", "pitch_deg": 4.1, "offset_deg": 1.025} and cal.rows_step.selected() == "coarse"
    assert cal.read_rows.get_text() == "≈ 44 rows · 4.10° each" and cal.read_shift.get_text() == "+0.25 rows"
    cal.finish()
    b = load_config(tmp / "config.yaml").ball
    assert (b.row_pitch_deg, b.row_offset_deg) == (4.1, 1.025)


def test_rows_start_from_the_configured_ball_size_and_back_navigation(cfgwin):
    w, fake, tmp = cfgwin
    (tmp / "config.yaml").write_text("ball: {diameter_cm: 30, tile_mm: 10}\n")
    w.open_calibration()
    cal = w.calibration_window
    cal._go(4)
    assert cal.read_rows.get_text() == "≈ 47 rows · 3.82° each"  # from the ball's size, as the design's example
    # 47 rows is odd, so the ball has a row centred on the equator and the nearest seam is half a row away
    assert cal.read_shift.get_text() == "-0.50 rows"
    cal._go(3)
    assert cal.step_no == 3 and last_preview(fake)["crosshair"] is not None
    cal._go(2)
    p = last_preview(fake)
    assert p.get("lines", True) is True and p.get("crosshair") is None and p.get("rows") is None
    assert cal.footers.get_visible_child_name() == "2"


def test_the_balls_size_seeds_the_pitch_and_is_remembered(cfgwin):
    w, fake, tmp = cfgwin
    w.open_calibration()
    cal = w.calibration_window
    cal._go(4)
    assert cal.diameter_step.value == 30 and cal.tile_step.value == 10  # placeholders until one is picked
    assert cal.rows.pitch == 4.0  # nothing on file: the default pitch, not one derived from the placeholders
    cal.diameter_step.choose(50)
    assert cal.rows.pitch == pytest.approx(180 / (3.14159265 * 500 / 20), rel=0.01)  # a 50 cm ball, 10 mm tiles
    assert last_preview(fake)["rows"]["pitch_deg"] == pytest.approx(cal.rows.pitch)
    cal.tile_step.choose(20)
    assert cal.rows.pitch == pytest.approx(2 * 180 / (3.14159265 * 500 / 20), rel=0.01)  # twice the tile, twice the pitch
    cal._go(5)
    cal.finish()
    b = load_config(tmp / "config.yaml").ball
    assert (b.diameter_cm, b.tile_mm) == (50, 20) and b.row_pitch_deg == pytest.approx(cal.rows.pitch, abs=1e-3)
    # and a second calibration starts from what was measured
    w.open_calibration()
    cal = w.calibration_window
    cal._go(4)
    assert cal.diameter_step.value == 50 and cal.tile_step.value == 20


def test_the_ball_step_holds_the_hardware_and_seeds_the_rows(cfgwin):
    w, fake, tmp = cfgwin
    w.open_calibration()
    cal = w.calibration_window
    assert cal.step_no == 1 and (cal.diameter_step.value, cal.tile_step.value) == (30, 10)
    assert last_preview(fake).get("lines", True) is True  # the silhouette lines, as on step 2
    # the sizes are picked from the list, not typed: 50 cm is the last entry, 20 mm the fourth
    cal.diameter_step.choose(50)
    cal.tile_step.choose(20)
    assert (cal.ball_diameter, cal.ball_tile) == (50, 20) and not cal.diameter_step.stepper.get_visible()
    cal._go(4)  # the rows step starts from the measurements, without them being typed there
    assert cal.rows.pitch == pytest.approx(cal._seeded_pitch(), abs=1e-6)
    cal._go(5)
    assert cal.done_ball.get_text() == "50 cm · 20 mm tiles · not turning"
    assert cal.done_rows.get_text().startswith("≈ 39 rows")  # measured, so the seeded grid is kept
    cal.finish()
    b = load_config(tmp / "config.yaml").ball
    assert (b.diameter_cm, b.tile_mm, b.rotation_rpm) == (50, 20, 0.0)
    assert b.row_pitch_deg == pytest.approx(cal._seeded_pitch(), abs=1e-3)


def test_an_unusual_ball_size_goes_in_under_other(cfgwin):
    w, fake, tmp = cfgwin
    w.open_calibration()
    cal = w.calibration_window
    dia = cal.diameter_step
    assert not dia.stepper.get_visible() and dia.dd.get_selected() == DIAMETERS_CM.index(30.0)
    dia._on_pick(len(DIAMETERS_CM))  # "Other…": the stepper appears to hold whatever is not in the list
    assert dia.stepper.get_visible()
    dia.stepper.set_value(37, notify=True)
    assert (dia.value, cal.ball_diameter) == (37, 37) and dia.dd.get_selected() == len(DIAMETERS_CM)
    cal._go(5)
    assert cal.done_ball.get_text().startswith("37 cm")
    cal.finish()
    assert load_config(tmp / "config.yaml").ball.diameter_cm == 37
    # reopening finds it again, still under "Other…"
    w.open_calibration()
    dia = w.calibration_window.diameter_step
    assert dia.value == 37 and dia.stepper.get_visible() and dia.dd.get_selected() == len(DIAMETERS_CM)


def test_the_ball_step_records_the_rotation_direction_and_speed(cfgwin):
    w, fake, tmp = cfgwin
    w.open_calibration()
    cal = w.calibration_window
    assert cal.rotation_seg.selected() == "still"
    assert not cal.speed_step.get_sensitive() and cal.speed_hint.get_text() == ""
    cal._ball_changed(direction="left")
    assert cal.speed_step.get_sensitive() and cal.speed_hint.get_text() == "≈ 10 s per turn"
    cal.speed_step.set_value(3.0, notify=True)
    assert cal.ball_rotation_rpm == -3.0 and cal.speed_hint.get_text() == "≈ 20 s per turn"
    cal._go(5)
    assert cal.done_ball.get_text() == "30 cm · 10 mm tiles · 3.0 rpm left"
    cal.finish()
    assert load_config(tmp / "config.yaml").ball.rotation_rpm == -3.0
    # and the next calibration starts from it, direction and all
    w.open_calibration()
    cal = w.calibration_window
    assert cal.rotation_seg.selected() == "left" and cal.speed_step.value == 3.0
    cal._ball_changed(direction="right")
    cal.finish()
    assert load_config(tmp / "config.yaml").ball.rotation_rpm == 3.0


def test_resetting_the_ball_step_puts_the_placeholders_back(cfgwin):
    w, fake, tmp = cfgwin
    (tmp / "config.yaml").write_text("ball: {diameter_cm: 50, tile_mm: 20, rotation_rpm: -1.5}\n")
    w.open_calibration()
    cal = w.calibration_window
    assert cal._step_dirty(1) is False  # straight from the file, nothing changed yet
    cal.speed_step.set_value(2.0, notify=True)
    assert cal._step_dirty(1) is True
    cal._do_reset(1)
    assert (cal.ball_diameter, cal.ball_tile, cal.ball_rotation_rpm) == (30.0, 10.0, 0.0)
    assert cal.rotation_seg.selected() == "still" and cal.diameter_step.value == 30
    cal._go(5)
    assert cal.done_ball.get_text() == "30 cm · 10 mm tiles · not turning"
    cal.finish()
    b = load_config(tmp / "config.yaml").ball
    assert (b.diameter_cm, b.tile_mm, b.rotation_rpm) == (30.0, 10.0, 0.0)


def test_every_step_leads_to_the_confirmation_screen(cfgwin):
    w, fake, tmp = cfgwin
    from mirrorballs.control.calibration_model import done_key_to_action, key_to_action, rows_key_to_action

    w.open_calibration()
    cal = w.calibration_window
    assert key_to_action("Return") == ("next", None)  # the silhouette no longer finishes early
    for step in (2, 3, 4, 5):
        cal._on_key(None, Gdk.KEY_Return, 0, Gdk.ModifierType(0))
        assert cal.step_no == step and cal.footers.get_visible_child_name() == str(step)
    assert rows_key_to_action("Return") == ("next", None) and done_key_to_action("Return") == ("finish", None)
    p = last_preview(fake)
    assert p["spin"] is True and p["grid"] is True and p["lines"] is False
    assert cal.done_silhouette.get_text() == "756 × 756 px at 960, 540"
    assert cal.done_axis.get_text() == "Not set" and cal.done_rows.get_text() == "Not set"
    spin = cal.done_diagram.spin
    cal._spin_tick()
    assert cal.done_diagram.spin > spin  # the globe turns while the step shows
    cal.finish()
    assert load_config(tmp / "config.yaml").calibration is not None


def test_every_editing_page_has_a_reset_and_none_has_a_skip(cfgwin):
    w, fake, tmp = cfgwin
    w.open_calibration()
    cal = w.calibration_window
    for step in (1, 2, 3, 4, 5):
        if step != 1:
            cal._go(step)
        labels = texts(cal.footers.get_child_by_name(str(step)))
        assert "Skip" not in labels
        assert ("Reset to defaults" in labels) is (step in (1, 2, 3, 4))
    cal._go(2)
    cal._act("line", ("top", +1))
    cal._do_reset(2)
    assert not cal.session.dirty and cal.read_size.get_text() == "756 × 756 px"


def test_resetting_the_rows_step_switches_snapping_off_again(cfgwin):
    w, fake, tmp = cfgwin
    (tmp / "config.yaml").write_text("ball: {row_pitch_deg: 3.8, row_offset_deg: 1.0}\n")
    w.open_calibration()
    cal = w.calibration_window
    cal._go(4)
    cal._rows_act("offset", +1)
    cal._go(5)
    assert cal.done_rows.get_text().startswith("≈")
    cal.finish()
    # a tenth of a row on top of the saved 1.0 degrees, with the shift itself kept to four decimals
    assert load_config(tmp / "config.yaml").ball.row_offset_deg == pytest.approx(1.3802)
    w.open_calibration()
    cal = w.calibration_window
    cal._go(4)
    cal._do_reset(4)
    assert cal.rows_cleared and cal.rows.pitch == 4.0 and cal.rows.offset == 0.0
    cal._go(5)
    assert cal.done_rows.get_text() == "Not set"
    cal.finish()
    b = load_config(tmp / "config.yaml").ball
    assert b.row_pitch_deg is None and b.row_offset_deg == 0.0


def test_step_keys_reach_the_right_handlers(cfgwin):
    w, fake, tmp = cfgwin
    from mirrorballs.control.calibration_model import axis_key_to_action, rows_key_to_action

    w.open_calibration()
    cal = w.calibration_window
    cal._go(3)
    cal._axis_act(*axis_key_to_action("Down"))
    assert cal.axis.py < 1.0
    cal._go(4)
    before = cal.rows.shift
    cal._rows_act(*rows_key_to_action("Up"))
    assert cal.rows.shift == pytest.approx(before + 0.1)  # one tenth of a row, whatever the pitch
    cal._rows_reset_shift()
    assert cal.rows.shift == 0.0
    cal._rows_act(*rows_key_to_action("z"))
    assert cal.rows.mode == "equator"


def test_calibration_window_fits_the_designed_720_pixel_width_on_every_step(cfgwin):
    w, fake, _ = cfgwin
    w.open_calibration()
    cal = w.calibration_window
    for step in (1, 2, 3, 4):
        cal._go(step)
        page = cal.pages.get_child_by_name(str(step)).measure(Gtk.Orientation.HORIZONTAL, -1)[0]
        footer = cal.footers.get_child_by_name(str(step)).measure(Gtk.Orientation.HORIZONTAL, -1)[0]
        assert page <= 720 and footer <= 720, (step, page, footer)
    assert cal.get_preferred_size().minimum_size.width <= 720


def test_three_step_calibration_against_a_live_renderer_core(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MIRRORBALLS_CONFIG_DIR", str(tmp_path / "cfg"))
    rig = Rig(tmp_path)
    win = MainWindow(app, None, Library([tmp_path / "scenes"]), ConfigStore(tmp_path / "config.yaml"))
    client = RendererClient(rig.sock, win.apply_state, win.set_connection, win.toast, win.apply_event)
    win.client = client
    client.start()
    win.present()

    def wait(cond, what):
        deadline = time.time() + 5
        while not cond() and time.time() < deadline:
            pump(20)
        assert cond(), what

    try:
        wait(lambda: win.connected, "connected")
        rig.core.window = {"width": 1920, "height": 1080, "fullscreen": True, "monitor": "X"}
        wait(lambda: win.state and win.state.get("window"), "window info")
        win.open_calibration()
        cal = win.calibration_window
        wait(lambda: rig.core.state(time.monotonic())["calibrating"], "calibrating")
        # step 2: the renderer draws the crosshair and the tilted grid, not the four lines
        cal._go(3)
        cal._axis_act("step", "coarse")
        for _ in range(8):
            cal._axis_act("cross", (0, 1))
        want = cal.axis.crosshair_norm()
        wait(lambda: rig.core._cal_preview and rig.core._cal_preview.get("crosshair")
             and abs(rig.core._cal_preview["crosshair"][1] - want[1]) < 1e-9, "the final crosshair position reached the renderer")
        f = rig.core.frame(time.monotonic())
        assert f.overlay.lines is None and f.overlay.grid and f.overlay.crosshair == pytest.approx(cal.axis.crosshair_norm())
        assert f.cal.tilt_deg == pytest.approx(cal.axis.calibration().tilt_deg) and f.cal.tilt_deg > 5
        # step 3: the renderer draws the row stripes for the pitch and offset chosen here
        cal._go(4)
        cal._rows_act("pitch", +1)
        wait(lambda: rig.core._cal_preview and (rig.core._cal_preview.get("rows") or {}).get("pitch_deg") == pytest.approx(4.02),
             "the row pitch reached the renderer")
        f = rig.core.frame(time.monotonic())
        assert f.overlay.rows == ("all", pytest.approx(4.02), pytest.approx(0.0)) and f.overlay.crosshair is None
        tilt = cal.session.cal.tilt_deg
        cal.finish()
        wait(lambda: not rig.core.state(time.monotonic())["calibrating"], "left calibration mode")
        wait(lambda: rig.core.config.ball.row_pitch_deg == pytest.approx(4.02), "renderer reloaded the ball rows")
        committed = rig.core.calibration(None)
        assert committed.tilt_deg == pytest.approx(tilt) and tilt > 5
        assert rig.core._tile() == (pytest.approx(4.02), pytest.approx(0.0))  # scenes now get tile = 4.02 and snap to rows
        cfg = load_config(tmp_path / "config.yaml")
        assert cfg.calibration.tilt_deg == pytest.approx(tilt) and cfg.ball.row_pitch_deg == pytest.approx(4.02)
    finally:
        client.close()
        win.destroy()
        rig.close()


def all_of(widget, cls, out=None):
    out = [] if out is None else out
    if isinstance(widget, cls):
        out.append(widget)
    c = widget.get_first_child()
    while c:
        all_of(c, cls, out)
        c = c.get_next_sibling()
    return out


@pytest.fixture
def pwin(app, tmp_path, monkeypatch):
    """A main window with previews, the helper replaced by one that writes small fake files in-process."""
    import numpy as np
    from PIL import Image

    from mirrorballs.control.preview_widgets import PreviewManager, PreviewPicture
    from mirrorballs.control.previews import PreviewCache

    jobs = []

    def fake_start(self, job):
        jobs.append(job)
        for s in job["scenes"]:
            frames = np.zeros((16, job["height"], job["width"], 3), dtype=np.uint8)
            frames[:, :, :, 0] = np.arange(16, dtype=np.uint8)[:, None, None] * 10  # a different red per frame
            Image.fromarray(frames[4]).save(self.cache.still_path(s["sid"], s["key"]))
            np.savez_compressed(self.cache.clip_path(s["sid"], s["key"]), frames=frames, fpb=8, loop_beats=2)
            self._changed(s["sid"])

    monkeypatch.setattr(PreviewManager, "_start", fake_start)
    (tmp_path / "cache").mkdir()
    w = MainWindow(app, FakeClient(), Library([scene_dir(tmp_path)]), ConfigStore(tmp_path / "config.yaml"),
                   previews=PreviewCache(tmp_path / "cache"))
    w.present()
    pump_until(lambda: jobs and all(p.get_paintable() for p in all_of(w.grid, PreviewPicture)))
    yield w, jobs, PreviewPicture
    w.destroy()


def test_card_previews_fill_the_card_at_16_9_and_never_get_smaller(pwin):
    w, jobs, PreviewPicture = pwin
    from mirrorballs.control.previews import OVERSAMPLE, THUMB_H, THUMB_W

    pics = all_of(w.grid, PreviewPicture)
    assert sorted(p.sid for p in pics) == ["alpha", "beta"]
    assert len(jobs) == 1 and {s["sid"] for s in jobs[0]["scenes"]} == {"alpha", "beta"}
    assert jobs[0]["width"] >= THUMB_W * OVERSAMPLE  # sharp even at the largest a card gets
    for width in (960, 1300, 1800):
        w.set_size_request(width, 700)
        pump(300)
        for p in all_of(w.grid, PreviewPicture):
            assert p.get_paintable() is not None and p.has_css_class("has-preview")
            pw, ph = p.get_width(), p.get_height()
            assert pw >= THUMB_W and abs(ph - pw * THUMB_H / THUMB_W) <= 1
            card = p.get_ancestor(Gtk.Button)
            assert card.get_width() - pw < 30  # it fills the card, up to the card's padding


def test_hovering_plays_the_loop_on_the_live_beat_and_leaving_shows_the_still(pwin):
    w, jobs, PreviewPicture = pwin
    w.set_connection(True)
    w.apply_state(st(clock=dict(source="internal", port=None, bpm=120.0, beat=9.5, phase=0.5, bar=2.375,
                                beats_per_bar=4, locked=True, status="internal")), t=time.monotonic())
    pic = next(p for p in all_of(w.grid, PreviewPicture) if p.sid == "beta")
    still = pic.get_paintable()
    pic._play()
    w.state_time = time.monotonic()  # beat 9.5 now: 1.5 beats into the 2-beat loop, frame 12
    pic._on_tick(pic, None)
    assert pic._shown in (12, 13) and pic.get_paintable() is not still
    pic._still()
    assert pic.get_paintable() is still and pic._clip is None


def test_an_accent_change_remakes_only_accented_previews(pwin, tmp_path):
    w, jobs, _ = pwin
    (tmp_path / "scenes" / "gamma.yaml").write_text("schema: 1\nname: Gamma\nlayers:\n  - {type: fill, color: accent}\n")
    w.library.refresh()
    w.previews.sync()
    assert [s["sid"] for s in jobs[-1]["scenes"]] == ["gamma"]
    w.set_accent("#ff0000")
    assert pump_until(lambda: len(jobs) == 3, 4000)
    assert [s["sid"] for s in jobs[-1]["scenes"]] == ["gamma"]


def test_the_scenes_dialog_shows_a_preview_per_valid_scene(pwin):
    w, jobs, PreviewPicture = pwin
    w.open_scenes()
    pump(100)
    pics = all_of(w.scenes_dialog.scene_group, PreviewPicture)
    assert sorted(p.sid for p in pics) == ["alpha", "beta"] and all(p.get_paintable() for p in pics)
    w.scenes_dialog.close()


def test_a_wider_window_gets_more_cards_per_row_not_wider_cards(app, tmp_path):
    d = scene_dir(tmp_path)
    for i in range(10):
        (d / f"s{i}.yaml").write_text(f"schema: 1\nname: S{i}\nlayers:\n  - {{type: fill}}\n")
    w = MainWindow(app, FakeClient(), Library([d]), ConfigStore(tmp_path / "config.yaml"))
    w.present()

    def rows():
        cards = [c.get_child() for c in all_of(w.grid, Gtk.FlowBoxChild)]
        tops = [round(c.compute_bounds(w.grid)[1].get_y()) for c in cards]
        return tops.count(tops[0]), max(c.get_width() for c in cards), min(c.get_width() for c in cards)

    try:
        w.set_default_size(960, 700)
        pump(300)
        narrow, nw, _ = rows()
        w.set_size_request(1800, 700)
        assert pump_until(lambda: rows()[0] > narrow, 3000), (w.get_width(), w.grid.get_width(), rows())
        wide, ww, wmin = rows()
        assert narrow >= 3 and wide >= 2 * narrow - 1
        assert ww < nw * 1.3  # lines are filled, but a card grows by a fraction of its width at most
    finally:
        w.destroy()


def test_hotkeys_are_renumbered_by_the_filter(win, tmp_path):
    w, fake = win
    (tmp_path / "scenes" / "gamma.yaml").write_text("schema: 1\nname: Gamma\ntags: [z]\nlayers:\n  - {type: fill}\n")
    w.library.refresh()
    w.set_connection(True)
    w.apply_state(st())
    w.refresh()
    assert "3" in texts(cards(w)[2])  # alpha, beta, gamma
    w._tag_buttons["z"].set_active(True)
    assert w.visible_ids() == ["gamma"] and "1" in texts(cards(w)[0])
    w.run_action("scene-key", "1")
    assert fake.sent[-1][0] == "scene.set" and fake.sent[-1][1]["scene"] == "gamma"


# --- clock sources: the audio and MIDI pickers ------------------------------------------------


def audio_state(source="alsa_output.speakers", locked=True, speakers_db=-14.0, mic_db=-90.0, error=None, running=True):
    return {"running": running, "error": error, "source": source, "wanted": source, "throttled": False, "previewing": True,
            "sources": [{"id": "alsa_output.speakers", "name": "Monitor of Speakers", "kind": "monitor", "channels": 2,
                         "rms_db": [speakers_db, speakers_db - 2], "peak_db": [speakers_db + 6, speakers_db + 4]},
                        {"id": "alsa_input.mic", "name": "Built-in mic", "kind": "mic", "channels": 2,
                         "rms_db": [mic_db, mic_db], "peak_db": [mic_db, mic_db]}],
            "tracking": {"bpm": 124.3, "confidence": 0.7, "locked": locked, "anchor_time": time.monotonic(),
                         "anchor_beat": 0.0},
            "health": None}


MIDI_PORTS = [{"name": "Mixxx 1", "status": "beats", "bpm": 100.0, "beat_time": 0.0, "note": 50},
              {"name": "Pads", "status": "active", "bpm": None, "beat_time": None, "note": None}]


def toasts_of(w):
    shown = []
    w.toast = shown.append
    return shown


def dropdown_labels(dd):
    m = dd.get_model()
    return [m.get_string(i) for i in range(m.get_n_items())]


def test_the_audio_dropdown_lists_the_sources_by_name_and_picks_one(win):
    w, fake = win
    shown = toasts_of(w)
    w.store.update({"clock": {"source": "audio", "audio_source": "alsa_output.speakers"}})
    w.cfg = w.store.load()
    w.set_connection(True)
    w.apply_state(st(clock=dict(st()["clock"], source="audio", bpm=124.3), audio=audio_state()))
    pump(50)
    dd = w.audio_picker
    assert dd.get_visible() and not w.midi_picker.get_visible()
    # full names (a monitor says so), no tempo or status in them
    assert dropdown_labels(dd) == ["Monitor of Speakers", "Built-in mic"]
    assert dd.selected_id() == "alsa_output.speakers"
    assert dd._meters["alsa_output.speakers"][0].levels == (-14.0, -16.0)  # the button's meter
    assert w.source_chip.get_text() == "Audio · 124.3 BPM · Monitor of Speakers"
    fake.sent.clear()
    dd.set_selected(1)  # chosen from the list
    assert w.store.load().clock.audio_source == "alsa_input.mic" and ("reload", {}) in fake.sent
    assert shown[-1] == "Audio · Built-in mic is silent"


def test_the_audio_dropdown_keeps_its_width_and_says_when_nothing_is_chosen(win):
    w, _ = win
    w.set_connection(True)
    w.store.update({"clock": {"source": "audio"}})
    w.cfg = w.store.load()
    w.apply_state(st(clock=dict(st()["clock"], source="audio"), audio=audio_state(source=None)))
    pump(50)
    dd = w.audio_picker
    assert dropdown_labels(dd) == ["Choose a source…", "Monitor of Speakers", "Built-in mic"] and dd.selected_id() == ""
    width = dd.get_preferred_size().natural_size.width
    w.apply_state(st(clock=dict(st()["clock"], source="audio"), audio=dict(audio_state(source=None), sources=[
        dict(audio_state()["sources"][0], name="Monitor of A Much Longer Output Name Than Usual")])))
    pump(50)
    assert dd.get_preferred_size().natural_size.width == width


def test_levels_move_without_refreshing_the_window(win):
    w, _ = win
    w.store.update({"clock": {"source": "audio", "audio_source": "alsa_output.speakers"}})
    w.cfg = w.store.load()
    w.set_connection(True)
    w.apply_state(st(clock=dict(st()["clock"], source="audio"), audio=audio_state()))
    pump(50)
    refreshes = []
    real = w.refresh
    w.refresh = lambda: (refreshes.append(1), real())
    w.apply_state(st(clock=dict(st()["clock"], source="audio"), audio=audio_state(speakers_db=-30.0)))
    meter = w.audio_picker._meters["alsa_output.speakers"][0]
    meter._painted = 0.0  # not throttled for the next one
    w.apply_state(st(clock=dict(st()["clock"], source="audio"), audio=audio_state(speakers_db=-8.0)))
    assert not refreshes
    assert meter.levels == (-8.0, -10.0)


IDLE_AUDIO = {"running": False, "error": None, "source": None, "wanted": "alsa_output.speakers", "sources": [],
              "tracking": None, "health": None, "throttled": False, "previewing": True}


def test_switching_to_audio_says_what_it_listens_to_and_later_whether_there_is_a_beat(win):
    w, _ = win
    shown = toasts_of(w)
    w.store.update({"clock": {"source": "audio", "audio_source": "alsa_output.speakers"}})
    w.cfg = w.store.load()
    w.set_connection(True)
    w.apply_state(st(clock=dict(st()["clock"], source="audio"), audio=audio_state()))  # the name is seen once
    w.store.update({"clock": {"source": "internal"}})
    w.cfg = w.store.load()
    w.apply_state(st(audio=IDLE_AUDIO))  # another clock: nothing listens
    w._clock_hold = 0.0
    w.apply_state(st(audio=IDLE_AUDIO))
    w.clock_seg.buttons["audio"].set_active(True)
    assert w.store.load().clock.source == "audio"
    assert shown[-1] == "Audio clock · listening to Monitor of Speakers (holding 120 BPM until the beat is found)"
    w.apply_state(st(clock=dict(st()["clock"], source="audio", locked=False), audio=audio_state(locked=False)))
    w._check_beat(None)  # what the timer does a few seconds later
    assert shown[-1] == "No beat found on Monitor of Speakers so far"


def test_the_hover_over_audio_shows_the_chosen_sources_tempo(win):
    w, _ = win
    w.store.update({"clock": {"source": "audio", "audio_source": "alsa_output.speakers"}})
    w.cfg = w.store.load()
    w.set_connection(True)
    w.apply_state(st(clock=dict(st()["clock"], source="audio"), audio=audio_state()))
    w.audio_hover.show_now()
    pump(50)
    assert w.audio_hover.visible and w.audio_hover.text.get_text() == "Monitor of Speakers · 124.3 BPM"
    w.apply_state(st(clock=dict(st()["clock"], source="audio"), audio=audio_state(locked=False)))
    assert w.audio_hover.text.get_text() == "Monitor of Speakers · no beat yet"
    # under another clock nothing listens: the hover names the source it would listen to
    w.store.update({"clock": {"source": "internal"}})
    w.cfg = w.store.load()
    w._clock_hold = 0.0
    w.apply_state(st(audio=IDLE_AUDIO))
    assert w.audio_hover.text.get_text() == "Audio · Monitor of Speakers · listened to while the audio clock runs"
    w.audio_hover.hide()
    pump(50)
    assert not w.audio_hover.visible


def test_the_midi_picker_previews_ports_only_while_it_shows(win):
    w, fake = win
    shown = toasts_of(w)
    w.store.update({"clock": {"source": "midi", "midi_port": "mixxx"}})
    w.cfg = w.store.load()
    w.set_connection(True)
    fake.sent.clear()
    w.apply_state(st(clock=dict(st()["clock"], source="midi", port="Mixxx 1"), midi_ports=MIDI_PORTS))
    assert ("midi.preview", {"on": True}) in fake.sent and w.midi_picker.get_visible()
    chips = w.midi_picker.chips
    assert chips["Mixxx 1"].get_active() and chips["Mixxx 1"].status.get_text() == "100 BPM"
    assert chips["Pads"].status.get_text() == "no beat"
    chips["Pads"].set_active(True)
    assert w.store.load().clock.midi_port == "Pads" and shown[-1] == "MIDI · Pads · no beat on this port yet"
    fake.sent.clear()
    w.apply_state(st())  # back on the internal clock
    w._clock_hold = 0.0
    w.apply_state(st())
    assert ("midi.preview", {"on": False}) in fake.sent and not w.midi_picker.get_visible()


def test_an_unavailable_listener_gets_a_banner_that_goes_back_to_internal(win):
    w, _ = win
    w.set_connection(True)
    w.apply_state(st(clock=dict(st()["clock"], source="audio", locked=False),
                     audio=audio_state(error="audio needs PyGObject and GStreamer", running=False)))
    assert any("not running: audio needs PyGObject" in t for t in texts(w.banner_box))
    assert w.source_chip.get_text() == "Audio · not available"


def test_the_preferences_audio_page_lists_sources_and_saves(cfgwin):
    w, fake, tmp = cfgwin
    w.apply_state(st(audio=audio_state(source=None)))
    w.open_preferences()
    d = w.preferences_dialog
    d.on_state(st(audio=audio_state(source=None)))
    d.clock_seg.buttons["audio"].set_active(True)
    names = [d.audio_dd.get_model().get_string(i) for i in range(d.audio_dd.get_model().get_n_items())]
    assert names == ["Select a source…", "Monitor of Speakers", "Built-in mic"]
    d.audio_dd.set_selected(1)
    d.audio_min.set_value(100, notify=True)
    d._flush()
    c = load_config(tmp / "config.yaml").clock
    assert (c.source, c.audio_source, c.audio_min_bpm) == ("audio", "alsa_output.speakers", 100.0)
    d.close()
