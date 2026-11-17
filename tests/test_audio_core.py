"""The renderer's side of audio: AudioState, the audio clock in the core, the audio config, and the ListenManager."""

import random
import sys
import textwrap
import time
from queue import Empty, SimpleQueue

import pytest

from mirrorballs.common import protocol as P
from mirrorballs.common.config import ClockConfig
from mirrorballs.render.audio_state import STALE_S, AudioState
from mirrorballs.render.core import Core
from mirrorballs.render.listen import ListenManager

SOURCES = [{"id": "alsa_output.speakers", "name": "Monitor of Speakers", "kind": "monitor", "channels": 2},
           {"id": "alsa_input.mic", "name": "Built-in mic", "kind": "mic", "channels": 2}]
SCENE = "schema: 1\nname: a\nlayers:\n  - {type: fill, color: white}\n"


def make_core(tmp_path, config_text=""):
    (tmp_path / "scenes").mkdir(exist_ok=True)
    (tmp_path / "scenes" / "a.yaml").write_text(SCENE)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(config_text)
    return Core(cfg, [tmp_path / "scenes"], now=100.0, rng=random.Random(1))


def estimate(t, beat_at_t, bpm=126.0, locked=True, source="alsa_output.speakers", confidence=0.8):
    return {"type": "estimate", "source": source, "t": t, "anchor_time": t, "anchor_beat": beat_at_t, "bpm": bpm,
            "confidence": confidence, "locked": locked}


# --- AudioState -------------------------------------------------------------------------------


def test_the_chosen_source_is_matched_exactly_then_as_a_substring():
    a = AudioState()
    a.on_message({"type": "sources", "sources": SOURCES}, 0.0)
    assert a.resolve("alsa_input.mic") == "alsa_input.mic"
    assert a.resolve("speakers") == "alsa_output.speakers"  # by id
    assert a.resolve("built-in") == "alsa_input.mic"  # by name
    assert a.resolve("hdmi") is None and a.resolve(None) is None


def test_the_listener_runs_only_for_the_audio_clock():
    a = AudioState()
    assert a.wants("internal", "speakers", 70, 180) is None
    assert a.wants("audio", "speakers", 70, 180) == ("speakers", False, 70, 180)  # unknown yet: the substring
    a.on_message({"type": "sources", "sources": SOURCES}, 0.0)
    assert a.wants("audio", "speakers", 70, 180) == ("alsa_output.speakers", False, 70, 180)
    a.preview("c1", True)
    assert a.wants("audio", "speakers", 70, 180) == ("alsa_output.speakers", True, 70, 180)  # and meters the others
    assert a.wants("midi", "speakers", 70, 180) is None  # a preview alone never starts it
    a.forget()
    assert a.sources == [] and a.estimate is None


def test_state_shows_levels_and_tracking_while_fresh():
    a = AudioState()
    a.on_message({"type": "sources", "sources": SOURCES}, 0.0)
    a.on_message({"type": "level", "source": "alsa_input.mic", "rms_db": [-20.0, -21.0], "peak_db": [-8.0, -9.0]}, 1.0)
    a.on_message(estimate(1.0, 3.0), 1.0)
    a.on_message({"type": "health", "source": "alsa_output.speakers", "rtf": 0.02, "age_ms": 9.0, "jitter_ms": 1.0,
                  "drift_ppm": 20.0, "gap_ms": 0.0, "ok": True}, 1.0)
    st = a.state(1.2, "speakers", tracked=True)
    mic = next(s for s in st["sources"] if s["id"] == "alsa_input.mic")
    assert mic["rms_db"] == [-20.0, -21.0] and mic["peak_db"] == [-8.0, -9.0]
    assert "rms_db" not in next(s for s in st["sources"] if s["id"] == "alsa_output.speakers")
    assert st["source"] == "alsa_output.speakers" and st["tracking"]["bpm"] == 126.0 and st["tracking"]["locked"]
    assert st["health"]["ok"] and "type" not in st["health"]
    later = a.state(1.0 + STALE_S + 0.1, "speakers", tracked=True)
    assert "rms_db" not in next(s for s in later["sources"] if s["id"] == "alsa_input.mic")
    assert later["tracking"] is None


