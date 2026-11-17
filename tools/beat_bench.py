"""Beat-tracker benchmark: how well and how cheaply does a causal tracker put the beat where the light is drawn?

    uv run python tools/beat_bench.py synth                          # write the synthetic clips (exact ground truth)
    uv run python tools/beat_bench.py run                            # every tracker on every clip
    uv run --with aubio-ledfx python tools/beat_bench.py run --tracker aubio --clip jump
    uv run python tools/beat_bench.py run --sources 8                # cost of 8 sources at once
    uv run --with beat-this python tools/beat_bench.py annotate rec.wav   # reference beats for a real recording
    uv run python tools/beat_bench.py micify song-line.wav            # song-mic.wav: through the simulated mic

Clips live in ~/.cache/floorshow/beat-clips (``--dir``). Each ``name.wav`` has ``name.beats`` (one beat time in
seconds per line) and optionally ``name.events`` (times where the tempo or phase changes, for the lock-time metric).
Real recordings (``tools/audio_spike.py --record``) get their ``.beats`` from ``annotate``, which runs Beat This!, an
offline tracker that is far better than any causal one, so it serves as the reference.

The evaluation is what the projector shows, not what the tracker says after the fact. Audio reaches the tracker in
blocks, each ``--delay`` ms after its last sample was captured. Every 10 ms of show time the tracker is asked where
the beat is *now*, using only the audio it has. The moments its beat count crosses a whole number are the beats the
light shows, and those are compared with the true beats:

- F1: shown beats within +-70 ms of a true beat (the MIREX window), after the first 8 s
- late: median signed phase error in ms (+ = the light comes late)
- err med / p90: |phase error| at each true beat in ms, over beats where the tracker gives an estimate
- in-time: share of true beats where the shown beat is within 30 ms (a hit that looks right on the ball)
- lock: after the start and after each event, seconds until 8 beats in a row are within 30 ms (- = never)
- octave: share of time the tempo is about 2x or 1/2x the true one
- RTF: processing time / audio time per source (0.05 = 5 % of a core per source)
"""

from __future__ import annotations

import argparse
import math
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

SR = 44100
DEFAULT_DIR = Path.home() / ".cache" / "floorshow" / "beat-clips"
QUERY_HZ = 100
WARMUP_S = 8.0
F1_WINDOW_S = 0.07
IN_TIME_S = 0.03
LOCK_BEATS = 8  # in a row within IN_TIME_S: locked (4 can happen by chance while drifting past)


# --- trackers ------------------------------------------------------------------------------------


class Tracker(Protocol):
    name: str

    def feed(self, samples: np.ndarray, t0: float) -> None:
        """Mono float32 audio at SR; ``t0`` is the capture time of its first sample."""

    def beat_at(self, t: float) -> float | None:
        """Continuous beat count at show time ``t`` (any origin; only the fraction matters), None = no idea yet."""

    def bpm(self) -> float | None: ...


class Metronome:
    """What a perfect tap gives: the first true tempo and phase, never adapted. The floor every tracker must beat
    after a tempo change, and the ceiling of a perfect tracker on a steady clip."""

    name = "metronome"

    def __init__(self, beats: np.ndarray):
        self.t0, self.period = beats[0], float(np.median(np.diff(beats[:9])))

    def feed(self, samples: np.ndarray, t0: float) -> None:
        pass

    def beat_at(self, t: float) -> float | None:
        return (t - self.t0) / self.period

    def bpm(self) -> float | None:
        return 60.0 / self.period


