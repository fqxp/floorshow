"""Beat clock: one authority for ``beat`` / ``phase`` / ``bar``, fed by MIDI clock or an internal BPM.

Pure logic. Every method takes the time explicitly (seconds, any monotonic origin), so it can be
driven by simulated tick streams in tests. The renderer samples ``snapshot(now)`` once per frame.

Model: ``beat(now) = anchor_beat + (now - anchor_time) * bpm / 60`` for both sources. MIDI ticks
(24 per beat) do not set the beat directly; they nudge the anchor (a first-order phase-locked loop)
and their tick spacing over the last two beats sets the tempo, so ticks that arrive with a few ms of jitter never make
the phase jump. A large error (Start, Song Position Pointer, a dropout) hard-resyncs instead.

Some programs (Mixxx's "MIDI for light" script) send no MIDI clock but one note per beat. "Beat notes" mode feeds
those into the same kind of loop: every note is the next whole beat. The bar position is not part of such a
stream, so the downbeat is set by hand (``sync_downbeat``); an announced tempo is only used for display.
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
BEAT_SLEW_GAIN = 0.3  # beat notes are 24 times rarer than clock ticks, so each one corrects more
BEAT_WINDOW = 9  # beat notes the tempo is measured over (8 intervals)
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
        # MIDI beat notes
        self.midi_beats = False
        self._beat_times: deque[float] = deque(maxlen=BEAT_WINDOW)
        self._next_beat: int | None = None  # number of the beat the next note is expected to be
        self._reported_bpm: float | None = None  # tempo announced by the sender (display only)
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

    def use_midi(self, now: float, beats: bool = False) -> None:
        """Switch to MIDI clock (``beats``: one note per beat instead). Free-runs at the current tempo until it arrives."""
        self._reanchor(now)
        self.source = "midi"
        self.midi_beats = beats
        self._beat_times.clear()
        self._next_beat = None
        self._reported_bpm = None
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
        here = self._anchor_beat
        self._anchor_beat = float(round(here / bpb) * bpb)
        if self._next_beat is not None:  # beat notes are numbered against the model: keep them in step
            self._next_beat += round(self._anchor_beat - here)

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

    def on_midi_beat(self, t: float) -> None:
        """One beat note arrived (beat-notes mode)."""
        if self.source != "midi" or not self.midi_beats:
            return
        if self._last_tick_time is not None and t - self._last_tick_time > max(1.5, 3 * 60.0 / self._bpm):
            self._beat_times.clear()  # a gap: start over on the next whole beat
            self._next_beat = None
        self._beat_times.append(t)
        self._last_tick_time = t
        model = self._beat_at(t)
        if self._next_beat is None:
            self._anchor_beat, self._anchor_time = float(round(model)), t  # nearest whole beat keeps the bar position
            self._next_beat = round(model) + 1
        else:
            beat = float(self._next_beat)
            self._next_beat += 1
            err = beat - model
            if abs(err) > HARD_RESYNC_BEATS:
                self._anchor_beat, self._anchor_time = beat, t
            else:
                self._anchor_beat, self._anchor_time = model + BEAT_SLEW_GAIN * err, t
        n = len(self._beat_times)
        if n >= 2:
            span = self._beat_times[-1] - self._beat_times[0]
            if span > 0:
                self._bpm = _clamp_bpm(60.0 * (n - 1) / span)

    def set_reported_bpm(self, bpm: float) -> None:
        """Tempo announced by the sender of beat notes; shown, but the measured beat spacing drives the clock."""
        self._reported_bpm = _clamp_bpm(bpm)

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
            bpm=self._reported_bpm if self.midi_beats and self._reported_bpm else self._bpm,
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
        timeout = max(LOSS_TIMEOUT_S, 2.5 * 60.0 / self._bpm) if self.midi_beats else max(LOSS_TIMEOUT_S, 4 * 60.0 / (self._bpm * PPQN))
        return now - self._last_tick_time <= timeout


def _clamp_bpm(bpm: float) -> float:
    return min(MAX_BPM, max(MIN_BPM, float(bpm)))