def test_errors_name_the_source():
    a = AudioState()
    a.on_message({"type": "sources", "sources": SOURCES}, 0.0)
    a.on_message({"type": "error", "source": "alsa_input.mic", "message": "Internal data stream error."}, 0.0)
    assert a.state(0.0, None, False)["error"] == "Built-in mic: Internal data stream error."


# --- config -------------------------------------------------------------------------------------


def test_audio_config_defaults_and_range():
    c = ClockConfig(source="audio", audio_source="speakers")
    assert (c.audio_min_bpm, c.audio_max_bpm, c.audio_offset_ms) == (70.0, 180.0, 0.0)
    with pytest.raises(ValueError):
        ClockConfig(audio_min_bpm=120, audio_max_bpm=150)  # too narrow: a tempo and its half must not both fit out


def test_audio_preview_command_parses():
    assert P.parse_command({"cmd": "audio.preview", "on": True}).on is True


# --- core ---------------------------------------------------------------------------------------


def test_the_audio_clock_follows_the_listener(tmp_path):
    core = make_core(tmp_path, "clock: {source: audio, audio_source: speakers}\n")
    assert core.clock.source == "audio" and core.audio_wants() == ("speakers", False, 70.0, 180.0)
    core.on_audio({"type": "sources", "sources": SOURCES}, 100.0)
    assert core.audio_wants() == ("alsa_output.speakers", False, 70.0, 180.0) and core.audio.dirty
    core.on_audio(estimate(101.0, 7.25), 101.0)  # a quarter beat into the tracker's beat
    st = core.state(101.0)
    assert st["clock"]["source"] == "audio" and st["clock"]["locked"] and st["clock"]["bpm"] == pytest.approx(126)
    assert st["clock"]["phase"] == pytest.approx(0.25, abs=1e-3)
    assert st["audio"]["tracking"]["bpm"] == 126.0 and st["clock"]["confidence"] == pytest.approx(0.8)


def test_estimates_of_another_source_do_not_move_the_clock(tmp_path):
    core = make_core(tmp_path, "clock: {source: audio, audio_source: speakers}\n")
    core.on_audio({"type": "sources", "sources": SOURCES}, 100.0)
    core.on_audio(estimate(101.0, 7.25, source="alsa_input.mic"), 101.0)
    assert not core.state(101.0)["clock"]["locked"]


def test_previews_are_per_client_and_end_with_the_connection(tmp_path):
    core = make_core(tmp_path, "clock: {source: audio, audio_source: speakers}\n")
    assert core.audio_wants() == ("speakers", False, 70.0, 180.0)
    assert core.handle(P.parse_command({"cmd": "audio.preview", "on": True}), "c1", 100.0)["ok"]
    assert core.audio_wants() == ("speakers", True, 70.0, 180.0)
    core.client_disconnected("c1", 100.0)
    assert core.audio_wants() == ("speakers", False, 70.0, 180.0)


def test_another_clock_stops_the_listener_and_forgets_what_it_said(tmp_path):
    core = make_core(tmp_path, "clock: {source: audio, audio_source: speakers}\n")
    core.handle(P.parse_command({"cmd": "audio.preview", "on": True}), "c1", 100.0)
    core.on_audio({"type": "sources", "sources": SOURCES}, 100.0)
    core.on_audio(estimate(101.0, 7.25), 101.0)
    (tmp_path / "config.yaml").write_text("clock: {source: internal, audio_source: speakers}\n")
    core.reload(102.0)
    assert core.audio_wants() is None  # a client still previews: no matter
    st = core.state(102.0)["audio"]
    assert st["sources"] == [] and st["tracking"] is None
    core.on_audio({"type": "sources", "sources": SOURCES}, 102.1)  # late, from the stopped listener
    assert core.state(102.1)["audio"]["sources"] == []


