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

from mirrorballs.common.config import SequenceItem
from mirrorballs.control import tokens, viewmodel as vm
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


def test_every_stepper_is_off_when_a_sequence_sets_its_own_lengths(win):
    w, _ = win
    w.set_connection(True)
    w.apply_state(st(mode="sequence"))
    assert w.every_label.get_parent().get_sensitive()
    w.cfg.director.sequence = [SequenceItem(scene="alpha", bars=4)]
    w.refresh()
    assert not w.every_label.get_parent().get_sensitive()


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
    assert "Scroll" in w.source_chip.get_tooltip_text()
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
    assert not w._on_bpm_scroll(None, 0, -1.0) and fake.sent == []
    assert "MIDI" in w.source_chip.get_tooltip_text()


def test_beat_indicator_switch_is_on_by_default_and_saved(win):
    w, _ = win
    assert w.indicator_sw.get_active() and w.beat_tile.get_opacity() == 1.0 and w.pips_box.get_opacity() == 1.0
    before = w.clock_panel.get_width(), w.clock_panel.get_height()
    w.indicator_sw.set_active(False)
    pump(100)
    assert w.beat_tile.get_opacity() == 0.0 and w.pips_box.get_opacity() == 0.0
    assert (w.clock_panel.get_width(), w.clock_panel.get_height()) == before  # the layout stays put
    assert w.store.load().ui.beat_indicator is False
    w.indicator_sw.set_active(True)
    assert w.store.load().ui.beat_indicator is True


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
from mirrorballs.common.config import load_config  # noqa: E402


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
    for x in (w.config_dialog, w.calibration_window):
        if x:
            x.destroy()
    w.destroy()


def test_clock_and_timing_settings_are_saved_and_the_renderer_reloads(cfgwin):
    w, fake, tmp = cfgwin
    w.open_config()
    d = w.config_dialog
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


def test_output_tab_shows_window_state_and_toggles_the_test_pattern(cfgwin):
    w, fake, _ = cfgwin
    w.open_config()
    d = w.config_dialog
    assert "HDMI-A-1 · 1920 × 1080" in d.window_sub.get_text()
    assert "Fullscreen" in texts(d.window_chip_box)
    w.apply_state(st(window=dict(width=1280, height=720, fullscreen=False, monitor=None)))
    assert "Windowed" in texts(d.window_chip_box)
    d._toggle_test_pattern()
    assert fake.sent[-1] == ("testpattern.set", {"on": True})
    w.apply_state(st(test_pattern=True))
    assert "Hide test pattern" in texts(d.test_btn)


def test_midi_mapping_learn_flow(cfgwin):
    w, fake, tmp = cfgwin
    w.open_config()
    d = w.config_dialog
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


def test_director_ball_and_calibration_pages(cfgwin):
    w, fake, tmp = cfgwin
    w.open_config()
    d = w.config_dialog
    d.every.set_value(4, notify=True)
    d.no_repeat.set_active(False)
    d._seq_add()
    d._seq_set(0, scene="beta", bars=2)
    d.d_step.set_value(40, notify=True)  # diameter 40 cm with 10 mm tiles
    d._flush()
    cfg = load_config(tmp / "config.yaml")
    assert cfg.director.every.bars == 4 and cfg.director.no_repeat is False
    assert [(s.scene, s.bars) for s in cfg.director.sequence] == [("beta", 2)]
    assert cfg.ball.diameter_cm == 40 and cfg.ball.row_pitch_deg == pytest.approx(180 / (3.14159265 * 400 / 20), rel=0.02)
    assert d.rows_step.value == 63  # rows follow the diameter (pi * 400 / 20)
    d.rows_step.set_value(50, notify=True)  # and the tile size follows the rows
    assert d.tile_step.value == round(3.14159265 * 400 / 100)
    assert d.cal_centre.get_text() == "Not calibrated"
    (tmp / "config.yaml").write_text("calibration: {left: 0.3, right: 0.7, top: 0.2, bottom: 0.8, calibrated_at: '2020-01-02T03:04'}\n")
    d.cfg = w.store.load()
    d._update_calibration()
    assert (d.cal_centre.get_text(), d.cal_size.get_text()) == ("960, 540 px", "768 × 648 px")
    assert d.cal_when.get_text() == "2 Jan, 03:04"


def test_scene_file_operations(cfgwin):
    w, fake, tmp = cfgwin
    w.open_config()
    d = w.config_dialog
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
    assert (s.width, s.height) == (1920, 1080) and not s.dirty and not cal.edited_chip.get_visible()
    assert cal.read_centre.get_text() == "960, 540 px" and cal.read_size.get_text() == "756 × 756 px"
    for key in ("a", "a", "3", "d"):
        from mirrorballs.control.calibration_model import key_to_action

        cal._act(*key_to_action(key))
    assert s.dirty and cal.edited_chip.get_visible()
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


# --- calibration steps 2 (ball axis) and 3 (tile rows) -------------------------------------------


def last_preview(fake):
    return next(kw for c, kw in reversed(fake.sent) if c == "calibration.preview")


