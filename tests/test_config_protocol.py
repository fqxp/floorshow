import pytest
from pydantic import ValidationError

from floorshow.common.config import CalibrationConfig, Config, ConfigError, load_config, update_config
from floorshow.common.geometry import Calibration
from floorshow.common.ipc import LineDecoder, encode
from floorshow.common.protocol import error, ok, parse_command


def test_missing_file_gives_defaults(tmp_path):
    c = load_config(tmp_path / "nope.yaml")
    assert c.clock.source == "internal" and c.clock.bpm == 120 and c.calibration is None
    assert c.director.every.in_beats(4) == 32


def test_full_config_roundtrip_from_plan_example(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("""\
schema: 1
clock:
  source: midi
  midi_port: "Arturia BeatStep"
  bpm: 120
  beats_per_bar: 4
  offset_ms: 0
ball: {diameter_cm: null, row_pitch_deg: null, row_offset_deg: 0, motor: null}
calibration: {left: 0.303, right: 0.697, top: 0.15, bottom: 0.85, roll_deg: 0, tilt_deg: 0}
director:
  mode: random
  every: {bars: 8}
  no_repeat: true
  pool: {tags: [beat]}
  transition: {type: crossfade, beats: 1}
midi:
  control_port: "Launchpad"
  map:
    - {on: {note: 36, ch: 10}, do: {scene: bars-to-poles}}
    - {on: {program: 3},       do: {scene_index: 3}}
    - {on: {cc: 7},            do: master}
    - {on: {note: 48},         do: blackout}
bindings:
  keys: {"1": bars-to-poles, space: next, b: blackout}
""")
    c = load_config(p)
    assert c.clock.source == "midi" and c.director.mode == "random" and len(c.midi.map) == 4
    assert c.calibration.to_calibration() == Calibration(0.303, 0.697, 0.15, 0.85)
    assert c.bindings.keys["1"] == "bars-to-poles"


def test_a_calibrated_ball_keeps_its_size_in_any_output_window():
    """The lines are fractions of a window, so they only mean a ball alongside the window they were measured
    in. Without that, resizing the output turns the ball into an ellipse."""
    measured = Calibration(0.303, 0.697, 0.15, 0.85)  # round, 756 px across, on a fullscreen 1920x1080
    c = CalibrationConfig.from_calibration(measured, (1920, 1080))
    assert (c.width, c.height) == (1920, 1080)
    for size in ((1920, 1080), (1280, 720), (1920, 1200), (900, 900)):
        cal = c.for_window(size)
        assert (2 * cal.radii[0] * size[0], 2 * cal.radii[1] * size[1]) == pytest.approx((756.48, 756.0))
        assert cal.center == pytest.approx((0.5, 0.5))  # the centre is a fraction, so it follows the window
    # a file written before the size was recorded reads as it always did: fractions of whatever shows them
    old = CalibrationConfig.from_calibration(measured)
    assert old.width is None and old.for_window((1280, 720)) == measured


def test_the_balls_rotation_is_a_signed_rate(tmp_path):
    p = tmp_path / "c.yaml"
    assert load_config(p).ball.rotation_rpm == 0.0  # nothing on file: the ball is assumed to stand still
    p.write_text("ball: {diameter_cm: 50, rotation_rpm: -1.5}\n")  # minus: it turns left, facing the ball
    b = load_config(p).ball
    assert (b.diameter_cm, b.rotation_rpm) == (50, -1.5) and b.row_pitch_deg is None
    update_config(p, {"ball": {"rotation_rpm": 2.0}})
    assert load_config(p).ball.rotation_rpm == 2.0


@pytest.mark.parametrize("text,needle", [
    ("clock: {bpm: 5}\n", "clock.bpm"),
    ("clok: {}\n", "clok: unknown key"),
    ("- 1\n", "mapping"),
    ("clock: [\n", "YAML syntax"),
])
def test_bad_config_reports_where(tmp_path, text, needle):
    p = tmp_path / "c.yaml"
    p.write_text(text)
    with pytest.raises(ConfigError, match=needle):
        load_config(p)


def test_update_config_preserves_comments_and_validates(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("# my setup\nschema: 1\nclock:\n  bpm: 128   # club tempo\n  source: internal\n")
    cal = CalibrationConfig.from_calibration(Calibration(0.3, 0.7, 0.2, 0.8)).model_dump()
    update_config(p, {"calibration": cal, "clock": {"source": "midi"}})
    text = p.read_text()
    assert "# my setup" in text and "# club tempo" in text
    c = load_config(p)
    assert c.clock.source == "midi" and c.clock.bpm == 128 and c.calibration.left == 0.3
    before = p.read_text()
    with pytest.raises(ValidationError):
        update_config(p, {"clock": {"bpm": 9999}})
    assert p.read_text() == before  # an invalid update never touches the file
    assert not list(tmp_path.glob("*.tmp"))


def test_update_config_creates_file_and_can_clear(tmp_path):
    p = tmp_path / "sub" / "config.yaml"
    update_config(p, {"calibration": {"left": 0.3, "right": 0.7, "top": 0.2, "bottom": 0.8}})
    assert load_config(p).calibration is not None
    update_config(p, {"calibration": None})
    assert load_config(p).calibration is None


def test_ipc_framing_handles_partial_and_multiple_messages():
    d = LineDecoder()
    data = encode({"a": 1}) + encode({"b": [1, 2]})
    assert d.feed(data[:3]) == []
    assert d.feed(data[3:]) == [{"a": 1}, {"b": [1, 2]}]


def test_ipc_bad_lines_do_not_break_the_stream():
    d = LineDecoder()
    out = d.feed(b"garbage\n" + encode({"ok": 1}) + b"[1]\n")
    assert out[0]["_error"].startswith("invalid JSON")
    assert out[1] == {"ok": 1}
    assert out[2] == {"_error": "message must be a JSON object"}


def test_parse_commands():
    c = parse_command({"id": 2, "cmd": "scene.set", "scene": "x", "transition": {"type": "wipe_lat", "beats": 2}})
    assert c.scene == "x" and c.transition.type == "wipe_lat" and c.id == 2
    assert parse_command({"cmd": "reload"}).cmd == "reload"
    p = parse_command({"cmd": "calibration.preview", "left": 0.3, "right": 0.7, "top": 0.2, "bottom": 0.8, "blink": True})
    assert p.blink and p.active is None
    assert parse_command({"cmd": "master.set", "value": 0.5}).value == 0.5


@pytest.mark.parametrize("msg,needle", [
    ({"cmd": "nope"}, ""),
    ({"cmd": "scene.set"}, "scene"),
    ({"cmd": "master.set", "value": 2}, "value"),
    ({"cmd": "running.set", "running": True, "extra": 1}, "extra: unknown key"),
    ({}, ""),
])
def test_parse_command_errors_are_readable(msg, needle):
    with pytest.raises(ValueError) as ei:
        parse_command(msg)
    assert needle in str(ei.value)


def test_reply_helpers():
    assert ok(3, x=1) == {"id": 3, "ok": True, "x": 1}
    assert error(3, "boom") == {"id": 3, "ok": False, "error": "boom"}



def test_a_config_with_the_removed_safety_section_still_loads(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("safety: {flash_limit: true, max_flashes_per_s: 3}\nclock: {bpm: 100}\n")
    assert load_config(p).clock.bpm == 100


def test_the_accent_colour_is_checked_and_old_keys_still_load(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("ui: {beat_indicator: false}\naccent: {color: '#ff8800', colors: [{name: Warm, color: '#ff8800'}]}\n")
    c = load_config(p)  # named colours were removed, but a file that has them still loads
    assert c.accent.rgb() == pytest.approx((1.0, 0x88 / 255, 0.0))
    p.write_text("accent: {color: orange}\n")
    with pytest.raises(ConfigError, match="accent.color"):
        load_config(p)