class Aubio:
    """aubio's tempo object (``aubio-ledfx`` fork on PyPI). Between detected beats the count is extrapolated at
    aubio's current tempo, which is how a plain beat-event source would drive the clock."""

    name = "aubio"
    HOP = 512
    METHOD = "default"  # aubio's onset function: "default" (HFC), "specflux", "energy", ...

    def __init__(self, beats: np.ndarray):  # noqa: ARG002
        import aubio

        self.t = aubio.tempo(self.METHOD, 2 * self.HOP, self.HOP, SR)
        self.buf = np.zeros(0, dtype=np.float32)
        self.n = 0  # samples fed to aubio
        self.t_first: float | None = None
        self.last: tuple[float, int] | None = None  # (time of last detected beat, its count)
        self.count = 0

    def feed(self, samples: np.ndarray, t0: float) -> None:
        if self.t_first is None:
            self.t_first = t0
        self.buf = np.concatenate([self.buf, samples])
        while len(self.buf) >= self.HOP:
            hop, self.buf = self.buf[: self.HOP], self.buf[self.HOP:]
            if self.t(hop)[0]:
                self.count += 1
                self.last = (self.t_first + self.t.get_last_s(), self.count)
            self.n += self.HOP

    def beat_at(self, t: float) -> float | None:
        bpm = self.bpm()
        if self.last is None or not bpm:
            return None
        return self.last[1] + (t - self.last[0]) * bpm / 60.0

    def bpm(self) -> float | None:
        b = float(self.t.get_bpm())
        return b if b > 0 else None


class AubioEnergy(Aubio):
    """aubio with the plain energy onset function: on synthetic house it locks to the kick, where the default (HFC)
    and specflux lock to the off-beat hats and bass."""

    name = "aubio-energy"
    METHOD = "energy"


class Ours:
    """floorshow.common.beat_tracker, the tracker the app uses."""

    name = "ours"

    def __init__(self, beats: np.ndarray):  # noqa: ARG002
        from floorshow.common.beat_tracker import BeatTracker

        self.t = BeatTracker(SR)

    def feed(self, samples: np.ndarray, t0: float) -> None:
        self.t.feed(samples, t0)

    def beat_at(self, t: float) -> float | None:
        return self.t.beat_at(t)

    def bpm(self) -> float | None:
        return self.t.bpm()


TRACKERS = {"ours": Ours, "metronome": Metronome, "aubio": Aubio, "aubio-energy": AubioEnergy}


# --- synthetic clips ------------------------------------------------------------------------------


@dataclass
class Section:
    beats: int
    bpm_from: float
    bpm_to: float | None = None  # ramp to this tempo over the section
    kick: bool = True
    phase_jump: float = 0.0  # beats the grid jumps by at the start of the section (a mix that is not beat-matched)
    halftime: bool = False  # kick on 1 and 3 only, snare on 3: reads as half the tempo


SCENARIOS: dict[str, list[Section]] = {
    "steady": [Section(128, 124)],
    "jump": [Section(64, 124), Section(64, 128, phase_jump=0.37)],  # a new track, not beat-matched
    "ramp": [Section(32, 120), Section(64, 120, 130), Section(32, 130)],  # the pitch fader, over 16 bars
    "breakdown": [Section(48, 126), Section(32, 126, kick=False), Section(48, 126)],
    "dnb": [Section(192, 174, halftime=True)],  # octave trap: kick and snare say 87
    "slow": [Section(64, 96)],
}


def _grid(sections: list[Section]) -> tuple[list[tuple[float, float, Section, int]], list[float]]:
    """(beat time, beat length, its section, index in the section) for every beat, and the event times."""
    t, out, events = 0.5, [], []
    for s in sections:
        if out:
            events.append(t)
        t += s.phase_jump * 60.0 / s.bpm_from
        for i in range(s.beats):
            bpm = s.bpm_from if s.bpm_to is None else s.bpm_from + (s.bpm_to - s.bpm_from) * i / s.beats
            out.append((t, 60.0 / bpm, s, i))
            t += 60.0 / bpm
    return out, events


