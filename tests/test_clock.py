import random

import numpy as np
import pytest

from floorshow.common.clock import PPQN, Clock


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


def feed_beats(clock, bpm, count, t0=0.0, jitter=0.0, seed=1):
    """Send one beat note per beat at ``bpm``; returns the ideal beat times."""
    rng = random.Random(seed)
    period = 60.0 / bpm
    times = [t0 + k * period for k in range(count)]
    for t in times:
        clock.on_midi_beat(t + rng.gauss(0, jitter))
    return times


def beat_clock():
    c = Clock(bpm=120, now=0.0)
    c.use_midi(0.0, beats=True)
    return c


def test_beat_notes_lock_to_the_tempo_and_phase():
    c = beat_clock()
    times = feed_beats(c, 96, 16, t0=5.0, jitter=0.004)
    s = c.snapshot(times[-1] + 0.25)
    assert s.locked and s.status == "locked"
    assert c._bpm == pytest.approx(96, rel=0.01)
    assert s.phase == pytest.approx(0.25 * 96 / 60, abs=0.03)  # a quarter second after a beat


def test_beat_notes_first_note_lands_on_a_whole_beat():
    c = beat_clock()
    c.on_midi_beat(3.0)
    assert c.snapshot(3.0).phase == pytest.approx(0.0, abs=1e-9)


def test_beat_notes_adapt_to_a_tempo_change():
    c = beat_clock()
    feed_beats(c, 100, 12, t0=0.0)
    t0 = 12 * 0.6
    feed_beats(c, 140, 20, t0=t0)
    assert c._bpm == pytest.approx(140, rel=0.02)


def test_beat_notes_gap_then_recovery_and_lock_flag():
    c = beat_clock()
    times = feed_beats(c, 120, 8)
    assert c.snapshot(times[-1] + 0.4).locked
    assert not c.snapshot(times[-1] + 3.0).locked  # no note for 6 beats
    c.on_midi_beat(times[-1] + 3.0)
    assert c.snapshot(times[-1] + 3.0).locked


def test_beat_notes_keep_the_downbeat_set_by_hand():
    c = beat_clock()
    times = feed_beats(c, 120, 10)
    ts = times[-1] + 0.1
    c.sync_downbeat(ts)  # "now is the first beat of a bar"
    assert c.snapshot(ts).beat % 4 == pytest.approx(0.0, abs=1e-6)
    later = feed_beats(c, 120, 9, t0=ts + 0.5)  # the notes go on; each is the next beat after the sync point
    beat = c.snapshot(later[-1]).beat
    assert beat % 4 == pytest.approx(1.0, abs=0.1)  # 9 beats after a downbeat = beat 2 of the bar (0-based 1)


def test_announced_tempo_is_shown_but_does_not_drive_the_clock():
    c = beat_clock()
    feed_beats(c, 120, 12)
    c.set_reported_bpm(118)
    s = c.snapshot(8.0)
    assert s.bpm == 118 and c._bpm == pytest.approx(120, rel=0.01)


def test_beat_notes_are_ignored_in_clock_mode_and_on_the_internal_source():
    c = Clock(bpm=120, now=0.0)
    c.on_midi_beat(1.0)  # internal
    assert c.snapshot(1.0).beat == pytest.approx(2.0)
    c.use_midi(1.0)  # clock mode
    c.on_midi_beat(2.0)
    assert c._next_beat is None


# --- audio source --------------------------------------------------------------------------


def test_audio_free_runs_until_the_tracker_is_sure():
    c = Clock(bpm=120, now=0.0)
    c.use_audio(now=1.0)
    s = c.snapshot(2.0)
    assert s.source == "audio" and not s.locked and s.status == "waiting for beat"
    assert s.beat == pytest.approx(4.0)  # the beat kept running at the old tempo
    c.on_audio_estimate(2.0, anchor_time=2.0, anchor_beat=0.3, bpm=128, confidence=0.1, locked=False)
    assert c.snapshot(2.0).beat == pytest.approx(4.0) and c.snapshot(2.0).bpm == 120
    assert c.snapshot(2.0).confidence == pytest.approx(0.1)


