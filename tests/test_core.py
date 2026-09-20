import random

import pytest

from mirrorballs.common import protocol as P
from mirrorballs.common.geometry import Calibration
from mirrorballs.render.core import Core

SCENE = "schema: 1\nname: {n}\ntags: [{tags}]\nlayers:\n  - {{type: fill, color: white}}\n"


@pytest.fixture
def env(tmp_path):
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    for n, tags in (("a", "calm"), ("b", "beat"), ("c", "beat, calm")):
        (scenes / f"{n}.yaml").write_text(SCENE.format(n=n, tags=tags))
    cfg = tmp_path / "config.yaml"
    return tmp_path, cfg, scenes


def make(env, config_text=None, now=100.0, seed=1):
    tmp, cfg, scenes = env
    if config_text is not None:
        cfg.write_text(config_text)
    return Core(cfg, [scenes], now=now, rng=random.Random(seed))


def cmd(core, msg, now=100.0, client="c1"):
    return core.handle(P.parse_command(msg), client, now)


def test_starts_on_first_scene_and_reports_state(env):
    c = make(env)
    st = c.state(100.0)
    assert st["scene"] == "a" and st["scenes"] == ["a", "b", "c"] and st["running"] and st["mode"] == "manual"
    assert st["clock"]["source"] == "internal" and st["clock"]["bpm"] == 120
    assert cmd(c, {"cmd": "hello"})["state"]["scene"] == "a"
    bad = cmd(c, {"cmd": "hello", "proto": 99})
    assert not bad["ok"] and "protocol" in bad["error"]


def test_scene_set_cut_and_crossfade(env):
    c = make(env)
    assert cmd(c, {"cmd": "scene.set", "scene": "b", "transition": {"type": "cut", "beats": 0}})["ok"]
    f = c.frame(100.0)
    assert c.current == "b" and f.mix == 1.0 and f.outgoing is None
    cmd(c, {"cmd": "scene.set", "scene": "c", "transition": {"type": "crossfade", "beats": 2}}, now=101.0)
    mid = c.frame(101.5)  # 120 BPM: 2 beats = 1 s, halfway
    assert mid.transition == "crossfade" and mid.mix == pytest.approx(0.5) and mid.outgoing is not None
    assert c.frame(102.5).mix == 1.0 and c.previous is None
    r = cmd(c, {"cmd": "scene.set", "scene": "nope"})
    assert not r["ok"] and "unknown scene" in r["error"]


def test_scene_transition_precedence_uses_scene_then_config(env):
    tmp, cfg, scenes = env
    (scenes / "b.yaml").write_text("schema: 1\nname: b\ntransition: {in: {type: iris, beats: 4}}\nlayers:\n  - {type: fill}\n")
    c = make(env, "director: {transition: {type: flash, beats: 1}}\n")
    cmd(c, {"cmd": "scene.set", "scene": "b"})
    assert c.frame(100.5).transition == "iris"
    c.frame(110.0)
    cmd(c, {"cmd": "scene.set", "scene": "c"}, now=110.0)
    assert c.frame(110.25).transition == "flash"


def test_random_transition_picks_a_concrete_type_on_every_scene_change(env):
    c = make(env, "director: {transition: {type: random, beats: 2}}\n")
    seen = set()
    for i in range(40):
        cmd(c, {"cmd": "scene.set", "scene": "b" if i % 2 == 0 else "c"}, now=100.0 + 10 * i)
        t = c.frame(100.0 + 10 * i + 0.1).transition
        if t is not None:
            seen.add(t)
    assert seen and "random" not in seen and len(seen) >= 3
    assert seen <= {"crossfade", "cut", "wipe_lat", "iris", "flash"}


def test_bpm_set_retunes_the_internal_clock_and_keeps_the_beat_continuous(env):
    c = make(env, "clock: {source: internal, bpm: 120}\n")
    before = c.clock.snapshot(101.0).beat
    assert cmd(c, {"cmd": "bpm.set", "bpm": 90}, now=101.0)["ok"]
    snap = c.clock.snapshot(101.0)
    assert snap.bpm == 90 and snap.beat == pytest.approx(before)
    with pytest.raises(ValueError):
        cmd(c, {"cmd": "bpm.set", "bpm": 5}, now=101.0)