def synth(sections: list[Section], mic: bool, seed: int) -> tuple[np.ndarray, np.ndarray, list[float]]:
    rng = np.random.default_rng(seed)
    grid, events = _grid(sections)
    total = grid[-1][0] + grid[-1][1] + 1.0
    y = np.zeros(int(total * SR) + SR, dtype=np.float64)

    def add(at: float, sig: np.ndarray, gain: float) -> None:
        i = int(round(at * SR))
        y[i:i + len(sig)] += gain * sig[: max(0, len(y) - i)]

    tk = np.arange(int(0.25 * SR)) / SR
    kick = np.sin(2 * np.pi * np.cumsum(50 + 100 * np.exp(-tk / 0.03)) / SR) * np.exp(-tk / 0.12)
    kick[: int(0.004 * SR)] += np.diff(rng.standard_normal(int(0.004 * SR) + 1)) * 0.25 * np.exp(-tk[: int(0.004 * SR)] / 0.001)  # the beater click
    th = np.arange(int(0.05 * SR)) / SR
    hat = np.diff(rng.standard_normal(len(th) + 1)) * np.exp(-th / 0.012)  # differentiated noise: bright
    ts = np.arange(int(0.2 * SR)) / SR
    snare = (rng.standard_normal(len(ts)) * 0.6 + np.sin(2 * np.pi * 190 * ts)) * np.exp(-ts / 0.06)
    chord = [110.0, 130.8, 164.8, 196.0]
    for t, length, s, i in grid:
        if s.kick and (not s.halftime or i % 2 == 0):
            add(t, kick, 0.9)
        if s.halftime and i % 4 == 2:
            add(t, snare, 0.5)
        add(t + length / 2, hat, 0.25)  # off-beat hat
        if not s.halftime:
            add(t + length / 4, hat, 0.08)
        # bass on the off-beat eighths, pads every bar: tonal stuff without sharp onsets
        tb = np.arange(int(length / 2 * SR)) / SR
        bass = sum(np.sin(2 * np.pi * chord[0] / 2 * h * tb) / h for h in (1, 2, 3))  # a soft saw: no hard edges
        add(t + length / 2, bass * np.minimum(1, tb / 0.008) * np.exp(-tb / 0.15), 0.2)
        if i % 4 == 0:
            tp = np.arange(int(4 * length * SR)) / SR
            pad = sum(np.sin(2 * np.pi * f * tp + rng.uniform(0, 6)) for f in chord) * np.minimum(1, tp / 0.4)
            add(t, pad, 0.04)
    if mic:
        y = micify(y, rng)
    y /= np.max(np.abs(y)) * 1.1
    return y.astype(np.float32), np.array([g[0] for g in grid]), events


