"""The listen process's logic, without GStreamer: which sources are captured, a beat tracker per source, and the
messages that go to the renderer. Capture is injected (``capture_factory``), so this runs in tests on synthetic audio.

Messages (dicts, sent as JSON lines by ``__main__``):

- ``{"type": "sources", "sources": [{"id", "name", "kind"}]}`` whenever the list changes. ``kind`` is "mic" (an input)
  or "monitor" (what an output plays).
- ``{"type": "estimate", "source", "active", "t", "anchor_time", "anchor_beat", "bpm", "confidence", "locked",
  "level_db"}``: the tracker's model as of ``t`` (monotonic seconds, the renderer's clock too). Ten times a second for
  the active source, four times for the others (previews).
- ``{"type": "health", "source", "rtf", "age_ms", "jitter_ms", "drift_ppm", "gap_ms", "level_db", "ok"}`` once a
  second. ``age_ms``: how old the newest analysed audio was when it was analysed. ``rtf``: analysis time per audio
  time. ``gap_ms``: audio PipeWire lost in the last 10 s (filled with silence). ``ok``: the beat can be had in time.
- ``{"type": "load", "load", "throttled"}`` when previews are paused because the analysis cannot keep up, or resume.
- ``{"type": "error", "source", "message"}``.
"""

from __future__ import annotations

import math
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Protocol

import numpy as np

from ..common.beat_tracker import BeatTracker
from .timing import SampleClock

SR = 48000
ACTIVE_EVERY_S = 0.1
PREVIEW_EVERY_S = 0.25
HEALTH_EVERY_S = 1.0
GAP_WINDOW_S = 10.0
# "ok": the analysis keeps up with room to spare, and audio is analysed while its beat is still fresh enough to correct
MAX_AGE_S = 0.15
MAX_RTF = 0.25  # one source; all of them share one core (Python's GIL)
# Previews pause when any source's audio is analysed later than MAX_AGE_S (the analysis is falling behind), or as a
# backstop when the summed rtf passes LOAD_LIMIT. The age is the real signal: CPU time per audio time says little about
# what is left. Fed in 20 ms bursts with idle time between (cold caches, the CPU clocked down), the same analysis costs
# five times what it costs run back to back (0.064 against 0.013 on an i7-8650U); under real load the CPU speeds up.
LOAD_LIMIT = 0.9
THROTTLE_RETRY_S = 30.0
SILENT_DB = -60.0

Emit = Callable[[dict[str, Any]], None]


class CaptureHandle(Protocol):
    def stop(self) -> None: ...

    def filled_samples(self) -> int:
        """Samples PipeWire lost that were filled with silence, since the start."""
        ...


# capture_factory(source, on_buffer, on_error) -> handle; on_buffer(samples float32 mono at SR, arrival monotonic)
CaptureFactory = Callable[[dict[str, Any], Callable[[np.ndarray, float], None], Callable[[str], None]], CaptureHandle]


class SourceAnalyzer:
    """One source: timestamps, tracker, level, health. ``process`` is called with every buffer, in order."""

    def __init__(self, source_id: str, active: bool, emit: Emit, now_fn: Callable[[], float] = time.monotonic,
                 filled_fn: Callable[[], int] = lambda: 0):
        self.id = source_id
        self.active = active
        self.emit = emit
        self.now_fn = now_fn
        self.filled_fn = filled_fn
        self.clock = SampleClock(SR)
        self.tracker = BeatTracker(SR)
        self.n = 0
        self.rtf = 0.0
        self._busy = 0.0
        self._audio = 0.0
        self._level = 1e-10  # mean square, smoothed
        self.age = 0.0  # how old the newest analysed audio was when it was analysed (s)
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
            self._next_estimate = now + (ACTIVE_EVERY_S if self.active else PREVIEW_EVERY_S)
            self._send_estimate(now)
        if now >= self._next_health:
            self._next_health = now + HEALTH_EVERY_S
            self._send_health(now)

    def level_db(self) -> float:
        return 10 * math.log10(max(self._level, 1e-10))

    def _send_estimate(self, now: float) -> None:
        e = self.tracker.estimate()
        silent = self.level_db() < SILENT_DB
        self.emit({"type": "estimate", "source": self.id, "active": self.active, "t": now,
                   "anchor_time": e.anchor_time, "anchor_beat": e.anchor_beat, "bpm": round(e.bpm, 3),
                   "confidence": 0.0 if silent else round(e.confidence, 3), "locked": e.locked and not silent,
                   "level_db": round(self.level_db(), 1)})

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
                   "gap_ms": round(gap_ms, 1), "level_db": round(self.level_db(), 1), "ok": ok})


