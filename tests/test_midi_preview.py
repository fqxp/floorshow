import random

import pytest

from mirrorballs.render.midi_preview import MidiPreview

PORT = "VirMIDI 1-0"


def clock(p, bpm, seconds, t0=10.0, start=True, jitter=0.0, seed=1):
    rng = random.Random(seed)
    if start:
        p.on_message(PORT, bytes([0xFA]), t0)
    k, period = 0, 60.0 / bpm / 24
    while k * period < seconds:
        p.on_message(PORT, bytes([0xF8]), t0 + k * period + rng.gauss(0, jitter))
        k += 1
    return t0 + (k - 1) * period


def test_a_clock_shows_its_tempo_and_where_its_beats_fall():
    p = MidiPreview()
    last = clock(p, 128, 4.0, jitter=0.001)
    out = p.port(PORT, last + 0.01)
    assert out["status"] == "clock" and out["bpm"] == pytest.approx(128, abs=0.5)
    beat = 60.0 / 128
    assert ((out["beat_time"] - 10.0) / beat) == pytest.approx(round((out["beat_time"] - 10.0) / beat), abs=0.02)


def test_a_clock_that_stops_is_no_beat():
    p = MidiPreview()
    last = clock(p, 128, 2.0)
    assert p.port(PORT, last + 1.0)["status"] == "active"
    assert p.port(PORT, last + 5.0)["status"] == "quiet"


def test_beat_notes_are_found_without_knowing_the_note():
    p = MidiPreview()
    for k in range(12):
        t = 10.0 + k * 60 / 124
        p.on_message(PORT, bytes([0x90, 50, 100]), t)  # Mixxx: note D on every beat
        p.on_message(PORT, bytes([0x90, 52, 74]), t)  # and E, whose value is the tempo (also on every beat)
        if k % 3 == 0:
            p.on_message(PORT, bytes([0x90, 61, 100]), t + 0.1)  # something uneven on top
        p.on_message(PORT, bytes([0x90, 50, 0]), t + 0.2)  # note-off as note-on with velocity 0: not an onset
    out = p.port(PORT, t + 0.1)
    assert out["status"] == "beats" and out["bpm"] == pytest.approx(124, abs=0.1) and out["note"] in (50, 52)
    assert out["beat_time"] == pytest.approx(t)
    assert p.port(PORT, t + 5.0)["status"] == "quiet"  # the beat stopped long ago


def test_uneven_notes_are_activity_not_a_beat():
    p = MidiPreview()
    rng = random.Random(4)
    t = 10.0
    for _ in range(20):
        t += rng.uniform(0.1, 0.9)
        p.on_message(PORT, bytes([0x90, 60, 90]), t)
    assert p.port(PORT, t + 0.1)["status"] == "active"


def test_an_unknown_port_is_quiet_and_active_sensing_is_ignored():
    p = MidiPreview()
    assert p.port("nothing", 0.0) == {"name": "nothing", "status": "quiet", "bpm": None, "beat_time": None, "note": None}
    p.on_message(PORT, bytes([0xFE]), 1.0)
    assert p.port(PORT, 1.1)["status"] == "quiet"
