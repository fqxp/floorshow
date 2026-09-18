import pytest
from pydantic import ValidationError

from mirrorballs.common.config import CalibrationConfig, Config, ConfigError, load_config, update_config
from mirrorballs.common.geometry import Calibration
from mirrorballs.common.ipc import LineDecoder, encode
from mirrorballs.common.protocol import error, ok, parse_command


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
