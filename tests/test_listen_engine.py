import json
import threading

import numpy as np
import pytest
from test_beat_tracker import track

from mirrorballs.listen import engine
from mirrorballs.listen.engine import SR, Listener, SourceAnalyzer

BLOCK = SR // 50  # 20 ms


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeCapture:
    def __init__(self, source, mode, on_buffer, on_level, on_error):
        self.source, self.mode = source, mode
        self.on_buffer, self.on_level, self.on_error = on_buffer, on_level, on_error
        self.stopped = False
        self.filled = 0

    def stop(self):
        self.stopped = True

    def filled_samples(self):
        return self.filled


def audio_48k(bpm=126, beats=40, kick=True):
    """The tracker tests' house track, resampled to 48 kHz."""
    y, b = track([(beats, bpm, kick, 0)])
    x = np.arange(int(len(y) * SR / 44100)) * 44100 / SR
    return np.interp(x, np.arange(len(y)), y).astype(np.float32), b


def play(capture, y, clock, delay=0.006):
    """Deliver ``y`` in 20 ms buffers, ``delay`` after each buffer's last sample, advancing the fake clock."""
    start = clock.t
    for i in range(0, len(y) - BLOCK + 1, BLOCK):
        clock.t = start + (i + BLOCK) / SR + delay
        capture.on_buffer(y[i:i + BLOCK], clock.t)


SOURCES = [{"id": "mic", "name": "Built-in mic", "kind": "mic"},
           {"id": "out", "name": "Monitor of Speakers", "kind": "monitor"},
           {"id": "hdmi", "name": "Monitor of HDMI", "kind": "monitor"}]


def make(threaded=False):
    out, caps, clock = [], {}, FakeClock()

    def factory(source, mode, on_buffer, on_level, on_error):
        caps[source["id"]] = FakeCapture(source, mode, on_buffer, on_level, on_error)
        return caps[source["id"]]

    return Listener(factory, out.append, now_fn=clock, threaded=threaded), out, caps, clock


def test_only_the_active_source_is_captured_without_previews():
    li, out, caps, _ = make()
    li.set_sources(SOURCES)
    assert out[0] == {"type": "sources", "sources": SOURCES}
    li.configure("out", scan=False)
    assert set(caps) == {"out"} and caps["out"].mode == "track"


def test_previews_meter_every_other_source_and_stop_again():
    li, _, caps, _ = make()
    li.set_sources(SOURCES)
    li.configure("out", scan=True)
    assert {sid: c.mode for sid, c in caps.items()} == {"mic": "meter", "out": "track", "hdmi": "meter"}
    assert caps["mic"].on_buffer is None  # no samples into Python for a meter
    li.configure("out", scan=False)
    assert caps["mic"].stopped and caps["hdmi"].stopped and not caps["out"].stopped


def test_previews_without_an_active_source():
    li, _, caps, _ = make()
    li.set_sources(SOURCES)
    li.configure(None, scan=True)
    assert {c.mode for c in caps.values()} == {"meter"}


def test_choosing_another_source_swaps_meter_and_tracker():
    li, _, caps, _ = make()
    li.set_sources(SOURCES)
    li.configure("out", scan=True)
    old_out, old_mic = caps["out"], caps["mic"]
    li.configure("mic", scan=True)
    assert old_out.stopped and old_mic.stopped
    assert caps["mic"].mode == "track" and caps["out"].mode == "meter"


def test_levels_are_reported_per_channel():
    li, out, caps, _ = make()
    li.set_sources(SOURCES)
    li.configure("out", scan=True)
    caps["hdmi"].on_level([-12.34, float("-inf")], [-6.0, -700.0])
    assert out[-1] == {"type": "level", "source": "hdmi", "rms_db": [-12.3, -100.0], "peak_db": [-6.0, -100.0]}


def test_a_source_that_goes_away_is_stopped_and_comes_back():
    li, out, caps, _ = make()
    li.set_sources(SOURCES)
    li.configure("hdmi", scan=False)
    first = caps["hdmi"]
    li.set_sources(SOURCES[:2])
    assert first.stopped
    li.set_sources(SOURCES)
    assert caps["hdmi"] is not first and not caps["hdmi"].stopped
    assert sum(m["type"] == "sources" for m in out) == 3


def test_a_failed_capture_is_retried():
    li, out, caps, clock = make()
    li.set_sources(SOURCES)
    li.configure("out", scan=True)
    first = caps["hdmi"]
    first.on_error("Internal data stream error.")
    assert out[-1] == {"type": "error", "source": "hdmi", "message": "Internal data stream error."}
    li.check_load()
    assert caps["hdmi"] is first  # not at once: a broken device would spin
    clock.t += engine.RETRY_S
    li.check_load()
    assert first.stopped and caps["hdmi"] is not first and caps["hdmi"].mode == "meter"


