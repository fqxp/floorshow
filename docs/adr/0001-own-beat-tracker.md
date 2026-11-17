# 1. Our own causal beat tracker instead of a beat-tracking library

- Status: accepted
- Date: 2026-11-16
- Code: `src/floorshow/common/beat_tracker.py`, benchmark `tools/beat_bench.py`

## Context

The show needed a third clock source besides the internal BPM and MIDI: the beat of the music itself, taken from an
audio source (an output's monitor or an input). What the tracker has to deliver is unusual for a beat tracker:

- **Causal.** The light is drawn *now*, from audio that has already been heard. A tracker that looks ahead, even by a
  second, is no use, however accurate it is afterwards.
- **A beat model, not a list of beats.** The renderer's clock is `beat(t) = anchor + (t - anchor_time) * bpm / 60`,
  sampled every frame. The tracker must say where the beat is at any moment, including between detections.
- **Precise.** Within 30 ms the beat looks right on the ball; further off, it looks late or early.
- **Fast to follow** a DJ's tempo changes: a new track that is not beat-matched, a pitch-fader ramp.
- **Robust to dance music**: hats and bass on the off-beat are often louder than the kick, and breakdowns have no
  kick at all.
- **Cheap**, because it was meant to run on every audio source at once for previews (later only the chosen source
  is tracked; the others get level meters, see the suggested ADRs).
- **Installable** with Python 3.13 and NumPy 2 through uv.

## Options considered

| Option | Causal | Python 3.13 / NumPy 2 | Notes |
|---|---|---|---|
| aubio (`tempo`) | yes | only the `aubio-ledfx` fork has wheels; the original is unmaintained | C, very cheap; F 57 on GTZAN in the BeatNet paper's comparison |
| madmom (RNN + online DBN) | yes | no: last PyPI release 0.16.1 (2017) needs Python < 3.10 and NumPy < 1.20; only a git install or the `madmom-onnx` fork | strong onset network |
| BeatNet / BeatNet+ (CRNN + particle filter) | yes | needs PyTorch | best causal results published (F 75 on GTZAN), but far too heavy to run per source |
| Beat This! (transformer) | **no**, offline | needs PyTorch | state of the art offline; used here as the *reference* |
| librosa, essentia beat tracking | no, offline | yes | not usable for a live clock |
| Our own tracker in NumPy | yes | yes | costs the effort of writing and tuning it |

The published numbers come from the BeatNet paper (arXiv 2111.00704) and the projects' PyPI pages, as found on
2026-11-16. madmom and BeatNet were not installed or measured here: their packaging (old NumPy, or PyTorch) ruled
them out before a benchmark would have mattered.

## How the candidates were tested

`tools/beat_bench.py` measures what the projector would show, not what a tracker reports after the fact:

- Audio is fed in 20 ms blocks, each 10 ms after its last sample was captured. Every 10 ms of show time the tracker is
  asked where the beat is *now*, using only the audio it has.
- The moments its beat count crosses a whole number are the beats the light shows. They are compared with the true
  beats:
  - **F1**: shown beats within ±70 ms of a true beat (the MIREX window), after the first 8 s.
  - **Median and p90 phase error** in ms at every true beat.
  - **In time**: the share of true beats with the shown beat within 30 ms.
  - **Lock**: seconds after the start and after each tempo or phase change until 8 beats in a row are within 30 ms.
  - **Octave**: the share of time the tempo is about double, half, 3/2 or 2/3 of the true one.
  - **RTF**: processing time per audio time, for one source.
- Clips:
  - **Synthetic** (`beat_bench.py synth`), exact beats known. Six scenarios: steady 124 BPM; a jump from 124 to 128
    with a 0.37-beat phase shift (a mix that is not beat-matched); a ramp from 120 to 130 BPM over 16 bars; a
    breakdown of 32 beats without kick; drum and bass at 174 with a half-time kick and snare; slow 96 BPM. Kick on the
    beat, hats and bass on the off-beat, pads. Each as "line" and as "mic" (see ADR 0002).
  - **Real**: three tracks (electronic at 145 and at 110 BPM, a guitar post-punk track at about 110 with a 30 s
    intro). Reference beats from Beat This! (`beat_bench.py annotate`). Two fixes were needed before the references
    could be trusted: Beat This! without its DBN puts beats on 20 ms frames, so steady stretches are smoothed onto a
    straight line; and it annotated the guitar intro at double tempo (214), so double-tempo stretches are folded to
    the track's main level.
- A metronome that knows the first tempo and phase and never adapts served as a sanity check of the harness (F1 0.98
  on steady clips, drifting off after a jump or a ramp, as it should).

## Results

aubio, on the synthetic clips (line):

- The default onset function (HFC) and `specflux` locked **half a beat off on every clip**, on the off-beat hats
  and bass, F1 0.00. Checked against the true beat times outside the harness, so it is aubio, not the adapter.
- With the `energy` onset function it locks to the kick on clean audio: steady 99 % in time (median 3.0 ms), slow
  98 %, the jump re-locked after 3.5 s, but the ramp only 69 % in time (it trails by 24 ms), the breakdown 37 %
  (the off-beat took over), drum and bass at half tempo. On the "mic" copies it flipped to the off-beat on most clips.
- On the real tracks (line): `energy` 65 % / 84 % / 50 % in time (fast / slow / guitar), the default 57 % / 13 % /
  52 %. Locking took 12 to 35 s on two of them.
- Cost: about 0.2 to 0.8 % of a core per source.

Our tracker, final version, line clips:

| Clip | In time | Median / p90 error | Lock (start; after each change) |
|---|---|---|---|
| synthetic steady | 99 % | 2.5 / 3.3 ms | 4.4 s |
| synthetic jump (124 → 128, phase shift) | 96 % | 2.0 / 4.1 ms | 4.4 s; 1.6 s |
| synthetic ramp (120 → 130) | 99 % | 3.9 / 8.4 ms | 4.5 s |
| synthetic breakdown (no kick for 32 beats) | 99 % | 0.5 / 1.1 ms | 4.3 s; stays; stays |
| synthetic slow 96 | 98 % | 0.7 / 1.7 ms | 4.2 s |
| synthetic drum and bass 174 | 48 % | at 87 BPM (half tempo) | – |
| real, electronic 145 | 94 % | 4.2 / 15 ms | 12.5 s |
| real, electronic 110 | 93 % | 7.2 / 10.5 ms | 9.4 s |
| real, guitar with intro | 64 % | 19.6 / 262 ms | 23 s |

Cost: 0.012 to 0.014 of a core per source (0.023 before replacing `np.roll` and per-band means with a ring buffer
and one `reduceat`, with identical results).

How it got there, each step measured on the benchmark:

1. Multi-band spectral flux, autocorrelation tempo with a log-tempo prior, and a phase fold. Steady and jump were good
   at once; slow tracks locked on the off-beat.
2. A phase jump must show 4 times in a row (0.4 s) before it is taken: real 110 BPM from 25 % to 90 % in time.
3. A detector bias of 12 ms (the onsets come out early) calibrated on the synthetic clips with exact beats.
4. Half-beat flips decided by accumulated evidence (a CUSUM of the kick strength at the other phase against the
   kicks seen so far, in linear rather than log magnitude): breakdown from 71 % to 99 %, real 145 BPM from 74 % to
   94 %. Two simpler rules (a 3 s wait; a fixed kick ratio) each fixed one clip and broke another.
5. The first tempo estimate after 4 s instead of 3 s, and easy tempo switches during the first 10 s.

Live checks: a temporary PipeWire null sink playing the real tracks, captured by the listener through GStreamer, locked
at 145.1 to 145.4 BPM (reference 145.2) and 109.9 BPM (reference 110.2).

## Decision

Write our own causal tracker in NumPy (`common/beat_tracker.py`) and keep aubio and Beat This! only in the benchmark
(aubio as a baseline, Beat This! as the reference for real recordings).

## Consequences

- No native or heavy dependency for beat tracking; NumPy only. It runs on every machine the app runs on.
- Better than the only installable library on what matters here (beats in time, breakdowns, ramps, the off-beat),
  at a cost low enough to run many at once.
- We maintain and tune it ourselves. The benchmark exists so that every change can be measured; it should be run
  before changing the tracker (`uv run python tools/beat_bench.py run`).
- Tuned on three real tracks plus synthetic ones: there is a risk of over-fitting. More real recordings
  (`tools/audio_spike.py --record`, then `beat_bench.py annotate`) are the cheapest improvement.
- Known weaknesses: drum and bass is read at half tempo with the default tempo range (70 to 180 BPM; a narrower range
  in Preferences fixes it); locking takes 4 to 12 s, longer when a track starts without a kick; music without a
  steady kick (the guitar track) is followed with low confidence and slips to the off-beat now and then.
- If a better onset detector is needed later, an RNN onset stage (madmom's network through ONNX Runtime) for the
  tracked source only is the next candidate; it was approved in principle but not needed for line-level dance music.
