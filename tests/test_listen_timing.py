import numpy as np
import pytest

from mirrorballs.listen.timing import CAPTURE_DELAY_S, SampleClock

SR = 48000


def feed(clock, seconds, ppm=0.0, jitter_s=0.003, block=960, t0=500.0, seed=3, min_delay=0.004):
    """Buffers of ``block`` samples from a card running ``ppm`` fast, arriving ``min_delay`` plus random extra late."""
    rng = np.random.default_rng(seed)
    n = 0
    while n < seconds * SR:
        n += block
        captured = t0 + n / SR / (1 + ppm * 1e-6)  # real time the buffer's last sample was captured
        clock.add(n, captured + min_delay + rng.exponential(jitter_s))


def true_time(n, ppm, t0=500.0, min_delay=0.004):
    return t0 + n / SR / (1 + ppm * 1e-6) + min_delay - CAPTURE_DELAY_S


@pytest.mark.parametrize("ppm", [0.0, 20.0, -35.0, 200.0])
def test_capture_times_follow_the_card_through_jitter(ppm):
    c = SampleClock(SR)
    feed(c, 120, ppm=ppm)
    n = 120 * SR
    assert c.time_of(n) == pytest.approx(true_time(n, ppm), abs=0.0005)  # half a millisecond after two minutes
    assert c.drift_ppm() == pytest.approx(-ppm, abs=3)  # a fast card: its samples take less real time


def test_early_on_the_earliest_arrival_is_used():
    c = SampleClock(SR)
    feed(c, 0.5)
    assert c.time_of(0.5 * SR) == pytest.approx(true_time(0.5 * SR, 0), abs=0.003)


def test_jitter_is_reported():
    c = SampleClock(SR)
    feed(c, 10, jitter_s=0.002)
    assert 1.0 < c.jitter_ms() < 4.0


def test_no_buffer_no_time():
    with pytest.raises(ValueError):
        SampleClock(SR).time_of(0)
