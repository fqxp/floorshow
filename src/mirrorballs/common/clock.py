"""Beat clock: one authority for ``beat`` / ``phase`` / ``bar``, fed by MIDI clock or an internal BPM.

Pure logic. Every method takes the time explicitly (seconds, any monotonic origin), so it can be
driven by simulated tick streams in tests. The renderer samples ``snapshot(now)`` once per frame.

Model: ``beat(now) = anchor_beat + (now - anchor_time) * bpm / 60`` for both sources. MIDI ticks
(24 per beat) do not set the beat directly; they nudge the anchor (a first-order phase-locked loop)
and their tick spacing over the last two beats sets the tempo, so ticks that arrive with a few ms of jitter never make
the phase jump. A large error (Start, Song Position Pointer, a dropout) hard-resyncs instead.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Literal

PPQN = 24
MIN_BPM, MAX_BPM = 20.0, 300.0
SLEW_GAIN = 0.15  # fraction of the phase error removed per tick
HARD_RESYNC_BEATS = 0.25
LOSS_TIMEOUT_S = 0.5  # no tick for this long (or 4 tick periods, if slower) means the clock is lost
TAP_RESET_S = 2.0
MAX_TAPS = 8

Source = Literal["internal", "midi"]


@dataclass(frozen=True)
class ClockSnapshot:
    beat: float  # continuous beats since the clock origin
    phase: float  # 0..1 within the beat
    bar: float  # continuous bars (beat / beats_per_bar)
    bpm: float
    beats_per_bar: int
    source: Source
    locked: bool  # internal: always True; midi: ticks are arriving
    running: bool  # False after a MIDI Stop (the beat keeps free-running; visuals never freeze)
    status: str  # "internal" | "locked" | "waiting for clock" | "stopped"


class Clock:
    def __init__(self, bpm: float = 120.0, beats_per_bar: int = 4, offset_ms: float = 0.0, now: float = 0.0):
        self.source: Source = "internal"
        self.beats_per_bar = beats_per_bar
        self.offset_s = offset_ms / 1000.0
        self._bpm = _clamp_bpm(bpm)
        self._anchor_beat = 0.0
        self._anchor_time = now
        # MIDI state
        self._tick_times: deque[float] = deque(maxlen=2 * PPQN + 1)  # tempo = slope over this window
        self._last_tick_time: float | None = None
        self._next_tick = 0  # index of the next tick; beat position of tick k is k / PPQN
        self._hard_next = False
        self._running = True
        # tap tempo
        self._taps: deque[float] = deque(maxlen=MAX_TAPS)

    # --- configuration -------------------------------------------------------------------

    def set_offset_ms(self, offset_ms: float) -> None:
        self.offset_s = offset_ms / 1000.0

    def set_beats_per_bar(self, n: int) -> None:
        self.beats_per_bar = max(1, int(n))

    def use_internal(self, bpm: float, now: float) -> None:
        """Switch to (or retune) the internal clock, keeping the beat continuous."""
        self._reanchor(now)
        self.source = "internal"
        self._bpm = _clamp_bpm(bpm)

    def use_midi(self, now: float) -> None:
        """Switch to MIDI clock. Free-runs at the current tempo until ticks arrive."""
        self._reanchor(now)
        self.source = "midi"
        self._tick_times.clear()
        self._last_tick_time = None
        self._next_tick = 0
        self._hard_next = True
        self._running = True

    # --- internal-clock controls ---------------------------------------------------------

    def tap(self, now: float) -> None:
        """Tap tempo. Sets BPM from the recent taps and puts the beat boundary on the tap."""
        if self._taps and now - self._taps[-1] > TAP_RESET_S:
            self._taps.clear()
        self._taps.append(now)
        self._reanchor(now)
        if len(self._taps) >= 2:
            span = (self._taps[-1] - self._taps[0]) / (len(self._taps) - 1)
            if span > 0:
                self._bpm = _clamp_bpm(60.0 / span)
        self._anchor_beat = float(round(self._anchor_beat))

    def sync_downbeat(self, now: float) -> None:
        """The current moment is the first beat of a bar."""
        self._reanchor(now)
        bpb = self.beats_per_bar
        self._anchor_beat = float(round(self._anchor_beat / bpb) * bpb)

    # --- MIDI input ----------------------------------------------------------------------

    def on_midi_tick(self, t: float) -> None:
        if self.source != "midi":
            return
        if self._last_tick_time is not None and t - self._last_tick_time >= 1.0:
            # a real gap: forget the old tempo estimate, resync on this tick
            self._tick_times.clear()
            self._hard_next = True
        self._tick_times.append(t)
        self._last_tick_time = t

        tick_beat = self._next_tick / PPQN
        self._next_tick += 1
        model_beat = self._beat_at(t)
        err = tick_beat - model_beat
        n = len(self._tick_times)
        if n >= 7:
            # Slope over the whole window: jitter only enters through the two end points, so it
            # is divided by the window length (much better than averaging or taking a median of intervals).
            span = self._tick_times[-1] - self._tick_times[0]
            if span > 0:
                self._bpm = _clamp_bpm(60.0 * (n - 1) / (PPQN * span))
        if self._hard_next or abs(err) > HARD_RESYNC_BEATS:
            self._anchor_beat, self._anchor_time = tick_beat, t
            self._hard_next = False
        else:
            self._anchor_beat, self._anchor_time = model_beat + SLEW_GAIN * err, t

    def on_midi_start(self, t: float) -> None:
        self._next_tick = 0
        self._hard_next = True
        self._running = True
        self._anchor_beat, self._anchor_time = 0.0, t

    def on_midi_continue(self, t: float) -> None:  # noqa: ARG002
        self._running = True

    def on_midi_stop(self, t: float) -> None:
        self._running = False

    def on_midi_spp(self, sixteenths: int, t: float) -> None:
        """Song Position Pointer: position in MIDI beats (16th notes), 6 ticks each."""
        self._next_tick = max(0, sixteenths) * 6
        self._hard_next = True

    # --- output --------------------------------------------------------------------------

    def snapshot(self, now: float) -> ClockSnapshot:
        beat = self._beat_at(now - self.offset_s)  # positive offset = the beat is shown later
        if self.source == "internal":
            locked, status = True, "internal"
        else:
            locked = self._is_locked(now)
            status = "waiting for clock" if not locked else "locked" if self._running else "stopped"
        return ClockSnapshot(
            beat=beat,
            phase=beat - math.floor(beat),
            bar=beat / self.beats_per_bar,
            bpm=self._bpm,
            beats_per_bar=self.beats_per_bar,
            source=self.source,
            locked=locked,
            running=self._running,
            status=status,
        )

    # --- internals -----------------------------------------------------------------------

    def _beat_at(self, t: float) -> float:
        return self._anchor_beat + (t - self._anchor_time) * self._bpm / 60.0

    def _reanchor(self, now: float) -> None:
        self._anchor_beat, self._anchor_time = self._beat_at(now), now

    def _is_locked(self, now: float) -> bool:
        if self._last_tick_time is None:
            return False
        timeout = max(LOSS_TIMEOUT_S, 4 * 60.0 / (self._bpm * PPQN))
        return now - self._last_tick_time <= timeout


def _clamp_bpm(bpm: float) -> float:
    return min(MAX_BPM, max(MIN_BPM, float(bpm)))
