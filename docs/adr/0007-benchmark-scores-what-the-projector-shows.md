# 7. Beat trackers are benchmarked on what the projector would show, with Beat This! as the reference

- Status: accepted
- Date: 2026-11-16
- Code: `tools/beat_bench.py`

## Context

Choosing and tuning a beat tracker (ADRs 1 and 2) needed numbers that mean something on the ball. The usual beat
tracking scores compare a tracker's detected beats with annotations after the fact. A live show cares about something
else: whether the beat the light shows *at the moment it shows it*, from audio heard so far, is on the beat. A
tracker that detects every beat 100 ms after it happened scores well offline and looks wrong on the ball.

## Decision

`tools/beat_bench.py` simulates the show:

- Audio reaches the tracker in 20 ms blocks, each 10 ms after its last sample (`--block`, `--delay`).
- Every 10 ms of show time the tracker is asked for its beat *now*. The moments its beat count crosses a whole number
  are the beats the light shows.
- Scores: F1 within ±70 ms (MIREX), median and p90 phase error, the share of beats within 30 ms ("in time"), the time
  to stay locked for 8 beats after the start and after every tempo or phase change, the share of time at double or
  half tempo, and CPU time per audio time (with `--sources N`, for N at once).
- Clips with exact beats are synthesised (`synth`): steady, a jump without beat-matching, a ramp, a breakdown without
  kick, drum and bass at half-time, a slow track; each as line and as a simulated microphone (`micify`).
- Real recordings (`tools/audio_spike.py --record`, or any file decoded to WAV) get reference beats from Beat This!
  (`annotate`), an offline transformer tracker far better than any live one. Installed only for that, in a throwaway
  environment: `uv run --with beat-this python tools/beat_bench.py annotate ...`.
- Baselines: a metronome that knows the first tempo and never adapts (checks the harness), and aubio
  (`uv run --with aubio-ledfx ...`).

## How the harness itself was checked

- The metronome scored F1 0.98 on steady clips and drifted off after a jump or a ramp, as it should.
- aubio's half-beat errors were checked outside the harness, against the true beat times, before believing them.
- Beat This! references needed two corrections, both now in `annotate`: without its DBN it puts beats on 20 ms
  frames (steady stretches are smoothed onto a straight line), and it annotated a 30 s guitar intro at double tempo
  (double-tempo stretches are folded to the track's main level).
- An early lock criterion of 4 beats in a row was met by chance by a drifting clock; it is 8 now.

## Consequences

- Every tracker change can be measured in a minute on the same clips: `uv run python tools/beat_bench.py run`.
  The ADRs' figures come from it.
- Synthetic clips are generated (deterministic, under `~/.cache/mirrorballs/beat-clips/`), not committed; real
  recordings and their references are not in the repository either.
- Beat This! pulls in PyTorch (a large download) but only into uv's cache, never into the project.
- Three real tracks are few. Adding recordings is the cheapest way to make the numbers more trustworthy.
