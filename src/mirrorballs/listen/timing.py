"""When was a sample captured? Maps a stream's sample count to the monotonic clock.

Buffers arrive in Python with a delay that varies (scheduling, GStreamer's threads), but never arrive *before* their
samples were captured, and the shortest delays are nearly constant. So the earliest arrivals over time trace a
straight line through ``(sample count, time)``: per second of audio the earliest arrival (relative to the nominal rate)
is kept, and a line through the last ``WINDOW_BINS`` of those gives the time of any sample. The line's slope follows
the sound card's drift against the monotonic clock (some 20 ppm), which PipeWire's own timestamps do not, measured on
the built-in card.

What remains is a constant: the shortest delay from capture to Python. It is not observable here and is taken as
``CAPTURE_DELAY_S``; the clock's offset setting calibrates the rest.
"""

from __future__ import annotations

from collections import deque

import numpy as np

BIN_S = 1.0
WINDOW_BINS = 30
CAPTURE_DELAY_S = 0.005  # one PipeWire quantum (256 frames at 48 kHz), the shortest delivery seen in the spike


class SampleClock:
    def __init__(self, sr: int):
        self.sr = sr
        self._bins: deque[tuple[float, float]] = deque(maxlen=WINDOW_BINS)  # (sample count, min residual)
        self._bin_end = 0
        self._bin_min: tuple[float, float] | None = None
        self._origin: float | None = None  # arrival time of the first buffer, to keep the numbers small
        self._fit = (0.0, 0.0)  # residual = a + b * n
        self._resid: deque[float] = deque(maxlen=200)  # recent arrival residuals against the fit, for jitter

    def add(self, n_end: int, arrival: float) -> None:
        """A buffer ending at sample ``n_end`` (count since the stream started) arrived at ``arrival``."""
        if self._origin is None:
            self._origin = arrival - n_end / self.sr
        r = arrival - self._origin - n_end / self.sr  # >= the (constant) shortest delay, plus drift
        if self._bin_min is None or r < self._bin_min[1]:
            self._bin_min = (float(n_end), r)
        self._resid.append(r - self._line(n_end))
        if n_end >= self._bin_end:
            self._bins.append(self._bin_min)
            self._bin_min = None
            self._bin_end = n_end + int(BIN_S * self.sr)
            self._refit()

    def _refit(self) -> None:
        if len(self._bins) < 3:
            self._fit = (min(r for _, r in self._bins), 0.0)
            return
        n = np.array([b[0] for b in self._bins])
        r = np.array([b[1] for b in self._bins])
        b, a = np.polyfit(n, r, 1)
        # the line has to run under the points, not through them: shift it to the lowest one
        a += float(np.min(r - (a + b * n)))
        self._fit = (a, b)

    def _line(self, n: float) -> float:
        a, b = self._fit
        return a + b * n

    def time_of(self, n: float) -> float:
        """Capture time (monotonic) of sample ``n``."""
        if self._origin is None:
            raise ValueError("no buffer yet")
        return self._origin + n / self.sr + self._line(n) - CAPTURE_DELAY_S

    def drift_ppm(self) -> float:
        return float(self._fit[1] * self.sr * 1e6)

    def jitter_ms(self) -> float:
        """Spread of arrival times around the fit (how late buffers come, not how wrong the timestamps are)."""
        return float(np.std(self._resid)) * 1000 if len(self._resid) > 1 else 0.0
