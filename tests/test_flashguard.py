import math

import pytest

from mirrorballs.render.flashguard import FlashGuard

FPS = 60


def run(guard, seconds, fn, t0=0.0):
    """Feed fn(t) as luminance at 60 fps; return the gains. The output swing is luminance * gain."""
    out = []
    n = int(seconds * FPS)
    for k in range(n):
        t = t0 + k / FPS
        out.append(guard.update(t, fn(t)))
    return out


def square(hz, lo=0.0, hi=1.0):
    return lambda t: hi if math.floor(t * hz * 2) % 2 == 0 else lo


def test_steady_and_slow_content_is_never_touched():
    g = FlashGuard()
    assert set(run(g, 3, lambda t: 0.6)) == {1.0}
    g = FlashGuard()
    assert set(run(g, 6, square(2.0))) == {1.0}  # 2 flashes per second is allowed
    assert not g.limited


def test_exactly_three_flashes_per_second_is_allowed():
    g = FlashGuard()
    assert set(run(g, 4, square(3.0))) == {1.0}


def test_fast_full_frame_flashing_is_limited_and_the_output_swing_drops_below_the_threshold():
    g = FlashGuard()
    gains = run(g, 3, square(8.0))
    assert g.limited and min(gains) < 0.2
    # once limited, the *output* swing (scene swing * gain) is below 10 %, so it stops counting as flashing
    tail = run(g, 1, square(8.0), t0=3.0)
    assert max(tail) < 0.2 and 1.0 * max(tail) < 0.2


def test_reaction_is_fast():
    g = FlashGuard()
    gains = run(g, 2, square(10.0))
    first_limited = next(i for i, x in enumerate(gains) if x < 0.999) / FPS
    assert first_limited < 0.6  # within a fraction of the first second


def test_low_contrast_flicker_is_ignored():
    g = FlashGuard()
    assert set(run(g, 4, square(10.0, 0.50, 0.55))) == {1.0}  # 5 % swing is below the 10 % threshold


def test_gain_recovers_after_the_flashing_stops():
    g = FlashGuard()
    run(g, 2, square(9.0))
    assert g.limited
    later = run(g, 4, lambda t: 0.4, t0=2.0)
    assert later[-1] == 1.0 and not g.limited
    assert min(later) < 1.0 and later == sorted(later)  # it ramps back up (release is 1.0 per second), never snapping or dipping again


def test_disabled_guard_never_limits():
    g = FlashGuard(enabled=False)
    assert set(run(g, 3, square(10.0))) == {1.0}
    g2 = FlashGuard()
    run(g2, 2, square(10.0))
    assert g2.limited
    g2.configure(3.0, enabled=False)
    assert g2.gain == 1.0 and not g2.limited


def test_limit_is_configurable():
    g = FlashGuard(max_flashes_per_s=1)
    run(g, 3, square(2.5))  # 2.5 flashes/s is fine at the default 3 but not at 1
    assert g.limited
    g = FlashGuard(max_flashes_per_s=5)
    assert set(run(g, 3, square(4.0))) == {1.0}


def test_measured_flash_rate():
    g = FlashGuard()
    run(g, 2, square(2.0))
    assert g.flashes_per_s(2.0) == pytest.approx(2.0, abs=0.6)
