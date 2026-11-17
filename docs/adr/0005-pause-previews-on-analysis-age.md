# 5. "Can the beat be had in time?" is judged by the age of the analysed audio, not by CPU load

- Status: accepted
- Date: 2026-11-16
- Code: `src/floorshow/listen/engine.py` (`MAX_AGE_S`, `LOAD_LIMIT`, `Listener.check_load`)

## Context

The feature was asked to predict when performance makes it hard to get the beat in time, and to degrade gracefully:
pause the source previews before the tracking of the chosen source suffers. That needs a measure of "falling behind".
All analysis threads of the listener share one core (Python's GIL).

## Options considered

- **CPU time per audio time** (real-time factor, "load"), summed over the sources, against a limit.
- **The age of the analysed audio**: how old the newest sample is when the tracker has processed it, from its capture
  time (ADR 4) to the end of the analysis. A growing age is falling behind, directly.

## How it was tested

The load measured live was three to six times the benchmark's (0.07 to 0.14 against 0.023 of a core per source),
even on silent sources and after measuring thread CPU time instead of wall time. Run in isolation:

| Feeding | Threads | CPU time per audio time, per source |
|---|---|---|
| back to back | 1 | 0.013 |
| back to back | 4 | 0.028 |
| paced like live audio (20 ms blocks, idle between) | 1 | 0.064 |
| paced like live audio | 4 | 0.095 |

Pacing alone costs five times as much CPU time per block: between bursts the caches go cold and the CPU clocks down.
Under real load the CPU speeds up, so the live load overstates how close the limit is. Meanwhile the age of the
analysed audio stayed at 8 to 14 ms in every live run.

## Decision

The listener's health line reports both. Previews are paused when the tracked source's audio is analysed more than
150 ms late (`MAX_AGE_S`), with the load only as a backstop (above 0.9 of a core); they are tried again after 30 s.
A source is "ok" when its age is under 150 ms and its load under 0.25.

## Consequences

- The pause reacts to what matters (the beat being corrected late), not to a number that depends on the CPU's clock
  speed.
- The load figure in the health messages and in `floorshow-listen --watch` looks alarming on an idle laptop; this
  record is the explanation.
- Since previews became level meters measured inside GStreamer (no samples in Python), a pause is unlikely; the
  mechanism stays as a safety net.