def test_a_capture_that_cannot_start_is_retried():
    out, clock, calls = [], FakeClock(), []

    def factory(source, mode, on_buffer, on_level, on_error):
        calls.append(source["id"])
        if len(calls) == 1:
            raise RuntimeError("cannot capture from Speakers")
        return FakeCapture(source, mode, on_buffer, on_level, on_error)

    li = Listener(factory, out.append, now_fn=clock, threaded=False)
    li.set_sources(SOURCES[1:2])
    li.configure("out", scan=False)
    assert out[-1]["type"] == "error"
    clock.t += engine.RETRY_S
    li.check_load()
    assert calls == ["out", "out"] and li._running["out"].analyzer is not None


def test_a_new_tempo_range_restarts_the_tracker():
    li, _, caps, _ = make()
    li.set_sources(SOURCES)
    li.configure("out", scan=False)
    first = caps["out"]
    li.configure("out", scan=False, min_bpm=100, max_bpm=200)
    assert first.stopped and caps["out"] is not first
    assert li._running["out"].analyzer.tracker.min_bpm == 100


def test_estimates_of_the_active_source_lock_on_the_beat():
    li, out, caps, clock = make()
    li.set_sources(SOURCES)
    li.configure("out", scan=False)
    y, beats = audio_48k()
    t0 = clock.t
    play(caps["out"], y, clock)
    est = [m for m in out if m["type"] == "estimate"]
    assert all(m["source"] == "out" for m in est)
    assert np.median(np.diff([m["t"] for m in est])) == pytest.approx(engine.ESTIMATE_EVERY_S, abs=0.021)
    last = est[-1]
    assert last["locked"] and last["bpm"] == pytest.approx(126, abs=0.5)
    # the model puts a beat where the music has one, on the monotonic clock
    true = t0 + beats[-3]
    b = last["anchor_beat"] + (true - last["anchor_time"]) * last["bpm"] / 60
    assert abs(b - round(b)) * 60 / 126 < 0.015
    json.dumps(out)  # plain json, no numpy types: a message that cannot be sent once stopped a worker for good


def test_silence_is_never_locked():
    li, out, caps, clock = make()
    li.set_sources(SOURCES)
    li.configure("mic", scan=False)
    play(caps["mic"], np.zeros(10 * SR, dtype=np.float32), clock)
    est = [m for m in out if m["type"] == "estimate"]
    assert est and not any(m["locked"] for m in est)


def test_health_reports_lost_audio_and_age():
    out, clock = [], FakeClock()
    cap = FakeCapture({}, "track", None, None, None)
    an = SourceAnalyzer("out", out.append, now_fn=clock, filled_fn=cap.filled_samples)
    y, _ = audio_48k(beats=12)
    for i in range(0, len(y) - BLOCK + 1, BLOCK):
        clock.t = 1000.0 + (i + BLOCK) / SR + 0.006
        if i == 3 * SR:
            cap.filled += 240  # PipeWire lost 5 ms
        an.process(y[i:i + BLOCK], clock.t)
    h = [m for m in out if m["type"] == "health"]
    assert h[-1]["gap_ms"] == pytest.approx(5.0)
    assert h[-1]["ok"] and 0 <= h[-1]["age_ms"] < 10


def test_previews_pause_when_the_tracking_falls_behind():
    li, out, caps, clock = make()
    li.set_sources(SOURCES)
    li.configure("out", scan=True)
    li._running["out"].analyzer.age = 0.4  # its audio is analysed 400 ms late
    li.check_load()
    assert out[-1] == {"type": "load", "load": 0.0, "throttled": True}
    assert caps["mic"].stopped and not caps["out"].stopped
    li.check_load()
    assert out[-1]["throttled"] is True  # nothing new until the retry
    li._running["out"].analyzer.age = 0.01  # caught up
    clock.t += engine.THROTTLE_RETRY_S
    li.check_load()
    assert out[-1]["throttled"] is False and not li._running["mic"].capture.stopped


def test_previews_pause_on_load_as_a_backstop():
    li, out, _, _ = make()
    li.set_sources(SOURCES)
    li.configure("out", scan=True)
    li._running["out"].analyzer.rtf = 1.05
    li.check_load()
    assert out[-1] == {"type": "load", "load": 1.05, "throttled": True}


def test_threaded_workers_process_in_order():
    li, out, caps, clock = make(threaded=True)
    li.set_sources(SOURCES)
    li.configure("out", scan=False)
    y, _ = audio_48k(beats=8)
    play(caps["out"], y, clock)
    li.close()
    for t in threading.enumerate():
        if t.name == "listen out":
            t.join(timeout=5)
    assert any(m["type"] == "estimate" for m in out)
    assert not [m for m in out if m["type"] == "error"]