def test_the_audio_offset_applies_only_to_the_audio_clock(tmp_path):
    core = make_core(tmp_path, "clock: {source: audio, offset_ms: 20, audio_offset_ms: 30}\n")
    assert core.clock.offset_s == pytest.approx(0.05)
    (tmp_path / "config.yaml").write_text("clock: {source: internal, offset_ms: 20, audio_offset_ms: 30}\n")
    core.reload(101.0)
    assert core.clock.offset_s == pytest.approx(0.02)


# --- ListenManager ------------------------------------------------------------------------------

FAKE_LISTENER = textwrap.dedent("""
    import json, sys
    print(json.dumps({"type": "sources", "sources": []}), flush=True)
    for line in sys.stdin:
        msg = json.loads(line)
        if msg.get("active") == "crash":
            print("RuntimeError: the fake listener crashed", file=sys.stderr, flush=True)
            sys.exit(3)
        print(json.dumps({"type": "configured", **msg}), flush=True)
""")


def drain(inbox, n, timeout=5.0):
    out, end = [], time.monotonic() + timeout
    while len(out) < n and time.monotonic() < end:
        try:
            out.append(inbox.get(timeout=0.05))
        except Empty:
            pass
    return out


@pytest.fixture
def manager():
    inbox = SimpleQueue()
    m = ListenManager(inbox, command=[sys.executable, "-c", FAKE_LISTENER])
    yield m, inbox
    m.close()


def test_the_listener_starts_when_wanted_and_gets_configured(manager):
    m, inbox = manager
    m.poll(0.0)
    assert not m.status()["running"]  # nobody wants it
    m.configure(("speakers", True, 70.0, 180.0))
    m.poll(0.0)
    msgs = [item[1] for item in drain(inbox, 2)]
    assert msgs[0] == {"type": "sources", "sources": []}
    assert msgs[1] == {"type": "configured", "cmd": "configure", "active": "speakers", "scan": True,
                       "min_bpm": 70.0, "max_bpm": 180.0}
    assert m.status() == {"running": True, "error": None}
    m.configure(None)
    assert not m.status()["running"]


STUBBORN_LISTENER = textwrap.dedent("""
    import json, signal, sys, time
    signal.signal(signal.SIGTERM, signal.SIG_IGN)  # ignores SIGTERM, and the end of its input below
    print(json.dumps({"type": "sources", "sources": []}), flush=True)
    sys.stdin.close()
    while True:
        time.sleep(1)
""")


@pytest.mark.parametrize("script", [FAKE_LISTENER, STUBBORN_LISTENER], ids=["quits-on-eof", "stubborn"])
def test_not_wanted_any_more_the_listener_process_is_gone(script):
    inbox = SimpleQueue()
    m = ListenManager(inbox, command=[sys.executable, "-c", script])
    m.configure(("speakers", False, 70.0, 180.0))
    m.poll(0.0)
    drain(inbox, 1)
    proc = m._proc
    assert proc.poll() is None
    m.configure(None)  # the clock switched away from audio
    assert proc.poll() is not None  # ended and reaped (no zombie left)
    assert not m.status()["running"] and m._proc is None


def test_a_listener_that_dies_is_restarted_later_and_its_error_shown(manager):
    m, inbox = manager
    m.configure(("crash", False, 70.0, 180.0))
    m.poll(0.0)
    drain(inbox, 1)
    m._proc.wait(5)
    time.sleep(0.2)  # let the stderr reader take the last line
    m.poll(1.0)
    assert m.status() == {"running": False, "error": "RuntimeError: the fake listener crashed"}
    m.configure(("speakers", False, 70.0, 180.0))  # a working configuration again
    m.poll(1.5)
    assert not m.status()["running"]  # not before the pause is over
    m.poll(2.1)
    assert m.status()["running"]
