import numpy as np
import pytest

from mirrorballs.common.beat_tracker import BeatTracker

SR = 44100


def track(sections, seed=1):
    """Kick on the beat, hat and bass on the off-beat. ``sections``: (beats, bpm, kick, phase_jump_beats).
    Returns (audio, true beat times). The off-beat is as loud as the kick in the highs, as in house."""
    rng = np.random.default_rng(seed)
    t, beats = 0.5, []
    for n, bpm, kick, jump in sections:
        t += jump * 60.0 / bpm
        for _ in range(n):
            beats.append((t, 60.0 / bpm, kick))
            t += 60.0 / bpm
    y = np.zeros(int((t + 1.0) * SR))
    tk = np.arange(int(0.2 * SR)) / SR
    kick_sig = np.sin(2 * np.pi * (50 * tk + 2.5 * (1 - np.exp(-tk / 0.025)))) * np.exp(-tk / 0.1)
    th = np.arange(int(0.04 * SR)) / SR
    hat = np.diff(rng.standard_normal(len(th) + 1)) * np.exp(-th / 0.01)
    tb = np.arange(int(0.15 * SR)) / SR
    bass = np.sin(2 * np.pi * 55 * tb) * np.minimum(1, tb / 0.008) * np.exp(-tb / 0.08)

    def add(at, sig, gain):
        i = int(at * SR)
        y[i:i + len(sig)] += gain * sig[: len(y) - i]

    for bt, period, kick in beats:
        if kick:
            add(bt, kick_sig, 0.8)
        add(bt + period / 2, hat, 0.3)
        add(bt + period / 2, bass, 0.2)
    y += 0.002 * rng.standard_normal(len(y))
    return y.astype(np.float32), np.array([b[0] for b in beats])


def run(y, block_s=0.02, t0=100.0):
    """Feed in blocks; yield (time the block arrived, tracker) after each block."""
    tr = BeatTracker(SR)
    block = int(block_s * SR)
    for i in range(0, len(y) - block, block):
        tr.feed(y[i:i + block], t0 + i / SR)
        yield t0 + (i + block) / SR, tr


def phase_errors_ms(tr, beats, t0, since):
    """Phase error of the tracker's model at every true beat after ``since`` (s, clip time), in ms."""
    out = []
    for k in range(len(beats) - 1):
        if beats[k] < since:
            continue
        c = tr.beat_at(t0 + beats[k])
        out.append((c - round(c)) * (beats[k + 1] - beats[k]) * 1000)
    return np.array(out)


def test_locks_on_the_kick_not_the_louder_off_beat():
    y, beats = track([(40, 126, True, 0)])
    errs = []
    for now, tr in run(y):
        k = np.searchsorted(beats, now - 100.0) - 1
        if now - 100.0 > 8 and 0 <= k < len(beats) - 1:  # where the tracker puts the latest beat, using only the past
            c = tr.beat_at(100.0 + beats[k])
            errs.append((c - round(c)) * 60 / 126 * 1000)
    assert tr.bpm() == pytest.approx(126, abs=0.5)
    assert np.median(np.abs(errs)) < 10
    assert tr.estimate().locked


def test_follows_a_tempo_jump_with_a_phase_shift_within_a_few_seconds():
    y, beats = track([(32, 124, True, 0), (32, 130, True, 0.37)])
    jump_at = beats[32]
    tr = None
    for now, tr in run(y):
        if now - 100.0 >= jump_at + 5.0:
            break
    assert tr.bpm() == pytest.approx(130, abs=0.7)
    errs = phase_errors_ms(tr, beats[beats < jump_at + 5.0], 100.0, jump_at + 4.0)
    assert np.all(np.abs(errs) < 25)


def test_keeps_the_beat_through_a_break_without_kick():
    y, beats = track([(24, 126, True, 0), (24, 126, False, 0), (16, 126, True, 0)])
    end_break = beats[48]
    worst = 0.0
    for now, tr in run(y):
        clip_t = now - 100.0
        if 10.0 < clip_t < end_break:
            k = np.searchsorted(beats, clip_t) - 1
            c = tr.beat_at(100.0 + beats[k])
            worst = max(worst, abs(c - round(c)) * 60 / 126 * 1000)
    assert worst < 30  # the off-beat hats and bass never took the beat over


def test_silence_gives_no_beat():
    y = np.zeros(10 * SR, dtype=np.float32)
    for _, tr in run(y):
        pass
    assert tr.beat_at(110.0) is None
    assert not tr.estimate().locked


def test_beat_at_extrapolates_continuously():
    y, _ = track([(30, 120, True, 0)])
    for _, tr in run(y):
        pass
    now = 100.0 + len(y) / SR
    assert tr.beat_at(now + 1.0) - tr.beat_at(now) == pytest.approx(tr.bpm() / 60.0)


def test_sample_rate_must_divide_into_frames():
    with pytest.raises(ValueError):
        BeatTracker(44101)