def micify(y: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """A laptop mic in a loud room: no lows under ~150 Hz, room reverb, crowd noise, a gain that wanders."""
    y = np.asarray(y, dtype=np.float64)
    ir_t = np.arange(int(0.4 * SR)) / SR
    ir = rng.standard_normal(len(ir_t)) * np.exp(-ir_t / 0.08)
    ir[0] = 12.0
    n = len(y) + len(ir)
    y = np.fft.irfft(np.fft.rfft(y, n) * np.fft.rfft(ir / np.abs(ir).sum() * 6, n), n)[: len(y)]
    y = y - _onepole_lowpass(y, 150.0)  # roughly a high-pass
    noise = _onepole_lowpass(rng.standard_normal(len(y)), 800.0)
    y = y + noise * 0.25 * np.sqrt(np.mean(y ** 2)) / np.sqrt(np.mean(noise ** 2))
    return y * (1 + 0.3 * np.sin(2 * np.pi * np.arange(len(y)) / SR / 23.0))


def _onepole_lowpass(x: np.ndarray, fc: float) -> np.ndarray:
    """First-order low-pass magnitude response, applied in the frequency domain (offline, so no causality needed)."""
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return np.fft.irfft(np.fft.rfft(x) / np.sqrt(1 + (f / fc) ** 2), len(x))


def write_wav(path: Path, y: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise SystemExit(f"{path}: only 16-bit WAV")
        y = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
        y = y.reshape(-1, w.getnchannels()).mean(axis=1)
        sr = w.getframerate()
    if sr != SR:  # linear interpolation is plenty for onset detection
        y = np.interp(np.arange(int(len(y) * SR / sr)) * sr / SR, np.arange(len(y)), y).astype(np.float32)
    return y


# --- evaluation -----------------------------------------------------------------------------------


@dataclass
class Result:
    f1: float
    err_signed_ms: float
    err_med_ms: float
    err_p90_ms: float
    in_time: float
    locks: list[float | None]
    octave: float
    rtf: float


def evaluate(make, y: np.ndarray, truth: np.ndarray, events: list[float], block_s: float, delay_s: float,
             sources: int = 1) -> Result:
    trackers = [make(truth) for _ in range(sources)]
    block = int(block_s * SR)
    n_blocks = len(y) // block
    query_dt = 1.0 / QUERY_HZ
    shown: list[float] = []  # show times where the beat count crossed a whole number
    at_truth: list[float | None] = [None] * len(truth)
    bpm_trace: list[tuple[float, float]] = []
    prev_count: float | None = None
    q = 0.0
    k = 0  # next true beat to sample
    busy = 0.0
    for b in range(n_blocks):
        seg = y[b * block:(b + 1) * block]
        t0 = b * block_s
        t_avail = t0 + block_s + delay_s  # the tracker gets this block now
        # show time runs up to the moment the next block arrives
        while q < t_avail:
            c = trackers[0].beat_at(q)
            while k < len(truth) and truth[k] <= q:
                ck = trackers[0].beat_at(truth[k])
                at_truth[k] = ck
                k += 1
            if c is not None and prev_count is not None and math.floor(c) > math.floor(prev_count):
                frac = c - math.floor(c)
                rate = c - prev_count
                shown.append(q - (frac / rate * query_dt if rate > 0 else 0))
            prev_count = c
            q += query_dt
        s = time.perf_counter()
        for tr in trackers:
            tr.feed(seg, t0)
        busy += time.perf_counter() - s
        bpm = trackers[0].bpm()
        if bpm:
            bpm_trace.append((t_avail, bpm))

    shown_a = np.array([s for s in shown if s >= WARMUP_S])
    ref = truth[truth >= WARMUP_S]
    hits = _match(shown_a, ref, F1_WINDOW_S)
    f1 = 2 * hits / (len(shown_a) + len(ref)) if len(shown_a) + len(ref) else 0.0

    errs = np.full(len(truth), np.nan)  # signed phase error in seconds; NaN = no estimate
    for i, c in enumerate(at_truth):
        if c is None or i + 1 >= len(truth):
            continue
        frac = c - round(c)
        errs[i] = frac * (truth[i + 1] - truth[i])
    valid = ~np.isnan(errs) & (truth >= WARMUP_S)
    ab = np.abs(errs[valid]) * 1000
    # errs > 0: at the true beat the count is already past the whole number, so the light was early
    signed = -float(np.median(errs[valid] * 1000)) if valid.any() else math.nan  # + = the light is late
    in_time = float(np.mean(np.abs(np.nan_to_num(errs[truth >= WARMUP_S], nan=1.0)) <= IN_TIME_S))

    locks: list[float | None] = []
    for ev in [0.0, *events]:
        idx = np.flatnonzero(truth >= ev)
        lock = None
        run = 0
        for i in idx:
            e = errs[i]
            run = run + 1 if not np.isnan(e) and abs(e) <= IN_TIME_S else 0
            if run == LOCK_BEATS:
                lock = float(truth[i - LOCK_BEATS + 1] - ev)
                break
        locks.append(lock)

    true_bpm = 60.0 / np.diff(truth)
    oct_n = 0
    for t, bpm in bpm_trace:
        if t < WARMUP_S:
            continue
        j = min(len(true_bpm) - 1, int(np.searchsorted(truth, t)))
        r = bpm / true_bpm[j]
        oct_n += abs(r - 2) < 0.1 or abs(r - 0.5) < 0.05 or abs(r - 1.5) < 0.07 or abs(r - 2 / 3) < 0.04
    octave = oct_n / max(1, sum(1 for t, _ in bpm_trace if t >= WARMUP_S))
    return Result(f1, signed, float(np.median(ab)) if len(ab) else math.nan,
                  float(np.percentile(ab, 90)) if len(ab) else math.nan, in_time, locks, octave,
                  busy / (len(y) / SR) / sources)


def _match(est: np.ndarray, ref: np.ndarray, win: float) -> int:
    """Greedy one-to-one matching of sorted beat times within ``win``."""
    i = j = hits = 0
    while i < len(est) and j < len(ref):
        d = est[i] - ref[j]
        if abs(d) <= win:
            hits += 1
            i += 1
            j += 1
        elif d < 0:
            i += 1
        else:
            j += 1
    return hits


# --- commands -------------------------------------------------------------------------------------


def cmd_synth(args) -> int:
    args.dir.mkdir(parents=True, exist_ok=True)
    for seed, (name, sections) in enumerate(SCENARIOS.items()):
        for mic in (False, True):
            stem = f"synth-{name}-{'mic' if mic else 'line'}"
            y, beats, events = synth(sections, mic, seed)
            write_wav(args.dir / f"{stem}.wav", y)
            (args.dir / f"{stem}.beats").write_text("".join(f"{b:.5f}\n" for b in beats))
            (args.dir / f"{stem}.events").write_text("".join(f"{e:.5f}\n" for e in events))
            print(f"{stem}: {len(y) / SR:.0f} s, {len(beats)} beats")
    return 0


def _load_clip(wav: Path) -> tuple[np.ndarray, np.ndarray, list[float]] | None:
    beats = wav.with_suffix(".beats")
    if not beats.exists():
        print(f"skipping {wav.name}: no .beats (run: annotate {wav})", file=sys.stderr)
        return None
    ev = wav.with_suffix(".events")
    truth = np.loadtxt(beats, ndmin=1)
    if truth.ndim > 1:
        truth = truth[:, 0]
    return read_wav(wav), truth, [float(x) for x in np.loadtxt(ev, ndmin=1)] if ev.exists() and ev.stat().st_size else []


def cmd_run(args) -> int:
    clips = sorted(p for p in args.dir.glob("*.wav") if not args.clip or any(c in p.stem for c in args.clip))
    if not clips:
        print(f"no clips in {args.dir} (run: synth)", file=sys.stderr)
        return 1
    names = args.tracker or list(TRACKERS)
    makers = {}
    for n in names:
        try:
            TRACKERS[n](np.array([0.0, 0.5, 1.0]))
            makers[n] = TRACKERS[n]
        except ImportError as e:
            print(f"skipping tracker {n}: {e}", file=sys.stderr)
    print(f"block {args.block * 1000:.0f} ms, delivery delay {args.delay * 1000:.0f} ms, {args.sources} source(s)")
    print(f"{'clip':28} {'tracker':12} {'F1':>5} {'late ms':>7} {'err med/p90 ms':>15} {'in-time':>7} {'lock s (start, events)':>24} "
          f"{'octave':>6} {'RTF':>7}")
    for wav in clips:
        clip = _load_clip(wav)
        if clip is None:
            continue
        y, truth, events = clip
        for n, make in makers.items():
            r = evaluate(make, y, truth, events, args.block, args.delay, args.sources)
            locks = ", ".join("-" if x is None else f"{x:.1f}" for x in r.locks)
            print(f"{wav.stem[:28]:28} {n:12} {r.f1:5.2f} {r.err_signed_ms:7.1f} {r.err_med_ms:7.1f}/{r.err_p90_ms:6.1f} {r.in_time:7.0%} "
                  f"{locks:>24} {r.octave:6.0%} {r.rtf:7.4f}")
    return 0


def cmd_micify(args) -> int:
    """name-line.wav -> name-mic.wav through the simulated mic; the .beats / .events are copied when present."""
    for wav in args.wav:
        stem = wav.stem.removesuffix("-line")
        out = wav.with_name(f"{stem}-mic.wav")
        y = micify(read_wav(wav), np.random.default_rng(len(stem)))
        write_wav(out, (y / (np.max(np.abs(y)) * 1.1)).astype(np.float32))
        for ext in (".beats", ".events"):
            if wav.with_suffix(ext).exists():
                out.with_suffix(ext).write_text(wav.with_suffix(ext).read_text())
        print(f"{out}")
    return 0


def smooth_beats(beats: np.ndarray, half: int = 4, tol: float = 0.015) -> tuple[np.ndarray, int]:
    """Beat This! without its DBN puts beats on 20 ms frames. Where the tempo is steady (a straight line through the
    beat and its ``half`` neighbours on each side fits within ``tol`` s) the beat is moved onto that line, which
    takes the frame rounding out; elsewhere (tempo changes, gaps) it stays as it is."""
    out = beats.copy()
    moved = 0
    for i in range(len(beats)):
        lo, hi = max(0, i - half), min(len(beats), i + half + 1)
        if hi - lo < half + 2:
            continue
        k = np.arange(lo, hi)
        slope, icpt = np.polyfit(k, beats[lo:hi], 1)
        if np.max(np.abs(beats[lo:hi] - (slope * k + icpt))) <= tol:
            out[i] = slope * i + icpt
            moved += 1
    return out, moved


def fold_double_tempo(beats: np.ndarray) -> tuple[np.ndarray, int]:
    """Beat This! sometimes annotates a stretch (a fast guitar intro) at twice the tempo of the rest of the track. A
    reference must keep one metrical level, so in such a stretch every other beat is dropped, keeping the ones in
    step with the first normal beat after it (or before it, at the end of the track)."""
    if len(beats) < 4:
        return beats, 0
    d = np.diff(beats)
    med = np.median(d)
    fast = d < 0.7 * med
    keep = np.ones(len(beats), dtype=bool)
    i, dropped = 0, 0
    while i < len(fast):
        if not fast[i]:
            i += 1
            continue
        j = i
        while j < len(fast) and fast[j]:
            j += 1
        # beats i..j are at double tempo; beat j is the first one followed by a normal interval (or the last beat)
        anchor = j if j < len(fast) else i
        for k in range(i, j + 1):
            if (k - anchor) % 2:
                keep[k] = False
                dropped += 1
        i = j
    return beats[keep], dropped


def cmd_annotate(args) -> int:
    try:
        from beat_this.inference import Audio2Beats
    except ImportError:
        print("needs Beat This!: uv run --with beat-this python tools/beat_bench.py annotate ...", file=sys.stderr)
        return 1
    a2b = Audio2Beats(checkpoint_path="final0", device="cpu", dbn=False)  # its own loader needs torchaudio codecs
    for wav in args.wav:
        beats, _downbeats = a2b(read_wav(wav).astype(np.float64), SR)
        beats, dropped = fold_double_tempo(np.asarray(beats, dtype=float))
        beats, moved = smooth_beats(beats)
        wav.with_suffix(".beats").write_text("".join(f"{b:.5f}\n" for b in beats))
        print(f"{wav}: {len(beats)} beats, median {60 / np.median(np.diff(beats)):.2f} BPM, {moved} smoothed, {dropped} double-tempo beats dropped")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR, help=f"clip folder (default {DEFAULT_DIR})")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("synth", help="write the synthetic clips")
    r = sub.add_parser("run", help="benchmark trackers")
    r.add_argument("--tracker", action="append", choices=list(TRACKERS))
    r.add_argument("--clip", action="append", help="substring of a clip name (repeatable)")
    r.add_argument("--block", type=float, default=0.02, help="seconds of audio per block (default 0.02)")
    r.add_argument("--delay", type=float, default=0.01, help="seconds from capture to the tracker (default 0.01)")
    r.add_argument("--sources", type=int, default=1, help="run this many copies at once, for the cost")
    m = sub.add_parser("micify", help="a simulated-mic copy of real clips (name-line.wav -> name-mic.wav)")
    m.add_argument("wav", type=Path, nargs="+")
    a = sub.add_parser("annotate", help="reference beats for real recordings (Beat This!)")
    a.add_argument("wav", type=Path, nargs="+")
    args = ap.parse_args()
    return {"synth": cmd_synth, "run": cmd_run, "micify": cmd_micify, "annotate": cmd_annotate}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
