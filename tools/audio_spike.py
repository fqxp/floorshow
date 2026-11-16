"""Audio capture spike: can GStreamer's pipewiresrc capture every source at once, with exact timestamps?

    uv run --extra control python tools/audio_spike.py --list
    uv run --extra control python tools/audio_spike.py --seconds 20            # every mic and every output monitor
    uv run --extra control python tools/audio_spike.py --seconds 20 --record /tmp/clips   # also write one WAV per source

One pipeline per source: ``pipewiresrc ! audioconvert ! F32 mono 48 kHz ! appsink``. Mics are captured from their
source node, outputs from their monitor (``stream.capture.sink=true`` on the sink node). For every buffer it keeps
the buffer's capture time (pipeline clock: base time + PTS, mapped to ``time.monotonic``), the time Python got it, and
the sample count, then reports per source:

- delivery latency: arrival - capture time of the buffer's last sample (how old audio is when the analyser sees it)
- jitter: residuals of a straight-line fit of capture time against sample index (timestamp quality)
- rate: the slope of that fit, as a sample rate (clock drift between the sound card and the monotonic clock)
- gaps: discontinuities (DISCONT flag, or a PTS that does not follow from the previous buffer)
- level: RMS in dBFS, to see that something is actually captured (a monitor of a silent output reads -inf)
"""

from __future__ import annotations

import argparse
import math
import sys
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

import gi
import numpy as np

