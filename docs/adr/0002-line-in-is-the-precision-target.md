# 2. Line-level audio is the precision target; a microphone is best effort

- Status: accepted
- Date: 2026-11-16
- Related: ADR 0001 (the tracker and the benchmark used here)

## Context

The first plan was beat detection from a microphone in the room. Before tuning a tracker for it, it had to be known
whether a microphone carries the beat precisely enough at all: within 30 ms on the ball. If it does not, the tracker
should be tuned for line-level audio instead (what an output plays, e.g. a DJ program on the same laptop, or a line
input), with the microphone left as a fallback.

## How it was tested

There were no real room recordings, so the microphone was simulated (`beat_bench.py micify`): room reverb, everything
below about 150 Hz cut (a laptop microphone loses the kick's body), crowd noise, and a gain that wanders. The six
synthetic clips and the three real tracks were each run as "line" and as "mic". Three independent checks:

1. **What the best offline tracker can still get from the mic version** (Beat This!, which sees the whole track, so
   an upper bound for any live tracker), against the line references:

   | Clip | Line: median error | Mic: F1 | Mic: median error |
   |---|---|---|---|
   | real, electronic 145 | about 2 ms | 0.98 | 18 ms |
   | real, electronic 110 | about 4 ms | 0.99 | 4 ms |
   | real, guitar with intro | about 0 ms | 0.79 | 20 ms |
   | synthetic ramp | 9 ms | 0.50 | 21 ms |

   (The line figures are near zero partly because the references are Beat This! on the line versions.)

2. **Whether the onsets are still visible at the beats**, tracker-independent: the share of true beats where the
   onset curve has a clear peak within ±20 ms, per frequency band. On line audio the beats show in every band. On the
   mic versions everything above 500 Hz mostly vanishes; only 150 to 500 Hz holds up (60 to 90 % of the beats), and
   on the guitar track it drops to 28 %.

3. **A live tracker** (aubio, the only installable library at the time), line against mic, share of beats in time:
   electronic 110 from 84 % to 28 %, guitar from 49 % to 17 %, electronic 145 about the same.

## Decision

Tune and benchmark for line-level audio. Keep microphones selectable, with no tuning for them: the same code path
works, and the confidence and level shown in the app tell honestly whether a microphone source has a usable beat.

## Consequences

- The beat detection is precise where it is meant to be used: our tracker puts 93 to 99 % of the beats within 30 ms
  on line-level electronic music (ADR 0001). On the mic copies it reaches 77 to 99 % on most clips, but only 26 % on
  the guitar track.
- Sources are listed outputs first (their monitors are line level) and the documentation says that line level works
  best.
- The simulated microphone is a guess. A loud PA with real bass would probably do better than the model, a laptop
  speaker worse. If microphone use becomes important, real room recordings (`tools/audio_spike.py --record` with the
  microphone and the output's monitor at once, then `beat_bench.py annotate` on the monitor) are the way to
  revisit this.
