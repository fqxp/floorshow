"""Causal beat tracker for line-level audio: tempo, beat phase and a confidence, from a stream of samples.

Pure numpy, no I/O. ``feed(samples, t0)`` takes audio with the capture time of its first sample (seconds on any
monotonic clock); ``beat_at(t)`` says where the beat is at time ``t``, which may be in the future: the model is
``beat(t) = anchor_beat + (t - anchor_time) * bpm / 60``, like the beat clock's, so it can be sampled every frame.

Stages, all run at 100 frames per second:

1. Onsets: log-magnitude spectral flux in five bands, each normalised by its own running level, so a loud bass
   line does not drown the hats and a quiet track works as well as a loud one. Two onset curves come out: ``all``
   (every band) and ``low`` (below 500 Hz: kick and bass), which decides beat against off-beat.
2. Tempo, every 0.25 s: autocorrelation of the recent onset curve (recent frames weigh more, so a new tempo wins
   within a few seconds), scored with its multiples so the metrical level is consistent, times a log-tempo prior.
   Small changes (a pitch fader) are followed smoothly. A different tempo has to win a few updates in a row
   before the tracker switches to it; then the phase is taken over at once instead of slewing to it.
3. Phase, every 0.1 s: the recent onset curve is folded at the beat period. The fold's peak is where the beats
   are. If the kick-and-bass curve is stronger half a beat later, that is the beat (house hats and bass sit on
   the off-beat). The newest beat time this gives corrects the model like a phase-locked loop.
4. Confidence: how much the fold stands out (0 = no periodicity, about 1 = a clean four-to-the-floor). Below
   ``MIN_CONFIDENCE`` the tracker says it is not locked and holds its tempo; ``beat_at`` keeps free-running.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

FPS = 100
N_FFT = 2048
BANDS_HZ = ((30, 150), (150, 500), (500, 2000), (2000, 6000), (6000, 16000))
LOW_BANDS = 2  # the first two bands make the "low" curve
LEVEL_TAU_S = 3.0  # band level normalisation
TEMPO_EVERY = 25  # frames between tempo updates
PHASE_EVERY = 10  # frames between phase observations
TEMPO_WINDOW_S = 8.0
TEMPO_TAU_S = 3.0  # recent onsets weigh more in the tempo estimate
PHASE_WINDOW_S = 4.0
PHASE_TAU_S = 1.5
FOLD_BINS = 48
SMOOTH_TEMPO = 0.04  # within this ratio a new estimate is followed smoothly
SWITCH_UPDATES = 3  # a different tempo must win this many tempo updates in a row
SWITCH_UPDATES_OCTAVE = 8  # ... and this many when it is the double or half of the current one
FIRST_TEMPO_S = 4.0  # audio needed before the first tempo estimate
YOUNG_S = 10.0  # for this long after the first estimate a better tempo is taken at once (it was a guess)
PHASE_GAIN = 0.25  # share of the phase error removed per observation
FREQ_GAIN = 0.05  # share of the phase error that goes into the tempo (second-order loop: follows ramps)
HARD_RESYNC_BEATS = 0.2
RESYNC_OBS = 4  # a phase error that big must be seen this many observations in a row (within 0.1 beat of each other)
PULL_BEATS = 0.15  # the phase follows the fold's peak within this distance of the model's beat
HALF_DECAY = 0.97  # per observation: old evidence for "the beat is half a beat away" fades over a few seconds
HALF_BIAS = 0.05  # per observation: evidence has to beat this to count (a little stickiness)
HALF_FLIP = 1.5  # summed log kick ratio at which the beat moves by half a beat
KICK_KEEP = 0.6  # ... where the beat counts as at least this share of the usual kick strength
KICK_RISE, KICK_FADE = 0.2, 0.005  # per observation
DETECTOR_DELAY_S = 0.012  # onsets come out this much early (measured on the synthetic clips, which have exact beats)
MIN_CONFIDENCE = 0.25
MIN_OBSERVE = 0.1  # weaker folds are ignored; between this and 0.5 the correction is scaled down
MIN_ONSET_LEVEL = 1e-3  # below this (per band, after normalisation) there is nothing to track: silence


@dataclass(frozen=True)
class Estimate:
    bpm: float
    confidence: float  # 0..1
    locked: bool
    anchor_time: float
    anchor_beat: float


class BeatTracker:
    def __init__(self, sr: int, min_bpm: float = 70.0, max_bpm: float = 180.0, prior_bpm: float = 122.0,
                 offset_s: float = DETECTOR_DELAY_S):
        if sr % FPS:
            raise ValueError(f"sample rate must be a multiple of {FPS}")
        self.sr = sr
        self.hop = sr // FPS
        self.min_bpm, self.max_bpm, self.prior_bpm = min_bpm, max_bpm, prior_bpm
        self.offset_s = offset_s  # added to onset times: corrects the detector's bias
        self._win = np.hanning(N_FFT).astype(np.float32)
        freqs = np.fft.rfftfreq(N_FFT, 1.0 / sr)
        self._band_idx = [np.flatnonzero((freqs >= lo) & (freqs < hi)) for lo, hi in BANDS_HZ]
        # the bands are adjacent runs of bins: their means are one reduceat over the run from the first to the last
        self._band_lo, self._band_hi = int(self._band_idx[0][0]), int(self._band_idx[-1][-1]) + 1
        self._band_starts = np.array([int(i[0]) for i in self._band_idx]) - self._band_lo
        self._band_sizes = np.array([len(i) for i in self._band_idx], dtype=float)
        self._buf = np.zeros(N_FFT, dtype=np.float32)  # newest N_FFT samples
        self._pending = np.zeros(0, dtype=np.float32)
        self._samples = 0  # samples consumed into frames
        self._fed = 0  # samples handed to feed() so far
        self._t_first: float | None = None  # capture time of sample 0, as the newest block's timestamp implies
        self._prev_spec: np.ndarray | None = None
        self._low_bins = np.concatenate(self._band_idx[:LOW_BANDS])
        self._prev_low_mag: np.ndarray | None = None
        self._level = np.full(len(BANDS_HZ), 1e-3)
        n = int(TEMPO_WINDOW_S * FPS)
        # ring buffer of the onset curves: all bands, the low bands, and the low bands without level normalisation
        # (linear, not log: how hard the kick really hits); ``_head`` is where the next frame goes
        self._curves = np.zeros((3, n))
        self._head = 0
        self._half_evidence = 0.0
        self._kick_ref = 0.0  # raw low flux at the beat while locked: rises fast, fades over ~20 s (outlasts a break)
        self._frames = 0
        # model
        self._bpm = prior_bpm
        self._period = FPS * 60.0 / prior_bpm  # frames per beat
        self._have_tempo = False
        self._tempo_since = 0  # frame of the first tempo estimate
        self._anchor_time = 0.0
        self._anchor_beat = 0.0
        self._phased = False
        self._confidence = 0.0
        self._candidate: tuple[float, int] | None = None  # (period, updates it has won in a row)
        self._resync = False
        self._jump: tuple[float, int] | None = None  # (offset in beats, observations in a row) of a pending jump

    # --- input ---------------------------------------------------------------------------------

    def feed(self, samples: np.ndarray, t0: float) -> None:
        """``t0`` is the capture time of the first sample. Every block's time counts, not just the first one's: the
        sound card's clock drifts against the monotonic clock (some 20 ppm, over an hour more than 70 ms)."""
        self._t_first = t0 - self._fed / self.sr
        self._fed += len(samples)
        self._pending = np.concatenate([self._pending, np.asarray(samples, dtype=np.float32)])
        while len(self._pending) >= self.hop:
            hop, self._pending = self._pending[: self.hop], self._pending[self.hop:]
            self._buf = np.concatenate([self._buf[self.hop:], hop])
            self._samples += self.hop
            self._frame()

    def _frame(self) -> None:
        mag = np.abs(np.fft.rfft(self._buf * self._win))
        spec = np.log1p(1000.0 * mag)
        low_mag = mag[self._low_bins]
        if self._prev_spec is None:
            self._prev_spec, self._prev_low_mag = spec, low_mag
            return
        # linear (not log) flux of the low bands: how hard the kick really hits, against a bass note
        low_raw = float(np.maximum(0.0, low_mag - self._prev_low_mag).sum())
        self._prev_low_mag = low_mag
        flux = np.maximum(0.0, spec - self._prev_spec)
        self._prev_spec = spec
        bands = np.add.reduceat(flux[self._band_lo:self._band_hi], self._band_starts) / self._band_sizes
        a = 1.0 / (LEVEL_TAU_S * FPS)
        self._level += a * (bands - self._level)
        norm = bands / np.maximum(self._level, MIN_ONSET_LEVEL)
        quiet = self._level < MIN_ONSET_LEVEL
        norm[quiet] = 0.0
        self._curves[:, self._head] = (norm.sum(), norm[:LOW_BANDS].sum(), low_raw)
        self._head = (self._head + 1) % self._curves.shape[1]
        self._frames += 1
        if self._frames % TEMPO_EVERY == 0 and self._frames >= FIRST_TEMPO_S * FPS:
            self._update_tempo()
        if self._frames % PHASE_EVERY == 0 and self._have_tempo:
            self._observe_phase()

    def _recent(self, n: int) -> np.ndarray:
        """The newest ``n`` frames of the three onset curves, oldest first (shape 3 x n)."""
        size = self._curves.shape[1]
        return self._curves[:, (self._head - n + np.arange(n)) % size]

    def _frame_time(self, k_from_end: float) -> float:
        """Time of the onset in frame ``-1 - k_from_end`` (k = 0: the newest frame)."""
        center = self._samples - N_FFT / 2 - k_from_end * self.hop  # the window's centre, in samples
        return (self._t_first or 0.0) + center / self.sr + self.offset_s

    # --- tempo ---------------------------------------------------------------------------------

    def _update_tempo(self) -> None:
        n = min(self._frames, self._curves.shape[1])
        x = self._recent(n)[0]
        x = x - x.mean()
        w = np.exp((np.arange(n) - n) / (TEMPO_TAU_S * FPS))
        x = x * np.sqrt(w)
        if not np.any(x):
            return
        spec = np.fft.rfft(x, 2 * n)
        ac = np.fft.irfft(spec * np.conj(spec))[:n]
        if ac[0] <= 0:
            return
        ac = ac / ac[0]
        lo = int(FPS * 60.0 / self.max_bpm)
        hi = min(int(math.ceil(FPS * 60.0 / self.min_bpm)), n // 2 - 1)
        lags = np.arange(lo, hi + 1)
        score = ac[lags] + 0.5 * _at(ac, 2 * lags) + 0.25 * _at(ac, 4 * lags) + 0.25 * _at(ac, lags / 2)
        bpms = FPS * 60.0 / lags
        prior = np.exp(-0.5 * (np.log2(bpms / self.prior_bpm) / 0.8) ** 2)
        total = score * prior
        i = int(np.argmax(total))
        if total[i] <= 0:
            return
        period = float(lags[i]) + _parabola(total, i)
        # refine on the autocorrelation itself, which is sharper than the combined score
        j = int(round(period))
        if 1 <= j < n - 1:
            period = j + _parabola(ac, j)
        if not self._have_tempo:
            self._set_period(period)
            self._have_tempo = True
            self._tempo_since = self._frames
            self._resync = True
            return
        ratio = period / self._period
        if abs(ratio - 1) <= SMOOTH_TEMPO:
            self._set_period(self._period + 0.3 * (period - self._period))
            self._candidate = None
            return
        octave = min(abs(ratio - 2), abs(ratio - 0.5), abs(ratio - 1.5), abs(ratio - 2 / 3)) < 0.05
        need = SWITCH_UPDATES_OCTAVE if octave else SWITCH_UPDATES
        if self._frames - self._tempo_since < YOUNG_S * FPS:
            need = 2 if octave else 1
        if self._candidate and abs(period / self._candidate[0] - 1) <= SMOOTH_TEMPO:
            self._candidate = (period, self._candidate[1] + 1)
        else:
            self._candidate = (period, 1)
        if self._candidate[1] >= need:
            self._set_period(period)
            self._candidate = None
            self._resync = True

    def _set_period(self, period: float) -> None:
        now = self._frame_time(0)
        if self._phased:
            self._anchor_beat, self._anchor_time = self._beat(now), now
        self._period = period
        self._bpm = FPS * 60.0 / period

    # --- phase ---------------------------------------------------------------------------------

    def _observe_phase(self) -> None:
        n = min(self._frames, int(PHASE_WINDOW_S * FPS))
        p = self._period
        if n < 2 * p:
            return
        k = np.arange(n)  # frames back from the newest
        w = np.exp(-k / (PHASE_TAU_S * FPS))
        all_, low, raw = self._recent(n)[:, ::-1]  # newest first, as k counts
        e_all = all_ * w
        e_low = low * w
        ph = (k / p) % 1.0  # 0 = a beat at the newest frame, growing into the past
        bins = (ph * FOLD_BINS).astype(int) % FOLD_BINS
        fold = np.bincount(bins, e_all, FOLD_BINS)
        fold_low = np.bincount(bins, e_low, FOLD_BINS)
        fold_raw = np.bincount(bins, raw * w, FOLD_BINS) / np.maximum(np.bincount(bins, w, FOLD_BINS), 1e-9)
        fold = _circ_smooth(fold)
        fold_low = _circ_smooth(fold_low)
        fold_raw = _circ_smooth(fold_raw)
        mean = fold.mean()
        if mean <= 0:
            self._confidence = 0.0
            return
        H = FOLD_BINS // 2
        b = int(np.argmax(fold))  # the strongest periodic onsets: the beat, or in house often the off-beat
        if not self._phased or self._resync:
            if fold_low[(b + H) % FOLD_BINS] > 1.15 * fold_low[b] and fold[(b + H) % FOLD_BINS] > 0.5 * fold[b]:
                b = (b + H) % FOLD_BINS  # more kick and bass half a beat later: that is the beat
            self._confidence = float(min(1.0, (fold[b] - mean) / (3.0 * mean)))
            if self._confidence < MIN_OBSERVE:
                return
            t_beat = self._bin_time(fold, b)
            self._anchor_time = t_beat
            self._anchor_beat = float(round(self._beat(t_beat))) if self._phased else 0.0
            self._phased, self._resync, self._jump, self._half_evidence = True, False, None, 0.0
            self._kick_ref = float(fold_raw[b])
            return
        # where the model puts the beat, as a fold bin (the fold counts beats back from the newest frame)
        m = int(round((self._beat(self._frame_time(0)) % 1.0) * FOLD_BINS - 0.5)) % FOLD_BINS
        d = _wrap((b - m) / FOLD_BINS)  # strongest onsets relative to the model's beat, in beats
        if abs(d) > HARD_RESYNC_BEATS and abs(abs(d) - 0.5) > 0.12:
            # neither the beat nor the off-beat: the music moved (a mix that is not beat-matched). Take it once
            # it keeps showing at the same place.
            if self._jump is not None and abs(_wrap(d - self._jump[0])) <= 0.1:
                self._jump = (d, self._jump[1] + 1)
            else:
                self._jump = (d, 1)
            if self._jump[1] >= RESYNC_OBS:
                self._resync = True
            return
        self._jump = None
        # follow the peak nearest the model's beat (not the strongest one, which may be the off-beat)
        lo = int(round(PULL_BEATS * FOLD_BINS))
        near = [(m + k) % FOLD_BINS for k in range(-lo, lo + 1)]
        c = max(near, key=lambda i: fold[i])
        self._confidence = float(min(1.0, (fold[c] - mean) / (3.0 * mean)))
        # is the beat really half a beat away? Kick strength there against here, summed up over time (CUSUM):
        # strong evidence flips within a few observations, a break without kick adds nothing.
        other = (c + H) % FOLD_BINS
        here = float(fold_raw[c])
        rate = KICK_RISE if here > self._kick_ref else KICK_FADE
        self._kick_ref += rate * (here - self._kick_ref)
        # against the kicks seen so far, not just what is here now: in a break nothing is on the beat, and the
        # off-beat bass must not win against that
        e = math.log((fold_raw[other] + 1e-9) / (max(here, KICK_KEEP * self._kick_ref) + 1e-9))
        self._half_evidence = max(0.0, HALF_DECAY * self._half_evidence + e - HALF_BIAS)
        if self._half_evidence > HALF_FLIP:
            self._anchor_beat += 0.5 if d >= 0 else -0.5
            self._half_evidence = 0.0
            return
        if self._confidence < MIN_OBSERVE:
            return
        t_beat = self._bin_time(fold, c)
        model = self._beat(t_beat)
        err = round(model) - model
        g = min(1.0, self._confidence / 0.5)
        self._anchor_beat, self._anchor_time = model + g * PHASE_GAIN * err, t_beat
        self._set_period_from_phase(g * err)

    def _bin_time(self, fold: np.ndarray, b: int) -> float:
        """Time of the newest beat when fold bin ``b`` (refined between bins) is the beat."""
        pos = (b + _parabola_circ(fold, b) + 0.5) / FOLD_BINS  # beats back from the newest frame
        return self._frame_time(pos * self._period)

    def _set_period_from_phase(self, err: float) -> None:
        """Second-order loop: a phase error that keeps coming back in the same direction means the tempo is off."""
        self._period /= 1.0 + FREQ_GAIN * err
        self._bpm = FPS * 60.0 / self._period

    # --- output --------------------------------------------------------------------------------

    def beat_at(self, t: float) -> float | None:
        return self._beat(t) if self._phased else None

    def _beat(self, t: float) -> float:
        return self._anchor_beat + (t - self._anchor_time) * self._bpm / 60.0

    def bpm(self) -> float | None:
        return self._bpm if self._have_tempo else None

    def estimate(self) -> Estimate:
        return Estimate(self._bpm, self._confidence, self._phased and self._confidence >= MIN_CONFIDENCE,
                        self._anchor_time, self._anchor_beat)


def _wrap(x: float) -> float:
    """``x`` beats wrapped to -0.5..0.5."""
    return x - round(x)


def _at(a: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """``a`` at fractional indices (linear interpolation), 0 beyond the end."""
    idx = np.asarray(idx, dtype=float)
    i = np.floor(idx).astype(int)
    f = idx - i
    ok = i + 1 < len(a)
    out = np.zeros(len(idx))
    out[ok] = a[i[ok]] * (1 - f[ok]) + a[i[ok] + 1] * f[ok]
    return out


def _parabola(y: np.ndarray, i: int) -> float:
    if i <= 0 or i >= len(y) - 1:
        return 0.0
    a, b, c = y[i - 1], y[i], y[i + 1]
    d = a - 2 * b + c
    return float(0.5 * (a - c) / d) if d < 0 else 0.0


def _parabola_circ(y: np.ndarray, i: int) -> float:
    n = len(y)
    a, b, c = y[(i - 1) % n], y[i], y[(i + 1) % n]
    d = a - 2 * b + c
    return float(0.5 * (a - c) / d) if d < 0 else 0.0


def _circ_smooth(y: np.ndarray) -> np.ndarray:
    return 0.25 * np.roll(y, 1) + 0.5 * y + 0.25 * np.roll(y, -1)