gi.require_version("Gst", "1.0")
gi.require_version("GstApp", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

RATE = 48000


@dataclass
class Device:
    node: str  # node.name: what pipewiresrc's target-object takes
    name: str  # display name
    kind: str  # "mic" | "monitor"


def list_devices() -> list[Device]:
    mon = Gst.DeviceMonitor()
    mon.add_filter("Audio/Source", None)
    mon.add_filter("Audio/Sink", None)
    mon.start()
    out = []
    try:
        for d in mon.get_devices():
            props = d.get_properties()
            if props is None or not props.has_field("node.name"):
                continue
            node = props.get_value("node.name")
            if d.get_device_class() == "Audio/Sink":
                out.append(Device(node, f"Monitor of {d.get_display_name()}", "monitor"))
            else:
                out.append(Device(node, d.get_display_name(), "mic"))
    finally:
        mon.stop()
    return out


@dataclass
class Capture:
    dev: Device
    latency_frames: int
    chunk_ms: float = 0.0  # > 0: audiobuffersplit re-chunks into blocks this long before Python sees them
    align_ns: int = 0  # audiobuffersplit alignment-threshold (0 = its default, 40 ms)
    record: Path | None = None
    pipeline: Gst.Pipeline | None = None
    # per buffer: (capture time of first sample in monotonic s, arrival monotonic s, first sample index, n samples)
    rows: list[tuple[float, float, int, int]] = field(default_factory=list)
    gaps: int = 0
    samples: int = 0
    sumsq: float = 0.0
    peak: float = 0.0
    error: str | None = None
    callback_s: float = 0.0
    chunks: list[np.ndarray] = field(default_factory=list)
    _next_pts: int | None = None
    _clock_offset_ns: int | None = None  # monotonic_ns - pipeline clock time
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def build(self) -> None:
        props = f"props,node.latency={self.latency_frames}/{RATE},media.role=Music"
        if self.dev.kind == "monitor":
            props += ",stream.capture.sink=true"
        desc = (
            f'pipewiresrc name=src target-object="{self.dev.node}" client-name=mirrorballs-spike '
            f'stream-properties="{props}" ! audioconvert ! audioresample ! '
            f"audio/x-raw,format=F32LE,channels=1,rate={RATE},layout=interleaved ! "
            + (f"audiobuffersplit output-buffer-duration={round(self.chunk_ms * 10)}/10000 "
               + (f"alignment-threshold={self.align_ns} " if self.align_ns else "") + "! " if self.chunk_ms > 0 else "")
            + "appsink name=sink emit-signals=true sync=false max-buffers=1000 drop=false"
        )
        self.pipeline = Gst.parse_launch(desc)
        self.pipeline.get_by_name("sink").connect("new-sample", self._on_sample)

    def _on_sample(self, sink) -> Gst.FlowReturn:
        arrival = time.monotonic()
        t0 = time.perf_counter()
        sample = sink.emit("pull-sample")
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.ERROR
        try:
            data = np.frombuffer(info.data, dtype=np.float32).copy()
        finally:
            buf.unmap(info)
        n = len(data)
        clock = self.pipeline.get_clock()
        if self._clock_offset_ns is None and clock is not None:
            # sample both clocks back to back; the pair with the shortest gap bounds the error
            best = None
            for _ in range(5):
                a = time.monotonic_ns()
                c = clock.get_time()
                b = time.monotonic_ns()
                if best is None or b - a < best[0]:
                    best = (b - a, (a + b) // 2 - c)
            self._clock_offset_ns = best[1]
        if buf.pts != Gst.CLOCK_TIME_NONE and self._clock_offset_ns is not None:
            pts = buf.pts
            discont = bool(buf.get_flags() & Gst.BufferFlags.DISCONT)
            if self._next_pts is not None and (discont or abs(pts - self._next_pts) > 2_000_000):  # > 2 ms off
                self.gaps += 1
            self._next_pts = pts + n * 1_000_000_000 // RATE
            capture = (self.pipeline.get_base_time() + pts + self._clock_offset_ns) / 1e9
            with self._lock:
                self.rows.append((capture, arrival, self.samples, n))
        self.samples += n
        self.sumsq += float(np.dot(data, data))
        self.peak = max(self.peak, float(np.max(np.abs(data))) if n else 0.0)
        if self.record is not None:
            self.chunks.append(data)
        self.callback_s += time.perf_counter() - t0
        return Gst.FlowReturn.OK

    def report(self, seconds: float) -> dict:
        rows = np.array(self.rows) if self.rows else np.zeros((0, 4))
        r: dict = {"name": self.dev.name, "kind": self.dev.kind, "buffers": len(rows), "gaps": self.gaps,
                   "error": self.error}
        if len(rows) >= 10:
            cap, arr, idx, n = rows[:, 0], rows[:, 1], rows[:, 2], rows[:, 3]
            r["block_ms"] = float(np.median(n)) / RATE * 1000
            # age of the newest sample of each buffer when Python receives it
            delivery = arr - (cap + n / RATE)
            r["deliver_ms"] = (float(np.median(delivery)) * 1000, float(np.percentile(delivery, 99)) * 1000)
            # fit the longest stretch without a gap: a gap moves capture time against the sample count
            step = cap[1:] - (cap[:-1] + n[:-1] / RATE)
            cuts = np.flatnonzero(np.abs(step) > 0.002) + 1
            r["gap_ms"] = float(np.max(np.abs(step[cuts - 1]))) * 1000 if len(cuts) else 0.0
            bounds = np.concatenate([[0], cuts, [len(cap)]])
            k = int(np.argmax(np.diff(bounds)))
            s = slice(bounds[k], bounds[k + 1])
            slope, icpt = np.polyfit(idx[s], cap[s], 1)
            resid = cap[s] - (slope * idx[s] + icpt)
            r["jitter_ms"] = (float(np.std(resid)) * 1000, float(np.max(np.abs(resid))) * 1000)
            r["rate"] = 1.0 / slope
            # the same fit on arrival times: what timestamps would be worth without the pipeline clock
            a_slope, a_icpt = np.polyfit(idx[s], arr[s], 1)
            r["arrival_jitter_ms"] = float(np.std(arr[s] - (a_slope * idx[s] + a_icpt))) * 1000
            r["arrival_rate"] = 1.0 / a_slope
        r["dbfs"] = 10 * math.log10(self.sumsq / self.samples) if self.samples and self.sumsq > 0 else -math.inf
        r["peak"] = self.peak
        r["cpu_pct"] = 100 * self.callback_s / seconds
        return r

    def write_wav(self, folder: Path, name: str) -> Path | None:
        if not self.chunks:
            return None
        folder.mkdir(parents=True, exist_ok=True)
        kind = "monitor" if self.dev.kind == "monitor" else "mic"
        path = folder / f"{name}-{kind}-{self.dev.node.split('.')[-1]}.wav"
        pcm = (np.clip(np.concatenate(self.chunks), -1, 1) * 32767).astype("<i2")
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(pcm.tobytes())
        return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--list", action="store_true", help="list sources and exit")
    ap.add_argument("--seconds", type=float, default=15.0)
    ap.add_argument("--latency", type=int, default=256, help="node.latency in frames at 48 kHz (default 256 = 5.3 ms)")
    ap.add_argument("--chunk-ms", type=float, default=0.0, help="re-chunk into blocks this long in GStreamer (0 = off)")
    ap.add_argument("--align-ns", type=int, default=0, help="audiobuffersplit alignment-threshold in ns (0 = default)")
    ap.add_argument("--only", action="append", default=[], help="substring of a node or display name (repeatable)")
    ap.add_argument("--record", type=Path, default=None, help="write one WAV per source into this folder")
    ap.add_argument("--name", default=time.strftime("rec-%Y%m%d-%H%M%S"), help="file name prefix for --record")
    args = ap.parse_args()

    Gst.init(None)
    devices = list_devices()
    if args.only:
        devices = [d for d in devices if any(s.lower() in (d.node + d.name).lower() for s in args.only)]
    if args.list or not devices:
        for d in devices:
            print(f"{d.kind:8} {d.name:40} {d.node}")
        return 0 if devices else 1

    caps = [Capture(d, args.latency, args.chunk_ms, args.align_ns, args.record) for d in devices]
    loop = GLib.MainLoop()
    for c in caps:
        c.build()
        bus = c.pipeline.get_bus()
        bus.add_signal_watch()

        def on_msg(_bus, msg, c=c):
            if msg.type == Gst.MessageType.ERROR:
                err, dbg = msg.parse_error()
                c.error = f"{err.message} ({dbg})"
                c.pipeline.set_state(Gst.State.NULL)

        bus.connect("message", on_msg)
        c.pipeline.set_state(Gst.State.PLAYING)
    print(f"capturing {len(caps)} source(s) for {args.seconds:.0f} s at node.latency {args.latency}/{RATE} ...",
          file=sys.stderr)
    GLib.timeout_add(int(args.seconds * 1000), loop.quit)
    wall0, cpu0 = time.monotonic(), time.process_time()
    loop.run()
    wall = time.monotonic() - wall0
    cpu = time.process_time() - cpu0

    for c in caps:
        q = Gst.Query.new_latency()
        lat = q.parse_latency() if c.pipeline.query(q) else None
        c.pipeline.set_state(Gst.State.NULL)
        c.reported_latency_ms = lat[1] / 1e6 if lat and lat[0] else None

    print(f"process CPU while capturing: {100 * cpu / wall:.1f}% of one core for {len(caps)} source(s)")
    print(f"{'source':44} {'buf':>5} {'block':>6} {'deliver med/p99':>16} {'jitter sd/max':>14} "
          f"{'arrival sd':>10} {'rate':>9} {'arr.rate':>9} {'gaps':>10} {'dBFS':>6} {'cpu':>5} {'gst lat':>7}")
    for c in caps:
        r = c.report(wall)
        if r.get("error") or "jitter_ms" not in r:
            print(f"{r['name'][:44]:44} {r['buffers']:5} ERROR: {r.get('error') or 'too few buffers'}")
            continue
        gaps = f"{r['gaps']} ({r['gap_ms']:.0f}ms)" if r["gaps"] else "0"
        lat = f"{c.reported_latency_ms:.1f}" if c.reported_latency_ms is not None else "?"
        print(f"{r['name'][:44]:44} {r['buffers']:5} {r['block_ms']:5.1f}m "
              f"{r['deliver_ms'][0]:7.2f}/{r['deliver_ms'][1]:6.2f}ms "
              f"{r['jitter_ms'][0]:6.3f}/{r['jitter_ms'][1]:5.2f}ms {r['arrival_jitter_ms']:8.2f}ms "
              f"{r['rate']:9.1f} {r['arrival_rate']:9.1f} {gaps:>10} {r['dbfs']:6.1f} {r['cpu_pct']:4.1f}% {lat:>6}ms")
        if args.record:
            p = c.write_wav(args.record, args.name)
            if p:
                print(f"    wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
