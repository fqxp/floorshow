"""The listen process's logic, without GStreamer: which sources are captured and how, the beat tracker of the active
source, and the messages that go to the renderer. Capture is injected (``capture_factory``), so this runs in tests on
synthetic audio.

The active source is *tracked*: its samples go to a beat tracker. While previews are on, every other source is only
*metered*: GStreamer measures its level per channel, no sample reaches Python, so watching a dozen sources costs
almost nothing. A meter is enough to see where the music is.

Messages (dicts, sent as JSON lines by ``__main__``):

- ``{"type": "sources", "sources": [{"id", "name", "kind", "channels"}]}`` whenever the list changes. ``kind`` is
  "mic" (an input) or "monitor" (what an output plays).
- ``{"type": "level", "source", "rms_db": [...], "peak_db": [...]}``: per channel, about ten times a second, for every
  captured source (the tracked one too).
- ``{"type": "estimate", "source", "t", "anchor_time", "anchor_beat", "bpm", "confidence", "locked"}``: the tracker's
  model as of ``t`` (monotonic seconds, the renderer's clock too), ten times a second.
- ``{"type": "health", "source", "rtf", "age_ms", "jitter_ms", "drift_ppm", "gap_ms", "ok"}`` once a second, for the
  tracked source. ``age_ms``: how old the newest analysed audio was when it was analysed. ``rtf``: analysis time per
  audio time. ``gap_ms``: audio PipeWire lost in the last 10 s (filled with silence). ``ok``: the beat can be had in
  time.
- ``{"type": "load", "load", "throttled"}`` when previews are paused because the analysis falls behind, or resume.
- ``{"type": "error", "source", "message"}``.
"""

from __future__ import annotations

import math
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Literal, Protocol

import numpy as np

from ..common.beat_tracker import BeatTracker
from .timing import SampleClock

SR = 48000
ESTIMATE_EVERY_S = 0.1
HEALTH_EVERY_S = 1.0
GAP_WINDOW_S = 10.0
# "ok": the analysis keeps up with room to spare, and audio is analysed while its beat is still fresh enough to correct
MAX_AGE_S = 0.15
MAX_RTF = 0.25
# Previews pause when the tracked source's audio is analysed later than MAX_AGE_S (the analysis falls behind), or as a
# backstop when its rtf passes LOAD_LIMIT. The age is the real signal: CPU time per audio time says little about what
# is left. Fed in 20 ms bursts with idle time between (cold caches, the CPU clocked down), the same analysis costs five
# times what it costs run back to back (0.064 against 0.013 on an i7-8650U); under real load the CPU speeds up.
LOAD_LIMIT = 0.9
THROTTLE_RETRY_S = 30.0
RETRY_S = 5.0  # a capture that failed is started again this much later
SILENT_DB = -60.0
FLOOR_DB = -100.0

Emit = Callable[[dict[str, Any]], None]
Mode = Literal["track", "meter"]
OnBuffer = Callable[[np.ndarray, float], None]  # samples (float32 mono at SR), arrival (monotonic)
OnLevel = Callable[[list[float], list[float]], None]  # rms_db, peak_db per channel


class CaptureHandle(Protocol):
    def stop(self) -> None: ...

    def filled_samples(self) -> int:
        """Samples PipeWire lost that were filled with silence, since the start (track mode)."""
        ...


# capture_factory(source, mode, on_buffer (None when metering), on_level, on_error) -> handle
CaptureFactory = Callable[[dict[str, Any], Mode, OnBuffer | None, OnLevel, Callable[[str], None]], CaptureHandle]