def test_audio_takes_the_trackers_phase_but_keeps_its_own_beat_numbers():
    c = Clock(bpm=120, now=0.0)
    c.use_audio(now=0.0)
    # the clock is at beat 10.3; the tracker says a beat (its own number 0) is happening right now
    c.on_audio_estimate(5.15, anchor_time=5.15, anchor_beat=0.0, bpm=126, confidence=0.8, locked=True)
    s = c.snapshot(5.15)
    assert s.beat == pytest.approx(10.0) and s.bpm == pytest.approx(126)
    assert s.locked and s.status == "locked"


def test_audio_slews_small_differences():
    c = Clock(bpm=120, now=0.0)
    c.use_audio(now=0.0)
    c.on_audio_estimate(1.0, 1.0, 0.0, 120, 0.9, True)  # first one is taken whole: beat 2.0 at t=1
    c.on_audio_estimate(2.0, 2.0, 0.1, 120, 0.9, True)  # tracker 0.1 beat ahead now
    assert c.snapshot(2.0).beat == pytest.approx(4.05)  # half of it


def test_audio_jumps_when_the_tracker_relocks():
    c = Clock(bpm=120, now=0.0)
    c.use_audio(now=0.0)
    c.on_audio_estimate(1.0, 1.0, 0.0, 120, 0.9, True)
    c.on_audio_estimate(2.0, 2.0, 0.4, 120, 0.9, True)  # a new track, 0.4 beat off
    assert c.snapshot(2.0).beat == pytest.approx(4.4)


def test_audio_keeps_the_downbeat():
    c = Clock(bpm=120, beats_per_bar=4, now=0.0)
    c.use_audio(now=0.0)
    c.on_audio_estimate(1.0, 1.0, 0.0, 120, 0.9, True)
    c.sync_downbeat(now=1.4)  # beat 2.8 -> 4.0: the bar starts here
    for k in range(10):  # tracker beats every 0.5 s from 1.4 on, its own numbering
        t = 1.5 + 0.1 * k
        c.on_audio_estimate(t, 1.4, 7.0, 120, 0.9, True)
    assert c.snapshot(1.4 + 2.0).bar == pytest.approx(2.0, abs=1e-6)  # four beats later: the next bar


def test_audio_lock_times_out_but_the_beat_keeps_running():
    c = Clock(bpm=120, now=0.0)
    c.use_audio(now=0.0)
    c.on_audio_estimate(1.0, 1.0, 0.0, 124, 0.9, True)
    assert c.snapshot(1.9).locked
    s = c.snapshot(3.0)
    assert not s.locked and s.status == "waiting for beat" and s.bpm == pytest.approx(124)


def test_audio_estimates_are_ignored_by_the_other_sources():
    c = Clock(bpm=120, now=0.0)
    c.on_audio_estimate(1.0, 1.0, 0.3, 140, 0.9, True)
    assert c.snapshot(1.0).beat == pytest.approx(2.0) and c.snapshot(1.0).bpm == 120


def test_audio_clock_follows_the_tracker_on_real_audio():
    from test_beat_tracker import SR, track

    from floorshow.common.beat_tracker import BeatTracker

    y, beats = track([(40, 126, True, 0)])
    t0 = 50.0
    c = Clock(bpm=120, now=t0)
    c.use_audio(now=t0)
    tr = BeatTracker(SR)
    block = int(0.02 * SR)
    last = 0.0
    errs = []
    for i in range(0, len(y) - block, block):
        tr.feed(y[i:i + block], t0 + i / SR)
        now = t0 + (i + block) / SR
        if now - last >= 0.1:  # the listen process hands estimates over ten times a second
            e = tr.estimate()
            c.on_audio_estimate(now, e.anchor_time, e.anchor_beat, e.bpm, e.confidence, e.locked)
            last = now
        k = int(np.searchsorted(beats, now - t0)) - 1
        if now - t0 > 12 and 0 <= k:  # the tracker has settled by then (its own test checks the first seconds)
            b = c.snapshot(t0 + beats[k]).beat
            errs.append((b - round(b)) * 60 / 126 * 1000)
    assert c.snapshot(now).locked
    assert np.max(np.abs(errs)) < 15
