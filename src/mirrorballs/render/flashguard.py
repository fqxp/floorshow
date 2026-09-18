"""Photosensitivity guard: limit how often the output flashes.

Rule of thumb used by accessibility guidelines (WCAG 2.3.1, ITU-R BT.1702): no more than three flashes
in any one second, where a flash is a pair of opposing changes in luminance of at least 10 %. A
mirror ball throws that light all over a room, so this is on by default.

The guard watches the luminance of the scene *before* master brightness. When the last second holds
too many opposing changes it lowers a gain (applied to the output) until the remaining swing is below
the threshold, holds it for a moment, then lets it recover. Pure logic with explicit time, so it is
tested with synthetic signals.
"""

from __future__ import annotations

from collections import deque

THRESHOLD = 0.10  # relative luminance change that counts as a change
WINDOW_S = 1.0
HOLD_S = 1.0  # keep the reduced gain this long after the last violation
ATTACK_PER_S = 12.0  # how fast the gain may fall (per second): almost immediate
RELEASE_PER_S = 1.0  # and how fast it recovers
MIN_GAIN = 0.05


class FlashGuard:
    def __init__(self, max_flashes_per_s: float = 3.0, enabled: bool = True):
        self.max_flashes_per_s = max_flashes_per_s
        self.enabled = enabled
        self.gain = 1.0
        self.limited = False
        self._changes: deque[float] = deque()  # times of confirmed opposing changes
        self._samples: deque[tuple[float, float]] = deque()
        self._dir = 0  # +1 rising, -1 falling, 0 not yet known
        self._ext = 0.0  # running extreme of the current swing
        self._lo = self._hi = 0.0
        self._last_t: float | None = None
        self._hold_until = 0.0
        self._started = False

    def configure(self, max_flashes_per_s: float, enabled: bool) -> None:
        self.max_flashes_per_s, self.enabled = max_flashes_per_s, enabled
        if not enabled:
            self.gain, self.limited = 1.0, False

    def flashes_per_s(self, now: float) -> float:
        self._trim(now)
        return len(self._changes) / 2.0

    def update(self, now: float, luminance: float) -> float:
        """Feed one luminance sample (0..1, ball-relative). Returns the gain to apply from now on."""
        if not self.enabled:
            return 1.0
        self._observe(now, luminance)
        self._trim(now)
        dt = 0.0 if self._last_t is None else max(0.0, now - self._last_t)
        self._last_t = now
        violating = len(self._changes) > 2 * self.max_flashes_per_s
        if violating:
            self._hold_until = now + HOLD_S
        if now < self._hold_until:
            amp = self._amplitude()
            target = max(MIN_GAIN, min(1.0, 0.8 * THRESHOLD / amp)) if amp > 0 else 1.0
            self.gain = max(target, self.gain - ATTACK_PER_S * dt) if target < self.gain else self.gain
            self.limited = self.gain < 0.999
        else:
            self.gain = min(1.0, self.gain + RELEASE_PER_S * dt)
            self.limited = self.gain < 0.999
        return self.gain

    # --- internals -----------------------------------------------------------------------

    def _observe(self, now: float, lum: float) -> None:
        self._samples.append((now, lum))
        if not self._started:
            self._started = True
            self._ext = self._lo = self._hi = lum
            return
        if self._dir == 0:
            self._lo, self._hi = min(self._lo, lum), max(self._hi, lum)
            if lum - self._lo >= THRESHOLD:
                self._dir, self._ext = +1, lum
                self._changes.append(now)
            elif self._hi - lum >= THRESHOLD:
                self._dir, self._ext = -1, lum
                self._changes.append(now)
            return
        if self._dir > 0:
            self._ext = max(self._ext, lum)
            if self._ext - lum >= THRESHOLD:  # the rise is over and it fell back: an opposing change
                self._dir, self._ext = -1, lum
                self._changes.append(now)
        else:
            self._ext = min(self._ext, lum)
            if lum - self._ext >= THRESHOLD:
                self._dir, self._ext = +1, lum
                self._changes.append(now)

    def _trim(self, now: float) -> None:
        # Half-open window: an event exactly one second old no longer counts, so a steady 3 flashes
        # per second (6 changes, 1/6 s apart) is exactly at the limit, not over it.
        edge = WINDOW_S - 1e-9
        while self._changes and now - self._changes[0] >= edge:
            self._changes.popleft()
        while self._samples and now - self._samples[0][0] >= edge:
            self._samples.popleft()

    def _amplitude(self) -> float:
        if not self._samples:
            return 0.0
        values = [v for _, v in self._samples]
        return max(values) - min(values)