class SourceAnalyzer:
    """The tracked source: timestamps, tracker, health. ``process`` is called with every buffer, in order."""

    def __init__(self, source_id: str, emit: Emit, now_fn: Callable[[], float] = time.monotonic,
                 filled_fn: Callable[[], int] = lambda: 0, min_bpm: float = 70.0, max_bpm: float = 180.0):
        self.id = source_id
        self.emit = emit
        self.now_fn = now_fn
        self.filled_fn = filled_fn
        self.clock = SampleClock(SR)
        self.tracker = BeatTracker(SR, min_bpm=min_bpm, max_bpm=max_bpm, prior_bpm=_prior(min_bpm, max_bpm))
        self.n = 0
        self.rtf = 0.0
        self.age = 0.0  # how old the newest analysed audio was when it was analysed (s)
        self._busy = 0.0
        self._audio = 0.0
        self._level = 1e-10  # mean square, smoothed: a silent source is never locked
        self._next_estimate = 0.0
        self._next_health = 0.0
        self._filled: deque[tuple[float, int]] = deque()  # (time, filled samples so far)

    def process(self, samples: np.ndarray, arrival: float) -> None:
        start = time.thread_time()  # CPU time of this thread: wall time would count waiting for the GIL
        n0 = self.n
        self.n += len(samples)
        self.clock.add(self.n, arrival)
        self.tracker.feed(samples, self.clock.time_of(n0))
        if len(samples):
            ms = float(np.dot(samples, samples)) / len(samples)
            self._level += 0.1 * (ms - self._level)
        self._busy += time.thread_time() - start
        self._audio += len(samples) / SR
        now = self.now_fn()
        self.age = float(now - self.clock.time_of(self.n))
        if now >= self._next_estimate:
            self._next_estimate = now + ESTIMATE_EVERY_S
            self._send_estimate(now)
        if now >= self._next_health:
            self._next_health = now + HEALTH_EVERY_S
            self._send_health(now)

    def level_db(self) -> float:
        return 10 * math.log10(max(self._level, 1e-10))

    def _send_estimate(self, now: float) -> None:
        e = self.tracker.estimate()
        silent = self.level_db() < SILENT_DB
        self.emit({"type": "estimate", "source": self.id, "t": now, "anchor_time": e.anchor_time,
                   "anchor_beat": e.anchor_beat, "bpm": round(e.bpm, 3),
                   "confidence": 0.0 if silent else round(e.confidence, 3), "locked": bool(e.locked and not silent)})

    def _send_health(self, now: float) -> None:
        if self._audio > 0:
            self.rtf = self._busy / self._audio
        self._busy = self._audio = 0.0
        filled = self.filled_fn()
        self._filled.append((now, filled))
        while self._filled and now - self._filled[0][0] > GAP_WINDOW_S:
            self._filled.popleft()
        gap_ms = (filled - self._filled[0][1]) / SR * 1000
        ok = bool(self.rtf < MAX_RTF and self.age < MAX_AGE_S)
        self.emit({"type": "health", "source": self.id, "rtf": round(self.rtf, 4), "age_ms": round(self.age * 1000, 1),
                   "jitter_ms": round(self.clock.jitter_ms(), 2), "drift_ppm": round(self.clock.drift_ppm(), 1),
                   "gap_ms": round(gap_ms, 1), "ok": ok})


def _prior(min_bpm: float, max_bpm: float) -> float:
    """The tracker's favourite tempo: 122 when the range allows, else the middle of the range (on a log scale)."""
    return 122.0 if min_bpm <= 122.0 <= max_bpm else math.sqrt(min_bpm * max_bpm)


@dataclass
class _Running:
    mode: Mode
    capture: CaptureHandle | None = None
    analyzer: SourceAnalyzer | None = None
    queue: queue.SimpleQueue | None = None
    thread: threading.Thread | None = None
    failed_at: float | None = None  # the capture reported an error; it is restarted RETRY_S later