def test_next_change_beat_follows_the_mode(env):
    c = make(env, "clock: {source: internal, bpm: 120, beats_per_bar: 4}\n", now=100.0)
    assert c.state(100.0)["next_change_beat"] is None and c.state(100.0)["rev"] == P.REV  # manual
    cmd(c, {"cmd": "mode.set", "mode": "random", "every": {"bars": 8}})
    assert c.state(100.0)["next_change_beat"] == 32.0 and c.state(100.0)["change_interval_beats"] == 32.0
    beat = c.clock.snapshot(116.0).beat  # 32 beats later
    assert c.state(116.0)["next_change_beat"] == (int(beat // 32) + 1) * 32
    cmd(c, {"cmd": "calibration.begin"}, client="cal")
    assert c.state(100.0)["next_change_beat"] is None


def test_next_prev_wrap(env):
    c = make(env)
    for expect in ("b", "c", "a"):
        cmd(c, {"cmd": "scene.next"})
        assert c.current == expect
    cmd(c, {"cmd": "scene.prev"})
    assert c.current == "c"


def test_params_clamped_and_validated(env):
    tmp, cfg, scenes = env
    (scenes / "p.yaml").write_text(
        "schema: 1\nname: p\nparams:\n  w: {type: float, default: 5, min: 1, max: 10}\n"
        "  k: {type: int, default: 2}\n  m: {type: choice, default: x, choices: [x, y]}\nlayers:\n  - {type: fill}\n")
    c = make(env)
    cmd(c, {"cmd": "scene.set", "scene": "p", "transition": {"type": "cut", "beats": 0}})
    assert cmd(c, {"cmd": "param.set", "name": "w", "value": 99})["ok"]
    assert c.state(100.0)["params"] == {"w": 10.0, "k": 2, "m": "x"}
    cmd(c, {"cmd": "param.set", "name": "k", "value": 3.6})
    assert c.params["p"]["k"] == 4
    assert not cmd(c, {"cmd": "param.set", "name": "m", "value": "z"})["ok"]
    assert "no parameter" in cmd(c, {"cmd": "param.set", "name": "nope", "value": 1})["error"]


def test_random_mode_switches_on_the_grid_and_respects_pool(env):
    c = make(env, "clock: {bpm: 120, beats_per_bar: 4}\ndirector: {mode: random, every: {beats: 4}, pool: {tags: [beat]}}\n")
    assert c.mode == "random" and c.every_beats == 4
    seen = set()
    t = 100.0
    c.frame(t)
    for _ in range(40):
        t += 2.0  # 4 beats at 120 BPM
        c.frame(t)
        seen.add(c.current)
    assert seen <= {"b", "c", "a"} and {"b", "c"} <= seen
    # after the first switch away from 'a' (not in the pool) it stays within the pool
    later = set()
    for _ in range(30):
        t += 2.0
        c.frame(t)
        later.add(c.current)
    assert later == {"b", "c"}


def test_no_switch_within_the_slot_and_manual_never_switches(env):
    c = make(env, "director: {mode: random, every: {beats: 8}}\n")
    c.frame(100.0)
    for k in range(1, 8):
        c.frame(100.0 + k * 0.5)  # 4 beats total: still the same slot
    assert c.current == "a"
    m = make(env)
    for k in range(50):
        m.frame(100.0 + k)
    assert m.current == "a"


def test_sequence_mode_cycles_in_order(env):
    c = make(env, "director: {mode: sequence, every: {beats: 4}}\n")
    order, t = [], 100.0
    c.frame(t)
    for _ in range(6):
        t += 2.0
        c.frame(t)
        order.append(c.current)
    assert order == ["b", "c", "a", "b", "c", "a"]


def test_mode_set_from_client(env):
    c = make(env)
    cmd(c, {"cmd": "mode.set", "mode": "random", "every": {"bars": 2}})
    assert c.mode == "random" and c.every_beats == 8
    assert c.state(100)["every_beats"] == 8


def test_blackout_fades_and_master(env):
    c = make(env)
    assert c.frame(100.0).master == 1.0 and not c.frame(100.0).black
    cmd(c, {"cmd": "running.set", "running": False}, now=101.0)
    half = c.frame(101.2)
    assert 0.0 < half.master < 1.0 and not half.black
    end = c.frame(102.0)
    assert end.master == 0.0 and end.black
    cmd(c, {"cmd": "running.set", "running": True}, now=103.0)
    assert c.frame(103.5).master == 1.0
    cmd(c, {"cmd": "master.set", "value": 0.25})
    assert c.frame(104.0).master == pytest.approx(0.25)
    with pytest.raises(ValueError, match="value"):
        P.parse_command({"cmd": "master.set", "value": 3})


def test_midi_triggers(env):
    c = make(env, """\
midi:
  control_port: pad
  map:
    - {on: {note: 36, ch: 10}, do: {scene: b}}
    - {on: {program: 2}, do: {scene_index: 2}}
    - {on: {cc: 7}, do: master}
    - {on: {note: 48}, do: blackout}
    - {on: {note: 50}, do: next}
""")
    ctl = {"control"}
    c.on_midi(ctl, bytes([0x99, 36, 100]), 100.0)  # note-on ch 10
    assert c.current == "b"
    c.on_midi(ctl, bytes([0x90, 36, 100]), 100.0)  # wrong channel: ignored
    c.on_midi(ctl, bytes([0x99, 36, 0]), 100.0)  # velocity 0 = note off: ignored
    c.on_midi(ctl, bytes([0xC0, 2]), 100.0)
    assert c.current == "c"
    c.on_midi(ctl, bytes([0xB3, 7, 64]), 100.0)
    assert c.master == pytest.approx(64 / 127)
    c.on_midi(ctl, bytes([0x90, 48, 90]), 100.0)
    assert not c.running
    c.on_midi(ctl, bytes([0x90, 50, 90]), 100.0)
    assert c.current == "a"
    c.on_midi(ctl, bytes([0xC0, 99]), 100.0)  # out-of-range index: logged, not raised
    assert c.midi_wants() == (None, "pad")


def test_midi_clock_routing(env):
    c = make(env, "clock: {source: midi, midi_port: 'Test'}\n")
    assert c.midi_wants() == ("Test", None) and c.clock.source == "midi"
    for k in range(50):
        c.on_midi({"clock"}, bytes([0xF8]), 100.0 + k * 60 / (120 * 24))
    snap = c.clock.snapshot(100.0 + 49 * 60 / (120 * 24))
    assert snap.locked and snap.bpm == pytest.approx(120, abs=1)
    c.on_midi({"clock"}, bytes([0xFA]), 105.0)
    assert c.clock.snapshot(105.0).beat == pytest.approx(0.0)
    c.on_midi({"clock"}, bytes([0xF2, 8, 0]), 105.0)  # SPP 8 sixteenths
    internal = make(env)  # clock messages are ignored on an internal clock
    internal.on_midi({"clock"}, bytes([0xFA]), 100.0)
    assert internal.clock.snapshot(101.0).beat == pytest.approx(2.0)


def test_calibration_mode(env):
    c = make(env)
    assert cmd(c, {"cmd": "calibration.begin"}, client="ctl")["ok"]
    assert c.state(100)["calibrating"]
    assert "already in progress" in cmd(c, {"cmd": "calibration.begin"}, client="other")["error"]
    assert "not calibrating" in cmd(c, {"cmd": "calibration.end"}, client="other")["error"]
    f = c.frame(100.0)  # before any preview: shows the current (default) lines, no scene
    assert f.incoming is None and f.overlay.lines is not None
    cmd(c, {"cmd": "calibration.preview", "left": 0.3, "right": 0.7, "top": 0.2, "bottom": 0.8,
            "active": "left", "grid": True}, client="ctl")
    f = c.frame(100.0)
    assert f.overlay.lines == (0.3, 0.7, 0.2, 0.8) and f.overlay.levels == (1.0, 0.6, 0.6, 0.6) and f.overlay.grid
    cmd(c, {"cmd": "calibration.preview", "left": 0.3, "right": 0.7, "top": 0.2, "bottom": 0.8, "blink": True}, client="ctl")
    assert {c.frame(t).overlay.levels[0] for t in (100.0, 100.5)} == {0.0, 0.6}  # blinks at 2 Hz
    # cancel restores the (default) calibration
    cmd(c, {"cmd": "calibration.end", "commit": False}, client="ctl")
    assert c.config.calibration is None and not c.state(100)["calibrating"]
    # commit adopts the preview immediately
    cmd(c, {"cmd": "calibration.begin"}, client="ctl")
    cmd(c, {"cmd": "calibration.preview", "left": 0.31, "right": 0.69, "top": 0.19, "bottom": 0.81}, client="ctl")
    cmd(c, {"cmd": "calibration.end", "commit": True}, client="ctl")
    assert c.calibration(None) == Calibration(0.31, 0.69, 0.19, 0.81)
    assert c.frame(100.0).cal == Calibration(0.31, 0.69, 0.19, 0.81) and c.frame(100.0).incoming is not None


def test_disconnect_ends_calibration_and_ignores_scene_triggers_meanwhile(env):
    c = make(env)
    cmd(c, {"cmd": "calibration.begin"}, client="ctl")
    c._midi_action({"scene": "b"}, None, 100.0)
    assert c.current == "a"
    c.client_disconnected("someone-else", 100.0)
    assert c.state(100)["calibrating"]
    c.client_disconnected("ctl", 100.0)
    assert not c.state(100)["calibrating"] and c.frame(100.0).incoming is not None


def test_hot_reload_scene_config_and_error_isolation(env):
    tmp, cfg, scenes = env
    c = make(env)
    cmd(c, {"cmd": "scene.set", "scene": "b", "transition": {"type": "cut", "beats": 0}})
    assert not c.poll_files(101.0)
    (scenes / "d.yaml").write_text(SCENE.format(n="d", tags=""))
    assert c.poll_files(102.0) and "d" in c.scene_ids() and c.current == "b"
    # a scene that breaks while it is the active one keeps running with its last good version
    (scenes / "b.yaml").write_text("schema: 1\nname: b\nlayers:\n  - {type: band, lat: fase, width: 3}\n")
    assert c.poll_files(103.0)
    assert "b" in c.scene_errors and "fase" in c.state(103.0)["scene_errors"]["b"][0]
    assert c.current == "b" and c.frame(103.0).incoming.shapes
    # fixing it clears the error
    (scenes / "b.yaml").write_text(SCENE.format(n="b", tags=""))
    c.poll_files(104.0)
    assert not c.scene_errors
    # removing the active scene falls back to the first one
    (scenes / "b.yaml").unlink()
    c.poll_files(105.0)
    assert c.current == "a"
    # config: clock changes apply, tap-tempo changes survive an unrelated reload
    cfg.write_text("clock: {bpm: 100}\n")
    c.poll_files(106.0)
    assert c.clock.snapshot(106.0).bpm == 100
    for t in (200.0, 200.4, 200.8):
        cmd(c, {"cmd": "tap"}, now=t)
    tapped = c.clock.snapshot(200.8).bpm
    assert tapped == pytest.approx(150)
    cfg.write_text("clock: {bpm: 100}\nball: {row_pitch_deg: 3.5}\n")
    c.poll_files(201.0)
    assert c.clock.snapshot(201.0).bpm == pytest.approx(150)
    assert c._tile() == (3.5, 0.0)


def test_broken_config_keeps_previous_settings(env):
    tmp, cfg, scenes = env
    c = make(env, "clock: {bpm: 140}\n")
    cfg.write_text("clock: {bpm: 5}\n")
    c.poll_files(101.0)
    assert c.config.clock.bpm == 140 and "clock.bpm" in c.state(101.0)["config_error"]


def test_beat_reset_does_not_leave_negative_scene_beat(env):
    tmp, cfg, scenes = env
    (scenes / "s.yaml").write_text("schema: 1\nname: s\nlayers:\n  - {type: circle, at: {lat: 0, lon: 0}, radius: 'scene_beat'}\n")
    c = make(env, "clock: {source: midi, midi_port: x}\n")
    for k in range(60):
        c.on_midi({"clock"}, bytes([0xF8]), 100.0 + k * 60 / (120 * 24))
    cmd(c, {"cmd": "scene.set", "scene": "s", "transition": {"type": "cut", "beats": 0}}, now=102.5)
    c.on_midi({"clock"}, bytes([0xFA]), 103.0)  # Start: beat jumps back to 0
    f = c.frame(103.05)
    assert f.incoming.shapes[0].outer >= 0.0


def test_tick_drives_the_director_without_any_frames(env):
    """The control thread calls tick(); scene changes must not depend on frames being drawn."""
    c = make(env, "director: {mode: sequence, every: {beats: 4}}\n")
    c.tick(100.0)
    assert c.current == "a"
    c.tick(102.1)  # 4+ beats later at 120 BPM, no frame() calls in between
    assert c.current == "b"
    c.tick(102.2)
    assert c.current == "b"  # same slot: no double switch


def test_test_pattern_and_window_info(env):
    c = make(env)
    assert c.state(100.0)["window"] is None and not c.state(100.0)["test_pattern"]
    c.window = {"width": 1920, "height": 1080, "fullscreen": True, "monitor": "HDMI-A-1"}
    assert c.state(100.0)["window"]["monitor"] == "HDMI-A-1"
    assert cmd(c, {"cmd": "testpattern.set", "on": True})["ok"]
    f = c.frame(100.0)
    assert f.incoming is None and f.overlay.grid and f.overlay.lines is not None
    cmd(c, {"cmd": "testpattern.set", "on": False})
    assert c.frame(100.0).incoming is not None


def test_midi_learn_reports_the_next_control_message_without_acting_on_it(env):
    c = make(env, "midi:\n  control_port: pad\n  map:\n    - {on: {note: 36}, do: {scene: b}}\n")
    assert cmd(c, {"cmd": "midi.learn", "on": True})["ok"] and c.state(100.0)["learning"]
    c.on_midi({"control"}, bytes([0x90, 36, 100]), 100.0)  # would switch to b, but is learned instead
    assert c.current == "a" and not c.learn
    assert c.take_outbox() == [{"event": "midi_learned", "on": {"note": 36, "ch": 1}}]
    assert c.take_outbox() == []
    cmd(c, {"cmd": "midi.learn", "on": True})
    c.on_midi({"control"}, bytes([0xB2, 7, 64]), 100.0)
    assert c.take_outbox() == [{"event": "midi_learned", "on": {"cc": 7, "ch": 3}}]
    cmd(c, {"cmd": "midi.learn", "on": True})
    c.on_midi({"control"}, bytes([0xC0, 5]), 100.0)
    assert c.take_outbox()[0]["on"] == {"program": 5, "ch": 1}
    c.on_midi({"control"}, bytes([0x90, 36, 100]), 100.0)  # learning is over: mappings work again
    assert c.current == "b"


def test_sequence_with_per_item_bars_and_loop(env):
    cfg = ("clock: {bpm: 120, beats_per_bar: 4}\ndirector:\n  mode: sequence\n  loop: true\n  sequence:\n"
           "    - {scene: c, bars: 2}\n    - {scene: a, bars: 1}\n    - {scene: nope, bars: 5}\n    - {scene: b, bars: 1}\n")
    c = make(env, cfg)
    c.tick(100.0)
    assert c.current == "c"  # starts with the first item (unknown scenes are skipped)
    c.tick(103.9)  # 2 bars = 16 beats = 8 s at 120 BPM... not yet
    assert c.current == "c"
    c.tick(108.1)
    assert c.current == "a"
    c.tick(110.2)  # 1 bar = 2 s
    assert c.current == "b"
    c.tick(112.3)
    assert c.current == "c"  # looped
    c.tick(112.4)
    assert c.current == "c" and c.state(112.4)["sequence_index"] == 0


def test_sequence_without_loop_stays_on_the_last_scene(env):
    cfg = "clock: {bpm: 120}\ndirector:\n  mode: sequence\n  loop: false\n  sequence:\n    - {scene: a, bars: 1}\n    - {scene: b, bars: 1}\n"
    c = make(env, cfg)
    c.tick(100.0)
    c.tick(102.1)
    assert c.current == "b"
    for t in (104.5, 110.0, 200.0):
        c.tick(t)
    assert c.current == "b"


def test_sequence_restarts_the_item_when_the_beat_counter_jumps_back(env):
    cfg = "clock: {source: midi, midi_port: x}\ndirector:\n  mode: sequence\n  sequence:\n    - {scene: a, bars: 2}\n    - {scene: b, bars: 2}\n"
    c = make(env, cfg)
    for k in range(80):
        c.on_midi({"clock"}, bytes([0xF8]), 100.0 + k * 60 / (120 * 24))
    c.tick(101.4)
    assert c.current == "a"
    c.on_midi({"clock"}, bytes([0xFA]), 102.0)  # Start: beat -> 0
    c.tick(102.05)
    assert c.current == "a" and c._seq_next is not None and c._seq_next <= 9.0  # 2 bars from ~0, not from the old position


def test_calibration_preview_carries_crosshair_rows_and_line_visibility(env):
    c = make(env)
    cmd(c, {"cmd": "calibration.begin"}, client="ctl")
    base = {"cmd": "calibration.preview", "left": 0.3, "right": 0.7, "top": 0.2, "bottom": 0.8}
    cmd(c, {**base, "roll_deg": 5, "tilt_deg": 20, "lines": False, "grid": True, "crosshair": [0.5, 0.3]}, client="ctl")
    o = c.frame(100.0).overlay
    assert o.lines is None and o.grid and o.crosshair == (0.5, 0.3) and o.rows is None
    assert c.frame(100.0).cal.tilt_deg == 20
    cmd(c, {**base, "lines": False, "rows": {"mode": "single", "pitch_deg": 3.8, "offset_deg": 0.4}}, client="ctl")
    assert c.frame(100.0).overlay.rows == ("single", 3.8, 0.4)
    with pytest.raises(ValueError, match="rows.pitch_deg"):  # rejected while parsing, before it reaches the core
        P.parse_command({**base, "rows": {"mode": "zebra", "pitch_deg": 40, "offset_deg": 0}})
    cmd(c, {**base, "roll_deg": 5, "tilt_deg": 20}, client="ctl")
    assert c.frame(100.0).overlay.lines == (0.3, 0.7, 0.2, 0.8)  # step 1 again: the lines are back
    cmd(c, {"cmd": "calibration.end", "commit": True}, client="ctl")
    committed = c.calibration(None)
    assert (committed.roll_deg, committed.tilt_deg) == (5.0, 20.0)  # roll and tilt are committed with the lines


def test_output_state_reports_a_stalled_render_loop(env):
    c = make(env)
    assert c.state(100.0)["output"] == {"presenting": True, "stalled_s": 0.0}  # no window (tests, headless): assumed fine
    c.frame_heartbeat = 100.0
    assert c.state(100.5)["output"]["presenting"] is True
    s = c.state(103.2)["output"]  # the render thread has not presented a frame for 3.2 s
    assert s["presenting"] is False and s["stalled_s"] == 3.2
    c.frame_heartbeat = 103.2
    assert c.state(103.3)["output"]["presenting"] is True  # it recovers as soon as frames flow again


MIXXX = "clock: {source: midi, midi_port: mixxx, midi_mode: beats}\n"


def test_mixxx_midi_for_light_beat_notes_drive_the_clock(env):
    c = make(env, MIXXX)
    assert c.midi_wants() == ("mixxx", None)
    clk = {"clock"}
    for k in range(12):
        c.on_midi(clk, bytes([0x90, 0x32, 0x64]), 100.0 + k * 0.5)  # D, velocity 100: the beat
        c.on_midi(clk, bytes([0x90, 0x34, 73]), 100.0 + k * 0.5)  # E: tempo 73 + 50
    s = c.state(105.5 + 0.1)["clock"]
    assert s["source"] == "midi" and s["locked"] and s["bpm"] == 123
    assert c.clock._bpm == pytest.approx(120, rel=0.01)  # measured from the beat spacing
    n = len(c.clock._beat_times)
    c.on_midi(clk, bytes([0x90, 0x32, 90]), 106.0)  # wrong velocity
    c.on_midi(clk, bytes([0x90, 0x33, 100]), 106.0)  # wrong note
    c.on_midi(clk, bytes([0x80, 0x32, 100]), 106.0)  # note-off
    c.on_midi(clk, bytes([0x90, 0x32, 0]), 106.0)  # note-on with velocity 0
    c.on_midi(clk, bytes([0xF8]), 106.0)  # a plain clock tick means nothing in this mode
    assert len(c.clock._beat_times) == n


def test_beat_note_settings_are_configurable(env):
    c = make(env, "clock: {source: midi, midi_port: x, midi_mode: beats, beat_note: 60, beat_velocity: 0, midi_channel: 3, tempo_note: -1}\n")
    clk = {"clock"}
    c.on_midi(clk, bytes([0x90, 60, 33]), 100.0)  # wrong channel
    assert not c.clock._beat_times
    c.on_midi(clk, bytes([0x92, 60, 33]), 100.0)  # channel 3, any velocity
    assert len(c.clock._beat_times) == 1
    c.on_midi(clk, bytes([0x92, 52, 70]), 100.1)  # tempo note switched off
    assert c.clock._reported_bpm is None


def test_switching_between_clock_and_beat_notes_reconfigures_the_clock(env):
    c = make(env, "clock: {source: midi, midi_port: x}\n")
    assert not c.clock.midi_beats
    env[1].write_text(MIXXX)
    c.poll_files(200.0)
    assert c.clock.midi_beats