@dataclass
class _Running:
    analyzer: SourceAnalyzer
    capture: CaptureHandle
    queue: queue.SimpleQueue | None
    thread: threading.Thread | None


class Listener:
    """Captures what is wanted: the active source, and while previews are on every source (unless throttled)."""

    def __init__(self, capture_factory: CaptureFactory, emit: Emit, now_fn: Callable[[], float] = time.monotonic,
                 threaded: bool = True):
        self.capture_factory = capture_factory
        self.emit = emit
        self.now_fn = now_fn
        self.threaded = threaded
        self.sources: dict[str, dict[str, Any]] = {}
        self.active: str | None = None
        self.scan = False
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

    def configure(self, active: str | None, scan: bool) -> None:
        self.active, self.scan = active, scan
        self._apply()

    def check_load(self) -> None:
        """Called about once a second: pause the previews when the analysis is too slow, retry later."""
        with self._lock:
            load = sum(r.analyzer.rtf for r in self._running.values())
            late = any(r.analyzer.age > MAX_AGE_S for r in self._running.values())
        now = self.now_fn()
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

    def _wanted(self) -> set[str]:
        want = {self.active} if self.active in self.sources else set()
        if self.scan and self.now_fn() >= self.throttled_until:
            want |= set(self.sources)
        return want

    def _apply(self) -> None:
        want = self._wanted()
        with self._lock:
            for sid in [s for s in self._running if s not in want]:
                self._stop(sid)
            for sid in want:
                if sid in self._running:
                    self._running[sid].analyzer.active = sid == self.active
                else:
                    self._start(sid)

    def _start(self, sid: str) -> None:
        q: queue.SimpleQueue | None = queue.SimpleQueue() if self.threaded else None
        holder: dict[str, CaptureHandle] = {}
        an = SourceAnalyzer(sid, sid == self.active, self.emit, self.now_fn,
                            filled_fn=lambda: holder["c"].filled_samples() if "c" in holder else 0)

        def on_buffer(samples: np.ndarray, arrival: float) -> None:
            if q is not None:
                q.put((samples, arrival))
            else:
                an.process(samples, arrival)

        def on_error(message: str) -> None:
            self.emit({"type": "error", "source": sid, "message": message})

        try:
            cap = self.capture_factory(self.sources[sid], on_buffer, on_error)
        except Exception as e:  # noqa: BLE001 - a broken device must not take the others down
            on_error(str(e))
            return
        holder["c"] = cap
        thread = None
        if q is not None:
            thread = threading.Thread(target=_work, args=(q, an), name=f"listen {sid}", daemon=True)
            thread.start()
        self._running[sid] = _Running(an, cap, q, thread)

    def _stop(self, sid: str) -> None:
        r = self._running.pop(sid)
        try:
            r.capture.stop()
        finally:
            if r.queue is not None:
                r.queue.put(None)


def _work(q: queue.SimpleQueue, an: SourceAnalyzer) -> None:
    while (item := q.get()) is not None:
        try:
            an.process(*item)
        except Exception as e:  # noqa: BLE001
            an.emit({"type": "error", "source": an.id, "message": f"analysis failed: {e}"})
            return