class Listener:
    """Captures what is wanted: the active source (tracked), and while previews are on every other one (metered)."""

    def __init__(self, capture_factory: CaptureFactory, emit: Emit, now_fn: Callable[[], float] = time.monotonic,
                 threaded: bool = True):
        self.capture_factory = capture_factory
        self.emit = emit
        self.now_fn = now_fn
        self.threaded = threaded
        self.sources: dict[str, dict[str, Any]] = {}
        self.active: str | None = None
        self.scan = False
        self.tempo = (70.0, 180.0)
        self.throttled_until = 0.0
        self._running: dict[str, _Running] = {}
        self._lock = threading.Lock()

    # --- inputs ----------------------------------------------------------------------------

    def set_sources(self, sources: list[dict[str, Any]]) -> None:
        new = {s["id"]: s for s in sources}
        if new.keys() != self.sources.keys() or any(new[k] != self.sources[k] for k in new):
            self.sources = new
            self.emit({"type": "sources", "sources": list(new.values())})
        self._apply()

    def configure(self, active: str | None, scan: bool, min_bpm: float = 70.0, max_bpm: float = 180.0) -> None:
        tempo = (float(min_bpm), float(max_bpm))
        if tempo != self.tempo and active in self._running:
            with self._lock:
                self._stop(active)  # a new tempo range: start the tracker over
        self.active, self.scan, self.tempo = active, scan, tempo
        self._apply()

    def check_load(self) -> None:
        """Called about once a second: restart failed captures, pause the previews when the tracking falls behind."""
        now = self.now_fn()
        with self._lock:
            failed = [s for s, r in self._running.items() if r.failed_at is not None and now - r.failed_at >= RETRY_S]
            for sid in failed:
                self._stop(sid)
            tracked = [r.analyzer for r in self._running.values() if r.analyzer]
        if failed:
            self._apply()
        load = sum(a.rtf for a in tracked)
        late = any(a.age > MAX_AGE_S for a in tracked)
        if self.scan and (late or load > LOAD_LIMIT) and now >= self.throttled_until:
            self.throttled_until = now + THROTTLE_RETRY_S
            self.emit({"type": "load", "load": round(load, 3), "throttled": True})
            self._apply()
        elif self.throttled_until and now >= self.throttled_until:
            self.throttled_until = 0.0
            self.emit({"type": "load", "load": round(load, 3), "throttled": False})
            self._apply()

    def close(self) -> None:
        self.active, self.scan = None, False
        self._apply()

    # --- internals -------------------------------------------------------------------------

    def _wanted(self) -> dict[str, Mode]:
        want: dict[str, Mode] = {}
        if self.scan and self.now_fn() >= self.throttled_until:
            want = {sid: "meter" for sid in self.sources}
        if self.active in self.sources:
            want[self.active] = "track"
        return want

    def _apply(self) -> None:
        want = self._wanted()
        with self._lock:
            for sid in [s for s, r in self._running.items() if want.get(s) != r.mode]:
                self._stop(sid)
            for sid, mode in want.items():
                if sid not in self._running:
                    self._start(sid, mode)

    def _start(self, sid: str, mode: Mode) -> None:
        def on_level(rms: list[float], peak: list[float]) -> None:
            self.emit({"type": "level", "source": sid, "rms_db": [_db(x) for x in rms],
                       "peak_db": [_db(x) for x in peak]})

        entry = _Running(mode)

        def on_error(message: str) -> None:
            self.emit({"type": "error", "source": sid, "message": message})
            if entry.failed_at is None:
                entry.failed_at = self.now_fn()

        holder: dict[str, CaptureHandle] = {}
        an = q = None
        on_buffer: OnBuffer | None = None
        if mode == "track":
            an = SourceAnalyzer(sid, self.emit, self.now_fn, min_bpm=self.tempo[0], max_bpm=self.tempo[1],
                                filled_fn=lambda: holder["c"].filled_samples() if "c" in holder else 0)
            if self.threaded:  # GStreamer's thread only queues; the analysis runs in the source's own thread
                q = queue.SimpleQueue()
                on_buffer = lambda samples, arrival, q=q: q.put((samples, arrival))  # noqa: E731
            else:
                on_buffer = an.process
        self._running[sid] = entry  # also when the capture cannot start: check_load retries it RETRY_S later
        try:
            cap = self.capture_factory(self.sources[sid], mode, on_buffer, on_level, on_error)
        except Exception as e:  # noqa: BLE001 - a broken device must not take the others down
            on_error(str(e))
            return
        holder["c"] = cap
        entry.capture, entry.analyzer, entry.queue = cap, an, q
        if an is not None and q is not None:
            entry.thread = threading.Thread(target=_work, args=(q, an), name=f"listen {sid}", daemon=True)
            entry.thread.start()

    def _stop(self, sid: str) -> None:
        r = self._running.pop(sid)
        try:
            if r.capture is not None:
                r.capture.stop()
        finally:
            if r.queue is not None:
                r.queue.put(None)


def _db(x: float) -> float:
    return round(max(FLOOR_DB, x), 1) if math.isfinite(x) else FLOOR_DB


def _work(q: queue.SimpleQueue, an: SourceAnalyzer) -> None:
    while (item := q.get()) is not None:
        try:
            an.process(*item)
        except Exception as e:  # noqa: BLE001
            an.emit({"type": "error", "source": an.id, "message": f"analysis failed: {e}"})
            return
