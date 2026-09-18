import random

import pytest

from mirrorballs.common.clock import PPQN, Clock


def feed_ticks(clock, bpm, seconds, t0=0.0, jitter=0.0, seed=1, first_tick=0):
    """Send ideal ticks at ``bpm`` (with gaussian jitter) and return (tick_time, ideal_beat) pairs."""
    rng = random.Random(seed)
    period = 60.0 / (bpm * PPQN)
    out, k = [], 0
    while k * period < seconds:
        t = t0 + k * period + rng.gauss(0, jitter)
        clock.on_midi_tick(t)
        out.append((t0 + k * period, (first_tick + k) / PPQN))
        k += 1
    return out


def test_internal_clock_basic():
    c = Clock(bpm=120, now=10.0)
    s = c.snapshot(10.75)
    assert s.beat == pytest.approx(1.5) and s.phase == pytest.approx(0.5)
    assert s.bar == pytest.approx(1.5 / 4)
    assert s.source == "internal" and s.locked and s.status == "internal"


def test_internal_bpm_change_keeps_beat_continuous():
    c = Clock(bpm=120, now=0.0)
    before = c.snapshot(3.3).beat
    c.use_internal(90, now=3.3)
    assert c.snapshot(3.3).beat == pytest.approx(before)
    assert c.snapshot(4.3).beat == pytest.approx(before + 1.5)


def test_positive_offset_shows_the_beat_later():
    c = Clock(bpm=120, offset_ms=100, now=0.0)
    assert c.snapshot(1.0).beat == pytest.approx(2.0 - 0.2)
    c.set_offset_ms(-100)
    assert c.snapshot(1.0).beat == pytest.approx(2.0 + 0.2)


def test_tap_tempo_sets_bpm_and_beat_boundary():
    c = Clock(bpm=120, now=0.0)
    for t in (5.0, 5.5, 6.0, 6.5):  # 120 BPM taps
        c.tap(t)
    assert c.snapshot(6.5).bpm == pytest.approx(120.0)
    assert c.snapshot(6.5).phase == pytest.approx(0.0, abs=1e-9)
    c2 = Clock(bpm=120, now=0.0)
    for t in (1.0, 1.4, 1.8):  # 150 BPM
        c2.tap(t)
    assert c2.snapshot(1.8).bpm == pytest.approx(150.0)
    c2.tap(10.0)  # long gap resets the tap history
    assert c2.snapshot(10.0).bpm == pytest.approx(150.0)


def test_sync_downbeat_snaps_to_bar():
    c = Clock(bpm=120, beats_per_bar=4, now=0.0)
    c.sync_downbeat(3.9)  # beat 7.8 -> nearest bar start 8
    assert c.snapshot(3.9).beat == pytest.approx(8.0)
    assert c.snapshot(3.9).bar == pytest.approx(2.0)


def test_midi_locks_with_jitter():
    c = Clock(now=0.0)
    c.use_midi(0.0)
    ticks = feed_ticks(c, bpm=128, seconds=8.0, jitter=0.002)
    s = c.snapshot(ticks[-1][0])
    assert s.locked and s.status == "locked"
    assert s.bpm == pytest.approx(128, abs=1.0)
    # phase error against the ideal grid stays well under a frame (60 Hz ~ 0.034 beats at 128 BPM)
    for t_ideal, beat in ticks[-40:]:
        assert abs(c.snapshot(t_ideal).beat - beat) < 0.02


def test_midi_phase_is_smooth_under_jitter():
    c = Clock(now=0.0)
    c.use_midi(0.0)
    feed_ticks(c, bpm=120, seconds=4.0, jitter=0.003)
    # sampled at 60 fps, the beat must never go backwards and never jump
    beats = [c.snapshot(4.0 + k / 60).beat for k in range(60)]
    steps = [b - a for a, b in zip(beats, beats[1:])]
    assert all(s > 0 for s in steps)
    assert max(steps) - min(steps) < 1e-9  # pure extrapolation between ticks


def test_midi_follows_tempo_change():
    c = Clock(now=0.0)
    c.use_midi(0.0)
    feed_ticks(c, bpm=100, seconds=4.0)
    t0 = 4.0
    ticks = feed_ticks(c, bpm=140, seconds=6.0, t0=t0, first_tick=int(4.0 / (60 / (100 * PPQN))))
    assert c.snapshot(t0 + 6.0).bpm == pytest.approx(140, abs=2.0)


def test_midi_start_resets_beat_to_zero():
    c = Clock(now=0.0)
    c.use_midi(0.0)
    feed_ticks(c, bpm=120, seconds=3.0)
    c.on_midi_start(10.0)
    assert c.snapshot(10.0).beat == pytest.approx(0.0)
    feed_ticks(c, bpm=120, seconds=1.0, t0=10.02)
    s = c.snapshot(11.02)
    assert s.beat == pytest.approx(2.0, abs=0.06)
    # the tick grid defines the bar: beat 2.0 is 2 whole beats after Start
    assert s.phase == pytest.approx(0.0, abs=0.06) or s.phase > 0.94


def test_midi_spp_jumps_position():
    c = Clock(now=0.0)
    c.use_midi(0.0)
    feed_ticks(c, bpm=120, seconds=2.0)
    c.on_midi_spp(64, 2.0)  # 64 sixteenths = 16 beats
    c.on_midi_tick(2.0 + 60 / (120 * PPQN))
    assert c.snapshot(2.0 + 60 / (120 * PPQN)).beat == pytest.approx(16.0, abs=0.01)


def test_clock_loss_free_runs_then_recovers():
    c = Clock(now=0.0)
    c.use_midi(0.0)
    feed_ticks(c, bpm=120, seconds=3.0)
    b_at_loss = c.snapshot(3.0).beat
    s = c.snapshot(4.0)
    assert not s.locked and s.status == "waiting for clock"
    assert s.beat == pytest.approx(b_at_loss + 2.0, abs=0.1)  # keeps advancing at last tempo
    assert s.bpm == pytest.approx(120, abs=0.5)
    feed_ticks(c, bpm=120, seconds=2.0, t0=4.0, first_tick=0)
    assert c.snapshot(6.0).locked


def test_midi_stop_freeruns_and_flags_status():
    c = Clock(now=0.0)
    c.use_midi(0.0)
    feed_ticks(c, bpm=120, seconds=2.0)
    c.on_midi_stop(2.0)
    s = c.snapshot(2.01)
    assert not s.running
    c.on_midi_continue(3.0)
    assert c.snapshot(3.0).running


def test_no_ticks_yet_reports_waiting():
    c = Clock(bpm=130, now=0.0)
    c.use_midi(0.0)
    s = c.snapshot(1.0)
    assert not s.locked and s.status == "waiting for clock" and s.bpm == 130


def test_ticks_ignored_on_internal_source():
    c = Clock(bpm=120, now=0.0)
    c.on_midi_tick(0.1)
    assert c.snapshot(1.0).beat == pytest.approx(2.0)