def test_axis_step_drives_the_renderer_overlay_and_saves_roll_and_tilt(cfgwin):
    w, fake, tmp = cfgwin
    w.open_calibration()
    cal = w.calibration_window
    cal._go(2)
    p = last_preview(fake)
    assert cal.step_no == 2 and cal.pages.get_visible_child_name() == "2"
    assert p["lines"] is False and p["grid"] is True and p.get("rows") is None and len(p["crosshair"]) == 2
    assert cal.instruction.get_text().startswith("Move the crosshair onto the hanger eyelet")
    assert cal.read_tilt.get_text().startswith("0°") and "upright" in cal.read_roll.get_text()
    cal._axis_act("step", "coarse")
    for _ in range(8):
        cal._axis_act("cross", (0, 1))
    assert cal.axis.dirty and last_preview(fake)["tilt_deg"] > 5 and "above" in cal.read_tilt.get_text()
    assert cal.edited_chip.get_visible()
    cal._axis_set_above(False)
    assert last_preview(fake)["tilt_deg"] < -5 and "below" in cal.read_tilt.get_text()
    cal._axis_set_above(True)
    tilt = last_preview(fake)["tilt_deg"]
    cal.finish()  # finishing straight from step 2 keeps the angles
    saved = load_config(tmp / "config.yaml")
    assert saved.calibration.tilt_deg == pytest.approx(tilt, abs=1e-6)
    assert saved.ball.row_pitch_deg is None  # step 3 was never visited


def test_axis_numeric_fallback_and_skip(cfgwin):
    w, fake, tmp = cfgwin
    w.open_calibration()
    cal = w.calibration_window
    cal._go(2)
    cal.height_step.set_value(35, notify=True)
    cal.distance_step.set_value(2.4, notify=True)
    assert last_preview(fake)["tilt_deg"] == pytest.approx(8.3, abs=0.05)
    cal._go(3, apply_axis=False)  # Skip: the angles are not applied
    assert cal.session.cal.tilt_deg == 0.0 and last_preview(fake)["tilt_deg"] == 0.0
    cal._go(2)
    cal._go(3)  # Next: they are
    assert cal.session.cal.tilt_deg == 0.0  # the skipped values were discarded with the old axis session


def test_rows_step_previews_stripes_and_saves_only_what_was_changed(cfgwin):
    w, fake, tmp = cfgwin
    w.open_calibration()
    cal = w.calibration_window
    cal._go(3)
    p = last_preview(fake)
    assert p["lines"] is False and p["rows"] == {"mode": "zebra", "pitch_deg": 4.0, "offset_deg": 0.0} and p.get("crosshair") is None
    assert cal.read_rows.get_text() == "rows ≈ 45.0 · tile 4.0°"
    cal.finish()  # nothing touched: the rows are not saved
    assert load_config(tmp / "config.yaml").ball.row_pitch_deg is None


def test_rows_step_nudges_and_persists(cfgwin):
    w, fake, tmp = cfgwin
    w.open_calibration()
    cal = w.calibration_window
    cal._go(3)
    cal._rows_act("step", "coarse")
    cal._rows_act("offset", +1)
    cal._rows_act("pitch", +1)
    cal._rows_set_pattern("single")
    p = last_preview(fake)
    assert p["rows"] == {"mode": "single", "pitch_deg": 4.1, "offset_deg": 1.0} and cal.rows_step.selected() == "coarse"
    assert cal.read_rows.get_text() == "rows ≈ 43.9 · tile 4.1°" and cal.edited_chip.get_visible()
    cal.finish()
    b = load_config(tmp / "config.yaml").ball
    assert (b.row_pitch_deg, b.row_offset_deg) == (4.1, 1.0)


def test_rows_start_from_the_configured_ball_size_and_back_navigation(cfgwin):
    w, fake, tmp = cfgwin
    (tmp / "config.yaml").write_text("ball: {diameter_cm: 30, tile_mm: 10}\n")
    w.open_calibration()
    cal = w.calibration_window
    cal._go(3)
    assert cal.read_rows.get_text() == "rows ≈ 47.1 · tile 3.8°"  # from the ball's size, as the design's example
    cal._go(2)
    assert cal.step_no == 2 and last_preview(fake)["crosshair"] is not None
    cal._go(1)
    p = last_preview(fake)
    assert p.get("lines", True) is True and p.get("crosshair") is None and p.get("rows") is None
    assert cal.footers.get_visible_child_name() == "1"


def test_step_keys_reach_the_right_handlers(cfgwin):
    w, fake, tmp = cfgwin
    from mirrorballs.control.calibration_model import axis_key_to_action, rows_key_to_action

    w.open_calibration()
    cal = w.calibration_window
    cal._go(2)
    cal._axis_act(*axis_key_to_action("Down"))
    assert cal.axis.py < 1.0
    cal._go(3)
    before = cal.rows.offset
    cal._rows_act(*rows_key_to_action("Up"))
    assert cal.rows.offset == pytest.approx(before + 0.2)
    cal._rows_act(*rows_key_to_action("z"))
    assert cal.rows.mode == "single"


def test_calibration_window_fits_the_designed_720_pixel_width_on_every_step(cfgwin):
    w, fake, _ = cfgwin
    w.open_calibration()
    cal = w.calibration_window
    for step in (1, 2, 3):
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
        cal._go(2)
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
        cal._go(3)
        cal._rows_act("pitch", +1)
        wait(lambda: rig.core._cal_preview and (rig.core._cal_preview.get("rows") or {}).get("pitch_deg") == pytest.approx(4.02),
             "the row pitch reached the renderer")
        f = rig.core.frame(time.monotonic())
        assert f.overlay.rows == ("zebra", pytest.approx(4.02), pytest.approx(0.0)) and f.overlay.crosshair is None
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


def test_advanced_calibration_opens_on_the_axis_step(cfgwin):
    w, fake, _ = cfgwin
    w.open_config()
    w.config_dialog.show_tab("Ball")
    w.open_calibration(step=2)
    cal = w.calibration_window
    assert cal.step_no == 2 and cal.pages.get_visible_child_name() == "2" and cal.footers.get_visible_child_name() == "2"
    assert last_preview(fake)["lines"] is False and last_preview(fake)["crosshair"] is not None

