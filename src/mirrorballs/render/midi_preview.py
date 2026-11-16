"""Beat previews of MIDI input ports: does a port carry a beat, and at what tempo? Pure, fed every MIDI message with
the name of the port it came from.

A port shows one of:

- "clock": MIDI clock ticks (24 per beat). The tempo is the slope over the last two beats of ticks, the beat falls on
  every 24th tick counted from Start (or from the first tick seen).
- "beats": a note that repeats evenly, like the beat notes of Mixxx's "MIDI for light" script. Found without knowing
  which note it is: any note whose last onsets come at even intervals, at a tempo between MIN_BPM and MAX_BPM.
- "active": messages, but no beat in them.
- "quiet": nothing for a while.
- "busy": the port could not be opened (another program holds the raw device); set by the MIDI manager.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

PPQN = 24
CLOCK_TICKS = 2 * PPQN + 1  # the tempo is measured over this many ticks
CLOCK_LOST_S = 0.5
NOTE_ONSETS = 9
NOTE_LOST_BEATS = 3.0  # a beat note that has not come for this many of its beats is gone
EVEN = 0.06  # every interval within this share of the median: an even beat
MIN_BPM, MAX_BPM = 40.0, 240.0
ACTIVE_S = 2.0


@dataclass
class _Port:
    ticks: deque = field(default_factory=lambda: deque(maxlen=CLOCK_TICKS))
    tick_count: int = 0  # ticks since Start (or since the first one seen)
    beat_tick_time: float | None = None  # time of the newest tick that starts a beat
    notes: dict[int, deque] = field(default_factory=dict)  # note -> onset times
    last_message: float | None = None


class MidiPreview:
    def __init__(self) -> None:
        self._ports: dict[str, _Port] = {}

    def on_message(self, port: str, data: bytes, t: float) -> None:
        if not data:
            return
        p = self._ports.setdefault(port, _Port())
        status = data[0]
        if status == 0xFE:  # active sensing says nothing about a beat
            return
        p.last_message = t
        if status == 0xFA:  # Start: the next tick is the first of a beat
            p.tick_count = 0
            p.ticks.clear()
        elif status == 0xF8:
            if p.ticks and t - p.ticks[-1] > CLOCK_LOST_S:
                p.ticks.clear()
            if p.tick_count % PPQN == 0:
                p.beat_tick_time = t
            p.ticks.append(t)
            p.tick_count += 1
        elif status & 0xF0 == 0x90 and len(data) >= 3 and data[2] > 0:
            p.notes.setdefault(data[1], deque(maxlen=NOTE_ONSETS)).append(t)

    def forget(self, port: str) -> None:
        self._ports.pop(port, None)

    def port(self, name: str, now: float) -> dict[str, Any]:
        """The preview of one port: {"name", "status", "bpm", "beat_time", "note"}."""
        p = self._ports.get(name)
        out: dict[str, Any] = {"name": name, "status": "quiet", "bpm": None, "beat_time": None, "note": None}
        if p is None:
            return out
        if len(p.ticks) >= 7 and now - p.ticks[-1] <= CLOCK_LOST_S:
            span = p.ticks[-1] - p.ticks[0]
            if span > 0:
                out.update(status="clock", bpm=round(60.0 * (len(p.ticks) - 1) / (PPQN * span), 2),
                           beat_time=p.beat_tick_time)
                return out
        best = self._beat_note(p, now)
        if best is not None:
            note, bpm, last = best
            out.update(status="beats", bpm=round(bpm, 2), beat_time=last, note=note)
            return out
        if p.last_message is not None and now - p.last_message <= ACTIVE_S:
            out["status"] = "active"
        return out

    @staticmethod
    def _beat_note(p: _Port, now: float) -> tuple[int, float, float] | None:
        """(note, bpm, last onset) of the most even repeating note, or None."""
        best = None
        for note, onsets in p.notes.items():
            if len(onsets) < 5:
                continue
            iv = sorted(b - a for a, b in zip(onsets, list(onsets)[1:]))
            med = iv[len(iv) // 2]
            if med <= 0 or not MIN_BPM <= 60.0 / med <= MAX_BPM:
                continue
            if now - onsets[-1] > NOTE_LOST_BEATS * med:
                continue
            spread = max(abs(x - med) for x in iv) / med
            if spread > EVEN:
                continue
            if best is None or spread < best[0]:
                span = onsets[-1] - onsets[0]
                best = (spread, note, 60.0 * (len(onsets) - 1) / span, onsets[-1])
        return None if best is None else best[1:]
